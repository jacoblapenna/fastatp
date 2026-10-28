"""Canonical DAG-CBOR codec.

Encodes the atproto data model: maps with string keys sorted length-first
then bytewise, minimal-width integer heads, tag 42 for CID links, no floats,
no indefinite-length items. The decoder is strict: it rejects non-canonical
input (wide integers, unsorted or duplicate map keys, unknown tags, floats),
which matters when verifying repo bytes from other implementations.
"""

from __future__ import annotations

from typing import Any

from fastatp.cid import CID
from fastatp.exceptions import DataModelError
from fastatp.lex.types import Bytes, CidLink

_MAX_UINT64 = 2**64 - 1
_HEAD_MINIMUMS = {24: 24, 25: 0x100, 26: 0x10000, 27: 0x100000000}


def _head(major: int, value: int) -> bytes:
    if value < 24:
        return bytes([(major << 5) | value])
    if value < 0x100:
        return bytes([(major << 5) | 24, value])
    if value < 0x10000:
        return bytes([(major << 5) | 25]) + value.to_bytes(2, "big")
    if value < 0x100000000:
        return bytes([(major << 5) | 26]) + value.to_bytes(4, "big")
    if value <= _MAX_UINT64:
        return bytes([(major << 5) | 27]) + value.to_bytes(8, "big")
    raise DataModelError(f"integer out of CBOR range: {value}")


def _sort_key(item: tuple[bytes, Any]) -> tuple[int, bytes]:
    return (len(item[0]), item[0])


def _encode_cid(cid: CID, out: bytearray) -> None:
    payload = b"\x00" + bytes(cid)  # identity multibase prefix inside tag 42
    out += b"\xd8\x2a"  # tag(42)
    out += _head(2, len(payload))
    out += payload


def _encode(obj: Any, out: bytearray) -> None:
    if obj is None:
        out.append(0xF6)
    elif obj is True:
        out.append(0xF5)
    elif obj is False:
        out.append(0xF4)
    elif isinstance(obj, int):
        if obj >= 0:
            out += _head(0, obj)
        else:
            out += _head(1, -1 - obj)
    elif isinstance(obj, float):
        raise DataModelError("floats are not allowed in the atproto data model")
    elif isinstance(obj, str):
        raw = obj.encode("utf-8")
        out += _head(3, len(raw))
        out += raw
    elif isinstance(obj, (bytes, bytearray, memoryview)):
        raw = bytes(obj)
        out += _head(2, len(raw))
        out += raw
    elif isinstance(obj, Bytes):
        _encode(obj.data, out)
    elif isinstance(obj, CID):
        _encode_cid(obj, out)
    elif isinstance(obj, CidLink):
        _encode_cid(CID.decode(obj.ref), out)
    elif isinstance(obj, dict):
        converted = _convert_dollar_form(obj)
        if converted is not None:
            _encode(converted, out)
            return
        items: list[tuple[bytes, Any]] = []
        for key, value in obj.items():
            if not isinstance(key, str):
                raise DataModelError(f"map keys must be strings, got {type(key).__name__}")
            items.append((key.encode("utf-8"), value))
        items.sort(key=_sort_key)
        out += _head(5, len(items))
        for raw_key, value in items:
            out += _head(3, len(raw_key))
            out += raw_key
            _encode(value, out)
    elif isinstance(obj, (list, tuple)):
        out += _head(4, len(obj))
        for item in obj:
            _encode(item, out)
    else:
        raise DataModelError(f"{type(obj).__name__} has no DAG-CBOR encoding")


def _convert_dollar_form(obj: dict[str, Any]) -> Any:
    """Recognize the JSON data-model forms for links and bytes."""
    if set(obj.keys()) == {"$link"} and isinstance(obj["$link"], str):
        try:
            return CID.decode(obj["$link"])
        except ValueError as e:
            raise DataModelError(f"invalid $link CID: {e}") from None
    if set(obj.keys()) == {"$bytes"} and isinstance(obj["$bytes"], str):
        return Bytes._validate(obj).data
    return None


def encode(obj: Any) -> bytes:
    """Encode a data-model tree to canonical DAG-CBOR bytes."""
    out = bytearray()
    _encode(obj, out)
    return bytes(out)


# --- strict decoder -------------------------------------------------------------


