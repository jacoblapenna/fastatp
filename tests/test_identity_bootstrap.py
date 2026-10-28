"""Identity bootstrap (create-once/load-forever) + durability.

The durability contract: no domain -> ephemeral did:key; domain= ->
durable state_dir REQUIRED or fail loudly; rotation key external by default;
restart preserves identity (same DID, keys, chain, repo); key loss is a visible
rebirth, never a quiet substitution."""

import pytest

from fastatp import crypto
from fastatp.cid import CID
from fastatp.exceptions import DIDDocumentInvalid, IdentityError, RepoError
from fastatp.identity import Identity
from fastatp.repo.store import SqliteBlockStore

DOMAIN = "svc.example"


class _Rec:
    # a tiny record the repo can hold (dict form; no Lexicon registration needed)
    @staticmethod
    def make(text):
        return {"$type": "com.example.state", "text": text}


# --- the durability contract --------------------------------------------------


def test_no_domain_is_ephemeral_did_key():
    ident = Identity.bootstrap()
    assert ident.did.startswith("did:key:")
    assert ident.chain is None
    assert ident.domain is None
    assert ident.state_dir is None


def test_domain_without_state_dir_fails_loudly():
    with pytest.raises(IdentityError, match="requires a durable state_dir"):
        Identity.bootstrap(domain=DOMAIN)


def test_domain_ephemeral_ok_escape_for_tests():
    ident = Identity.bootstrap(domain=DOMAIN, ephemeral_ok=True)
    assert ident.did == f"did:web:{DOMAIN}"
    assert ident.chain is not None
    ident.chain.verify()
    assert ident.state_dir is None  # nothing persisted


# --- create-once / load-forever ----------------------------------------------


def test_didweb_restart_preserves_identity(tmp_path):
    sdir = tmp_path / "id"
    first = Identity.bootstrap(domain=DOMAIN, state_dir=sdir)
    first_did = first.did
    assert first.chain is not None
    twin = first.chain.twin_did
    first.repo.put_record("com.example.state", "s", _Rec.make("hello"))
    signing_pub = crypto.encode_multikey(first.signing_key.public_key())

    # a fresh "process": same state_dir, no mint — load-forever
    reloaded = Identity.bootstrap(domain=DOMAIN, state_dir=sdir)
    assert reloaded.did == first_did  # same DID
    assert crypto.encode_multikey(reloaded.signing_key.public_key()) == signing_pub  # same keys
    assert reloaded.chain is not None
    assert reloaded.chain.twin_did == twin  # same chain / slug
    reloaded.chain.verify()
    rec = reloaded.repo.get_record("com.example.state", "s")
    assert rec is not None
    assert rec["text"] == "hello"  # repo intact


def test_did_key_with_state_dir_is_stable_across_restart(tmp_path):
    sdir = tmp_path / "k"
    first = Identity.bootstrap(state_dir=sdir)  # no domain -> did:key, persistent
    did = first.did
    first.repo.put_record("com.example.state", "s", _Rec.make("kept"))
    reloaded = Identity.bootstrap(state_dir=sdir)
    assert reloaded.did == did
    rec = reloaded.repo.get_record("com.example.state", "s")
    assert rec is not None
    assert rec["text"] == "kept"


# --- rotation-key custody -----------------------------------------------------


def test_rotation_key_is_external_by_default(tmp_path):
    ident = Identity.bootstrap(domain=DOMAIN, state_dir=tmp_path / "id")
    # the private pen is returned once, NOT written to the state_dir
    assert ident.rotation_private_key is not None
    assert not (tmp_path / "id" / "rotation.key").exists()


def test_persist_rotation_key_warns_and_writes(tmp_path):
    with pytest.warns(UserWarning, match="rotation PRIVATE key"):
        ident = Identity.bootstrap(
            domain=DOMAIN, state_dir=tmp_path / "id", persist_rotation_key=True
        )
    assert (tmp_path / "id" / "rotation.key").exists()
    assert ident.rotation_private_key is not None


