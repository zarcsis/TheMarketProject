"""Research 01: is +10% per 24-hour window reachable, and what does it cost?

Three measurements:

1. Volatility of the universe, from daily klines: how often does a symbol move far enough
   in a day for +10% of equity at leverage L, even with perfect hindsight?
2. The no-edge baseline, from 5-minute klines: one position per window at leverage L,
   exiting at +10% or -9% of equity (or at the window end), with fees, stop slippage,
   and gap fills. Direction comes from simple rules, including a coin flip.
3. Monte Carlo of compounding those per-window outcomes.

Universes are chosen per day from the previous day's data only, so there is no
look-ahead. Delisted symbols stay in, so there is no survivorship bias.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path

import numpy as np
import polars as pl

from marketbot.data import archive

# Stablecoin and index perpetuals: no directional volatility to trade.
EXCLUDED = frozenset({"USDCUSDT", "FDUSDUSDT", "BTCDOMUSDT", "DEFIUSDT", "BLUEBIRDUSDT"})
MAJORS = ("BTCUSDT", "ETHUSDT")
TOP_VOLUME = 20
IN_PLAY = 10
IN_PLAY_MIN_QUOTE_VOLUME = 50_000_000.0


class Universe(StrEnum):
    MAJORS = "majors (BTC, ETH)"
    TOP_VOLUME = f"top {TOP_VOLUME} by volume"
    IN_PLAY = f"in play (top {IN_PLAY} movers)"


@dataclass(frozen=True)
class GameRules:
    """One position per window, exiting at the equity target or stop."""

    leverage: float
    target: float = 0.10
    stop: float = 0.09
    taker_fee: float = 0.0005
    stop_slippage: float = 0.001


# Daily data.


def non_crypto_symbols(exchange_info: dict) -> frozenset[str]:
    """TradFi perpetuals (equities, commodities, FX, pre-market) and index contracts.

    Delisted symbols are absent from exchangeInfo and count as crypto; nearly all are.
    """
    return frozenset(
        row["symbol"]
        for row in exchange_info["symbols"]
        if row.get("contractType") != "PERPETUAL" or row.get("underlyingType") != "COIN"
    )


def daily_features(klines: pl.DataFrame) -> pl.DataFrame:
    """Per symbol and UTC day: return, best excursions from the open, and volume."""
    return (
        klines.filter(~pl.col("symbol").is_in(EXCLUDED))
        .with_columns(day=pl.col("open_time").dt.date())
        .select(
            "symbol",
            "day",
            "quote_volume",
            ret=pl.col("close") / pl.col("open") - 1,
            up=pl.col("high") / pl.col("open") - 1,
            down=1 - pl.col("low") / pl.col("open"),
        )
        .sort("symbol", "day")
    )


def select_universes(daily: pl.DataFrame) -> pl.DataFrame:
    """(day, symbol, universe) rows, chosen from the previous day's data only."""
    previous = daily.with_columns(
        prev_volume=pl.col("quote_volume").shift(1).over("symbol"),
        prev_ret=pl.col("ret").shift(1).over("symbol"),
        prev_day=pl.col("day").shift(1).over("symbol"),
    ).filter(
        # The symbol traded the day before (no gaps in its history).
        (pl.col("day") - pl.col("prev_day")) == pl.duration(days=1)
    )
    majors = previous.filter(pl.col("symbol").is_in(MAJORS)).select(
        "day", "symbol", universe=pl.lit(Universe.MAJORS.value)
    )
    top_volume = (
        previous.filter(
            pl.col("prev_volume").rank("ordinal", descending=True).over("day") <= TOP_VOLUME
        )
    ).select("day", "symbol", universe=pl.lit(Universe.TOP_VOLUME.value))
    in_play = (
        previous.filter(pl.col("prev_volume") >= IN_PLAY_MIN_QUOTE_VOLUME)
        .filter(pl.col("prev_ret").abs().rank("ordinal", descending=True).over("day") <= IN_PLAY)
        .select("day", "symbol", universe=pl.lit(Universe.IN_PLAY.value))
    )
    return pl.concat([majors, top_volume, in_play]).sort("universe", "day", "symbol")


def required_move(rules: GameRules) -> float:
    """Price move needed for the equity target at the rules' leverage, after fees."""
    return rules.target / rules.leverage + 2 * rules.taker_fee


