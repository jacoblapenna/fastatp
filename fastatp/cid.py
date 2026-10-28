"""CIDv1: the content-identifier format used throughout atproto.

Only the "blessed" atproto configuration is produced: CIDv1, SHA-256
multihash, codec ``dag-cbor`` (0x71) for nodes or ``raw`` (0x55) for blobs,
base32 string encoding. Decoding accepts any well-formed binary CIDv1.
"""

from __future__ import annotations

import hashlib

from fastatp.lex.types import multibase_decode, read_varint

DAG_CBOR_CODEC = 0x71
RAW_CODEC = 0x55
SHA2_256 = 0x12

_BASE32_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"


def _base32_encode(data: bytes) -> str:
    """RFC 4648 base32, lowercase, no padding."""
    out = []
    buffer = 0
    bits = 0
    for byte in data:
        buffer = (buffer << 8) | byte
        bits += 8
        while bits >= 5:
            bits -= 5
            out.append(_BASE32_ALPHABET[(buffer >> bits) & 0x1F])
    if bits:
        out.append(_BASE32_ALPHABET[(buffer << (5 - bits)) & 0x1F])
    return "".join(out)


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


class CID:
    """An immutable CIDv1."""

    __slots__ = ("_raw",)
    _raw: bytes

    def __init__(self, raw: bytes) -> None:
        version, off = read_varint(raw, 0)
        if version != 1:
            raise ValueError(f"only CIDv1 is supported (got version {version})")
        _codec, off = read_varint(raw, off)
        _hash_code, off = read_varint(raw, off)
        hash_size, off = read_varint(raw, off)
        if len(raw) - off != hash_size:
            raise ValueError("CID multihash length mismatch")
        object.__setattr__(self, "_raw", bytes(raw))

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("CID is immutable")

    # --- constructors -----------------------------------------------------

    @classmethod
    def create(cls, codec: int, data: bytes) -> CID:
        digest = hashlib.sha256(data).digest()
        return cls(_varint(1) + _varint(codec) + _varint(SHA2_256) + _varint(32) + digest)

    @classmethod
    def for_dag_cbor(cls, encoded: bytes) -> CID:
        return cls.create(DAG_CBOR_CODEC, encoded)

    @classmethod
    def for_raw(cls, data: bytes) -> CID:
        return cls.create(RAW_CODEC, data)

    @classmethod
    def decode(cls, value: str | bytes | CID) -> CID:
        if isinstance(value, CID):
            return value
        if isinstance(value, (bytes, bytearray, memoryview)):
            return cls(bytes(value))
        return cls(multibase_decode(value))

    # --- accessors --------------------------------------------------------

    @property
    def version(self) -> int:
        return 1

    @property
    def codec(self) -> int:
        _, off = read_varint(self._raw, 0)
        codec, _ = read_varint(self._raw, off)
        return codec

    @property
    def multihash(self) -> bytes:
        _, off = read_varint(self._raw, 0)
        _, off = read_varint(self._raw, off)
        return self._raw[off:]

    def encode(self) -> str:
        """Base32 multibase string form (``b`` prefix)."""
        return "b" + _base32_encode(self._raw)

    def __bytes__(self) -> bytes:
        return self._raw

    def __eq__(self, other: object) -> bool:
        return isinstance(other, CID) and other._raw == self._raw

    def __hash__(self) -> int:
        return hash((CID, self._raw))

    def __repr__(self) -> str:
        return f"CID({self.encode()!r})"

    def __str__(self) -> str:
        return self.encode()
