"""Binance announcements (public CMS endpoint, no key): listings and delistings.

Catalogs: 48 = new cryptocurrency listings, 161 = delistings. Each article has a title and
a release time; symbols are parsed from the title.
"""

import asyncio
import re
from pathlib import Path

import httpx
import polars as pl

URL = "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"
NEW_LISTINGS = 48
DELISTINGS = 161
PAGE_SIZE = 50
HEADERS = {"User-Agent": "Mozilla/5.0 (research; TheMarketProject)"}

_PERP = re.compile(r"\b([A-Z0-9]{2,20}USDT)\b")
_SPOT_DELIST = re.compile(r"^Binance Will Delist (.+?) on \d{4}-\d{2}-\d{2}", re.IGNORECASE)


async def fetch_catalog(
    client: httpx.AsyncClient, catalog_id: int, max_pages: int = 40
) -> pl.DataFrame:
    rows: list[dict] = []
    for page in range(1, max_pages + 1):
        response = await client.get(
            URL,
            params={"type": 1, "catalogId": catalog_id, "pageNo": page, "pageSize": PAGE_SIZE},
            headers=HEADERS,
        )
        response.raise_for_status()
        catalogs = response.json()["data"]["catalogs"]
        articles = catalogs[0]["articles"] if catalogs else []
        if not articles:
            break
        rows += [
            {"id": a["id"], "title": a["title"], "release_ms": a["releaseDate"]} for a in articles
        ]
        await asyncio.sleep(0.5)  # be polite to a public website endpoint
    return pl.DataFrame(rows).with_columns(
        released=pl.from_epoch("release_ms", time_unit="ms").dt.replace_time_zone("UTC"),
        catalog=pl.lit(catalog_id),
    )


def delisted_perpetuals(title: str) -> list[str]:
    """USD-M perpetuals affected by a delisting announcement.

    Futures notices name the contracts ("Binance Futures Will Delist XUSDT ..."); spot
    notices name tokens ("Binance Will Delist A, B and C on <date>"), whose USDT perpetuals
    are affected too.
    """
    if "Futures" in title and "Delist" in title:
        return sorted(set(_PERP.findall(title)))
    match = _SPOT_DELIST.match(title)
    if match:
        tokens = re.split(r",\s*|\s+and\s+", match.group(1))
        return sorted({f"{token.strip()}USDT" for token in tokens if token.strip().isalnum()})
    return []


_SPOT_LIST = re.compile(r"^Binance Will List ", re.IGNORECASE)
_TICKER = re.compile(r"\(([A-Z0-9]{2,20})\)")


def spot_listed_perpetuals(title: str) -> list[str]:
    """USDT perpetuals of tokens in a spot listing announcement ("Binance Will List X (X)")."""
    if not _SPOT_LIST.match(title):
        return []
    return sorted({f"{ticker}USDT" for ticker in _TICKER.findall(title)})


def save(frame: pl.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(path)
