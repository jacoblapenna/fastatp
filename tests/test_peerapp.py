"""PeerApp: dispatch, emit, call, admit/evict, decoupled execution.

Two PeerApps with real DIDs served over localhost HTTP, real signed records,
and an in-process firehose transport GATED by the reconciler-managed WireGuard
interface (so admit/evict genuinely toggles delivery — the same SimTunnel-style
gate the v0.4 integration test uses, minus real kernel WG).
"""

import asyncio
import base64
import socket
from collections.abc import Callable

import pytest
import uvicorn

from fastatp import (
    DIDServer,
    Envelope,
    Peer,
    PeerApp,
    PeerServer,
    WebResolver,
    build_did_document,
    crypto,
)
from fastatp.authz import MemoryWireGuardInterface, WireGuardReconciler, build_wireguard_service
from fastatp.lex.types import Datetime, Tid
from fastatp.lexicon import Lexicon
from fastatp.peerapp import FrameSource
from fastatp.repo.repo import CommitEvent
from fastatp.repo.sync import commit_frame_body, encode_frame, sync_frame_body
from fastatp.tid import generate_tid

# --- developer-defined lexicons (their namespace, not ours) -------------------


class QuoteRequest(Lexicon):
    nsid = "com.example.fx.quoteRequest"
    context_id: Tid
    pair: str


class Quote(Lexicon):
    nsid = "com.example.fx.quote"
    context_id: Tid
    price: str


class Noise(Lexicon):
    nsid = "com.example.fx.noise"
    context_id: Tid
    at: Datetime


def _wg_key(seed: int) -> str:
    return base64.b64encode(bytes([seed]) * 32).decode()


# --- an in-process frame source gated by the producer's WG interface ----------


class GatedInProcessSource(FrameSource):
    """Delivers a producer PeerServer's firehose to a follower IFF the
    follower's WG pubkey is present on the producer's interface — the same
    'absence = unreachable' gate the reconciler manages, in-process.

    On open (when admitted) it sends a #sync checkpoint of the producer's
    current state first, then live #commit frames — exactly the real
    "subscribe pulls a checkpoint, then follows" flow, so the follower's
    inductive verification never diverges from prior (genesis/capability)
    commits it wasn't present for.
    """

    def __init__(self, producer: PeerServer, interface, follower_pubkey: str) -> None:
        self._producer = producer
        self._interface = interface
        self._follower_pubkey = follower_pubkey
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._seq = 0
        self._original_emit: Callable[[CommitEvent], CommitEvent] | None = None

    def _admitted(self) -> bool:
        return self._follower_pubkey in self._interface.peers()

    async def open(self) -> None:
        if self._admitted():
            repo = self._producer.repo
            body = sync_frame_body(
                repo.did,
                self._producer.seq,
                repo.rev,
                repo.export_car(),
                "2026-07-02T12:00:00Z",
            )
            self._queue.put_nowait(encode_frame("#sync", body))
        original = self._producer._emit

        def hook(event: CommitEvent) -> CommitEvent:
            result = original(event)
            if self._admitted():
                self._seq += 1
                frame = encode_frame(
                    "#commit", commit_frame_body(self._producer.repo.did, self._seq, event)
                )
                self._queue.put_nowait(frame)
            return result

        # deliberately swap the producer's emit hook to observe live commits
        self._producer._emit = hook  # type: ignore[method-assign]
        self._original_emit = original

    async def __aiter__(self):
        while True:
            yield await self._queue.get()

    async def close(self) -> None:
        if self._original_emit is not None:
            self._producer._emit = self._original_emit  # type: ignore[method-assign, assignment]


# --- environment --------------------------------------------------------------


def _bind() -> tuple[socket.socket, int]:
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    return sock, sock.getsockname()[1]


async def _serve_did(doc, port: int, sock: socket.socket):
    async def resolver(slug: str):
        return doc if slug == "" else None

    server = DIDServer(domain=f"localhost:{port}", resolver=resolver)
    uv = uvicorn.Server(uvicorn.Config(server.app, log_level="error"))
    task = asyncio.create_task(uv.serve(sockets=[sock]))  # keep the bound socket: no rebind race
    while not uv.started:
        await asyncio.sleep(0.01)
    return uv, task


