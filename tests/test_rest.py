import httpx
import pytest

from marketbot.exchange.rest import BinanceAPIError, BinanceREST
from marketbot.exchange.signing import HmacSigner

pytestmark = pytest.mark.anyio


class FixedSigner:
    def sign(self, payload: str) -> str:
        return "ab+/c="


def make_client(handler, **kwargs) -> BinanceREST:
    return BinanceREST(
        "https://api.example", "/api/v3/time", transport=httpx.MockTransport(handler), **kwargs
    )


async def test_signed_request_signs_exact_query_string() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    signer = HmacSigner("secret")
    async with make_client(
        handler, api_key="key", signer=signer, clock=lambda: 1_700_000_000.0
    ) as client:
        client.time_offset_ms = 250
        assert await client.signed("GET", "/sapi/v1/test", {"symbol": "BTCUSDT"}) == {"ok": True}

    payload, signature = requests[0].url.query.decode().rsplit("&signature=", 1)
    assert payload == "symbol=BTCUSDT&recvWindow=5000&timestamp=1700000000250"
    assert signature == signer.sign(payload)
    assert requests[0].headers["X-MBX-APIKEY"] == "key"


async def test_signature_is_percent_encoded() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={})

    async with make_client(handler, api_key="key", signer=FixedSigner()) as client:
        await client.signed("GET", "/sapi/v1/test")

    assert requests[0].url.query.decode().endswith("&signature=ab%2B%2Fc%3D")


async def test_signed_request_without_credentials_fails() -> None:
    async with make_client(lambda request: httpx.Response(200, json={})) as client:
        with pytest.raises(RuntimeError):
            await client.signed("GET", "/sapi/v1/test")


async def test_sync_time_uses_request_midpoint() -> None:
    local_times = iter([100.0, 100.2])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"serverTime": 100_600})

    async with make_client(handler, clock=lambda: next(local_times)) as client:
        assert await client.sync_time() == 500
        assert client.time_offset_ms == 500


async def test_error_response_raises_with_binance_code() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401, json={"code": -2015, "msg": "Invalid API-key, IP, or permissions for action."}
        )

    async with make_client(handler) as client:
        with pytest.raises(BinanceAPIError) as error:
            await client.public("GET", "/api/v3/test")

    assert error.value.status == 401
    assert error.value.code == -2015


async def test_used_weight_headers_are_tracked() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={}, headers={"X-MBX-USED-WEIGHT-1M": "12"})

    async with make_client(handler) as client:
        await client.public("GET", "/api/v3/test")
        assert client.used_weight == {"x-mbx-used-weight-1m": 12}
