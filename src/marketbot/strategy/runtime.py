"""Live strategies: the same signal code as the backtest, run on fresh klines.

A strategy only proposes actions. The engine applies them through the risk manager:
entries are sized or rejected by `size_trade`, stops can only move towards the market,
and closing is always allowed.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol

import polars as pl

from marketbot.backtest.signals import Donchian, Fade, OpeningRange, resample, with_indicators
from marketbot.backtest.trades import ExitRules
from marketbot.domain import Side
from marketbot.exchange.rest import BinanceREST
from marketbot.research.feasibility import EXCLUDED, IN_PLAY, IN_PLAY_MIN_QUOTE_VOLUME, TOP_VOLUME

KLINE_LIMIT = 600  # 50 hours of 5-minute bars: enough for 24-bar hourly channels and ATR


@dataclass(frozen=True)
class Propose:
    symbol: str
    side: Side
    stop: Decimal
    target: Decimal | None
    strategy: str
    why: str
    bucket: str = "btc_beta"


@dataclass(frozen=True)
class MoveStop:
    symbol: str
    stop: Decimal
    why: str


@dataclass(frozen=True)
class Close:
    symbol: str
    why: str


Action = Propose | MoveStop | Close


@dataclass(frozen=True)
class PositionView:
    """What a strategy may know about an open position."""

    symbol: str
    side: Side
    entry_price: Decimal
    stop: Decimal
    initial_risk: Decimal  # price distance from entry to the first stop
    opened_at: datetime
    strategy: str


class Strategy(Protocol):
    name: str

    async def step(self, now: datetime, positions: dict[str, PositionView]) -> list[Action]: ...


class Klines:
    """Public USD-M klines (from the live venue, also when executing on Demo Trading)."""

    def __init__(self, rest: BinanceREST, concurrency: int = 10) -> None:
        self._rest = rest
        self._semaphore = asyncio.Semaphore(concurrency)
        self._daily: tuple[date, pl.DataFrame] | None = None
        self._info: tuple[datetime, dict] | None = None
        self._daily_lock = asyncio.Lock()

    async def daily(self, today: date) -> pl.DataFrame:
        """The last few daily bars of every perpetual, fetched once per day for all users."""
        async with self._daily_lock:
            if self._daily is None or self._daily[0] != today:
                symbols = await self.perpetuals()
                frames = await asyncio.gather(
                    *(self.get(symbol, "1d", 3) for symbol in symbols), return_exceptions=True
                )
                daily = pl.concat(
                    [f for f in frames if isinstance(f, pl.DataFrame) and f.height]
                ).select(
                    "symbol",
                    day=pl.col("open_time").dt.date(),
                    open="open",
                    close="close",
                    quote_volume="quote_volume",
                )
                self._daily = (today, daily)
            return self._daily[1]

    async def get(self, symbol: str, interval: str, limit: int) -> pl.DataFrame:
        async with self._semaphore:
            rows = await self._rest.public(
                "GET", "/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": limit}
            )
        return parse_klines(symbol, rows)

    async def exchange_info(self, max_age: timedelta = timedelta(minutes=10)) -> dict:
        now = datetime.now(UTC)
        if self._info is None or now - self._info[0] > max_age:
            self._info = (now, await self._rest.public("GET", "/fapi/v1/exchangeInfo"))
        return self._info[1]

    async def quote_volumes(self) -> dict[str, float]:
        """24-hour quote volume of every symbol (one request, weight 40)."""
        rows = await self._rest.public("GET", "/fapi/v1/ticker/24hr")
        return {row["symbol"]: float(row["quoteVolume"]) for row in rows}

    async def perpetuals(self) -> list[str]:
        info = await self.exchange_info()
        return [
            row["symbol"]
            for row in info["symbols"]
            if row.get("contractType") == "PERPETUAL"
            and row.get("quoteAsset") == "USDT"
            and row.get("status") == "TRADING"
            and row["symbol"] not in EXCLUDED
        ]


def parse_klines(symbol: str, rows: list[list[Any]]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "symbol": [symbol] * len(rows),
            "open_time": [int(row[0]) for row in rows],
            "open": [float(row[1]) for row in rows],
            "high": [float(row[2]) for row in rows],
            "low": [float(row[3]) for row in rows],
            "close": [float(row[4]) for row in rows],
            "quote_volume": [float(row[7]) for row in rows],
            "close_ms": [int(row[6]) for row in rows],
        },
        schema_overrides={"open_time": pl.Int64, "close_ms": pl.Int64},
    ).with_columns(pl.col("open_time").cast(pl.Datetime("ms", "UTC")))


def closed_only(bars: pl.DataFrame, now: datetime) -> pl.DataFrame:
    """Drop the bar that is still forming."""
    now_ms = int(now.timestamp() * 1000)
    return bars.filter(pl.col("close_ms") < now_ms).drop("close_ms")


def choose_universe(daily: pl.DataFrame, universe: str, day: date) -> list[str]:
    """Symbols for `day` from the previous day's volume and move (research 01 universes)."""
    previous = daily.filter(pl.col("day") == day - timedelta(days=1)).with_columns(
        ret=pl.col("close") / pl.col("open") - 1
    )
    if universe == "top volume":
        chosen = previous.sort("quote_volume", descending=True).head(TOP_VOLUME)
    elif universe == "in play":
        chosen = (
            previous.filter(pl.col("quote_volume") >= IN_PLAY_MIN_QUOTE_VOLUME)
            .sort(pl.col("ret").abs(), descending=True)
            .head(IN_PLAY)
        )
    else:
        raise ValueError(universe)
    return sorted(chosen["symbol"].to_list())


