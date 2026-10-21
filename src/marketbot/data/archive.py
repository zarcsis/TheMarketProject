"""Historical USD-M futures data from the public Binance archive (data.binance.vision).

Files are monthly zip archives of CSV with a published SHA-256 checksum. Each month is
stored as one Parquet file:

    <root>/futures_um/klines/<interval>/<SYMBOL>/<YYYY-MM>.parquet
    <root>/futures_um/fundingRate/<SYMBOL>/<YYYY-MM>.parquet

A month that is not in the archive (before listing or after delisting) is stored as an
empty file, so later runs do not ask for it again.
"""

import asyncio
import hashlib
import io
import re
import zipfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

import httpx
import polars as pl
from defusedxml import ElementTree

ARCHIVE_URL = "https://data.binance.vision"
LISTING_URL = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
MARKET = "futures/um"
DEFAULT_ROOT = Path("data/history")

KLINE_COLUMNS = (
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "count",
    "taker_buy_volume",
    "taker_buy_quote_volume",
    "ignore",
)
FUNDING_COLUMNS = ("calc_time", "funding_interval_hours", "last_funding_rate")

KLINE_SCHEMA = {
    "open_time": pl.Datetime("ms", "UTC"),
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Float64,
    "quote_volume": pl.Float64,
    "count": pl.Int64,
    "taker_buy_volume": pl.Float64,
    "taker_buy_quote_volume": pl.Float64,
}
FUNDING_SCHEMA = {
    "calc_time": pl.Datetime("ms", "UTC"),
    "funding_interval_hours": pl.Int64,
    "last_funding_rate": pl.Float64,
}

# Timestamps above this are in microseconds (the archive switched some datasets to them).
MICROSECOND_THRESHOLD = 10**14
_S3_NAMESPACE = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}

Dataset = Literal["klines", "fundingRate"]


class ChecksumError(Exception):
    pass


