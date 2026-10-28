"""Shelf intake + twin-only chains.

An opt-in DIDServer route accepts exactly one artifact type — a
self-certifying genesis + chain — verifying it fail-closed before the
caller's store ever sees it. Twin-only chains (no pretty alias) are the
shelved-identity shape: many identities under one neutral domain.
"""

import asyncio

import httpx
import pytest

from fastatp import crypto
from fastatp.did.continuity import ContinuityChain
from fastatp.did.document import DIDDocument, build_did_document
from fastatp.exceptions import DIDDocumentInvalid
from fastatp.server.did_server import SHELF_INTAKE_PATH, DIDServer

DOMAIN = "shelf.example"


def _mint(domain: str = DOMAIN):
    """Client-side connection-zero identity half: keys + twin-only chain."""
    signing = crypto.generate_keypair("p256")
    rotation = crypto.generate_keypair("k256")
    rotation_did = crypto.encode_did_key(rotation.public_key())
    chain = ContinuityChain.create(domain, signing.public_key(), [rotation_did], bind_pretty=False)
    return chain, rotation, rotation_did


def _rotate(chain: ContinuityChain, rotation, rotation_did: str) -> None:
    new_signing = crypto.generate_keypair("p256")
    chain.append(
        build_did_document(
            did=chain.twin_did,
            signing_key=new_signing.public_key(),
            also_known_as=[],
            rotation_keys=[rotation_did],
        ),
        rotation,
    )


def _shelf(**kwargs):
    """A neutral shelf: dict-backed store; resolver/chain_resolver serve it."""
    store: dict[str, dict] = {}

    async def resolver(slug: str) -> DIDDocument | None:
        entry = store.get(slug)
        if entry is None:
            return None
        return DIDDocument.from_json_dict(entry["chain"][-1]["document"])

    async def chain_resolver(slug: str):
        return store.get(slug)

    def shelf_store(slug: str, chain: ContinuityChain) -> None:
        store[slug] = chain.to_json()

    server = DIDServer(
        DOMAIN,
        resolver,
        chain_resolver=chain_resolver,
        shelf_store=shelf_store,
        **kwargs,
    )
    return server, store


def _client(server: DIDServer) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.app), base_url=f"http://{DOMAIN}"
    )


# --- twin-only chains ------------------------------------------------------------


def test_twin_only_chain_self_certifies():
    chain, _rot, _rot_did = _mint()
    chain.verify()
    assert chain.genesis.also_known_as == []
    assert chain.twin_did.startswith(f"did:web:{DOMAIN}:")


def test_twin_only_rotation_appends_and_verifies():
    chain, rotation, rotation_did = _mint()
    _rotate(chain, rotation, rotation_did)
    chain.verify()
    assert len(chain.pages) == 2


def test_tampered_twin_only_genesis_fails_closed():
    chain, _rot, _rot_did = _mint()
    other = crypto.encode_did_key(crypto.generate_keypair("k256").public_key())
    chain.genesis.rotation_keys[:] = [other]  # tamper: swap the rotation key
    with pytest.raises(DIDDocumentInvalid, match="slug"):
        chain.verify()


# --- the intake route ------------------------------------------------------------


async def test_valid_chain_is_accepted_and_served():
    server, store = _shelf()
    chain, _rot, _rot_did = _mint()
    async with _client(server) as client:
        response = await client.post(SHELF_INTAKE_PATH, json=chain.to_json())
        assert response.status_code == 201
        slug = response.json()["slug"]
        assert response.json()["twin"] == chain.twin_did

        served = await client.get(f"/{slug}/did.json")
        assert served.status_code == 200
        assert served.json()["id"] == chain.twin_did
        assert (await client.get(f"/{slug}/chain.json")).status_code == 200
    assert slug in store


async def test_tampered_chain_is_refused():
    server, store = _shelf()
    chain, _rot, _rot_did = _mint()
    payload = chain.to_json()
    payload["chain"][0]["document"]["rotationKeys"] = [
        crypto.encode_did_key(crypto.generate_keypair("k256").public_key())
    ]
    async with _client(server) as client:
        response = await client.post(SHELF_INTAKE_PATH, json=payload)
    assert response.status_code == 422
    assert response.json()["error"] == "InvalidChain"
    assert store == {}  # nothing reached the store


