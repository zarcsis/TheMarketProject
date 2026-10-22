"""Research 13: out-of-time check of the event strategies.

The research used Binance's monthly archives up to 2026-09. This study takes the events
since then from the live sources the bot uses (the announcements catalogs and
exchangeInfo), fetches 5-minute klines and funding from the public REST API, and simulates
each strategy with the bot's own settings:

- delisting-short-4h, monitoring-short-1d, airdrop-short-7d: short at the first 5-minute
  bar at least the strategy's delay after the announcement;
- listing-short-busy-7d: short at the open of the day after the listing day, if the
  listing day traded at least the busy threshold.

Stops are checked on 5-minute highs and fill at the stop (or a worse open) plus slippage.
Trades still within their holding time are marked at the last close and reported as open.
Re-run it at any time: `uv run mb research forward`.
"""

import asyncio
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import polars as pl

from marketbot.data import announcements, exchanges
from marketbot.exchange.rest import BinanceREST
from marketbot.research.delistings import STOP_SLIPPAGE, TAKER
from marketbot.research.feasibility import markdown_table
from marketbot.strategy.delisting import (
    AIRDROP_CONFIG,
    DEFAULT_CONFIG,
    EXCHANGE_DELISTING_CONFIG,
    MONITORING_CONFIG,
)
from marketbot.strategy.listing import DEFAULT_CONFIG as LISTING_CONFIG
from marketbot.strategy.runtime import parse_klines

BAR = timedelta(minutes=5)
MIN_VOLUME_24H = 100_000.0


@dataclass(frozen=True)
class Rule:
    strategy: str
    stop: float
    hold: timedelta
    delay: timedelta
    expected: str  # the research estimate per trade, for comparison


RULES = {
    "delisting-short-4h": Rule(
        "delisting-short-4h",
        float(DEFAULT_CONFIG.stop),
        DEFAULT_CONFIG.hold,
        DEFAULT_CONFIG.delay,
        "+5% (research 04)",
    ),
    "monitoring-short-1d": Rule(
        "monitoring-short-1d",
        float(MONITORING_CONFIG.stop),
        MONITORING_CONFIG.hold,
        MONITORING_CONFIG.delay,
        "+3.5% to +6.8% (research 07)",
    ),
    "airdrop-short-7d": Rule(
        "airdrop-short-7d",
        float(AIRDROP_CONFIG.stop),
        AIRDROP_CONFIG.hold,
        AIRDROP_CONFIG.delay,
        "+10% (research 09)",
    ),
    "listing-short-busy-7d": Rule(
        "listing-short-busy-7d",
        float(LISTING_CONFIG.stop),
        timedelta(days=LISTING_CONFIG.hold_days),
        timedelta(0),
        "+2% to +8% (research 03)",
    ),
    "exchange-delisting-short-7d": Rule(
        "exchange-delisting-short-7d",
        float(EXCHANGE_DELISTING_CONFIG.stop),
        EXCHANGE_DELISTING_CONFIG.hold,
        EXCHANGE_DELISTING_CONFIG.delay,
        "+4.4% (research 14)",
    ),
}


def simulate(
    bars: pl.DataFrame, funding: pl.DataFrame, start: datetime, rule: Rule
) -> dict[str, Any] | None:
    """A short from the first bar at or after `start`; None without a bar near `start`."""
    times = bars["open_time"]
    index = int(times.search_sorted(start))
    if index >= bars.height or times[index] - start > 2 * BAR:
        return None
    opens, highs, closes = (bars[c].to_numpy() for c in ("open", "high", "close"))
    entry = float(opens[index])
    level = entry * (1 + rule.stop)
    end_time = times[index] + rule.hold
    last = int(times.search_sorted(end_time)) - 1  # the last bar inside the holding time
    complete = last < bars.height - 1 or times[-1] + BAR >= end_time
    last = min(last, bars.height - 1)
    status, exit_price, exit_time = "held", float(closes[last]), times[last] + BAR
    for j in range(index, last + 1):
        if highs[j] >= level:
            status = "stopped"
            exit_price = max(level, float(opens[j])) * (1 + STOP_SLIPPAGE)
            exit_time = times[j] + BAR
            break
    if status == "held" and not complete:
        status = "open"
    paid = (
        funding.filter((pl.col("time") > times[index]) & (pl.col("time") <= exit_time))[
            "rate"
        ].sum()
        if funding.height
        else 0.0
    )
    ret = (entry - exit_price) / entry - TAKER * (entry + exit_price) / entry
    return {
        "entry_time": times[index],
        "entry": entry,
        "exit_time": exit_time,
        "status": status,
        "return": ret,
        "with_funding": ret + float(paid),
    }


