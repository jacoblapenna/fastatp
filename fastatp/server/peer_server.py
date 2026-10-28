"""PeerServer: hosts one or more repos and a subscribeRepos-shaped firehose.

The message-layer analogue of DIDServer. Writes go through the server
(``put_record`` / ``delete_record`` / ``apply_writes``) so every commit is
broadcast to subscribers in commit order with a monotonically increasing
``seq``.

One server can host **many repos**, routed by DID: ``getRepo?did=X``
and ``subscribeRepos?did=X`` select a hosted repo; with ``did`` omitted the
server's primary repo answers (single-repo back-compat). Each hosted repo
carries its **own** ``seq``, cursor, and backfill window — a shared counter
across repos would tear holes in every follower's sequence. This is plain
atproto PDS shape (commit frames already carry ``did``; ``getRepo`` already
takes ``?did=``): zero wire change.

Resumable sync: subscribers pass ``?cursor=<seq>``; events after the cursor
replay from the in-memory backfill window (``backfill_window`` is yours to
size, not a relay's). A cursor older than the window gets an ``#info
OutdatedCursor`` followed by a ``#sync`` checkpoint (full current-state CAR)
to resume from; a cursor ahead of the stream gets a ``FutureCursor`` error.
A ``getRepo``-style HTTP route serves the same checkpoint on demand.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from starlette.applications import Starlette
from starlette.requests import HTTPConnection, Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from fastatp.authz.capability import ACTION_SUBSCRIBE, live_capabilities
from fastatp.cid import CID
from fastatp.exceptions import RepoError
from fastatp.peer import Peer
from fastatp.repo.repo import CommitEvent, RecordLike, Repo, WriteOp
from fastatp.repo.store import BlockStore
from fastatp.repo.sync import (
    account_frame_body,
    commit_frame_body,
    encode_error_frame,
    encode_frame,
    identity_frame_body,
    sync_frame_body,
)

log = logging.getLogger("fastatp.peerserver")

FIREHOSE_PATH = "/xrpc/com.atproto.sync.subscribeRepos"
GET_REPO_PATH = "/xrpc/com.atproto.sync.getRepo"
LIST_REPOS_PATH = "/xrpc/com.atproto.sync.listRepos"
GET_LATEST_COMMIT_PATH = "/xrpc/com.atproto.sync.getLatestCommit"
GET_RECORD_PATH = "/xrpc/com.atproto.sync.getRecord"
GET_BLOB_PATH = "/xrpc/com.atproto.sync.getBlob"
LIST_BLOBS_PATH = "/xrpc/com.atproto.sync.listBlobs"

_EVICTED = -1  # queue sentinel: subscriber fell behind and was evicted
_LOOPBACK = frozenset({None, "127.0.0.1", "::1", "localhost"})
_PROXY_TRACK_MAX = 4096  # LRU cap on the diagnostic proxy-detection tracking

CallerResolver = Callable[[HTTPConnection], "str | None | Awaitable[str | None]"]
"""``resolve(connection) -> did | None`` (sync or async): the doorman's identity
seam. Maps an inbound firehose/getRepo connection to the admitted caller's DID,
or ``None`` if it cannot be tied to one. Default: ``TunnelIdentityResolver``
(WireGuard cryptokey routing — source tunnel IP -> DID). Injectable so tests
supply a fake and other enforcement planes (e.g. a tailnet) supply their own."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class _HostedRepo:
    """Per-repo firehose state: one repo plus its own seq, backfill, subscribers.

    Splitting *server* from *repo* means each repo must carry its own
    **position** — a private-per-request repo that dies must not tear its page
    numbers out of a sequence other followers still read. Every hosted repo
    owns its ``seq``, backfill window, and subscriber queue set; fan-out never
    crosses repos.
    """

    def __init__(
        self,
        repo: Repo,
        backfill_window: int,
        *,
        public: bool = False,
        expires_at: datetime | None = None,
    ) -> None:
        self.repo = repo
        self.backfill_window = backfill_window
        self.public = public
        self.expires_at = expires_at
        self.seq = 0
        self._backfill: deque[tuple[int, bytes]] = deque(maxlen=backfill_window)
        self._queues: set[asyncio.Queue[tuple[int, bytes]]] = set()
        # a host announces the account before its commits: relays ignore
        # #commit/#sync events for accounts not known to be active (sync spec)
        self.emit_identity()
        self.emit_account(active=True)

    @property
    def did(self) -> str:
        return self.repo.did

    def is_expired(self, now: datetime | None = None) -> bool:
        if self.expires_at is None:
            return False
        return self.expires_at <= (now or datetime.now(timezone.utc))

    def _emit_frame(self, build: Callable[[int], bytes]) -> None:
        """Allocate the next seq, build the frame, backfill it, fan it out."""
        self.seq += 1
        frame = build(self.seq)
        self._backfill.append((self.seq, frame))
        for queue in list(self._queues):
            try:
                queue.put_nowait((self.seq, frame))
            except asyncio.QueueFull:
                # slow consumer: evict rather than grow without bound
                self._queues.discard(queue)
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait((_EVICTED, b""))

    def emit(self, event: CommitEvent) -> CommitEvent:
        self._emit_frame(
            lambda seq: encode_frame("#commit", commit_frame_body(self.repo.did, seq, event))
        )
        return event

    def emit_account(self, *, active: bool, status: str | None = None) -> None:
        self._emit_frame(
            lambda seq: encode_frame(
                "#account",
                account_frame_body(self.did, seq, _now_iso(), active=active, status=status),
            )
        )

    def emit_identity(self, *, handle: str | None = None) -> None:
        self._emit_frame(
            lambda seq: encode_frame(
                "#identity", identity_frame_body(self.did, seq, _now_iso(), handle=handle)
            )
        )

    def sync_frame(self) -> bytes:
        # spec: a #sync carries the commit block ONLY; consumers that need the
        # contents fetch getRepo (the checkpoint route serves the full CAR)
        return encode_frame(
            "#sync",
            sync_frame_body(
                self.repo.did, self.seq, self.repo.rev, self.repo.commit_car(), _now_iso()
            ),
        )

    def close_subscribers(self) -> None:
        """Poison every live subscriber (whole-repo teardown / expiry)."""
        for queue in list(self._queues):
            self._queues.discard(queue)
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait((_EVICTED, b""))

    async def catch_up(self, websocket: WebSocket, cursor: int | None) -> int | None:
        """Replay history for a cursor; returns the last seq sent (or None to stop)."""
        if cursor is None:
            return self.seq  # live tail only
        if cursor > self.seq:
            await websocket.send_bytes(
                encode_error_frame(
                    "FutureCursor", f"cursor {cursor} is ahead of the stream ({self.seq})"
                )
            )
            await websocket.close()
            return None
        oldest_available = self._backfill[0][0] if self._backfill else self.seq + 1
        if cursor + 1 < oldest_available:
            # gap beyond the retained window: checkpoint fallback
            await websocket.send_bytes(
                encode_frame(
                    "#info",
                    {
                        "name": "OutdatedCursor",
                        "message": "cursor is older than the backfill window; sending checkpoint",
                    },
                )
            )
            # capture the seq the frame was built at BEFORE any await: a
            # concurrent emit() during send would otherwise advance seq and
            # make the caller skip the very frame that emit enqueued
            sync_frame = self.sync_frame()
            checkpoint_seq = self.seq
            await websocket.send_bytes(sync_frame)
            return checkpoint_seq
        last = cursor
        for seq, frame in list(self._backfill):
            if seq > cursor:
                await websocket.send_bytes(frame)
                last = seq
        return last


