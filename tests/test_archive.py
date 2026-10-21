import hashlib
import io
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import polars as pl
import pytest

from marketbot.data import archive
from marketbot.data.archive import Month

pytestmark = pytest.mark.anyio

HEADER = (
    "open_time,open,high,low,close,volume,close_time,quote_volume,count,"
    "taker_buy_volume,taker_buy_quote_volume,ignore\n"
)
ROW_1 = "1788220800000,102.99,103.04,102.90,103.03,28031.98,1788221099999,2886365.33,3966,15795.45,1626419.67,0\n"  # noqa: E501
ROW_2 = "1788221100000,103.03,103.15,103.02,103.14,33631.94,1788221399999,3466931.04,4544,20217.25,2084171.46,0\n"  # noqa: E501


def zipped(csv: str, name: str = "data.csv") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive_file:
        archive_file.writestr(name, csv)
    return buffer.getvalue()


def test_month_arithmetic() -> None:
    assert Month.parse("2025-12").next() == Month(2026, 1)
    assert Month(2026, 1).previous() == Month(2025, 12)
    assert [str(m) for m in archive.month_range(Month(2025, 11), Month(2026, 2))] == [
        "2025-11",
        "2025-12",
        "2026-01",
        "2026-02",
    ]
    with pytest.raises(ValueError):
        Month.parse("2025-13")


def test_parse_klines_with_and_without_header() -> None:
    with_header = archive.parse_klines_csv((HEADER + ROW_2 + ROW_1).encode())
    without_header = archive.parse_klines_csv((ROW_1 + ROW_2).encode())
    assert with_header.equals(without_header)
    assert with_header.height == 2
    assert with_header["open_time"][0] == datetime(2026, 9, 1, tzinfo=UTC)
    assert with_header["close"].to_list() == [103.03, 103.14]
    assert set(with_header.columns) == set(archive.KLINE_SCHEMA)


def test_parse_klines_converts_microseconds() -> None:
    row = ROW_1.replace("1788220800000", "1788220800000000", 1)
    frame = archive.parse_klines_csv(row.encode())
    assert frame["open_time"][0] == datetime(2026, 9, 1, tzinfo=UTC)


def test_parse_funding() -> None:
    csv = b"calc_time,funding_interval_hours,last_funding_rate\n1788220800005,8,0.00003679\n"
    frame = archive.parse_funding_csv(csv)
    assert frame["last_funding_rate"][0] == pytest.approx(0.00003679)
    assert frame["funding_interval_hours"][0] == 8


def test_parse_listing_pages() -> None:
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
<NextMarker>data/futures/um/monthly/klines/ETHUSDT/</NextMarker>
<IsTruncated>true</IsTruncated>
<CommonPrefixes><Prefix>data/futures/um/monthly/klines/BTCUSDT/</Prefix></CommonPrefixes>
<CommonPrefixes><Prefix>data/futures/um/monthly/klines/ETHUSDT/</Prefix></CommonPrefixes>
</ListBucketResult>"""
    symbols, marker = archive.parse_listing(xml)
    assert symbols == ["BTCUSDT", "ETHUSDT"]
    assert marker == "data/futures/um/monthly/klines/ETHUSDT/"
    _, last = archive.parse_listing(xml.replace("<IsTruncated>true", "<IsTruncated>false"))
    assert last is None


def test_checksum_mismatch_raises() -> None:
    content = b"payload"
    archive.verify_checksum(content, f"{hashlib.sha256(content).hexdigest()}  file.zip\n")
    with pytest.raises(archive.ChecksumError):
        archive.verify_checksum(content, "00" * 32)


async def test_download_stores_months_and_caches_absence(tmp_path: Path) -> None:
    content = zipped(HEADER + ROW_1 + ROW_2)
    checksum = f"{hashlib.sha256(content).hexdigest()}  BTCUSDT-5m-2026-09.zip"
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        requests.append(url)
        if "2026-09" not in url:
            return httpx.Response(404)
        return httpx.Response(
            200,
            text=checksum if url.endswith(".CHECKSUM") else None,
            content=None if url.endswith(".CHECKSUM") else content,
        )

    months = [Month(2026, 8), Month(2026, 9), Month(2026, 10)]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        stats = await archive.download(
            client,
            "klines",
            ["BTCUSDT"],
            months,
            root=tmp_path,
            interval="5m",
            today=date(2026, 10, 21),
        )
        assert (stats.downloaded, stats.missing, stats.failed) == (1, 2, 0)
        # 2026-08 is settled, so its absence is cached; 2026-10 is not published yet.
        assert archive.local_path(tmp_path, "klines", "BTCUSDT", Month(2026, 8), "5m").exists()
        assert not archive.local_path(tmp_path, "klines", "BTCUSDT", Month(2026, 10), "5m").exists()

        requests.clear()
        again = await archive.download(
            client,
            "klines",
            ["BTCUSDT"],
            months,
            root=tmp_path,
            interval="5m",
            today=date(2026, 10, 21),
        )
        assert again.cached == 2
        assert requests == [archive.archive_url("klines", "BTCUSDT", Month(2026, 10), "5m")]

    frame = archive.load("klines", root=tmp_path, interval="5m")
    assert frame.height == 2
    assert frame["symbol"].unique().to_list() == ["BTCUSDT"]
    assert frame.schema["open_time"] == pl.Datetime("ms", "UTC")


async def test_download_counts_checksum_failure(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith(".CHECKSUM"):
            return httpx.Response(200, text="00" * 32)
        return httpx.Response(200, content=zipped(HEADER + ROW_1))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        stats = await archive.download(
            client, "klines", ["BTCUSDT"], [Month(2026, 9)], root=tmp_path, interval="5m"
        )
    assert stats.failed == 1
    assert not list(tmp_path.rglob("*.parquet"))  # noqa: ASYNC240
