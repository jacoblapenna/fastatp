"""The Lexicon schema bridge.

Three pieces:

1. ``LexiconDoc`` — a typed (Pydantic) object model of ``lexicon: 1`` schema
   documents. Parsing validates a document; serializing round-trips it
   faithfully (``exclude_unset`` keeps exactly the fields that were present).
2. ``LexiconCatalog`` / ``from_lexicon_json`` — codegen: turn lexicon JSON
   (yours, ``com.atproto.*``, or third-party) into runtime Pydantic models
   built from the type palette.
3. ``class_to_lexicon_json`` — export: turn a user-authored ``Lexicon``
   subclass into a valid ``lexicon: 1`` document, rejecting Python constructs
   with no Lexicon equivalent (floats, plain dicts, etc.).
"""

from __future__ import annotations

import json
import types as _types
from datetime import datetime
from pathlib import Path
from typing import (
    Annotated,
    Any,
    Literal,
    Optional,
    Union,
    get_args,
    get_origin,
)

import annotated_types
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    create_model,
    field_validator,
    model_validator,
)

from fastatp.exceptions import LexiconExportError, LexiconValidationError
from fastatp.lex import types as lex_types
from fastatp.lex.types import (
    Blob,
    Bytes,
    CidLink,
    KnownValues,
    LexFormat,
    MaxGraphemes,
    MaxLength,
    MinGraphemes,
    MinLength,
    validate_nsid,
)

__all__ = [
    "LexiconCatalog",
    "LexiconDoc",
    "class_to_lexicon_json",
    "from_lexicon_json",
]


class _LexModel(BaseModel):
    # strict=True: a lexicon document is a wire artifact — "maxLength": "300"
    # is a malformed document, not a coercible one (strictness pattern from
    # MarshalX/atproto@d2e4214 atproto_lexicon/models.py; verified safe
    # against the full com.atproto corpus round-trip)
    model_config = ConfigDict(populate_by_name=True, extra="forbid", strict=True)


# --- field-level types --------------------------------------------------------

_STRING_FORMATS = Literal[
    "at-identifier",
    "at-uri",
    "cid",
    "datetime",
    "did",
    "handle",
    "nsid",
    "tid",
    "record-key",
    "uri",
    "language",
]


class LexBoolean(_LexModel):
    type: Literal["boolean"]
    description: str | None = None
    default: bool | None = None
    const: bool | None = None


class LexInteger(_LexModel):
    type: Literal["integer"]
    description: str | None = None
    default: int | None = None
    minimum: int | None = None
    maximum: int | None = None
    enum: list[int] | None = None
    const: int | None = None


class LexString(_LexModel):
    type: Literal["string"]
    description: str | None = None
    format: _STRING_FORMATS | None = None
    default: str | None = None
    minLength: int | None = None
    maxLength: int | None = None
    minGraphemes: int | None = None
    maxGraphemes: int | None = None
    knownValues: list[str] | None = None
    enum: list[str] | None = None
    const: str | None = None


class LexBytes(_LexModel):
    type: Literal["bytes"]
    description: str | None = None
    minLength: int | None = None
    maxLength: int | None = None


class LexCidLink(_LexModel):
    type: Literal["cid-link"]
    description: str | None = None


class LexUnknown(_LexModel):
    type: Literal["unknown"]
    description: str | None = None


class LexBlob(_LexModel):
    type: Literal["blob"]
    description: str | None = None
    accept: list[str] | None = None
    maxSize: int | None = None


class LexRef(_LexModel):
    type: Literal["ref"]
    description: str | None = None
    ref: str


class LexUnion(_LexModel):
    type: Literal["union"]
    description: str | None = None
    refs: list[str]
    closed: bool | None = None


class LexToken(_LexModel):
    type: Literal["token"]
    description: str | None = None


class LexArray(_LexModel):
    type: Literal["array"]
    description: str | None = None
    items: LexFieldType
    minLength: int | None = None
    maxLength: int | None = None


class LexObject(_LexModel):
    type: Literal["object"]
    description: str | None = None
    properties: dict[str, LexFieldType] = Field(default_factory=dict)
    required: list[str] | None = None
    nullable: list[str] | None = None


