"""Codegen (lexicon JSON -> Pydantic) and export (Pydantic -> lexicon JSON)."""

from datetime import datetime
from typing import Annotated

import pytest
from pydantic import BaseModel as _BM
from pydantic import ValidationError

from fastatp.exceptions import LexiconExportError, LexiconValidationError
from fastatp.lex.schema import LexiconCatalog, LexiconDoc, from_lexicon_json
from fastatp.lex.types import (
    Bytes,
    CidLink,
    Datetime,
    Did,
    MaxGraphemes,
    MaxLength,
    Tid,
)
from fastatp.lexicon import Lexicon, lookup_lexicon, parse_record
from tests.conftest import REPO_ROOT

ATPROTO_LEXICONS = REPO_ROOT / "reference" / "atproto" / "lexicons" / "com" / "atproto"


@pytest.fixture(scope="module")
def catalog() -> LexiconCatalog:
    if not ATPROTO_LEXICONS.is_dir():
        pytest.skip("reference/atproto/lexicons not present")
    cat = LexiconCatalog()
    count = cat.add_directory(ATPROTO_LEXICONS)
    assert count > 50
    return cat


def test_codegen_all_atproto_models(catalog):
    """Every com.atproto def with a runtime shape becomes a model."""
    built = 0
    for nsid, doc in catalog.docs().items():
        for defname, defn in doc.defs.items():
            if defn.type in ("record", "object", "string", "token"):
                model = catalog.model_for(f"{nsid}#{defname}", nsid)
                assert model is not None
                built += 1
    assert built > 40


def test_codegen_record_registers(catalog):
    model = catalog.model_for("com.atproto.lexicon.schema")
    assert issubclass(model, Lexicon)
    assert model.nsid == "com.atproto.lexicon.schema"
    inst = model(lexicon=1)
    assert lookup_lexicon("com.atproto.lexicon.schema") is model
    dumped = inst.to_json_dict()
    assert dumped["$type"] == "com.atproto.lexicon.schema"
    assert dumped["lexicon"] == 1


def test_codegen_object_model_validates(catalog):
    strong_ref = catalog.model_for("com.atproto.repo.strongRef")
    ref = strong_ref.model_validate(
        {
            "uri": "at://did:plc:asdf123/com.atproto.feed.post/self",
            "cid": "bafyreidfayvfuwqa7qlnopdjiqrxzs6blmoeu4rujcjtnci5beludirz2a",
        }
    )
    assert ref.uri.startswith("at://")
    with pytest.raises(ValidationError):
        strong_ref.model_validate({"uri": "not-an-at-uri", "cid": "bafyre..."})


class Reading(Lexicon):
    """A pressure reading from an edge sensor."""

    nsid = "com.example.telemetry.pressureReading"
    pressure_psi: int
    timestamp: Datetime
    note: Annotated[str, MaxGraphemes(200), MaxLength(800)] | None = None


class Task(Lexicon):
    nsid = "com.example.agent.task"
    assigned_to: Did
    context_id: Tid
    payload_ref: CidLink | None = None
    raw: Bytes | None = None


def test_registry_dispatch():
    rec = parse_record(
        {
            "$type": "com.example.telemetry.pressureReading",
            "pressure_psi": 1450,
            "timestamp": "2026-07-02T12:00:00Z",
        }
    )
    assert isinstance(rec, Reading)
    assert rec.pressure_psi == 1450


def test_type_mismatch_rejected():
    with pytest.raises(LexiconValidationError):
        Reading.from_json_dict(
            {"$type": "com.other.thing", "pressure_psi": 1, "timestamp": "2026-07-02T12:00:00Z"}
        )


def test_export_valid_doc():
    doc = Reading.to_lexicon_json()
    assert doc["lexicon"] == 1
    assert doc["id"] == Reading.nsid
    main = doc["defs"]["main"]
    assert main["type"] == "record"
    props = main["record"]["properties"]
    assert props["pressure_psi"] == {"type": "integer"}
    assert props["timestamp"] == {"type": "string", "format": "datetime"}
    assert props["note"] == {"type": "string", "maxLength": 800, "maxGraphemes": 200}
    assert sorted(main["record"]["required"]) == ["pressure_psi", "timestamp"]
    # export validates as a proper lexicon: 1 doc
    LexiconDoc.from_json_dict(doc)


def test_export_import_round_trip():
    doc = Task.to_lexicon_json()
    regenerated = from_lexicon_json(doc)
    assert regenerated.to_lexicon_json() == doc


def test_export_rejects_floats():
    class BadFloat(Lexicon):
        nsid = "com.example.test.badFloat"
        value: float

    with pytest.raises(LexiconExportError, match="float"):
        BadFloat.to_lexicon_json()


def test_export_rejects_typed_dict_values():
    class BadMap(Lexicon):
        nsid = "com.example.test.badMap"
        counts: dict[str, int]

    with pytest.raises(LexiconExportError):
        BadMap.to_lexicon_json()


def test_grapheme_and_byte_constraints():
    # 4 family emoji = 4 graphemes but many bytes
    Reading(pressure_psi=1, timestamp="2026-07-02T12:00:00Z", note="👨‍👩‍👧‍👧" * 4)
    with pytest.raises(ValidationError):
        Reading(pressure_psi=1, timestamp="2026-07-02T12:00:00Z", note="x" * 201)
    with pytest.raises(ValidationError):  # 801 bytes but only 267 graphemes
        Reading(pressure_psi=1, timestamp="2026-07-02T12:00:00Z", note="é" * 401)


