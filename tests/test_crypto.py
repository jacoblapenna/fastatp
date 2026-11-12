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


# --- the signer seam ---------------------------------------------------------
#
# A Signer returns whatever its device gives back; fastatp normalizes to
# compact low-S and verifies before anything is committed. The fakes below
# stand in for a TPM (raw r||s), an HSM (DER), a part that never normalizes
# (high-S), and a misbehaving device (wrong message, wrong key, garbage).


def _der(key, message):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec

    return key.sign(message, ec.ECDSA(hashes.SHA256()))


def _raw(der):
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    r, s = decode_dss_signature(der)
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


class _FakeSigner:
    """A hardware-shaped signer: holds a key the test can see, returns what
    ``encode`` says the device would."""

    def __init__(self, key, encode):
        self._key = key
        self._encode = encode

    @property
    def public_key(self):
        return self._key.public_key()

    async def sign(self, message):
        return self._encode(self._key, message)


def _high_s(key, message):
    sig = _raw(_der(key, message))
    r = int.from_bytes(sig[:32], "big")
    s = int.from_bytes(sig[32:], "big")
    n = crypto.curve_order(key.curve)
    if s <= n // 2:
        s = n - s
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


@pytest.mark.parametrize("curve", ["k256", "p256"])
def test_normalize_signature_accepts_der_and_high_s_fixtures(interop_dir, curve):
    """The interop fixtures that verify() must reject (DER, high-S) are exactly
    what a device hands back; normalized, they verify."""
    algorithm = {"k256": "ES256K", "p256": "ES256"}[curve]
    seen = 0
    for fx in _load(interop_dir, "signature-fixtures.json"):
        if fx["algorithm"] != algorithm or fx["validSignature"]:
            continue
        pubkey = crypto.decode_did_key(fx["publicKeyDid"])
        message = _b64(fx["messageBase64"])
        normalized = crypto.normalize_signature(_b64(fx["signatureBase64"]), pubkey.curve)
        assert len(normalized) == 64
        assert crypto.verify_signature(pubkey, message, normalized), fx["comment"]
        seen += 1
    assert seen == 2  # one DER, one high-S per curve


@pytest.mark.parametrize("curve", ["k256", "p256"])
def test_normalize_signature_gives_one_form_for_every_encoding(curve):
    key = crypto.generate_keypair(curve)
    msg = b"one signature, three encodings"
    der = _der(key, msg)
    expected = crypto.normalize_signature(der, key.curve)
    assert crypto.normalize_signature(_raw(der), key.curve) == expected
    assert crypto.normalize_signature(_high_s_of(_raw(der), key.curve), key.curve) == expected
    assert crypto.normalize_signature(expected, key.curve) == expected  # idempotent
    assert crypto.verify_signature(key.public_key(), msg, expected)


def _high_s_of(raw, curve):
    r = int.from_bytes(raw[:32], "big")
    s = int.from_bytes(raw[32:], "big")
    n = crypto.curve_order(curve)
    return r.to_bytes(32, "big") + (n - s if s <= n // 2 else s).to_bytes(32, "big")


def test_normalize_signature_refuses_what_is_not_a_signature():
    with pytest.raises(crypto.CryptoError, match="neither raw"):
        crypto.normalize_signature(b"\x00" * 70, "p256")
    with pytest.raises(crypto.CryptoError, match="out of range"):
        crypto.normalize_signature(b"\x00" * 64, "p256")  # r = s = 0
    n = crypto.curve_order("k256")
    with pytest.raises(crypto.CryptoError, match="out of range"):
        crypto.normalize_signature((1).to_bytes(32, "big") + n.to_bytes(32, "big"), "k256")


def test_sign_message_is_the_software_signer_path():
    """sign_message and SoftwareSigner share normalize_signature: both give
    64-byte low-S that verify() accepts."""
    key = crypto.generate_keypair("p256")
    sig = crypto.sign_message(key, b"m")
    n = crypto.curve_order("p256")
    assert int.from_bytes(sig[32:], "big") <= n // 2
    assert crypto.verify_signature(key.public_key(), b"m", sig)


@pytest.mark.parametrize("curve", ["k256", "p256"])
async def test_software_signer_signs_and_is_a_signer(curve):
    key = crypto.generate_keypair(curve)
    signer = crypto.SoftwareSigner(key)
    assert isinstance(signer, crypto.Signer)
    assert signer.public_key == key.public_key()
    sig = await crypto.sign_with(signer, b"hello")
    assert len(sig) == 64
    assert crypto.verify_signature(key.public_key(), b"hello", sig)
    assert crypto.encode_did_key(key.public_key()) in repr(signer)


def test_as_signer_wraps_a_key_and_passes_a_signer_through():
    key = crypto.generate_keypair("k256")
    wrapped = crypto.as_signer(key)
    assert isinstance(wrapped, crypto.SoftwareSigner)
    fake = _FakeSigner(key, _der)
    assert crypto.as_signer(fake) is fake


@pytest.mark.parametrize("curve", ["k256", "p256"])
@pytest.mark.parametrize(
    "encode",
    [
        pytest.param(_der, id="der"),
        pytest.param(lambda key, msg: _raw(_der(key, msg)), id="raw"),
        pytest.param(_high_s, id="high-s"),
    ],
)
async def test_sign_with_accepts_every_device_encoding(curve, encode):
    key = crypto.generate_keypair(curve)
    signer = _FakeSigner(key, encode)
    sig = await crypto.sign_with(signer, b"commit bytes")
    assert len(sig) == 64
    assert int.from_bytes(sig[32:], "big") <= crypto.curve_order(curve) // 2
    assert crypto.verify_signature(key.public_key(), b"commit bytes", sig)


async def test_sign_with_refuses_a_signature_over_another_message():
    key = crypto.generate_keypair("p256")
    signer = _FakeSigner(key, lambda key, msg: _der(key, b"something else"))
    with pytest.raises(crypto.CryptoError, match="does not verify against its own public key"):
        await crypto.sign_with(signer, b"commit bytes")


async def test_sign_with_refuses_a_signer_that_lies_about_its_key():
    """The device signs with one key and reports another: refused at the
    write, not discovered at a mirror."""
    actual = crypto.generate_keypair("p256")
    claimed = crypto.generate_keypair("p256")

    class _Liar(_FakeSigner):
        @property
        def public_key(self):
            return claimed.public_key()

    with pytest.raises(crypto.CryptoError, match=crypto.encode_did_key(claimed.public_key())):
        await crypto.sign_with(_Liar(actual, _der), b"commit bytes")


async def test_sign_with_refuses_garbage():
    key = crypto.generate_keypair("k256")
    with pytest.raises(crypto.CryptoError, match="neither raw"):
        await crypto.sign_with(_FakeSigner(key, lambda key, msg: b"\xff" * 71), b"m")


def test_software_signer_refuses_an_unsupported_curve():
    from cryptography.hazmat.primitives.asymmetric import ec

    with pytest.raises(crypto.CryptoError, match="unsupported curve"):
        crypto.SoftwareSigner(ec.generate_private_key(ec.SECP384R1()))


def test_signer_is_exported():
    import fastatp

    assert fastatp.Signer is crypto.Signer
    assert fastatp.SoftwareSigner is crypto.SoftwareSigner