async def _klines(rest: BinanceREST, symbol: str, start: datetime, end: datetime) -> pl.DataFrame:
    frames = []
    cursor = start
    while cursor < end:
        rows = await rest.public(
            "GET",
            "/fapi/v1/klines",
            {
                "symbol": symbol,
                "interval": "5m",
                "startTime": int(cursor.timestamp() * 1000),
                "endTime": int(end.timestamp() * 1000),
                "limit": 1500,
            },
        )
        if not rows:
            break
        frame = parse_klines(symbol, rows)
        frames.append(frame)
        cursor = frame["open_time"][-1] + BAR
        if len(rows) < 1500:
            break
    return pl.concat(frames) if frames else pl.DataFrame()


async def _funding(rest: BinanceREST, symbol: str, start: datetime, end: datetime) -> pl.DataFrame:
    rows = await rest.public(
        "GET",
        "/fapi/v1/fundingRate",
        {
            "symbol": symbol,
            "startTime": int(start.timestamp() * 1000),
            "endTime": int(end.timestamp() * 1000),
            "limit": 1000,
        },
    )
    return pl.DataFrame(
        {
            "time": [datetime.fromtimestamp(r["fundingTime"] / 1000, UTC) for r in rows],
            "rate": [float(r["fundingRate"]) for r in rows],
        },
        schema={"time": pl.Datetime("us", "UTC"), "rate": pl.Float64},
    )


async def announcement_events(since: datetime) -> list[tuple[str, str, datetime]]:
    """(strategy, symbol, announced) from the announcements catalogs since `since`."""
    sources = (
        ("delisting-short-4h", announcements.DELISTINGS, DEFAULT_CONFIG.parser),
        ("monitoring-short-1d", announcements.NEWS, MONITORING_CONFIG.parser),
        ("airdrop-short-7d", announcements.NEW_LISTINGS, AIRDROP_CONFIG.parser),
    )
    events = []
    binance_delistings: dict[str, list[datetime]] = {}
    async with httpx.AsyncClient(timeout=30) as client:
        for strategy, catalog, parser in sources:
            frame = await announcements.fetch_catalog(client, catalog, max_pages=3)
            for title, released in frame.select("title", "released").iter_rows():
                if strategy == "delisting-short-4h":
                    for symbol in parser(title):
                        binance_delistings.setdefault(symbol, []).append(released)
                if released >= since:
                    events += [(strategy, symbol, released) for symbol in parser(title)]
    # Bybit and OKX delistings, except tokens Binance announced delisting in the 30 days
    # before (research 14 counts those apart; the bot skips perpetuals Binance delists).
    frame = await exchanges.latest_delistings()
    for title, released in frame.select("title", "released").iter_rows():
        if released < since:
            continue
        for symbol in EXCHANGE_DELISTING_CONFIG.parser(title):
            earlier = binance_delistings.get(symbol, [])
            if not any(timedelta(0) <= released - day < timedelta(days=30) for day in earlier):
                events.append(("exchange-delisting-short-7d", symbol, released))
    return sorted(set(events), key=lambda event: event[2])


def listing_events(info: dict, since: date, until: date) -> list[tuple[str, date]]:
    """Crypto USDT perpetuals listed between `since` and `until` (exclusive)."""
    rows = []
    for row in info["symbols"]:
        if (
            row.get("contractType") == "PERPETUAL"
            and row.get("underlyingType") == "COIN"
            and row.get("quoteAsset") == "USDT"
        ):
            listed = datetime.fromtimestamp(row["onboardDate"] / 1000, UTC).date()
            if since <= listed < until:
                rows.append((row["symbol"], listed))
    return sorted(rows, key=lambda row: row[1])


