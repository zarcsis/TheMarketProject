"""Research 11: do perpetuals fall around scheduled token unlocks?

Events: cliff unlocks from DefiLlama's emission schedules, grouped by token and UTC day.
The size is the share of the circulating supply (the day before) that unlocks to insiders
and private-sale investors, the holders most likely to sell. Tokens are matched to Binance
USDT perpetuals through their CoinGecko ticker.

Trade, on daily bars: short at the open `k` days before the unlock day and cover at the
open `m` days after it (m = 0 is the open of the unlock day). Funding is summed over the
days held. Unlock schedules are published in advance, so the entry is feasible.

Baseline: the same window on every day of the period for the same perpetual. Tokens with
unlocks may drift down anyway; the excess over the baseline is the unlock effect.
"""

from datetime import date
from pathlib import Path

import numpy as np
import polars as pl

from marketbot.data import archive
from marketbot.research.feasibility import markdown_table
from marketbot.research.listings import STOP_SLIPPAGE, TAKER
from marketbot.research.upbit import perpetual

INSIDERS = ("insiders", "privateSale")
SIZES = ((1.0, 2.0), (2.0, 5.0), (5.0, 10.0), (10.0, float("inf")))
ENTRIES = (14, 7, 3, 1)  # days before the unlock day
EXITS = (0, 1, 3, 7)  # days after it
STOP = 0.30
MAIN = (7, 0)  # fixed in advance: short a week before, cover at the unlock day's open


def unlock_events(
    cliffs: pl.DataFrame, tickers: dict[str, str], symbols: set[str], start: date, end: date
) -> pl.DataFrame:
    """Unlocks by token and day, with the insider and total shares of supply in percent."""
    daily = (
        cliffs.with_columns(
            day=pl.col("unlock").dt.date(),
            insider=pl.col("category").is_in(INSIDERS),
        )
        .filter((pl.col("day") >= start) & (pl.col("day") < end))
        .group_by("protocol", "gecko_id", "day")
        .agg(
            insider_pct=(pl.col("tokens") * pl.col("insider")).sum()
            / pl.col("supply_before").max()
            * 100,
            total_pct=pl.col("tokens").sum() / pl.col("supply_before").max() * 100,
        )
    )
    mapped = [perpetual(tickers.get(gecko_id or "", ""), symbols) for gecko_id in daily["gecko_id"]]
    return (
        daily.with_columns(symbol=pl.Series(mapped, dtype=pl.String))
        .drop_nulls("symbol")
        .unique(["symbol", "day"])
        .sort("day", "symbol")
    )


def daily_bars(root: Path, symbols: list[str]) -> pl.DataFrame:
    klines = archive.load("klines", symbols, root=root, interval="1d").select(
        "symbol", day=pl.col("open_time").dt.date(), open="open", high="high"
    )
    funding = archive.load("fundingRate", symbols, root=root)
    if funding.height:
        per_day = funding.group_by("symbol", day=pl.col("calc_time").dt.date()).agg(
            funding=pl.col("last_funding_rate").sum()
        )
        klines = klines.join(per_day, on=["symbol", "day"], how="left")
    else:
        klines = klines.with_columns(funding=pl.lit(None, pl.Float64))
    return klines.with_columns(pl.col("funding").fill_null(0.0)).sort("symbol", "day")


def windows(bars: pl.DataFrame, before: int, after: int) -> pl.DataFrame:
    """For every symbol and day D: a short from the open of D - before to the open of
    D + after, without a stop, with the funding of the days held."""
    held = before + after
    return (
        bars.with_columns(
            entry=pl.col("open").shift(before).over("symbol"),
            exit=pl.col("open").shift(-after).over("symbol"),
            entry_day=pl.col("day").shift(before).over("symbol"),
            exit_day=pl.col("day").shift(-after).over("symbol"),
            funding_held=pl.col("funding").rolling_sum(held).shift(1 - after).over("symbol")
            if held
            else pl.lit(0.0),
        )
        .filter(
            # No gaps: the entry and exit days are exactly `before` and `after` days away.
            ((pl.col("day") - pl.col("entry_day")).dt.total_days() == before)
            & ((pl.col("exit_day") - pl.col("day")).dt.total_days() == after)
        )
        .with_columns(
            ret=(pl.col("entry") - pl.col("exit")) / pl.col("entry")
            - TAKER * (pl.col("entry") + pl.col("exit")) / pl.col("entry")
        )
        .with_columns(with_funding=pl.col("ret") + pl.col("funding_held"))
        .select("symbol", "day", "ret", "with_funding")
    )


def stopped_short(bars: pl.DataFrame, symbol: str, day: date, before: int, after: int) -> float:
    """The same short with a stop STOP above the entry, checked on daily highs."""
    rows = bars.filter(pl.col("symbol") == symbol)
    days = rows["day"].to_list()
    if day not in days:
        return np.nan
    i = days.index(day)
    if i - before < 0 or i + after >= len(days):
        return np.nan
    opens, highs = rows["open"].to_numpy(), rows["high"].to_numpy()
    funding = rows["funding"].to_numpy()
    entry = opens[i - before]
    level = entry * (1 + STOP)
    exit_price, last = opens[i + after], i + after
    for j in range(i - before, i + after):
        if highs[j] >= level:
            exit_price, last = max(level, opens[j]) * (1 + STOP_SLIPPAGE), j + 1
            break
    ret = (entry - exit_price) / entry - TAKER * (entry + exit_price) / entry
    return float(ret + funding[i - before : last].sum())