def _source_ip(conn: HTTPConnection) -> str | None:
    return conn.client.host if conn.client is not None else None


class TunnelIdentityResolver:
    """Default doorman identity: source tunnel IP -> DID via signed capabilities.

    WireGuard binds each admitted peer's static public key to a unique tunnel
    IP at handshake, so a connection's source tunnel IP is an unspoofable
    identity *inside the tunnel*. The IP -> DID map is the union of every
    hosted repo's live capabilities' ``wg_tunnel_ip`` (signed data) —
    no new state. A caller whose source IP is in no capability maps to ``None``
    and is refused for gated repos (fail-closed).
    """

    def __init__(self, server: PeerServer) -> None:
        self._server = server

    def __call__(self, conn: HTTPConnection) -> str | None:
        ip = _source_ip(conn)
        if ip is None:
            return None
        return self._server._ip_to_did().get(ip)


class RepoHandle:
    """A write + admin handle to one hosted repo.

    This is the **only** write path to a non-primary repo. There is
    deliberately no ``emit(did=...)`` string-target write API: a confused or
    swapped DID string could otherwise write a record into the wrong repo —
    signed under the wrong identity and/or exposed to the wrong subscribers. A
    handle cannot be confused with another repo's handle. Private
    channels are exactly ``server.register(...)`` handles.
    """

    def __init__(self, server: PeerServer, host: _HostedRepo) -> None:
        self._server = server
        self._host = host

    @property
    def did(self) -> str:
        return self._host.did

    @property
    def repo(self) -> Repo:
        return self._host.repo

    @property
    def seq(self) -> int:
        return self._host.seq

    @property
    def public(self) -> bool:
        return self._host.public

    @property
    def expires_at(self) -> datetime | None:
        return self._host.expires_at

    def put_record(self, collection: str, rkey: str, record: RecordLike) -> CommitEvent:
        return self._host.emit(self._host.repo.put_record(collection, rkey, record))

    def delete_record(self, collection: str, rkey: str) -> CommitEvent:
        return self._host.emit(self._host.repo.delete_record(collection, rkey))

    def apply_writes(self, writes: list[WriteOp]) -> CommitEvent:
        return self._host.emit(self._host.repo.apply_writes(writes))

    def emit_account(self, *, active: bool = True, status: str | None = None) -> None:
        """Re-announce this repo's hosting status (#account)."""
        self._host.emit_account(active=active, status=status)

    def emit_identity(self, *, handle: str | None = None) -> None:
        """Prod subscribers to refresh this DID's identity (#identity)."""
        self._host.emit_identity(handle=handle)

    def expire(self) -> None:
        """Retire this repo now — the channel-owner's "TTL reached / done with
        it" intent. A semantic alias that **delegates** to :meth:`unregister`
        so the two can never drift; kept as the name a channel reads
        naturally."""
        self.unregister()

    def unregister(self) -> None:
        """Stop hosting this repo and cut its live subscribers."""
        self._server.unregister(self.did)


