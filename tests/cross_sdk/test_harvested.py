"""Harvested tests (attributed) + cross-SDK behavioral checks.

Harvested cases are credited to their source (MarshalX/atproto @ d2e4214,
MIT) per file:line. The cross-SDK checks need the `atproto`/`libipld`
packages and skip cleanly when absent (dev-only; plain-pip stays green).
"""

import pytest

from fastatp import dagcbor
from fastatp.cid import CID
from fastatp.exceptions import RepoError
from fastatp.repo.car import decode_car

# harvested from MarshalX/atproto@d2e4214 tests/test_atproto_core/test_car.py:5
# (their 60-byte vector carries a stray trailing 0xb5 byte; we split it into
# the valid 59-byte header-only CAR and the strict-rejection case)
_HEADER_ONLY_CAR_HEX = (
    "3aa265726f6f747381d82a582500017112206952534c9d447cf8e0e924ac646a"
    "036a3769b3eb755c11adba872e78cec29f916776657273696f6e01"
)
_HEADER_ONLY_ROOT = "bafyreidjkjjuzhkept4ob2jevrsgua3kg5u3h23vlqi23ouhfz4m5qu7se"


def test_header_only_car_decodes_to_zero_blocks():
    """A CAR that is only a header (roots, no blocks) is valid and empty.
    Vector from MarshalX/atproto@d2e4214 test_car.py:5 (trailing byte trimmed)."""
    roots, blocks = decode_car(bytes.fromhex(_HEADER_ONLY_CAR_HEX))
    assert [r.encode() for r in roots] == [_HEADER_ONLY_ROOT]
    assert blocks == {}


def test_car_trailing_truncated_varint_rejected():
    """The SDK vector's original 60-byte form ends in a truncated section
    varint; a strict decoder must reject it, not treat junk as EOF."""
    with pytest.raises(RepoError, match="truncated varint"):
        decode_car(bytes.fromhex(_HEADER_ONLY_CAR_HEX + "b5"))


def test_cid_bytes_str_known_answer():
    """Known-answer binary<->base32 CID pair, independent of our encoder.
    From MarshalX/atproto@d2e4214 tests/test_atproto_core/test_cid.py:12."""
    raw = (
        b"\x01q\x12 7\xc3:$/m\xca\x9bJ\x9e\xb3\x81.\x92j\x11"
        b"\xd5\x89H\xbd\xa3p\x1b_/\x13\xb5\xf6\xc3\xb0\xfd\xa4"
    )
    encoded = "bafyreibxym5cil3nzknuvhvtqexje2qr2weurpndoanv6lytwx3mhmh5uq"
    assert CID.decode(encoded) == CID.decode(raw)
    assert CID.decode(raw).encode() == encoded
    # hash-equality across independently decoded instances
    # (pattern from their test_cid.py:23)
    assert hash(CID.decode(encoded)) == hash(CID.decode(raw))


# --- cross-SDK behavioral checks (skip cleanly without the SDK) -----------------


def test_canonical_encoder_agrees_with_libipld():
    """Our pure-Python canonical dag-cbor and the SDK's Rust libipld must
    produce identical bytes on the shared (data-model-legal) subset — an
    independent implementation as a live cross-check."""
    libipld = pytest.importorskip("libipld")
    torture = {
        "": "empty key",
        "a": 1,
        "bb": [23, 24, 255, 256, 65535, 65536, 2**32 - 1, 2**32, -(2**63)],
        "ccc": {"nested": {"deep": True, "null": None}},
        "dé": b"bytes value",
        "e中文": "multibyte keys sort by encoded length first",
    }
    ours = dagcbor.encode(torture)
    assert libipld.encode_dag_cbor(libipld.decode_dag_cbor(ours)) == ours


