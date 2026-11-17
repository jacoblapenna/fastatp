"""Key handling, Multikey/did:key encoding, and low-S ECDSA signing.

Interop-critical rules:

- Curves: secp256k1 ("k256") and NIST P-256 ("p256").
- Signatures: ECDSA over SHA-256, low-S normalized, compact 64-byte R||S.
  Verification rejects high-S and DER-encoded signatures.
- Public keys travel as Multikey / did:key multibase (base58btc, "z" prefix)
  with the per-curve multicodec prefix over the compressed point.

All primitives come from ``cryptography``; nothing is hand-rolled.

The signer seam: where the pen lives is a deployment property, not an
architecture fork. A :class:`Signer` signs through a handle, so a key locked
in a TPM, an HSM or a secure element can sign commits without its bytes ever
entering process memory; :class:`SoftwareSigner` holds the key in memory for
native peers and tests. The seam returns whatever the device gives back, and
fastatp owns the wire form: :func:`normalize_signature` turns DER or raw
``r||s`` into compact low-S in one place, and :func:`sign_with` verifies every
signature against the signer's public key before a caller commits it. This is
the Python half of the Rust ``Signer`` trait in ``fastatp-core``.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

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
    "Signer",
    "SoftwareSigner",
    "as_signer",
    "curve_name",
    "curve_order",
    "decode_did_key",
    "decode_multikey",
    "encode_did_key",
    "encode_multikey",
    "generate_keypair",
    "load_private_key_bytes",
    "normalize_signature",
    "sign_message",
    "sign_with",
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
    return normalize_signature(der, private_key.curve)


def normalize_signature(signature: bytes, curve: ec.EllipticCurve | str) -> bytes:
    """Turn a signature as a device gives it into compact low-S 64-byte R||S.

    Accepts DER (what ``cryptography``, PKCS#11 and most HSMs return) or raw
    ``r||s`` (what a TPM returns), low-S or not. Length decides the encoding:
    exactly 64 bytes is raw, anything else is parsed as DER. A raw signature
    whose ``r`` happens to start with the DER tag byte is still raw, and a
    64-byte DER signature cannot occur for a 256-bit curve in practice.

    This is the one place fastatp applies low-S normalization: hardware
    signers don't, and :func:`verify_signature` rejects high-S, so every
    signer goes through here.
    """
    if len(signature) == 64:
        r = int.from_bytes(signature[:32], "big")
        s = int.from_bytes(signature[32:], "big")
    else:
        try:
            r, s = decode_dss_signature(signature)
        except ValueError as e:
            raise CryptoError(
                f"signature is neither raw 64-byte r||s nor DER ({len(signature)} bytes): {e}"
            ) from None
    n = curve_order(curve)
    if not 0 < r < n or not 0 < s < n:
        raise CryptoError("signature r or s is out of range for the curve")
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


# --- the signer seam ---------------------------------------------------------


@runtime_checkable
class Signer(Protocol):
    """Something that signs with a key it may not be able to hand over.

    Implement this to put a peer's pen in a TPM, an HSM, a secure element or
    a remote signing service: ``sign`` is awaited so a device that takes tens
    of milliseconds never stalls the peer's event loop. Return the signature
    exactly as the device gives it, DER or raw ``r||s``, low-S or not; fastatp
    normalizes and verifies it (:func:`sign_with`) before anything is
    committed, so a misbehaving device fails at the write, not at a mirror.

    Curves are k256 and p256 only. TPM 2.0 parts generally offer only p256.
    """

    @property
    def public_key(self) -> PublicKey:
        """The verifying half: what a DID document or ``did:key`` carries."""
        ...

    async def sign(self, message: bytes) -> bytes:
        """ECDSA-SHA256 over ``message``, in whatever encoding the device returns."""
        ...


class SoftwareSigner:
    """A :class:`Signer` holding a ``cryptography`` private key in memory.

    The in-memory pen for native peers and tests; every API that takes a
    ``PrivateKey`` wraps it in one of these (:func:`as_signer`).
    """

    def __init__(self, private_key: PrivateKey) -> None:
        curve_name(private_key.curve)  # refuse unsupported curves at construction
        self._private_key = private_key

    @property
    def public_key(self) -> PublicKey:
        return self._private_key.public_key()

    async def sign(self, message: bytes) -> bytes:
        return self._private_key.sign(message, ec.ECDSA(hashes.SHA256()))

    def __repr__(self) -> str:
        return f"SoftwareSigner({encode_did_key(self.public_key)})"


def as_signer(key: PrivateKey | Signer) -> Signer:
    """Accept a private key or a signer; return a signer."""
    if isinstance(key, ec.EllipticCurvePrivateKey):
        return SoftwareSigner(key)
    return key


async def sign_with(signer: Signer, message: bytes) -> bytes:
    """Sign through the seam: normalize to compact low-S and verify before use.

    The one checked path for every commit signature. Raises
    :class:`CryptoError` when the device returns something that is not a
    signature over ``message`` by ``signer.public_key``.
    """
    public_key = signer.public_key
    raw = await signer.sign(message)
    signature = normalize_signature(raw, public_key.curve)
    if not verify_signature(public_key, message, signature):
        raise CryptoError(
            f"signer {encode_did_key(public_key)} returned a signature that does not"
            " verify against its own public key; nothing was committed"
        )
    return signature
