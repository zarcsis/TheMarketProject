"""Token unlock schedules from DefiLlama's public datasets (no key).

Each protocol file has the unlock events (a cliff or a linear stream, by allocation
category) and, per allocation, the cumulative unlocked supply by day. The categories are
DefiLlama's: insiders, privateSale, publicSale, airdrop, farming, ecosystem, and
noncirculating (treasuries and reserves, left out of the circulating supply here).
"""

import asyncio
import bisect
from datetime import UTC, datetime
from typing import Any

import httpx
import polars as pl

DATASETS = "https://defillama-datasets.llama.fi"
HEADERS = {"User-Agent": "Mozilla/5.0 (research; TheMarketProject)"}
COINGECKO_LIST = "https://api.coingecko.com/api/v3/coins/list"


async def fetch_protocol_ids(client: httpx.AsyncClient) -> list[str]:
    response = await client.get(f"{DATASETS}/emissionsProtocolsList", headers=HEADERS)
    response.raise_for_status()
    return list(response.json())


async def fetch_protocol(client: httpx.AsyncClient, protocol: str) -> dict[str, Any]:
    response = await client.get(f"{DATASETS}/emissions/{protocol}", headers=HEADERS)
    response.raise_for_status()
    return response.json()


async def fetch_all(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    documents = []
    for protocol in await fetch_protocol_ids(client):
        try:
            documents.append({"id": protocol, **await fetch_protocol(client, protocol)})
        except (httpx.HTTPError, ValueError):
            continue  # a missing or malformed file: skip the protocol
        await asyncio.sleep(0.2)  # be polite to a public bucket
    return documents


async def fetch_coingecko_symbols(client: httpx.AsyncClient) -> dict[str, str]:
    """CoinGecko id -> ticker, upper case."""
    response = await client.get(COINGECKO_LIST, headers=HEADERS)
    response.raise_for_status()
    return {coin["id"]: coin["symbol"].upper() for coin in response.json()}


def circulating(document: dict[str, Any]) -> dict[int, float]:
    """Unlocked supply by day (Unix seconds), without non-circulating allocations."""
    excluded = set((document.get("categories") or {}).get("noncirculating", []))
    sections = (document.get("documentedData") or {}).get("data") or []
    total: dict[int, float] = {}
    for section in sections:
        if section.get("label") in excluded:
            continue
        for point in section.get("data") or []:
            total[point["timestamp"]] = total.get(point["timestamp"], 0.0) + float(
                point.get("unlocked") or 0.0
            )
    return total


def cliff_events(document: dict[str, Any]) -> list[dict[str, Any]]:
    """Cliff unlocks, each with the circulating supply the day before it.

    Unlocks at the token's first day (no supply before) and to non-circulating
    allocations are left out.
    """
    supply = circulating(document)
    days = sorted(supply)
    rows = []
    for event in (document.get("metadata") or {}).get("events") or []:
        if event.get("unlockType") != "cliff" or event.get("category") == "noncirculating":
            continue
        timestamp = int(event["timestamp"])
        index = bisect.bisect_left(days, timestamp)
        before = supply[days[index - 1]] if index else 0.0
        if before <= 0:
            continue
        rows.append(
            {
                "protocol": document.get("id") or document.get("name"),
                "gecko_id": document.get("gecko_id"),
                "unlock": datetime.fromtimestamp(timestamp, UTC),
                "category": event.get("category"),
                "tokens": float(sum(event.get("noOfTokens") or [0])),
                "supply_before": before,
            }
        )
    return rows


def events_frame(documents: list[dict[str, Any]]) -> pl.DataFrame:
    rows = [row for document in documents for row in cliff_events(document)]
    return pl.DataFrame(
        rows,
        schema={
            "protocol": pl.String,
            "gecko_id": pl.String,
            "unlock": pl.Datetime("us", "UTC"),
            "category": pl.String,
            "tokens": pl.Float64,
            "supply_before": pl.Float64,
        },
    )
