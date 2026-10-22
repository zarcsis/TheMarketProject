"""Research 16: perpetuals after Binance shortens their funding interval.

Binance shortens a perpetual's funding interval (8 or 4 hours to 4, 2, or 1) when its
funding rate is pinned at the cap: one side of the market is crowded. The change is visible
in the funding history (`funding_interval_hours`) and, live, in `/fapi/v1/fundingInfo`.

Events: every shortening since 2024-10, at the first funding settlement on the new
interval. The trade enters at the first 5-minute bar at least 5 minutes after it, long or
short, and holds 1, 3, or 7 days with a stop or without, after fees and funding (a long
receives negative funding). The sign of the funding at the switch says which side is
crowded: negative means crowded shorts, so a squeeze would favour the long.
"""

from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl

from marketbot.data import archive
from marketbot.research.delistings import STOP_SLIPPAGE, TAKER
from marketbot.research.feasibility import markdown_table

BAR = timedelta(minutes=5)
DELAY = timedelta(minutes=5)
HOLDS = {"1d": 288, "3d": 864, "7d": 2016}
STOPS: tuple[float | None, ...] = (0.20, 0.30, None)
MIN_VOLUME_24H = 1_000_000.0


def switches(funding: pl.DataFrame, since: datetime) -> pl.DataFrame:
    """Settlements where the funding interval got shorter, with the rate per 8 hours."""
    return (
        funding.sort("symbol", "calc_time")
        .with_columns(previous=pl.col("funding_interval_hours").shift(1).over("symbol"))
        .filter(
            pl.col("previous").is_not_null()
            & (pl.col("funding_interval_hours") < pl.col("previous"))
            & (pl.col("calc_time") >= since)
        )
        .select(
            "symbol",
            switched=pl.col("calc_time"),
            interval="funding_interval_hours",
            previous="previous",
            rate_8h=pl.col("last_funding_rate") * 8 / pl.col("previous"),
        )
    )


def trade(
    bars: pl.DataFrame, funding: pl.DataFrame, start: datetime, side: int, length: int, stop
) -> float | None:
    """A position from the first bar at or after `start`; side +1 long, -1 short."""
    times = bars["open_time"]
    index = int(times.search_sorted(start))
    end = index + length - 1
    if index < 288 or end >= bars.height or times[index] - start > 2 * BAR:
        return None
    if times[end] - times[index] > (length + 12) * BAR:
        return None  # a gap in the data
    opens, highs, lows, closes = (bars[c].to_numpy() for c in ("open", "high", "low", "close"))
    entry = float(opens[index])
    exit_price, last = float(closes[end]), end
    if stop is not None:
        level = entry * (1 - side * stop)
        adverse = lows if side > 0 else highs
        hits = np.nonzero(
            adverse[index : end + 1] <= level if side > 0 else adverse[index : end + 1] >= level
        )[0]
        if hits.size:
            last = index + int(hits[0])
            worse = min(level, float(opens[last])) if side > 0 else max(level, float(opens[last]))
            exit_price = worse * (1 - side * STOP_SLIPPAGE)
    exit_time = times[last] + BAR
    paid = funding.filter(
        (pl.col("calc_time") > times[index]) & (pl.col("calc_time") <= exit_time)
    )["last_funding_rate"].sum()
    ret = side * (exit_price / entry - 1) - TAKER * (entry + exit_price) / entry
    return float(ret - side * paid)


def jobs(events: pl.DataFrame) -> list[tuple[str, str]]:
    needed = set()
    for symbol, switched in events.select("symbol", "switched").iter_rows():
        for offset in (-2, 0, 8):
            needed.add((symbol, f"{switched + timedelta(days=offset):%Y-%m}"))
    return sorted(needed)


