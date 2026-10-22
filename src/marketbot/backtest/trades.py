"""Trade-level backtest: simulate entries bar by bar on intraday klines.

Conventions, chosen to avoid look-ahead and to err against the strategy:
- A signal uses closed bars only; the entry fills at the next bar's open plus slippage.
- Each bar checks the stop first, then the target. A bar that touches both counts as a stop.
- A stop that the bar opens beyond fills at the open (a gap), minus stop slippage.
- Trailing stops and break-even moves use the bar's high or low after the stop check, so
  they apply from the next bar on.
- Fees are taker fees on both sides (the bot exits with market and stop-market orders).
- One position per symbol at a time; signals during a trade are ignored.
"""

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np


@dataclass(frozen=True)
class Costs:
    taker_fee: float = 0.0005
    entry_slippage: float = 0.0002
    stop_slippage: float = 0.001


@dataclass(frozen=True)
class ExitRules:
    target_r: float | None = 2.0  # take profit at entry + target_r * risk
    trail_atr: float | None = None  # chandelier stop: extreme since entry -/+ trail_atr * ATR
    breakeven_r: float | None = None  # move the stop to entry once price reaches +breakeven_r R
    max_bars: int = 288  # time stop
    day_end: bool = True  # close at the last bar of the UTC day


@dataclass(frozen=True)
class Entry:
    index: int  # bar whose open is the entry
    direction: int  # +1 long, -1 short
    stop: float
    tag: str = ""


@dataclass(frozen=True)
class Trade:
    symbol: str
    entry_time: datetime
    exit_time: datetime
    direction: int
    entry: float
    exit: float
    stop: float
    risk: float  # price distance from entry to the initial stop
    r: float  # result in multiples of risk, after costs
    ret: float  # price return of the position after costs (unleveraged)
    reason: str
    bars: int
    tag: str = ""


@dataclass
class Bars:
    """One symbol's klines as numpy arrays (sorted by time)."""

    symbol: str
    time: np.ndarray  # datetime64[ms]
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    atr: np.ndarray  # ATR on the signal timeframe, aligned to these bars (known at bar open)
    day: np.ndarray = field(init=False)

    def __post_init__(self) -> None:
        self.day = self.time.astype("datetime64[D]")


def simulate(bars: Bars, entries: list[Entry], rules: ExitRules, costs: Costs) -> list[Trade]:
    trades: list[Trade] = []
    n = len(bars.open)
    busy_until = -1
    for entry in sorted(entries, key=lambda e: e.index):
        i = entry.index
        if i <= busy_until or i >= n:
            continue
        trade = _simulate_one(bars, entry, rules, costs)
        if trade is None:
            continue
        trades.append(trade)
        busy_until = i + trade.bars - 1
    return trades


def _simulate_one(bars: Bars, entry: Entry, rules: ExitRules, costs: Costs) -> Trade | None:
    i, d = entry.index, entry.direction
    o, h, lo, c = bars.open, bars.high, bars.low, bars.close
    price = o[i] * (1 + d * costs.entry_slippage)
    stop = entry.stop
    risk = (price - stop) * d
    if not np.isfinite(risk) or risk <= 0:
        return None  # the market opened beyond the stop
    target = price + d * rules.target_r * risk if rules.target_r else None
    last = min(len(o) - 1, i + rules.max_bars - 1)
    if rules.day_end:
        same_day = np.nonzero(bars.day[i : last + 1] == bars.day[i])[0]
        last = i + int(same_day[-1])
    extreme = price
    exit_price, reason, j = c[last], "time", last
    for j in range(i, last + 1):
        if (lo[j] <= stop) if d > 0 else (h[j] >= stop):
            fill = min(stop, o[j]) if d > 0 else max(stop, o[j])
            exit_price, reason = fill * (1 - d * costs.stop_slippage), "stop"
            break
        if target is not None and ((h[j] >= target) if d > 0 else (lo[j] <= target)):
            exit_price = max(target, o[j]) if d > 0 else min(target, o[j])
            reason = "target"
            break
        extreme = max(extreme, h[j]) if d > 0 else min(extreme, lo[j])
        if rules.trail_atr is not None and np.isfinite(bars.atr[j]):
            trail = extreme - d * rules.trail_atr * bars.atr[j]
            stop = max(stop, trail) if d > 0 else min(stop, trail)
        if rules.breakeven_r is not None and (extreme - price) * d >= rules.breakeven_r * risk:
            stop = max(stop, price) if d > 0 else min(stop, price)
    else:
        exit_price, reason, j = c[last], ("day end" if rules.day_end else "time"), last
    fees = (price + exit_price) * costs.taker_fee
    pnl = (exit_price - price) * d - fees
    return Trade(
        symbol=bars.symbol,
        entry_time=bars.time[i].astype(datetime),
        exit_time=bars.time[j].astype(datetime),
        direction=d,
        entry=float(price),
        exit=float(exit_price),
        stop=float(entry.stop),
        risk=float(risk),
        r=float(pnl / risk),
        ret=float(pnl / price),
        reason=reason,
        bars=j - i + 1,
        tag=entry.tag,
    )
