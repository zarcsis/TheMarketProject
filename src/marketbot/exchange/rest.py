"""Minimal async REST client for one Binance host."""

import time
from collections.abc import Callable, Mapping
from decimal import Decimal
from typing import Any
from urllib.parse import quote, urlencode

import httpx

from marketbot.exchange.signing import Signer

WEIGHT_HEADER_PREFIXES = ("x-mbx-used-weight", "x-sapi-used")
CLOCK_ERROR = -1021  # "Timestamp for this request is outside of the recvWindow"


class BinanceAPIError(Exception):
    def __init__(self, status: int, code: int | None, message: str) -> None:
        super().__init__(f"HTTP {status}, code {code}: {message}")
        self.status = status
        self.code = code
        self.message = message


class BinanceREST:
    """One Binance REST host (spot/SAPI or USD-M futures).

    Signed requests carry every parameter in the query string, so the signed payload is
    exactly the string that is sent.
    """

    def __init__(
        self,
        base_url: str,
        time_path: str,
        *,
        api_key: str | None = None,
        signer: Signer | None = None,
        recv_window_ms: int = 5000,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 10.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, transport=transport, timeout=timeout)
        self._time_path = time_path
        self._api_key = api_key
        self._signer = signer
        self._recv_window_ms = recv_window_ms
        self._clock = clock
        self.time_offset_ms = 0
        self.used_weight: dict[str, int] = {}

    async def __aenter__(self) -> "BinanceREST":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    async def sync_time(self) -> int:
        """Measure the server clock offset and apply it to signed request timestamps."""
        before = self._now_ms()
        data = await self.public("GET", self._time_path)
        after = self._now_ms()
        self.time_offset_ms = int(data["serverTime"]) - (before + after) // 2
        return self.time_offset_ms

    async def public(self, method: str, path: str, params: Mapping[str, Any] | None = None) -> Any:
        return await self._send(method, path, _encode(params or {}), headers={})

    async def signed(self, method: str, path: str, params: Mapping[str, Any] | None = None) -> Any:
        try:
            return await self._signed_once(method, path, params)
        except BinanceAPIError as exc:
            if exc.code != CLOCK_ERROR:
                raise
        # The computer's clock jumped (a correction after a power cut or a sleep): measure the
        # offset again and retry once. Binance rejects such a request before acting on it, so
        # the retry cannot duplicate an order.
        await self.sync_time()
        return await self._signed_once(method, path, params)

    async def _signed_once(self, method: str, path: str, params: Mapping[str, Any] | None) -> Any:
        if not self._api_key or self._signer is None:
            raise RuntimeError("Signed request requires an API key and a signer")
        query = _encode(
            {
                **(params or {}),
                "recvWindow": self._recv_window_ms,
                "timestamp": self._now_ms() + self.time_offset_ms,
            }
        )
        signature = quote(self._signer.sign(query), safe="")
        return await self._send(
            method,
            path,
            f"{query}&signature={signature}",
            headers={"X-MBX-APIKEY": self._api_key},
        )

    def _now_ms(self) -> int:
        return int(self._clock() * 1000)

    async def _send(self, method: str, path: str, query: str, headers: dict[str, str]) -> Any:
        url = f"{path}?{query}" if query else path
        response = await self._client.request(method, url, headers=headers)
        for name, value in response.headers.items():
            if name.lower().startswith(WEIGHT_HEADER_PREFIXES) and value.isdigit():
                self.used_weight[name.lower()] = int(value)
        try:
            data = response.json()
        except ValueError:
            data = None
        if response.is_error:
            if isinstance(data, dict):
                raise BinanceAPIError(response.status_code, data.get("code"), str(data.get("msg")))
            raise BinanceAPIError(response.status_code, None, response.text[:200])
        return data


def _encode(params: Mapping[str, Any]) -> str:
    return urlencode([(key, _format(value)) for key, value in params.items() if value is not None])


def _format(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        return format(value, "f")  # never exponent notation such as 1E+1
    return str(value)
