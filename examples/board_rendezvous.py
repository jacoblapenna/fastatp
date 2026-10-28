"""Symmetric board rendezvous: a documentless requester and a private service
meet at a public board and complete a **private** exchange — end to end.

Nobody exposes a
private intake: first contact happens at a public board, symmetrically, and
then the conversation goes dark down an ephemeral WireGuard-reachable channel.

    requester ──want ad + self-attestation──▶  BOARD  ◀──lurks── private service
                                                 │                      │
        ◀───────── engagement attestation ───────┘   (spins an ephemeral
        │            (signed by the service)              did:key channel,
        └── follows the channel, reads the private reply  admits requester by key,
            (a second board lurker is REFUSED the reply)  writes the reply into it)

What is LIBRARY vs EXAMPLE:
- Library (under test): open_private_channel / attest / reply_private /
  follow_attested, the Attestation record, the per-repo doorman that enforces
  isolation, and whole-repo TTL.
- Example (yours to shape): the Board, the WantAd/Inspection lexicons, and any
  admission/reputation policy. The board here is a faithful **proof relay** — it
  forwards each poster's ORIGINAL signed proof, so every lurker verifies against
  the poster's own DID; the board never re-signs and is never trusted for
  content.

Run: python examples/board_rendezvous.py
"""

from __future__ import annotations

import asyncio
import socket
from datetime import datetime, timedelta, timezone

import uvicorn
import websockets

from fastatp import Peer, PeerServer, build_did_document, crypto
from fastatp.authz.attestation import Attestation, build_attestation
from fastatp.authz.wireguard import generate_wireguard_keypair
from fastatp.lexicon import Lexicon
from fastatp.peerapp import Envelope, PeerApp
from fastatp.repo.sync import decode_frame
from fastatp.server.peer_server import FIREHOSE_PATH

SERVICE_DID = "did:web:inspector.example"
CHANNEL_TTL = 120.0


# --- the DEVELOPER's lexicons (example only — never shipped by the library) ---


class WantAd(Lexicon):
    nsid = "com.example.wantad"
    context_id: str
    need: str


class Inspection(Lexicon):
    nsid = "com.example.inspection"
    context_id: str
    finding: str


# --- the EXAMPLE board: a faithful relay of signed proofs --------------------


class Board:
    """A public bulletin board. It relays each posted proof to every lurker
    verbatim, so signatures survive and each lurker verifies against the
    poster's own DID. It is dumb pipe, not a trusted party."""

    def __init__(self) -> None:
        self._lurkers: list = []
        self.posts: list[bytes] = []

    def lurk(self, callback) -> None:
        self._lurkers.append(callback)

    async def post(self, proof: bytes) -> None:
        self.posts.append(proof)
        for callback in list(self._lurkers):
            await callback(proof)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _bind() -> tuple[socket.socket, int]:
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    return sock, sock.getsockname()[1]


def _as_resolver(conn):
    # loopback stand-in for WireGuard cryptokey routing: a connection's identity
    # is the DID it presents via ?as= (in production this is the unspoofable
    # source tunnel IP the kernel bound at handshake).
    return conn.query_params.get("as")


def _latest_engagement(server, context_id: str, ephemeral_did: str):
    """Find + prove the service-signed engagement attestation just emitted."""
    for rkey, _cid in server.repo.list_records(Attestation.nsid):
        data = server.repo.get_record(Attestation.nsid, rkey)
        if data and data["context_id"] == context_id and data["ephemeral_did"] == ephemeral_did:
            return server.repo.prove(Attestation.nsid, rkey)
    raise LookupError("engagement attestation not found")