LexFieldType = Annotated[
    LexBoolean
    | LexInteger
    | LexString
    | LexBytes
    | LexCidLink
    | LexUnknown
    | LexBlob
    | LexArray
    | LexObject
    | LexRef
    | LexUnion
    | LexToken,
    Field(discriminator="type"),
]

_LexParamProp = Annotated[
    LexBoolean | LexInteger | LexString | LexUnknown | LexArray,
    Field(discriminator="type"),
]


class LexParams(_LexModel):
    type: Literal["params"]
    description: str | None = None
    required: list[str] | None = None
    properties: dict[str, _LexParamProp] = Field(default_factory=dict)


_LexBodySchema = Annotated[LexObject | LexRef | LexUnion, Field(discriminator="type")]


class LexXrpcBody(_LexModel):
    description: str | None = None
    encoding: str
    schema_: _LexBodySchema | None = Field(default=None, alias="schema")


class LexXrpcMessage(_LexModel):
    description: str | None = None
    schema_: _LexBodySchema | None = Field(default=None, alias="schema")


class LexXrpcError(_LexModel):
    name: str
    description: str | None = None


# --- primary (main-only) types --------------------------------------------------


class LexRecord(_LexModel):
    type: Literal["record"]
    description: str | None = None
    key: str
    record: LexObject

    @field_validator("key")
    @classmethod
    def _check_key(cls, v: str) -> str:
        if v in ("tid", "nsid", "any") or v.startswith("literal:"):
            return v
        raise ValueError(f"invalid record key clause {v!r}")


class LexQuery(_LexModel):
    type: Literal["query"]
    description: str | None = None
    parameters: LexParams | None = None
    output: LexXrpcBody | None = None
    errors: list[LexXrpcError] | None = None


class LexProcedure(_LexModel):
    type: Literal["procedure"]
    description: str | None = None
    parameters: LexParams | None = None
    input: LexXrpcBody | None = None
    output: LexXrpcBody | None = None
    errors: list[LexXrpcError] | None = None


class LexSubscription(_LexModel):
    type: Literal["subscription"]
    description: str | None = None
    parameters: LexParams | None = None
    message: LexXrpcMessage | None = None
    errors: list[LexXrpcError] | None = None


class LexPermission(_LexModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    type: Literal["permission"]
    resource: str


class LexPermissionSet(_LexModel):
    type: Literal["permission-set"]
    description: str | None = None
    title: str | None = None
    title_lang: dict[str, str] | None = Field(default=None, alias="title:lang")
    detail: str | None = None
    detail_lang: dict[str, str] | None = Field(default=None, alias="detail:lang")
    permissions: list[LexPermission]


_PRIMARY_TYPES = ("record", "query", "procedure", "subscription", "permission-set")

LexUserType = Annotated[
    LexRecord
    | LexQuery
    | LexProcedure
    | LexSubscription
    | LexPermissionSet
    | LexObject
    | LexArray
    | LexBoolean
    | LexInteger
    | LexString
    | LexBytes
    | LexCidLink
    | LexBlob
    | LexToken,
    Field(discriminator="type"),
]


class LexiconDoc(_LexModel):
    """A ``lexicon: 1`` schema document."""

    lexicon: Literal[1]
    id: str
    revision: int | None = None
    description: str | None = None
    defs: dict[str, LexUserType]

    @field_validator("id")
    @classmethod
    def _check_id(cls, v: str) -> str:
        validate_nsid(v)
        return v

    @model_validator(mode="after")
    def _check_primary_defs(self) -> LexiconDoc:
        for name, defn in self.defs.items():
            if defn.type in _PRIMARY_TYPES and name != "main":
                raise ValueError(
                    f"primary type {defn.type!r} must be the 'main' definition, not {name!r}"
                )
        return self

    def to_json_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True, exclude_unset=True)

    @classmethod
    def from_json_dict(cls, data: Any) -> LexiconDoc:
        try:
            return cls.model_validate(data)
        except LexiconValidationError:
            raise
        except Exception as e:
            raise LexiconValidationError(str(e)) from e


