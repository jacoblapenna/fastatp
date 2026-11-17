"""A restart resumes the firehose seq, so followers' cursors keep working.

The seq lives in the repo's head store: a commit's comes from the swap that
landed it, every other frame takes the next one through the same swap, and
a durable store makes the counter survive the process.
"""

import asyncio
import json
from collections.abc import Awaitable
from typing import cast
from urllib.parse import urlencode

import pytest
import websockets

from fastatp import Identity, Peer, PeerServer, Repo, crypto
from fastatp.repo.head import Head, SqliteHeadStore
from fastatp.repo.store import SqliteBlockStore
from fastatp.repo.sync import decode_frame
from fastatp.server.peer_server import FIREHOSE_PATH

DID = "did:web:durable.example"
NOTE = "com.example.note"


async def _frames(port: int, did: str, cursor: int, want: int, timeout: float = 5.0):
    """The next ``want`` frames after ``cursor`` as (seq, type) pairs."""
    query = urlencode({"did": did, "cursor": cursor})
    out = []
    async with websockets.connect(f"ws://127.0.0.1:{port}{FIREHOSE_PATH}?{query}") as ws:
        while len(out) < want:
            header, body = decode_frame(
                await asyncio.wait_for(cast("Awaitable[bytes]", ws.recv()), timeout)
            )
            if header.get("op") == -1:
                out.append(("error", body.get("error")))
                break
            out.append((body.get("seq"), header.get("t")))
    return out


async def test_a_restart_resumes_the_seq_and_an_old_cursor_still_works(tmp_path, serve_app):
    path = tmp_path / "repo.sqlite"
    key = crypto.generate_keypair("k256")
    repo = await Repo.create(
        DID, key, store=SqliteBlockStore(path), head_store=SqliteHeadStore(path)
    )
    assert repo.seq == 0  # the genesis is the start of the stream, not an event on it
    srv = await PeerServer.create(Peer(did=DID, signing_key=key), repo)
    assert srv.seq == 2  # #identity 1, #account 2

    async with serve_app(srv.app) as port:
        e1 = await srv.put_record(NOTE, "one", {"$type": NOTE, "text": "1"})
        e2 = await srv.put_record(NOTE, "two", {"$type": NOTE, "text": "2"})
        assert (e1.seq, e2.seq) == (3, 4)
        assert await _frames(port, DID, 0, 4) == [
            (1, "#identity"),
            (2, "#account"),
            (3, "#commit"),
            (4, "#commit"),
        ]
    follower_cursor = 4

    # the process dies; a new one opens the same store
    reopened = await Repo.open(DID, key, SqliteBlockStore(path), SqliteHeadStore(path))
    assert reopened.seq == 4
    again = await PeerServer.create(Peer(did=DID, signing_key=key), reopened)
    assert again.seq == 6  # announced again: 5, 6; nothing started over

    async with serve_app(again.app) as port:
        assert await _frames(port, DID, follower_cursor, 2) == [(5, "#identity"), (6, "#account")]
        e3 = await again.put_record(NOTE, "three", {"$type": NOTE, "text": "3"})
        assert e3.seq == 7
        assert await _frames(port, DID, 6, 1) == [(7, "#commit")]
        # a cursor from before the restart that is older than the window
        # falls back to the checkpoint path exactly as before
        again._primary._backfill = again._primary._backfill[-1:]
        kinds = [kind for _seq, kind in await _frames(port, DID, 1, 2)]
        assert kinds == ["#info", "#sync"]  # OutdatedCursor, then the checkpoint


async def test_frames_enter_the_backfill_in_seq_order_whatever_finishes_first():
    """A commit's seq is taken when it lands; an announcement in flight at the
    same time takes the next one. The backfill sorts by seq, so a follower
    never sees them out of order."""

    class _Slow(crypto.SoftwareSigner):
        async def sign(self, message: bytes) -> bytes:
            await asyncio.sleep(0.02)
            return await super().sign(message)

    key = crypto.generate_keypair("p256")
    srv = await PeerServer.create(Peer(did=DID, signing_key=_Slow(key)))
    await asyncio.gather(
        srv.put_record(NOTE, "a", {"$type": NOTE, "text": "a"}),
        srv.emit_account(active=True, status="still here"),
        srv.put_record(NOTE, "b", {"$type": NOTE, "text": "b"}),
    )
    seqs = [seq for seq, _frame in srv._primary._backfill]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    assert srv.seq == max(seqs) == (await srv.repo.head_store.load(DID)).seq


