"""The workload pattern on PeerApp, end to end — runnable without root.

An **agent** peer and a **tool** peer, each its own DID / repo / PeerServer /
reconciler, cross-admit, and exchange the developer's OWN lexicons (defined
here in ``com.example.*`` — FastATP ships none of these). The agent asks the
tool to do a job; the tool acknowledges (a liveness signal), does the work, and
returns a result; the agent correlates both by ``context_id``. Then the agent
evicts the tool (the bilateral kill switch) and a further request never lands.

This is the async, signed, provenance-carrying substitute for "call a tool and
block": no connection is held, every record is a signed durable object, and the
tool's reply is verifiable by anyone against the tool's DID.

The transport here is an in-process WireGuard-reachability model (real signed
records + real did:web resolution over HTTP; the tunnel itself is simulated so
this runs without root). Kernel enforcement is proven separately in
tests/test_wireguard_netns.py.

Run: python examples/workorder.py
"""

import asyncio
import base64
import socket
from datetime import datetime, timezone

import uvicorn

from fastatp import DIDServer, Peer, PeerApp, PeerServer, WebResolver, build_did_document
from fastatp.authz import MemoryWireGuardInterface, WireGuardReconciler, build_wireguard_service
from fastatp.crypto import generate_keypair
from fastatp.lex.types import Datetime, Tid
from fastatp.lexicon import Lexicon
from fastatp.peerapp import FrameSource
from fastatp.repo.sync import commit_frame_body, encode_frame, sync_frame_body
from fastatp.tid import generate_tid

# --- the developer's own lexicons (com.example.*, not shipped by FastATP) -----


class InspectionRequest(Lexicon):
    nsid = "com.example.ops.inspectionRequest"
    context_id: Tid
    asset: str


class InspectionAck(Lexicon):
    nsid = "com.example.ops.inspectionAck"
    context_id: Tid
    at: Datetime


class InspectionResult(Lexicon):
    nsid = "com.example.ops.inspectionResult"
    context_id: Tid
    finding: str


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# --- in-process gated firehose transport (see the module docstring) -----------


class GatedSource(FrameSource):
    def __init__(self, producer, interface, follower_pubkey):
        self.p, self.iface, self.fpk = producer, interface, follower_pubkey
        self.q: asyncio.Queue = asyncio.Queue()
        self.seq = 0
        self.orig = None

    def admitted(self):
        return self.fpk in self.iface.peers()

    async def open(self):
        if self.admitted():
            r = self.p.repo
            body = sync_frame_body(r.did, self.p.seq, r.rev, r.export_car(), now())
            self.q.put_nowait(encode_frame("#sync", body))
        self.orig = self.p._emit

        def hook(event):
            result = self.orig(event)
            if self.admitted():
                self.seq += 1
                self.q.put_nowait(
                    encode_frame("#commit", commit_frame_body(self.p.repo.did, self.seq, event))
                )
            return result

        self.p._emit = hook

    async def __aiter__(self):
        while True:
            yield await self.q.get()

    async def close(self):
        if self.orig is not None:
            self.p._emit = self.orig


def _bind():
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    return sock, sock.getsockname()[1]


async def _serve(doc, port, sock):
    async def resolver(slug):
        return doc if slug == "" else None

    server = DIDServer(domain=f"localhost:{port}", resolver=resolver)
    uv = uvicorn.Server(uvicorn.Config(server.app, log_level="error"))
    task = asyncio.create_task(uv.serve(sockets=[sock]))  # keep the bound socket: no rebind race
    while not uv.started:
        await asyncio.sleep(0.01)
    return uv, task


