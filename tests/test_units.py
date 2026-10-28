"""Focused unit tests: encodings, compound types, resolver behavior, errors."""

import asyncio

import httpx
import pytest
from pydantic import ValidationError

from fastatp import (
    DIDDocument,
    DIDDocumentInvalid,
    DIDNotFound,
    DIDResolutionTimeout,
    Peer,
    WebResolver,
    build_did_document,
    crypto,
)
from fastatp.did.document import Service
from fastatp.did.resolver import did_web_url
from fastatp.exceptions import CryptoError, SignatureInvalid
from fastatp.lex import types as t

# --- multibase / varint / cid bytes ------------------------------------------


def test_base58_round_trip():
    for data in (b"", b"\x00\x00abc", b"hello world", bytes(range(50))):
        assert t.base58btc_decode(t.base58btc_encode(data)) == data
    with pytest.raises(ValueError):
        t.base58btc_decode("0OIl")  # excluded chars


def test_multibase_bases():
    payload = b"\x01q\x12 " + bytes(32)
    import base64 as b64

    assert t.multibase_decode("f" + payload.hex()) == payload
    assert t.multibase_decode("z" + t.base58btc_encode(payload)) == payload
    b32 = b64.b32encode(payload).decode().lower().rstrip("=")
    assert t.multibase_decode("b" + b32) == payload
    std = b64.b64encode(payload).decode().rstrip("=")
    assert t.multibase_decode("m" + std) == payload
    url = b64.urlsafe_b64encode(payload).decode().rstrip("=")
    assert t.multibase_decode("u" + url) == payload
    for bad in ("", "Qunknown", "b" + "A", "fzz", "m!"):
        with pytest.raises(ValueError):
            t.multibase_decode(bad)


def test_varint():
    assert t.read_varint(b"\x00", 0) == (0, 1)
    assert t.read_varint(b"\xe7\x01", 0) == (0xE7, 2)
    assert t.read_varint(b"\x80\x24", 0) == (0x1200, 2)
    with pytest.raises(ValueError):
        t.read_varint(b"\x80", 0)  # truncated
    with pytest.raises(ValueError):
        t.read_varint(b"\xff" * 12, 0)  # too long


def test_validate_cid_bytes():
    good = b"\x01q\x12 " + bytes(32)  # v1, dag-cbor, sha2-256, 32 bytes
    assert t.validate_cid_bytes(good) == good
    with pytest.raises(ValueError):
        t.validate_cid_bytes(b"\x00q\x12 " + bytes(32))  # bad version
    with pytest.raises(ValueError):
        t.validate_cid_bytes(b"\x01q\x12 " + bytes(31))  # short digest


# --- compound types -------------------------------------------------------------

CID = "bafyreidfayvfuwqa7qlnopdjiqrxzs6blmoeu4rujcjtnci5beludirz2a"


def test_cidlink():
    link = t.CidLink(CID)
    assert t.CidLink._validate({"$link": CID}) == link
    assert t.CidLink._validate(link) is link
    assert hash(link) == hash(t.CidLink(CID))
    assert repr(link).startswith("CidLink(")
    with pytest.raises(ValueError):
        t.CidLink._validate({"$link": 5})
    with pytest.raises(ValueError):
        t.CidLink._validate(42)


def test_bytes_type():
    b = t.Bytes(b"hello")
    assert t.Bytes._validate({"$bytes": b.encode()}) == b
    assert t.Bytes._validate(bytearray(b"hello")) == b
    assert t.Bytes._validate(b) is b
    assert hash(b) == hash(t.Bytes(b"hello"))
    assert "hello" in repr(b)
    with pytest.raises(ValueError):
        t.Bytes._validate({"$bytes": "!!!not-base64!!!"})
    with pytest.raises(ValueError):
        t.Bytes._validate(3.14)


def test_blob_legacy_form():
    blob = t.Blob.model_validate({"cid": CID, "mimeType": "image/png"})
    assert blob.type == "blob"
    assert blob.ref == t.CidLink(CID)
    assert blob.size == -1
    typed = t.Blob.model_validate(
        {"$type": "blob", "ref": {"$link": CID}, "mimeType": "image/png", "size": 10}
    )
    assert typed.size == 10
    with pytest.raises(ValidationError):
        t.Blob.model_validate(
            {"$type": "notblob", "ref": {"$link": CID}, "mimeType": "x", "size": 1}
        )


def test_format_datetime_rejects_naive():
    from datetime import datetime

    with pytest.raises(ValueError):
        t.format_datetime(datetime(2026, 1, 1))


