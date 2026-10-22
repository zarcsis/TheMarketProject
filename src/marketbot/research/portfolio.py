"""Research 08: the event strategies together, under the project's risk rules.

Trades come from research 03 (busy listings, 7-day short, 40% stop, with funding), 04
(delisting announcements, 4-hour short, 20% stop), 07 (Monitoring Tag, 1-day short, 20%
stop), and 09 (airdrop announcements, 7-day short, 40% stop, with funding). A symbol is
held by one strategy at a time, as in the bot. Each entry risks a fixed share of current
equity (position = risk / (stop + costs)), subject to a maximum of open positions and of
positions per strategy bucket. Several events often start at the same moment; which of
them get the free slots is decided at random, and the study reports the spread over many
random orders. Equity changes when trades close (realized), so drawdowns are understated
against mark-to-market.
"""

import random
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

import numpy as np
import polars as pl

from marketbot.research.feasibility import markdown_table

STOP_COSTS = 0.004  # fees and slippage on top of the stop distance when sizing


@dataclass(frozen=True)
class EventTrade:
    entry: datetime
    exit: datetime
    bucket: str
    stop: float
    ret: float
    symbol: str = ""


def load_trades(directory: Path = Path("data/research")) -> list[EventTrade]:
    listings = pl.read_parquet(directory / "03-listing-trades.parquet").filter(
        (pl.col("entry_day") == 1)
        & (pl.col("hold_days") == 7)
        & (pl.col("stop") == 0.4)
        & pl.col("busy")
    )
    delistings = pl.read_parquet(directory / "04-delisting-events.parquet").drop_nans(
        "short 4h stop 20%"
    )
    monitoring = pl.read_parquet(directory / "07-monitoring-events.parquet").drop_nans(
        "short 1d stop 20%"
    )
    airdrops = pl.read_parquet(directory / "09-airdrop-shorts.parquet").filter(
        (pl.col("hold") == "7d") & (pl.col("stop") == "40%") & (pl.col("delay") == "5 min")
    )
    trades = [
        EventTrade(
            datetime.combine(r["entry_date"], time(0, 5), UTC),
            datetime.combine(r["exit_date"], time(23, 59), UTC),
            "listing",
            0.40,
            r["ret_with_funding"],
            r["symbol"],
        )
        for r in listings.iter_rows(named=True)
    ]
    trades += [
        EventTrade(
            r["released"] + timedelta(minutes=5),
            r["released"] + timedelta(hours=4, minutes=5),
            "delisting",
            0.20,
            r["short 4h stop 20%"],
            r["symbol"],
        )
        for r in delistings.iter_rows(named=True)
    ]
    trades += [
        EventTrade(
            r["released"] + timedelta(minutes=5),
            r["released"] + timedelta(days=1, minutes=5),
            "monitoring",
            0.20,
            r["short 1d stop 20%"],
            r["symbol"],
        )
        for r in monitoring.iter_rows(named=True)
    ]
    trades += [
        EventTrade(
            r["released"] + timedelta(minutes=5),
            r["released"] + timedelta(days=7, minutes=5),
            "airdrop",
            0.40,
            r["with_funding"],
            r["symbol"],
        )
        for r in airdrops.iter_rows(named=True)
    ]
    return trades


def simulate(
    trades: list[EventTrade],
    *,
    risk: float,
    max_open: int,
    per_bucket: int,
    seed: int,
) -> tuple[float, float, int]:
    """(final equity multiple, maximum realized drawdown, trades taken)."""
    rng = random.Random(seed)  # noqa: S311 - a simulation, not cryptography
    order = sorted(trades, key=lambda t: (t.entry, rng.random()))
    equity, peak, drawdown, taken = 1.0, 1.0, 0.0, 0
    open_positions: list[tuple[datetime, float, float, str, str]] = []

    def settle(until: datetime | None) -> None:
        nonlocal equity, peak, drawdown
        for position in sorted(open_positions):
            if until is not None and position[0] > until:
                continue
            equity += position[1] * position[2]
            open_positions.remove(position)
            peak = max(peak, equity)
            drawdown = max(drawdown, 1 - equity / peak)

    for trade in order:
        settle(trade.entry)
        in_bucket = sum(position[3] == trade.bucket for position in open_positions)
        held = trade.symbol and any(position[4] == trade.symbol for position in open_positions)
        if len(open_positions) >= max_open or in_bucket >= per_bucket or held:
            continue
        notional = risk * equity / (trade.stop + STOP_COSTS)
        open_positions.append((trade.exit, notional, trade.ret, trade.bucket, trade.symbol))
        taken += 1
    settle(None)
    return equity, drawdown, taken


def run(directory: Path = Path("data/research"), seeds: int = 50) -> str:
    trades = load_trades(directory)
    days = (max(t.entry for t in trades) - min(t.entry for t in trades)).days
    combos = {
        "all four": {"listing", "delisting", "monitoring", "airdrop"},
        "all three": {"listing", "delisting", "monitoring"},
        "delisting + monitoring": {"delisting", "monitoring"},
        "delisting": {"delisting"},
        "monitoring": {"monitoring"},
        "listing": {"listing"},
        "airdrop": {"airdrop"},
    }
    rows = []
    for name, buckets in combos.items():
        subset = [t for t in trades if t.bucket in buckets]
        for risk, max_open in ((0.03, 3), (0.015, 3), (0.03, 5)):
            results = [
                simulate(subset, risk=risk, max_open=max_open, per_bucket=2, seed=seed)
                for seed in range(seeds)
            ]
            multiples = np.array([r[0] for r in results])
            drawdowns = np.array([r[1] for r in results])
            median = float(np.median(multiples))
            rows.append(
                {
                    "strategies": name,
                    "risk per trade %": risk * 100,
                    "max open": max_open,
                    "events": len(subset),
                    "taken (median)": int(np.median([r[2] for r in results])),
                    "equity x (median)": median,
                    "x (5th pct)": float(np.percentile(multiples, 5)),
                    "x (95th pct)": float(np.percentile(multiples, 95)),
                    "annual % (median)": (median ** (365 / days) - 1) * 100,
                    "max realized DD % (median)": float(np.median(drawdowns)) * 100,
                }
            )
    return "\n".join(
        [
            "# Research 08 results: the event strategies as a portfolio",
            "",
            "Generated by `uv run mb research portfolio`. The analysis is in "
            "[../08-portfolio.md](../08-portfolio.md).",
            "",
            f"- Period: {days} days of events (2024-10 to 2026-09).",
            "- Rules: each entry risks the given share of current equity; at most 2 open "
            "positions per strategy bucket. Positions are sized by stop distance plus "
            f"{STOP_COSTS:.1%} costs.",
            f"- {seeds} random orders of simultaneous events per row; percentiles over them.",
            "- Equity is realized only, so drawdowns are understated.",
            "",
            markdown_table(pl.DataFrame(rows), 2),
            "",
        ]
    )