def test_supplied_rotation_public_keys_keep_secret_off_box(tmp_path):
    cold = crypto.generate_keypair("k256")
    cold_did = crypto.encode_did_key(cold.public_key())
    ident = Identity.bootstrap(
        domain=DOMAIN, state_dir=tmp_path / "id", rotation_public_keys=[cold_did]
    )
    assert ident.rotation_private_key is None  # we never had it
    assert ident.chain is not None
    assert ident.chain.head.rotation_keys == [cold_did]
    assert not (tmp_path / "id" / "rotation.key").exists()


# --- rotation -----------------------------------------------------------------


def test_rotate_appends_a_page_and_survives_restart(tmp_path):
    sdir = tmp_path / "id"
    ident = Identity.bootstrap(domain=DOMAIN, state_dir=sdir)
    pen = ident.rotation_private_key
    assert pen is not None
    new_signing = crypto.generate_keypair("k256")

    ident.rotate(new_signing, pen)
    assert ident.chain is not None
    ident.chain.verify()
    assert len(ident.chain.pages) == 2
    new_pub = crypto.encode_multikey(new_signing.public_key())
    assert crypto.encode_multikey(ident.signing_key.public_key()) == new_pub

    # restart: the rotated key + extended chain persist, and the repo head
    # (re-signed under the new key) still loads
    reloaded = Identity.bootstrap(domain=DOMAIN, state_dir=sdir)
    assert crypto.encode_multikey(reloaded.signing_key.public_key()) == new_pub
    assert reloaded.chain is not None
    assert len(reloaded.chain.pages) == 2
    reloaded.chain.verify()


def test_rotate_with_wrong_pen_fails_early(tmp_path):
    ident = Identity.bootstrap(domain=DOMAIN, state_dir=tmp_path / "id")
    wrong = crypto.generate_keypair("k256")
    with pytest.raises(DIDDocumentInvalid, match="rotationKeys entry"):
        ident.rotate(crypto.generate_keypair("k256"), wrong)
    assert ident.chain is not None
    assert len(ident.chain.pages) == 1  # nothing appended


def test_did_key_cannot_rotate():
    ident = Identity.bootstrap()
    with pytest.raises(IdentityError, match="did:key"):
        ident.rotate(crypto.generate_keypair("k256"), crypto.generate_keypair("k256"))


# --- visible rebirth ----------------------------------------------------------


def test_lost_state_is_a_visible_rebirth(tmp_path):
    """Losing the state_dir and re-bootstrapping under the same domain mints a
    NEW genesis -> NEW slug/twin: a visible rebirth, never a silent swap."""
    first = Identity.bootstrap(domain=DOMAIN, state_dir=tmp_path / "a")
    reborn = Identity.bootstrap(domain=DOMAIN, state_dir=tmp_path / "b")  # amnesia
    assert first.did == reborn.did  # same pretty DID (same domain)
    assert first.chain is not None
    assert reborn.chain is not None
    assert first.chain.twin_did != reborn.chain.twin_did  # DIFFERENT twin/slug


def _head_cid(sdir):
    return CID.decode((sdir / "repo_head").read_text().strip())


def test_repo_head_always_names_a_present_block(tmp_path):
    """The durable head pointer must always name a block still in the store —
    the persist-before-GC ordering guarantees it, even across a rotation."""
    sdir = tmp_path / "id"
    ident = Identity.bootstrap(domain=DOMAIN, state_dir=sdir)
    assert ident.store.get(_head_cid(sdir)) is not None  # genesis head
    ident.repo.put_record("com.example.state", "s", _Rec.make("x"))
    assert ident.store.get(_head_cid(sdir)) is not None  # after a write + GC
    assert ident.rotation_private_key is not None
    ident.rotate(crypto.generate_keypair("k256"), ident.rotation_private_key)
    assert ident.store.get(_head_cid(sdir)) is not None  # after a rotation recommit


