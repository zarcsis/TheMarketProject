from datetime import UTC, date, datetime, timedelta

import numpy as np
import polars as pl
import pytest

from marketbot.research.feasibility import (
    GameRules,
    Rule,
    Universe,
    apply_rules,
    breakeven_target_share,
    daily_features,
    monte_carlo,
    select_universes,
    simulate_windows,
    summarize,
)

DAY = datetime(2026, 9, 1, tzinfo=UTC)
RULES = GameRules(leverage=5)


def bars(rows: list[tuple[float, float, float, float]], symbol: str = "SOLUSDT") -> pl.DataFrame:
    return pl.DataFrame(
        {
            "symbol": [symbol] * len(rows),
            "open_time": [DAY + timedelta(minutes=5 * i) for i in range(len(rows))],
            "open": [float(r[0]) for r in rows],
            "high": [float(r[1]) for r in rows],
            "low": [float(r[2]) for r in rows],
            "close": [float(r[3]) for r in rows],
        }
    )


def outcome(rows) -> dict:
    return simulate_windows(bars(rows), RULES, min_bars=1).row(0, named=True)


def test_target_and_stop_with_fees() -> None:
    # At 5x: long target 100 * (1 + 0.02 + 0.001) = 102.1; short stop near 101.6.
    result = outcome([(100, 101, 99.5, 100.5), (101, 102.2, 100.8, 102)])
    assert result["long_outcome"] == "target"
    assert result["long_return"] == pytest.approx(0.10)
    assert result["short_outcome"] == "stop"
    assert result["short_return"] == pytest.approx(-0.09)


def test_gap_through_stop_fills_at_the_open() -> None:
    result = outcome([(100, 100.5, 99.5, 100), (103, 103.5, 102.5, 103)])
    assert result["short_outcome"] == "stop"
    assert result["short_return"] == pytest.approx(-5 * (103 * 1.001 / 100 - 1) - 0.005)


def test_bar_touching_both_levels_counts_as_stop() -> None:
    result = outcome([(100, 103, 97, 100)])
    assert result["long_outcome"] == "stop"
    assert result["short_outcome"] == "stop"
    assert result["long_ambiguous"]
    assert result["short_ambiguous"]


def test_open_window_closes_at_the_last_bar() -> None:
    result = outcome([(100, 100.5, 99.5, 100), (100, 101, 99.8, 101)])
    assert result["long_outcome"] == "open"
    assert result["long_return"] == pytest.approx(5 * 0.01 - 0.005)
    assert result["short_return"] == pytest.approx(-5 * 0.01 - 0.005)


def test_short_days_are_skipped() -> None:
    assert simulate_windows(bars([(100, 101, 99, 100)]), RULES).is_empty()


def daily_klines(rows: list[tuple[str, int, float, float, float]]) -> pl.DataFrame:
    """(symbol, day offset, open, close, quote volume) rows as daily klines."""
    return pl.DataFrame(
        {
            "symbol": [r[0] for r in rows],
            "open_time": [DAY + timedelta(days=r[1]) for r in rows],
            "open": [r[2] for r in rows],
            "high": [max(r[2], r[3]) for r in rows],
            "low": [min(r[2], r[3]) for r in rows],
            "close": [float(r[3]) for r in rows],
            "quote_volume": [r[4] for r in rows],
        }
    )


def test_universes_use_only_the_previous_day() -> None:
    daily = daily_features(
        daily_klines(
            [
                ("AUSDT", 0, 100, 150, 1e9),  # big mover and volume on day 0
                ("AUSDT", 1, 150, 150, 1e3),
                ("BUSDT", 0, 100, 101, 1e3),
                ("BUSDT", 1, 101, 200, 1e9),  # big move on day 1 only
                ("USDCUSDT", 0, 1, 1, 1e12),
                ("USDCUSDT", 1, 1, 1, 1e12),
            ]
        )
    )
    universes = select_universes(daily)
    day1 = universes.filter(pl.col("day") == date(2026, 9, 2))
    in_play = day1.filter(pl.col("universe") == Universe.IN_PLAY.value)["symbol"].to_list()
    assert in_play == ["AUSDT"]
    assert "USDCUSDT" not in universes["symbol"].to_list()
    assert universes.filter(pl.col("day") == date(2026, 9, 1)).is_empty()


def test_rules_pick_sides_from_the_previous_day() -> None:
    windows = pl.DataFrame(
        {
            "symbol": ["AUSDT"],
            "day": [date(2026, 9, 2)],
            "long_outcome": ["target"],
            "long_return": [0.10],
            "short_outcome": ["stop"],
            "short_return": [-0.09],
            "long_ambiguous": [False],
            "short_ambiguous": [False],
        }
    )
    daily = daily_features(daily_klines([("AUSDT", 0, 100, 90, 1e9), ("AUSDT", 1, 90, 99, 1e9)]))
    ruled = apply_rules(windows, daily)
    by_rule = {row["rule"]: row["ret"] for row in ruled.iter_rows(named=True)}
    assert by_rule[Rule.MOMENTUM.value] == pytest.approx(-0.09)  # day 0 fell, so short
    assert by_rule[Rule.REVERSAL.value] == pytest.approx(0.10)
    summary = summarize(ruled.filter(pl.col("rule") == Rule.COIN_FLIP.value), ["rule"])
    assert summary["windows"][0] == 1
    assert summary["target %"][0] == pytest.approx(50.0)
    assert summary["mean return %"][0] == pytest.approx(0.5)


def test_breakeven_share() -> None:
    assert breakeven_target_share() == pytest.approx(0.4974, abs=1e-4)


def test_monte_carlo_extremes() -> None:
    winning = monte_carlo(np.array([0.10]), 7, paths=10)
    assert winning.median_multiple == pytest.approx(1.1**7)
    assert winning.p_ladder == 1
    losing = monte_carlo(np.array([-0.09]), 30, paths=10)
    assert losing.p_project_pause == 1
    assert losing.median_multiple == pytest.approx(0.91**4)
