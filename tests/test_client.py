import pytest
from pydantic import SecretStr

from marketbot.config import Settings
from marketbot.exchange.client import Binance
from marketbot.exchange.signing import generate_ed25519_pem


def settings_with_key(tmp_path, api_key: str) -> Settings:
    private_pem, _ = generate_ed25519_pem()
    key_path = tmp_path / "private.pem"
    key_path.write_bytes(private_pem)
    return Settings(
        _env_file=None, binance_api_key=SecretStr(api_key), binance_private_key_path=key_path
    )


def test_private_key_pasted_as_api_key_is_refused(tmp_path) -> None:
    settings = settings_with_key(tmp_path, "placeholder")
    body = settings.binance_private_key_path.read_text().splitlines()[1]
    settings.binance_api_key = SecretStr(body)

    with pytest.raises(ValueError, match="private key"):
        Binance.from_settings(settings)


@pytest.mark.anyio
async def test_regular_api_key_is_accepted(tmp_path) -> None:
    settings = settings_with_key(tmp_path, "A" * 64)
    async with Binance.from_settings(settings) as binance:
        assert binance.spot is not None