def test_persisted_keys_are_not_world_readable(tmp_path):
    import stat

    sdir = tmp_path / "id"
    with pytest.warns(UserWarning):
        Identity.bootstrap(domain=DOMAIN, state_dir=sdir, persist_rotation_key=True)
    for name in ("signing.key", "rotation.key"):
        mode = stat.S_IMODE((sdir / name).stat().st_mode)
        assert mode == 0o600, f"{name} is {oct(mode)}, not 0o600"


def test_load_fails_closed_on_corrupt_head_block(tmp_path):
    sdir = tmp_path / "id"
    ident = Identity.bootstrap(domain=DOMAIN, state_dir=sdir)
    head = _head_cid(sdir)
    ident.store.put(head, b"\xde\xad\xbe\xef not a commit")  # corrupt under the same key
    assert isinstance(ident.store, SqliteBlockStore)
    ident.store.close()
    with pytest.raises(RepoError, match="does not hash"):
        Identity.bootstrap(domain=DOMAIN, state_dir=sdir)


def test_tampered_persisted_chain_fails_closed_on_load(tmp_path):
    import json

    sdir = tmp_path / "id"
    Identity.bootstrap(domain=DOMAIN, state_dir=sdir)
    # a tamperer edits the persisted chain (swaps rotationKeys) but keeps the slug
    data = json.loads((sdir / "chain.json").read_text())
    attacker = crypto.generate_keypair("k256")
    data["chain"][0]["document"]["rotationKeys"] = [crypto.encode_did_key(attacker.public_key())]
    (sdir / "chain.json").write_text(json.dumps(data))
    with pytest.raises(DIDDocumentInvalid, match="genesis hash"):
        Identity.bootstrap(domain=DOMAIN, state_dir=sdir)


# --- adopting an existing did:web ----------------------------------------------


def test_adopt_web_keeps_did_and_key(tmp_path):
    existing = crypto.generate_keypair("p256")  # the key they already serve
    ident = Identity.adopt_web(DOMAIN, existing, tmp_path / "id")
    assert ident.did == f"did:web:{DOMAIN}"
    assert ident.curve == "p256"  # inferred from the imported key, no curve= param
    assert crypto.encode_did_key(ident.signing_key.public_key()) == crypto.encode_did_key(
        existing.public_key()
    )
    ident.chain.verify()
    # chain genesis and served pretty doc both bind the EXISTING key
    multikey = crypto.encode_multikey(existing.public_key())
    assert ident.chain.head.get_verification_method().public_key_multibase == multikey
    assert ident.pretty_document().get_verification_method().public_key_multibase == multikey
    assert ident.rotation_private_key is not None  # the pen, returned once


def test_adopt_web_reloads_via_plain_bootstrap(tmp_path):
    existing = crypto.generate_keypair()
    sdir = tmp_path / "id"
    adopted = Identity.adopt_web(DOMAIN, existing, sdir)
    loaded = Identity.bootstrap(domain=DOMAIN, state_dir=sdir)
    assert loaded.did == adopted.did
    assert crypto.encode_did_key(loaded.signing_key.public_key()) == crypto.encode_did_key(
        existing.public_key()
    )
    assert loaded.chain is not None and loaded.chain.twin_did == adopted.chain.twin_did


def test_adopt_web_refuses_to_clobber(tmp_path):
    sdir = tmp_path / "id"
    first = Identity.bootstrap(domain=DOMAIN, state_dir=sdir)
    assert isinstance(first.store, SqliteBlockStore)
    first.store.close()
    with pytest.raises(IdentityError, match="refusing to clobber"):
        Identity.adopt_web(DOMAIN, crypto.generate_keypair(), sdir)


