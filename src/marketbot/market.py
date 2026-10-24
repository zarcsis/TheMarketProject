"""`mb market`: a read-only market brief from public data, for session analysis."""

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import polars as pl

from marketbot.data import announcements
from marketbot.research.feasibility import non_crypto_symbols

FAPI = "https://fapi.binance.com"
FEAR_GREED = "https://api.alternative.me/fng/?limit=2"


async def _get(client: httpx.AsyncClient, url: str, **params) -> object:
    response = await client.get(url, params=params or None)
    response.raise_for_status()
    return response.json()


def trend_line(symbol: str, rows: list[list]) -> str:
    closes = [float(row[4]) for row in rows]
    last = closes[-1]

    def change(days: int) -> str:
        return f"{(last / closes[-1 - days] - 1) * 100:+.1f}%" if len(closes) > days else "n/a"

    ma20 = sum(closes[-20:]) / min(20, len(closes))
    ma50 = sum(closes[-50:]) / min(50, len(closes))
    return (
        f"{symbol}: {last:,.2f} | 1d {change(1)}, 7d {change(7)}, 30d {change(30)} | "
        f"vs MA20 {(last / ma20 - 1) * 100:+.1f}%, vs MA50 {(last / ma50 - 1) * 100:+.1f}%"
    )


async def brief(now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    async with httpx.AsyncClient(base_url=FAPI, timeout=30) as client:
        info, tickers, premium, btc, eth = await asyncio.gather(
            _get(client, "/fapi/v1/exchangeInfo"),
            _get(client, "/fapi/v1/ticker/24hr"),
            _get(client, "/fapi/v1/premiumIndex"),
            _get(client, "/fapi/v1/klines", symbol="BTCUSDT", interval="1d", limit=60),
            _get(client, "/fapi/v1/klines", symbol="ETHUSDT", interval="1d", limit=60),
        )
        try:
            fear_greed = await _get(client, FEAR_GREED)
            fg = fear_greed["data"][0]
            fear_line = f"Fear & Greed: {fg['value']} ({fg['value_classification']})"
        except httpx.HTTPError, KeyError, ValueError:
            fear_line = "Fear & Greed: unavailable"
        try:
            delistings = await announcements.fetch_catalog(
                client, announcements.DELISTINGS, max_pages=1
            )
        except httpx.HTTPError, KeyError, ValueError:
            delistings = pl.DataFrame()

    excluded = non_crypto_symbols(info)
    crypto = {
        row["symbol"]: row
        for row in info["symbols"]
        if row["symbol"] not in excluded
        and row.get("quoteAsset") == "USDT"
        and row.get("status") == "TRADING"
    }
    lines = [f"Market brief, {now:%Y-%m-%d %H:%M} UTC", "", trend_line("BTC", btc)]
    lines += [trend_line("ETH", eth), fear_line, ""]

    movers = pl.DataFrame(
        [
            {
                "symbol": t["symbol"],
                "change %": float(t["priceChangePercent"]),
                "volume M": float(t["quoteVolume"]) / 1e6,
            }
            for t in tickers
            if t["symbol"] in crypto and float(t["quoteVolume"]) >= 50e6
        ]
    ).sort("change %")
    lines.append(f"Liquid crypto perpetuals (24h volume >= 50M USDT): {movers.height}")
    up = ", ".join(
        f"{r['symbol']} {r['change %']:+.0f}%" for r in movers.tail(5).iter_rows(named=True)
    )
    down = ", ".join(
        f"{r['symbol']} {r['change %']:+.0f}%" for r in movers.head(5).iter_rows(named=True)
    )
    lines += [f"  top gainers: {up}", f"  top losers:  {down}", ""]

    funding = pl.DataFrame(
        [
            {"symbol": p["symbol"], "funding %": float(p["lastFundingRate"]) * 100}
            for p in premium
            if p["symbol"] in crypto
        ]
    ).sort("funding %")
    lines.append(
        f"Funding (last, per period): median {funding['funding %'].median():+.4f}%; "
        f"most negative: "
        + ", ".join(
            f"{r['symbol']} {r['funding %']:+.3f}%" for r in funding.head(3).iter_rows(named=True)
        )
        + "; most positive: "
        + ", ".join(
            f"{r['symbol']} {r['funding %']:+.3f}%" for r in funding.tail(3).iter_rows(named=True)
        )
    )

    week = now - timedelta(days=7)
    recent = sorted(
        (
            datetime.fromtimestamp(row["onboardDate"] / 1000, UTC),
            symbol,
        )
        for symbol, row in crypto.items()
        if row.get("contractType") == "PERPETUAL"
        and datetime.fromtimestamp(row["onboardDate"] / 1000, UTC) >= week
    )
    lines.append(
        "New crypto perpetuals (7 days): "
        + (", ".join(f"{s} ({d:%m-%d})" for d, s in recent) if recent else "none")
    )
    if delistings.height:
        fresh = delistings.filter(pl.col("released") >= now - timedelta(days=7)).sort("released")
        lines.append("Delisting announcements (7 days):")
        lines += [
            f"  {row['released']:%m-%d %H:%M} {row['title'][:110]}"
            for row in fresh.iter_rows(named=True)
        ] or ["  none"]
    return "\n".join(lines)