class _Peer:
    did: str
    wg: str
    server: PeerServer
    iface: MemoryWireGuardInterface
    recon: WireGuardReconciler
    app: PeerApp


@pytest.fixture
async def two_apps():
    """Agent app + tool app, each its own DID/repo/PeerServer/reconciler, with
    a shared HTTP resolver and mutual in-process gated transports."""
    sock_a, port_a = _bind()
    sock_b, port_b = _bind()
    did_a = f"did:web:localhost%3A{port_a}"
    did_b = f"did:web:localhost%3A{port_b}"
    sk_a = crypto.generate_keypair("k256")
    sk_b = crypto.generate_keypair("p256")
    wg_a, wg_b = _wg_key(1), _wg_key(2)

    doc_a = build_did_document(
        did=did_a,
        signing_key=sk_a.public_key(),
        extra_services=[build_wireguard_service(wg_a, "10.100.0.1")],
    )
    doc_b = build_did_document(
        did=did_b,
        signing_key=sk_b.public_key(),
        extra_services=[build_wireguard_service(wg_b, "10.100.0.2")],
    )

    uv_a, t_a = await _serve_did(doc_a, port_a, sock_a)
    uv_b, t_b = await _serve_did(doc_b, port_b, sock_b)
    resolver = WebResolver()

    agent = _Peer()
    agent.did, agent.wg = did_a, wg_a
    agent.server = PeerServer(Peer(did=did_a, signing_key=sk_a))
    agent.iface = MemoryWireGuardInterface()
    agent.recon = WireGuardReconciler(agent.server, agent.iface, resolver)
    agent.app = PeerApp(agent.server, agent.recon, resolver=resolver)

    tool = _Peer()
    tool.did, tool.wg = did_b, wg_b
    tool.server = PeerServer(Peer(did=did_b, signing_key=sk_b))
    tool.iface = MemoryWireGuardInterface()
    tool.recon = WireGuardReconciler(tool.server, tool.iface, resolver)
    tool.app = PeerApp(tool.server, tool.recon, resolver=resolver)

    yield agent, tool

    await agent.app.aclose()
    await tool.app.aclose()
    await resolver.aclose()
    for uv, task in ((uv_a, t_a), (uv_b, t_b)):
        uv.should_exit = True
        await task


def _src(producer_peer, follower_peer) -> GatedInProcessSource:
    return GatedInProcessSource(producer_peer.server, producer_peer.iface, follower_peer.wg)


def _solo_app() -> PeerApp:
    """A standalone app with no reconciler, for error-branch unit tests."""
    peer = Peer(did="did:web:solo.example", signing_key=crypto.generate_keypair("k256"))
    return PeerApp(PeerServer(peer))


# --- tests --------------------------------------------------------------------


async def test_dispatch_by_type_and_unknown_ignored(two_apps):
    agent, tool = two_apps
    seen = []

    @tool.app.on(QuoteRequest)
    async def handle(req: QuoteRequest, ctx: Envelope) -> None:
        seen.append((req.pair, ctx.issuer_did, ctx.context_id))

    await agent.app.admit(tool.did)  # tool may reach the agent's firehose (to follow it)
    await tool.app.follow(agent.did, source=_src(agent, tool))
    await asyncio.sleep(0.02)

    cid = generate_tid()
    agent.app.emit(QuoteRequest.nsid, "r1", QuoteRequest(context_id=cid, pair="EURUSD"))
    # an unregistered $type on the same firehose must be ignored, not error
    agent.app.emit(Noise.nsid, "n1", Noise(context_id=generate_tid(), at="2026-07-02T12:00:00Z"))
    await asyncio.sleep(0.1)

    assert seen == [("EURUSD", agent.did, cid)]


