"""Identity bootstrap: create-once / load-forever, and the durability contract.

An additive layer over the low-level ``Peer(did=, signing_key=)`` — it does not
change it. ``Identity.bootstrap(...)`` is the "how do I stand up a durable peer"
entry point that enforces the durability contract:

- **No ``domain=`` → an ephemeral ``did:key`` peer.** The name IS the key, so no
  durability is required or meaningful. A ``state_dir`` is *optional* here (it
  makes the same ``did:key`` survive restarts, useful but never required).
- **``domain=`` → durable state is REQUIRED.** A domain is a promise of
  continuity — a name others re-resolve and pin against — so ephemeral keys
  under it are the self-inflicted "new occupant looks like rotation" event.
  Without a ``state_dir`` (and without ``ephemeral_ok=True`` for tests) bootstrap
  **fails loudly**; it never silently mints a genesis under a durable name.

The rotation key is **external by default**: its public half in the genesis
``rotationKeys`` is the registration; the private half is returned to the
operator once (``Identity.rotation_private_key``) and never written to the
``state_dir`` unless ``persist_rotation_key=True`` (a loud, explicit opt-in). Or
pass ``rotation_public_keys=[...]`` generated fully off-box so the secret never
touches the serving machine at all.
"""

from __future__ import annotations

import json
import os
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastatp.cid import CID
from fastatp.crypto import (
    PrivateKey,
    curve_name,
    encode_did_key,
    generate_keypair,
    load_private_key_bytes,
)
from fastatp.did.continuity import ContinuityChain, build_did_document, build_pretty
from fastatp.did.document import DIDDocument
from fastatp.exceptions import IdentityError
from fastatp.repo.repo import CommitEvent, Repo
from fastatp.repo.store import BlockStore, MemoryBlockStore, SqliteBlockStore

if TYPE_CHECKING:
    from fastatp.peer import Peer
    from fastatp.server.peer_server import PeerServer

_META = "identity.json"
_SIGNING_KEY = "signing.key"
_ROTATION_KEY = "rotation.key"
_CHAIN = "chain.json"
_HEAD = "repo_head"
_BLOCKS = "blocks.sqlite"


def _key_bytes(key: PrivateKey) -> bytes:
    return key.private_numbers().private_value.to_bytes(32, "big")


def _write_secret(path: Path, raw: bytes) -> None:
    # create the file 0600 ATOMICALLY (os.open mode, not write-then-chmod): the
    # write-then-chmod pattern leaves a private key world-readable under the
    # default umask for the window between creation and chmod.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, raw)
    finally:
        os.close(fd)
    os.chmod(path, 0o600)  # tighten again in case the file pre-existed looser


def _head_writer(state_dir: Path) -> Callable[[CommitEvent], None]:
    def _persist(event: CommitEvent) -> None:
        (state_dir / _HEAD).write_text(event.commit_cid.encode())

    return _persist


