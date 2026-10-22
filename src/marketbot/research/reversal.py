"""Research 12: cross-sectional weekly reversal and momentum in liquid altcoin perpetuals.

Each day D, the eligible perpetuals are the crypto USDT perpetuals listed for at least 30
days whose average quote volume over the last 7 days is at least `MIN_VOLUME`. They are
ranked by their return over the last 7 days (open of D-7 to close of D-1). The study
shorts the `N` biggest gainers and buys the `N` biggest losers at the open of D, equally
weighted, and holds each leg for 7 days to the open of D+7. Taker fees on both sides and
funding over the days held are included; there are no stops.

Cohorts starting every day overlap; the reported statistics use the cohorts that start on
Mondays, which do not overlap, so their t-statistics are honest.
"""

from datetime import date
from pathlib import Path

import numpy as np
import polars as pl

from marketbot.data import archive
from marketbot.research.feasibility import EXCLUDED, markdown_table

TAKER = 0.0005
MIN_VOLUME = 20_000_000.0
MIN_AGE_DAYS = 30
LOOKBACK = 7
HOLD = 7
COUNTS = (3, 5, 10)


def daily_panel(root: Path, exclude: frozenset[str]) -> pl.DataFrame:
    klines = archive.load("klines", root=root, interval="1d").filter(
        ~pl.col("symbol").is_in(EXCLUDED | exclude)
    )
    daily = klines.select(
        "symbol",
        day=pl.col("open_time").dt.date(),
        open="open",
        close="close",
        quote_volume="quote_volume",
    )
    funding = archive.load("fundingRate", root=root)
    per_day = funding.group_by("symbol", day=pl.col("calc_time").dt.date()).agg(
        funding=pl.col("last_funding_rate").sum()
    )
    return (
        daily.join(per_day, on=["symbol", "day"], how="left")
        .with_columns(pl.col("funding").fill_null(0.0))
        .sort("symbol", "day")
    )


def signals(panel: pl.DataFrame) -> pl.DataFrame:
    """Per symbol and day: the past return, liquidity, age, and the forward 7-day legs."""
    by = "symbol"
    return (
        panel.with_columns(
            past=pl.col("close").shift(1).over(by) / pl.col("open").shift(LOOKBACK).over(by) - 1,
            volume=pl.col("quote_volume").shift(1).rolling_mean(LOOKBACK).over(by),
            age=pl.int_range(pl.len()).over(by),
            forward=pl.col("open").shift(-HOLD).over(by) / pl.col("open") - 1,
            funding_held=pl.col("funding").rolling_sum(HOLD).shift(-(HOLD - 1)).over(by),
            span=(
                pl.col("day").shift(-HOLD).over(by) - pl.col("day").shift(LOOKBACK).over(by)
            ).dt.total_days(),
        )
        .filter(
            (pl.col("span") == LOOKBACK + HOLD)  # no gaps around the day
            & (pl.col("age") >= MIN_AGE_DAYS)
            & (pl.col("volume") >= MIN_VOLUME)
        )
        .with_columns(
            # A short receives positive funding; a long pays it.
            short=-pl.col("forward") - 2 * TAKER + pl.col("funding_held"),
            long=pl.col("forward") - 2 * TAKER - pl.col("funding_held"),
        )
        .drop_nulls(["past", "forward", "funding_held"])
    )


def cohorts(frame: pl.DataFrame, count: int) -> pl.DataFrame:
    """Per start day: the mean short return of the top `count` past gainers, the mean long
    return of the bottom `count` past losers, and the market (all eligible, long)."""
    ranked = frame.with_columns(
        rank_up=pl.col("past").rank("ordinal", descending=True).over("day"),
        rank_down=pl.col("past").rank("ordinal").over("day"),
        eligible=pl.len().over("day"),
    ).filter(pl.col("eligible") >= 4 * count)
    return (
        ranked.group_by("day")
        .agg(
            short_gainers=pl.col("short").filter(pl.col("rank_up") <= count).mean(),
            long_losers=pl.col("long").filter(pl.col("rank_down") <= count).mean(),
            short_losers=pl.col("short").filter(pl.col("rank_down") <= count).mean(),
            long_gainers=pl.col("long").filter(pl.col("rank_up") <= count).mean(),
            market=pl.col("long").mean(),
            eligible=pl.len(),
        )
        .sort("day")
    )


def _t(values: np.ndarray) -> float:
    if len(values) < 3 or values.std(ddof=1) == 0:
        return float("nan")
    return float(values.mean() / values.std(ddof=1) * np.sqrt(len(values)))


LEGS = {
    "reversal: short gainers": "short_gainers",
    "reversal: long losers": "long_losers",
    "momentum: long gainers": "long_gainers",
    "momentum: short losers": "short_losers",
    "market: long all eligible": "market",
}


def run(root: Path, exclude: frozenset[str], split: date) -> tuple[pl.DataFrame, str]:
    frame = signals(daily_panel(root, exclude))
    rows = []
    weekly_all = []
    for count in COUNTS:
        weekly = cohorts(frame, count).filter(pl.col("day").dt.weekday() == 1)
        weekly_all.append(weekly.with_columns(count=pl.lit(count)))
        for name, column in LEGS.items():
            if column == "market" and count != COUNTS[0]:
                continue
            row = {"leg": name, "names": count if column != "market" else None}
            for period, part in (
                ("first", weekly.filter(pl.col("day") < split)),
                ("second", weekly.filter(pl.col("day") >= split)),
                ("all", weekly),
            ):
                values = part[column].drop_nulls().to_numpy()
                row[f"{period}: weeks"] = len(values)
                row[f"{period}: mean %"] = float(values.mean()) * 100 if len(values) else None
                row[f"{period}: t"] = _t(values)
            rows.append(row)
    table = pl.DataFrame(rows)
    eligible = frame.group_by("day").len()
    text = "\n".join(
        [
            "# Research 12 results: weekly reversal and momentum",
            "",
            "Generated by `uv run mb research reversal`. The analysis is in "
            "[../12-reversal.md](../12-reversal.md).",
            "",
            f"- Universe: crypto USDT perpetuals listed for {MIN_AGE_DAYS}+ days with a "
            f"{LOOKBACK}-day average quote volume of at least {MIN_VOLUME / 1e6:.0f}M USDT; "
            f"{int(eligible['len'].median())} eligible on a median day.",
            f"- Signal: the return over the last {LOOKBACK} days. Legs hold {HOLD} days from "
            f"the open, equally weighted, after {TAKER:.2%} taker fees per side and funding.",
            "- Weeks: cohorts starting on Mondays only (no overlap). Mean per week and leg, "
            f"split at {split}.",
            "",
            markdown_table(table, 2),
            "",
        ]
    )
    return pl.concat(weekly_all), text
