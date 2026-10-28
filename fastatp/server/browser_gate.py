"""The browser admission gate: signed-challenge reads over TLS.

Browsers cannot join the WireGuard plane, so the doorman's default identity
(source tunnel IP) never resolves for them. This module supplies the second
enforcement plane through the seam the doorman reserves: a ``CallerResolver``
that verifies a **single-use nonce challenge** signed by the presenting pen
and maps pen → DID by walking the live delegation chain (presence
semantics). Nothing is minted, nothing survives the connection, nothing can
be replayed — signature admission, not session issuance.

Flow (query params because browser WebSockets cannot set headers; nothing in
them is secret — the nonce is single-use and the signature is not replayable):

1. ``GET /xrpc/dev.fastatp.auth.getChallenge`` → ``{"nonce": ...}``.
2. The pen signs the nonce's ASCII bytes (WebCrypto, non-extractable).
3. The gated read connects with ``?pen=did:key:..&iss=<root did>&nonce=..&
   sig=<base64url>`` alongside the usual ``did``/``cursor`` params.
4. The resolver consumes the nonce (single use), verifies the signature, and
   walks pen → ``iss`` over the delegation chain. Any failure resolves to no
   identity, and the doorman refuses — fail closed. With ``iss`` omitted the
   pen *is* the caller (a documentless ``did:key`` holding its own
   capability needs no delegation).

The doorman's membership check is untouched: this plane only answers *who is
asking*. It is TLS + signature admission — strictly weaker than WireGuard
(absence = refused requests, not unreachability); the builder's HTTPS/WSS
termination is required in front, and native peers keep the WG plane.
"""

from __future__ import annotations

import base64
import binascii
import inspect
import secrets
import time
from collections import OrderedDict

from starlette.requests import HTTPConnection, Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from fastatp.authz.delegation import RepoSource, walk_delegation_chain
from fastatp.crypto import decode_did_key, verify_signature
from fastatp.server.peer_server import CallerResolver, PeerServer

CHALLENGE_PATH = "/xrpc/dev.fastatp.auth.getChallenge"

_DEFAULT_TTL = 60.0
_MAX_PENDING = 4096  # LRU cap: a nonce flood evicts oldest pending (fail closed)


def compose_caller_resolvers(*resolvers: CallerResolver) -> CallerResolver:
    """First resolver to name a DID wins; all silent → no identity.

    A server hosting both native and browser subscribers composes planes:
    tunnel identity when the connection has one, signed challenge otherwise.
    """

    async def resolve(conn: HTTPConnection) -> str | None:
        for resolver in resolvers:
            result = resolver(conn)
            if inspect.isawaitable(result):
                result = await result
            if result is not None:
                return result
        return None

    return resolve


class BrowserGate:
    """Issues single-use challenges and resolves signed presentations to DIDs.

    ``repo_source`` is where delegation presence is checked (default when
    installed on a :class:`PeerServer`: that server's hosted repos). ``ttl``
    bounds how long an unanswered nonce stays redeemable.
    """

    def __init__(self, repo_source: RepoSource | None = None, *, ttl: float = _DEFAULT_TTL):
        self._repo_source = repo_source
        self._ttl = ttl
        self._pending: OrderedDict[str, float] = OrderedDict()  # nonce -> deadline
        self._installed = False

    # --- challenge lifecycle ----------------------------------------------------

    def issue(self) -> str:
        """Mint a single-use nonce (also the route handler's core)."""
        self._prune()
        nonce = secrets.token_urlsafe(32)
        self._pending[nonce] = time.monotonic() + self._ttl
        while len(self._pending) > _MAX_PENDING:
            self._pending.popitem(last=False)
        return nonce

    def _consume(self, nonce: str) -> bool:
        """True exactly once per live nonce; expired/unknown/repeated = False."""
        deadline = self._pending.pop(nonce, None)
        return deadline is not None and deadline > time.monotonic()

    def _prune(self) -> None:
        now = time.monotonic()
        while self._pending:
            nonce, deadline = next(iter(self._pending.items()))
            if deadline > now:
                break
            self._pending.pop(nonce, None)

    async def _challenge_endpoint(self, request: Request) -> JSONResponse:
        return JSONResponse({"nonce": self.issue()}, headers={"Cache-Control": "no-store"})

    def route(self) -> Route:
        return Route(CHALLENGE_PATH, self._challenge_endpoint, methods=["GET"])

    # --- the CallerResolver -----------------------------------------------------

    async def resolve(self, conn: HTTPConnection) -> str | None:
        """Signed presentation → caller DID, or ``None`` (always fail closed).

        Every failure mode — missing params, unknown/expired/replayed nonce,
        undecodable pen, bad signature, absent delegation chain — is the same
        silent ``None``; the doorman turns it into the refusal.
        """
        params = conn.query_params
        pen = params.get("pen")
        nonce = params.get("nonce")
        sig = params.get("sig")
        if not pen or not nonce or not sig:
            return None
        if not self._consume(nonce):  # consume FIRST: a bad signature still burns it
            return None
        try:
            public_key = decode_did_key(pen)
            signature = base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4))
        except (ValueError, binascii.Error):
            return None
        if not verify_signature(public_key, nonce.encode("ascii"), signature):
            return None
        issuer = params.get("iss")
        if issuer is None or issuer == pen:
            return pen  # a documentless did:key speaks only for itself
        if self._repo_source is None:
            return None
        chain = await walk_delegation_chain(pen, issuer, self._repo_source)
        return issuer if chain is not None else None

    # --- wiring -------------------------------------------------------------------

    def install(self, server: PeerServer) -> None:
        """Opt this server into browser reads: add the challenge route and
        compose this gate behind the server's existing identity resolver
        (tunnel identity still wins when the connection has one). One gate,
        one server — a second install would stack duplicate routes and
        resolver layers, so it refuses."""
        if self._installed:
            raise ValueError("this BrowserGate is already installed; create one per server")
        self._installed = True
        if self._repo_source is None:
            self._repo_source = server.hosted_repo
        server.app.router.routes.append(self.route())
        server.identity_resolver = compose_caller_resolvers(server.identity_resolver, self.resolve)
