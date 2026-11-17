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
import bisect
import contextlib
import inspect
import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from starlette.applications import Starlette
from starlette.requests import HTTPConnection, Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from fastatp.authz.capability import ACTION_SUBSCRIBE, live_capabilities
from fastatp.authz.wireguard import overlay_address
from fastatp.cid import CID
from fastatp.crypto import decode_did_key
from fastatp.exceptions import CryptoError, FastATPError, RepoError
from fastatp.peer import Peer
from fastatp.repo.repo import CommitEvent, RecordLike, Repo, WriteOp
from fastatp.repo.storage import Storage
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


@dataclass(frozen=True)
class ResolvedCaller:
    """Who is asking, and what proved it.

    ``proof`` is the scope of the evidence: ``"signature"`` when the caller
    signed a challenge with the key its ``did:key`` names; ``"repo:<did>"``
    when the identity was established by records in that repo (a WireGuard
    tunnel bound by a capability there, or a delegation chain rooted there);
    ``None`` when the resolver did not say. The capability rule needs only
    the DID. The owner rule needs the proof to be a signature or to come
    from the repo being read, because any hosted repo's records can name
    any DID, and a record in *another* repo must never make someone the
    owner of this one.
    """

    did: str
    proof: str | None = None


CallerResolver = Callable[
    [HTTPConnection],
    "str | ResolvedCaller | Awaitable[str | ResolvedCaller | None] | None",
]
"""``resolve(connection) -> did | ResolvedCaller | None`` (sync or async): the
doorman's identity seam. Maps an inbound firehose/getRepo connection to the
admitted caller's DID, or ``None`` if it cannot be tied to one. A bare DID is
an unscoped identity: good for the capability rule, never for owner pull.
Default: ``TunnelIdentityResolver`` (WireGuard cryptokey routing — source
tunnel IP -> DID, scoped to the repo whose capability bound it). Injectable so
tests supply a fake and other enforcement planes (e.g. a tailnet) supply
their own."""

OwnerRule = Callable[[Repo], "str | None"]
"""``rule(repo) -> did:key | None``: a builder's answer to "which pen writes
this repo right now". Passed as ``owner_pull=`` to :meth:`PeerServer.register`
in place of the default rule (the caller's key verifies the head commit) when
the host knows better — e.g. from the identity's chain head, which names a
freshly rotated pen before it has signed anything."""


def _not_served(did: str | None) -> str:
    """One message for "not hosted" and "hosted, gated, refused": a gated
    repo's existence is never leaked to a stranger."""
    return f"no repo {did!r} is served to this caller"


