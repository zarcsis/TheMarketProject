"""Research 10: Binance perpetuals after Upbit announcements.

Events: Upbit "trade" announcements naming a token whose Binance USDT perpetual had traded
for at least a day: KRW-market listings, listings in other markets only, delistings,
warnings ("designated for investment caution"), and calls for caution. The event study is
research 04's: the entry is the open of the first 5-minute bar at least 5 minutes after the
announcement; short and long returns to fixed horizons, with and without stops.
"""

from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl

from marketbot.data import archive, upbit
from marketbot.research.airdrops import _stats, short_table, study_shorts
from marketbot.research.delistings import DELAY, STOP_SLIPPAGE, TAKER, study_event
from marketbot.research.feasibility import markdown_table

MULTIPLIERS = ("", "1000", "1000000", "1M")
KINDS = ("krw listing", "other listing", "delisting", "warning", "caution call")
# Kinds with a short effect in the first pass, studied again with funding and wide stops.
SHORT_KINDS = ("krw listing", "other listing", "warning")


def perpetual(ticker: str, symbols: set[str]) -> str | None:
    """The Binance USDT perpetual of a token, also under a 1000x name (1000PEPEUSDT)."""
    for prefix in MULTIPLIERS:
        if f"{prefix}{ticker}USDT" in symbols:
            return f"{prefix}{ticker}USDT"
    return None


def event_frame(notices: pl.DataFrame, since: datetime, symbols: set[str]) -> pl.DataFrame:
    rows = []
    recent = notices.filter(pl.col("released") >= since).sort("released")
    for released, title in recent.select("released", "title").iter_rows():
        for ticker, kind in upbit.events(title):
            symbol = perpetual(ticker, symbols)
            if symbol is not None:
                rows.append({"symbol": symbol, "kind": kind, "released": released})
    return pl.DataFrame(rows).unique(["symbol", "kind", "released"]).sort("released")


def jobs(events: pl.DataFrame) -> list[tuple[str, str]]:
    """The 5-minute kline months an event study of these events needs."""
    needed = set()
    for symbol, released in events.select("symbol", "released").iter_rows():
        for day in (released - timedelta(days=2), released, released + timedelta(days=8)):
            needed.add((symbol, f"{day:%Y-%m}"))
    return sorted(needed)


def study(root: Path, events: pl.DataFrame) -> pl.DataFrame:
    rows = []
    for symbol in events["symbol"].unique().sort():
        bars = archive.load("klines", [symbol], root=root, interval="5m").sort("open_time")
        if bars.is_empty():
            continue
        for kind, released in (
            events.filter(pl.col("symbol") == symbol).select("kind", "released").iter_rows()
        ):
            short = study_event(bars, released, -1)
            long = study_event(bars, released, +1)
            if short is not None and long is not None:
                rows.append({"symbol": symbol, "kind": kind, "released": released, **long, **short})
    return pl.DataFrame(rows).sort("released")


def summarize(frame: pl.DataFrame, measures: list[str], split: date) -> pl.DataFrame:
    """Per measure: events, mean, and t in each period, then mean, median, share > 0 overall."""
    first = frame.filter(pl.col("released").dt.date() < split)
    second = frame.filter(pl.col("released").dt.date() >= split)
    rows = []
    for measure in measures:
        row: dict = {"measure": measure}
        for name, part in (("first", first), ("second", second)):
            values = part[measure].drop_nans().drop_nulls().to_numpy()
            row[f"{name}: events"] = len(values)
            row[f"{name}: mean %"] = float(values.mean()) * 100 if len(values) else np.nan
            row[f"{name}: t"] = _t(values)
        values = frame[measure].drop_nans().drop_nulls().to_numpy()
        row["all: mean %"] = float(values.mean()) * 100 if len(values) else np.nan
        row["all: median %"] = float(np.median(values)) * 100 if len(values) else np.nan
        row["all: positive %"] = float((values > 0).mean()) * 100 if len(values) else np.nan
        row["all: t"] = _t(values)
        rows.append(row)
    return pl.DataFrame(rows)


def _t(values: np.ndarray) -> float:
    if len(values) < 3 or values.std(ddof=1) == 0:
        return np.nan
    return float(values.mean() / values.std(ddof=1) * np.sqrt(len(values)))


def run(
    root: Path, notices: pl.DataFrame, since: datetime, split: date, symbols: set[str]
) -> tuple[pl.DataFrame, pl.DataFrame, str]:
    events = event_frame(notices, since, symbols)
    frame = study(root, events)
    shorts = study_shorts(root, events.filter(pl.col("kind").is_in(SHORT_KINDS))).with_columns(
        period=pl.when(pl.col("released").dt.date() < split)
        .then(pl.lit("first half"))
        .otherwise(pl.lit("second half"))
    )
    funded = []
    for kind in SHORT_KINDS:
        part = shorts.filter(pl.col("kind") == kind)
        seven_days = part.filter(pl.col("hold") == "7d")
        by_delay = (
            seven_days.group_by("stop", "delay").agg(*_stats("with_funding")).sort("stop", "delay")
        )
        funded += [
            f"### {kind.capitalize()}",
            "",
            markdown_table(short_table(part.filter(pl.col("delay") == "5 min")), 2),
            "",
            "Entry delay, 7-day hold, both periods:",
            "",
            markdown_table(by_delay, 2),
            "",
        ]
    sections = []
    for kind in KINDS:
        part = frame.filter(pl.col("kind") == kind)
        if part.is_empty():
            continue
        sides = ("long", "short") if kind.endswith("listing") else ("short",)
        measures = ["pre_24h", "reaction"] + [
            column for column in frame.columns if column.startswith(sides)
        ]
        sections += [
            f"## {kind.capitalize()}: {part.height} events",
            "",
            markdown_table(summarize(part, measures, split), 2),
            "",
        ]
    counts = events.group_by("kind").len().sort("kind")
    text = "\n".join(
        [
            "# Research 10 results: Binance perpetuals after Upbit announcements",
            "",
            "Generated by `uv run mb research upbit`. The analysis is in "
            "[../10-upbit.md](../10-upbit.md).",
            "",
            f"- Upbit announcements since {since:%Y-%m-%d} naming a token with a Binance USDT "
            f"perpetual: {', '.join(f'{k} {n}' for k, n in counts.iter_rows())}. "
            "Studied: those whose perpetual traded for at least a day before, with 5-minute "
            "klines around the announcement.",
            f"- Entry: open of the first bar at least {DELAY.seconds // 60} minutes after the "
            "announcement. `pre_24h`: move over the 24 hours to the entry; `reaction`: move "
            "from the last close before the announcement to the entry.",
            f"- Returns after taker fees ({TAKER:.2%} per side); stops fill at the stop "
            f"(or a worse open) plus {STOP_SLIPPAGE:.1%}. No funding.",
            f"- Periods split at {split}; t is the mean over its standard error.",
            "",
            *sections,
            "## Shorts with funding and wide stops",
            "",
            "The same entry, with the research 09 short model: holds of 1 to 14 days, stops "
            "of 20% to 40% or none, funding summed over the time held, and a BTC-hedged "
            "variant. Entry delays of 5 minutes, 1 hour, and 4 hours.",
            "",
            *funded,
        ]
    )
    return frame, shorts, text