def hindsight_table(
    daily: pl.DataFrame, universes: pl.DataFrame, leverages: list[float]
) -> pl.DataFrame:
    """Share of symbol-days whose best excursion from the open reaches the target.

    This is an upper bound: it assumes the right direction and ignores the stop.
    """
    joined = universes.join(daily, on=["day", "symbol"])
    best = pl.max_horizontal("up", "down")
    return (
        joined.group_by("universe")
        .agg(
            pl.len().alias("symbol-days"),
            (best.median() * 100).alias("median best move %"),
            (pl.col("ret").abs().median() * 100).alias("median |open-close| %"),
            *[
                ((best >= required_move(GameRules(lev))).mean() * 100).alias(f"reach @{lev:g}x %")
                for lev in leverages
            ],
        )
        .sort("universe")
    )


# Intraday simulation.


def simulate_windows(bars: pl.DataFrame, rules: GameRules, *, min_bars: int = 280) -> pl.DataFrame:
    """Equity outcome of a long and of a short opened at each UTC day's first bar.

    `bars` holds intraday klines with `symbol`. Exits: take profit at the equity target,
    stop at the equity stop (filled at the bar open when the bar gaps through it, minus
    slippage), else the close of the last bar. A bar touching both levels counts as a stop.
    """
    lev, fee, slip = rules.leverage, rules.taker_fee, rules.stop_slippage
    frame = (
        bars.with_columns(day=pl.col("open_time").dt.date())
        .sort("symbol", "open_time")
        .with_columns(
            open0=pl.col("open").first().over("symbol", "day"),
            bar=pl.int_range(pl.len()).over("symbol", "day"),
        )
        .with_columns(
            long_tp=pl.col("open0") * (1 + rules.target / lev + 2 * fee),
            long_sp=pl.col("open0") * (1 - rules.stop / lev + 2 * fee) / (1 - slip),
            short_tp=pl.col("open0") * (1 - rules.target / lev - 2 * fee),
            short_sp=pl.col("open0") * (1 + rules.stop / lev - 2 * fee) / (1 + slip),
        )
    )

    def first(expr: pl.Expr, condition: pl.Expr) -> pl.Expr:
        return expr.filter(condition).first()

    windows = frame.group_by("symbol", "day").agg(
        bars=pl.len(),
        open0=pl.col("open0").first(),
        last_close=pl.col("close").last(),
        long_tp_bar=first(pl.col("bar"), pl.col("high") >= pl.col("long_tp")),
        long_sp_bar=first(pl.col("bar"), pl.col("low") <= pl.col("long_sp")),
        long_sp_open=first(pl.col("open"), pl.col("low") <= pl.col("long_sp")),
        long_sp=pl.col("long_sp").first(),
        short_tp_bar=first(pl.col("bar"), pl.col("low") <= pl.col("short_tp")),
        short_sp_bar=first(pl.col("bar"), pl.col("high") >= pl.col("short_sp")),
        short_sp_open=first(pl.col("open"), pl.col("high") >= pl.col("short_sp")),
        short_sp=pl.col("short_sp").first(),
    )
    windows = windows.filter(pl.col("bars") >= min_bars)

    def outcome(side: str) -> list[pl.Expr]:
        sign = 1 if side == "long" else -1
        tp_bar, sp_bar = pl.col(f"{side}_tp_bar"), pl.col(f"{side}_sp_bar")
        stopped = sp_bar.is_not_null() & (tp_bar.is_null() | (sp_bar <= tp_bar))
        took_profit = tp_bar.is_not_null() & ~stopped
        if side == "long":
            fill = pl.min_horizontal(f"{side}_sp", f"{side}_sp_open") * (1 - slip)
        else:
            fill = pl.max_horizontal(f"{side}_sp", f"{side}_sp_open") * (1 + slip)
        stop_return = sign * lev * (fill / pl.col("open0") - 1) - 2 * lev * fee
        close_return = sign * lev * (pl.col("last_close") / pl.col("open0") - 1) - 2 * lev * fee
        return [
            pl.when(stopped)
            .then(pl.lit("stop"))
            .when(took_profit)
            .then(pl.lit("target"))
            .otherwise(pl.lit("open"))
            .alias(f"{side}_outcome"),
            pl.when(stopped)
            .then(stop_return)
            .when(took_profit)
            .then(pl.lit(rules.target))
            .otherwise(close_return)
            .alias(f"{side}_return"),
            # The deciding bar touched both levels; counted as a stop, which may be wrong.
            (stopped & (sp_bar == tp_bar)).alias(f"{side}_ambiguous"),
        ]

    return windows.select("symbol", "day", *outcome("long"), *outcome("short")).sort(
        "symbol", "day"
    )