async def run_board_rendezvous() -> dict:
    """Drive the whole exchange; return a result dict for assertions/printing."""
    # --- identities ---------------------------------------------------------
    service_key = crypto.generate_keypair("k256")
    service_doc = build_did_document(did=SERVICE_DID, signing_key=service_key.public_key())

    async def resolver(did):
        return service_doc if did == SERVICE_DID else None

    req_key = crypto.generate_keypair("k256")
    req_did = crypto.encode_did_key(req_key.public_key())
    _req_wg_priv, req_wg = generate_wireguard_keypair()

    snoop_did = "did:key:zSnoopingLurker"

    # --- the private service: serves its channels, gates them by ?as= -------
    sock, port = _bind()
    host_port = f"127.0.0.1:{port}"
    service_server = PeerServer(
        Peer(did=SERVICE_DID, signing_key=service_key, resolver=resolver),
        identity_resolver=_as_resolver,
    )
    service = PeerApp(service_server, resolver=resolver, channel_endpoint=host_port)
    uv = uvicorn.Server(uvicorn.Config(service_server.app, log_level="error"))
    serve_task = asyncio.create_task(uv.serve(sockets=[sock]))
    while not uv.started:
        await asyncio.sleep(0.01)

    board = Board()
    result: dict = {"verified_hops": []}

    # --- the service lurks the board, engages want ads privately ------------
    async def service_reads_board(proof: bytes) -> None:
        vr = await service.peer.verify_detailed(proof)  # against the poster's own DID
        record = vr.record
        if isinstance(record, Attestation):
            service._cache_attestation(vr.issuer_did, record)  # requester's coordinates
        elif isinstance(record, WantAd):
            result["verified_hops"].append(("service verified want ad from", vr.issuer_did))
            ctx = Envelope(
                issuer_did=vr.issuer_did,
                record_cid=vr.record_cid,
                received_at=_now(),
                record=record,
                collection=WantAd.nsid,
                rkey=vr.path.split("/", 1)[1],
            )
            # ONE call: spin an ephemeral channel admitting only this requester,
            # write the reply into it, and publish the signed engagement.
            channel = await service.reply_private(
                ctx, Inspection(context_id=record.context_id, finding="all clear"), ttl=CHANNEL_TTL
            )
            result["channel_did"] = channel.did
            # faithfully relay the service-signed engagement to the board
            await board.post(_latest_engagement(service_server, record.context_id, channel.did))

    board.lurk(service_reads_board)

    # --- the requester lurks the board too, to catch the engagement ---------
    engagement_seen: asyncio.Future = asyncio.get_running_loop().create_future()

    async def requester_reads_board(proof: bytes) -> None:
        vr = await Peer(resolver=resolver).verify_detailed(proof)
        if isinstance(vr.record, Attestation) and vr.issuer_did == SERVICE_DID:
            result["verified_hops"].append(("requester verified engagement from", vr.issuer_did))
            if not engagement_seen.done():
                engagement_seen.set_result(vr.record)

    board.lurk(requester_reads_board)

    # a second lurker sees everything public on the board (isolation target)
    board.lurk(lambda proof: asyncio.sleep(0))

    # --- the requester posts its want ad + self-attestation -----------------
    ctx_id = "inspect-bridge-7"
    req_repo = Peer(did=req_did, signing_key=req_key).create_repo()
    self_att = build_attestation(
        context_id=ctx_id,
        ephemeral_did=req_did,
        wg_public_key=req_wg,
        tunnel_ip="10.7.0.2",
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=1))
        .isoformat()
        .replace("+00:00", "Z"),
        host=None,
    )
    req_repo.put_record(Attestation.nsid, "self", self_att)
    req_repo.put_record(WantAd.nsid, "ad", WantAd(context_id=ctx_id, need="inspect the bridge"))
    await board.post(req_repo.prove(Attestation.nsid, "self"))  # coordinates first
    await board.post(req_repo.prove(WantAd.nsid, "ad"))  # then the ask -> service engages

    engagement = await asyncio.wait_for(engagement_seen, 10)

    # --- the requester follows the channel and reads the private reply ------
    got_reply: asyncio.Future = asyncio.get_running_loop().create_future()
    requester = PeerApp(PeerServer(Peer(did=req_did, signing_key=req_key)), resolver=resolver)

    @requester.on(Inspection)
    async def _capture(reply: Inspection, _ctx: Envelope) -> None:
        if not got_reply.done():
            got_reply.set_result(reply)

    channel_url = f"ws://{host_port}{FIREHOSE_PATH}?as={req_did}"
    await requester.follow_attested(engagement, firehose_url=channel_url)
    reply = await asyncio.wait_for(got_reply, 10)
    result["verified_hops"].append(("requester read reply from channel", engagement.ephemeral_did))
    result["reply"] = reply.finding

    # --- isolation: a second board lurker is REFUSED the private reply ------
    snoop_url = (
        f"ws://{host_port}{FIREHOSE_PATH}?did={engagement.ephemeral_did}&cursor=0&as={snoop_did}"
    )
    try:
        async with websockets.connect(snoop_url) as sock_ws:
            decode_frame(await asyncio.wait_for(sock_ws.recv(), 5.0))
        result["snoop_refused"] = False
    except Exception as e:  # RepoError NotAuthorized frame -> decode raises
        result["snoop_refused"] = "NotAuthorized" in str(e)

    # --- lifetime: past its TTL the channel is unroutable -------------------
    host = service_server._hosts[engagement.ephemeral_did]
    host.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    reaped = service_server.reap_expired()
    result["channel_expired"] = engagement.ephemeral_did in reaped
    result["channel_unroutable_after_ttl"] = (
        service_server._resolve_host(engagement.ephemeral_did) is None
    )

    # --- teardown -----------------------------------------------------------
    await requester.aclose()
    await service.aclose()
    uv.should_exit = True
    await serve_task
    return result


async def main() -> None:
    r = await run_board_rendezvous()
    print("private reply reached the requester:", r["reply"])
    print("channel:", r["channel_did"])
    print("second board lurker refused the reply:", r["snoop_refused"])
    print(
        "channel expired + unroutable after TTL:",
        r["channel_expired"],
        r["channel_unroutable_after_ttl"],
    )
    print("verified hops (each against the poster's own DID):")
    for what, who in r["verified_hops"]:
        print(f"  - {what} {who}")


if __name__ == "__main__":
    asyncio.run(main())
