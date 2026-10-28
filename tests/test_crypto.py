"""Crypto conformance tests driven by the interop vectors.

- signature-fixtures.json: verify() accepts valid low-S compact signatures and
  rejects high-S and DER-encoded ones, on both curves.
- w3c_didkey_{K256,P256}.json: private key bytes -> did:key encoding.
"""

import base64
import json

import pytest

from fastatp import crypto


def _b64(s: str) -> bytes:
    return base64.b64decode(s + "=" * (-len(s) % 4))


def _load(interop_dir, name):
    return json.loads((interop_dir / "crypto" / name).read_text())


def test_signature_fixtures(interop_dir):
    fixtures = _load(interop_dir, "signature-fixtures.json")
    assert len(fixtures) >= 6
    for fx in fixtures:
        pubkey = crypto.decode_did_key(fx["publicKeyDid"])
        message = _b64(fx["messageBase64"])
        sig = _b64(fx["signatureBase64"])
        assert crypto.verify_signature(pubkey, message, sig) == fx["validSignature"], fx["comment"]


@pytest.mark.parametrize(
    "name,curve", [("w3c_didkey_K256.json", "k256"), ("w3c_didkey_P256.json", "p256")]
)
def test_didkey_vectors(interop_dir, name, curve):
    from fastatp.lex.types import base58btc_decode

    for fx in _load(interop_dir, name):
        if "privateKeyBytesHex" in fx:
            raw = bytes.fromhex(fx["privateKeyBytesHex"])
        else:
            raw = base58btc_decode(fx["privateKeyBytesBase58"])
        priv = crypto.load_private_key_bytes(raw, curve)
        did_key = crypto.encode_did_key(priv.public_key())
        assert did_key == fx["publicDidKey"]


@pytest.mark.parametrize("curve", ["k256", "p256"])
def test_sign_verify_round_trip(curve):
    key = crypto.generate_keypair(curve)
    msg = b"hello world"
    sig = crypto.sign_message(key, msg)
    assert len(sig) == 64
    assert crypto.verify_signature(key.public_key(), msg, sig)
    assert not crypto.verify_signature(key.public_key(), b"tampered", sig)
    # low-S enforcement: the complement (high-S) form must be rejected
    r = int.from_bytes(sig[:32], "big")
    s = int.from_bytes(sig[32:], "big")
    n = crypto.curve_order(key.curve)
    high_s = r.to_bytes(32, "big") + (n - s).to_bytes(32, "big")
    assert not crypto.verify_signature(key.public_key(), msg, high_s)


@pytest.mark.parametrize("curve", ["k256", "p256"])
def test_multikey_round_trip(curve):
    key = crypto.generate_keypair(curve)
    multikey = crypto.encode_multikey(key.public_key())
    assert multikey.startswith("z")
    decoded = crypto.decode_multikey(multikey)
    assert crypto.encode_multikey(decoded) == multikey
