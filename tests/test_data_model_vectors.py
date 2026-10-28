"""DAG-CBOR + CID conformance against the data-model vectors."""

import base64
import json

import pytest

from fastatp import dagcbor, datamodel
from fastatp.cid import CID, DAG_CBOR_CODEC, RAW_CODEC
from fastatp.exceptions import DataModelError


def _load(interop_dir, name):
    return json.loads((interop_dir / "data-model" / name).read_text())


def test_fixtures_encode_decode_cid(interop_dir):
    fixtures = _load(interop_dir, "data-model-fixtures.json")
    assert len(fixtures) >= 3
    for fx in fixtures:
        tree = datamodel.from_json(fx["json"])
        encoded = dagcbor.encode(tree)
        expected = base64.b64decode(fx["cbor_base64"] + "=" * (-len(fx["cbor_base64"]) % 4))
        assert encoded == expected, "canonical encoding mismatch"
        # CID identical to atproto
        assert CID.for_dag_cbor(encoded).encode() == fx["cid"]
        # decode -> JSON form round-trip
        decoded = dagcbor.decode(encoded)
        assert datamodel.to_json(decoded) == fx["json"]


def test_data_model_valid(interop_dir):
    for case in _load(interop_dir, "data-model-valid.json"):
        tree = datamodel.from_json(case["json"])
        # must also encode cleanly
        dagcbor.encode(tree)


def test_data_model_invalid(interop_dir):
    for case in _load(interop_dir, "data-model-invalid.json"):
        with pytest.raises(DataModelError):
            datamodel.from_json(case["json"])


def test_integer_like_float_coerces():
    tree = datamodel.from_json({"a": 123.0})
    assert tree == {"a": 123}
    assert isinstance(tree["a"], int)


def test_cid_class():
    # dag-cbor CID from fixture 1
    c = CID.decode("bafyreiclp443lavogvhj3d2ob2cxbfuscni2k5jk7bebjzg7khl3esabwq")
    assert c.version == 1
    assert c.codec == DAG_CBOR_CODEC
    assert c.encode() == "bafyreiclp443lavogvhj3d2ob2cxbfuscni2k5jk7bebjzg7khl3esabwq"
    r = CID.for_raw(b"hello world")
    assert r.codec == RAW_CODEC
    assert CID.decode(r.encode()) == r
    assert CID.decode(bytes(c)) == c
    with pytest.raises(ValueError):
        CID.decode("QmbWqxBEKC3P8tqsKc98xmWNzrzDtRLMiMPL8wBuTGsMnR")


def test_canonical_encoding_rules():
    # map keys sort length-first, then bytewise
    assert dagcbor.encode({"bb": 1, "a": 2, "ab": 3}) == dagcbor.encode({"a": 2, "ab": 3, "bb": 1})
    enc = dagcbor.encode({"bb": 1, "a": 2})
    # 'a' key must come first
    assert enc.index(b"a") < enc.index(b"bb")
    # ints are minimal width
    assert dagcbor.encode(23) == b"\x17"
    assert dagcbor.encode(24) == b"\x18\x18"
    assert dagcbor.encode(-1) == b"\x20"
    assert dagcbor.encode(True) == b"\xf5"
    assert dagcbor.encode(None) == b"\xf6"
    with pytest.raises(DataModelError):
        dagcbor.encode(1.5)
    with pytest.raises(DataModelError):
        dagcbor.encode({1: "non-string key"})
    with pytest.raises(DataModelError):
        dagcbor.encode(object())


def test_strict_decode():
    # indefinite-length map is rejected
    with pytest.raises(DataModelError):
        dagcbor.decode(b"\xbf\x61\x61\x01\xff")
    # non-minimal int is rejected
    with pytest.raises(DataModelError):
        dagcbor.decode(b"\x18\x17")  # 23 encoded wide
    # trailing garbage is rejected
    with pytest.raises(DataModelError):
        dagcbor.decode(b"\x01\x01")
    # unsorted map keys are rejected
    ok = dagcbor.encode({"a": 1, "b": 2})
    swapped = b"\xa2\x61b\x02\x61a\x01"
    with pytest.raises(DataModelError):
        dagcbor.decode(swapped)
    assert dagcbor.decode(ok) == {"a": 1, "b": 2}
    # duplicate keys are rejected
    with pytest.raises(DataModelError):
        dagcbor.decode(b"\xa2\x61a\x01\x61a\x02")


def test_lexicon_record_to_canonical_cbor():
    """Palette compound types serialize to their data-model forms in DAG-CBOR."""
    from fastatp.lex.types import Bytes, CidLink
    from fastatp.lexicon import Lexicon

    class Packet(Lexicon):
        nsid = "com.example.test.packet"
        payload: Bytes
        prev: CidLink

    cid_str = "bafyreiclp443lavogvhj3d2ob2cxbfuscni2k5jk7bebjzg7khl3esabwq"
    rec = Packet(payload=b"\x01\x02", prev=cid_str)
    encoded = rec.to_dag_cbor()
    # tag 42 for the link (0xd8 0x2a), byte string (major 2) for bytes
    assert b"\xd8\x2a" in encoded
    decoded = dagcbor.decode(encoded)
    assert decoded["$type"] == "com.example.test.packet"
    assert decoded["payload"] == b"\x01\x02"
    assert decoded["prev"] == CID.decode(cid_str)
    assert rec.cid() == CID.for_dag_cbor(encoded)
    # and the JSON form still uses $bytes/$link
    j = rec.to_json_dict()
    assert j["payload"] == {"$bytes": "AQI"}
    assert j["prev"] == {"$link": cid_str}


def test_non_canonical_simples_rejected():
    """0xf8-encoded (extended) simples and all float widths are refused."""
    import pytest

    for bad in (b"\xf8\x14", b"\xf8\x15", b"\xf8\x16", b"\xf8\x20"):
        with pytest.raises(DataModelError):
            dagcbor.decode(bad)
    for bad in (b"\xf9\x3c\x00", b"\xfa\x3f\x80\x00\x00", b"\xfb" + bytes(8)):
        with pytest.raises(DataModelError):
            dagcbor.decode(bad)
    # canonical simples still decode
    assert dagcbor.decode(b"\xf4") is False
    assert dagcbor.decode(b"\xf5") is True
    assert dagcbor.decode(b"\xf6") is None