async def collect(since: datetime, now: datetime) -> pl.DataFrame:
    rows = []
    async with BinanceREST("https://fapi.binance.com", "/fapi/v1/time") as rest:
        info = await rest.public("GET", "/fapi/v1/exchangeInfo")
        trading = {r["symbol"] for r in info["symbols"] if r.get("status") == "TRADING"}
        known = {r["symbol"] for r in info["symbols"]}
        starts: list[tuple[str, str, datetime, datetime, str]] = []
        for strategy, symbol, announced in await announcement_events(since):
            starts.append(
                (strategy, symbol, announced, announced + RULES[strategy].delay, "announced")
            )
        for symbol, listed in listing_events(info, since.date(), now.date()):
            day1 = datetime.combine(listed + timedelta(days=1), datetime.min.time(), UTC)
            if day1 > now:
                continue
            daily = parse_klines(
                symbol,
                await rest.public(
                    "GET",
                    "/fapi/v1/klines",
                    {
                        "symbol": symbol,
                        "interval": "1d",
                        "startTime": int((day1 - timedelta(days=1)).timestamp() * 1000),
                        "limit": 1,
                    },
                ),
            )
            volume = float(daily["quote_volume"][0]) if daily.height else 0.0
            if volume >= LISTING_CONFIG.min_listing_volume:
                starts.append(("listing-short-busy-7d", symbol, day1, day1, f"listed {listed}"))
        for strategy, symbol, event_time, start, note in starts:
            rule = RULES[strategy]
            end = min(start + rule.hold + BAR, now)
            row: dict[str, Any] = {
                "strategy": strategy,
                "symbol": symbol,
                "event": event_time,
                "note": note,
            }
            if symbol not in known:
                rows.append({**row, "status": "skipped: no Binance perpetual"})
                continue
            try:
                bars = await _klines(rest, symbol, start - timedelta(hours=1), end)
                history = await rest.public(
                    "GET",
                    "/fapi/v1/klines",
                    {
                        "symbol": symbol,
                        "interval": "1h",
                        "endTime": int(event_time.timestamp() * 1000),
                        "limit": 25,
                    },
                )
                funding = await _funding(rest, symbol, start, end)
            except Exception as exc:  # delisted symbols, for example
                rows.append({**row, "status": f"no data ({type(exc).__name__})"})
                continue
            if strategy == "airdrop-short-7d" and len(history) <= 24:
                rows.append({**row, "status": "skipped: perpetual too new"})
                continue
            # As in research 10 and 14: a perpetual that hardly traded in the day before
            # (usually one Binance had already delisted) is not tradable.
            if sum(float(bar[7]) for bar in history[-24:]) < MIN_VOLUME_24H:
                rows.append({**row, "status": "skipped: not tradable"})
                continue
            result = simulate(bars, funding, start, rule) if bars.height else None
            if result is None:
                status = "no data" if symbol not in trading else "no bar at entry"
                rows.append({**row, "status": status})
                continue
            rows.append({**row, **result})
            await asyncio.sleep(0.1)
    return pl.DataFrame(rows, infer_schema_length=None)


def report(frame: pl.DataFrame, since: datetime, now: datetime) -> str:
    traded = frame.filter(pl.col("return").is_not_null()) if "return" in frame.columns else frame
    summary_rows = []
    for name, rule in RULES.items():
        part = traded.filter(pl.col("strategy") == name)
        closed = part.filter(pl.col("status") != "open")
        summary_rows.append(
            {
                "strategy": name,
                "trades": part.height,
                "closed": closed.height,
                "mean of closed, with funding %": closed["with_funding"].mean() * 100
                if closed.height
                else None,
                "wins": int((closed["with_funding"] > 0).sum()) if closed.height else 0,
                "research estimate": rule.expected,
            }
        )
    table = frame.select(
        "strategy",
        "symbol",
        pl.col("event").dt.strftime("%Y-%m-%d %H:%M").alias("event (UTC)"),
        "status",
        (pl.col("with_funding") * 100).alias("return with funding %")
        if "with_funding" in frame.columns
        else pl.lit(None).alias("return with funding %"),
    )
    return "\n".join(
        [
            "# Research 13 results: out-of-time check",
            "",
            "Generated by `uv run mb research forward`. The analysis is in "
            "[../13-out-of-time.md](../13-out-of-time.md).",
            "",
            f"- Events from {since:%Y-%m-%d} to {now:%Y-%m-%d %H:%M} UTC, after the research "
            "data ends.",
            "- Simulated with the bot's settings on public 5-minute klines and funding; "
            f"taker fees {TAKER:.2%} per side, stop slippage {STOP_SLIPPAGE:.1%}.",
            "- `open`: still within its holding time, marked at the last close.",
            "",
            markdown_table(pl.DataFrame(summary_rows), 2),
            "",
            "## Events",
            "",
            markdown_table(table, 2),
            "",
        ]
    )


SHORT_NAMES = {
    "delisting-short-4h": "delisting 4h",
    "monitoring-short-1d": "monitoring 1d",
    "airdrop-short-7d": "airdrop 7d",
    "listing-short-busy-7d": "listing 7d",
    "exchange-delisting-short-7d": "bybit/okx 7d",
}


def brief(frame: pl.DataFrame, since: datetime) -> str:
    """A phone-width summary: closed trades, their mean with funding, and the research
    estimate, per strategy."""
    traded = frame.filter(pl.col("return").is_not_null()) if "return" in frame.columns else frame
    lines = [
        f"Out-of-time since {since:%Y-%m-%d}",
        f"{'strategy':<14}{'n':>3}{'mean%':>6}{'open':>5}  est.%",
    ]
    for name, rule in RULES.items():
        part = traded.filter(pl.col("strategy") == name) if traded.height else traded
        closed = part.filter(pl.col("status") != "open") if part.height else part
        mean = f"{closed['with_funding'].mean() * 100:+.1f}" if closed.height else "-"
        opened = part.height - closed.height
        estimate = rule.expected.split(" (")[0].replace("% to ", "..").replace("%", "")
        lines.append(f"{SHORT_NAMES[name]:<14}{closed.height:>3}{mean:>6}{opened:>5}  {estimate}")
    return "\n".join(lines)
