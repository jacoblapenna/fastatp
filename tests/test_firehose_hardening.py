"""Firehose consumer hardening, incident-derived.

Each test encodes a production lesson from the MarshalX atproto SDK's
history (patterns credited per test; see docs/COMPARISON-ATPROTO-SDK.md):
reconnect-with-fresh-cursor, error-frame triage, gap recovery, unknown-op
frames, and cache invalidation on #identity.
"""

import asyncio
from typing import Any

import pytest

from fastatp import Peer, PeerServer, build_did_document, crypto, dagcbor
from fastatp.exceptions import CommitGap, FirehoseErrorFrame, RepoError
from fastatp.peerapp import FrameSource, PeerApp, WebSocketFrameSource
from fastatp.repo.sync import RepoMirror, commit_frame_body, decode_frame, encode_frame

DID = "did:web:hardening.example"
NSID = "com.example.hardening.note"


def _note(i: int) -> dict[str, Any]:
    return {"$type": NSID, "n": i}


# --- frame decode triage ---------------------------------------------------------


def test_error_frames_are_typed():
    """op -1 raises FirehoseErrorFrame carrying the error name — the triage
    signal recovery routing needs (never conflate with malformed frames)."""
    frame = dagcbor.encode({"op": -1}) + dagcbor.encode(
        {"error": "ConsumerTooSlow", "message": "reconnect with cursor"}
    )
    with pytest.raises(FirehoseErrorFrame) as exc:
        decode_frame(frame)
    assert exc.value.error == "ConsumerTooSlow"


def test_unknown_op_frames_are_skippable_errors():
    """The event-stream spec reserves other ops; a future op=2 frame with a
    #commit-shaped t must be ignored, not applied as if op were 1."""
    frame = dagcbor.encode({"op": 2, "t": "#commit"}) + dagcbor.encode({"seq": 1})
    with pytest.raises(RepoError, match="unknown op"):
        decode_frame(frame)


def test_commit_gap_is_typed():
    """since/prevData mismatches raise CommitGap (recovery), not bare
    RepoError (skip) — skipping a gap warn-loops forever."""

    async def run() -> None:
        key = crypto.generate_keypair("k256")
        doc = build_did_document(did=DID, signing_key=key.public_key())

        async def resolver(did):
            return doc if did == DID else None

        from fastatp.repo.repo import Repo

        repo = Repo(DID, key)
        mirror = RepoMirror(DID, resolver=resolver)
        await mirror.apply_commit(commit_frame_body(DID, 1, repo.put_record(NSID, "a", _note(0))))
        repo.put_record(NSID, "b", _note(1))  # the mirror never sees this one
        stale = repo.put_record(NSID, "c", _note(2))
        with pytest.raises(CommitGap):
            await mirror.apply_commit(commit_frame_body(DID, 3, stale))

    asyncio.run(run())


# --- WebSocketFrameSource: cursor and reconnect ----------------------------------


def test_source_advance_updates_resume_cursor():
    """Reconnects must resume from the last applied seq, not the
    construction-time cursor (the stale-cursor replay bug; pattern from
    MarshalX/atproto@071a6fb — automated here instead of left to callers)."""
    src = WebSocketFrameSource("ws://example.invalid/xrpc/x", start_cursor=5)
    assert src._full_url().endswith("cursor=5")
    src.advance(42)
    assert src._full_url().endswith("cursor=42")
    live_tail = WebSocketFrameSource("ws://example.invalid/xrpc/x")
    assert live_tail._full_url() == "ws://example.invalid/xrpc/x"
    live_tail.advance(7)
    assert live_tail._full_url().endswith("cursor=7")


def test_source_defaults_encode_operational_lessons():
    """max_size covers the sync spec's frame ceiling (websockets' 1 MiB
    default drops legitimate commits — MarshalX/atproto@63537e4); an
    inactivity timeout exists (alive-but-silent servers are invisible to WS
    pings — MarshalX/atproto@8b89a24)."""
    src = WebSocketFrameSource("ws://example.invalid/xrpc/x")
    assert src.max_size >= 5 * 1024 * 1024
    assert src.recv_timeout is not None
    assert src.max_backoff <= 120  # capped, never unbounded