class Rule(StrEnum):
    COIN_FLIP = "coin flip"
    LONG = "always long"
    MOMENTUM = "previous day's direction"
    REVERSAL = "against previous day's direction"


def apply_rules(windows: pl.DataFrame, daily: pl.DataFrame) -> pl.DataFrame:
    """Long format: one row per window and direction rule, with `outcome` and `ret`."""
    previous = daily.select("symbol", "day", prev_ret=pl.col("ret").shift(1).over("symbol"))
    frame = windows.join(previous, on=["symbol", "day"], how="left")
    momentum_long = pl.col("prev_ret") >= 0

    def pick(long_side: pl.Expr) -> list[pl.Expr]:
        return [
            pl.when(long_side).then("long_outcome").otherwise("short_outcome").alias("outcome"),
            pl.when(long_side).then("long_return").otherwise("short_return").alias("ret"),
            pl.when(long_side)
            .then("long_ambiguous")
            .otherwise("short_ambiguous")
            .alias("ambiguous"),
        ]

    coin_flip = pl.concat(
        [
            frame.select(
                "symbol",
                "day",
                outcome="long_outcome",
                ret="long_return",
                ambiguous="long_ambiguous",
            ),
            frame.select(
                "symbol",
                "day",
                outcome="short_outcome",
                ret="short_return",
                ambiguous="short_ambiguous",
            ),
        ]
    ).with_columns(weight=pl.lit(0.5))
    parts = [
        coin_flip.with_columns(rule=pl.lit(Rule.COIN_FLIP.value)),
        frame.select("symbol", "day", *pick(pl.lit(True))).with_columns(
            weight=pl.lit(1.0), rule=pl.lit(Rule.LONG.value)
        ),
        frame.filter(pl.col("prev_ret").is_not_null())
        .select("symbol", "day", *pick(momentum_long))
        .with_columns(weight=pl.lit(1.0), rule=pl.lit(Rule.MOMENTUM.value)),
        frame.filter(pl.col("prev_ret").is_not_null())
        .select("symbol", "day", *pick(~momentum_long))
        .with_columns(weight=pl.lit(1.0), rule=pl.lit(Rule.REVERSAL.value)),
    ]
    return pl.concat(parts)


def summarize(outcomes: pl.DataFrame, by: list[str]) -> pl.DataFrame:
    """Weighted outcome shares and mean equity return per group."""
    weight = pl.col("weight")
    return (
        outcomes.group_by(by)
        .agg(
            weight.sum().round().cast(pl.Int64).alias("windows"),
            *[
                ((weight * (pl.col("outcome") == name)).sum() / weight.sum() * 100).alias(
                    f"{name} %"
                )
                for name in ("target", "stop", "open")
            ],
            ((weight * pl.col("ambiguous")).sum() / weight.sum() * 100).alias(
                "stop and target in one bar %"
            ),
            ((weight * pl.col("ret")).sum() / weight.sum() * 100).alias("mean return %"),
            (
                (weight * pl.col("ret").clip(lower_bound=-0.99).log1p()).sum() / weight.sum() * 100
            ).alias("mean log growth %"),
        )
        .sort(by)
    )


# Compounding.


@dataclass(frozen=True)
class MonteCarloResult:
    windows: int
    median_multiple: float
    p_double: float
    p_project_pause: float
    p_ladder: float  # every window hits the target


def monte_carlo(
    returns: np.ndarray,
    windows: int,
    *,
    paths: int = 20_000,
    pause_below: float = 0.70,
    target: float = 0.10,
    seed: int = 1,
) -> MonteCarloResult:
    """Bootstrap per-window returns into equity paths; a path stops at the project pause."""
    rng = np.random.default_rng(seed)
    draws = rng.choice(returns, size=(paths, windows), replace=True)
    equity = np.cumprod(1 + draws, axis=1)
    paused = (equity < pause_below).any(axis=1)
    pause_at = np.where(paused, (equity < pause_below).argmax(axis=1), windows - 1)
    final = np.where(paused, equity[np.arange(paths), pause_at], equity[:, -1])
    p_hit = float((returns >= target - 1e-12).mean())
    return MonteCarloResult(
        windows=windows,
        median_multiple=float(np.median(final)),
        p_double=float((final >= 2).mean()),
        p_project_pause=float(paused.mean()),
        p_ladder=p_hit**windows,
    )