def _t(values: pl.Series) -> float:
    values = values.drop_nulls().drop_nans()
    if values.len() < 3 or values.std() == 0:
        return np.nan
    return float(values.mean() / values.std() * values.len() ** 0.5)


def run(
    root: Path,
    cliffs: pl.DataFrame,
    tickers: dict[str, str],
    symbols: set[str],
    start: date,
    end: date,
    split: date,
) -> tuple[pl.DataFrame, str]:
    events = unlock_events(cliffs, tickers, symbols, start, end).filter(
        pl.col("insider_pct") >= SIZES[0][0]
    )
    bars = daily_bars(root, events["symbol"].unique().to_list())
    size = pl.lit(None, pl.String)
    for low, high in SIZES:
        label = f"{low:g}% or more" if high == float("inf") else f"{low:g}-{high:g}%"
        size = (
            pl.when((pl.col("insider_pct") >= low) & (pl.col("insider_pct") < high))
            .then(pl.lit(label))
            .otherwise(size)
        )
    events = events.with_columns(
        size=size,
        period=pl.when(pl.col("day") < split)
        .then(pl.lit("first half"))
        .otherwise(pl.lit("second half")),
    )
    rows = []
    trades = []
    for before in ENTRIES:
        for after in EXITS:
            window = windows(bars, before, after)
            baseline = window.group_by("symbol").agg(base=pl.col("with_funding").mean())
            joined = (
                events.join(window, on=["symbol", "day"])
                .join(baseline, on="symbol")
                .with_columns(excess=pl.col("with_funding") - pl.col("base"))
            )
            if (before, after) == MAIN:
                trades.append(joined)
            for group in ("all", *[label for label in joined["size"].unique().sort()]):
                part = joined if group == "all" else joined.filter(pl.col("size") == group)
                if part.is_empty():
                    continue
                first = part.filter(pl.col("period") == "first half")
                second = part.filter(pl.col("period") == "second half")
                rows.append(
                    {
                        "entry": f"{before}d before",
                        "exit": f"{after}d after",
                        "insider unlock": group,
                        "events": part.height,
                        "mean %": part["with_funding"].mean() * 100,
                        "baseline %": part["base"].mean() * 100,
                        "excess %": part["excess"].mean() * 100,
                        "excess t": _t(part["excess"]),
                        "first: excess %": first["excess"].mean() * 100 if first.height else None,
                        "second: excess %": second["excess"].mean() * 100
                        if second.height
                        else None,
                    }
                )
    table = pl.DataFrame(rows)
    main = trades[0].with_columns(
        stopped=pl.struct("symbol", "day").map_elements(
            lambda row: stopped_short(bars, row["symbol"], row["day"], *MAIN),
            return_dtype=pl.Float64,
        )
    )
    main_table = table.filter(
        (pl.col("entry") == f"{MAIN[0]}d before") & (pl.col("exit") == f"{MAIN[1]}d after")
    )
    with_stop = (
        main.group_by("size")
        .agg(
            pl.len().alias("events"),
            (pl.col("stopped").mean() * 100).alias(f"mean with a {STOP:.0%} stop %"),
            (pl.col("with_funding").mean() * 100).alias("mean without a stop %"),
        )
        .sort("size")
    )
    big = table.filter(pl.col("insider unlock").is_in(["5-10%", "10% or more"]))
    text = "\n".join(
        [
            "# Research 11 results: perpetuals around token unlocks",
            "",
            "Generated by `uv run mb research unlocks`. The analysis is in "
            "[../11-unlocks.md](../11-unlocks.md).",
            "",
            f"- Events: {events.height} cliff unlocks from {start} to {end} of "
            f"{events['symbol'].n_unique()} tokens with a Binance USDT perpetual, where "
            "insiders and private-sale investors receive at least "
            f"{SIZES[0][0]:g}% of the circulating supply. Source: DefiLlama emission "
            "schedules; one event per token and day.",
            "- Short from the open `entry` days before the unlock day to the open `exit` days "
            "after it, after taker fees and with funding.",
            "- Baseline: the same window on every day of the period for the same "
            "perpetual. Excess: the event's return minus its perpetual's baseline.",
            f"- Periods split at {split}.",
            "",
            f"## The main variant: {MAIN[0]} days before to the unlock day's open",
            "",
            markdown_table(main_table.drop("entry", "exit"), 2),
            "",
            markdown_table(with_stop, 2),
            "",
            "## Large unlocks (5% of supply or more), every window",
            "",
            markdown_table(big, 2),
            "",
            "## All windows and sizes",
            "",
            markdown_table(table, 2),
            "",
        ]
    )
    return main, text