def test_close_wins_over_reconnect_backoff():
    """close() must stop the reconnect loop even mid-backoff (the SDK needed
    two fixes for this exact bug class)."""

    async def run() -> None:
        src = WebSocketFrameSource("ws://127.0.0.1:9/xrpc/x")  # port 9: refused

        class _DeadWS:
            async def recv(self):
                raise ConnectionError("gone")

            async def close(self):
                pass

        src._ws = _DeadWS()
        frames = []

        async def consume():
            async for frame in src:
                frames.append(frame)

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.05)  # let it enter the backoff/reconnect path
        await src.close()
        await asyncio.wait_for(task, 5)  # terminates promptly, no frames
        assert frames == []

    asyncio.run(run())


# --- recovery routing in the dispatcher ------------------------------------------


class _ScriptedSource(FrameSource):
    """In-process source: yields scripted frames, serves a checkpoint."""

    def __init__(self, frames: list[bytes], checkpoint_car: bytes | None) -> None:
        self.frames = frames
        self.checkpoint_car = checkpoint_car
        self.checkpoint_calls = 0
        self.advanced: list[int] = []

    async def open(self) -> None: ...

    async def close(self) -> None: ...

    def advance(self, cursor: int) -> None:
        self.advanced.append(cursor)

    async def checkpoint(self, did: str) -> bytes | None:
        self.checkpoint_calls += 1
        return self.checkpoint_car

    async def __aiter__(self):
        for frame in self.frames:
            yield frame


def _pipeline(frames, checkpoint_car):
    """Run one follow over scripted frames; returns (app, mirror-ish state)."""
    key = crypto.generate_keypair("k256")
    doc = build_did_document(did=DID, signing_key=key.public_key())

    async def resolver(did):
        return doc if did == DID else None

    async def run():
        server = PeerServer(
            Peer(did="did:web:consumer.example", signing_key=crypto.generate_keypair("k256"))
        )
        app = PeerApp(server, resolver=resolver)
        source = _ScriptedSource(frames, checkpoint_car)
        await app.follow(DID, source=source)
        await asyncio.sleep(0.3)  # let ingestion drain
        task, _ = app._followed[DID]
        await asyncio.wait_for(task, 5)
        return source

    return asyncio.run(run())


def test_gap_triggers_checkpoint_recovery():
    """A missed commit routes to the checkpoint seam instead of the
    warn-and-drop-forever loop (our verification strength deserves a
    recovery path)."""
    from fastatp.repo.repo import Repo

    key = crypto.generate_keypair("k256")
    global DID  # keep the resolver simple: one DID for producer identity
    repo = Repo(DID, key)
    doc = build_did_document(did=DID, signing_key=key.public_key())

    async def resolver(did):
        return doc if did == DID else None

    e1 = repo.put_record(NSID, "a", _note(0))
    repo.put_record(NSID, "b", _note(1))  # never emitted: the gap
    e3 = repo.put_record(NSID, "c", _note(2))
    checkpoint = repo.export_car()
    frames = [
        encode_frame("#commit", commit_frame_body(DID, 1, e1)),
        encode_frame("#commit", commit_frame_body(DID, 3, e3)),  # gap!
    ]

    async def run():
        server = PeerServer(
            Peer(did="did:web:consumer.example", signing_key=crypto.generate_keypair("k256"))
        )
        app = PeerApp(server, resolver=resolver)
        source = _ScriptedSource(frames, checkpoint)
        await app.follow(DID, source=source)
        task, _ = app._followed[DID]
        await asyncio.wait_for(task, 5)
        return source

    source = asyncio.run(run())
    assert source.checkpoint_calls == 1  # the gap fetched a checkpoint
    assert source.advanced  # and the cursor advanced past the applied frame


def test_error_frame_triggers_checkpoint_recovery():
    """A protocol error frame (ConsumerTooSlow) recovers explicitly instead
    of being dropped as a bad frame."""
    from fastatp.repo.repo import Repo

    key = crypto.generate_keypair("k256")
    repo = Repo(DID, key)
    repo.put_record(NSID, "x", _note(0))
    checkpoint = repo.export_car()
    error_frame = dagcbor.encode({"op": -1}) + dagcbor.encode(
        {"error": "ConsumerTooSlow", "message": "evicted"}
    )
    doc = build_did_document(did=DID, signing_key=key.public_key())

    async def resolver(did):
        return doc if did == DID else None

    async def run():
        server = PeerServer(
            Peer(did="did:web:consumer.example", signing_key=crypto.generate_keypair("k256"))
        )
        app = PeerApp(server, resolver=resolver)
        source = _ScriptedSource([error_frame], checkpoint)
        await app.follow(DID, source=source)
        task, _ = app._followed[DID]
        await asyncio.wait_for(task, 5)
        return source

    source = asyncio.run(run())
    assert source.checkpoint_calls == 1


