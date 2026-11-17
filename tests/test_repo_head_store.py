"""A repo on a head store: the write lock as a compare-and-swap.

Two ``Repo`` objects on one shared store stand in for two processes behind
one database. Blocks are written before the head moves, the swap decides who
wins, and the loser retries on the winner's head.
"""

import asyncio

import pytest

from fastatp import HeadMoved, Repo, RepoMirror, crypto
from fastatp.cid import CID
from fastatp.did.document import build_did_document
from fastatp.exceptions import RepoError
from fastatp.repo.head import Head, MemoryHeadStore, SqliteHeadStore
from fastatp.repo.store import SqliteBlockStore

DID = "did:web:shared.example"
NOTE = "com.example.note"


def _note(text: str) -> dict:
    return {"$type": NOTE, "text": text}


@pytest.fixture
def shared(tmp_path):
    """One database for every process: blocks and heads in one sqlite file."""
    path = tmp_path / "repo.sqlite"
    return SqliteBlockStore(path), SqliteHeadStore(path)


class _Barrier:
    """A signer that waits for every participant to reach the signature, so
    both commits are built on the same head before either tries to land."""

    def __init__(self, key, parties: int) -> None:
        self._inner = crypto.SoftwareSigner(key)
        self._barrier = asyncio.Barrier(parties)
        self._released = False  # the loser's retry signs again, alone

    @property
    def public_key(self):
        return self._inner.public_key

    async def sign(self, message: bytes) -> bytes:
        if not self._released:
            await self._barrier.wait()
            self._released = True
        return await self._inner.sign(message)


async def test_create_open_and_commit_through_the_store(shared):
    blocks, heads = shared
    key = crypto.generate_keypair("k256")
    repo = await Repo.create(DID, key, store=blocks, head_store=heads)
    assert await heads.load(DID) == Head(repo.commit_cid, 0)  # the genesis starts the stream
    assert repo.seq == 0

    event = await repo.put_record(NOTE, "one", _note("first"))
    assert event is not None and event.seq == 1
    assert await heads.load(DID) == Head(repo.commit_cid, 1)

    again = await Repo.open(DID, key, blocks, heads)  # a second process, or a restart
    assert again.commit_cid == repo.commit_cid and again.seq == 1
    assert again.get_record(NOTE, "one") == _note("first")
    frozen = await Repo.open(DID, None, blocks, heads)
    assert frozen.frozen and frozen.rev == repo.rev

    with pytest.raises(RepoError, match="already holds a head"):
        await Repo.create(DID, key, store=blocks, head_store=heads)
    with pytest.raises(RepoError, match="holds no head"):
        await Repo.open("did:web:nobody.example", key, blocks, heads)
    with pytest.raises(ValueError, match="not both"):
        Repo(DID, key, blocks, head_cid=repo.commit_cid, head_store=heads)


async def test_two_processes_race_and_the_loser_retries_on_the_winners_head(shared):
    blocks, heads = shared
    key = crypto.generate_keypair("p256")
    doc = build_did_document(did=DID, signing_key=key.public_key())
    await Repo.create(DID, key, store=blocks, head_store=heads)
    signer = _Barrier(key, 2)
    a = await Repo.open(DID, signer, blocks, heads)
    b = await Repo.open(DID, signer, blocks, heads)

    ea, eb = await asyncio.gather(
        a.put_record(NOTE, "from-a", _note("a")), b.put_record(NOTE, "from-b", _note("b"))
    )
    assert ea is not None and eb is not None
    assert {ea.seq, eb.seq} == {1, 2}  # one landed first, the other on top of it
    head = await heads.load(DID)
    assert head.seq == 2
    final = await Repo.open(DID, None, blocks, heads)
    assert final.get_record(NOTE, "from-a") == _note("a")
    assert final.get_record(NOTE, "from-b") == _note("b")
    assert a.commit_cid != b.commit_cid or a.rev == b.rev  # each object holds a real head

    async def resolver(did):
        return doc if did == DID else None

    mirror = RepoMirror(DID, resolver=resolver)
    await mirror.load_checkpoint(final.export_car())  # the chain verifies end to end
    assert mirror.get_record(NOTE, "from-b") == _note("b")
    reachable = {final.commit_cid} | final._mst_nodes | set(final._entries.values())
    assert reachable <= set(blocks.cids())


async def test_the_same_record_written_by_both_lands_once(shared):
    blocks, heads = shared
    key = crypto.generate_keypair("p256")
    await Repo.create(DID, key, store=blocks, head_store=heads)
    signer = _Barrier(key, 2)
    a = await Repo.open(DID, signer, blocks, heads)
    b = await Repo.open(DID, signer, blocks, heads)

    results = await asyncio.gather(
        a.put_record(NOTE, "same", _note("bytes")), b.put_record(NOTE, "same", _note("bytes"))
    )
    assert sorted(r is None for r in results) == [False, True]  # one commit, one no-op
    assert (await heads.load(DID)).seq == 1
    assert (await Repo.open(DID, None, blocks, heads)).get_record(NOTE, "same") == _note("bytes")


