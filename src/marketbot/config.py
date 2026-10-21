"""Runtime settings loaded from the environment and the local .env file."""

from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

SPOT_BASE_URL = "https://api.binance.com"
FUTURES_BASE_URL = "https://fapi.binance.com"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")

    binance_api_key: SecretStr | None = None
    # Ed25519 (recommended): path to the private key PEM file.
    binance_private_key_path: Path | None = None
    binance_private_key_passphrase: SecretStr | None = None
    # HMAC: secret key. Used only when no Ed25519 key is configured.
    binance_api_secret: SecretStr | None = None

    spot_base_url: str = SPOT_BASE_URL
    futures_base_url: str = FUTURES_BASE_URL
    recv_window_ms: int = 5000

    @property
    def has_credentials(self) -> bool:
        return self.binance_api_key is not None and (
            self.binance_private_key_path is not None or self.binance_api_secret is not None
        )