def test_adopted_repo_migrates_without_resigning(tmp_path):
    """The bring-your-repo story: same key, so the old repo's commit chain is
    already valid — import_repo_car verifies and adopts it, nothing re-signed,
    and the head survives a restart."""
    from fastatp.repo.repo import Repo

    key = crypto.generate_keypair()
    old = Repo(f"did:web:{DOMAIN}", key)  # their previous host's repo
    old.put_record("com.example.state", "one", _Rec.make("carried over"))
    old_head = old.commit_cid
    car = old.export_car()

    sdir = tmp_path / "id"
    ident = Identity.adopt_web(DOMAIN, key, sdir)
    ident.import_repo_car(car)  # verifies against the SAME key
    assert ident.repo.commit_cid == old_head  # the identical signed commit
    assert ident.repo.get_record("com.example.state", "one")["text"] == "carried over"

    loaded = Identity.bootstrap(domain=DOMAIN, state_dir=sdir)
    assert loaded.repo.commit_cid == old_head  # durable head followed the import
    assert loaded.repo.get_record("com.example.state", "one")["text"] == "carried over"


def test_import_repo_car_rejects_foreign_repo(tmp_path):
    from fastatp.repo.repo import Repo

    key = crypto.generate_keypair()
    ident = Identity.adopt_web(DOMAIN, key, tmp_path / "id")

    # a CAR signed by a DIFFERENT key under the same DID must not import
    thief = Repo(f"did:web:{DOMAIN}", crypto.generate_keypair())
    with pytest.raises(RepoError):
        ident.import_repo_car(thief.export_car())

    # a CAR for a different DID must not import either
    other = Repo("did:web:other.example", key)
    with pytest.raises(RepoError, match="CAR is for"):
        ident.import_repo_car(other.export_car())


def test_import_gc_false_preserves_old_head_until_collected(tmp_path):
    """The persist-head-before-GC ordering seam: with gc=False both the old
    and imported heads are loadable; collect_garbage() then drops the old."""
    from fastatp.repo.repo import Repo

    key = crypto.generate_keypair()
    old = Repo(f"did:web:{DOMAIN}", key)
    old.put_record("com.example.state", "one", _Rec.make("x"))
    car = old.export_car()

    target = Repo(f"did:web:{DOMAIN}", key)
    genesis_head = target.commit_cid
    target.import_car(car, verify_key=key.public_key(), gc=False)
    assert target.store.get(genesis_head) is not None  # old head still loadable
    target.collect_garbage()
    assert target.store.get(genesis_head) is None  # now (and only now) dropped


def test_import_crash_before_head_write_keeps_identity_loadable(tmp_path, monkeypatch):
    """A crash between the state swap and the repo_head write must leave the
    OLD head loadable — never a repo_head naming a GC'd block."""
    from pathlib import Path

    from fastatp.repo.repo import Repo

    key = crypto.generate_keypair()
    old = Repo(f"did:web:{DOMAIN}", key)
    old.put_record("com.example.state", "one", _Rec.make("carried over"))
    car = old.export_car()

    sdir = tmp_path / "id"
    ident = Identity.adopt_web(DOMAIN, key, sdir)
    genesis_head = ident.repo.commit_cid

    def crash(self, *args, **kwargs):
        raise RuntimeError("simulated crash before head write")

    monkeypatch.setattr(Path, "write_text", crash)
    with pytest.raises(RuntimeError, match="simulated crash"):
        ident.import_repo_car(car)
    monkeypatch.undo()
    assert isinstance(ident.store, SqliteBlockStore)
    ident.store.close()

    # reload: repo_head still names the genesis head, whose block must exist
    reloaded = Identity.bootstrap(domain=DOMAIN, state_dir=sdir)
    assert reloaded.repo.commit_cid == genesis_head
    reloaded.import_repo_car(car)  # the migration simply retries
    assert reloaded.repo.get_record("com.example.state", "one")["text"] == "carried over"