def breakeven_target_share(target: float = 0.10, stop: float = 0.09) -> float:
    """Share of target windows needed for zero log growth when every window hits a barrier."""
    return -math.log1p(-stop) / (math.log1p(target) - math.log1p(-stop))


# The study.

LEVERAGES = (2.0, 3.0, 5.0, 10.0)
MONTE_CARLO_WINDOWS = (30, 90, 365)


def load_daily(root: Path) -> pl.DataFrame:
    return daily_features(archive.load("klines", root=root, interval="1d"))


def study_universes(root: Path, start: date, end: date) -> pl.DataFrame:
    return select_universes(load_daily(root)).filter(pl.col("day").is_between(start, end))


def intraday_jobs(universes: pl.DataFrame) -> list[tuple[str, str]]:
    """(symbol, YYYY-MM) pairs of intraday data the study needs."""
    rows = (
        universes.select("symbol", month=pl.col("day").dt.strftime("%Y-%m"))
        .unique()
        .sort("symbol", "month")
    )
    return [(symbol, month) for symbol, month in rows.iter_rows()]


def run_study(
    root: Path,
    start: date,
    end: date,
    interval: str = "5m",
    *,
    progress: Callable[[str], object] = print,
) -> str:
    daily = load_daily(root)
    universes = select_universes(daily).filter(pl.col("day").is_between(start, end))
    hindsight = hindsight_table(daily, universes, list(LEVERAGES))

    symbols = universes["symbol"].unique().sort().to_list()
    parts: list[pl.DataFrame] = []
    missing: list[str] = []
    for index, symbol in enumerate(symbols, 1):
        bars = archive.load("klines", [symbol], root=root, interval=interval)
        if bars.is_empty():
            missing.append(symbol)
            continue
        bars = bars.select("symbol", "open_time", "open", "high", "low", "close")
        days = universes.filter(pl.col("symbol") == symbol)
        symbol_daily = daily.filter(pl.col("symbol") == symbol)
        for leverage in LEVERAGES:
            windows = simulate_windows(bars, GameRules(leverage))
            parts.append(
                apply_rules(windows, symbol_daily)
                .join(days, on=["symbol", "day"])
                .with_columns(leverage=pl.lit(leverage))
            )
        if index % 25 == 0:
            progress(f"simulated {index}/{len(symbols)} symbols")
    outcomes = pl.concat(parts)
    summary = summarize(outcomes, ["universe", "rule", "leverage"])

    simulated = outcomes.filter(
        (pl.col("rule") == Rule.LONG.value) & (pl.col("leverage") == LEVERAGES[0])
    )
    coverage = (
        universes.group_by("universe")
        .agg(
            pl.len().alias("symbol-days in universe"), pl.col("symbol").n_unique().alias("symbols")
        )
        .join(
            simulated.group_by("universe").agg(pl.len().alias("windows simulated")),
            on="universe",
            how="left",
        )
        .sort("universe")
    )

    monte_carlo_rows = []
    for universe in Universe:
        for leverage in (3.0, 5.0, 10.0):
            sample = outcomes.filter(
                (pl.col("universe") == universe.value)
                & (pl.col("rule") == Rule.COIN_FLIP.value)
                & (pl.col("leverage") == leverage)
            )["ret"].to_numpy()
            for windows in MONTE_CARLO_WINDOWS:
                result = monte_carlo(sample, windows)
                monte_carlo_rows.append(
                    {
                        "universe": universe.value,
                        "leverage": leverage,
                        "windows": windows,
                        "median equity multiple": result.median_multiple,
                        "P(double) %": result.p_double * 100,
                        "P(project pause) %": result.p_project_pause * 100,
                    }
                )

    return _render(
        start=start,
        end=end,
        interval=interval,
        coverage=coverage,
        missing=missing,
        hindsight=hindsight,
        summary=summary,
        monte_carlo=pl.DataFrame(monte_carlo_rows),
    )