def test_datetime_coercion():
    from datetime import timezone

    rec = Reading(pressure_psi=1, timestamp=datetime(2026, 7, 2, 12, 0, tzinfo=timezone.utc))
    assert rec.timestamp == "2026-07-02T12:00:00Z"
    with pytest.raises(ValidationError):
        Reading(pressure_psi=1, timestamp=datetime(2026, 7, 2, 12, 0))  # naive


class Inner(Lexicon):
    nsid = "com.example.test.inner"
    tag: str


class Other(Lexicon):
    nsid = "com.example.test.other"
    tag: str


class NestedPart(_BM):
    label: str
    weight: int


class Outer(Lexicon):
    """Outer record exercising union, nested model, literals, arrays."""

    nsid = "com.example.test.outer"
    child: Inner | Other
    part: NestedPart
    mode: "list[str]"
    level: Annotated[int, None] = 0
    kind: "str" = "plain"


def test_export_union_and_nested():

    doc = Outer.to_lexicon_json()
    props = doc["defs"]["main"]["record"]["properties"]
    assert props["child"] == {
        "type": "union",
        "refs": ["com.example.test.inner", "com.example.test.other"],
    }
    assert props["part"] == {"type": "ref", "ref": "#nestedPart"}
    assert doc["defs"]["nestedPart"]["type"] == "object"
    assert props["mode"] == {"type": "array", "items": {"type": "string"}}
    assert props["level"] == {"type": "integer", "default": 0}
    assert props["kind"] == {"type": "string", "default": "plain"}
    # regenerating from the export is itself a valid, loadable lexicon
    LexiconDoc.from_json_dict(doc)


def test_export_literals_and_unknown():
    from typing import Any, Literal

    class Enums(Lexicon):
        nsid = "com.example.test.enums"
        color: Literal["red", "green"]
        size: Literal[1, 2, 3]
        blob_of_stuff: Any

    props = Enums.to_lexicon_json()["defs"]["main"]["record"]["properties"]
    assert props["color"] == {"type": "string", "enum": ["red", "green"]}
    assert props["size"] == {"type": "integer", "enum": [1, 2, 3]}
    assert props["blob_of_stuff"] == {"type": "unknown"}


def test_export_error_cases():
    from typing import Literal

    class BadUnion(Lexicon):
        nsid = "com.example.test.badUnion"
        v: "int | str"

    with pytest.raises(LexiconExportError):
        BadUnion.to_lexicon_json()

    class BadLiteral(Lexicon):
        nsid = "com.example.test.badLiteral"
        v: Literal["a", 1]

    with pytest.raises(LexiconExportError):
        BadLiteral.to_lexicon_json()

    class BadSet(Lexicon):
        nsid = "com.example.test.badSet"
        v: set[str]

    with pytest.raises(LexiconExportError):
        BadSet.to_lexicon_json()

    class NoNsid(Lexicon):
        v: int

    with pytest.raises(LexiconExportError):
        NoNsid.to_lexicon_json()


def test_codegen_union_and_token(catalog):
    from fastatp.lex.schema import LexiconCatalog

    cat = LexiconCatalog()
    cat.add(
        {
            "lexicon": 1,
            "id": "com.example.test.tokens",
            "defs": {
                "main": {
                    "type": "record",
                    "key": "tid",
                    "record": {
                        "type": "object",
                        "properties": {
                            "state": {"type": "ref", "ref": "#stateActive"},
                            "extra": {
                                "type": "union",
                                "refs": ["#detail"],
                            },
                        },
                    },
                },
                "stateActive": {"type": "token", "description": "the active state"},
                "detail": {
                    "type": "object",
                    "properties": {"note": {"type": "string"}},
                },
            },
        }
    )
    model = cat.model_for("com.example.test.tokens")
    inst = model.model_validate(
        {
            "state": "com.example.test.tokens#stateActive",
            "extra": {"$type": "com.example.test.tokens#detail", "note": "hi"},
        }
    )
    assert inst.state == "com.example.test.tokens#stateActive"
    assert inst.extra.note == "hi"
    with pytest.raises(ValidationError):
        model.model_validate({"state": "com.example.test.tokens#other"})
    # open union tolerates unknown $type as raw data
    loose = model.model_validate(
        {"state": "com.example.test.tokens#stateActive", "extra": {"$type": "com.x.y", "z": 1}}
    )
    assert loose.extra == {"$type": "com.x.y", "z": 1}
    # union values must be objects with $type
    with pytest.raises(ValidationError):
        model.model_validate({"extra": "nope"})
    with pytest.raises(ValidationError):
        model.model_validate({"extra": {"note": "missing type"}})


def test_model_for_unsupported_def(catalog):
    with pytest.raises(LexiconValidationError):
        catalog.model_for("com.atproto.server.createSession")  # procedure def
    with pytest.raises(LexiconValidationError):
        catalog.model_for("com.atproto.not.real")
    with pytest.raises(LexiconValidationError):
        catalog.doc("com.atproto.not.real")


def test_from_lexicon_json_requires_main():
    with pytest.raises(LexiconValidationError):
        from_lexicon_json(
            {"lexicon": 1, "id": "com.example.test.nomain", "defs": {"x": {"type": "string"}}}
        )