async def main() -> None:
    sock_a, port_a = _bind()
    sock_t, port_t = _bind()
    did_agent = f"did:web:localhost%3A{port_a}"
    did_tool = f"did:web:localhost%3A{port_t}"
    sk_a, sk_t = generate_keypair("k256"), generate_keypair("p256")
    wg_a, wg_t = base64.b64encode(b"\x01" * 32).decode(), base64.b64encode(b"\x02" * 32).decode()

    doc_a = build_did_document(
        did=did_agent,
        signing_key=sk_a.public_key(),
        extra_services=[build_wireguard_service(wg_a, "10.100.0.1")],
    )
    doc_t = build_did_document(
        did=did_tool,
        signing_key=sk_t.public_key(),
        extra_services=[build_wireguard_service(wg_t, "10.100.0.2")],
    )
    uv_a, ta = await _serve(doc_a, port_a, sock_a)
    uv_t, tt = await _serve(doc_t, port_t, sock_t)
    resolver = WebResolver()

    agent_srv = PeerServer(Peer(did=did_agent, signing_key=sk_a))
    agent_if = MemoryWireGuardInterface()
    agent = PeerApp(
        agent_srv, WireGuardReconciler(agent_srv, agent_if, resolver), resolver=resolver
    )

    tool_srv = PeerServer(Peer(did=did_tool, signing_key=sk_t))
    tool_if = MemoryWireGuardInterface()
    tool = PeerApp(tool_srv, WireGuardReconciler(tool_srv, tool_if, resolver), resolver=resolver)

    # the tool's handler: acknowledge (liveness), work, return a result
    @tool.on(InspectionRequest)
    async def inspect(req: InspectionRequest, ctx) -> None:
        ack = InspectionAck(context_id=req.context_id, at=now())
        tool.emit(InspectionAck.nsid, generate_tid(), ack)
        await asyncio.sleep(0.05)  # "the work"
        tool.emit(
            InspectionResult.nsid,
            generate_tid(),
            InspectionResult(context_id=req.context_id, finding=f"{req.asset}: nominal"),
        )

    # mutual admission: tool may reach agent (to reply), agent may reach tool (to ask)
    await agent.admit(did_tool)
    await tool.admit(did_agent)
    await tool.follow(did_agent, source=GatedSource(agent_srv, agent_if, wg_t))
    print(f"agent {did_agent}")
    print(f"tool  {did_tool}")
    print("mutually admitted; tool following agent's firehose")

    # the agent correlates the ack (liveness) as it streams, and calls for the result
    ctx = generate_tid()
    acks: list[str] = []

    @agent.on(InspectionAck)
    async def on_ack(ack: InspectionAck, _ctx) -> None:
        acks.append(ack.context_id)
        print(f"agent saw ACK for {ack.context_id} (tool is alive and working)")

    result = await agent.call(
        did_tool,
        InspectionRequest(context_id=ctx, asset="pump-7"),
        reply_type=InspectionResult,
        source=GatedSource(tool_srv, tool_if, wg_a),
        timeout=5,
    )
    assert acks == [ctx], "liveness: the ack must precede the result"
    print(f"agent got RESULT: {result.finding!r} (correlated by context_id {ctx})")

    # the bilateral kill switch: evict the tool; a new request never lands. The
    # revocation is a signed audit record on the grantor's (agent's) firehose.
    from fastatp.authz import Revocation

    await agent.evict(did_tool)
    agent_revs = agent_srv.repo.list_records(Revocation.nsid)
    print(f"agent evicted tool; revocation audit record on agent's firehose: {agent_revs[0][0]}")

    try:
        await agent.call(
            did_tool,
            InspectionRequest(context_id=generate_tid(), asset="pump-8"),
            reply_type=InspectionResult,
            source=GatedSource(tool_srv, tool_if, wg_a),
            timeout=1.0,
        )
        raise SystemExit("BUG: reply arrived after eviction")
    except asyncio.TimeoutError:
        print("post-eviction request timed out locally — tool is cryptographically unreachable")

    await agent.aclose()
    await tool.aclose()
    await resolver.aclose()
    for uv, task in ((uv_a, ta), (uv_t, tt)):
        uv.should_exit = True
        await task
    print("done: admitted, asked, verified reply, evicted, unreachable.")


if __name__ == "__main__":
    asyncio.run(main())
