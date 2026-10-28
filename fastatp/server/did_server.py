"""DIDServer: a Starlette app serving DID documents.

Register / rotate / revoke are the caller's provisioning concern; this app
reads through the supplied resolver. Run standalone with ``.run()`` or mount
``.app`` into any Starlette/FastAPI application.

One deliberate exception to "read-only": the opt-in,
off-by-default **shelf intake** route accepts exactly one artifact type — a
self-certifying genesis + continuity chain that fully validates
before the caller-supplied store ever sees it. Anyone can run a neutral
identity shelf with stock FastATP; the package never ships a default shelf
domain (the anti-registry rule).
"""

from __future__ import annotations

import asyncio
import inspect
import json
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from fastatp.did.continuity import ContinuityChain
from fastatp.did.document import DIDDocument
from fastatp.exceptions import DIDDocumentInvalid

SlugResolver = Callable[[str], Awaitable[DIDDocument | None]]
ChainResolver = Callable[[str], Awaitable["dict[str, Any] | None"]]

ShelfStore = Callable[[str, ContinuityChain], "None | Awaitable[None]"]
"""``store(slug, verified_chain)`` — persist an accepted chain so the caller's
``resolver``/``chain_resolver`` can serve it. Called only after the chain fully
self-certifies, the domain matches, rollback is refused, and the accept policy
passes."""

ShelfAccept = Callable[[str, ContinuityChain], "bool | Awaitable[bool]"]
"""``accept(slug, chain) -> bool`` — the shelf's own sovereign admission policy
(rate limit, size cap, payment, known users). Edge business logic, never
substrate; default accepts everything that self-certifies."""

SHELF_INTAKE_PATH = "/xrpc/dev.fastatp.shelf.putChain"

_CACHE_CONTROL = "max-age=60"
_MAX_CHAIN_BYTES = 1 << 20  # a chain is a few KB; a megabyte is already absurd
_MAX_INTAKE_LOCKS = 4096  # LRU cap on per-slug intake locks (spam bound)