class PeerServer:
    """Hosts a peer's repos; exposes the firehose and checkpoint routes."""

    def __init__(
        self,
        peer: Peer,
        repo: Repo | None = None,
        repo_store: BlockStore | None = None,
        backfill_window: int = 512,
        *,
        identity_resolver: CallerResolver | None = None,
        warn_on_proxy: bool = True,
        proxy_warn_threshold: int = 8,
    ) -> None:
        if peer.did is None or peer.signing_key is None:
            raise ValueError("PeerServer requires a Peer with a did and signing_key")
        self.peer = peer
        self.backfill_window = backfill_window
        primary = _HostedRepo(
            repo or Repo(peer.did, peer.signing_key, store=repo_store),
            backfill_window,
            public=True,  # the peer's own repo is its public firehose
        )
        self._primary = primary
        self._hosts: dict[str, _HostedRepo] = {primary.did: primary}
        # the doorman: gated repos consult identity_resolver on read
        self.identity_resolver: CallerResolver = identity_resolver or TunnelIdentityResolver(self)
        self._warn_on_proxy = warn_on_proxy
        self._proxy_warn_threshold = proxy_warn_threshold
        # diagnostic-only, LRU-bounded so an exposed listener can't grow them
        # unboundedly (fail-closed still holds regardless of eviction)
        self._subs_by_ip: OrderedDict[str | None, set[str]] = OrderedDict()
        self._proxy_warned: OrderedDict[str | None, None] = OrderedDict()
        self.app = Starlette(
            routes=[
                WebSocketRoute(FIREHOSE_PATH, self._subscribe),
                Route(GET_REPO_PATH, self._get_repo),
                Route(LIST_REPOS_PATH, self._list_repos),
                Route(GET_LATEST_COMMIT_PATH, self._get_latest_commit),
                Route(GET_RECORD_PATH, self._get_record),
                Route(GET_BLOB_PATH, self._get_blob),
                Route(LIST_BLOBS_PATH, self._list_blobs),
            ]
        )

    # --- multi-repo registry ---------------------------------------------------

    def register(
        self,
        repo: Repo,
        *,
        public: bool = False,
        backfill_window: int | None = None,
        ttl: float | None = None,
        expires_at: datetime | None = None,
    ) -> RepoHandle:
        """Host ``repo`` (routed by its DID) and return its write handle.

        ``public=False`` (the default — gated) marks the repo for the doorman;
        ``public=True`` opts it into open reads (a board). Writes go **only**
        through the returned handle — there is no string-DID write API (see
        ``RepoHandle``).

        A whole-repo TTL retires the repo automatically: pass ``ttl`` (seconds
        from now) or ``expires_at`` (an aware datetime). Past its expiry the
        repo is unroutable (``RepoNotFound``) and its subscribers are cut — the
        lifecycle of an ephemeral channel. Reaping is lazy (on the next
        access) and eager (:meth:`reap_expired`); the block store is released
        when the last reference to the retired repo drops.
        """
        if repo.did in self._hosts:
            raise ValueError(f"already hosting a repo for {repo.did!r}")
        if ttl is not None and expires_at is not None:
            raise ValueError("pass ttl or expires_at, not both")
        if ttl is not None:
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl)
        host = _HostedRepo(
            repo, backfill_window or self.backfill_window, public=public, expires_at=expires_at
        )
        self._hosts[repo.did] = host
        return RepoHandle(self, host)

    def unregister(self, did: str) -> None:
        """Stop hosting ``did``'s repo and poison its live subscribers.

        The primary repo cannot be unregistered (it is the peer's own repo).
        Unknown DIDs are a no-op.
        """
        host = self._hosts.get(did)
        if host is None:
            return
        if host is self._primary:
            raise ValueError("cannot unregister the primary repo")
        self._drop(host)

    def _drop(self, host: _HostedRepo) -> None:
        """Remove a hosted repo and cut its subscribers (the host, its repo, and
        its block store become unreferenced and are reclaimed).

        Each hosted repo keeps its OWN block store.
        Whole-repo GC is just dropping the reference: no cross-repo block
        refcounting, no way for one repo's teardown to corrupt a neighbor, and
        steady-state block count is bounded under churn because a retired
        ephemeral repo contributes zero. The trade — no cross-repo dedup of
        identical blocks — is permanently an app-composition concern (compose
        durable per-user repos / real storage infra if duplicate-block memory
        matters), never a substrate feature.
        """
        self._hosts.pop(host.did, None)
        # tell still-connected subscribers the account is no longer hosted
        # here before poisoning them (sync spec #account semantics)
        host.emit_account(active=False, status="deactivated")
        host.close_subscribers()

    def _resolve_host(self, did: str | None) -> _HostedRepo | None:
        """Route to a live host, reaping it lazily if its TTL has passed."""
        host = self._primary if did is None else self._hosts.get(did)
        if host is None:
            return None
        if host.is_expired():
            self._drop(host)  # never the primary: it carries no expiry
            return None
        return host

    def reap_expired(self, now: datetime | None = None) -> list[str]:
        """Retire every expired hosted repo now; returns the reaped DIDs.

        Lazy reaping already keeps expired repos unroutable, but a long-lived
        ephemeral repo with no further reads would otherwise linger until
        touched; call this periodically to reclaim eagerly.
        """
        moment = now or datetime.now(timezone.utc)
        reaped = [h for h in self._hosts.values() if h.is_expired(moment)]
        for host in reaped:
            self._drop(host)
        return [h.did for h in reaped]

    def hosts(self) -> list[str]:
        """DIDs of every currently hosted repo (primary first is not implied)."""
        return list(self._hosts)

    def hosted_repo(self, did: str) -> Repo | None:
        """Read access to a hosted repo by DID; ``None`` if unknown or expired.

        The host-side ``RepoSource`` for delegation walks: a DID
        whose repo this server does not hold simply cannot be presence-checked
        here — fail closed falls out.
        """
        host = self._resolve_host(did) if did else None
        return host.repo if host is not None else None

    # --- primary-repo back-compat surface --------------------------------------

    @property
    def repo(self) -> Repo:
        return self._primary.repo

    @property
    def seq(self) -> int:
        return self._primary.seq

    @property
    def _backfill(self) -> deque[tuple[int, bytes]]:
        return self._primary._backfill

    @property
    def _queues(self) -> set[asyncio.Queue[tuple[int, bytes]]]:
        return self._primary._queues

    def _sync_frame(self) -> bytes:
        return self._primary.sync_frame()

    async def _catch_up(self, websocket: WebSocket, cursor: int | None) -> int | None:
        return await self._primary.catch_up(websocket, cursor)

    # --- writes (broadcast on commit) ------------------------------------------

    def put_record(self, collection: str, rkey: str, record: RecordLike) -> CommitEvent:
        return self._emit(self.repo.put_record(collection, rkey, record))

    def delete_record(self, collection: str, rkey: str) -> CommitEvent:
        return self._emit(self.repo.delete_record(collection, rkey))

    def apply_writes(self, writes: list[WriteOp]) -> CommitEvent:
        return self._emit(self.repo.apply_writes(writes))

    def _emit(self, event: CommitEvent) -> CommitEvent:
        return self._primary.emit(event)

    def emit_account(self, *, active: bool = True, status: str | None = None) -> None:
        """Re-announce the primary repo's hosting status (#account)."""
        self._primary.emit_account(active=active, status=status)

    def emit_identity(self, *, handle: str | None = None) -> None:
        """Prod subscribers to refresh the primary DID's identity (#identity)."""
        self._primary.emit_identity(handle=handle)

    # --- the doorman (per-repo read gating) ------------------------------------

    def _ip_to_did(self) -> dict[str, str]:
        """Source tunnel IP -> DID, from every hosted repo's live capabilities.

        The signed IP<->DID binding lives in by-public-key capabilities
        (``wg_tunnel_ip``). Rebuilt per lookup: repos are few and
        in-memory, and a stale map must never outlive a revocation.
        """
        mapping: dict[str, str] = {}
        for host in self._hosts.values():
            for subject, (_rkey, cap) in live_capabilities(host.repo).items():
                if cap.wg_tunnel_ip:
                    mapping[cap.wg_tunnel_ip] = subject
        return mapping

    async def _resolve_caller(self, conn: HTTPConnection) -> str | None:
        result = self.identity_resolver(conn)
        if inspect.isawaitable(result):
            result = await result
        return result

    async def _admit_read(self, conn: HTTPConnection, host: _HostedRepo) -> tuple[bool, str]:
        """Doorman verdict for a read of ``host``: ``(allowed, reason)``.

        Public repos are open. A gated repo admits a caller only when the
        connection resolves to a DID that holds a live ``subscribe`` capability
        in **that repo's own** records. Both failure modes (no identity, not
        admitted) fail closed.
        """
        if host.public:
            return True, ""
        caller = await self._resolve_caller(conn)
        if caller is None:
            self._warn_if_proxy(conn, host)
            return False, "caller could not be tied to a tunnel identity"
        entry = live_capabilities(host.repo).get(caller)
        if entry is None or ACTION_SUBSCRIBE not in entry[1].actions:
            return False, f"{caller} is not admitted to {host.did}"
        return True, ""

    def _warn_if_proxy(self, conn: HTTPConnection, host: _HostedRepo) -> None:
        """Loudly diagnose the likely cause of an unidentifiable gated read.

        A proxy/bridge in front of the tunnel rewrites the source address, so
        every gated read fails closed — a confusing "nobody can connect" outage.
        Warn (once per source) when the source is loopback or one source IP is
        fanning out to many distinct repos, both signatures of a shared front.
        """
        if not self._warn_on_proxy:
            return
        ip = _source_ip(conn)
        seen = self._subs_by_ip.setdefault(ip, set())
        self._subs_by_ip.move_to_end(ip)
        while len(self._subs_by_ip) > _PROXY_TRACK_MAX:
            self._subs_by_ip.popitem(last=False)  # evict least-recently-seen
        seen.add(host.did)
        if ip in self._proxy_warned:
            return
        loopback = ip in _LOOPBACK
        if not (loopback or len(seen) > self._proxy_warn_threshold):
            return
        self._proxy_warned[ip] = None
        while len(self._proxy_warned) > _PROXY_TRACK_MAX:
            self._proxy_warned.popitem(last=False)
        reason = "loopback source" if loopback else f"{len(seen)} distinct repos from one IP"
        log.warning(
            "peerserver: gated read from %s could not be tied to a WireGuard tunnel "
            "identity (%s). The gated listener may be behind a proxy/bridge that rewrites "
            "the source address, or not bound on the tunnel interface — gated repos will "
            "refuse everyone. Bind directly on the tunnel.",
            ip,
            reason,
        )

    # --- checkpoint ----------------------------------------------------------

    async def _get_repo(self, request: Request) -> Response:
        did = request.query_params.get("did")
        host = self._resolve_host(did)
        if host is None:
            return JSONResponse(
                {"error": "RepoNotFound", "message": f"this server does not host {did!r}"},
                status_code=404,
            )
        allowed, reason = await self._admit_read(request, host)
        if not allowed:
            return JSONResponse({"error": "NotAuthorized", "message": reason}, status_code=403)
        return Response(
            host.repo.export_car(),
            media_type="application/vnd.ipld.car",
            headers={"x-fastatp-seq": str(host.seq), "x-fastatp-rev": host.repo.rev},
        )

    # --- relay discovery / resync routes (com.atproto.sync) --------------------

    async def _list_repos(self, request: Request) -> Response:
        """com.atproto.sync.listRepos: enumerate hosted repos.

        The doorman applies per repo: a caller sees public repos plus any
        gated repo it is admitted to — a gated repo's existence is never
        leaked to strangers.
        """
        try:
            limit = int(request.query_params.get("limit", "500"))
        except ValueError:
            return JSONResponse(
                {"error": "InvalidRequest", "message": "limit must be an integer"},
                status_code=400,
            )
        limit = max(1, min(limit, 1000))
        cursor = request.query_params.get("cursor")
        visible = []
        for did in sorted(self._hosts):
            if cursor is not None and did <= cursor:
                continue
            host = self._resolve_host(did)
            if host is None:
                continue
            allowed, _reason = await self._admit_read(request, host)
            if not allowed:
                continue
            visible.append(host)
        page = visible[:limit]
        body: dict[str, Any] = {
            "repos": [
                {
                    "did": h.did,
                    "head": h.repo.commit_cid.encode(),
                    "rev": h.repo.rev,
                    "active": True,
                }
                for h in page
            ]
        }
        if len(visible) > limit and page:
            body["cursor"] = page[-1].did
        return JSONResponse(body)

    async def _get_latest_commit(self, request: Request) -> Response:
        """com.atproto.sync.getLatestCommit: current commit CID + rev."""
        did = request.query_params.get("did")
        if did is None:
            return JSONResponse(
                {"error": "InvalidRequest", "message": "did is required"}, status_code=400
            )
        host = self._resolve_host(did)
        if host is None:
            return JSONResponse(
                {"error": "RepoNotFound", "message": f"this server does not host {did!r}"},
                status_code=404,
            )
        allowed, reason = await self._admit_read(request, host)
        if not allowed:
            return JSONResponse({"error": "NotAuthorized", "message": reason}, status_code=403)
        return JSONResponse({"cid": host.repo.commit_cid.encode(), "rev": host.repo.rev})

    async def _get_record(self, request: Request) -> Response:
        """com.atproto.sync.getRecord: a record's existence proof — the body
        IS ``Repo.prove()``: commit + MST path + record block as a CAR."""
        did = request.query_params.get("did")
        collection = request.query_params.get("collection")
        rkey = request.query_params.get("rkey")
        if did is None or collection is None or rkey is None:
            return JSONResponse(
                {"error": "InvalidRequest", "message": "did, collection, and rkey are required"},
                status_code=400,
            )
        host = self._resolve_host(did)
        if host is None:
            return JSONResponse(
                {"error": "RepoNotFound", "message": f"this server does not host {did!r}"},
                status_code=404,
            )
        allowed, reason = await self._admit_read(request, host)
        if not allowed:
            return JSONResponse({"error": "NotAuthorized", "message": reason}, status_code=403)
        try:
            car = host.repo.prove(collection, rkey)
        except RepoError:
            return JSONResponse(
                {"error": "RecordNotFound", "message": f"no record at {collection}/{rkey}"},
                status_code=404,
            )
        return Response(car, media_type="application/vnd.ipld.car")

    async def _get_blob(self, request: Request) -> Response:
        """com.atproto.sync.getBlob: the full blob as originally stored."""
        did = request.query_params.get("did")
        cid_param = request.query_params.get("cid")
        if did is None or cid_param is None:
            return JSONResponse(
                {"error": "InvalidRequest", "message": "did and cid are required"},
                status_code=400,
            )
        host = self._resolve_host(did)
        if host is None:
            return JSONResponse(
                {"error": "RepoNotFound", "message": f"this server does not host {did!r}"},
                status_code=404,
            )
        allowed, reason = await self._admit_read(request, host)
        if not allowed:
            return JSONResponse({"error": "NotAuthorized", "message": reason}, status_code=403)
        try:
            cid = CID.decode(cid_param)
        except ValueError:
            return JSONResponse(
                {"error": "InvalidRequest", "message": "cid is not a valid CID"},
                status_code=400,
            )
        data = host.repo.get_blob(cid)
        if data is None:
            return JSONResponse(
                {"error": "BlobNotFound", "message": f"no blob {cid_param}"}, status_code=404
            )
        info = host.repo.blob_info(cid)
        media = "application/octet-stream"
        if info is not None and info.mime_type:
            media = info.mime_type
        return Response(data, media_type=media)

    async def _list_blobs(self, request: Request) -> Response:
        """com.atproto.sync.listBlobs: blob CIDs, optionally since a rev.

        ``since`` filters on each blob's add-time rev stamp (the honest
        sovereign-peer approximation of "referenced since").
        """
        did = request.query_params.get("did")
        if did is None:
            return JSONResponse(
                {"error": "InvalidRequest", "message": "did is required"}, status_code=400
            )
        host = self._resolve_host(did)
        if host is None:
            return JSONResponse(
                {"error": "RepoNotFound", "message": f"this server does not host {did!r}"},
                status_code=404,
            )
        allowed, reason = await self._admit_read(request, host)
        if not allowed:
            return JSONResponse({"error": "NotAuthorized", "message": reason}, status_code=403)
        try:
            limit = int(request.query_params.get("limit", "500"))
        except ValueError:
            return JSONResponse(
                {"error": "InvalidRequest", "message": "limit must be an integer"},
                status_code=400,
            )
        limit = max(1, min(limit, 1000))
        since = request.query_params.get("since")
        cursor = request.query_params.get("cursor")
        infos = host.repo.list_blobs()  # sorted by CID string
        # >= not >: a blob's stamp is the head rev BEFORE the commit that
        # references it, so a relay whose cursor equals the stamp has NOT yet
        # seen the referencing commit and must receive the blob
        cids = [
            b.cid.encode()
            for b in infos
            if (since is None or (b.rev is not None and b.rev >= since))
        ]
        if cursor is not None:
            cids = [c for c in cids if c > cursor]
        page = cids[:limit]
        body: dict[str, Any] = {"cids": page}
        if len(cids) > limit and page:
            body["cursor"] = page[-1]
        return JSONResponse(body)

    # --- firehose --------------------------------------------------------------

    async def _subscribe(self, websocket: WebSocket) -> None:
        await websocket.accept()
        did = websocket.query_params.get("did")
        host = self._resolve_host(did)
        if host is None:
            await websocket.send_bytes(
                encode_error_frame("RepoNotFound", f"this server does not host {did!r}")
            )
            await websocket.close()
            return
        allowed, reason = await self._admit_read(websocket, host)
        if not allowed:
            await websocket.send_bytes(encode_error_frame("NotAuthorized", reason))
            await websocket.close()
            return
        cursor_param = websocket.query_params.get("cursor")
        cursor: int | None = None
        if cursor_param is not None:
            try:
                cursor = int(cursor_param)
            except ValueError:
                await websocket.send_bytes(
                    encode_error_frame("InvalidRequest", "cursor must be an integer")
                )
                await websocket.close()
                return

        queue: asyncio.Queue[tuple[int, bytes]] = asyncio.Queue(
            maxsize=max(host.backfill_window, 8)
        )
        host._queues.add(queue)
        # listen for client disconnect (and server shutdown) concurrently:
        # a send-only loop would block on the queue forever and stall shutdown
        receiver = asyncio.create_task(self._wait_disconnect(websocket))
        try:
            last_sent = await host.catch_up(websocket, cursor)
            if last_sent is None:
                return  # FutureCursor: closed
            while not receiver.done():
                getter = asyncio.create_task(queue.get())
                done, _pending = await asyncio.wait(
                    (getter, receiver), return_when=asyncio.FIRST_COMPLETED
                )
                if getter not in done:
                    getter.cancel()
                    break
                seq, frame = getter.result()
                if seq == _EVICTED:
                    await websocket.send_bytes(
                        encode_error_frame(
                            "ConsumerTooSlow", "subscriber fell behind; reconnect with cursor"
                        )
                    )
                    break
                if seq <= last_sent:
                    continue
                await websocket.send_bytes(frame)
                last_sent = seq
        except WebSocketDisconnect:
            pass
        finally:
            receiver.cancel()
            host._queues.discard(queue)
            with contextlib.suppress(Exception):
                await websocket.close()

    @staticmethod
    async def _wait_disconnect(websocket: WebSocket) -> None:
        with contextlib.suppress(WebSocketDisconnect, RuntimeError):
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    return

    def run(self, host: str = "127.0.0.1", port: int = 8001, **kwargs: Any) -> None:
        """Run standalone under uvicorn (blocking)."""
        import uvicorn

        uvicorn.run(self.app, host=host, port=port, **kwargs)
