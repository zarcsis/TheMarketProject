import base64

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from marketbot.exchange.signing import Ed25519Signer, HmacSigner, generate_ed25519_pem

# Example from the Binance API documentation on SIGNED endpoint security.
DOC_SECRET = "NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP1e3UZjInClVN65XAbvqqM6A7H5fATj0j"
DOC_QUERY = (
    "symbol=LTCBTC&side=BUY&type=LIMIT&timeInForce=GTC&quantity=1&price=0.1"
    "&recvWindow=5000&timestamp=1499827319559"
)
DOC_SIGNATURE = "c8db56825ae71d6d79447849e617115f4a920fa2acdcab2b053c4b2838bd6b71"


def test_hmac_matches_binance_documentation_example() -> None:
    assert HmacSigner(DOC_SECRET).sign(DOC_QUERY) == DOC_SIGNATURE


@pytest.mark.parametrize("passphrase", [None, "correct horse"])
def test_ed25519_signature_verifies_with_public_key(tmp_path, passphrase) -> None:
    private_pem, public_pem = generate_ed25519_pem(passphrase)
    key_path = tmp_path / "private.pem"
    key_path.write_bytes(private_pem)

    signature = Ed25519Signer.from_pem_file(key_path, passphrase).sign(DOC_QUERY)

    public_key = serialization.load_pem_public_key(public_pem)
    public_key.verify(base64.b64decode(signature), DOC_QUERY.encode())


def test_ed25519_signer_rejects_other_key_types(tmp_path) -> None:
    pem = ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    key_path = tmp_path / "ec.pem"
    key_path.write_bytes(pem)

    with pytest.raises(ValueError, match="Ed25519"):
        Ed25519Signer.from_pem_file(key_path)