class _Decoder:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def _byte(self) -> int:
        if self.pos >= len(self.data):
            raise DataModelError("truncated DAG-CBOR")
        byte = self.data[self.pos]
        self.pos += 1
        return byte

    def _read(self, n: int) -> bytes:
        if self.pos + n > len(self.data):
            raise DataModelError("truncated DAG-CBOR")
        chunk = self.data[self.pos : self.pos + n]
        self.pos += n
        return chunk

    def _head(self) -> tuple[int, int]:
        initial = self._byte()
        major = initial >> 5
        additional = initial & 0x1F
        if additional < 24:
            return major, additional
        if additional == 31:
            raise DataModelError("indefinite-length items are not allowed")
        if additional > 27:
            raise DataModelError(f"invalid additional info {additional}")
        if major == 7:
            # 0xf8 (extended simple) would allow non-canonical encodings of
            # false/true/null; 0xf9-0xfb are floats — none are allowed
            if additional == 24:
                raise DataModelError("extended simple values are not allowed")
            raise DataModelError("floats are not allowed in the atproto data model")
        width = 1 << (additional - 24)
        value = int.from_bytes(self._read(width), "big")
        if value < _HEAD_MINIMUMS[additional]:
            raise DataModelError("non-minimal integer head")
        return major, value

    def decode_item(self) -> Any:
        major, value = self._head()
        if major == 0:
            return value
        if major == 1:
            return -1 - value
        if major == 2:
            return self._read(value)
        if major == 3:
            raw = self._read(value)
            try:
                return raw.decode("utf-8")
            except UnicodeDecodeError as e:
                raise DataModelError(f"invalid UTF-8 in string: {e}") from None
        if major == 4:
            return [self.decode_item() for _ in range(value)]
        if major == 5:
            return self._decode_map(value)
        if major == 6:
            return self._decode_tag(value)
        return self._decode_simple(value)

    def _decode_map(self, count: int) -> dict[str, Any]:
        result: dict[str, Any] = {}
        prev_key: bytes | None = None
        for _ in range(count):
            major, length = self._head()
            if major != 3:
                raise DataModelError("map keys must be strings")
            raw_key = self._read(length)
            if prev_key is not None:
                if (len(raw_key), raw_key) == (len(prev_key), prev_key):
                    raise DataModelError("duplicate map key")
                if (len(raw_key), raw_key) < (len(prev_key), prev_key):
                    raise DataModelError("map keys are not in canonical order")
            prev_key = raw_key
            try:
                key = raw_key.decode("utf-8")
            except UnicodeDecodeError as e:
                raise DataModelError(f"invalid UTF-8 in map key: {e}") from None
            result[key] = self.decode_item()
        return result

    def _decode_tag(self, tag: int) -> CID:
        if tag != 42:
            raise DataModelError(f"tag {tag} is not allowed (only 42 for CID links)")
        major, length = self._head()
        if major != 2:
            raise DataModelError("tag 42 content must be a byte string")
        payload = self._read(length)
        if not payload.startswith(b"\x00"):
            raise DataModelError("tag 42 CID must use the identity multibase prefix")
        try:
            return CID.decode(payload[1:])
        except ValueError as e:
            raise DataModelError(f"invalid CID in tag 42: {e}") from None

    def _decode_simple(self, value: int) -> Any:
        # only reachable with additional info < 24 (canonical one-byte simples)
        if value == 20:
            return False
        if value == 21:
            return True
        if value == 22:
            return None
        raise DataModelError(f"simple value {value} is not allowed")


def decode(data: bytes) -> Any:
    """Strictly decode canonical DAG-CBOR bytes into a data-model tree.

    Byte strings come back as ``bytes`` and links as :class:`CID` — use
    ``fastatp.datamodel.to_json`` for the ``$bytes`` / ``$link`` JSON forms.
    """
    decoder = _Decoder(data)
    value = decoder.decode_item()
    if decoder.pos != len(data):
        raise DataModelError("trailing bytes after DAG-CBOR item")
    return value


def decode_first(data: bytes) -> tuple[Any, bytes]:
    """Decode one DAG-CBOR item, returning it and the remaining bytes."""
    decoder = _Decoder(data)
    value = decoder.decode_item()
    return value, data[decoder.pos :]


__all__ = ["decode", "decode_first", "encode"]