async def test_an_identity_resumes_its_seq_across_a_reboot(tmp_path):
    sdir = tmp_path / "alice"
    ident = await Identity.bootstrap(domain="alice.example", state_dir=sdir)
    srv = await PeerServer.create(ident.peer(), ident.repo)
    await srv.put_record(NOTE, "n", {"$type": NOTE, "text": "before the reboot"})
    before = srv.seq
    assert not (sdir / "repo_head").exists()  # the head lives in blocks.sqlite now

    rebooted = await Identity.bootstrap(domain="alice.example", state_dir=sdir)
    assert rebooted.repo.seq == before
    srv2 = await PeerServer.create(rebooted.peer(), rebooted.repo)
    assert srv2.seq == before + 2  # announced again, never back to zero


async def test_a_state_dir_from_before_the_head_store_is_folded_in(tmp_path):
    """A 0.2 state dir keeps its head in a ``repo_head`` file; the first load
    folds it into the sqlite head store with seq 0 and carries on."""
    sdir = tmp_path / "old"
    ident = await Identity.bootstrap(domain="old.example", state_dir=sdir)
    await ident.repo.put_record(NOTE, "n", {"$type": NOTE, "text": "kept"})
    head = ident.repo.commit_cid
    heads = SqliteHeadStore(sdir / "blocks.sqlite")
    await heads.delete(ident.did)  # what an old state dir looks like...
    (sdir / "repo_head").write_text(head.encode())  # ...with the file instead
    heads.close()

    loaded = await Identity.bootstrap(domain="old.example", state_dir=sdir)
    assert loaded.repo.commit_cid == head
    assert loaded.repo.get_record(NOTE, "n") == {"$type": NOTE, "text": "kept"}
    assert await SqliteHeadStore(sdir / "blocks.sqlite").load(loaded.did) == Head(head, 0)
    event = await loaded.repo.put_record(NOTE, "m", {"$type": NOTE, "text": "more"})
    assert event is not None and event.seq == 1
    assert json.loads((sdir / "identity.json").read_text())["did"] == loaded.did


# --- the announcement comes first, whatever else is happening -------------------


def _kinds(srv: PeerServer) -> list[tuple[int, str]]:
    return [(seq, decode_frame(frame)[0]["t"]) for seq, frame in srv._primary._backfill]


async def test_the_sync_constructor_announces_before_its_first_write():
    """``PeerServer(peer, repo)`` announces lazily; the first write must not
    take its seq before the announcement does, or the commit sorts ahead of
    #account and a relay drops it."""
    key = crypto.generate_keypair("k256")
    srv = PeerServer(Peer(did=DID, signing_key=key), await Repo.create(DID, key))
    event = await srv.put_record(NOTE, "first", {"$type": NOTE, "text": "1"})
    assert event is not None and event.seq == 3
    assert _kinds(srv) == [(1, "#identity"), (2, "#account"), (3, "#commit")]


async def test_concurrent_first_emits_wait_for_one_announcement():
    """Two writes race on an unannounced host: one announcement, both commits
    after it, nothing twice."""
    key = crypto.generate_keypair("k256")
    srv = PeerServer(Peer(did=DID, signing_key=key), await Repo.create(DID, key))
    await asyncio.gather(
        srv.put_record(NOTE, "a", {"$type": NOTE, "text": "a"}),
        srv.put_record(NOTE, "b", {"$type": NOTE, "text": "b"}),
        srv.emit_identity(handle="alice.example"),
    )
    kinds = _kinds(srv)
    assert kinds[:2] == [(1, "#identity"), (2, "#account")]
    assert sorted(kind for _seq, kind in kinds[2:]) == ["#commit", "#commit", "#identity"]
    assert [kind for _seq, kind in kinds].count("#account") == 1


async def test_retiring_a_repo_poisons_its_subscribers_even_if_the_frame_fails():
    from fastatp.server.peer_server import _EVICTED

    key = crypto.generate_keypair("k256")
    srv = await PeerServer.create(Peer(did=DID, signing_key=key))
    channel_key = crypto.generate_keypair("k256")
    channel_did = crypto.encode_did_key(channel_key.public_key())
    handle = await srv.register(await Repo.create(channel_did, channel_key))
    queue: asyncio.Queue = asyncio.Queue()
    srv._hosts[channel_did]._queues.add(queue)  # a live subscriber

    async def no_seq():
        raise OSError("the head store is gone")

    handle.repo.advance_seq = no_seq  # type: ignore[method-assign]
    with pytest.raises(OSError):
        await srv.unregister(channel_did)
    assert channel_did not in srv.hosts()
    assert queue.get_nowait()[0] == _EVICTED  # not left hanging