async def test_call_correlates_reply(two_apps):
    agent, tool = two_apps

    @tool.app.on(QuoteRequest, admit_issuer=True)
    async def price(req: QuoteRequest, ctx: Envelope) -> None:
        tool.app.emit(Quote.nsid, generate_tid(), Quote(context_id=req.context_id, price="1.09"))

    # mutual admission + the tool follows the agent to receive requests
    await agent.app.admit(tool.did)
    await tool.app.admit(agent.did)
    await tool.app.follow(agent.did, source=_src(agent, tool))
    await asyncio.sleep(0.02)

    ctx = generate_tid()
    reply = await agent.app.call(
        tool.did,
        QuoteRequest(context_id=ctx, pair="EURUSD"),
        reply_type=Quote,
        source=_src(tool, agent),
        timeout=5,
    )
    assert isinstance(reply, Quote)
    assert reply.price == "1.09" and reply.context_id == ctx


async def test_call_local_timeout_raises_without_reply(two_apps):
    agent, tool = two_apps
    await tool.app.admit(agent.did)  # agent may reach the tool (to hear a reply)
    # no tool handler emits a reply -> the caller's LOCAL timeout fires
    with pytest.raises(asyncio.TimeoutError):
        await agent.app.call(
            tool.did,
            QuoteRequest(context_id=generate_tid(), pair="EURUSD"),
            reply_type=Quote,
            source=_src(tool, agent),
            timeout=0.3,
        )


async def test_admit_evict_toggles_delivery(two_apps):
    agent, tool = two_apps
    seen = []

    @tool.app.on(QuoteRequest)
    async def handle(req: QuoteRequest, ctx: Envelope) -> None:
        seen.append(req.pair)

    # admit first (a follower cannot reach the firehose until admitted), follow,
    # then records flow
    await agent.app.admit(tool.did)
    await tool.app.follow(agent.did, source=_src(agent, tool))
    await asyncio.sleep(0.02)
    agent.app.emit(
        QuoteRequest.nsid, "live", QuoteRequest(context_id=generate_tid(), pair="DURING")
    )
    await asyncio.sleep(0.1)
    assert seen == ["DURING"]

    # evict -> gate closes -> new records never arrive
    await agent.app.evict(tool.did)
    agent.app.emit(QuoteRequest.nsid, "post", QuoteRequest(context_id=generate_tid(), pair="AFTER"))
    await asyncio.sleep(0.1)
    assert seen == ["DURING"]


async def test_idempotent_redelivery(two_apps):
    agent, tool = two_apps
    fired = []

    @tool.app.on(QuoteRequest)
    async def handle(req: QuoteRequest, ctx: Envelope) -> None:
        fired.append(ctx.record_cid)

    await agent.app.admit(tool.did)
    await tool.app.follow(agent.did, source=_src(agent, tool))
    await asyncio.sleep(0.02)

    agent.app.emit(QuoteRequest.nsid, "once", QuoteRequest(context_id=generate_tid(), pair="X"))
    await asyncio.sleep(0.1)
    assert len(fired) == 1

    # reconnect with a FRESH mirror (unfollow/refollow): the checkpoint re-delivers
    # the already-processed record — the app's CID seen-set must suppress it
    await tool.app.unfollow(agent.did)
    await tool.app.follow(agent.did, source=_src(agent, tool))
    await asyncio.sleep(0.1)

    assert len(fired) == 1  # same record CID -> handler fires once across reconnects


async def test_multi_app_per_process_isolation(two_apps):
    agent, tool = two_apps
    a_seen, b_seen = [], []

    # two apps in one process register handlers for the SAME $type; each must
    # only see its own ingestion, not the other's
    @agent.app.on(QuoteRequest)
    async def a_handle(req: QuoteRequest, ctx: Envelope) -> None:
        a_seen.append(req.pair)

    @tool.app.on(QuoteRequest)
    async def b_handle(req: QuoteRequest, ctx: Envelope) -> None:
        b_seen.append(req.pair)

    await agent.app.admit(tool.did)
    await tool.app.follow(agent.did, source=_src(agent, tool))
    await asyncio.sleep(0.02)

    agent.app.emit(QuoteRequest.nsid, "r", QuoteRequest(context_id=generate_tid(), pair="ONLY_B"))
    await asyncio.sleep(0.1)
    assert b_seen == ["ONLY_B"]
    assert a_seen == []  # agent.app never followed itself


