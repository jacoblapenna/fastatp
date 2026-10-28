"""Test 1 — two-peer authorization lifecycle, no root.

Two peers with real DIDs served by real DIDServers over localhost HTTP; real
signed lexicons flow both ways over the real firehose framing; the grant/
revoke enforcement toggle is observable end to end.

The enforcement transport here is ``SimTunnel`` — a userspace MODEL of
WireGuard reachability, NOT a substitute for it. Admission consults the exact
``MemoryWireGuardInterface`` the reconciler manages; admitted traffic is real
ChaCha20-Poly1305 over an X25519 ECDH session key, so the bytes on the wire
are genuine ciphertext. This proves the FastATP control-plane -> data-path
wiring (grant opens the tunnel, revoke closes it). It does NOT prove the
kernel enforces anything — that is test_wireguard_netns.py (root-gated).
"""

import asyncio
import base64
import os
import socket

import pytest
import uvicorn
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from fastatp import DIDServer, Peer, PeerServer, RepoMirror, WebResolver, build_did_document, crypto
from fastatp.authz import (
    MemoryWireGuardInterface,
    WireGuardReconciler,
    build_wireguard_service,
)
from fastatp.lex.types import Datetime
from fastatp.lexicon import Lexicon
from fastatp.repo.repo import Repo
from fastatp.repo.sync import commit_frame_body, decode_frame, encode_frame, sync_frame_body


class Ping(Lexicon):
    nsid = "com.example.test.p2pPing"
    seq: int
    at: Datetime


def _ping(i: int) -> Ping:
    return Ping(seq=i, at="2026-07-02T12:00:00Z")


# --- SimTunnel: userspace model of WG reachability (see module docstring) ------


def _x25519_keypair() -> tuple[X25519PrivateKey, str]:
    priv = X25519PrivateKey.generate()
    pub = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return priv, base64.b64encode(pub).decode()


def _session_key(my_priv: X25519PrivateKey, their_pub_b64: str) -> bytes:
    shared = my_priv.exchange(X25519PublicKey.from_public_bytes(base64.b64decode(their_pub_b64)))
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"fastatp-simtunnel").derive(
        shared
    )


class SimTunnel:
    def __init__(self, my_priv: X25519PrivateKey, interface=None) -> None:
        self._priv = my_priv
        self._iface = interface

    def send(self, peer_pub_b64: str, plaintext: bytes) -> bytes | None:
        # reachability gate: a peer absent from the interface is unreachable
        if self._iface is None or peer_pub_b64 not in self._iface.peers():
            return None
        key = _session_key(self._priv, peer_pub_b64)
        nonce = os.urandom(12)
        return nonce + ChaCha20Poly1305(key).encrypt(nonce, plaintext, None)

    def recv(self, peer_pub_b64: str, wire: bytes) -> bytes:
        key = _session_key(self._priv, peer_pub_b64)
        return ChaCha20Poly1305(key).decrypt(wire[:12], wire[12:], None)


# --- environment: two peers, two DID servers, real HTTP resolution ------------


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _serve_did(doc, port: int):
    async def resolver(slug: str):
        return doc if slug == "" else None

    server = DIDServer(domain=f"localhost:{port}", resolver=resolver)
    config = uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="error")
    uv = uvicorn.Server(config)
    task = asyncio.create_task(uv.serve())
    while not uv.started:
        await asyncio.sleep(0.01)
    return uv, task


class _Env:
    did1: str
    did2: str
    peer1: PeerServer
    peer2_repo: Repo
    iface1: MemoryWireGuardInterface
    recon1: WireGuardReconciler
    tunnel1: SimTunnel
    tunnel2: SimTunnel
    wg1_pub: str
    wg2_pub: str
    resolver: WebResolver


@pytest.fixture
async def env():
    port1, port2 = _free_port(), _free_port()
    did1 = f"did:web:localhost%3A{port1}"
    did2 = f"did:web:localhost%3A{port2}"

    sk1 = crypto.generate_keypair("k256")
    sk2 = crypto.generate_keypair("p256")
    wg1_priv, wg1_pub = _x25519_keypair()
    wg2_priv, wg2_pub = _x25519_keypair()

    doc1 = build_did_document(
        did=did1,
        signing_key=sk1.public_key(),
        extra_services=[build_wireguard_service(wg1_pub, "10.100.0.1")],
    )
    doc2 = build_did_document(
        did=did2,
        signing_key=sk2.public_key(),
        extra_services=[build_wireguard_service(wg2_pub, "10.100.0.2")],
    )

    uv1, t1 = await _serve_did(doc1, port1)
    uv2, t2 = await _serve_did(doc2, port2)
    resolver = WebResolver()

    e = _Env()
    e.did1, e.did2 = did1, did2
    e.peer1 = PeerServer(Peer(did=did1, signing_key=sk1))  # granting host + firehose
    e.peer2_repo = Repo(did2, sk2)  # grantee, signs the echo-back
    e.iface1 = MemoryWireGuardInterface()
    e.recon1 = WireGuardReconciler(e.peer1, e.iface1, resolver)
    e.tunnel1 = SimTunnel(wg1_priv, e.iface1)  # peer1 sends; gate = iface1
    e.tunnel2 = SimTunnel(wg2_priv)  # peer2 receives (no gate on recv)
    e.wg1_pub, e.wg2_pub = wg1_pub, wg2_pub
    e.resolver = resolver
    yield e

    await resolver.aclose()
    for uv, task in ((uv1, t1), (uv2, t2)):
        uv.should_exit = True
        await task


