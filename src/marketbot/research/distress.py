"""Research 15: perpetuals of tokens under Binance's Monitoring Tag, over weeks.

Research 07 found a short-lived drop after Binance adds the Monitoring Tag (a delisting risk
flag). This study looks at the weeks after it. For each tag addition since 2024-10, a short
opens at the open of the second UTC day after the announcement (after research 07's 1-day
trade) and holds 7, 14, 30, or 60 days, or until the perpetual stops trading (delisted).
Funding over the days held is included.

Altcoins fell over the period, so the short is compared with an equally weighted index of
liquid altcoin perpetuals over the same days: the excess is the short plus the index's
return, as if the index were held long against it.
"""

import re
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl

from marketbot.data import announcements, archive
from marketbot.research.feasibility import EXCLUDED, markdown_table

TAKER = 0.0005
HOLDS = (7, 14, 30, 60)
STOP = 0.40
INDEX_VOLUME = 20_000_000.0  # average daily quote volume for the altcoin index
_REMOVE = re.compile(r"Remove the Monitoring Tag for (.+?)(?:,? and | on \d{4}|$)", re.I)


def removed_perpetuals(title: str) -> list[str]:
    match = _REMOVE.search(title)
    if not match:
        return []
    tokens = re.split(r",\s*|\s*&\s*|\s+and\s+", match.group(1))
    return sorted({f"{token.strip()}USDT" for token in tokens if token.strip().isalnum()})


def tag_events(news: pl.DataFrame, delistings: pl.DataFrame) -> pl.DataFrame:
    """Tag additions with the first later removal and the first later delisting notice."""
    added, removed, delisted = [], {}, {}
    for title, released in news.select("title", "released").iter_rows():
        for symbol in announcements.monitoring_tagged_perpetuals(title):
            added.append((symbol, released))
        for symbol in removed_perpetuals(title):
            removed.setdefault(symbol, []).append(released)
    for title, released in delistings.select("title", "released").iter_rows():
        for symbol in announcements.delisted_perpetuals(title):
            delisted.setdefault(symbol, []).append(released)

    def first_after(days: list[datetime], start: datetime) -> datetime | None:
        later = [day for day in days if day > start]
        return min(later) if later else None

    rows = [
        {
            "symbol": symbol,
            "tagged": tagged,
            "removed": first_after(removed.get(symbol, []), tagged),
            "delisting_notice": first_after(delisted.get(symbol, []), tagged),
        }
        for symbol, tagged in added
    ]
    return pl.DataFrame(
        rows,
        schema={
            "symbol": pl.String,
            "tagged": pl.Datetime("us", "UTC"),
            "removed": pl.Datetime("us", "UTC"),
            "delisting_notice": pl.Datetime("us", "UTC"),
        },
    ).unique(["symbol", "tagged"])


def altcoin_index(daily: pl.DataFrame) -> dict[date, float]:
    """Equally weighted daily open-to-open return of liquid altcoin perpetuals."""
    returns = (
        daily.sort("symbol", "day")
        .with_columns(
            ret=pl.col("open").shift(-1).over("symbol") / pl.col("open") - 1,
            gap=(pl.col("day").shift(-1).over("symbol") - pl.col("day")).dt.total_days(),
            volume=pl.col("quote_volume").shift(1).rolling_mean(7).over("symbol"),
        )
        .filter(
            (pl.col("gap") == 1)
            & (pl.col("volume") >= INDEX_VOLUME)
            & ~pl.col("symbol").is_in(["BTCUSDT", "ETHUSDT"])
        )
        .group_by("day")
        .agg(pl.col("ret").mean())
    )
    return dict(returns.iter_rows())


def short_trade(
    bars: pl.DataFrame, start: date, hold: int, stop: float | None
) -> dict | None:
    """A short from the open of `start`, for `hold` days or until the bars end."""
    rows = bars.filter(pl.col("day") >= start).head(hold + 1)
    if rows.height < 2 or rows["day"][0] != start:
        return None
    opens, highs, closes = (rows[c].to_numpy() for c in ("open", "high", "close"))
    funding = rows["funding"].to_numpy()
    entry = opens[0]
    last = min(hold, rows.height - 1)  # exit at the open of day `hold`, or the last close
    exit_price = opens[last] if rows.height > hold else closes[-1]
    held_days = last if rows.height > hold else rows.height
    stopped = False
    if stop is not None:
        level = entry * (1 + stop)
        for j in range(held_days):
            if highs[j] >= level:
                exit_price, held_days, stopped = max(level, opens[j]) * 1.002, j + 1, True
                break
    ret = (entry - exit_price) / entry - TAKER * (entry + exit_price) / entry
    return {
        "ret": float(ret + funding[:held_days].sum()),
        "days": held_days,
        "end": rows["day"][held_days - 1] + timedelta(days=1),
        "stopped": stopped,
        "ended_early": rows.height <= hold,
    }


