"""Conversion and validation between JSON form and the atproto data model.

The data model's compound types have distinct JSON and CBOR representations:
``{"$link": <cid-str>}`` <-> tag 42, ``{"$bytes": <base64>}`` <-> byte string.
``from_json`` validates a JSON tree (the data-model-valid/-invalid vectors are
the oracle) and produces an object tree (CID / bytes); ``to_json`` goes back.
"""

from __future__ import annotations

import base64
import binascii
from typing import Any

from fastatp.cid import CID
from fastatp.exceptions import DataModelError

__all__ = ["blob_refs", "from_json", "to_json"]


def _bytes_from_json(value: Any) -> bytes:
    if not isinstance(value, str):
        raise DataModelError("$bytes value must be a base64 string")
    try:
        return base64.b64decode(value + "=" * (-len(value) % 4), validate=True)
    except (binascii.Error, ValueError) as e:
        raise DataModelError(f"invalid base64 in $bytes: {e}") from None


def _link_from_json(value: Any) -> CID:
    if not isinstance(value, str):
        raise DataModelError("$link value must be a CID string")
    try:
        return CID.decode(value)
    except ValueError as e:
        raise DataModelError(f"invalid CID in $link: {e}") from None


def _validate_blob(obj: dict[str, Any]) -> None:
    for key in ("ref", "mimeType", "size"):
        if key not in obj:
            raise DataModelError(f"blob is missing required field {key!r}")
    if not isinstance(obj["mimeType"], str):
        raise DataModelError("blob mimeType must be a string")
    if not isinstance(obj["size"], int) or isinstance(obj["size"], bool):
        raise DataModelError("blob size must be an integer")


def _from_json(obj: Any, depth: int) -> Any:
    if depth > 128:
        raise DataModelError("data is nested too deeply")
    if obj is None or isinstance(obj, (bool, str)):
        return obj
    if isinstance(obj, int):
        return obj
    if isinstance(obj, float):
        if obj.is_integer():
            return int(obj)
        raise DataModelError(f"float {obj} is not allowed (integers only)")
    if isinstance(obj, list):
        return [_from_json(item, depth + 1) for item in obj]
    if isinstance(obj, dict):
        keys = set(obj.keys())
        if "$bytes" in keys:
            if keys != {"$bytes"}:
                raise DataModelError("$bytes object must have exactly one key")
            return _bytes_from_json(obj["$bytes"])
        if "$link" in keys:
            if keys != {"$link"}:
                raise DataModelError("$link object must have exactly one key")
            return _link_from_json(obj["$link"])
        if "$type" in keys:
            type_value = obj["$type"]
            if not isinstance(type_value, str) or not type_value:
                raise DataModelError("$type must be a non-empty string")
            if type_value == "blob":
                _validate_blob(obj)
        return {key: _from_json(value, depth + 1) for key, value in obj.items()}
    raise DataModelError(f"{type(obj).__name__} is not part of the data model")


def from_json(obj: Any) -> Any:
    """Validate a JSON tree against the data model; return the object tree.

    Top level must be an object (records and nodes are maps). Integer-valued
    floats (JSON has one number type) coerce to int; true floats are
    rejected, as are malformed ``$type`` / ``$bytes`` / ``$link`` / blob
    shapes.
    """
    if not isinstance(obj, dict):
        raise DataModelError("top level of a data-model document must be an object")
    return _from_json(obj, 0)


def _to_json(obj: Any, depth: int) -> Any:
    if depth > 128:
        raise DataModelError("data is nested too deeply")
    if isinstance(obj, CID):
        return {"$link": obj.encode()}
    if isinstance(obj, (bytes, bytearray, memoryview)):
        return {"$bytes": base64.b64encode(bytes(obj)).decode("ascii").rstrip("=")}
    if isinstance(obj, list):
        return [_to_json(item, depth + 1) for item in obj]
    if isinstance(obj, dict):
        return {key: _to_json(value, depth + 1) for key, value in obj.items()}
    return obj


def to_json(obj: Any) -> Any:
    """Convert an object tree (CID / bytes) to its JSON data-model form."""
    return _to_json(obj, 0)


def _walk_blob_refs(obj: Any, out: set[CID]) -> None:
    if isinstance(obj, list):
        for item in obj:
            _walk_blob_refs(item, out)
        return
    if isinstance(obj, dict):
        ref = obj.get("ref")
        if obj.get("$type") == "blob" and isinstance(ref, CID):
            out.add(ref)
        for value in obj.values():
            _walk_blob_refs(value, out)


def blob_refs(obj: Any) -> set[CID]:
    """CIDs referenced by ``$type: blob`` objects anywhere in an object tree.

    Operates on the decoded (object) form, where ``ref`` is a :class:`CID`
    instance — i.e. the output of ``dagcbor.decode`` or ``from_json``.
    """
    out: set[CID] = set()
    _walk_blob_refs(obj, out)
    return out