@dataclass
class Identity:
    """A bootstrapped identity: its DID, signing key, durable repo, and (for a
    ``did:web`` identity) its self-certifying continuity chain."""

    did: str
    signing_key: PrivateKey
    store: BlockStore
    repo: Repo
    domain: str | None = None
    chain: ContinuityChain | None = None
    state_dir: Path | None = None
    curve: str = "k256"
    #: the freshly generated rotation PRIVATE key, returned exactly once at mint
    #: (store it cold — the recovery pen); None when supplied externally or loaded
    rotation_private_key: PrivateKey | None = field(default=None, repr=False)

    # --- peers / servers ------------------------------------------------------

    def peer(self, *, resolver: Any = None, authorize: Any = None) -> Peer:
        from fastatp.peer import Peer

        return Peer(self.did, self.signing_key, resolver=resolver, authorize=authorize)

    def create_server(self, *, resolver: Any = None, **kwargs: Any) -> PeerServer:
        """A PeerServer hosting this identity's durable repo."""
        from fastatp.server.peer_server import PeerServer

        return PeerServer(self.peer(resolver=resolver), repo=self.repo, **kwargs)

    # --- served documents (for DIDServer / the continuity resolver) -----------

    def pretty_document(self) -> DIDDocument:
        if self.domain is None or self.chain is None:
            raise IdentityError("a did:key identity has no did:web pretty document")
        return build_pretty(self.domain, self.signing_key.public_key(), self.chain.twin_did)

    def twin_document(self) -> DIDDocument:
        if self.chain is None:
            raise IdentityError("a did:key identity has no continuity twin")
        return self.chain.head

    # --- rotation -------------------------------------------------------------

    def rotate(self, new_signing_key: PrivateKey, rotation_private_key: PrivateKey) -> None:
        """Turn the page: sign a new document (with the new signing key) using
        the supplied rotation pen, append it to the chain, switch the serving
        key, and re-sign the repo head under the new key. The pen is checked
        against the current head's ``rotationKeys`` before anything changes."""
        if self.chain is None or self.domain is None:
            raise IdentityError(
                "only a did:web identity rotates; a did:key's name IS its key "
                "(a lost did:key is a new did:key, i.e. a new identity)"
            )
        new_doc = build_did_document(
            did=self.chain.twin_did,
            signing_key=new_signing_key.public_key(),
            also_known_as=[f"did:web:{self.domain}"],
            rotation_keys=list(self.chain.head.rotation_keys),
        )
        self.chain.append(new_doc, rotation_private_key)  # raises on a wrong pen
        # persist the identity state (new signing key + extended chain) BEFORE
        # re-signing the repo head, so a crash mid-rotation always reloads a
        # consistent identity: the new key/chain are durable before any head
        # moves under them, and the repo head loads by content-address either way
        if self.state_dir is not None:
            _write_secret(self.state_dir / _SIGNING_KEY, _key_bytes(new_signing_key))
            (self.state_dir / _CHAIN).write_text(json.dumps(self.chain.to_json()))
        self.signing_key = new_signing_key
        self.repo.signing_key = new_signing_key
        self.repo.recommit()  # re-sign the head under the new key (persists new head)

    # --- repo migration ---------------------------------------------------------

    def import_repo_car(self, data: bytes, *, verify: bool = True) -> None:
        """Bring an existing repo's full CAR into this identity (checkpoint load).

        With ``verify=True`` (default) the CAR's head commit must verify
        against THIS identity's signing key — the adopt-your-did:web story:
        same key, so the existing commit chain is already valid and nothing
        needs re-signing. Persists the new head so a reload resumes from it.
        """
        # gc=False: the durable head pointer must be persisted while BOTH the
        # old and imported states are still in the store — a crash anywhere in
        # this sequence leaves repo_head naming a block that exists (old head
        # pre-write, new head post-write). GC only after the pointer is durable.
        self.repo.import_car(
            data, verify_key=self.signing_key.public_key() if verify else None, gc=False
        )
        if self.state_dir is not None:
            # import_car is a checkpoint load, not a commit — it deliberately
            # fires no on_commit (nothing to announce on a firehose), so the
            # durable head pointer is written here instead
            (self.state_dir / _HEAD).write_text(self.repo.commit_cid.encode())
        self.repo.collect_garbage()

    # --- bootstrap ------------------------------------------------------------

    @classmethod
    def adopt_web(
        cls,
        domain: str,
        signing_key: PrivateKey,
        state_dir: str | Path,
        *,
        rotation_public_keys: list[str] | None = None,
        persist_rotation_key: bool = False,
    ) -> Identity:
        """Adopt an EXISTING did:web into FastATP: same DID, same signing key.

        The one difference from a fresh mint: the signing key is imported,
        not generated. Everything else is identical — a fresh continuity
        chain genesis binds the domain to the *current* key, a fresh rotation
        pen is returned once (or ``rotation_public_keys`` supplied off-box),
        and an empty-genesis repo is created (see ``import_repo_car`` to
        bring an existing repo across).

        Because the key is unchanged, vanilla did:web resolvers observe
        nothing: old records keep verifying, the served document merely gains
        the twin alias. Be honest about the boundary: the chain certifies
        custody FROM ADOPTION FORWARD — it cannot retroactively attest
        pre-adoption history, and whoever holds this key at adoption time is
        who the chain binds. Adopt with a key you still exclusively control.

        The curve is inferred from the supplied key. Refuses to clobber a
        ``state_dir`` that already holds an identity — adoption is a one-time
        migration; use ``bootstrap()`` to load thereafter.
        """
        sdir = Path(state_dir)
        if (sdir / _META).exists():
            raise IdentityError(
                f"state_dir {sdir} already holds an identity — refusing to clobber. "
                "Adoption is a one-time migration; use Identity.bootstrap() to load it."
            )
        sdir.mkdir(parents=True, exist_ok=True)
        return cls._mint_did_web(
            domain,
            sdir,
            rotation_public_keys,
            persist_rotation_key,
            curve_name(signing_key.curve),
            signing_key=signing_key,
        )

    @classmethod
    def bootstrap(
        cls,
        *,
        domain: str | None = None,
        state_dir: str | Path | None = None,
        ephemeral_ok: bool = False,
        rotation_public_keys: list[str] | None = None,
        persist_rotation_key: bool = False,
        curve: str = "k256",
    ) -> Identity:
        sdir = Path(state_dir) if state_dir is not None else None

        if domain is None:
            return cls._bootstrap_did_key(sdir, curve)

        if sdir is None:
            if not ephemeral_ok:
                raise IdentityError(
                    f"domain={domain!r} promises continuity, which requires a durable "
                    "state_dir (keys + genesis + chain). Refusing to mint an ephemeral "
                    "genesis under a durable name — that is the 'new occupant looks like "
                    "rotation' event this gate prevents. Pass a state_dir, or "
                    "ephemeral_ok=True for tests only."
                )
            return cls._mint_did_web(
                domain, None, rotation_public_keys, persist_rotation_key, curve
            )

        if (sdir / _META).exists():
            return cls._load(sdir)
        sdir.mkdir(parents=True, exist_ok=True)
        return cls._mint_did_web(domain, sdir, rotation_public_keys, persist_rotation_key, curve)

    # --- bootstrap internals --------------------------------------------------

    @classmethod
    def _bootstrap_did_key(cls, sdir: Path | None, curve: str) -> Identity:
        if sdir is not None and (sdir / _META).exists():
            return cls._load(sdir)
        signing_key = generate_keypair(curve)
        did = encode_did_key(signing_key.public_key())
        if sdir is None:
            store: BlockStore = MemoryBlockStore()
            repo = Repo(did, signing_key, store=store)
            return cls(did=did, signing_key=signing_key, store=store, repo=repo, curve=curve)
        sdir.mkdir(parents=True, exist_ok=True)
        store = SqliteBlockStore(sdir / _BLOCKS)
        repo = Repo(did, signing_key, store=store, on_commit=_head_writer(sdir))
        _write_secret(sdir / _SIGNING_KEY, _key_bytes(signing_key))
        (sdir / _META).write_text(
            json.dumps({"kind": "did:key", "did": did, "domain": None, "curve": curve})
        )
        return cls(
            did=did, signing_key=signing_key, store=store, repo=repo, state_dir=sdir, curve=curve
        )

    @classmethod
    def _mint_did_web(
        cls,
        domain: str,
        sdir: Path | None,
        rotation_public_keys: list[str] | None,
        persist_rotation_key: bool,
        curve: str,
        *,
        signing_key: PrivateKey | None = None,  # adopt_web imports; mint generates
    ) -> Identity:
        if signing_key is None:
            signing_key = generate_keypair(curve)
        rotation_private: PrivateKey | None = None
        if rotation_public_keys:
            rotation_dids = list(rotation_public_keys)  # generated off-box; secret never here
        else:
            rotation_private = generate_keypair(curve)
            rotation_dids = [encode_did_key(rotation_private.public_key())]

        chain = ContinuityChain.create(domain, signing_key.public_key(), rotation_dids)
        did = f"did:web:{domain}"

        if sdir is None:  # ephemeral_ok test mode: mint in memory, persist nothing
            store: BlockStore = MemoryBlockStore()
            repo = Repo(did, signing_key, store=store)
            return cls(
                did=did,
                signing_key=signing_key,
                store=store,
                repo=repo,
                domain=domain,
                chain=chain,
                curve=curve,
                rotation_private_key=rotation_private,
            )

        store = SqliteBlockStore(sdir / _BLOCKS)
        repo = Repo(did, signing_key, store=store, on_commit=_head_writer(sdir))
        _write_secret(sdir / _SIGNING_KEY, _key_bytes(signing_key))
        (sdir / _CHAIN).write_text(json.dumps(chain.to_json()))
        (sdir / _META).write_text(
            json.dumps({"kind": "did:web", "did": did, "domain": domain, "curve": curve})
        )
        if persist_rotation_key and rotation_private is not None:
            warnings.warn(
                "persist_rotation_key=True writes the rotation PRIVATE key (the recovery "
                "pen) onto the serving box. The durability model recommends holding "
                "it colder than the box; a box compromise is now also a rotation-authority "
                "compromise. Prefer the returned rotation_private_key or rotation_public_keys=.",
                stacklevel=2,
            )
            _write_secret(sdir / _ROTATION_KEY, _key_bytes(rotation_private))
        return cls(
            did=did,
            signing_key=signing_key,
            store=store,
            repo=repo,
            domain=domain,
            chain=chain,
            state_dir=sdir,
            curve=curve,
            rotation_private_key=rotation_private,
        )

    @classmethod
    def _load(cls, sdir: Path) -> Identity:
        meta = json.loads((sdir / _META).read_text())
        curve = meta.get("curve", "k256")
        signing_key = load_private_key_bytes((sdir / _SIGNING_KEY).read_bytes(), curve)
        # sanity: the loaded key must produce the persisted curve name
        curve_name(signing_key.curve)
        did = meta["did"]
        store = SqliteBlockStore(sdir / _BLOCKS)
        head_text = (sdir / _HEAD).read_text().strip()
        head_cid = CID.decode(head_text)
        repo = Repo(did, signing_key, store=store, head_cid=head_cid, on_commit=_head_writer(sdir))
        chain = None
        if meta.get("kind") == "did:web":
            chain = ContinuityChain.from_json(json.loads((sdir / _CHAIN).read_text()))
            chain.verify()  # fail closed on a corrupt/tampered persisted chain
        return cls(
            did=did,
            signing_key=signing_key,
            store=store,
            repo=repo,
            domain=meta.get("domain"),
            chain=chain,
            state_dir=sdir,
            curve=curve,
        )
