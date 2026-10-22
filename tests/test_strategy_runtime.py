from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import polars as pl
import pytest
from test_engine_drills import STOP_MARKET, Rig

from marketbot.backtest.signals import Donchian
from marketbot.backtest.trades import ExitRules
from marketbot.domain import Side
from marketbot.strategy.delisting import DelistingShort
from marketbot.strategy.listing import ListingShort
from marketbot.strategy.runtime import (
    Close,
    MoveStop,
    PositionView,
    Propose,
    SignalStrategyConfig,
    choose_universe,
    manage,
)

pytestmark = pytest.mark.anyio
D = Decimal


class Scripted:
    """A strategy that returns prepared actions once."""

    name = "scripted"

    def __init__(self, *actions) -> None:
        self.actions = list(actions)

    async def step(self, now, positions):
        actions, self.actions = self.actions, []
        return actions


async def rig_with(tmp_path: Path, strategy: Scripted, *, auto: bool = True) -> Rig:
    rig = Rig(tmp_path)
    rig.engine.strategies = [strategy]
    if auto:
        rig.store.enqueue("auto", {"on": True})
    await rig.tick()
    return rig


async def test_strategies_wait_for_auto_on(tmp_path: Path) -> None:
    strategy = Scripted(Propose("SOLUSDT", Side.LONG, D(98), None, "scripted", "test"))
    rig = await rig_with(tmp_path, strategy, auto=False)
    await rig.tick()
    assert not rig.exchange.holdings


async def test_strategy_entry_goes_through_the_risk_manager(tmp_path: Path) -> None:
    strategy = Scripted(Propose("SOLUSDT", Side.LONG, D(98), D(104), "scripted", "test"))
    rig = await rig_with(tmp_path, strategy)
    assert rig.exchange.holdings["SOLUSDT"].qty == D("1.36")
    managed = rig.engine.managed()["SOLUSDT"]
    assert (managed.strategy, managed.initial_stop) == ("scripted", D(98))


async def test_stops_only_tighten_and_crossed_stops_close(tmp_path: Path) -> None:
    strategy = Scripted(Propose("SOLUSDT", Side.LONG, D(98), None, "scripted", "test"))
    rig = await rig_with(tmp_path, strategy)
    strategy.actions = [MoveStop("SOLUSDT", D(97), "looser")]
    await rig.tick()
    assert [s.trigger_price for s in rig.stops()] == [D(98)]
    strategy.actions = [MoveStop("SOLUSDT", D(99), "trailing")]
    await rig.tick()
    assert [s.trigger_price for s in rig.stops()] == [D(99)]
    strategy.actions = [MoveStop("SOLUSDT", D(101), "break-even")]  # above the 100 mark
    await rig.tick()
    assert not rig.exchange.holdings


async def test_strategy_close(tmp_path: Path) -> None:
    strategy = Scripted(Propose("SOLUSDT", Side.LONG, D(98), None, "scripted", "test"))
    rig = await rig_with(tmp_path, strategy)
    strategy.actions = [Close("SOLUSDT", "day end")]
    await rig.tick()
    assert not rig.exchange.holdings
    await rig.tick()
    assert not [o for o in rig.exchange.conditional.values() if o.kind == STOP_MARKET]


def five_minute_bars(start: datetime, highs: list[float]) -> pl.DataFrame:
    n = len(highs)
    return pl.DataFrame(
        {
            "open_time": [start + timedelta(minutes=5 * i) for i in range(n)],
            "open": [h - 0.5 for h in highs],
            "high": highs,
            "low": [h - 1.0 for h in highs],
            "close": [h - 0.2 for h in highs],
            "quote_volume": [1.0] * n,
        }
    ).with_columns(pl.col("open_time").cast(pl.Datetime("ms", "UTC")))


def view(stop: str = "98") -> PositionView:
    return PositionView(
        "SOLUSDT",
        Side.LONG,
        D(100),
        D(stop),
        D(2),
        datetime(2026, 9, 1, 10, tzinfo=UTC),
        "scripted",
    )


def config(exits: ExitRules) -> SignalStrategyConfig:
    return SignalStrategyConfig(Donchian(), exits, "top volume")


def test_manage_break_even_and_day_end() -> None:
    bars = five_minute_bars(datetime(2026, 9, 1, 10, tzinfo=UTC), [100.5, 102.5])
    now = datetime(2026, 9, 1, 10, 10, tzinfo=UTC)
    exits = ExitRules(target_r=2, breakeven_r=1.0)
    assert manage(view(), bars, config(exits), now) == [MoveStop("SOLUSDT", D(100), "break-even")]
    assert manage(view("100"), bars, config(exits), now) == []
    late = datetime(2026, 9, 1, 23, 55, tzinfo=UTC)
    assert manage(view(), bars, config(exits), late) == [Close("SOLUSDT", "day end")]


def test_choose_universe_from_previous_day() -> None:
    daily = pl.DataFrame(
        {
            "symbol": ["A", "B", "C"],
            "day": [date(2026, 9, 1)] * 3,
            "open": [1.0, 1.0, 1.0],
            "close": [1.5, 0.95, 1.01],
            "quote_volume": [6e7, 9e9, 1e8],
        }
    )
    assert choose_universe(daily, "top volume", date(2026, 9, 2)) == ["A", "B", "C"]
    assert choose_universe(daily, "in play", date(2026, 9, 2))[0] == "A"


class FakeKlines:
    def __init__(self, info: dict, frames: dict) -> None:
        self.info = info
        self.frames = frames

    async def exchange_info(self) -> dict:
        return self.info

    async def get(self, symbol: str, interval: str, limit: int) -> pl.DataFrame:
        return self.frames[(symbol, interval)]


