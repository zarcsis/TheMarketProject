"""Research 02: intraday strategies with asymmetric trades, in-sample and out-of-sample.

Each strategy family produces entries on closed bars; the trade simulator applies exit
rules on 5-minute bars with costs. Symbols trade only on days when they belong to a
universe chosen from the previous day's data (see research 01). The in-sample period is
for choosing; the out-of-sample period is reported for every variant, chosen or not.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import polars as pl

from marketbot.backtest.signals import Donchian, Fade, OpeningRange, resample, with_indicators
from marketbot.backtest.trades import Bars, Costs, Entry, ExitRules, Trade, simulate
from marketbot.data import archive
from marketbot.research.feasibility import load_daily, markdown_table, select_universes

SIGNAL_EVERY = "1h"

STRATEGIES: list[Donchian | Fade | OpeningRange] = [
    Donchian(n=12, volume_mult=1.5, stop_atr=1.5),
    Donchian(n=24, volume_mult=1.5, stop_atr=1.5),
    Donchian(n=24, volume_mult=3.0, stop_atr=1.5),
    Donchian(n=24, volume_mult=1.5, stop_atr=3.0),
    Fade(k_atr=3.0, volume_mult=3.0),
    Fade(k_atr=4.0, volume_mult=4.0),
    OpeningRange(range_hours=2, stop_at_mid=True),
    OpeningRange(range_hours=4, stop_at_mid=True),
    OpeningRange(range_hours=4, stop_at_mid=False),
]

EXITS: dict[str, ExitRules] = {
    "target 1R": ExitRules(target_r=1.0),
    "target 2R": ExitRules(target_r=2.0),
    "target 3R": ExitRules(target_r=3.0),
    "trail 3atr, BE at 1R": ExitRules(target_r=None, trail_atr=3.0, breakeven_r=1.0),
}


@dataclass(frozen=True)
class StudyPeriod:
    start: date
    split: date  # first out-of-sample day
    end: date


def symbol_frames(bars5: pl.DataFrame) -> tuple[Bars, pl.DataFrame]:
    """The intraday bars as arrays (with ATR known at each bar) and the signal bars."""
    sig = with_indicators(resample(bars5, SIGNAL_EVERY), SIGNAL_EVERY)
    bars5 = bars5.sort("open_time").join_asof(
        sig.select("close_time", "atr").sort("close_time"),
        left_on="open_time",
        right_on="close_time",
        strategy="backward",
    )
    arrays = Bars(
        symbol=bars5["symbol"][0],
        time=bars5["open_time"].dt.replace_time_zone(None).to_numpy(),
        open=bars5["open"].to_numpy(),
        high=bars5["high"].to_numpy(),
        low=bars5["low"].to_numpy(),
        close=bars5["close"].to_numpy(),
        atr=bars5["atr"].fill_null(np.nan).to_numpy(),
    )
    return arrays, sig


def btc_trend(root: Path) -> pl.DataFrame:
    """Sign of BTC's last closed hourly close against its 24-hour EMA, by close time."""
    btc = archive.load("klines", ["BTCUSDT"], root=root, interval="5m")
    sig = with_indicators(resample(btc, SIGNAL_EVERY), SIGNAL_EVERY)
    return sig.select(
        "close_time",
        btc_trend=(pl.col("close") - pl.col("close").ewm_mean(span=24)).sign().cast(pl.Int8),
    ).sort("close_time")


def to_entries(signals: pl.DataFrame, bars: Bars) -> list[Entry]:
    """Entries at the bars whose open time equals the signal's entry time."""
    times = signals["entry_time"].dt.replace_time_zone(None).to_numpy()
    n = len(bars.time)
    position = np.searchsorted(bars.time, times)
    found = (position < n) & (bars.time[np.minimum(position, n - 1)] == times)
    directions = signals["direction"].to_numpy()
    stops = signals["stop"].to_numpy()
    tags = signals["tag"].to_list()
    return [
        Entry(int(position[k]), int(directions[k]), float(stops[k]), tags[k])
        for k in np.nonzero(found)[0]
    ]