# ---------------------------------------------------------------------------
# Codegen: lexicon JSON -> runtime Pydantic models
# ---------------------------------------------------------------------------

_FORMAT_TYPES: dict[str, Any] = {
    "did": lex_types.Did,
    "handle": lex_types.Handle,
    "at-identifier": lex_types.AtIdentifier,
    "nsid": lex_types.Nsid,
    "at-uri": lex_types.AtUri,
    "cid": lex_types.Cid,
    "tid": lex_types.Tid,
    "record-key": lex_types.RecordKey,
    "uri": lex_types.Uri,
    "language": lex_types.Language,
    "datetime": lex_types.Datetime,
}


class LexObjectModel(BaseModel):
    """Base for models generated from (non-record) object defs."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")


def _bytes_length_check(min_length: int | None, max_length: int | None) -> Any:
    def check(v: Bytes) -> Bytes:
        if min_length is not None and len(v.data) < min_length:
            raise ValueError(f"bytes shorter than minLength {min_length}")
        if max_length is not None and len(v.data) > max_length:
            raise ValueError(f"bytes longer than maxLength {max_length}")
        return v

    return check


def _mime_matches(pattern: str, mime: str) -> bool:
    if pattern == "*/*":
        return True
    if pattern.endswith("/*"):
        return mime.split("/")[0] == pattern.split("/")[0]
    return mime == pattern


def _blob_check(max_size: int | None, accept: list[str] | None) -> Any:
    def check(v: Blob) -> Blob:
        if max_size is not None and v.size > max_size:
            raise ValueError(f"blob size {v.size} exceeds maxSize {max_size}")
        if accept is not None and not any(_mime_matches(p, v.mime_type) for p in accept):
            raise ValueError(f"blob mimeType {v.mime_type!r} not in accept list")
        return v

    return check


def _canonical_ref(nsid: str, defname: str) -> str:
    return nsid if defname == "main" else f"{nsid}#{defname}"


def _py_class_name(nsid: str, defname: str) -> str:
    tail = nsid.split(".")[-1] if defname == "main" else defname
    return tail[:1].upper() + tail[1:]


class LexiconCatalog:
    """A set of lexicon documents plus the models generated from them.

    Refs (``com.example.foo#bar``, ``#local``) resolve against the catalog, so
    cross-document schemas work as long as their documents are all added.
    """

    def __init__(self) -> None:
        self._docs: dict[str, LexiconDoc] = {}
        self._models: dict[str, Any] = {}
        self._building: set[str] = set()

    # -- loading ----------------------------------------------------------

    def add(self, doc: LexiconDoc | dict[str, Any] | str) -> LexiconDoc:
        if isinstance(doc, str):
            doc = json.loads(doc)
        if isinstance(doc, dict):
            doc = LexiconDoc.from_json_dict(doc)
        self._docs[doc.id] = doc
        return doc

    def add_directory(self, path: str | Path) -> int:
        count = 0
        for file in sorted(Path(path).rglob("*.json")):
            self.add(json.loads(file.read_text()))
            count += 1
        return count

    def doc(self, nsid: str) -> LexiconDoc:
        if nsid not in self._docs:
            raise LexiconValidationError(f"lexicon {nsid!r} is not in the catalog")
        return self._docs[nsid]

    def docs(self) -> dict[str, LexiconDoc]:
        return dict(self._docs)

    # -- ref resolution -----------------------------------------------------

    def _resolve_ref(self, ref: str, base_nsid: str) -> tuple[str, str]:
        if ref.startswith("#"):
            return base_nsid, ref[1:]
        nsid, _, defname = ref.partition("#")
        return nsid, defname or "main"

    def _def(self, nsid: str, defname: str) -> Any:
        doc = self.doc(nsid)
        if defname not in doc.defs:
            raise LexiconValidationError(f"{nsid} has no definition {defname!r}")
        return doc.defs[defname]

    # -- model generation -----------------------------------------------------

    def model_for(self, ref: str, base_nsid: str = "") -> Any:
        """Python type for a lexicon ref (record/object defs become models)."""
        nsid, defname = self._resolve_ref(ref, base_nsid)
        key = _canonical_ref(nsid, defname)
        if key in self._models:
            return self._models[key]
        if key in self._building:
            # ref cycle: degrade to unvalidated data rather than recursing
            return Any
        self._building.add(key)
        try:
            model = self._build_def(nsid, defname)
        finally:
            self._building.discard(key)
        self._models[key] = model
        return model

    def _build_def(self, nsid: str, defname: str) -> Any:
        defn = self._def(nsid, defname)
        if isinstance(defn, LexRecord):
            return self._build_record(nsid, defname, defn)
        if isinstance(defn, LexObject):
            return self._build_object(nsid, defname, defn)
        if isinstance(defn, LexToken):
            # a token's value on the wire is its own canonical ref string
            return Literal[_canonical_ref(nsid, defname)]
        if isinstance(
            defn, (LexString, LexInteger, LexBoolean, LexBytes, LexCidLink, LexBlob, LexArray)
        ):
            annotation, _default = self._field_annotation(defn, nsid, f"{defname}")
            return annotation
        raise LexiconValidationError(
            f"{_canonical_ref(nsid, defname)}: defs of type {defn.type!r} have no runtime model"
        )

    def _build_record(self, nsid: str, defname: str, defn: LexRecord) -> type:
        from fastatp import lexicon as _lexicon

        fields = self._field_defs(defn.record, nsid, defname)
        model = create_model(_py_class_name(nsid, defname), __base__=_lexicon.Lexicon, **fields)
        model.nsid = nsid
        model.record_key = defn.key
        _lexicon._REGISTRY[nsid] = model
        model.__lexicon_catalog__ = self  # type: ignore[attr-defined]
        return model

    def _build_object(self, nsid: str, defname: str, defn: LexObject) -> type:
        fields = self._field_defs(defn, nsid, defname)
        # stamp the canonical ref as a defaulted `$type`: a directly
        # constructed union member must serialize wire-valid (union data
        # without $type is invalid), and a wire value carrying the WRONG
        # $type must fail validation. The Literal also accepts the
        # equivalent `#main`-suffixed spelling for main defs. Pattern from
        # MarshalX/atproto@d2e4214 (their generator stamps py_type on every
        # object model; same field name so the two ecosystems read alike).
        ref = _canonical_ref(nsid, defname)
        if "py_type" not in fields:
            accepted = (ref, f"{ref}#main") if defname == "main" else (ref,)
            fields["py_type"] = (
                Literal[accepted],  # type: ignore[valid-type]
                Field(default=ref, alias="$type"),
            )
        model = create_model(_py_class_name(nsid, defname), __base__=LexObjectModel, **fields)
        model.__lexicon_ref__ = _canonical_ref(nsid, defname)  # type: ignore[attr-defined]
        return model

    def _field_defs(self, obj: LexObject, nsid: str, defname: str) -> dict[str, tuple[Any, Any]]:
        required = set(obj.required or [])
        nullable = set(obj.nullable or [])
        fields: dict[str, tuple[Any, Any]] = {}
        for prop_name, prop in obj.properties.items():
            annotation, default = self._field_annotation(prop, nsid, f"{defname}.{prop_name}")
            if prop_name in nullable:
                annotation = Optional[annotation]  # noqa: UP045 - runtime construction
            field_name = prop_name if prop_name.isidentifier() else f"f_{abs(hash(prop_name))}"
            kwargs: dict[str, Any] = {}
            if field_name != prop_name:
                kwargs["alias"] = prop_name
            if getattr(prop, "description", None):
                kwargs["description"] = prop.description
            if prop_name in required:
                fields[field_name] = (annotation, Field(**kwargs))
            else:
                # optional-but-not-nullable: absent is fine (default None),
                # but an explicit null must still fail validation, so the
                # annotation is NOT widened to Optional here.
                fields[field_name] = (annotation, Field(default=default, **kwargs))
        return fields

    def _field_annotation(self, prop: Any, nsid: str, where: str) -> tuple[Any, Any]:
        """Return (annotation, default) for a lexicon field schema."""
        default: Any = None
        if isinstance(prop, LexBoolean):
            annotation: Any = StrictBool
            if prop.const is not None:
                annotation = Literal[prop.const]
            default = prop.default
        elif isinstance(prop, LexInteger):
            if prop.const is not None:
                annotation = Literal[prop.const]
            elif prop.enum is not None:
                annotation = Literal[tuple(prop.enum)]
            else:
                # StrictInt: the atproto data model has no bools-as-ints or
                # numeric strings; JSON true must not validate as integer 1
                annotation = StrictInt
                constraints = []
                if prop.minimum is not None:
                    constraints.append(annotated_types.Ge(prop.minimum))
                if prop.maximum is not None:
                    constraints.append(annotated_types.Le(prop.maximum))
                if constraints:
                    annotation = Annotated[tuple([annotation, *constraints])]
            default = prop.default
        elif isinstance(prop, LexString):
            if prop.const is not None:
                annotation = Literal[prop.const]
            elif prop.enum is not None:
                annotation = Literal[tuple(prop.enum)]
            else:
                annotation = _FORMAT_TYPES.get(prop.format or "", StrictStr)
                constraints = []
                if prop.maxLength is not None:
                    constraints.append(MaxLength(prop.maxLength))
                if prop.minLength is not None:
                    constraints.append(MinLength(prop.minLength))
                if prop.maxGraphemes is not None:
                    constraints.append(MaxGraphemes(prop.maxGraphemes))
                if prop.minGraphemes is not None:
                    constraints.append(MinGraphemes(prop.minGraphemes))
                if prop.knownValues is not None:
                    constraints.append(KnownValues(prop.knownValues))
                if constraints:
                    annotation = Annotated[tuple([annotation, *constraints])]
            default = prop.default
        elif isinstance(prop, LexBytes):
            annotation = Bytes
            if prop.minLength is not None or prop.maxLength is not None:
                annotation = Annotated[
                    Bytes, AfterValidator(_bytes_length_check(prop.minLength, prop.maxLength))
                ]
        elif isinstance(prop, LexCidLink):
            annotation = CidLink
        elif isinstance(prop, LexBlob):
            annotation = Blob
            if prop.maxSize is not None or prop.accept is not None:
                annotation = Annotated[Blob, AfterValidator(_blob_check(prop.maxSize, prop.accept))]
        elif isinstance(prop, LexUnknown):
            annotation = Any
        elif isinstance(prop, LexArray):
            item_annotation, _ = self._field_annotation(prop.items, nsid, f"{where}[]")
            annotation = list[item_annotation]
            constraints = []
            if prop.minLength is not None:
                constraints.append(annotated_types.MinLen(prop.minLength))
            if prop.maxLength is not None:
                constraints.append(annotated_types.MaxLen(prop.maxLength))
            if constraints:
                annotation = Annotated[tuple([annotation, *constraints])]
        elif isinstance(prop, LexObject):
            # inline object: generate an anonymous nested model
            name = where.replace(".", "_").replace("[]", "Item")
            fields = self._field_defs(prop, nsid, where)
            annotation = create_model(_py_class_name(nsid, name), __base__=LexObjectModel, **fields)
        elif isinstance(prop, LexRef):
            annotation = self.model_for(prop.ref, nsid)
        elif isinstance(prop, LexUnion):
            annotation = self._union_annotation(prop, nsid)
        elif isinstance(prop, LexToken):
            annotation = Literal[_canonical_ref(nsid, where)]
        else:  # pragma: no cover - the doc model prevents this
            raise LexiconValidationError(f"{where}: unsupported field type {prop!r}")
        return annotation, default

    def _union_annotation(self, prop: LexUnion, base_nsid: str) -> Any:
        mapping: dict[str, Any] = {}
        for ref in prop.refs:
            nsid, defname = self._resolve_ref(ref, base_nsid)
            mapping[_canonical_ref(nsid, defname)] = self.model_for(ref, base_nsid)
        closed = bool(prop.closed)

        def _validate(v: Any) -> Any:
            if isinstance(v, BaseModel):
                return v
            if not isinstance(v, dict):
                raise ValueError("union value must be an object")
            found = v.get("$type")
            if not isinstance(found, str):
                raise ValueError("union value requires a $type field")
            canonical = found[:-5] if found.endswith("#main") else found
            model = mapping.get(canonical)
            if model is not None and isinstance(model, type) and issubclass(model, BaseModel):
                return model.model_validate(v)
            if closed:
                raise ValueError(f"closed union does not accept $type {found!r}")
            return v

        from pydantic import PlainValidator

        return Annotated[Any, PlainValidator(_validate)]


def from_lexicon_json(
    doc: LexiconDoc | dict[str, Any] | str,
    catalog: LexiconCatalog | None = None,
) -> Any:
    """Ingest a lexicon JSON document and return the model for its main def.

    Pass a ``catalog`` when the document refs other documents; the returned
    model class is also registered in the global NSID registry when the main
    definition is a record.
    """
    catalog = catalog or LexiconCatalog()
    parsed = catalog.add(doc)
    if "main" not in parsed.defs:
        raise LexiconValidationError(f"{parsed.id} has no 'main' definition")
    return catalog.model_for(parsed.id)


# ---------------------------------------------------------------------------
# Export: user-authored Lexicon subclass -> lexicon JSON
# ---------------------------------------------------------------------------


def _is_optional(annotation: Any) -> tuple[Any, bool]:
    origin = get_origin(annotation)
    if origin in (Union, _types.UnionType):
        args = [a for a in get_args(annotation) if a is not type(None)]
        if len(args) < len(get_args(annotation)):
            inner = args[0] if len(args) == 1 else Union[tuple(args)]  # noqa: UP007 - runtime construction
            return inner, True
    return annotation, False


def _string_schema_from_metadata(metadata: list[Any]) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "string"}
    for marker in metadata:
        if isinstance(marker, LexFormat):
            schema["format"] = marker.format
        elif isinstance(marker, MaxLength):
            schema["maxLength"] = marker.limit
        elif isinstance(marker, MinLength):
            schema["minLength"] = marker.limit
        elif isinstance(marker, MaxGraphemes):
            schema["maxGraphemes"] = marker.limit
        elif isinstance(marker, MinGraphemes):
            schema["minGraphemes"] = marker.limit
        elif isinstance(marker, KnownValues):
            schema["knownValues"] = list(marker.values)
    return schema


def _export_field(
    annotation: Any, metadata: list[Any], defs: dict[str, Any], where: str
) -> dict[str, Any]:
    from fastatp.lexicon import Lexicon

    annotation, _ = _is_optional(annotation)

    # unwrap Annotated
    while get_origin(annotation) is Annotated:
        args = get_args(annotation)
        annotation = args[0]
        metadata = [*metadata, *args[1:]]

    origin = get_origin(annotation)

    if annotation is float or origin is float:
        raise LexiconExportError(
            f"{where}: float has no Lexicon equivalent (the atproto data model has no floats); "
            "use int or a string"
        )
    if annotation is str:
        return _string_schema_from_metadata(metadata)
    if annotation is bool:
        return {"type": "boolean"}
    if annotation is int:
        schema: dict[str, Any] = {"type": "integer"}
        for marker in metadata:
            if isinstance(marker, annotated_types.Ge):
                schema["minimum"] = int(marker.ge)  # type: ignore[arg-type]
            elif isinstance(marker, annotated_types.Le):
                schema["maximum"] = int(marker.le)  # type: ignore[arg-type]
        return schema
    if annotation is datetime:
        return {"type": "string", "format": "datetime"}
    if annotation is Bytes:
        return {"type": "bytes"}
    if annotation is CidLink:
        return {"type": "cid-link"}
    if annotation is Blob:
        return {"type": "blob"}
    if annotation is Any:
        return {"type": "unknown"}
    if origin in (list, tuple, set, frozenset):
        if origin is not list:
            raise LexiconExportError(f"{where}: only list maps to a Lexicon array")
        (item,) = get_args(annotation) or (Any,)
        schema = {"type": "array", "items": _export_field(item, [], defs, f"{where}[]")}
        for marker in metadata:
            if isinstance(marker, annotated_types.MinLen):
                schema["minLength"] = marker.min_length
            elif isinstance(marker, annotated_types.MaxLen):
                schema["maxLength"] = marker.max_length
        return schema
    if origin is Literal:
        values = get_args(annotation)
        if all(isinstance(v, str) for v in values):
            return {"type": "string", "enum": list(values)}
        if all(isinstance(v, int) and not isinstance(v, bool) for v in values):
            return {"type": "integer", "enum": list(values)}
        raise LexiconExportError(f"{where}: Literal values must be all-str or all-int")
    if origin in (Union, _types.UnionType):
        refs = []
        for arg in get_args(annotation):
            sub = _export_field(arg, [], defs, where)
            if sub.get("type") != "ref":
                raise LexiconExportError(
                    f"{where}: union variants must be Lexicon or nested models, got {arg!r}"
                )
            refs.append(sub["ref"])
        return {"type": "union", "refs": refs}
    if origin is dict or annotation is dict:
        args = get_args(annotation)
        if not args or args[1] is Any:
            return {"type": "unknown"}
        raise LexiconExportError(
            f"{where}: typed dicts/maps have no Lexicon equivalent; use a nested model or Any"
        )
    if isinstance(annotation, type) and issubclass(annotation, Lexicon):
        if not annotation.nsid:
            raise LexiconExportError(f"{where}: referenced Lexicon has no nsid")
        return {"type": "ref", "ref": annotation.nsid}
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        ref = getattr(annotation, "__lexicon_ref__", None)
        if isinstance(ref, str) and ref:
            return {"type": "ref", "ref": ref}
        name = annotation.__name__[:1].lower() + annotation.__name__[1:]
        if name not in defs:
            defs[name] = None  # reserve to break cycles
            defs[name] = _object_schema_from_model(annotation, defs)
        return {"type": "ref", "ref": f"#{name}"}
    raise LexiconExportError(f"{where}: {annotation!r} has no Lexicon equivalent")


def _object_schema_from_model(model: type[BaseModel], defs: dict[str, Any]) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, field in model.model_fields.items():
        if name in ("nsid", "record_key"):
            continue
        wire_name = field.alias or name
        schema = _export_field(
            field.annotation, list(field.metadata), defs, f"{model.__name__}.{name}"
        )
        if field.description:
            schema["description"] = field.description
        _, optional = _is_optional(field.annotation)
        if field.is_required() and not optional:
            required.append(wire_name)
        elif (
            field.default is not None
            and not callable(getattr(field, "default_factory", None) or 0)
            and isinstance(field.default, (str, int, bool))
            and schema["type"] in ("string", "integer", "boolean")
        ):
            schema["default"] = field.default
        properties[wire_name] = schema
    obj: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        obj["required"] = required
    return obj


def class_to_lexicon_json(cls: Any) -> dict[str, Any]:
    """Emit a valid ``lexicon: 1`` document for a user-authored Lexicon class.

    Validates the result through :class:`LexiconDoc` before returning, so a
    successful export is guaranteed to be a conforming schema document.
    """
    if not getattr(cls, "nsid", ""):
        raise LexiconExportError(f"{cls.__name__} has no nsid")
    defs: dict[str, Any] = {}
    record_schema = _object_schema_from_model(cls, defs)
    doc: dict[str, Any] = {
        "lexicon": 1,
        "id": cls.nsid,
        "defs": {
            "main": {"type": "record", "key": cls.record_key, "record": record_schema},
            **defs,
        },
    }
    if cls.__doc__ and cls.__doc__ is not cls.__mro__[1].__doc__:
        doc["defs"]["main"]["description"] = " ".join(cls.__doc__.split())
    try:
        LexiconDoc.from_json_dict(doc)
    except LexiconValidationError as e:
        raise LexiconExportError(f"{cls.__name__}: export is not a valid lexicon: {e}") from e
    return doc