def daily_frame(rows: list[tuple[datetime, float, float]]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "open_time": [r[0] for r in rows],
            "close": [r[1] for r in rows],
            "quote_volume": [r[2] for r in rows],
        }
    ).with_columns(pl.col("open_time").cast(pl.Datetime("ms", "UTC")))


async def test_listing_short_enters_busy_listings_and_exits_after_hold() -> None:
    listed = datetime(2026, 10, 21, 7, 45, tzinfo=UTC)
    info = {
        "symbols": [
            {
                "symbol": symbol,
                "contractType": contract,
                "underlyingType": underlying,
                "quoteAsset": "USDT",
                "status": "TRADING",
                "onboardDate": int(listed.timestamp() * 1000),
            }
            for symbol, contract, underlying in (
                ("BUSYUSDT", "PERPETUAL", "COIN"),
                ("QUIETUSDT", "PERPETUAL", "COIN"),
                ("NKEUSDT", "TRADIFI_PERPETUAL", "EQUITY"),
            )
        ]
    }
    day0 = datetime(2026, 10, 21, tzinfo=UTC)
    frames = {
        ("BUSYUSDT", "1d"): daily_frame([(day0, 1.0, 9e7)]),
        ("QUIETUSDT", "1d"): daily_frame([(day0, 1.0, 1e6)]),
        ("BUSYUSDT", "1m"): daily_frame([(day0, 2.0, 0.0)]),
    }
    strategy = ListingShort(FakeKlines(info, frames))
    now = datetime(2026, 10, 22, 0, 6, tzinfo=UTC)
    [proposal] = await strategy.step(now, {})
    assert (proposal.symbol, proposal.side, proposal.bucket) == ("BUSYUSDT", Side.SHORT, "listing")
    assert proposal.stop == D("2.80")
    assert await strategy.step(now + timedelta(minutes=5), {}) == []  # decided once

    held = PositionView("BUSYUSDT", Side.SHORT, D(2), D("2.8"), D("0.8"), now, "listing-short")
    later = now + timedelta(days=7, minutes=10)
    assert await strategy.step(later, {"BUSYUSDT": held}) == [Close("BUSYUSDT", "held 7 days")]


async def test_delisting_short_enters_after_delay_and_closes_after_hold() -> None:
    released = datetime(2026, 10, 22, 7, 0, 30, tzinfo=UTC)

    async def fetch():
        return pl.DataFrame(
            {
                "title": ["Binance Will Delist ABC, XYZ on 2026-11-05"],
                "released": [released],
            }
        )

    info = {"symbols": []}
    klines = FakeKlines(info, {("ABCUSDT", "1m"): daily_frame([(released, 5.0, 0.0)])})

    async def perpetuals():
        return ["ABCUSDT"]

    klines.perpetuals = perpetuals
    strategy = DelistingShort(klines, fetch=fetch)
    assert await strategy.step(released + timedelta(minutes=1), {}) == []  # too early
    actions = await strategy.step(released + timedelta(minutes=6), {})
    assert [(a.symbol, a.side, a.stop) for a in actions] == [("ABCUSDT", Side.SHORT, D("6.00"))]
    # XYZ has no tradable perpetual; neither symbol is proposed twice.
    assert await strategy.step(released + timedelta(minutes=8), {}) == []
    held = PositionView(
        "ABCUSDT", Side.SHORT, D(5), D(6), D(1), released + timedelta(minutes=6), "delisting-short"
    )
    later = released + timedelta(hours=4, minutes=7)
    assert await strategy.step(later, {"ABCUSDT": held}) == [Close("ABCUSDT", "held 4:00:00")]


async def test_stale_delisting_announcement_is_skipped() -> None:
    released = datetime(2026, 10, 22, 7, 0, tzinfo=UTC)

    async def fetch():
        return pl.DataFrame(
            {"title": ["Binance Will Delist ABC on 2026-11-05"], "released": [released]}
        )

    strategy = DelistingShort(FakeKlines({"symbols": []}, {}), fetch=fetch)
    assert await strategy.step(released + timedelta(hours=1), {}) == []


async def test_delisting_short_prefers_liquid_symbols() -> None:
    released = datetime(2026, 10, 22, 7, 0, 30, tzinfo=UTC)

    async def fetch():
        return pl.DataFrame(
            {"title": ["Binance Will Delist AAA, BBB on 2026-11-05"], "released": [released]}
        )

    frames = {(s, "1m"): daily_frame([(released, 1.0, 0.0)]) for s in ("AAAUSDT", "BBBUSDT")}
    klines = FakeKlines({"symbols": []}, frames)

    async def perpetuals():
        return ["AAAUSDT", "BBBUSDT"]

    async def quote_volumes():
        return {"AAAUSDT": 1e6, "BBBUSDT": 5e7}

    klines.perpetuals = perpetuals
    klines.quote_volumes = quote_volumes
    strategy = DelistingShort(klines, fetch=fetch)
    actions = await strategy.step(released + timedelta(minutes=6), {})
    assert [a.symbol for a in actions] == ["BBBUSDT", "AAAUSDT"]


def test_strategy_memory_survives_a_restart(tmp_path: Path) -> None:
    from marketbot.state.store import Store
    from marketbot.strategy.runtime import Memory

    store = Store(tmp_path / "bot.db")
    memory = Memory(store, "strategy:test:done")
    memory.add("ABCUSDT@2026-10-22T07:00:00+00:00")
    assert "ABCUSDT@2026-10-22T07:00:00+00:00" in Memory(store, "strategy:test:done")
    assert "XYZUSDT@x" not in Memory(store, "strategy:test:done")
