"""Key handling, Multikey/did:key encoding, and low-S ECDSA signing.

Interop-critical rules:

- Curves: secp256k1 ("k256") and NIST P-256 ("p256").
- Signatures: ECDSA over SHA-256, low-S normalized, compact 64-byte R||S.
  Verification rejects high-S and DER-encoded signatures.
- Public keys travel as Multikey / did:key multibase (base58btc, "z" prefix)
  with the per-curve multicodec prefix over the compressed point.

All primitives come from ``cryptography``; nothing is hand-rolled.
"""

from __future__ import annotations

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    decode_dss_signature,
    encode_dss_signature,
)

from fastatp.exceptions import CryptoError
from fastatp.lex.types import base58btc_decode, base58btc_encode

__all__ = [
    "curve_name",
    "curve_order",
    "decode_did_key",
    "decode_multikey",
    "encode_did_key",
    "encode_multikey",
    "generate_keypair",
    "load_private_key_bytes",
    "sign_message",
    "verify_signature",
]

PrivateKey = ec.EllipticCurvePrivateKey
PublicKey = ec.EllipticCurvePublicKey

_CURVES: dict[str, type[ec.EllipticCurve]] = {
    "k256": ec.SECP256K1,
    "p256": ec.SECP256R1,
}

_CURVE_ORDERS = {
    "k256": 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141,
    "p256": 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551,
}

# multicodec prefixes (as unsigned varint bytes) for compressed public keys
_MULTICODEC_PREFIX = {
    "k256": b"\xe7\x01",  # secp256k1-pub (0xe7)
    "p256": b"\x80\x24",  # p256-pub (0x1200)
}


def curve_name(curve: ec.EllipticCurve | str) -> str:
    """Normalize a curve object or name to "k256" / "p256"."""
    if isinstance(curve, str):
        name = curve.lower()
        if name in ("k256", "secp256k1", "es256k"):
            return "k256"
        if name in ("p256", "secp256r1", "prime256v1", "es256"):
            return "p256"
        raise CryptoError(f"unsupported curve {curve!r}")
    if isinstance(curve, ec.SECP256K1):
        return "k256"
    if isinstance(curve, ec.SECP256R1):
        return "p256"
    raise CryptoError(f"unsupported curve {curve.name!r}")


def curve_order(curve: ec.EllipticCurve | str) -> int:
    return _CURVE_ORDERS[curve_name(curve)]


def generate_keypair(curve: str = "k256") -> PrivateKey:
    """Generate a new private key on "k256" (default) or "p256"."""
    return ec.generate_private_key(_CURVES[curve_name(curve)]())


def load_private_key_bytes(data: bytes, curve: str) -> PrivateKey:
    """Load a raw 32-byte private scalar."""
    if len(data) != 32:
        raise CryptoError("private key must be 32 bytes")
    return ec.derive_private_key(int.from_bytes(data, "big"), _CURVES[curve_name(curve)]())


# --- Multikey / did:key -------------------------------------------------------


def encode_multikey(public_key: PublicKey) -> str:
    """Multibase (base58btc) multicodec-prefixed compressed public key."""
    name = curve_name(public_key.curve)
    point = public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.CompressedPoint
    )
    return "z" + base58btc_encode(_MULTICODEC_PREFIX[name] + point)


def decode_multikey(multikey: str) -> PublicKey:
    """Decode a Multikey multibase string to a public key."""
    if not multikey.startswith("z"):
        raise CryptoError("multikey must be base58btc ('z' prefix)")
    try:
        data = base58btc_decode(multikey[1:])
    except ValueError as e:
        raise CryptoError(f"invalid multikey: {e}") from None
    for name, prefix in _MULTICODEC_PREFIX.items():
        if data.startswith(prefix):
            point = data[len(prefix) :]
            try:
                return ec.EllipticCurvePublicKey.from_encoded_point(_CURVES[name](), point)
            except ValueError as e:
                raise CryptoError(f"invalid public key point: {e}") from None
    raise CryptoError("unsupported multicodec prefix in multikey")


def encode_did_key(public_key: PublicKey) -> str:
    return "did:key:" + encode_multikey(public_key)


def decode_did_key(did_key: str) -> PublicKey:
    if not did_key.startswith("did:key:"):
        raise CryptoError("not a did:key")
    return decode_multikey(did_key[len("did:key:") :])


# --- compact low-S ECDSA ------------------------------------------------------


def sign_message(private_key: PrivateKey, message: bytes) -> bytes:
    """ECDSA-SHA256 over message, low-S normalized, compact 64-byte R||S."""
    der = private_key.sign(message, ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    n = curve_order(private_key.curve)
    if s > n // 2:
        s = n - s
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def verify_signature(public_key: PublicKey, message: bytes, signature: bytes) -> bool:
    """Verify a compact 64-byte signature; rejects high-S and non-compact forms."""
    if len(signature) != 64:
        return False
    r = int.from_bytes(signature[:32], "big")
    s = int.from_bytes(signature[32:], "big")
    n = curve_order(public_key.curve)
    if not 0 < r < n or not 0 < s <= n // 2:
        return False
    try:
        public_key.verify(encode_dss_signature(r, s), message, ec.ECDSA(hashes.SHA256()))
        return True
    except InvalidSignature:
        return False