def run(root: Path, news: pl.DataFrame, delistings: pl.DataFrame, since: datetime, split: date):
    events = tag_events(news, delistings).filter(pl.col("tagged") >= since).sort("tagged")
    klines = archive.load("klines", root=root, interval="1d").filter(
        ~pl.col("symbol").is_in(EXCLUDED)
    )
    daily = klines.select(
        "symbol",
        day=pl.col("open_time").dt.date(),
        open="open",
        high="high",
        close="close",
        quote_volume="quote_volume",
    )
    index = altcoin_index(daily)
    funding = archive.load("fundingRate", events["symbol"].unique().to_list(), root=root)
    per_day = funding.group_by("symbol", day=pl.col("calc_time").dt.date()).agg(
        funding=pl.col("last_funding_rate").sum()
    )
    bars_all = (
        daily.filter(pl.col("symbol").is_in(events["symbol"].unique().to_list()))
        .join(per_day, on=["symbol", "day"], how="left")
        .with_columns(pl.col("funding").fill_null(0.0))
        .sort("symbol", "day")
    )
    rows = []
    for event in events.iter_rows(named=True):
        bars = bars_all.filter(pl.col("symbol") == event["symbol"])
        start = event["tagged"].date() + timedelta(days=1)
        before = bars.filter(pl.col("day") < start).tail(1)
        if before.is_empty() or float(before["quote_volume"][0]) < 100_000:
            continue  # not tradable
        for hold in HOLDS:
            for stop in (None, STOP):
                trade = short_trade(bars, start, hold, stop)
                if trade is None:
                    continue
                days = [start + timedelta(days=i) for i in range(trade["days"])]
                market = float(np.prod([1 + index.get(day, 0.0) for day in days]) - 1)
                notice = event["delisting_notice"]
                rows.append(
                    {
                        "symbol": event["symbol"],
                        "tagged": event["tagged"],
                        "hold": hold,
                        "stop": "none" if stop is None else f"{stop:.0%}",
                        "short": trade["ret"],
                        "index": market,
                        "excess": trade["ret"] + market,
                        "stopped": trade["stopped"],
                        "delisted": notice is not None
                        and notice.date() < start + timedelta(days=hold),
                        "period": "first half" if start < split else "second half",
                    }
                )
    trades = pl.DataFrame(rows)

    def summary(frame: pl.DataFrame) -> pl.DataFrame:
        return (
            frame.group_by("hold", "stop", "period")
            .agg(
                pl.len().alias("events"),
                (pl.col("short").mean() * 100).alias("short %"),
                (pl.col("index").mean() * 100).alias("index %"),
                (pl.col("excess").mean() * 100).alias("excess %"),
                (pl.col("excess").mean() / pl.col("excess").std() * pl.len().sqrt()).alias(
                    "excess t"
                ),
                ((pl.col("excess") > 0).mean() * 100).alias("excess > 0 %"),
                (pl.col("delisted").mean() * 100).alias("delisting notice %"),
                (pl.col("stopped").mean() * 100).alias("stopped %"),
            )
            .sort("hold", "stop", "period")
        )

    per_token = (
        trades.filter((pl.col("hold") == 30) & (pl.col("stop") == "none"))
        .group_by("symbol")
        .agg(pl.col("excess").mean())
    )["excess"]
    clustered_t = float(per_token.mean() / per_token.std() * per_token.len() ** 0.5)
    text = "\n".join(
        [
            "# Research 15 results: tokens under the Monitoring Tag, over weeks",
            "",
            "Generated by `uv run mb research distress`. The analysis is in "
            "[../15-distress.md](../15-distress.md).",
            "",
            f"- Events: {trades['tagged'].n_unique()} Monitoring Tag additions of "
            f"{trades['symbol'].n_unique()} tradable perpetuals since {since:%Y-%m-%d}.",
            "- Short from the open of the second UTC day after the announcement; hold "
            f"{', '.join(map(str, HOLDS))} days, or until the perpetual stops trading; with "
            f"and without a {STOP:.0%} stop on daily highs; taker fees and funding.",
            f"- Index: equally weighted liquid altcoin perpetuals (7-day average volume of at "
            f"least {INDEX_VOLUME / 1e6:.0f}M USDT). Excess: the short plus the index return.",
            "- `delisting notice %`: share of events where Binance announced the perpetual's "
            "delisting within the holding time.",
            f"- Periods split at {split}.",
            "",
            markdown_table(summary(trades), 2),
            "",
            f"With each token as one observation (30 days, no stop): {per_token.len()} tokens, "
            f"mean excess {per_token.mean() * 100:+.2f}%, t {clustered_t:.2f}.",
            "",
        ]
    )
    return trades, text
