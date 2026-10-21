"""Spot (including SAPI) and USD-M futures hosts sharing one set of credentials."""

import httpx

from marketbot.config import Settings
from marketbot.exchange.rest import BinanceREST
from marketbot.exchange.signing import Ed25519Signer, HmacSigner, Signer


class Binance:
    def __init__(self, spot: BinanceREST, futures: BinanceREST) -> None:
        self.spot = spot
        self.futures = futures

    @classmethod
    def from_settings(
        cls, settings: Settings, transport: httpx.AsyncBaseTransport | None = None
    ) -> "Binance":
        api_key = settings.binance_api_key.get_secret_value() if settings.binance_api_key else None
        signer = make_signer(settings)

        def host(base_url: str, time_path: str) -> BinanceREST:
            return BinanceREST(
                base_url,
                time_path,
                api_key=api_key,
                signer=signer,
                recv_window_ms=settings.recv_window_ms,
                transport=transport,
            )

        return cls(
            host(settings.spot_base_url, "/api/v3/time"),
            host(settings.futures_base_url, "/fapi/v1/time"),
        )

    async def __aenter__(self) -> "Binance":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self.spot.close()
        await self.futures.close()


def make_signer(settings: Settings) -> Signer | None:
    """Prefer an Ed25519 key; fall back to an HMAC secret; None means public access only."""
    if settings.binance_private_key_path is not None:
        passphrase = settings.binance_private_key_passphrase
        return Ed25519Signer.from_pem_file(
            settings.binance_private_key_path,
            passphrase.get_secret_value() if passphrase else None,
        )
    if settings.binance_api_secret is not None:
        return HmacSigner(settings.binance_api_secret.get_secret_value())
    return None
