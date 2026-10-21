"""Request signing for Binance signed endpoints (HMAC-SHA256 and Ed25519)."""

import base64
import hashlib
import hmac
from pathlib import Path
from typing import Protocol

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


class Signer(Protocol):
    def sign(self, payload: str) -> str:
        """Return the signature for the exact query string that will be sent."""
        ...


class HmacSigner:
    def __init__(self, secret: str) -> None:
        self._secret = secret.encode()

    def sign(self, payload: str) -> str:
        return hmac.new(self._secret, payload.encode(), hashlib.sha256).hexdigest()


class Ed25519Signer:
    def __init__(self, private_key: Ed25519PrivateKey) -> None:
        self._key = private_key

    @classmethod
    def from_pem_file(cls, path: Path, passphrase: str | None = None) -> "Ed25519Signer":
        key = serialization.load_pem_private_key(
            path.read_bytes(), password=passphrase.encode() if passphrase else None
        )
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError(f"{path} does not contain an Ed25519 private key")
        return cls(key)

    def sign(self, payload: str) -> str:
        return base64.b64encode(self._key.sign(payload.encode())).decode()


def generate_ed25519_pem(passphrase: str | None = None) -> tuple[bytes, bytes]:
    """Return (private_pem, public_pem) for a new Ed25519 key pair."""
    key = Ed25519PrivateKey.generate()
    encryption = (
        serialization.BestAvailableEncryption(passphrase.encode())
        if passphrase
        else serialization.NoEncryption()
    )
    private_pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, encryption
    )
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return private_pem, public_pem