async def test_slow_handler_does_not_stall_ingestion(two_apps):
    """A slow handler must not block the mirror read loop (which would fill the
    producer's queue and trigger ConsumerTooSlow). Records keep being ingested
    while one handler is still running."""
    agent, tool = two_apps
    started, release = [], asyncio.Event()

    @tool.app.on(QuoteRequest)
    async def slow(req: QuoteRequest, ctx: Envelope) -> None:
        started.append(req.pair)
        if req.pair == "SLOW":
            await release.wait()

    await agent.app.admit(tool.did)
    await tool.app.follow(agent.did, source=_src(agent, tool))
    await asyncio.sleep(0.02)

    agent.app.emit(QuoteRequest.nsid, "s", QuoteRequest(context_id=generate_tid(), pair="SLOW"))
    agent.app.emit(QuoteRequest.nsid, "f", QuoteRequest(context_id=generate_tid(), pair="FAST"))
    await asyncio.sleep(0.1)
    # FAST ran even though SLOW is still blocked -> ingestion wasn't stalled
    assert "SLOW" in started and "FAST" in started
    release.set()


async def test_publish_schemas_only_advertised(two_apps):
    _agent, tool = two_apps

    @tool.app.on(QuoteRequest, advertise=True)
    async def public(req: QuoteRequest, ctx: Envelope) -> None: ...

    @tool.app.on(Noise)  # advertise defaults False
    async def private(req: Noise, ctx: Envelope) -> None: ...

    published = tool.app.publish_schemas()
    ids = {doc["id"] for doc in published}
    assert ids == {QuoteRequest.nsid}  # only the advertised lexicon
    assert tool.server.repo.record_count("com.atproto.lexicon.schema") == 1


# --- focused unit coverage: error branches, ordering, transport helper --------


def test_on_requires_nsid():
    from fastatp import Peer, PeerApp, PeerServer, crypto

    app = PeerApp(
        PeerServer(Peer(did="did:web:x.example", signing_key=crypto.generate_keypair("k256")))
    )

    class NoNsid(Lexicon):
        v: int

    async def _noop(r: Lexicon, c: Envelope) -> None: ...

    with pytest.raises(ValueError, match="nsid"):
        app.on(NoNsid)(_noop)


async def test_admit_evict_and_follow_require_config():
    from fastatp import Peer, PeerApp, PeerServer, crypto

    app = PeerApp(
        PeerServer(Peer(did="did:web:x.example", signing_key=crypto.generate_keypair("k256")))
    )
    with pytest.raises(ValueError, match="reconciler"):
        await app.admit("did:web:y.example")
    with pytest.raises(ValueError, match="reconciler"):
        await app.evict("did:web:y.example")
    with pytest.raises(ValueError, match="firehose_url"):
        await app.follow("did:web:y.example")  # no source and no url
    await app.aclose()


def test_websocket_frame_source_url():
    from fastatp.peerapp import WebSocketFrameSource

    assert WebSocketFrameSource("ws://h/x")._full_url() == "ws://h/x"
    assert WebSocketFrameSource("ws://h/x", 5)._full_url() == "ws://h/x?cursor=5"
    assert WebSocketFrameSource("ws://h/x?a=b", 5)._full_url() == "ws://h/x?a=b&cursor=5"


async def test_call_missing_correlation_field(two_apps):
    agent, tool = two_apps
    await tool.app.admit(agent.did)

    class NoCorr(Lexicon):
        nsid = "com.example.fx.nocorr"
        v: int

    with pytest.raises(ValueError, match="correlate"):
        await agent.app.call(
            tool.did, NoCorr(v=1), reply_type=Quote, source=_src(tool, agent), timeout=1
        )


async def test_ordered_handler_preserves_commit_order(two_apps):
    agent, tool = two_apps
    order = []

    @tool.app.on(QuoteRequest, ordered=True)
    async def handle(req: QuoteRequest, ctx: Envelope) -> None:
        if req.pair == "FIRST":
            await asyncio.sleep(0.05)  # would finish last if concurrent
        order.append(req.pair)

    await agent.app.admit(tool.did)
    await tool.app.follow(agent.did, source=_src(agent, tool))
    await asyncio.sleep(0.02)
    agent.app.emit(QuoteRequest.nsid, "a", QuoteRequest(context_id=generate_tid(), pair="FIRST"))
    agent.app.emit(QuoteRequest.nsid, "b", QuoteRequest(context_id=generate_tid(), pair="SECOND"))
    await asyncio.sleep(0.2)
    assert order == ["FIRST", "SECOND"]  # ordered: slow FIRST still lands before SECOND


