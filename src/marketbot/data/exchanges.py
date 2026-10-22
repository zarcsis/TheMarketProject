"""Listing and delisting announcements of Bybit and OKX (public APIs, no key).

Bybit: `/v5/announcements/index`, types `new_crypto` and `delistings`. OKX:
`/api/v5/support/announcements`, types `announcements-new-listings` and
`announcements-delistings`. Titles (and Bybit's descriptions) name the tokens; notices that
name none ("Delisting of Selected Perpetual Contracts", "OKX to delist several perpetual
futures") cannot be used and are skipped.
"""

import asyncio
import re

import httpx
import polars as pl

HEADERS = {"User-Agent": "Mozilla/5.0 (research; TheMarketProject)", "Accept": "application/json"}
BYBIT_URL = "https://api.bybit.com/v5/announcements/index"
OKX_URL = "https://www.okx.com/api/v5/support/announcements"
BYBIT_TYPES = ("new_crypto", "delistings")
OKX_TYPES = ("announcements-new-listings", "announcements-delistings")


async def fetch_bybit(client: httpx.AsyncClient, kind: str, max_pages: int = 60) -> list[dict]:
    rows = []
    for page in range(1, max_pages + 1):
        response = await client.get(
            BYBIT_URL,
            params={"locale": "en-US", "type": kind, "limit": 100, "page": page},
            headers=HEADERS,
        )
        response.raise_for_status()
        items = response.json()["result"]["list"]
        if not items:
            break
        rows += [
            {
                "exchange": "bybit",
                "type": kind,
                "title": item["title"],
                "description": item.get("description") or "",
                "ms": item.get("publishTime") or item.get("dateTimestamp"),
            }
            for item in items
        ]
        await asyncio.sleep(0.3)
    return rows


async def fetch_okx(client: httpx.AsyncClient, kind: str, max_pages: int = 200) -> list[dict]:
    rows = []
    for page in range(1, max_pages + 1):
        response = await client.get(
            OKX_URL, params={"annType": kind, "page": page}, headers=HEADERS
        )
        response.raise_for_status()
        data = response.json().get("data") or []
        details = data[0]["details"] if data else []
        if not details:
            break
        rows += [
            {
                "exchange": "okx",
                "type": kind,
                "title": detail["title"],
                "description": "",
                "ms": int(detail["pTime"]),
            }
            for detail in details
        ]
        if page >= int(data[0].get("totalPage", page)):
            break
        await asyncio.sleep(0.3)
    return rows


async def fetch_all(client: httpx.AsyncClient) -> pl.DataFrame:
    rows = []
    for kind in BYBIT_TYPES:
        rows += await fetch_bybit(client, kind)
    for kind in OKX_TYPES:
        rows += await fetch_okx(client, kind)
    return pl.DataFrame(rows).with_columns(
        released=pl.from_epoch("ms", time_unit="ms").dt.replace_time_zone("UTC")
    )


_LIST_SPLIT = re.compile(r",\s*|\s+and\s+|\s*&\s*")
_TOKEN = re.compile(r"^([A-Z0-9]{1,15})(?:/USDT\b|/USD\b|\s*\(|$)")


def _tokens(text: str) -> list[str]:
    """Tickers from a list such as "A, B and C" or "PENGU/USDT" or "PARTI (Particle)"."""
    found = []
    for part in _LIST_SPLIT.split(text.strip()):
        match = _TOKEN.match(part.strip())
        if match:
            found.append(match.group(1))
    return list(dict.fromkeys(found))


_BYBIT_PERP_DELIST = re.compile(r"Delisting of\s+([A-Z0-9]+)USDT\s+Perpetual", re.IGNORECASE)
_BYBIT_SPOT_DELIST = re.compile(r"^Delisting of\s+(.+)$", re.IGNORECASE)
_PAIRS = re.compile(r"\b([A-Z0-9]{1,15})/USDT\b")
_BYBIT_PERP_LIST = re.compile(r"New Listing\s*:\s*([A-Z0-9]+)USDT\s+Perpetual", re.IGNORECASE)
_BYBIT_SPOT_LIST = re.compile(r"New Listing\s*:\s*([A-Z0-9]+)/USDT", re.IGNORECASE)
_OKX_SPOT_DELIST = re.compile(r"^OKX to delist (.+?) spot trading pairs?$", re.IGNORECASE)
_OKX_PERP_DELIST = re.compile(r"^OKX to delist perpetual futures for (.+?) crypto", re.IGNORECASE)
_OKX_SPOT_LIST = re.compile(
    r"^OKX (?:to list|will launch|to launch) (.+?) for spot trading", re.IGNORECASE
)
_OKX_PERP_LIST = re.compile(
    r"^OKX to (?:list|launch) perpetual(?: futures)? for (.+?) crypto", re.IGNORECASE
)
_VAGUE = re.compile(
    r"\b(?:several|selected|leveraged|USDC|X-Perps|TradFi|Pre-IPO)\b|\bequit(?:y|ies)\b"
    r"|margin trading pair",
    re.IGNORECASE,
)


def events(exchange: str, title: str, description: str = "") -> list[tuple[str, str]]:
    """(ticker, kind) pairs; kinds: spot listing, perp listing, spot delisting, perp delisting."""
    title = " ".join(title.split())
    if _VAGUE.search(title):
        return []
    if exchange == "bybit":
        if match := _BYBIT_PERP_DELIST.search(title):
            return [(match.group(1).upper(), "perp delisting")]
        if match := _BYBIT_PERP_LIST.search(title):
            return [(match.group(1).upper(), "perp listing")]
        if match := _BYBIT_SPOT_LIST.search(title):
            return [(match.group(1).upper(), "spot listing")]
        if match := _BYBIT_SPOT_DELIST.search(title):
            if "Perpetual" in title:
                return []  # a perpetual delisting without a named contract
            tokens = _PAIRS.findall(description) or _tokens(match.group(1))
            return [(token, "spot delisting") for token in dict.fromkeys(tokens)]
        return []
    if exchange == "okx":
        for pattern, kind in (
            (_OKX_PERP_DELIST, "perp delisting"),
            (_OKX_SPOT_DELIST, "spot delisting"),
            (_OKX_PERP_LIST, "perp listing"),
            (_OKX_SPOT_LIST, "spot listing"),
        ):
            if match := pattern.search(title):
                return [(token, kind) for token in _tokens(match.group(1))]
    return []


def perp_delistings(title: str) -> list[str]:
    """USDT perpetuals whose own Bybit or OKX perpetual is being delisted (live parser)."""
    return [
        f"{ticker}USDT"
        for exchange in ("bybit", "okx")
        for ticker, kind in events(exchange, title)
        if kind == "perp delisting"
    ]


async def latest_delistings() -> pl.DataFrame:
    """The latest delisting notices of Bybit and OKX, as `title` and `released`."""
    async with httpx.AsyncClient(timeout=20) as client:
        rows = await fetch_bybit(client, "delistings", max_pages=1)
        rows += await fetch_okx(client, "announcements-delistings", max_pages=1)
    return pl.DataFrame(rows).select(
        "title", released=pl.from_epoch("ms", time_unit="ms").dt.replace_time_zone("UTC")
    )
