"""DIDServer routes/headers + the live web_resolver round-trip."""

import asyncio
import socket

import httpx
import pytest
import uvicorn

from fastatp import DIDServer, Peer, WebResolver, build_did_document
from fastatp.crypto import generate_keypair
from fastatp.did.resolver import did_web_url
from fastatp.exceptions import DIDNotFound

DOMAIN = "devices.example"

root_key = generate_keypair("k256")
slug_key = generate_keypair("p256")

DOCS = {
    "": build_did_document(did=f"did:web:{DOMAIN}", signing_key=root_key.public_key()),
    "site-alpha": build_did_document(
        did=f"did:web:{DOMAIN}:site-alpha",
        signing_key=slug_key.public_key(),
        pds_endpoint=f"https://placeholder.{DOMAIN}/repos/site-alpha",
    ),
}


async def resolve_slug(slug: str):
    return DOCS.get(slug)


@pytest.fixture
def server():
    return DIDServer(domain=DOMAIN, resolver=resolve_slug)


@pytest.fixture
async def client(server):
    transport = httpx.ASGITransport(app=server.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


async def test_root_did_json(client):
    r = await client.get("/.well-known/did.json")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert r.headers["cache-control"] == "max-age=60"
    body = r.json()
    assert body["id"] == f"did:web:{DOMAIN}"
    assert body["verificationMethod"][0]["type"] == "Multikey"
    assert body["@context"][0] == "https://www.w3.org/ns/did/v1"


async def test_slug_did_json(client):
    r = await client.get("/site-alpha/did.json")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == f"did:web:{DOMAIN}:site-alpha"
    assert body["service"][0]["type"] == "AtprotoPersonalDataServer"


async def test_unknown_slug_404(client):
    r = await client.get("/nope/did.json")
    assert r.status_code == 404
    assert r.headers["cache-control"] == "max-age=60"


def test_did_web_url_mapping():
    assert did_web_url("did:web:example.com") == "https://example.com/.well-known/did.json"
    assert did_web_url("did:web:example.com:a:b") == "https://example.com/a/b/did.json"
    assert did_web_url("did:web:example.com%3A8443:dev") == "https://example.com:8443/dev/did.json"
    assert did_web_url("did:web:localhost%3A9099") == "http://localhost:9099/.well-known/did.json"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
async def live_server():
    """A real DIDServer on localhost serving documents whose DIDs match it."""
    port = _free_port()
    host_did = f"did:web:localhost%3A{port}"

    docs = {
        "": build_did_document(did=host_did, signing_key=root_key.public_key()),
        "dev": build_did_document(did=f"{host_did}:dev", signing_key=slug_key.public_key()),
    }

    async def resolver(slug: str):
        return docs.get(slug)

    server = DIDServer(domain=f"localhost:{port}", resolver=resolver)
    config = uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="error")
    uv_server = uvicorn.Server(config)
    task = asyncio.create_task(uv_server.serve())
    while not uv_server.started:
        await asyncio.sleep(0.01)
    yield host_did, port
    uv_server.should_exit = True
    await task


async def test_build_document_resolves_via_web_resolver(live_server):
    """build_did_document output round-trips through a live DIDServer."""
    host_did, _port = live_server
    resolver = WebResolver()
    doc = await resolver(host_did)
    assert doc.id == host_did
    slug_doc = await resolver(f"{host_did}:dev")
    assert slug_doc.id == f"{host_did}:dev"
    # cache honors max-age=60: a second resolve does not refetch
    doc2 = await resolver(host_did)
    assert doc2 is doc
    with pytest.raises(DIDNotFound):
        await resolver(f"{host_did}:missing")
    await resolver.aclose()


async def test_live_prove_verify_over_web_resolver(live_server):
    """End-to-end: commit with a did:web identity, verify via HTTP resolution."""
    from fastatp import Repo
    from fastatp.lexicon import Lexicon

    class Hello(Lexicon):
        nsid = "com.example.test.hello"
        msg: str

    host_did, _port = live_server
    repo = Repo(f"{host_did}:dev", slug_key)
    repo.put_record(Hello.nsid, "greeting", Hello(msg="hi"))
    proof = repo.prove(Hello.nsid, "greeting")

    resolver = WebResolver()
    verifier = Peer(resolver=resolver)
    record = await verifier.verify(proof, expected_signer=f"{host_did}:dev")
    assert isinstance(record, Hello)
    assert record.msg == "hi"
    await resolver.aclose()