def _repo_not_found(did: str | None) -> JSONResponse:
    return JSONResponse({"error": "RepoNotFound", "message": _not_served(did)}, status_code=404)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class _HostedRepo:
    """Per-repo firehose state: one repo plus its own seq, backfill, subscribers.

    Splitting *server* from *repo* means each repo must carry its own
    **position** — a private-per-request repo that dies must not tear its page
    numbers out of a sequence other followers still read. Every hosted repo
    owns its ``seq``, backfill window, and subscriber queue set; fan-out never
    crosses repos.

    The seq is the repo's: a commit's comes from the head swap that landed it
    (``CommitEvent.seq``), and every other frame takes the next one through
    ``Repo.advance_seq``, so with a durable head store a restart resumes
    where the last delivered frame left off and followers' cursors keep
    working. A number is durable before its frame goes out; frames enter the
    backfill in seq order whatever order their coroutines finish in.
    """

    def __init__(
        self,
        repo: Repo,
        backfill_window: int,
        *,
        public: bool = False,
        owner_pull: bool | OwnerRule = False,
        expires_at: datetime | None = None,
    ) -> None:
        self.repo = repo
        self.backfill_window = backfill_window
        self.public = public
        self.owner_pull = owner_pull
        self.expires_at = expires_at
        self._backfill: list[tuple[int, bytes]] = []
        self._queues: set[asyncio.Queue[tuple[int, bytes]]] = set()
        self._announced = False

    @property
    def did(self) -> str:
        return self.repo.did

    @property
    def seq(self) -> int:
        return self.repo.seq

    async def announce(self) -> None:
        """A host announces the account before its commits: relays ignore
        #commit/#sync events for accounts not known to be active (sync spec).
        Once per hosting; every emit makes sure it happened."""
        if self._announced:
            return
        self._announced = True
        await self.emit_identity()
        await self.emit_account(active=True)

    def is_expired(self, now: datetime | None = None) -> bool:
        if self.expires_at is None:
            return False
        return self.expires_at <= (now or datetime.now(UTC))

    def _emit_frame(self, seq: int, frame: bytes) -> None:
        """Backfill a frame whose seq is already durable, in seq order, and
        fan it out. No await in here: order is decided by the seq."""
        bisect.insort(self._backfill, (seq, frame))
        del self._backfill[: max(0, len(self._backfill) - self.backfill_window)]
        for queue in list(self._queues):
            try:
                queue.put_nowait((seq, frame))
            except asyncio.QueueFull:
                # slow consumer: evict rather than grow without bound
                self._queues.discard(queue)
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait((_EVICTED, b""))

    async def emit(self, event: CommitEvent) -> CommitEvent:
        """Announce a commit under the seq its head swap assigned."""
        await self.announce()
        frame = encode_frame("#commit", commit_frame_body(self.repo.did, event.seq, event))
        self._emit_frame(event.seq, frame)
        return event

    async def emit_if(self, event: CommitEvent | None) -> CommitEvent | None:
        """``emit``, unless the write turned out to be a no-op (``None``: a
        concurrent writer already made exactly this change; its own frame
        announced it)."""
        return await self.emit(event) if event is not None else None

    async def emit_account(self, *, active: bool, status: str | None = None) -> None:
        await self.announce()
        seq = await self.repo.advance_seq()
        body = account_frame_body(self.did, seq, _now_iso(), active=active, status=status)
        self._emit_frame(seq, encode_frame("#account", body))

    async def emit_sync(self) -> None:
        """Announce the current head as a ``#sync`` event: what a host does
        after a push lands in a frozen repo through ``import_car``, which
        commits nothing and so emits nothing by itself."""
        await self.announce()
        rev, car = self.repo.rev, self.repo.commit_car()
        seq = await self.repo.advance_seq()
        self._emit_frame(
            seq, encode_frame("#sync", sync_frame_body(self.did, seq, rev, car, _now_iso()))
        )

    async def emit_identity(self, *, handle: str | None = None) -> None:
        await self.announce()
        seq = await self.repo.advance_seq()
        body = identity_frame_body(self.did, seq, _now_iso(), handle=handle)
        self._emit_frame(seq, encode_frame("#identity", body))

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
        await self.announce()  # a subscriber is the first thing some hosts hear
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


AddressBinding = tuple[str, str]
"""What the doorman learns from an overlay address: ``(DID, the DID of the
repo whose capability granted the key)``."""

AddressLookup = Callable[[str], "AddressBinding | Awaitable[AddressBinding | None] | None"]
"""A builder's cache for the doorman: overlay address -> ``(DID, granting repo
DID)``, or ``None`` for a miss. Sync or async. It lives in the builder's own
store and is kept current as repo owners write and delete capabilities in the
repos this host serves (the commit path sees both). It is for speed, never
authority: a hit is checked against the capability currently in the named
repo before it names anyone, so a wrong or stale entry costs a lookup and can
never admit."""


