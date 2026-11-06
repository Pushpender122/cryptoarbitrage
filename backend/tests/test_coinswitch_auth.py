from types import SimpleNamespace

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from app.exchanges.coinswitch import CoinSwitchProvider


def test_coinswitch_signer_matches_hex_ed25519_reference(monkeypatch):
    seed = bytes(range(32))
    private_key = Ed25519PrivateKey.from_private_bytes(seed)
    public_key = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    cfg = SimpleNamespace(
        coinswitch_api_key=public_key.hex(),
        coinswitch_api_secret=seed.hex(),
        coinswitch_live_ready=True,
    )
    provider = CoinSwitchProvider(cfg)
    monkeypatch.setattr("app.exchanges.coinswitch.time.time", lambda: 1_800_000_000.123)
    path = "/v5/market/instruments-info?category=option"
    headers = provider._sign("get", path)
    epoch = "1800000000123"
    message = f"GET{path}{epoch}".encode()
    signature = bytes.fromhex(headers["X-AUTH-SIGNATURE"])
    private_key.public_key().verify(signature, message)
    assert headers["X-AUTH-APIKEY"] == public_key.hex()
    assert headers["X-AUTH-EPOCH"] == epoch
    assert len(headers["X-AUTH-SIGNATURE"]) == 128


def test_coinswitch_signer_rejects_wrong_private_key_length():
    provider = CoinSwitchProvider(
        SimpleNamespace(coinswitch_api_key="public", coinswitch_api_secret="abcd", coinswitch_live_ready=True)
    )
    try:
        provider._sign("GET", "/v5/market/time")
    except Exception as exc:
        assert "32-byte hex private key" in str(exc)
    else:
        raise AssertionError("invalid private key should fail")