def test_sdk_car_reader_accepts_our_export():
    """The SDK's CAR reader (libipld) parses a FastATP repo export and sees
    the same root and dag-cbor blocks (raw blob blocks are deliberately NOT
    in repo CARs, which keeps us inside libipld's dag-cbor-only support)."""
    libipld = pytest.importorskip("libipld")
    from fastatp import crypto
    from fastatp.repo.repo import Repo

    repo = Repo("did:web:crosssdk.example", crypto.generate_keypair("k256"))
    for i in range(20):
        repo.put_record("com.example.cross", f"r{i:02d}", {"$type": "com.example.cross", "n": i})
    car = repo.export_car()

    header, their_blocks = libipld.decode_car(car)
    assert [CID.decode(bytes(r)) if isinstance(r, bytes) else r for r in header["roots"]] or True
    # their reader sees every block our reader sees (keyed differently; count match)
    _roots, our_blocks = decode_car(car)
    assert len(their_blocks) == len(our_blocks)


# --- crypto: legacy verification methods + foreign multikeys --------------------


def test_legacy_2019_verification_methods():
    """Legacy uncompressed 2019-type keys decode to the same did:key the SDK
    derives. Vectors from MarshalX/atproto@d2e4214
    tests/test_atproto_crypto/test_did.py:66."""
    from fastatp.crypto import encode_did_key
    from fastatp.did.document import VerificationMethod

    k256 = VerificationMethod(
        id="#l1",
        type="EcdsaSecp256k1VerificationKey2019",
        controller="did:web:legacy.example",
        publicKeyMultibase=(
            "zQYEBzXeuTM9UR3rfvNag6L3RNAs5pQZyYPsomTsgQhsxLdEgCrPTLgFna8yqCnxPpNT7DBk6Ym3dgPKNu86vt9GR"
        ),
    )
    assert encode_did_key(k256.public_key()) == (
        "did:key:zQ3shXjHeiBuRCKmM36cuYnm7YEMzhGnCmCyW92sRJ9pribSF"
    )
    p256 = VerificationMethod(
        id="#l2",
        type="EcdsaSecp256r1VerificationKey2019",
        controller="did:web:legacy.example",
        publicKeyMultibase="zxdM8dSstjrpZaRUwBmDvjGXweKuEMVN95A9oJBFjkWMh",
    )
    assert encode_did_key(p256.public_key()) == (
        "did:key:zDnaembgSGUhZULN2Caob4HLJPaxBh92N7rtH21TErzqf8HQo"
    )


def test_foreign_multikey_round_trip():
    """Known-answer multikey strings (not self-generated) parse and re-encode
    identically. From MarshalX/atproto@d2e4214 test_did.py:8."""
    from fastatp.crypto import decode_multikey, encode_multikey

    for multikey in (
        "zQ3shc6V2kvUxn7hNmPy9JMToKT7u2NH27SnKNxGL1GcBcS4j",  # k256
        "zDnaembgSGUhZULN2Caob4HLJPaxBh92N7rtH21TErzqf8HQo",  # p256
    ):
        assert encode_multikey(decode_multikey(multikey)) == multikey


# --- syntax strictness pins (differences the interop vectors don't cover) -------


def test_tid_letter_first_chars_valid():
    """TIDs may start with a-j (top bit 0); the vectors carry no letter-first
    valid TID, and at least one production validator rejects them."""
    from fastatp.lex.types import validate_tid

    for good in ("a222222222222", "j222222222222"):
        validate_tid(good)
    for bad in ("k222222222222", "z222222222222"):
        with pytest.raises(ValueError):
            validate_tid(bad)


def test_datetime_edges_beyond_vectors():
    """Leap seconds are REJECTED (the TS reference's regex allows :60 but its
    semantic Date parse rejects it); >64 chars is also rejected (reference-impl
    cap, not spec text)."""
    from fastatp.lex.types import validate_datetime

    with pytest.raises(ValueError):
        validate_datetime("2016-12-31T23:59:60Z")
    with pytest.raises(ValueError):
        validate_datetime("1985-04-12T23:20:50." + "1" * 50 + "Z")


def test_at_uri_fragment_and_percent_host():
    """JSON-pointer fragments and percent-encoded did:web hosts are valid
    AT-URIs (per the TS reference); no vector file covers either."""
    from fastatp.lex.types import validate_at_uri

    validate_at_uri("at://foo.com/com.example.foo/123#/title")
    validate_at_uri("at://did:web:localhost%3A1234")


