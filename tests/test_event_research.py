from datetime import UTC, date, datetime, timedelta

import polars as pl
import pytest

from marketbot.research.airdrops import listing_kind
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


def test_listing_kind_uses_only_titles_known_at_entry() -> None:
    entry = datetime(2025, 3, 5, tzinfo=UTC)
    titles = [
        (
            datetime(2025, 3, 4, 5, tzinfo=UTC),
            "Introducing GoPlus Security (GPS) on Binance HODLer Airdrops!",
        ),
        (datetime(2025, 3, 6, tzinfo=UTC), "Introducing Late (LATE) on Binance HODLer Airdrops!"),
        (
            datetime(2025, 3, 4, tzinfo=UTC),
            "Binance Futures Will Launch USDⓈ-Margined LATEUSDT Perpetual",
        ),
        (
            datetime(2025, 2, 1, tzinfo=UTC),
            "Doodles (DOOD) Will Be Available on Binance Alpha and Binance Futures",
        ),
        (datetime(2021, 6, 1, tzinfo=UTC), "Binance Will List Dego Finance (DEGO)"),
    ]
    assert listing_kind("GPSUSDT", entry, titles) == "airdrop"
    assert listing_kind("LATEUSDT", entry, titles) == "futures only"  # the airdrop came later
    assert listing_kind("DOODUSDT", entry, titles) == "alpha"
    assert listing_kind("DEGOUSDT", entry, titles) == "spot"
    assert listing_kind("1000DEGOUSDT", entry, titles) == "spot"
    assert listing_kind("NONEUSDT", entry, titles) == "futures only"


def test_cliff_unlocks_are_sized_against_the_circulating_supply() -> None:
    from marketbot.data.unlocks import cliff_events

    day = 86_400
    document = {
        "id": "token",
        "gecko_id": "token",
        "categories": {"noncirculating": ["Treasury"], "insiders": ["Team"]},
        "documentedData": {
            "data": [
                {
                    "label": "Team",
                    "data": [
                        {"timestamp": t * day, "unlocked": u} for t, u in ((1, 0), (2, 0), (3, 50))
                    ],
                },
                {
                    "label": "Public",
                    "data": [{"timestamp": t * day, "unlocked": 100} for t in (1, 2, 3)],
                },
                {
                    "label": "Treasury",
                    "data": [{"timestamp": t * day, "unlocked": 900} for t in (1, 2, 3)],
                },
            ]
        },
        "metadata": {
            "events": [
                {
                    "timestamp": 1 * day,
                    "noOfTokens": [100],
                    "category": "publicSale",
                    "unlockType": "cliff",
                },
                {
                    "timestamp": 3 * day,
                    "noOfTokens": [50],
                    "category": "insiders",
                    "unlockType": "cliff",
                },
                {
                    "timestamp": 3 * day,
                    "noOfTokens": [900],
                    "category": "noncirculating",
                    "unlockType": "cliff",
                },
                {
                    "timestamp": 3 * day,
                    "noOfTokens": [5],
                    "category": "farming",
                    "unlockType": "linear",
                },
            ]
        },
    }
    [event] = cliff_events(document)  # the first-day unlock has no supply before it
    assert (event["category"], event["tokens"], event["supply_before"]) == ("insiders", 50.0, 100.0)


def test_unlock_windows_short_from_entry_to_exit_open() -> None:
    from marketbot.research.unlocks import windows

    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(6)]
    bars = pl.DataFrame(
        {
            "symbol": ["A"] * 6,
            "day": days,
            "open": [100.0, 100.0, 90.0, 80.0, 70.0, 60.0],
            "high": [100.0] * 6,
            "funding": [0.0, 0.001, 0.001, 0.001, 0.0, 0.0],
        }
    )
    window = windows(bars, 2, 1).filter(pl.col("day") == days[3])
    # Short at the open of Jan 2 (100), cover at the open of Jan 5 (70), funding of 3 days.
    expected = 0.30 - 0.0005 * 1.7 + 0.003
    assert window["with_funding"][0] == pytest.approx(expected)


def test_reversal_signals_use_only_past_data_for_the_rank() -> None:
    from marketbot.research.reversal import HOLD, LOOKBACK, MIN_AGE_DAYS, signals

    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(MIN_AGE_DAYS + 20)]
    opens = [100.0 + i for i in range(len(days))]
    panel = pl.DataFrame(
        {
            "symbol": ["A"] * len(days),
            "day": days,
            "open": opens,
            "close": [o + 1 for o in opens],
            "quote_volume": [1e9] * len(days),
            "funding": [0.001] * len(days),
        }
    )
    row = signals(panel).filter(pl.col("day") == days[MIN_AGE_DAYS]).row(0, named=True)
    i = MIN_AGE_DAYS
    assert row["past"] == pytest.approx((opens[i - 1] + 1) / opens[i - LOOKBACK] - 1)
    assert row["forward"] == pytest.approx(opens[i + HOLD] / opens[i] - 1)
    assert row["short"] == pytest.approx(-row["forward"] - 0.001 + HOLD * 0.001)


def test_forward_simulation_stops_holds_and_marks_open_trades() -> None:
    from marketbot.research.forward import Rule, simulate

    start = datetime(2026, 10, 1, tzinfo=UTC)
    times = [start + timedelta(minutes=5 * i) for i in range(12)]

    def bars(highs: list[float]) -> pl.DataFrame:
        return pl.DataFrame(
            {
                "open_time": times,
                "open": [100.0] * 12,
                "high": highs,
                "close": [90.0] * 12,
            }
        )

    funding = pl.DataFrame(
        {"time": [start + timedelta(minutes=20)], "rate": [0.01]},
        schema={"time": pl.Datetime("us", "UTC"), "rate": pl.Float64},
    )
    rule = Rule("test", 0.20, timedelta(minutes=30), timedelta(0), "")
    held = simulate(bars([100.0] * 12), funding, start, rule)
    assert held["status"] == "held"
    assert held["with_funding"] == pytest.approx(0.10 - 0.0005 * 1.9 + 0.01)
    stopped = simulate(bars([100.0, 125.0] + [100.0] * 10), funding, start, rule)
    assert stopped["status"] == "stopped"
    assert stopped["return"] == pytest.approx(-0.20 * 1.002 - 0.002 - 0.0005 * (1 + 1.2 * 1.002))
    long_rule = Rule("test", 0.20, timedelta(hours=2), timedelta(0), "")
    assert simulate(bars([100.0] * 12), funding, start, long_rule)["status"] == "open"


def test_distress_short_ends_with_the_perpetual() -> None:
    from marketbot.research.distress import removed_perpetuals, short_trade

    assert removed_perpetuals(
        "Binance Will Extend the Monitoring Tag to Include WAN, Remove the Monitoring Tag for "
        "GPS & MBL, and Remove the Seed Tag for BERA on 2025-10-09"
    ) == ["GPSUSDT", "MBLUSDT"]
    start = date(2026, 1, 1)
    bars = pl.DataFrame(
        {
            "day": [start + timedelta(days=i) for i in range(4)],
            "open": [100.0, 90.0, 80.0, 70.0],
            "high": [100.0, 90.0, 80.0, 70.0],
            "close": [90.0, 80.0, 70.0, 60.0],
            "funding": [0.0] * 4,
        }
    )
    held = short_trade(bars, start, 2, None)  # exit at the open of day 2
    assert held["ret"] == pytest.approx(0.20 - 0.0005 * 1.8)
    delisted = short_trade(bars, start, 10, None)  # bars end: exit at the last close
    assert delisted["ended_early"] and delisted["ret"] == pytest.approx(0.40 - 0.0005 * 1.6)