@dataclass(frozen=True, order=True)
class Month:
    year: int
    month: int

    @classmethod
    def parse(cls, text: str) -> "Month":
        match = re.fullmatch(r"(\d{4})-(\d{2})", text)
        if not match or not 1 <= int(match[2]) <= 12:
            raise ValueError(f"Expected YYYY-MM, got {text!r}")
        return cls(int(match[1]), int(match[2]))

    @classmethod
    def of(cls, day: date) -> "Month":
        return cls(day.year, day.month)

    def next(self) -> "Month":
        return Month(self.year + self.month // 12, self.month % 12 + 1)

    def previous(self) -> "Month":
        return Month(self.year - (self.month == 1), (self.month - 2) % 12 + 1)

    def __str__(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"


def month_range(start: Month, end: Month) -> Iterator[Month]:
    month = start
    while month <= end:
        yield month
        month = month.next()


def archive_url(dataset: Dataset, symbol: str, month: Month, interval: str | None = None) -> str:
    if dataset == "klines":
        return (
            f"{ARCHIVE_URL}/data/{MARKET}/monthly/klines/{symbol}/{interval}/"
            f"{symbol}-{interval}-{month}.zip"
        )
    return (
        f"{ARCHIVE_URL}/data/{MARKET}/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{month}.zip"
    )


def local_path(
    root: Path, dataset: Dataset, symbol: str, month: Month, interval: str | None = None
) -> Path:
    base = root / MARKET.replace("/", "_") / dataset
    if dataset == "klines":
        base = base / str(interval)
    return base / symbol / f"{month}.parquet"


# Parsing. Pure functions, unit-tested without network access.


def parse_listing(xml_text: str) -> tuple[list[str], str | None]:
    """Return (symbols, next_marker) from one page of the S3 bucket listing."""
    tree = ElementTree.fromstring(xml_text)
    prefixes = [
        element.text or "" for element in tree.findall("s3:CommonPrefixes/s3:Prefix", _S3_NAMESPACE)
    ]
    symbols = [prefix.rstrip("/").rsplit("/", 1)[-1] for prefix in prefixes]
    truncated = tree.findtext("s3:IsTruncated", default="false", namespaces=_S3_NAMESPACE)
    next_marker = tree.findtext("s3:NextMarker", namespaces=_S3_NAMESPACE)
    return symbols, next_marker if truncated == "true" else None


def verify_checksum(content: bytes, checksum_text: str) -> None:
    expected = checksum_text.split()[0].lower()
    actual = hashlib.sha256(content).hexdigest()
    if actual != expected:
        raise ChecksumError(f"SHA-256 mismatch: expected {expected}, got {actual}")


def read_zipped_csv(content: bytes) -> bytes:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = [name for name in archive.namelist() if name.endswith(".csv")]
        if len(names) != 1:
            raise ValueError(f"Expected one CSV in the archive, found {names}")
        return archive.read(names[0])


def parse_klines_csv(csv: bytes) -> pl.DataFrame:
    frame = _read_csv(csv, KLINE_COLUMNS)
    return frame.select(
        _timestamp("open_time"),
        *(pl.col(name).cast(dtype) for name, dtype in KLINE_SCHEMA.items() if name != "open_time"),
    ).sort("open_time")


def parse_funding_csv(csv: bytes) -> pl.DataFrame:
    frame = _read_csv(csv, FUNDING_COLUMNS)
    return frame.select(
        _timestamp("calc_time"),
        pl.col("funding_interval_hours").cast(pl.Int64),
        pl.col("last_funding_rate").cast(pl.Float64),
    ).sort("calc_time")


def empty_frame(dataset: Dataset) -> pl.DataFrame:
    return pl.DataFrame(schema=KLINE_SCHEMA if dataset == "klines" else FUNDING_SCHEMA)


def _read_csv(csv: bytes, columns: tuple[str, ...]) -> pl.DataFrame:
    has_header = not csv[:1].isdigit()
    return pl.read_csv(
        csv,
        has_header=has_header,
        new_columns=list(columns),
        infer_schema=False,
    )


def _timestamp(column: str) -> pl.Expr:
    raw = pl.col(column).cast(pl.Int64)
    millis = pl.when(raw > MICROSECOND_THRESHOLD).then(raw // 1000).otherwise(raw)
    return millis.cast(pl.Datetime("ms", "UTC")).alias(column)


# Network side.


async def list_symbols(client: httpx.AsyncClient, quote: str = "USDT") -> list[str]:
    """Every symbol with monthly klines in the archive, including delisted ones."""
    prefix = f"data/{MARKET}/monthly/klines/"
    symbols: list[str] = []
    marker: str | None = ""
    while marker is not None:
        response = await client.get(
            LISTING_URL, params={"delimiter": "/", "prefix": prefix, "marker": marker}
        )
        response.raise_for_status()
        page, marker = parse_listing(response.text)
        symbols += page
    return sorted(symbol for symbol in symbols if symbol.endswith(quote))


@dataclass
class DownloadStats:
    downloaded: int = 0
    cached: int = 0
    missing: int = 0
    failed: int = 0


async def download(
    client: httpx.AsyncClient,
    dataset: Dataset,
    symbols: Iterable[str],
    months: Iterable[Month],
    *,
    root: Path = DEFAULT_ROOT,
    interval: str | None = None,
    concurrency: int = 8,
    today: date | None = None,
) -> DownloadStats:
    if dataset == "klines" and interval is None:
        raise ValueError("klines need an interval")
    # The archive publishes a month a few days after it ends; do not cache its absence yet.
    settled = Month.of(today or date.today()).previous().previous()
    stats = DownloadStats()
    semaphore = asyncio.Semaphore(concurrency)

    async def fetch(symbol: str, month: Month) -> None:
        path = local_path(root, dataset, symbol, month, interval)
        if path.exists():
            stats.cached += 1
            return
        async with semaphore:
            url = archive_url(dataset, symbol, month, interval)
            try:
                frame = await _fetch_month(client, dataset, url)
            except (httpx.HTTPError, ChecksumError, ValueError) as exc:
                stats.failed += 1
                print(f"failed {url}: {type(exc).__name__}: {exc}")
                return
        if frame is None:
            stats.missing += 1
            if month > settled:
                return
            frame = empty_frame(dataset)
        else:
            stats.downloaded += 1
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        frame.write_parquet(temporary)
        temporary.replace(path)

    async with asyncio.TaskGroup() as group:
        for symbol in symbols:
            for month in months:
                group.create_task(fetch(symbol, month))
    return stats


async def _fetch_month(
    client: httpx.AsyncClient, dataset: Dataset, url: str
) -> pl.DataFrame | None:
    response = await client.get(url)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    checksum = await client.get(f"{url}.CHECKSUM")
    checksum.raise_for_status()
    verify_checksum(response.content, checksum.text)
    csv = read_zipped_csv(response.content)
    return parse_klines_csv(csv) if dataset == "klines" else parse_funding_csv(csv)


# Loading.


def load(
    dataset: Dataset,
    symbols: Iterable[str] | None = None,
    *,
    root: Path = DEFAULT_ROOT,
    interval: str | None = None,
) -> pl.DataFrame:
    """Load stored months as one frame with a `symbol` column."""
    base = root / MARKET.replace("/", "_") / dataset
    if dataset == "klines":
        base = base / str(interval)
    wanted = set(symbols) if symbols is not None else None
    frames = [
        pl.read_parquet(path).with_columns(pl.lit(path.parent.name).alias("symbol"))
        for path in sorted(base.glob("*/*.parquet"))
        if wanted is None or path.parent.name in wanted
    ]
    frames = [frame for frame in frames if frame.height]
    if not frames:
        return empty_frame(dataset).with_columns(pl.lit("").alias("symbol")).clear()
    return pl.concat(frames)