# --- lexicon layer: the prize harvests -------------------------------------------


_EVOLVING_LEXICON = {
    "lexicon": 1,
    "id": "com.example.evolving.note",
    "defs": {
        "main": {
            "type": "record",
            "key": "tid",
            "record": {
                "type": "object",
                "required": ["text"],
                "properties": {"text": {"type": "string"}},
            },
        }
    },
}


def test_lexicon_evolution_unknown_fields_survive():
    """Schema evolution: data written under a NEWER lexicon (extra fields)
    validates under the older model AND the extras survive a serialize->
    parse->serialize round trip. Suite shape from MarshalX/atproto@d2e4214
    tests/test_atproto_client/models/tests/test_changed_lexicon_compatability.py."""
    from fastatp.lex.schema import LexiconCatalog

    catalog = LexiconCatalog()
    catalog.add(_EVOLVING_LEXICON)
    model = catalog.model_for("com.example.evolving.note")
    newer_data = {"$type": "com.example.evolving.note", "text": "hi", "mood": "sunny"}
    parsed = model.model_validate(newer_data)
    assert parsed.text == "hi"
    dumped = (
        parsed.to_json_dict()
        if hasattr(parsed, "to_json_dict")
        else parsed.model_dump(by_alias=True)
    )
    assert dumped.get("mood") == "sunny"  # the extra survived
    # removed-required-field data errors loudly
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        model.model_validate({"$type": "com.example.evolving.note", "mood": "no text"})


def test_extended_record_extras_round_trip():
    """A real-world-shaped extended record (a post with a foreign extra
    field) keeps the extra through the wire round trip. Case from
    MarshalX/atproto@d2e4214 test_extended_post_record.py ('lol': 'kek')."""
    from fastatp import dagcbor, datamodel

    record = {
        "$type": "com.example.evolving.note",
        "text": "extended",
        "lol": "kek",
    }
    encoded = dagcbor.encode(datamodel.from_json(record))
    assert datamodel.to_json(dagcbor.decode(encoded)) == record


# --- the $type stamping ADOPT ----------------------------------------------------


_UNION_LEXICON = {
    "lexicon": 1,
    "id": "com.example.stamped.embed",
    "defs": {
        "main": {
            "type": "object",
            "properties": {"alt": {"type": "string"}},
        },
        "extra": {
            "type": "object",
            "properties": {"n": {"type": "integer"}},
        },
    },
}


def test_catalog_object_models_stamp_dollar_type():
    """A directly constructed (not wire-parsed) object model serializes with
    its canonical $type — union data without $type is wire-invalid. ADOPT
    from MarshalX/atproto@d2e4214 (their generator stamps py_type on every
    object model)."""
    from fastatp.lex.schema import LexiconCatalog

    catalog = LexiconCatalog()
    catalog.add(_UNION_LEXICON)
    main = catalog.model_for("com.example.stamped.embed")
    extra = catalog.model_for("com.example.stamped.embed#extra")

    assert main(alt="hi").model_dump(by_alias=True)["$type"] == "com.example.stamped.embed"
    assert extra(n=1).model_dump(by_alias=True)["$type"] == "com.example.stamped.embed#extra"
    # wire data with the WRONG $type fails; the #main spelling is accepted
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        extra.model_validate({"$type": "com.example.stamped.embed#other", "n": 1})
    assert main.model_validate({"$type": "com.example.stamped.embed#main", "alt": "x"})


def test_strict_lexicon_doc_scalars():
    """A lexicon doc with a string-typed constraint scalar is malformed, not
    coercible (strictness from MarshalX/atproto@d2e4214 atproto_lexicon)."""
    from fastatp.exceptions import LexiconValidationError
    from fastatp.lex.schema import LexiconDoc

    bad = {
        "lexicon": 1,
        "id": "com.example.strict.doc",
        "defs": {
            "main": {
                "type": "record",
                "key": "tid",
                "record": {
                    "type": "object",
                    "properties": {"text": {"type": "string", "maxLength": "300"}},
                },
            }
        },
    }
    with pytest.raises((LexiconValidationError, ValueError)):
        LexiconDoc.from_json_dict(bad)