def study(root: Path, events: pl.DataFrame) -> pl.DataFrame:
    rows = []
    for symbol in events["symbol"].unique().sort():
        bars = archive.load("klines", [symbol], root=root, interval="5m").sort("open_time")
        if bars.is_empty():
            continue
        funding = archive.load("fundingRate", [symbol], root=root)
        for event in events.filter(pl.col("symbol") == symbol).iter_rows(named=True):
            start = event["switched"] + DELAY
            index = int(bars["open_time"].search_sorted(start))
            if index < 288:
                continue
            volume = float(bars["quote_volume"][index - 288 : index].sum())
            if volume < MIN_VOLUME_24H:
                continue
            for hold, length in HOLDS.items():
                for stop in STOPS:
                    for side, name in ((1, "long"), (-1, "short")):
                        result = trade(bars, funding, start, side, length, stop)
                        if result is not None:
                            rows.append(
                                {
                                    **event,
                                    "hold": hold,
                                    "stop": "none" if stop is None else f"{stop:.0%}",
                                    "side": name,
                                    "return": result,
                                }
                            )
    return pl.DataFrame(rows)


def summary(trades: pl.DataFrame, split: date) -> pl.DataFrame:
    rows = []
    hold_order = {name: i for i, name in enumerate(HOLDS)}
    keys = trades.select("crowded", "side", "hold", "stop").unique().rows()
    for crowded, side, hold, stop in sorted(
        keys, key=lambda k: (k[0], k[1], hold_order[k[2]], k[3])
    ):
        part = trades.filter(
            (pl.col("crowded") == crowded)
            & (pl.col("side") == side)
            & (pl.col("hold") == hold)
            & (pl.col("stop") == stop)
        )
        row: dict = {"crowded": crowded, "side": side, "hold": hold, "stop": stop}
        for name, piece in (
            ("first", part.filter(pl.col("switched").dt.date() < split)),
            ("second", part.filter(pl.col("switched").dt.date() >= split)),
        ):
            values = piece["return"].to_numpy()
            row[f"{name}: n"] = len(values)
            row[f"{name}: mean %"] = values.mean() * 100 if len(values) else None
            row[f"{name}: t"] = (
                values.mean() / values.std(ddof=1) * np.sqrt(len(values))
                if len(values) > 2
                else None
            )
        values = np.sort(part["return"].to_numpy())
        row["median %"] = float(np.median(values)) * 100
        row["without best 5 %"] = float(values[:-5].mean()) * 100 if len(values) > 5 else None
        best = values[-5:].sum()
        row["best 5 share of total %"] = best / values.sum() * 100 if values.sum() > 0 else None
        rows.append(row)
    return pl.DataFrame(rows)


def run(root: Path, since: datetime, split: date) -> tuple[pl.DataFrame, str]:
    funding = archive.load("fundingRate", root=root)
    events = switches(funding, since)
    trades = study(root, events).with_columns(
        crowded=pl.when(pl.col("rate_8h") < 0).then(pl.lit("shorts")).otherwise(pl.lit("longs"))
    )
    by_symbol = (
        trades.filter(
            (pl.col("crowded") == "shorts")
            & (pl.col("side") == "long")
            & (pl.col("hold") == "1d")
            & (pl.col("stop") == "20%")
        )
        .group_by("symbol")
        .agg(pl.col("return").mean())
    )["return"]
    text = "\n".join(
        [
            "# Research 16 results: after Binance shortens the funding interval",
            "",
            "Generated by `uv run mb research squeeze`. The analysis is in "
            "[../16-squeeze.md](../16-squeeze.md).",
            "",
            f"- Events: {events.height} interval shortenings since {since:%Y-%m-%d}; studied "
            f"{trades.select('symbol', 'switched').n_unique()} with 5-minute klines and at "
            f"least {MIN_VOLUME_24H / 1e6:.0f}M USDT of volume in the 24 hours before.",
            "- `crowded`: the side paying funding at the switch (negative rate: shorts).",
            f"- Entry {DELAY.seconds // 60} minutes after the first settlement on the new "
            f"interval; taker fees {TAKER:.2%} per side, stop slippage {STOP_SLIPPAGE:.1%}, "
            "funding over the time held.",
            f"- Periods split at {split}.",
            "",
            markdown_table(summary(trades, split), 2),
            "",
            "Crowded shorts, long 1 day with a 20% stop, each symbol as one observation: "
            f"{by_symbol.len()} symbols, mean {by_symbol.mean() * 100:+.2f}%, "
            f"t {by_symbol.mean() / by_symbol.std() * by_symbol.len() ** 0.5:.2f}.",
            "",
        ]
    )
    return trades, text
