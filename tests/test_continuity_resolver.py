"""Twin/chain serving + the verifying ContinuityResolver.

An identity serves its pretty doc + twin head + append-only chain on a
DIDServer. A vanilla did:web resolver reads the pretty doc unchanged; the
FastATP ContinuityResolver verifies the chain and FAILS CLOSED on a domain
thief who swaps keys without a valid chain extension; an absent alias is plain
did:web (adoption never punished); strict mode requires continuity."""

import asyncio
import contextlib
import socket

import pytest
import uvicorn

from fastatp import crypto
from fastatp.did.continuity import build_pretty
from fastatp.did.resolver import ContinuityResolver, WebResolver
from fastatp.exceptions import DIDDocumentInvalid
from fastatp.identity import Identity
from fastatp.server.did_server import DIDServer


def _bind():
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0))
    return s, s.getsockname()[1]


@contextlib.asynccontextmanager
async def _serve(sock, slug_resolver, chain_resolver=None):
    server = DIDServer("continuity.test", slug_resolver, chain_resolver=chain_resolver)
    uv = uvicorn.Server(uvicorn.Config(server.app, log_level="error"))
    task = asyncio.create_task(uv.serve(sockets=[sock]))
    while not uv.started:
        await asyncio.sleep(0.01)
    try:
        yield
    finally:
        uv.should_exit = True
        await task


def _resolvers(ident, *, pretty_override=None, serve_chain=True):
    """SlugResolver + ChainResolver serving an identity's docs (with optional
    theft overrides)."""
    slug = ident.chain.slug
    pretty = pretty_override or ident.pretty_document()
    twin_head = ident.twin_document()
    chain_json = ident.chain.to_json()

    async def slug_resolver(s):
        if s == "":
            return pretty
        if s == slug:
            return twin_head
        return None

    async def chain_resolver(s):
        return chain_json if (serve_chain and s == slug) else None

    return slug_resolver, chain_resolver


async def test_vanilla_did_web_resolves_pretty_unchanged():
    sock, port = _bind()
    domain = f"127.0.0.1%3A{port}"
    ident = Identity.bootstrap(domain=domain, ephemeral_ok=True)
    slug_resolver, chain_resolver = _resolvers(ident)

    async with _serve(sock, slug_resolver, chain_resolver):
        # a plain did:web resolver ignores the twin alias entirely
        doc = await WebResolver()(f"did:web:{domain}")
    assert doc.id == f"did:web:{domain}"
    assert doc.get_verification_method().public_key_multibase == crypto.encode_multikey(
        ident.signing_key.public_key()
    )
    # it advertises the twin, but a vanilla resolver simply doesn't act on it
    assert any(a.startswith(f"did:web:{domain}:") for a in doc.also_known_as)


async def test_continuity_resolver_verifies_a_valid_chain():
    sock, port = _bind()
    domain = f"127.0.0.1%3A{port}"
    ident = Identity.bootstrap(domain=domain, ephemeral_ok=True)
    slug_resolver, chain_resolver = _resolvers(ident)

    async with _serve(sock, slug_resolver, chain_resolver):
        doc = await ContinuityResolver(WebResolver())(f"did:web:{domain}")
    assert doc is not None
    assert doc.id == f"did:web:{domain}"  # verified: slug, chain, key match


async def test_theft_swapped_keys_fails_closed():
    """A domain thief serves a pretty doc with THEIR keys but cannot extend the
    victim's chain (no rotation key). The resolver refuses."""
    sock, port = _bind()
    domain = f"127.0.0.1%3A{port}"
    ident = Identity.bootstrap(domain=domain, ephemeral_ok=True)
    thief_key = crypto.generate_keypair("k256")
    assert ident.chain is not None
    forged_pretty = build_pretty(domain, thief_key.public_key(), ident.chain.twin_did)
    slug_resolver, chain_resolver = _resolvers(ident, pretty_override=forged_pretty)

    async with _serve(sock, slug_resolver, chain_resolver):
        with pytest.raises(DIDDocumentInvalid, match="does not match the continuity chain head"):
            await ContinuityResolver(WebResolver())(f"did:web:{domain}")


async def test_missing_chain_log_fails_closed():
    """A pretty doc that advertises a twin but serves no chain fails closed."""
    sock, port = _bind()
    domain = f"127.0.0.1%3A{port}"
    ident = Identity.bootstrap(domain=domain, ephemeral_ok=True)
    slug_resolver, chain_resolver = _resolvers(ident, serve_chain=False)

    async with _serve(sock, slug_resolver, chain_resolver):
        with pytest.raises(DIDDocumentInvalid, match="chain log is unreachable"):
            await ContinuityResolver(WebResolver())(f"did:web:{domain}")


async def test_absent_twin_is_plain_did_web_and_strict_requires_it():
    """A did:web with no twin alias: default returns it plainly (adoption is
    opt-in, never punished); strict refuses it."""
    sock, port = _bind()
    domain = f"127.0.0.1%3A{port}"
    # a non-continuity identity: build a bare pretty doc with NO twin alias
    key = crypto.generate_keypair("k256")
    from fastatp.did.document import build_did_document

    bare = build_did_document(did=f"did:web:{domain}", signing_key=key.public_key())

    async def slug_resolver(s):
        return bare if s == "" else None

    async with _serve(sock, slug_resolver):
        default = await ContinuityResolver(WebResolver())(f"did:web:{domain}")
        assert default is not None
        assert default.id == f"did:web:{domain}"  # graceful degradation
        with pytest.raises(DIDDocumentInvalid, match="no twin"):
            await ContinuityResolver(WebResolver(), strict=True)(f"did:web:{domain}")


async def test_late_adoption_the_alias_appears_and_verifies():
    """Late adoption: an identity that mints a chain now serves a twin alias,
    and the continuity resolver verifies it — no penalty for adopting late."""
    sock, port = _bind()
    domain = f"127.0.0.1%3A{port}"
    ident = Identity.bootstrap(domain=domain, ephemeral_ok=True)  # chain minted "now"
    slug_resolver, chain_resolver = _resolvers(ident)

    async with _serve(sock, slug_resolver, chain_resolver):
        pretty = await WebResolver()(f"did:web:{domain}")
        assert ident.chain is not None
        assert pretty.also_known_as == [ident.chain.twin_did]  # alias now present
        verified = await ContinuityResolver(WebResolver())(f"did:web:{domain}")
        assert verified is not None
        assert verified.id == f"did:web:{domain}"
