"""Lexicon base class, the process-global NSID registry, and record parsing.

A ``Lexicon`` subclass is a Pydantic model plus an NSID. The NSID becomes the
``$type`` field on the wire; ``parse_record()`` (used by ``Peer.verify``)
dispatches on ``$type`` back to the right class.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar, TypeVar

from pydantic import BaseModel, ConfigDict, model_serializer, model_validator

from fastatp.exceptions import LexiconValidationError, UnknownLexicon
from fastatp.lex.types import validate_nsid

_REGISTRY: dict[str, type[Lexicon]] = {}

L = TypeVar("L", bound="Lexicon")


def registered_lexicons() -> dict[str, type[Lexicon]]:
    """Snapshot of the process-global NSID -> class registry."""
    return dict(_REGISTRY)


def lookup_lexicon(nsid: str) -> type[Lexicon] | None:
    return _REGISTRY.get(nsid)


class Lexicon(BaseModel):
    """Base class for user-defined, atproto-conforming record types.

    Subclasses declare their NSID as a bare class attribute::

        class PressureReading(Lexicon):
            nsid = "com.example.telemetry.pressureReading"
            pressure_psi: int

    Subclasses register themselves globally by NSID at definition time; the
    last definition for an NSID wins (re-definition is allowed to support
    notebooks and hot reload).
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    nsid: ClassVar[str] = ""
    record_key: ClassVar[str] = "tid"  # lexicon record "key" clause

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        if cls.__dict__.get("nsid"):
            try:
                validate_nsid(cls.nsid)
            except ValueError as e:
                raise LexiconValidationError(f"{cls.__name__}: invalid nsid: {e}") from None
            _REGISTRY[cls.nsid] = cls

    @model_validator(mode="before")
    @classmethod
    def _check_type_field(cls, data: Any) -> Any:
        if isinstance(data, dict) and "$type" in data:
            found = data["$type"]
            if found != cls.nsid:
                raise ValueError(f"$type mismatch: expected {cls.nsid!r}, got {found!r}")
            data = {k: v for k, v in data.items() if k != "$type"}
        return data

    @model_serializer(mode="wrap")
    def _serialize_with_type(self, handler: Any) -> dict[str, Any]:
        data = handler(self)
        return {"$type": type(self).nsid, **data}

    # --- wire helpers ---------------------------------------------------

    def to_json_dict(self) -> dict[str, Any]:
        """JSON-mode dict including ``$type``; omitted optionals are dropped."""
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)

    def to_json(self) -> str:
        return json.dumps(self.to_json_dict(), separators=(",", ":"), ensure_ascii=False)

    @classmethod
    def from_json_dict(cls: type[L], data: dict[str, Any]) -> L:
        try:
            return cls.model_validate(data)
        except LexiconValidationError:
            raise
        except Exception as e:
            raise LexiconValidationError(f"{cls.nsid}: {e}") from e

    def to_dag_cbor(self) -> bytes:
        """Canonical DAG-CBOR bytes of this record (atproto data-model forms)."""
        from fastatp import dagcbor, datamodel

        return dagcbor.encode(datamodel.from_json(self.to_json_dict()))

    def cid(self) -> Any:
        """The record's CID (CIDv1, dag-cbor, sha-256) — identical to atproto."""
        from fastatp.cid import CID

        return CID.for_dag_cbor(self.to_dag_cbor())

    # --- schema bridge (implemented in fastatp.lex.schema) ----------------

    @classmethod
    def to_lexicon_json(cls) -> dict[str, Any]:
        """Emit a valid ``lexicon: 1`` schema document for this class."""
        from fastatp.lex.schema import class_to_lexicon_json

        return class_to_lexicon_json(cls)


def parse_record(data: dict[str, Any]) -> Lexicon:
    """Dispatch a ``$type``-carrying dict to its registered Lexicon class."""
    if not isinstance(data, dict):
        raise LexiconValidationError("record must be a JSON object")
    nsid = data.get("$type")
    if not isinstance(nsid, str):
        raise LexiconValidationError("record is missing its $type field")
    cls = _REGISTRY.get(nsid)
    if cls is None:
        raise UnknownLexicon(f"no Lexicon registered for {nsid!r}")
    return cls.from_json_dict(data)