# --- crypto error paths -----------------------------------------------------------


def test_curve_name_errors():
    assert crypto.curve_name("SECP256K1") == "k256"
    assert crypto.curve_name("ES256") == "p256"
    with pytest.raises(CryptoError):
        crypto.curve_name("ed25519")
    with pytest.raises(CryptoError):
        crypto.load_private_key_bytes(b"short", "k256")


def test_decode_multikey_errors():
    with pytest.raises(CryptoError):
        crypto.decode_multikey("not-z-prefixed")
    with pytest.raises(CryptoError):
        crypto.decode_multikey("z0")  # invalid base58
    with pytest.raises(CryptoError):
        crypto.decode_multikey("z" + t.base58btc_encode(b"\x01\x02\x03"))  # bad prefix
    with pytest.raises(CryptoError):
        crypto.decode_did_key("did:web:example.com")
    # valid prefix, invalid point
    bad_point = t.base58btc_encode(b"\xe7\x01" + bytes(33))
    with pytest.raises(CryptoError):
        crypto.decode_multikey("z" + bad_point)


# --- DID document ------------------------------------------------------------------


def test_verification_method_matching():
    key = crypto.generate_keypair("p256")
    doc = build_did_document(
        did="did:web:example.com",
        signing_key=key.public_key(),
        extra_services=[Service(id="#x", type="Custom", service_endpoint="https://x")],
        also_known_as=["at://ex.example.com"],
        rotation_keys=["did:key:zQ3xyz"],
    )
    assert doc.get_verification_method().id == "did:web:example.com#atproto"
    assert doc.get_verification_method("did:web:example.com#atproto")
    assert doc.get_verification_method("#atproto")
    assert doc.get_verification_method("atproto")
    with pytest.raises(DIDDocumentInvalid):
        doc.get_verification_method("#missing")
    empty = DIDDocument(id="did:web:example.com")
    with pytest.raises(DIDDocumentInvalid):
        empty.get_verification_method()
    with pytest.raises(DIDDocumentInvalid):
        DIDDocument.from_json_dict({"id": "not a did"})
    with pytest.raises(ValueError):
        build_did_document(did="nope", signing_key=key.public_key())
    # round-trips through JSON
    assert DIDDocument.from_json_dict(doc.to_json_dict()) == doc


# --- resolver ---------------------------------------------------------------------


def _mock_resolver(handler, **kwargs) -> WebResolver:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return WebResolver(client=client, **kwargs)


KEY = crypto.generate_keypair("k256")


def _doc_for(did: str) -> dict:
    return build_did_document(did=did, signing_key=KEY.public_key()).to_json_dict()


async def test_web_resolver_behaviors():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        path = request.url.path
        if path == "/.well-known/did.json":
            return httpx.Response(
                200, json=_doc_for("did:web:example.com"), headers={"Cache-Control": "max-age=60"}
            )
        if path == "/nostore/did.json":
            return httpx.Response(
                200,
                json=_doc_for("did:web:example.com:nostore"),
                headers={"Cache-Control": "no-store"},
            )
        if path == "/mismatch/did.json":
            return httpx.Response(200, json=_doc_for("did:web:example.com:other"))
        if path == "/bad/did.json":
            return httpx.Response(200, content=b"not json")
        if path == "/teapot/did.json":
            return httpx.Response(418)
        return httpx.Response(404)

    r = _mock_resolver(handler)
    doc = await r("did:web:example.com")
    assert doc.id == "did:web:example.com"
    await r("did:web:example.com")
    assert calls["n"] == 1  # cached

    await r("did:web:example.com:nostore")
    await r("did:web:example.com:nostore")
    assert calls["n"] == 3  # no-store -> refetched

    with pytest.raises(DIDDocumentInvalid):
        await r("did:web:example.com:mismatch")
    with pytest.raises(DIDDocumentInvalid):
        await r("did:web:example.com:bad")
    with pytest.raises(DIDNotFound):
        await r("did:web:example.com:teapot")
    with pytest.raises(DIDNotFound):
        await r("did:web:example.com:missing")
    with pytest.raises(DIDNotFound):
        await r("did:plc:notweb")
    await r.aclose()


