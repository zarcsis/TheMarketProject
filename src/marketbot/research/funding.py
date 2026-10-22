"""Research 06: do extreme funding rates predict the next days' moves?

Funding is normalized to an 8-hour period. A symbol-day is extreme when its most negative
rate is at or below a negative threshold (crowded shorts) or its most positive rate is at or
above a positive threshold (crowded longs). Positions open at the next day's open, in either
direction, and hold 1 or 3 days with a 20% stop on daily highs and lows; funding paid or
received over the holding days is included.
"""

from pathlib import Path

import numpy as np
import polars as pl

from marketbot.data import archive
from marketbot.research.feasibility import EXCLUDED, markdown_table

TAKER = 0.0005
STOP = 0.20
STOP_SLIPPAGE = 0.002
THRESHOLDS = {"negative": (-0.003, -0.01), "positive": (0.001, 0.003)}
HOLDS = (1, 3)


def daily_with_funding(root: Path, exclude: frozenset[str]) -> pl.DataFrame:
    klines = archive.load("klines", root=root, interval="1d").filter(
        ~pl.col("symbol").is_in(EXCLUDED | exclude)
    )
    daily = klines.select(
        "symbol",
        day=pl.col("open_time").dt.date(),
        open="open",
        high="high",
        low="low",
        close="close",
        quote_volume="quote_volume",
    )
    funding = archive.load("fundingRate", root=root).with_columns(
        per8h=pl.col("last_funding_rate") * 8 / pl.col("funding_interval_hours").clip(1, 8)
    )
    per_day = funding.group_by("symbol", day=pl.col("calc_time").dt.date()).agg(
        min_funding=pl.col("per8h").min(),
        max_funding=pl.col("per8h").max(),
        funding_sum=pl.col("last_funding_rate").sum(),
    )
    return (
        daily.join(per_day, on=["symbol", "day"], how="left")
        .with_columns(pl.col("funding_sum").fill_null(0.0))
        .sort("symbol", "day")
    )


def forward(days: pl.DataFrame, index: int, hold: int, direction: int) -> float | None:
    if index + hold > days.height:
        return None
    window = days.slice(index, hold)
    opens, highs, lows, closes = (window[c].to_numpy() for c in ("open", "high", "low", "close"))
    entry = opens[0]
    level = entry * (1 - direction * STOP)
    exit_price, held = closes[-1], hold
    for i in range(hold):
        adverse = lows[i] <= level if direction > 0 else highs[i] >= level
        if adverse:
            worse = min(level, opens[i]) if direction > 0 else max(level, opens[i])
            exit_price, held = worse * (1 - direction * STOP_SLIPPAGE), i + 1
            break
    price_return = direction * (exit_price / entry - 1) - 2 * TAKER
    # Longs pay positive funding; shorts receive it.
    funding = -direction * float(window["funding_sum"][:held].sum())
    return float(price_return + funding)


def run(root: Path, exclude: frozenset[str], split) -> tuple[pl.DataFrame, str]:
    data = daily_with_funding(root, exclude)
    rows = []
    for symbol, days in data.group_by("symbol"):
        days = days.sort("day")
        mins, maxs = days["min_funding"].to_numpy(), days["max_funding"].to_numpy()
        volumes = days["quote_volume"].to_numpy()
        for i in range(len(days) - 1):
            if volumes[i] < 10_000_000:
                continue  # too thin to trade
            for side, (mild, strong) in THRESHOLDS.items():
                value = mins[i] if side == "negative" else maxs[i]
                if value is None or np.isnan(value):
                    continue
                hit = value <= mild if side == "negative" else value >= mild
                if not hit:
                    continue
                level = (
                    "strong"
                    if (value <= strong if side == "negative" else value >= strong)
                    else "mild"
                )
                for hold in HOLDS:
                    for direction in (1, -1):
                        result = forward(days, i + 1, hold, direction)
                        if result is not None:
                            rows.append(
                                {
                                    "symbol": symbol[0],
                                    "day": days["day"][i],
                                    "funding": side,
                                    "level": level,
                                    "hold": hold,
                                    "trade": "long" if direction > 0 else "short",
                                    "ret": result,
                                }
                            )
    trades = pl.DataFrame(rows).with_columns(
        period=pl.when(pl.col("day") < split)
        .then(pl.lit("first year"))
        .otherwise(pl.lit("second year"))
    )
    summary = (
        trades.group_by("funding", "level", "trade", "hold", "period")
        .agg(
            pl.len().alias("trades"),
            pl.col("symbol").n_unique().alias("symbols"),
            (pl.col("ret") > 0).mean().mul(100).alias("win %"),
            pl.col("ret").mean().mul(100).alias("mean %"),
            pl.col("ret").median().mul(100).alias("median %"),
            (pl.col("ret").mean() / pl.col("ret").std() * pl.len().sqrt()).alias("t"),
        )
        .sort("funding", "level", "trade", "hold", "period")
    )
    text = "\n".join(
        [
            "# Research 06 results: extreme funding",
            "",
            "Generated by `uv run mb research funding`. The analysis is in "
            "[../06-funding.md](../06-funding.md).",
            "",
            "- Crypto USD-M perpetuals (TradFi and index contracts excluded), days with at least "
            "10M USDT volume, 2024-10 to 2026-09.",
            "- Funding normalized to 8 hours. Negative: the day's lowest rate at or below -0.3% "
            "(mild) or -1% (strong). Positive: the highest at or above +0.1% (mild) or +0.3% "
            "(strong).",
            f"- Entry at the next day's open; hold 1 or 3 days; stop {STOP:.0%} on daily "
            f"highs/lows with {STOP_SLIPPAGE:.1%} slippage; taker {TAKER:.2%} per side; "
            "funding over the hold included.",
            "- Overlapping signals on consecutive days are all counted, so trades within a "
            "symbol are correlated and t-statistics are optimistic.",
            "",
            markdown_table(summary, 2),
            "",
        ]
    )
    return trades, text
