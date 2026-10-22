"""Research 09: listing types, and shorts after Binance airdrop announcements.

Part 1 splits the new-listing shorts of research 03 by the kind of listing. Each listing is
classified by the Binance announcements (catalogs 48 and 49) that name its token before
the short opens at the start of day 1, so the class is known at entry. The first matching
kind wins:

- airdrop: in the last 60 days, a HODLer Airdrop, Launchpool, or Megadrop of the token;
- alpha: in the last 60 days, a launch on Binance Alpha and Futures together;
- spot: a spot listing (with or without the Seed Tag), at any earlier time;
- futures only: no announcement names the token apart from the futures launch.

Part 2 is an event study. Binance announces an airdrop (HODLer Airdrops, Launchpool,
Megadrop) of a token whose USDT perpetual already trades. The short opens at the first
5-minute bar at least 5 minutes after the announcement and holds for a fixed time, with or
without a stop above the entry. Funding over the holding time is included.
"""

import re
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import numpy as np
import polars as pl

from marketbot.data import announcements, archive
from marketbot.research.delistings import STOP_SLIPPAGE, TAKER
from marketbot.research.feasibility import markdown_table

LOOKBACK = timedelta(days=60)
KINDS = (
    ("airdrop", re.compile(r"HODLer Airdrop|Launchpool|Megadrop", re.IGNORECASE)),
    ("alpha", re.compile(r"Binance Alpha", re.IGNORECASE)),
)
SPOT = re.compile(
    r"Will List|Vote to List|Continue to List|Exchange Adds|Binance Adds|Will Add .+ on Earn"
    r"|Seed Tag",
    re.IGNORECASE,
)
_MULTIPLIER = re.compile(r"^(?:1000000|100000|10000|1000|1M)(?=[A-Z])")

BAR = timedelta(minutes=5)
HOLDS = {"1d": 288, "3d": 864, "7d": 2016, "14d": 4032}
STOPS: tuple[float | None, ...] = (0.20, 0.30, 0.40, None)
DELAYS = {"5 min": timedelta(minutes=5), "1 h": timedelta(hours=1), "4 h": timedelta(hours=4)}
MAIN = ("7d", "40%")  # the variant the listing short uses: a 7-day hold with a 40% stop


def mentions(symbol: str) -> re.Pattern[str]:
    """Matches a title naming the perpetual's token, "(TOKEN)", or the contract itself."""
    base = symbol.removesuffix("USDT")
    token = _MULTIPLIER.sub("", base)
    names = {re.escape(token), re.escape(base)}
    return re.compile("|".join(rf"\({name}\)" for name in names) + rf"|\b{re.escape(base)}USDT\b")


def listing_kind(symbol: str, entry: datetime, titles: list[tuple[datetime, str]]) -> str:
    """The kind of a new listing from the announcements released before `entry`."""
    pattern = mentions(symbol)
    named = [(released, title) for released, title in titles if released < entry]
    named = [(released, title) for released, title in named if pattern.search(title)]
    recent = [title for released, title in named if released >= entry - LOOKBACK]
    for kind, kind_pattern in KINDS:
        if any(kind_pattern.search(title) for title in recent):
            return kind
    if any(SPOT.search(title) for _, title in named):
        return "spot"
    return "futures only"


def classify_listings(trades: pl.DataFrame, titles: list[tuple[datetime, str]]) -> pl.DataFrame:
    listings = trades.select("symbol", "listed").unique().sort("listed", "symbol")
    kinds = [
        listing_kind(symbol, datetime.combine(listed + timedelta(days=1), time(), UTC), titles)
        for symbol, listed in listings.iter_rows()
    ]
    return listings.with_columns(kind=pl.Series(kinds, dtype=pl.String))


def _stats(value: str) -> list[pl.Expr]:
    column = pl.col(value)
    return [
        pl.len().alias("trades"),
        column.mean().mul(100).alias("mean %"),
        column.median().mul(100).alias("median %"),
        (column > 0).mean().mul(100).alias("win %"),
        (column.mean() / column.std() * pl.len().sqrt()).alias("t"),
    ]