async def test_web_resolver_timeout_and_coalescing():
    started = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        started["n"] += 1
        await asyncio.sleep(0.05)
        return httpx.Response(
            200, json=_doc_for("did:web:example.com"), headers={"Cache-Control": "max-age=60"}
        )

    r = _mock_resolver(handler)
    results = await asyncio.gather(*(r("did:web:example.com") for _ in range(5)))
    assert started["n"] == 1  # coalesced into one fetch
    assert all(d.id == "did:web:example.com" for d in results)
    await r.aclose()

    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("boom")

    r2 = _mock_resolver(timeout_handler)
    with pytest.raises(DIDResolutionTimeout):
        await r2("did:web:example.com")
    await r2.aclose()

    def network_error(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    r3 = _mock_resolver(network_error)
    with pytest.raises(DIDNotFound):
        await r3("did:web:example.com")
    await r3.aclose()


def test_did_web_url_errors():
    with pytest.raises(DIDNotFound):
        did_web_url("did:plc:abc")
    with pytest.raises(DIDNotFound):
        did_web_url("did:web:")


def test_lru_eviction():
    r = WebResolver(cache_size=2)
    doc = DIDDocument.from_json_dict(_doc_for("did:web:a.example"))
    for i in range(4):
        r._store(f"did:web:{i}.example", doc, "max-age=60")
    assert len(r._cache) == 2


# --- peer error paths ------------------------------------------------------------


async def test_peer_error_paths():
    from fastatp import Repo
    from fastatp.lexicon import Lexicon

    class Note(Lexicon):
        nsid = "com.example.test.note"
        text: str

    with pytest.raises(ValueError):
        Peer().create_repo()

    key = crypto.generate_keypair("k256")
    did = "did:web:example.com"
    doc = build_did_document(did=did, signing_key=key.public_key())
    repo = Repo(did, key)
    repo.put_record(Note.nsid, "n1", Note(text="hello"))
    proof = repo.prove(Note.nsid, "n1")

    # resolver returns a raw dict -> coerced to DIDDocument
    async def dict_resolver(d):
        return doc.to_json_dict() if d == did else None

    assert await Peer(resolver=dict_resolver).verify(proof)

    # resolved doc id mismatch
    other = build_did_document(did="did:web:other.example", signing_key=key.public_key())

    async def wrong_doc(d):
        return other

    with pytest.raises(SignatureInvalid):
        await Peer(resolver=wrong_doc).verify(proof)

    # unknown $type: record not in the registry
    from fastatp.exceptions import UnknownLexicon

    raw = Repo(did, key)
    raw.put_record(
        "com.example.test.unregisteredType",
        "x",
        {"$type": "com.example.test.unregisteredType", "v": 1},
    )
    stray = raw.prove("com.example.test.unregisteredType", "x")

    async def resolver(d):
        return doc if d == did else None

    with pytest.raises(UnknownLexicon):
        await Peer(resolver=resolver).verify(stray)


def test_decode_car_invalid_cid_raises_repo_error():
    from fastatp.cid import CID
    from fastatp.exceptions import RepoError
    from fastatp.repo.car import decode_car, encode_car

    car = encode_car([CID.for_raw(b"x")], {CID.for_raw(b"x"): b"x"})
    # corrupt the section so the CID prefix is garbage varints
    broken = bytearray(car)
    broken[-3:] = b"\xff\xff\xff"
    with pytest.raises(RepoError):
        decode_car(bytes(broken))


async def test_web_resolver_host_policy():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_doc_for("did:web:allowed.example"))

    r = _mock_resolver(handler, host_policy=lambda host: not host.endswith(".internal"))
    doc = await r("did:web:allowed.example")
    assert doc.id == "did:web:allowed.example"
    with pytest.raises(DIDNotFound, match="host_policy"):
        await r("did:web:svc.internal")
    await r.aclose()


def test_decode_car_bad_header_raises_repo_error():
    from fastatp.exceptions import RepoError
    from fastatp.repo.car import decode_car

    # varint length prefix followed by garbage header bytes
    with pytest.raises(RepoError, match="header"):
        decode_car(b"\x03\xff\xff\xff")


async def test_web_resolver_caps_max_age():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_doc_for("did:web:pin.example"),
            headers={"Cache-Control": "max-age=2147483647"},
        )

    r = _mock_resolver(handler, max_cache_age=60.0)
    import time as _time

    before = _time.monotonic()
    await r("did:web:pin.example")
    expires_at, _doc = r._cache["did:web:pin.example"]
    assert expires_at - before <= 61  # capped, not ~68 years
    await r.aclose()


async def test_s_maxage_not_treated_as_max_age():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            200,
            json=_doc_for("did:web:cdn.example"),
            headers={"Cache-Control": "s-maxage=3600"},
        )

    r = _mock_resolver(handler)
    await r("did:web:cdn.example")
    await r("did:web:cdn.example")
    assert calls["n"] == 2  # s-maxage alone must not populate the client cache
    await r.aclose()