async def test_chain_for_another_domain_is_refused():
    server, store = _shelf()
    chain, _rot, _rot_did = _mint(domain="other.example")
    async with _client(server) as client:
        response = await client.post(SHELF_INTAKE_PATH, json=chain.to_json())
    assert response.status_code == 422
    assert response.json()["error"] == "WrongDomain"
    assert store == {}


async def test_rotation_reposts_a_longer_chain_and_rollback_is_refused():
    server, _store = _shelf()
    chain, rotation, rotation_did = _mint()
    genesis_only = chain.to_json()
    async with _client(server) as client:
        assert (await client.post(SHELF_INTAKE_PATH, json=genesis_only)).status_code == 201

        _rotate(chain, rotation, rotation_did)  # rotation = a LONGER chain
        assert (await client.post(SHELF_INTAKE_PATH, json=chain.to_json())).status_code == 201

        rollback = await client.post(SHELF_INTAKE_PATH, json=genesis_only)
        assert rollback.status_code == 409
        assert rollback.json()["error"] == "ChainRollback"


async def test_accept_policy_refuses():
    store: dict[str, dict] = {}

    async def resolver(slug: str) -> DIDDocument | None:
        return None

    server = DIDServer(
        DOMAIN,
        resolver,
        shelf_store=lambda slug, chain: store.__setitem__(slug, chain.to_json()),
        shelf_accept=lambda slug, chain: False,  # the shelf's sovereign "no"
    )
    chain, _rot, _rot_did = _mint()
    async with _client(server) as client:
        response = await client.post(SHELF_INTAKE_PATH, json=chain.to_json())
    assert response.status_code == 403
    assert response.json()["error"] == "NotAccepted"
    assert store == {}


async def test_malformed_body_is_refused():
    """Every syntactically-valid-but-wrong-shape body is a 400, never a 500
    non-object JSON and an empty chain are malformed input."""
    server, _store = _shelf()
    async with _client(server) as client:
        response = await client.post(SHELF_INTAKE_PATH, content=b"not json")
        assert response.status_code == 400
        garbage = await client.post(SHELF_INTAKE_PATH, json={"chain": [{"nope": 1}]})
        assert garbage.status_code == 400
        wrong_shapes: list[object] = [[], "a string", 7, None, {"chain": []}]
        for legal_but_wrong in wrong_shapes:
            response = await client.post(SHELF_INTAKE_PATH, json=legal_but_wrong)
            assert response.status_code == 400, f"{legal_but_wrong!r} must be a 400"
            assert response.json()["error"] == "MalformedChain"


async def test_concurrent_rollback_is_serialized():
    """Check-then-store is one critical section per slug: a rollback posted
    while a longer chain is mid-store must wait for the store to finish and
    then be refused, never interleave."""
    store: dict[str, dict] = {}
    release = asyncio.Event()

    async def shelf_store(slug: str, chain: ContinuityChain) -> None:
        entry = chain.to_json()
        if len(entry["chain"]) > 1:
            await release.wait()  # stall the long chain inside the store
        store[slug] = entry

    async def resolver(slug: str) -> DIDDocument | None:
        return None

    async def chain_resolver(slug: str):
        return store.get(slug)

    server = DIDServer(DOMAIN, resolver, chain_resolver=chain_resolver, shelf_store=shelf_store)
    chain, rotation, rotation_did = _mint()
    genesis_only = chain.to_json()
    _rotate(chain, rotation, rotation_did)

    async with _client(server) as client:
        long_post = asyncio.create_task(client.post(SHELF_INTAKE_PATH, json=chain.to_json()))
        await asyncio.sleep(0.05)  # long post is now stalled inside the lock
        rollback_post = asyncio.create_task(client.post(SHELF_INTAKE_PATH, json=genesis_only))
        await asyncio.sleep(0.05)  # rollback must be waiting, not stored
        assert store == {}
        release.set()
        assert (await long_post).status_code == 201
        rollback = await rollback_post
    assert rollback.status_code == 409
    assert len(store[chain.slug]["chain"]) == 2  # the longer chain survived


async def test_intake_is_off_by_default():
    async def resolver(slug: str) -> DIDDocument | None:
        return None

    server = DIDServer(DOMAIN, resolver)  # no shelf_store: read-only as ever
    async with _client(server) as client:
        response = await client.post(SHELF_INTAKE_PATH, json={})
    assert response.status_code == 404


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
