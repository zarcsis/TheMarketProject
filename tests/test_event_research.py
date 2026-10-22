from datetime import UTC, date, datetime, timedelta

import polars as pl
import pytest

from marketbot.research.delistings import study_event
from marketbot.research.listings import Variant, simulate_short
from marketbot.research.portfolio import EventTrade, simulate

START = datetime(2026, 9, 1, tzinfo=UTC)


def daily(rows: list[tuple[float, float, float]]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "symbol": ["NEWUSDT"] * len(rows),
            "day": [date(2026, 9, 1) + timedelta(days=i) for i in range(len(rows))],
            "open": [r[0] for r in rows],
            "high": [r[1] for r in rows],
            "close": [r[2] for r in rows],
            "funding": [-0.001] * len(rows),
        }
    )


def test_listing_short_hold_and_stop() -> None:
    days = daily([(1.0, 1.2, 1.0), (1.0, 1.05, 0.9), (0.9, 0.95, 0.8), (0.8, 0.85, 0.7)])
    held = simulate_short(days, Variant(entry_day=1, hold_days=3, stop=0.4))
    assert held["reason"] == "time"
    assert held["ret"] == pytest.approx((1.0 - 0.7) / 1.0 - 0.0005 * 1.7)
    assert held["funding"] == pytest.approx(-0.003)
    stopped = simulate_short(
        daily([(1.0, 1.0, 1.0), (1.0, 1.5, 1.4), (1.4, 1.5, 1.4)]), Variant(1, 2, 0.4)
    )
    assert stopped["reason"] == "stop"
    assert stopped["held"] == 1


def five_minute(prices: list[tuple[float, float, float, float]]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "open_time": [START + timedelta(minutes=5 * i) for i in range(len(prices))],
            "open": [p[0] for p in prices],
            "high": [p[1] for p in prices],
            "low": [p[2] for p in prices],
            "close": [p[3] for p in prices],
        }
    ).with_columns(pl.col("open_time").cast(pl.Datetime("ms", "UTC")))


def test_event_study_directions() -> None:
    flat = [(100.0, 100.5, 99.5, 100.0)] * 300
    falling = [
        (100.0 - i * 0.1, 100.2 - i * 0.1, 99.8 - i * 0.1, 99.9 - i * 0.1) for i in range(60)
    ]
    bars = five_minute(flat + falling)
    released = START + timedelta(minutes=5 * 299, seconds=10)
    short = study_event(bars, released, direction=-1)
    long = study_event(bars, released, direction=1)
    assert short["short 1h"] > 0 > long["long 1h"]
    assert short["short 1h"] == pytest.approx(-long["long 1h"] - 2 * 0.001, abs=1e-9)


def test_portfolio_bucket_limits_and_sizing() -> None:
    t0 = START
    trades = [
        EventTrade(t0, t0 + timedelta(hours=4), "delisting", 0.20, 0.10),
        EventTrade(t0, t0 + timedelta(hours=4), "delisting", 0.20, 0.10),
        EventTrade(t0, t0 + timedelta(hours=4), "delisting", 0.20, 0.10),  # third in bucket
    ]
    equity, drawdown, taken = simulate(trades, risk=0.03, max_open=3, per_bucket=2, seed=1)
    assert taken == 2
    notional = 0.03 / (0.20 + 0.004)
    assert equity == pytest.approx(1 + 2 * notional * 0.10)
    assert drawdown == 0