# --- baseline: real bidirectional exchange over real DID resolution -----------


async def test_baseline_bidirectional_without_wireguard(env):
    """sign+send peer1->peer2 and echo peer2->peer1; verify both ways via
    real did:web resolution over HTTP. No enforcement in the path."""
    # peer1 -> peer2
    mirror_of_1 = RepoMirror(env.did1, resolver=env.resolver)
    ev1 = env.peer1.put_record(Ping.nsid, "a", _ping(1))
    records = await mirror_of_1.apply_commit(commit_frame_body(env.did1, 1, ev1))
    assert records[0]["record"]["seq"] == 1
    rec_a = mirror_of_1.get_record(Ping.nsid, "a")
    assert rec_a is not None
    assert rec_a["seq"] == 1

    # peer2 -> peer1 (echo back)
    mirror_of_2 = RepoMirror(env.did2, resolver=env.resolver)
    ev2 = env.peer2_repo.put_record(Ping.nsid, "b", _ping(2))
    records = await mirror_of_2.apply_commit(commit_frame_body(env.did2, 1, ev2))
    assert records[0]["record"]["seq"] == 2


# --- enforcement toggle: gibberish -> decoded -> gibberish --------------------


async def test_grant_revoke_toggles_reachability(env):
    """Before grant the tunnel drops (silence); a sniffer would see only
    ciphertext. After grant peer2 decodes and verifies the signed record.
    After revoke the tunnel drops again."""
    mirror = RepoMirror(env.did1, resolver=env.resolver)

    # 1. BEFORE grant: peer2 is not on peer1's interface -> unreachable
    assert env.wg2_pub not in env.iface1.peers()
    ev = env.peer1.put_record(Ping.nsid, "pre", _ping(1))
    frame = encode_frame("#commit", commit_frame_body(env.did1, env.peer1.seq, ev))
    assert env.tunnel1.send(env.wg2_pub, frame) is None  # dropped: silence

    # 2. GRANT: sign a capability + open the tunnel. An authorized subscriber
    # pulls a current-state checkpoint (the real "subscribe after grant" flow).
    await env.recon1.grant(env.did2)
    assert env.wg2_pub in env.iface1.peers()
    env.peer1.put_record(Ping.nsid, "live", _ping(2))
    checkpoint = encode_frame(
        "#sync",
        sync_frame_body(
            env.did1,
            env.peer1.seq,
            env.peer1.repo.rev,
            env.peer1.repo.export_car(),
            "2026-07-02T12:00:00Z",
        ),
    )
    wire = env.tunnel1.send(env.wg2_pub, checkpoint)
    assert wire is not None
    assert wire != checkpoint  # on the wire it is real ciphertext
    decrypted = env.tunnel2.recv(env.wg1_pub, wire)
    assert decrypted == checkpoint
    header, body = decode_frame(decrypted)
    assert header["t"] == "#sync"
    await mirror.apply_sync(body)  # verifies peer1's commit sig via HTTP DID resolution
    rec_live = mirror.get_record(Ping.nsid, "live")
    assert rec_live is not None
    assert rec_live["seq"] == 2

    # a wrong session key (an unauthorized eavesdropper) cannot read the wire
    wrong_priv, _ = _x25519_keypair()
    with pytest.raises(InvalidTag):
        SimTunnel(wrong_priv).recv(env.wg1_pub, wire)

    # 3. REVOKE: sign a revocation + close the tunnel
    await env.recon1.revoke(env.did2, reason="offboarded")
    assert env.wg2_pub not in env.iface1.peers()
    ev = env.peer1.put_record(Ping.nsid, "post", _ping(3))
    frame = encode_frame("#commit", commit_frame_body(env.did1, env.peer1.seq, ev))
    assert env.tunnel1.send(env.wg2_pub, frame) is None  # dropped again: silence


async def test_reconciler_used_real_did_resolution(env):
    """The grant path resolved peer2's WG identity from its live DID document
    over HTTP (not a hand-fed fixture)."""
    _rkey, cap = await env.recon1.grant(env.did2)
    assert cap.subject == env.did2
    # the pubkey now enforced came from the resolved did:web document
    assert env.wg2_pub in env.iface1.peers()
    assert env.iface1.peers()[env.wg2_pub] == "10.100.0.2/32"
