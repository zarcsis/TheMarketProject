"""Research 18: do Binance's US-stock perpetuals fade their off-hours move after the open?

Binance lists perpetuals on US stocks (TradFi perpetuals). They trade around the clock,
also while the stock exchange is closed: overnight and over weekends and holidays. For each
NYSE trading day and each such perpetual, the study measures

- the off-hours move: from the perpetual's price at the previous session's close to its
  price at the open;
- the move after the open: from the price at the open to the price 5 to 120 minutes later
  and at the close.

If off-hours trading overreacts, big off-hours moves turn back after the open, and a trade
against the move (short after a rise, long after a drop) earns. Returns are raw and in
excess of the day's mean over all the perpetuals. Big moves come in sector-wide groups on
the same days, so the t-statistics cluster by day.
"""

import itertools
from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import httpx
import numpy as np
import polars as pl
import polars.selectors as cs

from marketbot.data import archive
from marketbot.research.feasibility import markdown_table

TAKER = 0.0005
BAR = timedelta(minutes=5)
WARMUP = timedelta(days=2)  # skip a perpetual's first days
MIN_VOLUME = 1_000_000.0  # quote volume of the previous session, USDT
HORIZONS = {"5m": 5, "15m": 15, "30m": 30, "60m": 60, "120m": 120}
THRESHOLDS = (0.03, 0.05, 0.08)
# NYSE full-day closures in 2026; the data starts in 2026.
HOLIDAYS = frozenset(
    {
        date(2026, 1, 1),
        date(2026, 1, 19),
        date(2026, 2, 16),
        date(2026, 4, 3),
        date(2026, 5, 25),
        date(2026, 6, 19),
        date(2026, 7, 3),
        date(2026, 9, 7),
        date(2026, 11, 26),
        date(2026, 12, 25),
    }
)


def _sunday(year: int, month: int, nth: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(6 - first.weekday()) % 7 + 7 * (nth - 1))


def nyse_session(day: date) -> tuple[datetime, datetime]:
    """The regular session in UTC: 09:30 to 16:00 New York time."""
    summer = _sunday(day.year, 3, 2) <= day < _sunday(day.year, 11, 1)
    shift = 4 if summer else 5
    return (
        datetime.combine(day, time(9 + shift, 30), UTC),
        datetime.combine(day, time(16 + shift), UTC),
    )


def trading_days(start: date, end: date) -> list[date]:
    days = (start + timedelta(days=i) for i in range((end - start).days + 1))
    return [day for day in days if day.weekday() < 5 and day not in HOLIDAYS]


def schedule(days: list[date]) -> pl.DataFrame:
    """One row per trading day after the first: the instants the study reads prices at."""
    rows = []
    for previous, day in itertools.pairwise(days):
        previous_open, previous_close = nyse_session(previous)
        open_, close = nyse_session(day)
        rows.append(
            {
                "day": day,
                "weekend": (day - previous).days > 1,
                "previous_open": previous_open,
                "previous_close": previous_close,
                "open": open_,
                **{label: open_ + timedelta(minutes=m) for label, m in HORIZONS.items()},
                "close": close,
            }
        )
    return pl.DataFrame(rows).with_columns(cs.datetime().dt.cast_time_unit("us"))


def panel(bars: pl.DataFrame, listed: dict[str, datetime], days: list[date]) -> pl.DataFrame:
    """Per perpetual and trading day: the off-hours move and the moves after the open.

    `bars` are 5-minute klines (`symbol`, `open_time`, `close`, `quote_volume`); the price
    at an instant is the close of the bar that ends there.
    """
    start = pl.DataFrame(
        {"symbol": list(listed), "listed": list(listed.values())},
        schema={"symbol": pl.String, "listed": pl.Datetime("us", "UTC")},
    )
    prices = (
        bars.join(start, on="symbol")
        .filter(pl.col("open_time") >= pl.col("listed") + WARMUP)
        .select(
            "symbol",
            t=(pl.col("open_time") + BAR).dt.cast_time_unit("us"),
            price="close",
            quote_volume="quote_volume",
        )
    )
    plan = schedule(days)
    frame = plan.join(
        prices.select("symbol", previous_close="t", p_previous_close="price"), on="previous_close"
    )
    for label in ("open", *HORIZONS, "close"):
        frame = frame.join(
            prices.select("symbol", pl.col("t").alias(label), pl.col("price").alias(f"p_{label}")),
            on=["symbol", label],
            how="left",
        )
    volume = (
        prices.join_where(
            plan.select("day", "previous_open", "previous_close"),
            pl.col("t") > pl.col("previous_open"),
            pl.col("t") <= pl.col("previous_close"),
        )
        .group_by("symbol", "day")
        .agg(previous_volume=pl.col("quote_volume").sum())
    )
    after = (*HORIZONS, "close")
    return (
        frame.join(volume, on=["symbol", "day"], how="left")
        .drop_nulls([f"p_{label}" for label in ("open", *after)])
        .with_columns(
            off=pl.col("p_open") / pl.col("p_previous_close") - 1,
            **{f"after_{label}": pl.col(f"p_{label}") / pl.col("p_open") - 1 for label in after},
        )
        .with_columns(
            **{
                f"excess_{label}": pl.col(f"after_{label}")
                - pl.col(f"after_{label}").mean().over("day")
                for label in after
            }
        )
        .select(
            "symbol",
            "day",
            "weekend",
            "previous_volume",
            "off",
            cs.starts_with("after_", "excess_"),
        )
        .sort("day", "symbol")
    )