# --- WebResolver.invalidate ------------------------------------------------------


def test_web_resolver_invalidate():
    """#identity's purpose: drop the cached document so the next verification
    re-resolves instead of trusting a rotated-away key until TTL expiry
    (invalidation-API pattern from MarshalX/atproto's identity cache)."""

    async def run() -> None:
        import httpx

        from fastatp.did.resolver import WebResolver

        key = crypto.generate_keypair("k256")
        doc_json = build_did_document(
            did="did:web:inv.example", signing_key=key.public_key()
        ).to_json_dict()
        fetches = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal fetches
            fetches += 1
            return httpx.Response(200, json=doc_json, headers={"Cache-Control": "max-age=3600"})

        resolver = WebResolver(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        await resolver("did:web:inv.example")
        await resolver("did:web:inv.example")  # cached
        assert fetches == 1
        resolver.invalidate("did:web:inv.example")
        await resolver("did:web:inv.example")  # refetches
        assert fetches == 2

    asyncio.run(run())


def test_checkpoint_failure_does_not_kill_ingest():
    """A 503 from getRepo during recovery must degrade to out-of-sync, never
    terminate the follow."""
    from fastatp.repo.repo import Repo

    key = crypto.generate_keypair("k256")
    repo = Repo(DID, key)
    doc = build_did_document(did=DID, signing_key=key.public_key())

    async def resolver(did):
        return doc if did == DID else None

    e1 = repo.put_record(NSID, "a", _note(0))
    repo.put_record(NSID, "b", _note(1))  # the gap
    e3 = repo.put_record(NSID, "c", _note(2))
    e4 = repo.put_record(NSID, "d", _note(3))

    class _FailingCheckpoint(_ScriptedSource):
        async def checkpoint(self, did: str) -> bytes | None:
            self.checkpoint_calls += 1
            raise ConnectionError("getRepo returned 503")

    frames = [
        encode_frame("#commit", commit_frame_body(DID, 1, e1)),
        encode_frame("#commit", commit_frame_body(DID, 3, e3)),  # gap -> recovery fails
        encode_frame("#commit", commit_frame_body(DID, 4, e4)),  # still consumed
    ]

    async def run():
        server = PeerServer(
            Peer(did="did:web:consumer.example", signing_key=crypto.generate_keypair("k256"))
        )
        app = PeerApp(server, resolver=resolver)
        source = _FailingCheckpoint(frames, None)
        await app.follow(DID, source=source)
        task, _ = app._followed[DID]
        await asyncio.wait_for(task, 5)  # the loop ran to completion, not died
        return source

    source = asyncio.run(run())
    assert source.checkpoint_calls >= 2  # recovery retried on each gap signal


def test_rotation_key_excluded_even_in_legacy_encoding():
    """A rotation key exposed through a legacy 2019 verification method
    (different encoding, same key) must still be excluded from signing
    candidates."""
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    from fastatp.crypto import encode_did_key
    from fastatp.did.document import DIDDocument, signing_key_candidates
    from fastatp.exceptions import DIDDocumentInvalid
    from fastatp.lex.types import base58btc_encode

    rotation = crypto.generate_keypair("k256")
    uncompressed = rotation.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    legacy_form = "z" + base58btc_encode(uncompressed)
    doc = DIDDocument.model_validate(
        {
            "id": "did:web:legacyrot.example",
            "verificationMethod": [
                {
                    "id": "did:web:legacyrot.example#atproto",
                    "type": "EcdsaSecp256k1VerificationKey2019",
                    "controller": "did:web:legacyrot.example",
                    "publicKeyMultibase": legacy_form,
                }
            ],
            "rotationKeys": [encode_did_key(rotation.public_key())],
        }
    )
    with pytest.raises(DIDDocumentInvalid, match="no usable signing key"):
        signing_key_candidates(doc)