class TunnelIdentityResolver:
    """Default doorman identity: source tunnel IP -> DID via signed capabilities.

    WireGuard binds each admitted peer's static public key to one overlay
    address (derived from the key: ``overlay_address``), so a connection's
    source address is an unspoofable identity *inside the tunnel*. The
    address -> DID map is derived from the keys in every hosted repo's live
    capabilities (signed data) — no new state and no address in any record.
    A caller whose source address derives from no granted key maps to
    ``None`` and is refused for gated repos (fail-closed).

    ``source_address`` is the seam for where the address comes from (the
    connection's client address by default); another enforcement plane, or a
    test presenting a derived address, supplies its own.

    ``lookup`` is the seam for scale: without one, every resolution walks
    every hosted repo's live capabilities (right for a peer with a few repos).
    With one, a hit is used after the doorman confirms the named repo still
    holds a live capability for that DID whose key derives this very address,
    and a miss or a failed confirmation falls back to the walk. So the cache
    decides nothing: it only says where to look first.
    """

    def __init__(
        self,
        server: PeerServer,
        *,
        source_address: Callable[[HTTPConnection], str | None] | None = None,
        lookup: AddressLookup | None = None,
    ) -> None:
        self._server = server
        self._source = source_address or _source_ip
        self._lookup = lookup

    async def __call__(self, conn: HTTPConnection) -> ResolvedCaller | None:
        ip = self._source(conn)
        if ip is None:
            return None
        bound: AddressBinding | None = None
        if self._lookup is not None:
            hit = self._lookup(ip)
            if inspect.isawaitable(hit):
                hit = await hit
            if hit is not None and self._server._binding_is_live(ip, hit[0], hit[1]):
                bound = hit
        if bound is None:
            bound = self._server._ip_to_did().get(ip)  # the walk: authoritative, slow
        if bound is None:
            return None
        did, repo_did = bound
        return ResolvedCaller(did, proof=f"repo:{repo_did}")


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
    def owner_pull(self) -> bool | OwnerRule:
        return self._host.owner_pull

    @property
    def expires_at(self) -> datetime | None:
        return self._host.expires_at

    async def put_record(
        self, collection: str, rkey: str, record: RecordLike
    ) -> CommitEvent | None:
        return await self._host.emit_if(await self._host.repo.put_record(collection, rkey, record))

    async def delete_record(self, collection: str, rkey: str) -> CommitEvent | None:
        return await self._host.emit_if(await self._host.repo.delete_record(collection, rkey))

    async def apply_writes(self, writes: list[WriteOp]) -> CommitEvent | None:
        return await self._host.emit_if(await self._host.repo.apply_writes(writes))

    async def emit_account(self, *, active: bool = True, status: str | None = None) -> None:
        """Re-announce this repo's hosting status (#account)."""
        await self._host.emit_account(active=active, status=status)

    async def emit_identity(self, *, handle: str | None = None) -> None:
        """Prod subscribers to refresh this DID's identity (#identity)."""
        await self._host.emit_identity(handle=handle)

    async def emit_sync(self) -> None:
        """Announce the head to subscribers after a push landed through
        ``repo.import_car`` (which emits nothing by itself)."""
        await self._host.emit_sync()

    async def expire(self) -> None:
        """Retire this repo now — the channel-owner's "TTL reached / done with
        it" intent. A semantic alias that **delegates** to :meth:`unregister`
        so the two can never drift; kept as the name a channel reads
        naturally."""
        await self.unregister()

    async def unregister(self) -> None:
        """Stop hosting this repo and cut its live subscribers."""
        await self._server.unregister(self.did)


