"""Two peers over a firehose, end to end.

Peer A (producer) signs records into its repo; a PeerServer hosts the repo
and exposes a ``com.atproto.sync.subscribeRepos``-shaped firehose. Peer B
(consumer) subscribes over WebSocket, receives commits in order, resolves
A's DID, verifies each commit signature, and parses records by ``$type``.

The script then demonstrates:
1. WAN-drop immunity — B disconnects, A keeps committing, B reconnects with
   its cursor and replays only what it missed.
2. A multi-party variant — a third peer C contributes to the same task
   thread; all records share a ``context_id``.

Run: python examples/two_peers.py
"""

import asyncio
import socket
from datetime import datetime, timezone

import uvicorn
import websockets

from fastatp import Lexicon, Peer, PeerServer, RepoMirror, build_did_document
from fastatp.crypto import generate_keypair
from fastatp.lex.types import Datetime, Did, Tid
from fastatp.lexicon import parse_record
from fastatp.repo.sync import decode_frame
from fastatp.server.peer_server import FIREHOSE_PATH
from fastatp.tid import generate_tid

DID_A = "did:web:peer-a.example"
DID_C = "did:web:peer-c.example"


class Task(Lexicon):
    nsid = "com.example.agent.task"
    assigned_to: Did
    context_id: Tid
    instructions: str
    created_at: Datetime


class TaskResult(Lexicon):
    nsid = "com.example.agent.taskResult"
    context_id: Tid
    summary: str
    created_at: Datetime


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


async def main() -> None:
    # --- peer A: identity + repo + firehose ---------------------------------
    key_a = generate_keypair("k256")
    doc_a = build_did_document(did=DID_A, signing_key=key_a.public_key())
    peer_a = Peer(did=DID_A, signing_key=key_a)
    server_a = PeerServer(peer_a, backfill_window=64)

    # bind the socket ourselves and hand it to uvicorn: no close/rebind race
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    config = uvicorn.Config(server_a.app, log_level="error")
    uv_server = uvicorn.Server(config)
    server_task = asyncio.create_task(uv_server.serve(sockets=[sock]))
    while not uv_server.started:
        await asyncio.sleep(0.01)
    url = f"ws://127.0.0.1:{port}{FIREHOSE_PATH}"
    print(f"peer A firehose live at {url}")

    # --- peer B: verified consumer ------------------------------------------
    async def resolver(did: str):
        # stand-in for web_resolver: in production this is an HTTPS fetch
        return doc_a if did == DID_A else None

    mirror = RepoMirror(DID_A, resolver=resolver)

    context_id = generate_tid()

    # A signs a task into its repo; B receives it over the firehose
    async with websockets.connect(url) as ws:
        await asyncio.sleep(0.05)
        server_a.put_record(
            Task.nsid,
            generate_tid(),
            Task(
                assigned_to=DID_C,
                context_id=context_id,
                instructions="inspect wellhead pressure trend",
                created_at=now(),
            ),
        )
        header, body = decode_frame(await asyncio.wait_for(ws.recv(), 5))
        assert header["t"] == "#commit"
        # verifies A's commit signature via the resolved DID document, then
        # inductively re-derives the MST root from the announced ops
        new_records = await mirror.apply_commit(body)
        task = parse_record(new_records[0]["record"])  # dispatch on $type
        assert isinstance(task, Task)
        print(f"B verified+parsed {type(task).nsid}: {task.instructions!r} (seq {body['seq']})")

    # --- WAN drop: B is offline while A keeps committing --------------------
    for i in range(3):
        server_a.put_record(
            Task.nsid,
            generate_tid(),
            Task(
                assigned_to=DID_C,
                context_id=context_id,
                instructions=f"follow-up #{i}",
                created_at=now(),
            ),
        )
    print(f"B disconnected; A committed 3 more (stream at seq {server_a.seq})")

    # reconnect with the kept cursor: replay exactly the missed events.
    # handle_frame dispatches on frame type (#commit / #sync checkpoint /
    # #info), so this loop also survives a cursor beyond the backfill window
    async with websockets.connect(f"{url}?cursor={mirror.seq}") as ws:
        replayed = 0
        while mirror.seq < server_a.seq:
            await mirror.handle_frame(await asyncio.wait_for(ws.recv(), 5))
            replayed += 1
        print(f"B resumed from cursor and replayed {replayed} missed frames")

    # --- multi-party: peer C answers in the same context --------------------
    key_c = generate_keypair("p256")
    peer_c = Peer(did=DID_C, signing_key=key_c)
    server_c = PeerServer(peer_c)
    server_c.put_record(
        TaskResult.nsid,
        generate_tid(),
        TaskResult(context_id=context_id, summary="pressure nominal", created_at=now()),
    )
    result = parse_record(
        server_c.repo.get_record(TaskResult.nsid, server_c.repo.list_records(TaskResult.nsid)[0][0])
    )
    thread = [task, result]
    assert {r.context_id for r in thread} == {context_id}
    print(
        f"multi-party thread shares context_id={context_id}: "
        f"{[type(r).nsid.split('.')[-1] for r in thread]}"
    )

    uv_server.should_exit = True
    await server_task
    print("done: signed, hosted, subscribed, verified, dropped, resumed.")


if __name__ == "__main__":
    asyncio.run(main())