@dataclass(frozen=True)
class SignalStrategyConfig:
    signal: Donchian | Fade | OpeningRange
    exits: ExitRules
    universe: str  # "top volume" or "in play"
    btc_filter: bool = False
    signal_every: str = "1h"


class SignalStrategy:
    """Runs one backtested signal family live, with its exit rules."""

    def __init__(
        self,
        config: SignalStrategyConfig,
        klines: Klines,
        *,
        journal: Callable[..., Any] = lambda *a, **k: None,
    ) -> None:
        self.config = config
        self.name = config.signal.tag
        self._klines = klines
        self._journal = journal
        self._universe_day: date | None = None
        self.universe: list[str] = []
        self._last_bar: datetime | None = None

    async def refresh_universe(self, today: date) -> None:
        if self._universe_day == today:
            return
        daily = await self._klines.daily(today)
        self.universe = choose_universe(daily, self.config.universe, today)
        self._universe_day = today
        self._journal("universe", strategy=self.name, day=today, symbols=self.universe)

    async def step(self, now: datetime, positions: dict[str, PositionView]) -> list[Action]:
        bar = now.replace(second=0, microsecond=0) - timedelta(minutes=now.minute % 5)
        if bar == self._last_bar:
            return []  # act once per closed 5-minute bar
        self._last_bar = bar
        await self.refresh_universe(now.date())
        actions: list[Action] = []
        mine = {s: p for s, p in positions.items() if p.strategy == self.name}
        symbols = sorted(set(self.universe) | set(mine))
        frames = await asyncio.gather(
            *(self._klines.get(symbol, "5m", KLINE_LIMIT) for symbol in symbols),
            return_exceptions=True,
        )
        trend = await self._btc_trend(now) if self.config.btc_filter else 0
        for symbol, frame in zip(symbols, frames, strict=True):
            if not isinstance(frame, pl.DataFrame) or frame.height < 100:
                continue
            bars = closed_only(frame, now)
            if symbol in mine:
                actions += manage(mine[symbol], bars, self.config, now)
            elif symbol in self.universe and symbol not in positions:
                proposal = self._entry(symbol, bars, now, trend)
                if proposal is not None:
                    actions.append(proposal)
        return actions

    def _entry(self, symbol: str, bars: pl.DataFrame, now: datetime, trend: int) -> Propose | None:
        signal = self.config.signal
        if isinstance(signal, OpeningRange):
            signals = signal.signals(bars)
        else:
            every = self.config.signal_every
            signals = signal.signals(with_indicators(resample(bars, every), every))
        if signals.is_empty():
            return None
        latest = signals.sort("entry_time").tail(1).row(0, named=True)
        # Only a signal whose entry bar is the one opening now (no stale signals).
        entry_time = latest["entry_time"]
        if not timedelta(0) <= now - entry_time < timedelta(minutes=5):
            return None
        direction = int(latest["direction"])
        if self.config.btc_filter and direction != trend:
            return None
        side = Side.LONG if direction > 0 else Side.SHORT
        stop = Decimal(str(latest["stop"]))
        last_close = Decimal(str(bars["close"][-1]))
        target = None
        if self.config.exits.target_r:
            risk = abs(last_close - stop)
            target = last_close + direction * Decimal(str(self.config.exits.target_r)) * risk
        return Propose(symbol, side, stop, target, self.name, f"{self.name} signal at {entry_time}")

    async def _btc_trend(self, now: datetime) -> int:
        bars = closed_only(await self._klines.get("BTCUSDT", "1h", 50), now)
        close = bars["close"]
        ema = close.ewm_mean(span=24)
        return int((close[-1] > ema[-1]) - (close[-1] < ema[-1]))


def manage(
    position: PositionView, bars: pl.DataFrame, config: SignalStrategyConfig, now: datetime
) -> list[Action]:
    """Exit rules of the backtest, applied to an open position on closed bars."""
    exits = config.exits
    direction = 1 if position.side is Side.LONG else -1
    if exits.day_end and now.astimezone(UTC).hour == 23 and now.minute >= 55:
        return [Close(position.symbol, "day end")]
    held = bars.filter(pl.col("open_time") >= position.opened_at)
    if held.height >= exits.max_bars:
        return [Close(position.symbol, "time stop")]
    if held.is_empty():
        return []
    extreme = held["high"].max() if direction > 0 else held["low"].min()
    stop = position.stop
    candidates = []
    if exits.breakeven_r is not None:
        reached = (Decimal(str(extreme)) - position.entry_price) * direction
        if reached >= Decimal(str(exits.breakeven_r)) * position.initial_risk:
            candidates.append(position.entry_price)
    if exits.trail_atr is not None:
        every = config.signal_every
        atr = with_indicators(resample(bars, every), every)["atr"].drop_nulls()
        if atr.len():
            trail = Decimal(str(extreme - direction * exits.trail_atr * atr[-1]))
            candidates.append(trail)
    tighter = [c for c in candidates if (c - stop) * direction > 0]
    if not tighter:
        return []
    best = max(tighter) if direction > 0 else min(tighter)
    return [MoveStop(position.symbol, best, "trailing" if exits.trail_atr else "break-even")]