def event_shorts(
    bars: pl.DataFrame,
    released: datetime,
    funding: pl.DataFrame,
    btc: pl.DataFrame,
    delay: timedelta,
) -> list[dict]:
    """Shorts opened `delay` after an announcement, for every hold and stop."""
    times = bars["open_time"]
    start = released + delay
    index = int(times.search_sorted(start))
    if index < 288 or index >= bars.height or times[index] - start > 2 * BAR:
        return []  # needs a day of trading before the entry, and no gap at the entry
    opens, highs, closes = (bars[c].to_numpy() for c in ("open", "high", "close"))
    entry = float(opens[index])
    btc_times = btc["open_time"]
    btc_entry = float(btc["open"][int(btc_times.search_sorted(times[index]))])
    rows = []
    for hold, length in HOLDS.items():
        end = index + length - 1
        if end >= bars.height or times[end] - times[index] > (length + 12) * BAR:
            continue  # not enough data, or a gap inside the holding time
        for stop in STOPS:
            last, exit_price, stopped = end, float(closes[end]), False
            if stop is not None:
                level = entry * (1 + stop)
                hit = np.nonzero(highs[index : end + 1] >= level)[0]
                if hit.size:
                    last, stopped = index + int(hit[0]), True
                    exit_price = max(level, float(opens[last])) * (1 + STOP_SLIPPAGE)
            exit_time = times[last] + BAR
            paid = funding.filter(
                (pl.col("calc_time") > times[index]) & (pl.col("calc_time") <= exit_time)
            )["last_funding_rate"].sum()
            btc_exit = float(btc["close"][max(int(btc_times.search_sorted(exit_time)) - 1, 0)])
            ret = (entry - exit_price) / entry - TAKER * (entry + exit_price) / entry
            rows.append(
                {
                    "hold": hold,
                    "stop": "none" if stop is None else f"{stop:.0%}",
                    "ret": ret,
                    # A short receives positive funding and pays negative funding.
                    "with_funding": ret + float(paid),
                    "hedged": ret + float(paid) + btc_exit / btc_entry - 1,
                    "stopped": stopped,
                }
            )
    return rows


def airdrop_events(titles: pl.DataFrame, since: datetime) -> pl.DataFrame:
    frame = titles.filter(pl.col("released") >= since).with_columns(
        symbol=pl.col("title").map_elements(
            announcements.airdropped_perpetuals, return_dtype=pl.List(pl.String)
        ),
        program=pl.col("title").str.extract(r"(HODLer Airdrops|Launchpool|Megadrop)", 1),
    )
    return (
        frame.explode("symbol")
        .drop_nulls("symbol")
        .unique(["symbol", "released"])
        .sort("released")
        .select("symbol", "released", "program")
    )


def study_shorts(root: Path, events: pl.DataFrame) -> pl.DataFrame:
    """`event_shorts` for events with `symbol` and `released`, at every delay; other
    columns are kept."""
    btc = archive.load("klines", ["BTCUSDT"], root=root, interval="5m").sort("open_time")
    rows = []
    for event in events.iter_rows(named=True):
        symbol = event["symbol"]
        bars = archive.load("klines", [symbol], root=root, interval="5m").sort("open_time")
        if bars.is_empty():
            continue
        funding = archive.load("fundingRate", [symbol], root=root)
        if funding.is_empty():
            funding = pl.DataFrame(
                schema={"calc_time": pl.Datetime("ms", "UTC"), "last_funding_rate": pl.Float64}
            )
        before = bars.filter(pl.col("open_time") < event["released"])
        pre_24h = (
            float(before["close"][-1] / before["open"][-288] - 1)
            if before.height >= 288
            else np.nan
        )
        for delay_name, delay in DELAYS.items():
            for row in event_shorts(bars, event["released"], funding, btc, delay):
                rows.append({**event, "delay": delay_name, "pre_24h": pre_24h, **row})
    return pl.DataFrame(rows)


def short_table(shorts: pl.DataFrame) -> pl.DataFrame:
    """Shorts with funding by hold, stop, and period."""
    hold_order = {name: i for i, name in enumerate(HOLDS)}
    return (
        shorts.group_by("hold", "stop", "period")
        .agg(
            *_stats("with_funding"),
            pl.col("stopped").mean().mul(100).alias("stopped %"),
            pl.col("hedged").mean().mul(100).alias("BTC-hedged %"),
        )
        .with_columns(order=pl.col("hold").replace_strict(hold_order))
        .sort("order", "stop", "period")
        .drop("order")
    )