async def test_different_bytes_at_the_same_path_fail_the_loser(shared):
    blocks, heads = shared
    key = crypto.generate_keypair("p256")
    await Repo.create(DID, key, store=blocks, head_store=heads)
    signer = _Barrier(key, 2)
    a = await Repo.open(DID, signer, blocks, heads)
    b = await Repo.open(DID, signer, blocks, heads)

    results = await asyncio.gather(
        a.put_record(NOTE, "same", _note("a's")),
        b.put_record(NOTE, "same", _note("b's")),
        return_exceptions=True,
    )
    winners = [r for r in results if not isinstance(r, Exception)]
    losers = [r for r in results if isinstance(r, Exception)]
    assert len(winners) == 1 and len(losers) == 1
    assert isinstance(losers[0], RepoError) and "already exists" in str(losers[0])
    assert (await heads.load(DID)).seq == 1


async def test_contention_beyond_the_retries_surfaces_as_head_moved():
    class _AlwaysMoved(MemoryHeadStore):
        async def swap(self, did, expected, new):
            if expected is None:
                return await super().swap(did, expected, new)  # let the genesis in
            return False

    heads = _AlwaysMoved()
    repo = await Repo.create(DID, crypto.generate_keypair("k256"), head_store=heads)
    genesis = repo.commit_cid
    with pytest.raises(HeadMoved) as caught:
        await repo.put_record(NOTE, "n", _note("never"))
    assert caught.value.head == Head(genesis, 0)
    assert repo.commit_cid == genesis and repo.get_record(NOTE, "n") is None
    reachable = {repo.commit_cid} | repo._mst_nodes | set(repo._entries.values())
    assert set(repo.store.cids()) == reachable  # nothing staged survived


async def test_a_crash_between_the_blocks_and_the_swap_leaves_the_old_head_loadable(shared):
    blocks, heads = shared
    key = crypto.generate_keypair("k256")
    repo = await Repo.create(DID, key, store=blocks, head_store=heads)
    old_head = repo.commit_cid

    class _Crashes(SqliteHeadStore):
        async def swap(self, did, expected, new):
            raise OSError("power lost")

    crashing = await Repo.open(DID, key, blocks, _Crashes(heads.path))
    with pytest.raises(OSError):
        await crashing.put_record(NOTE, "n", _note("lost"))

    reopened = await Repo.open(DID, key, blocks, heads)
    assert reopened.commit_cid == old_head and reopened.get_record(NOTE, "n") is None
    orphans = set(blocks.cids()) - ({old_head} | reopened._mst_nodes)
    assert orphans  # the staged blocks are there, unreachable
    await reopened.put_record(NOTE, "m", _note("after"))  # the next commit's GC clears them
    reachable = {reopened.commit_cid} | reopened._mst_nodes | set(reopened._entries.values())
    assert set(blocks.cids()) == reachable


async def test_an_import_on_a_stale_head_is_refused_and_lands_after_reload(shared):
    blocks, heads = shared
    key = crypto.generate_keypair("k256")
    await Repo.create(DID, key, store=blocks, head_store=heads)
    a = await Repo.open(DID, key, blocks, heads)
    b = await Repo.open(DID, None, blocks, heads)  # a host, importing the owner's pushes
    await a.put_record(NOTE, "meanwhile", _note("another process wrote"))
    owner = await Repo.create(DID, key)  # the owner's copy, pushing newer revs
    await owner.put_record(NOTE, "pushed", _note("by the owner"))
    await owner.put_record(NOTE, "more", _note("later"))

    with pytest.raises(HeadMoved):
        await b.import_car(owner.export_car(), verify_key=key.public_key())
    assert b.get_record(NOTE, "meanwhile") is not None  # b reloaded to the current head
    await b.import_car(owner.export_car(), verify_key=key.public_key(), newer_than=b.rev)
    assert (await heads.load(DID)) == Head(b.commit_cid, 2)  # the refused import took no seq
    assert b.get_record(NOTE, "more") is not None


async def test_a_caller_managed_head_still_works_without_a_store():
    key = crypto.generate_keypair("k256")
    first = await Repo.create(DID, key)
    await first.put_record(NOTE, "n", _note("kept"))
    reopened = Repo(DID, key, store=first.store, head_cid=first.commit_cid)
    event = await reopened.put_record(NOTE, "m", _note("more"))
    assert event is not None and event.seq == 1  # a private in-memory counter
    assert isinstance(reopened.head_store, MemoryHeadStore)
    assert (await reopened.head_store.load(DID)) == Head(reopened.commit_cid, 1)
    assert isinstance(reopened.commit_cid, CID)
