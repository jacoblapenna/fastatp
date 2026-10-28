"""PeerApp — the tool/agent/workload framework (v0.5).

A FastAPI-shaped layer over the shipped primitives (``Peer.verify``,
``PeerServer``, ``RepoMirror``, ``WireGuardReconciler``). It dispatches *any*
user-defined ``Lexicon`` to a handler, lets handlers reply with signed records,
and exposes admission (the bilateral kill switch) as ``admit`` / ``evict``.

It ships **mechanism, never application schemas** (see ``docs/PEERAPP.md`` and
``PHILOSOPHY.md``): the developer defines their own request/response lexicons
in their own namespace; PeerApp only routes and emits. One DID per app instance
(the DID/repo is the only enforceable listener boundary); run several PeerApps
in one process for several surfaces.

Design decisions:
- Handler execution is decoupled from firehose ingestion (a slow handler never
  stalls the mirror read loop, so it cannot trigger ``ConsumerTooSlow``
  eviction). Handlers run concurrently across producers; per-producer commit
  ordering is opt-in (``@app.on(..., ordered=True)``).
- Redelivery (cursor resume / checkpoint replay) is de-duplicated by record CID
  with an in-memory seen-set: **not durable, not exactly-once**. Durable dedup
  is the handler's job (the idempotency dial in ``docs/PEERAPP.md``).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
from collections import OrderedDict
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from fastatp.authz.attestation import Attestation, build_attestation
from fastatp.authz.capability import ACTION_SUBSCRIBE
from fastatp.authz.reconciler import WireGuardReconciler
from fastatp.authz.wireguard import MemoryWireGuardInterface, generate_wireguard_keypair
from fastatp.cid import CID
from fastatp.crypto import encode_did_key, generate_keypair
from fastatp.exceptions import CommitGap, FastATPError, FirehoseErrorFrame
from fastatp.lexicon import Lexicon, parse_record
from fastatp.repo.repo import CommitEvent, RecordLike, Repo
from fastatp.repo.sync import RepoMirror, decode_frame

if TYPE_CHECKING:
    from fastatp.server.peer_server import RepoHandle

log = logging.getLogger("fastatp.peerapp")

Handler = Callable[["Lexicon", "Envelope"], Awaitable[None]]
PushPolicy = Callable[[str, str, int], bool]
"""``policy(issuer_did, nsid, proof_size) -> bool`` — a board's edge decision
to accept a pushed record (rate-limit / size-cap / filter). Default: accept."""

PUSH_INTAKE_PATH = "/xrpc/dev.fastatp.repo.pushProof"
_ATTEST_CACHE_MAX = 4096  # LRU cap on the attestation discovery cache


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class Envelope:
    """Verified context handed to every handler.

    The record is already fully verified (commit signature, MST membership,
    rotation-key ban) and parsed to its Lexicon instance before a handler runs.
    ``context_id`` is read generically off the record if present — PeerApp
    never assumes a field shape.
    """

    issuer_did: str
    record_cid: CID
    received_at: str
    record: Lexicon
    collection: str
    rkey: str

    @property
    def context_id(self) -> str | None:
        return getattr(self.record, "context_id", None)


@dataclass
class PrivateChannel:
    """A live ephemeral private-response repo.

    A fresh ``did:key`` + repo + WG identity, hosted gated with a TTL, admitting
    exactly one counterpart by public key. The reply is written into it and a
    signed :class:`Attestation` points at it; when the TTL passes the host makes
    it unroutable and cuts subscribers (producer-authoritative lifetime, ADR
    0015). Writes go only through this handle — never ``emit(did=...)``.
    """

    handle: RepoHandle
    ephemeral_did: str
    wg_public_key: str
    # repr=False: never let the channel's WG private key leak into a log,
    # traceback, or str(channel) (a service opens one of these per request)
    wg_private_key: str = field(repr=False)
    tunnel_ip: str
    endpoint_host: str | None
    expires_at: str
    issuer_did: str

    @property
    def did(self) -> str:
        return self.ephemeral_did

    def put_record(self, collection: str, rkey: str, record: RecordLike) -> CommitEvent:
        return self.handle.put_record(collection, rkey, record)

    def close(self) -> None:
        """Retire the channel now (before its TTL)."""
        self.handle.unregister()


@dataclass
class _Registration:
    handler: Handler
    ordered: bool
    admit_issuer: bool
    advertise: bool
    lexicon_cls: type[Lexicon]


class FrameSource:
    """A source of firehose frame bytes for one producer.

    ``open()`` establishes the subscription (so callers can subscribe-before-
    emit); iterating yields raw frame bytes; ``close()`` tears it down. The
    default production source is ``WebSocketFrameSource``; tests inject an
    in-process source (optionally gated by a simulated WireGuard interface).
    """

    async def open(self) -> None:  # pragma: no cover - interface
        ...

    def __aiter__(self) -> AsyncIterator[bytes]:  # pragma: no cover - interface
        raise NotImplementedError

    async def close(self) -> None:  # pragma: no cover - interface
        ...

    async def checkpoint(self, did: str) -> bytes | None:
        """Fetch a full current-state CAR for ``did`` (the ``getRepo`` body).

        A #sync frame carries the commit block only (sync spec); a consumer
        that finds itself behind re-fetches state through this seam. ``None``
        means this transport cannot (the mirror then stays flagged
        out-of-sync until a checkpoint arrives some other way).
        """
        return None

    def advance(self, cursor: int) -> None:
        """Record the last successfully processed seq as the resume point.

        The dispatcher calls this after each verified frame so a reconnecting
        source resumes from where verification actually got to, never from
        the construction-time cursor (the stale-cursor replay bug; pattern
        from MarshalX/atproto@071a6fb, made automatic here).
        """


class WebSocketFrameSource(FrameSource):
    """Production frame source: subscribe to a producer's firehose over WS.

    ``url`` is the ``subscribeRepos`` WebSocket endpoint. Operationally
    hardened along the lines production atproto consumers learned the hard
    way (patterns from MarshalX/atproto — see docs/COMPARISON-ATPROTO-SDK.md):

    - transport failures reconnect with capped exponential backoff + jitter
      (never on decode failures — reconnecting on a bad frame replays the
      same bad frame forever);
    - reconnects resume from the last :meth:`advance`'d cursor, not the
      construction-time one;
    - an inactivity timeout catches alive-but-silent servers that WS pings
      cannot (the connection is healthy; the producer just stopped sending);
    - ``max_size`` defaults to the sync spec's frame ceiling, not the
      websockets library's 1 MiB default (which drops legitimate commits and
      masquerades as decode errors).
    """

    #: transport-shaped failures that warrant a backoff + reconnect; anything
    #: else propagates to the caller (a bug should be loud, not retried)
    _RECONNECT_ERRORS: tuple[type[BaseException], ...] = (
        ConnectionError,
        TimeoutError,
        OSError,
    )

    def __init__(
        self,
        url: str,
        start_cursor: int | None = None,
        *,
        max_size: int = 5 * 1024 * 1024,  # the sync spec's 5 MB frame ceiling
        recv_timeout: float | None = 300.0,
        max_backoff: float = 64.0,
    ) -> None:
        self.url = url
        self.cursor = start_cursor
        self.max_size = max_size
        self.recv_timeout = recv_timeout
        self.max_backoff = max_backoff
        self._ws: Any = None
        self._closed = False

    def _full_url(self) -> str:
        if self.cursor is None:
            return self.url
        sep = "&" if "?" in self.url else "?"
        return f"{self.url}{sep}cursor={self.cursor}"

    def advance(self, cursor: int) -> None:
        self.cursor = cursor

    async def _connect(self) -> None:
        import websockets

        self._ws = await websockets.connect(self._full_url(), max_size=self.max_size)

    async def open(self) -> None:
        self._closed = False
        await self._connect()

    def _reconnectable(self, e: BaseException) -> bool:
        from websockets import exceptions as ws_exc

        return isinstance(
            e,
            (
                *self._RECONNECT_ERRORS,
                ws_exc.ConnectionClosedError,
                ws_exc.InvalidHandshake,
                ws_exc.ProtocolError,
                # an oversized frame closes the connection; resume past it
                # rather than dying (it will fail verification downstream if
                # it mattered — the checkpoint seam recovers state)
                getattr(ws_exc, "PayloadTooBig", ()),
            ),
        )

    async def __aiter__(self) -> AsyncIterator[bytes]:
        from websockets import exceptions as ws_exc

        assert self._ws is not None, "open() before iterating"
        attempt = 0
        while not self._closed:
            try:
                while not self._closed:
                    message = await (
                        asyncio.wait_for(self._ws.recv(), self.recv_timeout)
                        if self.recv_timeout is not None
                        else self._ws.recv()
                    )
                    attempt = 0  # a delivered frame proves the link is live
                    if isinstance(message, bytes):
                        yield message
            except ws_exc.ConnectionClosedOK:
                return  # clean shutdown from the producer
            except asyncio.TimeoutError:
                log.warning(
                    "peerapp: no frame from %s in %.0fs; reconnecting (alive-but-"
                    "silent servers are invisible to WS pings)",
                    self.url,
                    self.recv_timeout,
                )
            except Exception as e:
                if self._closed:
                    return
                if not self._reconnectable(e):
                    raise
                log.warning("peerapp: firehose transport error (%s); reconnecting", e)
            if self._closed:
                return
            # capped exponential backoff with jitter; resumes at self.cursor,
            # which advance() has been keeping fresh
            delay = min(2.0**attempt, self.max_backoff) + random.uniform(0, 0.5)
            attempt += 1
            await asyncio.sleep(delay)
            try:
                await self._connect()
            except Exception as e:  # connect failures loop back into backoff
                log.warning("peerapp: firehose reconnect failed (%s); backing off", e)

    async def close(self) -> None:
        self._closed = True  # wins over any pending backoff/reconnect
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    async def checkpoint(self, did: str) -> bytes | None:
        """Fetch getRepo over HTTP from the same host serving the firehose."""
        import httpx

        url = self.url
        if url.startswith("ws://"):
            url = "http://" + url[len("ws://") :]
        elif url.startswith("wss://"):
            url = "https://" + url[len("wss://") :]
        url = url.split("?", 1)[0]
        if "subscribeRepos" not in url:
            return None
        url = url.replace("subscribeRepos", "getRepo")
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, params={"did": did})
            resp.raise_for_status()
            return resp.content


class PeerApp:
    """One DID's peer + PeerServer + reconciler, wrapped as a dispatch app."""

    def __init__(
        self,
        server: Any,
        reconciler: Any = None,
        *,
        resolver: Any = None,
        max_concurrency: int = 32,
        queue_size: int = 256,
        dedupe: bool = True,
        dedupe_limit: int = 100_000,
        wg_interface: Any = None,
        channel_endpoint: str | None = None,
    ) -> None:
        self.server = server
        self.peer = server.peer
        self.did = server.repo.did
        self.reconciler = reconciler
        self.resolver = resolver or (reconciler.resolver if reconciler else None)
        # shared WireGuard enforcement interface for private channels:
        # one interface, per-peer allowed-ips — never an interface per channel
        self._wg_interface = wg_interface or (
            reconciler.interface if reconciler else MemoryWireGuardInterface()
        )
        self._channel_endpoint = channel_endpoint
        self._channel_ip_counter = 0
        # discovery cache: shipped Attestations seen inbound, by context_id (0015),
        # LRU-bounded so a firehose of attestations can't grow it without bound
        self._attestations: OrderedDict[str, list[tuple[str, Attestation]]] = OrderedDict()
        self._attestation_waiters: dict[tuple[str, str], asyncio.Future[Attestation]] = {}
        self._registrations: dict[str, _Registration] = {}
        self._followed: dict[str, tuple[asyncio.Task[None], FrameSource]] = {}
        self._workers: dict[str, asyncio.Task[None]] = {}
        self._queues: dict[str, asyncio.Queue[Envelope | None]] = {}
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._queue_size = queue_size
        self._dedupe = dedupe
        self._dedupe_limit = dedupe_limit
        self._seen: OrderedDict[bytes, None] = OrderedDict()
        self._waiters: dict[tuple[str, str], asyncio.Future[Lexicon]] = {}
        self._push_policy: PushPolicy | None = None
        self._push_verifier: Any = None
        self._closed = False

    # --- registration ---------------------------------------------------------

    def on(
        self,
        lexicon_cls: type[Lexicon],
        *,
        ordered: bool = False,
        admit_issuer: bool = False,
        advertise: bool = False,
    ) -> Callable[[Handler], Handler]:
        """Register ``handler`` for records of ``lexicon_cls`` (keyed on its
        ``$type``). Per-app, not the process-global registry."""
        nsid = lexicon_cls.nsid
        if not nsid:
            raise ValueError("@app.on requires a Lexicon subclass with an nsid")

        def decorator(handler: Handler) -> Handler:
            self._registrations[nsid] = _Registration(
                handler=handler,
                ordered=ordered,
                admit_issuer=admit_issuer,
                advertise=advertise,
                lexicon_cls=lexicon_cls,
            )
            return handler

        return decorator

    # --- producing ------------------------------------------------------------

    def emit(self, collection: str, rkey: str, record: RecordLike) -> CommitEvent:
        """Write a record into this app's repo and broadcast it on the firehose."""
        return self.server.put_record(collection, rkey, record)

    async def admit(self, did: str, **grant_opts: Any) -> Any:
        """Let ``did`` reach this app's firehose (WireGuard grant + capability)."""
        if self.reconciler is None:
            raise ValueError("admit requires a reconciler")
        return await self.reconciler.grant(did, **grant_opts)

    async def evict(self, did: str, *, reason: str = "revoked") -> Any:
        """Cut ``did`` off — signed revocation + live WireGuard removal."""
        if self.reconciler is None:
            raise ValueError("evict requires a reconciler")
        return await self.reconciler.revoke(did, reason=reason)

    def publish_schemas(self) -> list[dict[str, Any]]:
        """Publish a schema record for every ``advertise=True`` handler — and
        only those (per-lexicon opt-in, never publish-everything). Returns the
        emitted ``lexicon: 1`` docs so they can also be posted to a board."""
        from fastatp.tid import generate_tid

        published = []
        for reg in self._registrations.values():
            if not reg.advertise:
                continue
            doc = reg.lexicon_cls.to_lexicon_json()
            self.emit("com.atproto.lexicon.schema", generate_tid(), doc)
            published.append(doc)
        return published

    # --- private-response channels -----------------------------------------------

    async def open_private_channel(
        self,
        issuer_did: str,
        *,
        channel_tunnel_ip: str,
        issuer_wg_public_key: str | None = None,
        issuer_tunnel_ip: str | None = None,
        endpoint_host: str | None = None,
        ttl: float = 600.0,
    ) -> PrivateChannel:
        """Spin an ephemeral ``did:key`` reply repo that admits only ``issuer_did``.

        Mints a fresh signing key + WG keypair + in-memory repo, registers it
        gated with ``ttl``, and admits the issuer **by public key**
        into the *channel* repo so the doorman lets exactly that one peer read
        it. ``channel_tunnel_ip`` is the ``/32`` the channel's firehose answers
        on (what the attestation points at); ``issuer_wg_public_key`` +
        ``issuer_tunnel_ip`` are the documentless requester's coordinates (from
        its want ad / self-attestation). A ``did:web`` issuer is admitted by
        resolving its document instead (omit the ``issuer_wg_*`` args).
        """
        signing_key = generate_keypair("k256")
        ephemeral_did = encode_did_key(signing_key.public_key())
        repo = Repo(ephemeral_did, signing_key)
        handle = self.server.register(repo, public=False, ttl=ttl)

        recon = WireGuardReconciler(handle, self._wg_interface, self._channel_resolver())
        if issuer_did.startswith("did:key:"):
            if issuer_wg_public_key is None or issuer_tunnel_ip is None:
                raise ValueError(
                    "a did:key issuer must be admitted by public key: pass "
                    "issuer_wg_public_key and issuer_tunnel_ip"
                )
            await recon.grant(
                issuer_did,
                wg_public_key=issuer_wg_public_key,
                tunnel_ip=issuer_tunnel_ip,
                actions=(ACTION_SUBSCRIBE,),
            )
        else:
            await recon.grant(issuer_did, actions=(ACTION_SUBSCRIBE,))  # resolve the doc

        wg_private, wg_public = generate_wireguard_keypair()
        expires_at = _iso(datetime.now(timezone.utc) + timedelta(seconds=ttl))
        channel = PrivateChannel(
            handle=handle,
            ephemeral_did=ephemeral_did,
            wg_public_key=wg_public,
            wg_private_key=wg_private,
            tunnel_ip=channel_tunnel_ip,
            endpoint_host=endpoint_host,
            expires_at=expires_at,
            issuer_did=issuer_did,
        )
        return channel

    async def attest(
        self, channel: PrivateChannel, context_id: str, *, to: str | None = None
    ) -> Attestation:
        """Publish a signed :class:`Attestation` pointing at ``channel``.

        Built and signed by this app's **stable** DID (its commit signature is
        the anti-spoof binding), it is emitted on the app's public firehose. If
        ``to`` (a board push-intake URL) is given, the same signed record is
        also pushed there — how a fully **private** service (no reachable public
        firehose) answers a board want ad. Returns the attestation.
        """
        from fastatp.tid import generate_tid

        attestation = build_attestation(
            context_id=context_id,
            ephemeral_did=channel.ephemeral_did,
            wg_public_key=channel.wg_public_key,
            tunnel_ip=channel.tunnel_ip,
            expires_at=channel.expires_at,
            host=channel.endpoint_host,
        )
        rkey = generate_tid()
        self.emit(Attestation.nsid, rkey, attestation)
        if to is not None:
            await push_proof(to, self.server.repo.prove(Attestation.nsid, rkey))
        return attestation

    async def reply_private(
        self,
        ctx: Envelope,
        record: RecordLike,
        *,
        ttl: float = 600.0,
        to: str | None = None,
        self_attestation_timeout: float = 2.0,
    ) -> PrivateChannel:
        """Answer a request privately in one call.

        Spins an ephemeral channel that admits **only** ``ctx``'s issuer (by the
        WG coordinates in the requester's self-attestation, discovered from the
        cache), writes ``record`` into it, and publishes a signed attestation
        binding the reply channel to ``ctx.context_id``. The developer never
        touches ``did:key``, WireGuard, or attestations.

        ``to`` is an optional board push-intake URL: a fully private service
        (no reachable public firehose) pushes its engagement attestation to the
        board the requester is watching. Returns the live channel.
        """
        from fastatp.tid import generate_tid

        if ctx.context_id is None:
            raise ValueError("reply_private needs a request carrying a context_id to correlate on")
        self_att = self._find_attestation(ctx.context_id, issuer_did=ctx.issuer_did)
        if self_att is None and self_attestation_timeout > 0:
            # the self-attestation is often pushed alongside the request; give it
            # a moment to land rather than depending on cross-record arrival order
            with contextlib.suppress(TimeoutError):
                self_att = await self._await_attestation(
                    ctx.context_id, issuer_did=ctx.issuer_did, timeout=self_attestation_timeout
                )
        if self_att is None:
            raise ValueError(
                f"no self-attestation from {ctx.issuer_did!r} for context "
                f"{ctx.context_id!r}: the requester must publish its channel coordinates "
                "(WG key + reachability) before it can be admitted by key"
            )
        info = self_att.peer_info()
        tunnel_ip, endpoint_host = self._allocate_channel_address()
        channel = await self.open_private_channel(
            ctx.issuer_did,
            channel_tunnel_ip=tunnel_ip,
            issuer_wg_public_key=info.public_key,
            issuer_tunnel_ip=info.tunnel_ip,
            endpoint_host=endpoint_host,
            ttl=ttl,
        )
        collection = type(record).nsid if isinstance(record, Lexicon) else record["$type"]
        channel.put_record(collection, generate_tid(), record)
        await self.attest(channel, ctx.context_id, to=to)
        return channel

    async def follow_attested(
        self,
        attestation: Attestation,
        *,
        firehose_url: str | None = None,
        source: FrameSource | None = None,
        start_cursor: int | None = 0,
    ) -> str:
        """Reach the channel an :class:`Attestation` points at, and follow it.

        Adds the channel as a WireGuard peer **by public key** (from the
        attestation) so it is reachable, then subscribes to its
        ephemeral firehose (routed by ``?did=``). The attestation was already
        verified against the poster's DID when it was received; here we only
        refuse an expired one (consumer-advisory TTL). Returns the
        ephemeral DID now being followed.
        """
        from fastatp.server.peer_server import FIREHOSE_PATH

        if attestation.is_expired():
            raise ValueError(f"attestation for {attestation.context_id!r} has expired")
        info = attestation.peer_info()
        self._wg_interface.add_peer(info.public_key, info.allowed_ips)  # reachability

        if source is None:
            url = firehose_url
            if url is None:
                if not info.endpoint:
                    raise ValueError(
                        "attestation has no endpoint host; pass firehose_url to reach the channel"
                    )
                url = f"ws://{info.endpoint}{FIREHOSE_PATH}"
            sep = "&" if "?" in url else "?"
            firehose_url = f"{url}{sep}did={attestation.ephemeral_did}"
        await self.follow(
            attestation.ephemeral_did,
            source=source,
            firehose_url=firehose_url,
            start_cursor=start_cursor,
        )
        return attestation.ephemeral_did

    def _allocate_channel_address(self) -> tuple[str, str | None]:
        """A fresh channel tunnel ``/32`` + the configured endpoint host.

        Sequential from ``10.99.0.1`` by default; a real deployment either sets
        ``channel_endpoint`` (the reachable WG endpoint) or overrides the coords
        per channel via ``open_private_channel`` directly.
        """
        self._channel_ip_counter += 1
        n = self._channel_ip_counter
        return f"10.99.{(n >> 8) & 0xFF}.{n & 0xFF}", self._channel_endpoint

    def _cache_attestation(self, issuer_did: str, attestation: Attestation) -> None:
        bucket = self._attestations.setdefault(attestation.context_id, [])
        self._attestations.move_to_end(attestation.context_id)
        # replace an existing entry from the same issuer (freshest wins)
        bucket[:] = [e for e in bucket if e[0] != issuer_did]
        bucket.append((issuer_did, attestation))
        while len(self._attestations) > _ATTEST_CACHE_MAX:
            self._attestations.popitem(last=False)
        # wake a call(private=True) waiting on this engagement
        waiter = self._attestation_waiters.get((attestation.context_id, issuer_did))
        if waiter is not None and not waiter.done():
            waiter.set_result(attestation)

    async def _await_attestation(
        self, context_id: str, *, issuer_did: str, timeout: float | None
    ) -> Attestation:
        existing = self._find_attestation(context_id, issuer_did=issuer_did)
        if existing is not None:
            return existing
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Attestation] = loop.create_future()
        self._attestation_waiters[(context_id, issuer_did)] = future
        try:
            return await asyncio.wait_for(future, timeout)
        finally:
            self._attestation_waiters.pop((context_id, issuer_did), None)

    def _find_attestation(
        self, context_id: str, *, issuer_did: str | None = None, now: datetime | None = None
    ) -> Attestation | None:
        """Newest unexpired attestation for ``context_id`` (optionally from a
        specific issuer). Discovery for both reply_private and call(private)."""
        moment = now or datetime.now(timezone.utc)
        for cached_issuer, att in reversed(self._attestations.get(context_id, [])):
            if issuer_did is not None and cached_issuer != issuer_did:
                continue
            if att.is_expired(moment):
                continue
            return att
        return None

    def _channel_resolver(self) -> Any:
        if self.resolver is not None:
            return self.resolver
        from fastatp.did.resolver import web_resolver

        return web_resolver

    # --- push intake (opt-in; a board turns it on, a private peer never does) --

    def enable_push_intake(
        self, *, policy: PushPolicy | None = None, path: str = PUSH_INTAKE_PATH
    ) -> str:
        """Accept POSTed ``Repo.prove()`` proof slices at ``path``.

        This is how a documentless (``did:key``) peer that hosts nothing —
        e.g. a want-ad requester — posts a signed record to a public board: it
        pushes one proof instead of being subscribed-to. Each POST body is one
        proof slice; it is verified with ``Peer.verify`` (which short-circuits
        ``did:key`` with zero resolution) and then dispatched through the exact
        same Envelope + handler path as a firehose record. Off unless called —
        a private peer simply never enables it. Returns the route path.
        """
        from starlette.responses import JSONResponse, Response
        from starlette.routing import Route

        from fastatp.peer import Peer

        self._push_policy = policy
        self._push_verifier = Peer(resolver=self.resolver)

        async def route(request: Any) -> Any:
            body = await request.body()
            try:
                accepted = await self._ingest_pushed_proof(body)
            except FastATPError as e:
                return JSONResponse({"error": "invalid", "message": str(e)}, status_code=400)
            if not accepted:
                return JSONResponse({"error": "rejected"}, status_code=403)
            return Response(status_code=202)

        self.server.app.router.routes.append(Route(path, route, methods=["POST"]))
        return path

    async def _ingest_pushed_proof(self, proof: bytes) -> bool:
        """Verify a pushed proof and dispatch it; return False if policy rejects.

        Raises a ``FastATPError`` subclass (e.g. ``SignatureInvalid``) for an
        invalid/malformed proof — the route maps that to 400.
        """
        from fastatp.repo.car import decode_car
        from fastatp.repo.commit import Commit
        from fastatp.repo.mst import MST
        from fastatp.repo.store import MemoryBlockStore

        record = await self._push_verifier.verify(proof)  # security gate (raises on bad)

        # metadata for the envelope, read from the now-verified proof bytes
        roots, blocks = decode_car(proof)
        commit = Commit.from_dag_cbor(blocks[roots[0]])
        issuer = commit.did
        record_cid = record.cid()
        if self._push_policy is not None and not self._push_policy(
            issuer, type(record).nsid, len(proof)
        ):
            return False

        store = MemoryBlockStore()
        for cid, block in blocks.items():
            store.put(cid, block)
        entries = MST(store).load_partial(commit.data)
        path = next((p for p, c in entries.items() if c == record_cid), f"{type(record).nsid}/")
        collection, _, rkey = path.partition("/")
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        await self._enqueue(
            issuer,
            Envelope(
                issuer_did=issuer,
                record_cid=record_cid,
                received_at=now,
                record=record,
                collection=collection,
                rkey=rkey,
            ),
        )
        return True

    # --- following / ingestion --------------------------------------------------

    async def follow(
        self,
        producer_did: str,
        *,
        source: FrameSource | None = None,
        firehose_url: str | None = None,
        start_cursor: int | None = None,
    ) -> None:
        """Subscribe to ``producer_did``'s firehose and dispatch its records.

        Returns once the subscription is established (subscribe-before-emit),
        so a caller can safely emit immediately after and not race the reply.
        Idempotent: following an already-followed producer is a no-op.
        """
        if producer_did in self._followed:
            return
        if source is None:
            if firehose_url is None:
                raise ValueError(
                    "follow needs a source or a firehose_url (DID-document firehose "
                    "discovery is not implemented; pass firehose_url explicitly)"
                )
            source = WebSocketFrameSource(firehose_url, start_cursor)
        await source.open()  # subscription live before we return
        mirror = RepoMirror(producer_did, resolver=self.resolver)
        task = asyncio.create_task(self._ingest(producer_did, source, mirror))
        self._followed[producer_did] = (task, source)

    async def unfollow(self, producer_did: str) -> None:
        entry = self._followed.pop(producer_did, None)
        if entry is None:
            return
        task, source = entry
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await source.close()

    async def _ingest(self, producer_did: str, source: FrameSource, mirror: RepoMirror) -> None:
        try:
            async for frame in source:
                try:
                    envelopes = await self._frame_to_envelopes(frame, mirror, source)
                except FirehoseErrorFrame as e:
                    # a deliberate protocol signal (FutureCursor, ConsumerTooSlow)
                    # — recover state explicitly, never silently drop
                    log.warning("peerapp: %s sent %s; recovering via checkpoint", producer_did, e)
                    await self._recover(producer_did, source, mirror)
                    continue
                except CommitGap as e:
                    # the event verified but doesn't extend our state: events
                    # were missed, and every later commit would fail the same
                    # way — refetch state instead of warn-looping forever
                    log.warning("peerapp: %s gap detected (%s); recovering", producer_did, e)
                    await self._recover(producer_did, source, mirror)
                    continue
                except Exception as e:  # a bad frame must not kill the loop
                    log.warning("peerapp: dropping bad frame from %s: %s", producer_did, e)
                    continue
                # resume point follows verification, not construction: a
                # reconnecting source replays from the last applied seq
                if mirror.seq is not None:
                    source.advance(mirror.seq)
                for env in envelopes:
                    await self._enqueue(producer_did, env)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # pragma: no cover - transport teardown
            log.warning("peerapp: ingest loop for %s ended: %s", producer_did, e)

    async def _recover(self, producer_did: str, source: FrameSource, mirror: RepoMirror) -> None:
        """State recovery: adopt a full checkpoint, then rejoin the live tail.

        Any events between the checkpoint fetch and the resubscribe are
        caught by the mirror's gap checks, which route back here — the loop
        converges because each pass adopts a strictly newer state.
        """
        # a failed fetch (HTTP error, malformed CAR) must degrade to
        # out-of-sync, never kill the ingest loop: the next gap or #sync
        # retriggers recovery
        try:
            car = await source.checkpoint(producer_did)
        except Exception as e:
            log.warning(
                "peerapp: checkpoint fetch for %s failed (%s); mirror stays "
                "out of sync until the next recovery signal",
                producer_did,
                e,
            )
            return
        if car is None:
            log.warning(
                "peerapp: %s needs state recovery but this frame source cannot "
                "fetch a checkpoint; mirror stays out of sync",
                producer_did,
            )
            return
        try:
            await mirror.load_checkpoint(car)
        except Exception as e:
            log.warning("peerapp: checkpoint for %s did not verify (%s)", producer_did, e)
            return
        if mirror.seq is not None:
            source.advance(mirror.seq)

    async def _frame_to_envelopes(
        self, frame: bytes, mirror: RepoMirror, source: FrameSource
    ) -> list[Envelope]:
        header, body = decode_frame(frame)
        t = header.get("t")
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        if t == "#commit":
            records = await mirror.apply_commit(body)
            out = []
            for r in records:
                if r["action"] == "delete":
                    continue
                out.append(self._envelope(mirror.did, r["cid"], r["path"], r["record"], now))
            return [e for e in out if e is not None]
        if t == "#sync":
            before = set(mirror.entries.items())
            await mirror.apply_sync(body)
            if mirror.resync_target is not None:
                # spec shape: #sync announced a state we don't hold; re-fetch
                car = await source.checkpoint(mirror.did)
                if car is not None:
                    await mirror.load_checkpoint(car)
                else:
                    log.warning(
                        "peerapp: %s announced new state but this frame source "
                        "cannot fetch a checkpoint; mirror is out of sync",
                        mirror.did,
                    )
            out = []
            for path, cid in mirror.entries.items():
                if (path, cid) in before:
                    continue  # already had it; only dispatch newly-learned records
                record_json = mirror.get_record(*path.split("/", 1))
                if record_json is not None:
                    out.append(self._envelope(mirror.did, cid, path, record_json, now))
            return [e for e in out if e is not None]
        if t == "#identity":
            # the producer's identity changed: drop any cached DID document so
            # the next verification re-resolves (pattern the #identity event
            # exists for; ties into WebResolver.invalidate)
            did = body.get("did")
            invalidate = getattr(self.resolver, "invalidate", None)
            if did and callable(invalidate):
                invalidate(did)
            return []
        return []  # #info, #account, and future frame types are non-dispatched

    def _envelope(
        self, issuer: str, cid: CID, path: str, record_json: dict[str, Any], now: str
    ) -> Envelope | None:
        try:
            record = parse_record(record_json)
        except Exception:
            return None  # a $type this process has no Lexicon for -> ignore
        collection, _, rkey = path.partition("/")
        return Envelope(
            issuer_did=issuer,
            record_cid=cid,
            received_at=now,
            record=record,
            collection=collection,
            rkey=rkey,
        )

    # --- handler execution (decoupled from ingestion) --------------------------

    async def _enqueue(self, producer_did: str, env: Envelope) -> None:
        queue = self._queues.get(producer_did)
        if queue is None:
            queue = asyncio.Queue(maxsize=self._queue_size)
            self._queues[producer_did] = queue
            self._workers[producer_did] = asyncio.create_task(self._worker(producer_did, queue))
        try:
            queue.put_nowait(env)
        except asyncio.QueueFull:
            # bounded work queue: shed rather than grow without bound, and say so
            log.warning(
                "peerapp: handler queue for %s full; dropping %s %s",
                producer_did,
                env.collection,
                env.rkey,
            )

    async def _worker(self, producer_did: str, queue: asyncio.Queue[Envelope | None]) -> None:
        inflight: set[asyncio.Task[None]] = set()
        try:
            while True:
                env = await queue.get()
                if env is None:
                    break
                reg = self._registrations.get(type(env.record).nsid)
                if reg is not None and reg.ordered:
                    await self._dispatch(env, reg)  # serial: preserve commit order
                else:
                    # concurrent, but bounded for the handler's whole duration
                    task = asyncio.create_task(self._dispatch_bounded(env, reg))
                    inflight.add(task)
                    task.add_done_callback(inflight.discard)
        except asyncio.CancelledError:
            raise
        finally:
            for task in inflight:
                task.cancel()
            # await the cancellations so aclose() does not return while handler
            # tasks are still unwinding (and touching soon-torn-down resources)
            if inflight:
                await asyncio.gather(*inflight, return_exceptions=True)

    async def _dispatch_bounded(self, env: Envelope, reg: _Registration | None) -> None:
        async with self._semaphore:
            await self._dispatch(env, reg)

    async def _dispatch(self, env: Envelope, reg: _Registration | None) -> None:
        # de-dup redeliveries by record CID (in-memory, non-durable)
        if self._dedupe:
            key = bytes(env.record_cid)
            if key in self._seen:
                return
            self._seen[key] = None
            while len(self._seen) > self._dedupe_limit:
                self._seen.popitem(last=False)

        # the shipped Attestation is infra, not application: cache every inbound
        # one for private-path discovery, whether or not a handler exists (0015)
        if isinstance(env.record, Attestation):
            self._cache_attestation(env.issuer_did, env.record)

        # resolve any app.call waiter interested in this record
        corr = env.context_id
        if corr is not None:
            fut = self._waiters.get((type(env.record).nsid, corr))
            if fut is not None and not fut.done():
                fut.set_result(env.record)

        if reg is None:
            return  # unknown $type for this app: ignored (waiter/cache may still have fired)
        if reg.admit_issuer and self.reconciler is not None:
            with contextlib.suppress(Exception):
                await self.admit(env.issuer_did)
        try:
            await reg.handler(env.record, env)
        except Exception:
            log.exception("peerapp: handler for %s raised", type(env.record).nsid)

    # --- request/reply ---------------------------------------------------------

    async def call(
        self,
        peer_did: str,
        request: Lexicon,
        *,
        reply_type: type[Lexicon],
        correlation: str = "context_id",
        timeout: float | None = None,
        firehose_url: str | None = None,
        source: FrameSource | None = None,
        collection: str | None = None,
        rkey: str | None = None,
        private: bool = False,
        self_attestation: Attestation | None = None,
        push_url: str | None = None,
        channel_firehose_url: str | None = None,
        channel_source: FrameSource | None = None,
    ) -> Lexicon:
        """Emit ``request`` and await the first ``reply_type`` from ``peer_did``
        whose ``correlation`` field matches the request's.

        Not RPC-with-a-login: no connection is held; the reply is a durable
        signed record. ``timeout`` is a **local** give-up (raises
        ``asyncio.TimeoutError`` here, cancels nothing on the peer); a missing
        reply's *meaning* comes from the tool's published liveness contract,
        not this deadline. Subscribes to the peer BEFORE emitting so a fast
        reply is never missed.

        ``private=True`` runs the private-response path from the **same call
        site**: publish the request plus the requester's ``self_attestation``
        (its WG coordinates — so the service can admit it by key), discover the
        service's engagement attestation on ``peer_did``'s firehose, follow the
        ephemeral channel it points at, and return the private reply from
        there. The developer never touches ``did:key``, WireGuard, or
        attestations. ``push_url`` posts the request/self-attestation to a board
        (or the peer's push intake) when the requester hosts nothing.
        """
        from fastatp.tid import generate_tid

        corr_value = getattr(request, correlation, None)
        if corr_value is None:
            raise ValueError(f"request has no {correlation!r} field to correlate on")
        corr = str(corr_value)

        if private:
            return await self._call_private(
                peer_did,
                request,
                reply_type=reply_type,
                corr=corr,
                timeout=timeout,
                firehose_url=firehose_url,
                source=source,
                collection=collection,
                rkey=rkey,
                self_attestation=self_attestation,
                push_url=push_url,
                channel_firehose_url=channel_firehose_url,
                channel_source=channel_source,
            )

        key = (reply_type.nsid, corr)

        # subscribe-before-emit: be following the peer before the request goes out
        await self.follow(peer_did, source=source, firehose_url=firehose_url)

        loop = asyncio.get_running_loop()
        future: asyncio.Future[Lexicon] = loop.create_future()
        self._waiters[key] = future
        try:
            self.emit(collection or request.nsid, rkey or generate_tid(), request)
            return await asyncio.wait_for(future, timeout)
        finally:
            self._waiters.pop(key, None)

    async def _call_private(
        self,
        peer_did: str,
        request: Lexicon,
        *,
        reply_type: type[Lexicon],
        corr: str,
        timeout: float | None,
        firehose_url: str | None,
        source: FrameSource | None,
        collection: str | None,
        rkey: str | None,
        self_attestation: Attestation | None,
        push_url: str | None,
        channel_firehose_url: str | None,
        channel_source: FrameSource | None,
    ) -> Lexicon:
        from fastatp.tid import generate_tid

        if self_attestation is None:
            raise ValueError(
                "a private call needs self_attestation — the requester's WG coordinates, "
                "so the service can admit it by public key"
            )

        # be watching the service's public firehose before we announce ourselves
        await self.follow(peer_did, source=source, firehose_url=firehose_url)

        # publish the request + our self-attestation (emit locally so we can prove
        # them; push to the board/peer when we host no reachable firehose)
        req_rkey = rkey or generate_tid()
        self.emit(collection or request.nsid, req_rkey, request)
        att_rkey = generate_tid()
        self.emit(Attestation.nsid, att_rkey, self_attestation)
        if push_url is not None:
            await push_proof(push_url, self.server.repo.prove(collection or request.nsid, req_rkey))
            await push_proof(push_url, self.server.repo.prove(Attestation.nsid, att_rkey))

        # one shared deadline across BOTH waits (discover the engagement, then
        # await the reply): the caller's timeout is a single budget, not doubled
        loop = asyncio.get_running_loop()
        deadline = None if timeout is None else loop.time() + timeout

        def _remaining() -> float | None:
            return None if deadline is None else max(0.0, deadline - loop.time())

        engagement = await self._await_attestation(corr, issuer_did=peer_did, timeout=_remaining())
        key = (reply_type.nsid, corr)
        future: asyncio.Future[Lexicon] = loop.create_future()
        self._waiters[key] = future
        try:
            await self.follow_attested(
                engagement, firehose_url=channel_firehose_url, source=channel_source
            )
            return await asyncio.wait_for(future, _remaining())
        finally:
            self._waiters.pop(key, None)

    # --- lifecycle -------------------------------------------------------------

    async def aclose(self) -> None:
        self._closed = True
        for did in list(self._followed):
            await self.unfollow(did)
        for queue in list(self._queues.values()):
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(None)  # sentinel: stop the worker
        for worker in list(self._workers.values()):
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker
        for fut in self._waiters.values():
            if not fut.done():
                fut.cancel()
        for att_fut in self._attestation_waiters.values():
            if not att_fut.done():
                att_fut.cancel()


async def push_proof(url: str, proof: bytes, *, timeout: float = 10.0) -> None:
    """Client side of push intake: POST a ``Repo.prove()`` slice to a board.

    The zero-infrastructure requester's one outbound write — a documentless
    ``did:key`` peer can post a signed want ad to a public board with just this
    (no hosted firehose). Raises on a non-2xx response.
    """
    import httpx

    async with httpx.AsyncClient() as client:
        response = await client.post(
            url,
            content=proof,
            headers={"content-type": "application/vnd.ipld.car"},
            timeout=timeout,
        )
        response.raise_for_status()