async def test_unfollow_unknown_is_noop(two_apps):
    _agent, tool = two_apps
    await tool.app.unfollow("did:web:never-followed.example")  # no raise


def test_publish_schemas_empty_when_none_advertised():
    from fastatp import Peer, PeerApp, PeerServer, crypto

    app = PeerApp(
        PeerServer(Peer(did="did:web:x.example", signing_key=crypto.generate_keypair("k256")))
    )

    @app.on(Quote)
    async def h(r, c): ...

    assert app.publish_schemas() == []


# --- push intake (documentless requester posts to a board) -----------


class WantAd(Lexicon):
    nsid = "com.example.board.wantAd"
    context_id: Tid
    seeking: str


async def test_push_intake_documentless_requester(two_apps):
    """A did:key requester that hosts nothing posts a signed want ad to a board
    via push intake; the board verifies it (zero resolution) and dispatches."""
    import httpx

    from fastatp import Repo
    from fastatp.peerapp import PUSH_INTAKE_PATH

    board, _tool = two_apps  # reuse one app as the public board
    seen = []

    @board.app.on(WantAd)
    async def on_ad(ad: WantAd, ctx: Envelope) -> None:
        seen.append((ad.seeking, ctx.issuer_did))

    board.app.enable_push_intake()

    # a fully ephemeral requester: keypair + in-memory repo, no hosted infra
    req_key = crypto.generate_keypair("k256")
    req_did = crypto.encode_did_key(req_key.public_key())
    repo = Repo(req_did, req_key)
    ctx = generate_tid()
    repo.put_record(WantAd.nsid, "ad", WantAd(context_id=ctx, seeking="fx quotes"))
    proof = repo.prove(WantAd.nsid, "ad")

    transport = httpx.ASGITransport(app=board.server.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://board") as client:
        resp = await client.post(PUSH_INTAKE_PATH, content=proof)
        assert resp.status_code == 202
    await asyncio.sleep(0.1)
    assert seen == [("fx quotes", req_did)]  # verified against the did:key, dispatched


async def test_push_intake_rejects_bad_proof_and_policy(two_apps):
    import httpx

    from fastatp import Repo
    from fastatp.peerapp import PUSH_INTAKE_PATH

    board, _tool = two_apps

    @board.app.on(WantAd)
    async def on_ad(ad: WantAd, ctx: Envelope) -> None: ...

    # policy: reject anything over 10 bytes (forces a 403 on a real proof)
    board.app.enable_push_intake(policy=lambda did, nsid, size: size <= 10)

    transport = httpx.ASGITransport(app=board.server.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://board") as client:
        # garbage bytes -> invalid proof -> 400
        bad = await client.post(PUSH_INTAKE_PATH, content=b"not a car")
        assert bad.status_code == 400

        # a valid proof that the policy rejects -> 403
        req_key = crypto.generate_keypair("k256")
        req_did = crypto.encode_did_key(req_key.public_key())
        repo = Repo(req_did, req_key)
        repo.put_record(WantAd.nsid, "ad", WantAd(context_id=generate_tid(), seeking="x"))
        proof = repo.prove(WantAd.nsid, "ad")
        rejected = await client.post(PUSH_INTAKE_PATH, content=proof)
        assert rejected.status_code == 403


async def test_push_intake_off_by_default(two_apps):
    """A peer that never enables push intake exposes no such route."""
    import httpx

    from fastatp.peerapp import PUSH_INTAKE_PATH

    _board, tool = two_apps  # tool never calls enable_push_intake
    transport = httpx.ASGITransport(app=tool.server.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://tool") as client:
        resp = await client.post(PUSH_INTAKE_PATH, content=b"x")
        assert resp.status_code == 404  # route absent — nothing to probe
