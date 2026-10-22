"""Runtime settings loaded from the environment and the local .env file."""

from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

SPOT_BASE_URL = "https://api.binance.com"
FUTURES_BASE_URL = "https://fapi.binance.com"
DEMO_SPOT_BASE_URL = "https://demo-api.binance.com"
DEMO_FUTURES_BASE_URL = "https://demo-fapi.binance.com"

Env = Literal["live", "demo"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")

    binance_api_key: SecretStr | None = None
    # Ed25519 (recommended): path to the private key PEM file.
    binance_private_key_path: Path | None = None
    binance_private_key_passphrase: SecretStr | None = None
    # HMAC: secret key. Used only when no Ed25519 key is configured.
    binance_api_secret: SecretStr | None = None

    # Binance Demo Trading keys (a separate key from the live one).
    binance_demo_api_key: SecretStr | None = None
    binance_demo_private_key_path: Path | None = None
    binance_demo_api_secret: SecretStr | None = None
    # The demo account holds far more than the live one; the bot sees this much equity there,
    # so position sizes match live trading.
    demo_equity: float = 125.0

    # The Telegram remote (`mb remote`): bot token from @BotFather and the owner's chat id.
    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: int | None = None
    # Optional external monitor (e.g. a healthchecks.io ping URL) the remote pings every minute.
    healthcheck_url: str | None = None

    spot_base_url: str = SPOT_BASE_URL
    futures_base_url: str = FUTURES_BASE_URL
    recv_window_ms: int = 5000

    @property
    def has_credentials(self) -> bool:
        return self.binance_api_key is not None and (
            self.binance_private_key_path is not None or self.binance_api_secret is not None
        )

    def for_env(self, env: Env) -> "Settings":
        """Settings whose credentials and hosts point at the given environment."""
        if env == "live":
            return self
        return self.model_copy(
            update={
                "binance_api_key": self.binance_demo_api_key,
                "binance_private_key_path": self.binance_demo_private_key_path,
                "binance_private_key_passphrase": None,
                "binance_api_secret": self.binance_demo_api_secret,
                "spot_base_url": DEMO_SPOT_BASE_URL,
                "futures_base_url": DEMO_FUTURES_BASE_URL,
            }
        )


def state_dir(env: Env) -> Path:
    return Path("state") / env


def log_dir(env: Env) -> Path:
    return Path("logs") / env
