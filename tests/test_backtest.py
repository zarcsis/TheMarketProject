from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl
import pytest

from marketbot.backtest.signals import Donchian, OpeningRange, resample, with_indicators
from marketbot.backtest.trades import Bars, Costs, Entry, ExitRules, simulate

NO_COSTS = Costs(taker_fee=0, entry_slippage=0, stop_slippage=0)
START = datetime(2026, 9, 1)


def bars(rows: list[tuple[float, float, float, float]], atr: float = 1.0, start=START) -> Bars:
    n = len(rows)
    times = np.array([start + timedelta(minutes=5 * i) for i in range(n)], dtype="datetime64[ms]")
    array = np.array(rows, dtype=float)
    return Bars(
        "SOLUSDT", times, array[:, 0], array[:, 1], array[:, 2], array[:, 3], np.full(n, atr)
    )


def one(b: Bars, entry: Entry, rules: ExitRules, costs: Costs = NO_COSTS):
    [trade] = simulate(b, [entry], rules, costs)
    return trade


def test_target_hit_gives_target_r() -> None:
    b = bars([(100, 101, 99.5, 100.5), (100.5, 104.5, 100, 104)])
    trade = one(b, Entry(0, 1, 98), ExitRules(target_r=2))
    assert (trade.reason, trade.r) == ("target", pytest.approx(2.0))


def test_stop_first_when_bar_touches_both() -> None:
    b = bars([(100, 105, 97, 100)])
    assert one(b, Entry(0, 1, 98), ExitRules(target_r=2)).reason == "stop"


def test_gap_through_stop_fills_at_open() -> None:
    b = bars([(100, 100.5, 99.5, 100), (95, 96, 94, 95)])
    trade = one(b, Entry(0, 1, 98), ExitRules(target_r=2))
    assert trade.exit == 95
    assert trade.r == pytest.approx(-2.5)


def test_short_with_costs() -> None:
    costs = Costs(taker_fee=0.001, entry_slippage=0, stop_slippage=0)
    b = bars([(100, 100.5, 95.5, 96)])
    trade = one(b, Entry(0, -1, 102), ExitRules(target_r=2), costs)
    # Target 96; fees 0.1% on 100 and 96 = 0.196; risk 2.
    assert trade.r == pytest.approx((4 - 0.196) / 2)


def test_trailing_stop_applies_from_the_next_bar() -> None:
    rows = [(100, 101, 99.8, 101), (101, 106, 101, 105.5), (105.5, 105.6, 103.4, 104)]
    trade = one(bars(rows), Entry(0, 1, 98), ExitRules(target_r=None, trail_atr=2.0))
    # After bar 1 the extreme is 106, so the stop trails to 104; bar 2 trades down to 103.4.
    assert (trade.reason, trade.exit) == ("stop", 104)


def test_breakeven_and_day_end() -> None:
    late = START.replace(hour=23, minute=50)
    rows = [(100, 102.5, 100, 102), (102, 102.2, 101, 101.5)]
    trade = one(bars(rows, start=late), Entry(0, 1, 98), ExitRules(target_r=None, breakeven_r=0.5))
    assert trade.reason == "day end"
    assert trade.exit == 101.5


def test_one_position_per_symbol() -> None:
    b = bars([(100, 100.5, 99.5, 100)] * 5)
    trades = simulate(b, [Entry(0, 1, 98), Entry(1, 1, 98)], ExitRules(max_bars=3), NO_COSTS)
    assert len(trades) == 1


def hourly(closes: list[float], volume: list[float]) -> pl.DataFrame:
    n = len(closes)
    start = datetime(2026, 9, 1, tzinfo=UTC)
    frame = pl.DataFrame(
        {
            "open_time": [start + timedelta(hours=i) for i in range(n)],
            "open": closes,
            "high": [c + 0.5 for c in closes],
            "low": [c - 0.5 for c in closes],
            "close": closes,
            "quote_volume": volume,
        }
    ).with_columns(pl.col("open_time").cast(pl.Datetime("ms", "UTC")))
    return with_indicators(frame, "1h", atr_n=3)


def test_donchian_breakout_uses_prior_bars_only() -> None:
    sig = hourly([100, 100, 100, 100, 103], [1, 1, 1, 1, 5])
    signals = Donchian(n=3, volume_mult=2, stop_atr=1).signals(sig)
    assert signals["direction"].to_list() == [1]
    assert signals["entry_time"][0] == datetime(2026, 9, 1, 5, tzinfo=UTC)


def test_resample_to_hours() -> None:
    start = datetime(2026, 9, 1, tzinfo=UTC)
    five = pl.DataFrame(
        {
            "open_time": [start + timedelta(minutes=5 * i) for i in range(24)],
            "open": [float(i) for i in range(24)],
            "high": [float(i) + 1 for i in range(24)],
            "low": [float(i) - 1 for i in range(24)],
            "close": [float(i) + 0.5 for i in range(24)],
            "quote_volume": [1.0] * 24,
        }
    ).with_columns(pl.col("open_time").cast(pl.Datetime("ms", "UTC")))
    hours = resample(five, "1h")
    assert hours["open"].to_list() == [0.0, 12.0]
    assert hours["high"].to_list() == [12.0, 24.0]
    assert hours["quote_volume"].to_list() == [12.0, 12.0]


def test_opening_range_first_breakout() -> None:
    start = datetime(2026, 9, 1, tzinfo=UTC)
    rows = []
    for i in range(12 * 4):
        price = 100.0 if i < 24 else 100.0 + (i - 24) * 0.2
        rows.append((start + timedelta(minutes=5 * i), price, price + 0.1, price - 0.1, price))
    five = pl.DataFrame(rows, schema=["open_time", "open", "high", "low", "close"], orient="row")
    five = five.with_columns(pl.col("open_time").cast(pl.Datetime("ms", "UTC")))
    signals = OpeningRange(range_hours=2).signals(five)
    assert signals["direction"].to_list() == [1]
    assert signals["stop"][0] == pytest.approx(100.0)