class PeerServer:
    """Hosts a peer's repos; exposes the firehose and checkpoint routes."""

    def __init__(
        self,
        peer: Peer,
        repo: Repo,
        backfill_window: int = 512,
        *,
        identity_resolver: CallerResolver | None = None,
        warn_on_proxy: bool = True,
        proxy_warn_threshold: int = 8,
    ) -> None:
        """Host ``repo`` as ``peer``'s public firehose. For a fresh repo use
        ``await PeerServer.create(peer)``, which signs the genesis commit."""
        if peer.did is None:
            raise ValueError("PeerServer requires a Peer with a did")
        if repo.did != peer.did:
            raise ValueError(f"the primary repo is for {repo.did}, not this peer's {peer.did}")
        self.peer = peer
        self.backfill_window = backfill_window
        primary = _HostedRepo(
            repo,
            backfill_window,
            public=True,  # the peer's own repo is its public firehose
        )
        self._primary = primary
        self._hosts: dict[str, _HostedRepo] = {primary.did: primary}
        self._retiring: set[asyncio.Task[None]] = set()
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

    @classmethod
    async def create(
        cls,
        peer: Peer,
        repo: Repo | None = None,
        *,
        repo_store: BlockStore | None = None,
        storage: Storage | None = None,
        backfill_window: int = 512,
        **kwargs: Any,
    ) -> PeerServer:
        """A server for ``peer``, announced on its firehose. Over ``repo`` if
        given, else over a fresh one whose genesis the peer's signer signs, in
        ``storage`` (one database for blocks, heads and blobs) or over a bare
        ``repo_store`` for its blocks. ``PeerServer(peer, repo)`` builds one
        without announcing; it announces on the first emit or subscriber."""
        if repo is None:
            repo = await peer.create_repo(store=repo_store, storage=storage)
        server = cls(peer, repo, backfill_window, **kwargs)
        await server._primary.announce()
        return server

    async def register(
        self,
        repo: Repo,
        *,
        public: bool = False,
        owner_pull: bool | OwnerRule = False,
        backfill_window: int | None = None,
        ttl: float | None = None,
        expires_at: datetime | None = None,
    ) -> RepoHandle:
        """Host ``repo`` (routed by its DID) and return its write handle.

        ``public=False`` (the default — gated) marks the repo for the doorman;
        ``public=True`` opts it into open reads (a board). Writes go **only**
        through the returned handle — there is no string-DID write API (see
        ``RepoHandle``).

        ``owner_pull`` lets the pen that writes a gated repo, and the identity
        itself through a delegated device, read it back with no capability
        record: a host serving a repo to its own pen is that peer reaching
        its own storage, not a subscriber. Off by default, so a host must
        choose it per repo. ``True`` uses the default rule — the
        caller is a ``did:key`` whose key verifies the head commit — which
        admits a rotated-out pen until the new one pushes; pass an
        :data:`OwnerRule` when the host knows the current pen (its chain
        head). The caller still has to prove the pen (WireGuard identity or
        the browser gate's signed knock); the capability rule applies to
        everyone else exactly as before.

        Owner pull decides who *reads*; frozen decides that this host cannot
        write. An ``owner_pull`` registration requires a frozen repo
        (``Repo(did, None, ...)``, see :attr:`Repo.frozen`): a repo served to
        its own pen is one whose pen lives elsewhere, and a host holding a
        pen for it is the custody shape the philosophy forbids, so the
        package says so at registration. The rule is one-way: a frozen repo
        may be registered without owner pull (a public mirror), and a penned
        repo without owner pull is this peer's own. Pushes land through
        ``repo.import_car`` with the owner's key, and :meth:`RepoHandle.emit_sync`
        announces the new head to subscribers.

        What keeps a hosted repo honest is the owner's signature, verified
        by every consumer, as on any atproto host; that is not FastATP's
        rule but atproto's. Frozen makes the same rule hold inside this
        host's code: with no pen there is no path here that makes a commit.

        A whole-repo TTL retires the repo automatically: pass ``ttl`` (seconds
        from now) or ``expires_at`` (an aware datetime). Past its expiry the
        repo is unroutable (``RepoNotFound``) and its subscribers are cut — the
        lifecycle of an ephemeral channel. Reaping is lazy (on the next
        access) and eager (:meth:`reap_expired`); the block store is released
        when the last reference to the retired repo drops.
        """
        if repo.did in self._hosts:
            raise ValueError(f"already hosting a repo for {repo.did!r}")
        if owner_pull and not repo.frozen:
            raise ValueError(
                f"owner_pull for {repo.did!r} needs a frozen repo (Repo(did, None, ...)): a "
                "host serving a repo to its own pen never holds that pen"
            )
        if ttl is not None and expires_at is not None:
            raise ValueError("pass ttl or expires_at, not both")
        if ttl is not None:
            expires_at = datetime.now(UTC) + timedelta(seconds=ttl)
        host = _HostedRepo(
            repo,
            backfill_window or self.backfill_window,
            public=public,
            owner_pull=owner_pull,
            expires_at=expires_at,
        )
        self._hosts[repo.did] = host
        await host.announce()
        return RepoHandle(self, host)

    async def unregister(self, did: str) -> None:
        """Stop hosting ``did``'s repo and poison its live subscribers.

        The primary repo cannot be unregistered (it is the peer's own repo).
        Unknown DIDs are a no-op.
        """
        host = self._hosts.get(did)
        if host is None:
            return
        if host is self._primary:
            raise ValueError("cannot unregister the primary repo")
        self._hosts.pop(host.did, None)
        await self._retire(host)

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
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # no loop: nobody to announce to
            host.close_subscribers()
            return
        # a sync lookup found it expired: retire it off the request's back
        task = loop.create_task(self._retire(host))
        self._retiring.add(task)
        task.add_done_callback(self._retiring.discard)

    async def _retire(self, host: _HostedRepo) -> None:
        # tell still-connected subscribers the account is no longer hosted
        # here before poisoning them (sync spec #account semantics)
        await host.emit_account(active=False, status="deactivated")
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

    async def reap_expired(self, now: datetime | None = None) -> list[str]:
        """Retire every expired hosted repo now; returns the reaped DIDs.

        Lazy reaping already keeps expired repos unroutable, but a long-lived
        ephemeral repo with no further reads would otherwise linger until
        touched; call this periodically to reclaim eagerly.
        """
        moment = now or datetime.now(UTC)
        reaped = [h for h in self._hosts.values() if h.is_expired(moment)]
        for host in reaped:
            self._hosts.pop(host.did, None)
            await self._retire(host)
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
    def _backfill(self) -> list[tuple[int, bytes]]:
        return self._primary._backfill

    @property
    def _queues(self) -> set[asyncio.Queue[tuple[int, bytes]]]:
        return self._primary._queues

    def _sync_frame(self) -> bytes:
        return self._primary.sync_frame()

    async def _catch_up(self, websocket: WebSocket, cursor: int | None) -> int | None:
        return await self._primary.catch_up(websocket, cursor)

    # --- writes (broadcast on commit) ------------------------------------------

    async def put_record(
        self, collection: str, rkey: str, record: RecordLike
    ) -> CommitEvent | None:
        return await self._emit_if(await self.repo.put_record(collection, rkey, record))

    async def delete_record(self, collection: str, rkey: str) -> CommitEvent | None:
        return await self._emit_if(await self.repo.delete_record(collection, rkey))

    async def apply_writes(self, writes: list[WriteOp]) -> CommitEvent | None:
        return await self._emit_if(await self.repo.apply_writes(writes))

    async def _emit(self, event: CommitEvent) -> CommitEvent:
        return await self._primary.emit(event)

    async def _emit_if(self, event: CommitEvent | None) -> CommitEvent | None:
        return await self._emit(event) if event is not None else None

    async def emit_account(self, *, active: bool = True, status: str | None = None) -> None:
        """Re-announce the primary repo's hosting status (#account)."""
        await self._primary.emit_account(active=active, status=status)

    async def emit_identity(self, *, handle: str | None = None) -> None:
        """Prod subscribers to refresh the primary DID's identity (#identity)."""
        await self._primary.emit_identity(handle=handle)

    # --- the doorman (per-repo read gating) ------------------------------------

    def _ip_to_did(self) -> dict[str, tuple[str, str]]:
        """Source overlay address -> (DID, the repo whose capability granted
        the key), from every hosted repo's live capabilities.

        The address is derived from the capability's ``wg_public_key``; no
        record ever names an address, so a record cannot claim one it does
        not hold the key for. Rebuilt per lookup: repos are few and
        in-memory, and a stale map must never outlive a revocation. The repo
        is kept because a grant is only that repo's word: it admits the DID
        to repos that hold a capability for it, and makes it an *owner* only
        of the repo that wrote the grant.

        Scale note: this walk is O(all capabilities this host serves), which
        is right for a peer hosting a few repos and wrong for a host serving
        millions. That host runs several instances, each with its own
        WireGuard interface, and keeps "this address is this DID, granted in
        this repo" as a cache in its own database, updated as repo owners
        write and delete capabilities in the repos it hosts (the host never
        writes one), and hands it to :class:`TunnelIdentityResolver` as
        ``lookup``. The walk is then the fallback, and every hit is checked
        against the capability currently in the named repo
        (:meth:`_binding_is_live`) before it names anyone.
        """
        mapping: dict[str, tuple[str, str]] = {}
        for host in self._hosts.values():
            for subject, (_rkey, cap) in live_capabilities(host.repo).items():
                if cap.wg_public_key:
                    try:
                        address = overlay_address(cap.wg_public_key)
                    except FastATPError:
                        continue  # a malformed key grants nobody
                    mapping[address] = (subject, host.did)
        return mapping

    def _binding_is_live(self, address: str, did: str, repo_did: str) -> bool:
        """Is "``address`` is ``did``, granted in ``repo_did``" what that repo
        says right now? The capability currently in the repo must name the
        DID and carry a key that derives exactly this address: a cache entry
        that survived a revocation or a key rotation fails here."""
        host = self._hosts.get(repo_did)
        if host is None:
            return False
        entry = live_capabilities(host.repo).get(did)
        if entry is None or not entry[1].wg_public_key:
            return False
        try:
            return overlay_address(entry[1].wg_public_key) == address
        except FastATPError:
            return False

    async def _resolve_caller(self, conn: HTTPConnection) -> ResolvedCaller | None:
        result = self.identity_resolver(conn)
        if inspect.isawaitable(result):
            result = await result
        if result is None or isinstance(result, ResolvedCaller):
            return result
        return ResolvedCaller(result)  # a bare DID: identity without a stated proof

    async def _admit_read(self, conn: HTTPConnection, host: _HostedRepo) -> tuple[bool, str]:
        """Doorman verdict for a read of ``host``: ``(allowed, reason)``.

        Public repos are open. A gated repo admits its own pen when the host
        opted the repo into ``owner_pull``, and otherwise a caller only when
        the connection resolves to a DID that holds a live ``subscribe``
        capability in **that repo's own** records. Both failure modes (no
        identity, not admitted) fail closed.
        """
        if host.public:
            return True, ""
        return self._admit_caller(conn, await self._resolve_caller(conn), host)

    def _admit_caller(
        self, conn: HTTPConnection, caller: ResolvedCaller | None, host: _HostedRepo
    ) -> tuple[bool, str]:
        """:meth:`_admit_read` with the caller already resolved."""
        if host.public:
            return True, ""
        if caller is None:
            self._warn_if_proxy(conn, host)
            return False, "caller could not be tied to a tunnel identity"
        if host.owner_pull is not False and self._is_owner(caller, host):
            return True, ""
        entry = live_capabilities(host.repo).get(caller.did)
        if entry is None or ACTION_SUBSCRIBE not in entry[1].actions:
            return False, f"{caller.did} is not admitted to {host.did}"
        return True, ""

    async def _open_read(self, conn: HTTPConnection, did: str | None) -> _HostedRepo | None:
        """The host a single-repo read may proceed against, or ``None``.

        ``None`` covers both "not hosted here" and "hosted, gated, and this
        caller is refused". A public host returns at once, needing no caller.
        For a gated or an absent DID the caller is resolved first, so the two
        run the same checks and answer identically (``RepoNotFound``): a
        gated repo's existence is never leaked to a stranger by status code,
        and a knock for a DID this server does not host still burns its
        nonce. What remains is timing: a gated refusal also scans that repo's
        capabilities, work an absent DID has nothing to do, a difference of
        an in-memory scan that only a caller presenting an identity can
        measure. ``listRepos`` keeps the same rule per repo.
        """
        host = self._resolve_host(did)
        if host is not None and host.public:
            return host  # open to all: no caller to resolve, nothing to hide
        # gated or absent: resolve first, so both run the same checks
        caller = await self._resolve_caller(conn)
        if host is None:
            return None
        allowed, _reason = self._admit_caller(conn, caller, host)
        return host if allowed else None

    @staticmethod
    def _is_owner(caller: ResolvedCaller, host: _HostedRepo) -> bool:
        """Is ``caller`` the owner of ``host``'s repo: the pen that writes it,
        or the identity itself?

        The proof must be this repo's own: a signature by the pen, or a
        binding or delegation chain rooted in **this** repo's records. An
        unscoped DID, or one established by another hosted repo's records,
        is never an owner here, whatever it says — any repo can write a
        record naming any DID. The identity itself (``caller.did ==
        host.did``) only ever resolves through a delegation in this repo,
        so with that proof it is the identity reading its own repo through
        one of its devices. Any other document DID is a different identity,
        never a pen.
        """
        here = f"repo:{host.did}"
        if caller.did.startswith("did:key:") and caller.proof in ("signature", here):
            # a pen: is it the one that writes this repo? (a device repo's DID
            # is its own pen, so this covers a pen pulling its own repo too)
            if callable(host.owner_pull):
                return host.owner_pull(host.repo) == caller.did
            try:
                return host.repo.commit.verify(decode_did_key(caller.did))
            except (CryptoError, ValueError):  # not a key at all: not an owner, not an error
                return False
        return caller.did == host.did and caller.proof == here

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
        host = await self._open_read(request, did)
        if host is None:
            return _repo_not_found(did)
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
        # resolve the caller ONCE for the whole listing: a browser knock is a
        # single-use nonce, and resolving per repo would spend it on the first
        caller = await self._resolve_caller(request)
        visible = []
        for did in sorted(self._hosts):
            if cursor is not None and did <= cursor:
                continue
            host = self._resolve_host(did)
            if host is None:
                continue
            allowed, _reason = self._admit_caller(request, caller, host)
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
        host = await self._open_read(request, did)
        if host is None:
            return _repo_not_found(did)
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
        host = await self._open_read(request, did)
        if host is None:
            return _repo_not_found(did)
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
        host = await self._open_read(request, did)
        if host is None:
            return _repo_not_found(did)
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
        host = await self._open_read(request, did)
        if host is None:
            return _repo_not_found(did)
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
        host = await self._open_read(websocket, did)
        if host is None:
            await websocket.send_bytes(encode_error_frame("RepoNotFound", _not_served(did)))
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

    async def serve(self, host: str = "127.0.0.1", port: int = 8001, **kwargs: Any) -> None:
        """Serve on the running event loop; :meth:`run` starts its own.

        The write path is async, so a program whose ``main`` awaits
        ``Repo.create`` or ``Identity.bootstrap`` serves with this.
        """
        import uvicorn

        await self._primary.announce()
        await uvicorn.Server(uvicorn.Config(self.app, host=host, port=port, **kwargs)).serve()
