"""Upbit announcements (public website API, no key): listings, delistings, and warnings.

Upbit is the largest Korean exchange; its KRW listings are known for moving prices on other
exchanges. The "trade" category holds listings ("신규 거래지원", new trading support, and
"디지털 자산 추가", an asset added to a market), delistings ("거래지원 종료", end of
trading support), and warnings ("유의 종목 지정", designated for investment caution).
Titles name tokens as "KoreanName(TICKER)" or "TICKER(KoreanName)".
"""

import asyncio
import re

import httpx
import polars as pl

URL = "https://api-manager.upbit.com/api/v1/announcements"
PAGE_SIZE = 20
HEADERS = {"User-Agent": "Mozilla/5.0 (research; TheMarketProject)", "Accept": "application/json"}

_TICKER_AFTER = re.compile(r"\(([A-Z0-9]{1,15})\)")  # 웜홀(W)
_TICKER_BEFORE = re.compile(r"(?<![A-Z0-9])([A-Z0-9]{2,15})\((?![A-Z0-9])")  # PUFFER(퍼퍼)
_MARKETS = re.compile(r"\(([A-Z ,]+)마켓\)|([A-Z, ]+)마켓 디지털 자산 추가")
_TICKER_MARKETS = re.compile(r"\(([A-Z0-9]{1,15})\)\(([A-Z ,]+)마켓\)")  # 아이오넷(IO)(BTC 마켓)


async def fetch_notices(
    client: httpx.AsyncClient, category: str = "trade", max_pages: int = 60
) -> pl.DataFrame:
    rows: list[dict] = []
    for page in range(1, max_pages + 1):
        response = await client.get(
            URL,
            params={"os": "web", "page": page, "per_page": PAGE_SIZE, "category": category},
            headers=HEADERS,
        )
        response.raise_for_status()
        notices = response.json()["data"]["notices"]
        if not notices:
            break
        rows += [
            {"id": n["id"], "title": n["title"], "first": n["first_listed_at"]} for n in notices
        ]
        await asyncio.sleep(0.5)  # be polite to a public website endpoint
    return (
        pl.DataFrame(rows)
        .with_columns(
            released=pl.col("first")
            .str.to_datetime("%Y-%m-%dT%H:%M:%S%z")
            .dt.convert_time_zone("UTC")
        )
        .drop("first")
    )


def tickers(title: str) -> list[str]:
    """Token tickers named in a title, in order of appearance, without duplicates."""
    found = [*_TICKER_AFTER.finditer(title), *_TICKER_BEFORE.finditer(title)]
    names = [match.group(1) for match in sorted(found, key=lambda match: match.start())]
    return list(dict.fromkeys(name for name in names if name not in ("KRW", "BTC", "USDT")))


def kind(title: str) -> str | None:
    """The event in an announcement title, or None for other notices.

    - "krw listing": trading opens in the KRW market (a new listing, or an added market);
    - "other listing": trading opens only in the BTC or USDT market;
    - "delisting": trading support ends;
    - "warning": designated for investment caution (not an extension or a release);
    - "caution call": a softer call for caution.
    """
    if "신규 거래지원" in title or "디지털 자산 추가" in title:
        markets = " ".join(next((g for g in m.groups() if g), "") for m in _MARKETS.finditer(title))
        return "krw listing" if "KRW" in markets else "other listing"
    if "거래지원 종료" in title:
        return "delisting"
    if "유의 종목 지정" in title and "연장" not in title and "해제" not in title:
        return "warning"
    if "유의 촉구" in title:
        return "caution call"
    return None


def events(title: str) -> list[tuple[str, str]]:
    """(ticker, kind) for each token in a title; a token's own market list overrides."""
    title_kind = kind(title)
    if title_kind is None:
        return []
    own_markets = {match.group(1): match.group(2) for match in _TICKER_MARKETS.finditer(title)}
    result = []
    for ticker in tickers(title):
        ticker_kind = title_kind
        if title_kind.endswith("listing") and ticker in own_markets:
            ticker_kind = "krw listing" if "KRW" in own_markets[ticker] else "other listing"
        result.append((ticker, ticker_kind))
    return result