# Rendering.


def markdown_table(frame: pl.DataFrame, decimals: int = 1) -> str:
    def cell(value: object) -> str:
        if isinstance(value, float):
            return f"{value:,.{decimals}f}"
        if isinstance(value, int):
            return f"{value:,}"
        return str(value)

    lines = [
        "| " + " | ".join(frame.columns) + " |",
        "|" + "|".join("---" for _ in frame.columns) + "|",
    ]
    lines += ["| " + " | ".join(cell(value) for value in row) + " |" for row in frame.iter_rows()]
    return "\n".join(lines)


def _render(
    *,
    start: date,
    end: date,
    interval: str,
    coverage: pl.DataFrame,
    missing: list[str],
    hindsight: pl.DataFrame,
    summary: pl.DataFrame,
    monte_carlo: pl.DataFrame,
) -> str:
    rules = GameRules(leverage=1)
    breakeven = breakeven_target_share(rules.target, rules.stop)
    ladder = pl.DataFrame(
        {
            "target share of windows": [0.5, 0.55, 0.6, 0.7],
            "P(7 targets in a row) %": [p**7 * 100 for p in (0.5, 0.55, 0.6, 0.7)],
            "P(30 targets in a row) %": [p**30 * 100 for p in (0.5, 0.55, 0.6, 0.7)],
        }
    )
    required = pl.DataFrame(
        {
            "leverage": list(LEVERAGES),
            "price move for +10% %": [required_move(GameRules(lev)) * 100 for lev in LEVERAGES],
            "price move to -9% %": [
                (rules.stop / lev - 2 * rules.taker_fee) * 100 for lev in LEVERAGES
            ],
        }
    )
    sections = [
        "# Research 01 results: feasibility",
        "",
        "Generated by `uv run mb research feasibility`. Do not edit by hand; the analysis is in "
        "[../01-feasibility.md](../01-feasibility.md).",
        "",
        f"- Period: {start} to {end} (UTC days).",
        f"- Data: Binance USD-M perpetuals, daily and {interval} klines from data.binance.vision, "
        "delisted symbols included.",
        f"- Universes per day, from the previous day's data: {', '.join(u.value for u in Universe)}"
        f". In play = the {IN_PLAY} largest absolute previous-day moves among symbols with at "
        f"least {IN_PLAY_MIN_QUOTE_VOLUME / 1e6:.0f}M USDT previous-day volume.",
        f"- Costs: taker fee {rules.taker_fee:.2%} per side, stop slippage "
        f"{rules.stop_slippage:.1%} of price; stops that gap fill at the bar open.",
        "",
        "## Coverage",
        "",
        markdown_table(coverage, 0),
        "",
        f"Symbols without {interval} data: {', '.join(missing) if missing else 'none'}.",
        "",
        "## Required price moves",
        "",
        "One position holding the whole window's equity at the given leverage, after taker fees "
        "both ways.",
        "",
        markdown_table(required, 2),
        "",
        "## 1. Volatility: can the target be reached at all?",
        "",
        "Share of symbol-days whose best excursion from the 00:00 UTC open (up or down, whichever "
        "is larger) reaches the move needed for +10% at each leverage. This assumes the right "
        "direction and ignores the stop, so it is an upper bound.",
        "",
        markdown_table(hindsight),
        "",
        "## 2. The no-edge baseline",
        "",
        "One position per symbol-day, opened at 00:00 UTC, closed at +10% or -9% of equity or at "
        "the end of the day. Return is the window's equity return. Log growth is the mean of "
        "log(1 + return), which decides long-run compounding.",
        "",
        markdown_table(summary, 2),
        "",
        "## 3. Compounding the coin-flip baseline",
        "",
        "Monte Carlo (20,000 paths) drawing window returns from the coin-flip rows above. A path "
        "stops when equity falls below 70% of the start (the project pause).",
        "",
        markdown_table(monte_carlo, 2),
        "",
        "## 4. Barrier arithmetic",
        "",
        f"If every window ends at exactly +10% or -9%, zero log growth needs {breakeven:.2%} of "
        "windows at the target. The ladder (every window at the target) is a streak:",
        "",
        markdown_table(ladder, 4),
        "",
    ]
    return "\n".join(sections)