def clustered_t(values: np.ndarray, clusters: np.ndarray) -> float:
    """t-statistic of the mean with standard errors clustered by `clusters`."""
    if len(values) < 3:
        return float("nan")
    residuals = pl.DataFrame({"c": clusters, "e": values - values.mean()})
    sums = residuals.group_by("c").agg(pl.col("e").sum())["e"].to_numpy()
    if len(sums) < 3 or not sums.any():
        return float("nan")
    return float(values.mean() / (np.sqrt((sums**2).sum()) / len(values)))


_NAMES = {"30m": "30 min", "60m": "60 min", "close": "to close"}


def fades(frame: pl.DataFrame, columns: Iterable[str]) -> pl.DataFrame:
    """Trades against big off-hours moves, after taker fees on both sides."""
    liquid = frame.filter(pl.col("previous_volume") >= MIN_VOLUME)
    rows = []
    for weekend, side, threshold in itertools.product((False, True), (-1, 1), THRESHOLDS):
        moved = pl.col("off") >= threshold if side == -1 else pl.col("off") <= -threshold
        part = liquid.filter(pl.col("weekend") == weekend, moved)
        row: dict[str, object] = {
            "session": "weekend or holiday" if weekend else "weeknight",
            "trade": "short after a rise" if side == -1 else "long after a drop",
            "off-hours move": f"{'+' if side == -1 else '-'}{threshold:.0%} or more",
            "events": part.height,
            "days": part["day"].n_unique(),
        }
        for column in columns:
            name = _NAMES[column.split("_", 1)[1]]
            values = side * part[column].to_numpy() - 2 * TAKER
            row[f"{name} %"] = float(values.mean()) * 100 if len(values) else None
            row[f"{name} t"] = clustered_t(values, part["day"].to_numpy())
        rows.append(row)
    return pl.DataFrame(rows)


def slopes(frame: pl.DataFrame) -> pl.DataFrame:
    """Pooled slope of the move after the open on the off-hours move."""
    rows = []
    for weekend in (False, True):
        part = frame.filter(pl.col("previous_volume") >= MIN_VOLUME, pl.col("weekend") == weekend)
        row: dict[str, object] = {"session": "weekend or holiday" if weekend else "weeknight"}
        row["events"] = part.height
        for label in ("30m", "60m", "close"):
            row[f"slope to {label}"] = float(
                np.polyfit(part["off"].to_numpy(), part[f"after_{label}"].to_numpy(), 1)[0]
            )
        rows.append(row)
    return pl.DataFrame(rows)


def us_stock_perpetuals(info: dict) -> dict[str, datetime]:
    """Binance's USDT perpetuals on US stocks, with their listing times."""
    return {
        row["symbol"]: datetime.fromtimestamp(row["onboardDate"] / 1000, UTC)
        for row in info["symbols"]
        if row.get("contractType") == "TRADIFI_PERPETUAL"
        and row.get("underlyingType") == "EQUITY"
        and row.get("quoteAsset") == "USDT"
    }


async def download(root: Path, listed: dict[str, datetime], end: date) -> archive.DownloadStats:
    """Fetch the 5-minute klines of each perpetual from its listing month to `end`."""
    last = archive.Month.of(end)
    jobs = [
        (symbol, month)
        for symbol, start in listed.items()
        for month in archive.month_range(archive.Month.of(start.date()), last)
    ]
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        return await archive.download(client, "klines", jobs, root=root, interval="5m")


def load_bars(root: Path, symbols: Iterable[str]) -> pl.DataFrame:
    return archive.load("klines", symbols, root=root, interval="5m").select(
        "symbol", "open_time", "close", "quote_volume"
    )


def run(
    root: Path, listed: dict[str, datetime], start: date, end: date
) -> tuple[pl.DataFrame, str]:
    frame = panel(load_bars(root, listed), listed, trading_days(start, end))
    raw = fades(frame, ["after_30m", "after_60m", "after_close"])
    excess = fades(frame, ["excess_30m", "excess_60m"])
    liquid = frame.filter(pl.col("previous_volume") >= MIN_VOLUME)
    text = "\n".join(
        [
            "# Research 18 results: US-stock perpetuals after the open",
            "",
            "Generated by `uv run mb research tradfi`. The analysis is in "
            "[../18-tradfi.md](../18-tradfi.md).",
            "",
            f"- Binance USDT perpetuals on US stocks, {frame['day'].min()} to "
            f"{frame['day'].max()}: {frame['symbol'].n_unique()} perpetuals, "
            f"{frame['day'].n_unique()} trading days, {frame.height:,} perpetual-days; "
            f"{liquid.height:,} with at least {MIN_VOLUME / 1e6:.0f}M USDT traded in the "
            "previous session, the only ones traded below.",
            "- Off-hours move: from the perpetual's price at the previous close to its price "
            "at the open. After the open: from the price at the open to 30 or 60 minutes "
            "later, or to the close.",
            f"- Returns of the trade against the move, after {TAKER:.2%} taker fees per side; "
            "the mean is per event, t clusters by day.",
            "",
            "## Raw returns",
            "",
            markdown_table(raw, 2),
            "",
            "## In excess of the day's mean over all the perpetuals",
            "",
            markdown_table(excess, 2),
            "",
            "## Slope of the move after the open on the off-hours move",
            "",
            markdown_table(slopes(frame), 3),
            "",
        ]
    )
    return frame, text