class DIDServer:
    """Serves ``/.well-known/did.json`` (root DID, slug ``""``) and
    ``/{slug}/did.json`` (path-segmented DIDs) from a caller-supplied resolver.

    For self-certifying did:web continuity an optional
    ``chain_resolver`` also serves ``/{slug}/chain.json`` — the append-only
    ContinuityChain log beside the twin's head document.

    Passing ``shelf_store`` opts in to the shelf-intake route:
    ``POST {SHELF_INTAKE_PATH}`` with a chain JSON body. The intake verifies
    the chain self-certifies for **this** server's domain (fail closed),
    refuses rollbacks when a ``chain_resolver`` can see the currently served
    chain, runs ``shelf_accept`` (default: accept), then hands the chain to
    ``shelf_store``. Without ``shelf_store`` the route does not exist.
    """

    def __init__(
        self,
        domain: str,
        resolver: SlugResolver,
        *,
        chain_resolver: ChainResolver | None = None,
        shelf_store: ShelfStore | None = None,
        shelf_accept: ShelfAccept | None = None,
    ) -> None:
        self.domain = domain
        self.resolver = resolver
        self.chain_resolver = chain_resolver
        self.shelf_store = shelf_store
        self.shelf_accept = shelf_accept
        self._intake_locks: OrderedDict[str, asyncio.Lock] = OrderedDict()
        routes = [
            Route("/.well-known/did.json", self._serve_root),
            Route("/{slug}/did.json", self._serve_slug),
        ]
        if chain_resolver is not None:
            routes.append(Route("/{slug}/chain.json", self._serve_chain))
        if shelf_store is not None:
            routes.append(Route(SHELF_INTAKE_PATH, self._shelf_intake, methods=["POST"]))
        self.app = Starlette(routes=routes)

    async def _serve_chain(self, request: Request) -> Response:
        assert self.chain_resolver is not None
        log = await self.chain_resolver(request.path_params["slug"])
        if log is None:
            return JSONResponse(
                {"error": "not_found", "message": "no continuity chain for this path"},
                status_code=404,
                headers={"Cache-Control": _CACHE_CONTROL},
            )
        return JSONResponse(log, headers={"Cache-Control": _CACHE_CONTROL})

    async def _respond(self, slug: str) -> Response:
        doc = await self.resolver(slug)
        if doc is None:
            return JSONResponse(
                {"error": "not_found", "message": "no DID document for this path"},
                status_code=404,
                headers={"Cache-Control": _CACHE_CONTROL},
            )
        return JSONResponse(
            doc.to_json_dict(),
            media_type="application/json",
            headers={"Cache-Control": _CACHE_CONTROL},
        )

    async def _serve_root(self, request: Request) -> Response:
        return await self._respond("")

    async def _serve_slug(self, request: Request) -> Response:
        return await self._respond(request.path_params["slug"])

    # --- shelf intake (opt-in, off by default) ---------------------------------

    async def _shelf_intake(self, request: Request) -> Response:
        """Accept a self-certifying genesis + chain — and nothing else.

        The artifact carries its own proof (the slug commits to the genesis;
        every page turn is rotation-signed), so the shelf needs no trust in
        the poster. Every check fails closed; a rotation arrives as a LONGER
        chain for the same slug, never an in-place edit.
        """
        assert self.shelf_store is not None
        body = await request.body()
        if len(body) > _MAX_CHAIN_BYTES:
            return _shelf_error(413, "ChainTooLarge", "chain body exceeds the size cap")
        try:
            # AttributeError: legal JSON that isn't an object (a bare array,
            # string, number, null); DIDDocumentInvalid: an empty chain —
            # both are malformed input, never a server error
            chain = ContinuityChain.from_json(json.loads(body))
        except (ValueError, KeyError, TypeError, AttributeError, DIDDocumentInvalid) as exc:
            return _shelf_error(400, "MalformedChain", f"unparseable chain document: {exc}")
        try:
            chain.verify()  # the self-certification, fail closed
        except DIDDocumentInvalid as exc:
            return _shelf_error(422, "InvalidChain", str(exc))
        slug = chain.slug
        if chain.twin_did != f"did:web:{self.domain}:{slug}":
            return _shelf_error(
                422,
                "WrongDomain",
                f"chain is minted for {chain.twin_did!r}, not this shelf ({self.domain!r})",
            )
        # rollback-check + accept + store are one critical section per slug:
        # without the lock, two concurrent posts could both pass the length
        # check and the shorter (older) chain could store last
        async with self._intake_lock(slug):
            if self.chain_resolver is not None:
                existing = await self.chain_resolver(slug)
                if existing is not None and len(existing.get("chain", [])) > len(chain.pages):
                    return _shelf_error(
                        409, "ChainRollback", "a longer chain is already served for this slug"
                    )
            if self.shelf_accept is not None:
                verdict = self.shelf_accept(slug, chain)
                if inspect.isawaitable(verdict):
                    verdict = await verdict
                if not verdict:
                    return _shelf_error(403, "NotAccepted", "the shelf declined this chain")
            stored = self.shelf_store(slug, chain)
            if inspect.isawaitable(stored):
                await stored
        return JSONResponse({"twin": chain.twin_did, "slug": slug}, status_code=201)

    def _intake_lock(self, slug: str) -> asyncio.Lock:
        """Per-slug serialization for the intake's check-then-store window.

        The map is LRU-bounded so slug spam cannot grow it without limit;
        an in-flight (locked) entry is never evicted.
        """
        lock = self._intake_locks.get(slug)
        if lock is None:
            lock = asyncio.Lock()
            self._intake_locks[slug] = lock
        self._intake_locks.move_to_end(slug)
        while len(self._intake_locks) > _MAX_INTAKE_LOCKS:
            oldest_slug, oldest_lock = next(iter(self._intake_locks.items()))
            if oldest_lock.locked():
                break
            self._intake_locks.pop(oldest_slug, None)
        return lock

    def run(self, host: str = "127.0.0.1", port: int = 8000, **kwargs: Any) -> None:
        """Run standalone under uvicorn (blocking)."""
        import uvicorn

        uvicorn.run(self.app, host=host, port=port, **kwargs)


def _shelf_error(status: int, error: str, message: str) -> JSONResponse:
    return JSONResponse({"error": error, "message": message}, status_code=status)