def run(
    root: Path, trades: pl.DataFrame, titles: pl.DataFrame, split: date, since: datetime
) -> tuple[pl.DataFrame, pl.DataFrame, str]:
    title_rows = list(titles.select("released", "title").unique().iter_rows())
    kinds = classify_listings(trades, title_rows)
    shorts = trades.filter(
        (pl.col("entry_day") == 1) & (pl.col("hold_days") == 7) & (pl.col("stop") == 0.40)
    ).join(kinds.select("symbol", "kind"), on="symbol")
    by_kind = pl.concat(
        [
            shorts.with_columns(listings=pl.lit("all")),
            shorts.filter(pl.col("busy")).with_columns(listings=pl.lit("busy")),
        ]
    )
    part1 = (
        by_kind.group_by("listings", "kind", "period")
        .agg(
            *_stats("ret_with_funding"),
            (pl.col("reason") == "stop").mean().mul(100).alias("stopped %"),
            pl.col("hedged").mean().mul(100).alias("BTC-hedged %"),
        )
        .sort("listings", "kind", "period")
    )
    variants = (
        trades.filter(pl.col("entry_day") == 1)
        .join(kinds.select("symbol", "kind"), on="symbol")
        .group_by("kind", "hold_days", "stop")
        .agg(*_stats("ret_with_funding"))
        .sort("kind", "hold_days", "stop")
    )
    half_years = (
        kinds.with_columns(
            half=pl.col("listed").dt.year().cast(pl.String)
            + pl.when(pl.col("listed").dt.month() <= 6).then(pl.lit("-H1")).otherwise(pl.lit("-H2"))
        )
        .group_by("half")
        .agg(
            pl.len().alias("listings"),
            *[
                (pl.col("kind") == kind).sum().alias(kind)
                for kind in ("airdrop", "alpha", "spot", "futures only")
            ],
        )
        .sort("half")
    )

    events = airdrop_events(titles, since)
    airdrops = study_shorts(root, events).with_columns(
        period=pl.when(pl.col("released").dt.date() < split)
        .then(pl.lit("first half"))
        .otherwise(pl.lit("second half"))
    )
    immediate = airdrops.filter(pl.col("delay") == "5 min")
    part2 = short_table(immediate)
    main = airdrops.filter((pl.col("hold") == MAIN[0]) & (pl.col("stop") == MAIN[1]))
    by_delay = main.group_by("delay").agg(*_stats("with_funding")).sort("delay")
    immediate_main = main.filter(pl.col("delay") == "5 min").sort("released")
    best = immediate_main.sort("with_funding", descending=True)
    without_best = best.slice(3)["with_funding"].mean()
    event_table = immediate_main.select(
        "symbol",
        day=pl.col("released").dt.date(),
        program="program",
        **{
            "24 h before %": pl.col("pre_24h") * 100,
            "short 7d, 40% stop, with funding %": pl.col("with_funding") * 100,
        },
    )
    text = "\n".join(
        [
            "# Research 09 results: listing types and airdrop announcements",
            "",
            "Generated by `uv run mb research airdrops`. The analysis is in "
            "[../09-airdrops.md](../09-airdrops.md).",
            "",
            "## Part 1: research 03 listing shorts by kind of listing",
            "",
            f"- {kinds.height} listings, classified by the announcements released before the "
            f"day-1 entry (airdrop and Alpha mentions within {LOOKBACK.days} days).",
            "- The short opens on day 1, holds 7 days, with a 40% stop; returns include funding.",
            f"- Periods split by listing date at {split}.",
            "",
            markdown_table(part1, 2),
            "",
            "### Listings by kind and half-year",
            "",
            markdown_table(half_years, 0),
            "",
            "### All variants (day-1 entry, both periods, with funding)",
            "",
            markdown_table(variants.rename({"hold_days": "hold days"}), 2),
            "",
            "## Part 2: shorts after airdrop announcements on listed perpetuals",
            "",
            f"- Events: {immediate_main.height} HODLer Airdrops, Launchpool, and Megadrop "
            f"announcements since {since:%Y-%m-%d} whose token's USDT perpetual traded for a "
            "day before (of "
            f"{events.height} airdrop announcements in all).",
            "- Entry: the open of the first 5-minute bar at least the delay after the "
            "announcement. Stops fill at the stop or a worse open, plus "
            f"{STOP_SLIPPAGE:.1%}. Taker fees {TAKER:.2%} per side. Funding is summed over "
            "the time held.",
            "- BTC-hedged: plus a BTC long of the same notional over the same time.",
            "",
            markdown_table(part2, 2),
            "",
            "### Entry delay (7-day hold, 40% stop, both periods)",
            "",
            markdown_table(by_delay, 2),
            "",
            f"Without the 3 best events, the 7-day short with a 40% stop averages "
            f"{without_best * 100:+.2f}% (5-minute delay).",
            "",
            "### Events",
            "",
            markdown_table(event_table, 1),
            "",
        ]
    )
    return kinds, airdrops, text