def run_trades(
    root: Path, period: StudyPeriod, *, progress: Callable[[str], object] = print
) -> pl.DataFrame:
    daily = load_daily(root)
    universes = select_universes(daily).filter(pl.col("day").is_between(period.start, period.end))
    trend = btc_trend(root)
    costs = Costs()
    rows: list[dict] = []
    symbols = universes["symbol"].unique().sort().to_list()
    for count, symbol in enumerate(symbols, 1):
        bars5 = archive.load("klines", [symbol], root=root, interval="5m")
        if bars5.height < 300:
            continue
        bars, sig = symbol_frames(bars5)
        member = universes.filter(pl.col("symbol") == symbol).select("day", "universe")
        for strategy in STRATEGIES:
            source = bars5 if isinstance(strategy, OpeningRange) else sig
            signals = strategy.signals(source)
            if signals.is_empty():
                continue
            signals = (
                signals.sort("entry_time")
                .join_asof(trend, left_on="entry_time", right_on="close_time", strategy="backward")
                .with_columns(day=pl.col("entry_time").dt.date())
                .join(member, on="day")
            )
            for universe in signals["universe"].unique().to_list():
                chosen = signals.filter(pl.col("universe") == universe)
                for filtered in (False, True):
                    subset = (
                        chosen.filter(pl.col("direction") == pl.col("btc_trend"))
                        if filtered
                        else chosen
                    )
                    entries = to_entries(subset, bars)
                    for exit_name, rules in EXITS.items():
                        for trade in simulate(bars, entries, rules, costs):
                            rows.append(_row(trade, universe, exit_name, filtered, period.split))
        if count % 50 == 0:
            progress(f"{count}/{len(symbols)} symbols, {len(rows)} trades")
    return pl.DataFrame(rows)


def _row(trade: Trade, universe: str, exit_name: str, filtered: bool, split: date) -> dict:
    return {
        "strategy": trade.tag,
        "exit": exit_name,
        "universe": universe,
        "btc filter": filtered,
        "period": "out-of-sample" if trade.entry_time.date() >= split else "in-sample",
        "symbol": trade.symbol,
        "entry_time": trade.entry_time,
        "direction": trade.direction,
        "r": trade.r,
        "ret": trade.ret,
        "risk_pct": trade.risk / trade.entry,
        "reason": trade.reason,
        "bars": trade.bars,
    }


VARIANT = ["strategy", "exit", "universe", "btc filter"]


def summarize(trades: pl.DataFrame, by: Iterable[str]) -> pl.DataFrame:
    r = pl.col("r")
    return (
        trades.group_by(list(by))
        .agg(
            pl.len().alias("trades"),
            (r > 0).mean().mul(100).alias("win %"),
            r.mean().alias("mean R"),
            (r.filter(r > 0).sum() / r.filter(r < 0).sum().abs()).alias("profit factor"),
            (r.mean() / r.std() * pl.len().sqrt()).alias("t"),
            pl.col("risk_pct").median().mul(100).alias("median stop %"),
            pl.col("bars").mean().mul(5 / 60).alias("hours held"),
        )
        .sort(list(by))
    )


def report(trades: pl.DataFrame, period: StudyPeriod) -> str:
    in_sample = summarize(trades.filter(pl.col("period") == "in-sample"), VARIANT)
    out_sample = summarize(trades.filter(pl.col("period") == "out-of-sample"), VARIANT)
    both = in_sample.join(out_sample, on=VARIANT, how="full", coalesce=True, suffix=" OOS")
    ranked = both.sort("mean R", descending=True, nulls_last=True)
    top = ranked.filter(pl.col("trades") >= 100).head(15)
    families = summarize(
        trades.with_columns(
            family=pl.col("strategy").str.extract(r"^(\w+)"),
        ),
        ["family", "period"],
    )
    lines = [
        "# Research 02 results: intraday strategies",
        "",
        "Generated by `uv run mb research strategies`. Do not edit by hand; the analysis is in "
        "[../02-strategies.md](../02-strategies.md).",
        "",
        f"- In-sample: {period.start} to {period.split}; out-of-sample: {period.split} to "
        f"{period.end}.",
        f"- Signals on {SIGNAL_EVERY} bars (opening range on 5-minute bars); trades simulated "
        "on 5-minute bars, entry at the next bar's open.",
        "- Costs: taker fee 0.05% per side, entry slippage 0.02%, stop slippage 0.1%; a bar "
        "touching stop and target counts as a stop; positions close at the end of the UTC day.",
        f"- {len(STRATEGIES)} strategies x {len(EXITS)} exits x 3 universes x BTC filter on/off "
        f"= {ranked.height} variants. Expect some to look good by chance.",
        "",
        "## By family",
        "",
        markdown_table(families, 3),
        "",
        "## Top 15 variants by in-sample mean R (at least 100 in-sample trades)",
        "",
        "The OOS columns are the same variant on the later, unseen period.",
        "",
        markdown_table(
            top.select(
                *VARIANT,
                "trades",
                "win %",
                "mean R",
                "profit factor",
                "t",
                "trades OOS",
                "win % OOS",
                "mean R OOS",
                "profit factor OOS",
                "t OOS",
            ),
            3,
        ),
        "",
        "## All variants",
        "",
        markdown_table(ranked, 3),
        "",
    ]
    return "\n".join(lines)
