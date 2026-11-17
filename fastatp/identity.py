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

Where the repo lives is one decision. ``storage=`` takes the builder's own
database (anything with ``blocks``, ``heads`` and ``blobs``, see
:mod:`fastatp.repo.storage`); without it a ``state_dir`` means the built-in
sqlite there, and neither means memory. The identity's small files (the
document chain, the pen when it is a software key, ``identity.json``) always
live in the ``state_dir``; the repo, its head and its firehose seq live in the
storage.

The signing pen may live in hardware. ``bootstrap(..., signer=)`` and
``adopt_web(domain, signer, ...)`` take any :class:`fastatp.Signer` (a TPM, an
HSM, a secure element) in place of a key: nothing is minted, no key bytes are
written, and the ``state_dir`` records only the pen's public key. Loading such
an identity needs the signer again (the app re-opens its handle each boot),
and a signer whose public key is not the recorded one is refused. ``rotate``
takes a key or a signer the same way.

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
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastatp.cid import CID
from fastatp.crypto import (
    PrivateKey,
    Signer,
    as_signer,
    curve_name,
    encode_did_key,
    encode_multikey,
    generate_keypair,
    load_private_key_bytes,
    sign_with,
)
from fastatp.did.continuity import ContinuityChain, build_did_document, build_pretty
from fastatp.did.document import DIDDocument
from fastatp.exceptions import IdentityError
from fastatp.repo.head import Head, HeadStore
from fastatp.repo.repo import Repo
from fastatp.repo.storage import MemoryStorage, SqliteStorage, Storage
from fastatp.repo.store import BlockStore

if TYPE_CHECKING:
    from fastatp.peer import Peer
    from fastatp.server.peer_server import PeerServer

_META = "identity.json"
_SIGNING_KEY = "signing.key"
_ROTATION_KEY = "rotation.key"
_CHAIN = "chain.json"
_HEAD = "repo_head"  # pre-0.3 head file, folded into the head store on first load


def _key_bytes(key: PrivateKey) -> bytes:
    return key.private_numbers().private_value.to_bytes(32, "big")


def _software_key(pen: PrivateKey | Signer) -> PrivateKey | None:
    """The private key when the pen is one; None when it is a signer whose
    bytes this process never sees."""
    return pen if isinstance(pen, PrivateKey) else None


def _write_pen(sdir: Path, pen: PrivateKey | Signer, meta: dict[str, Any]) -> None:
    """Persist the pen the only way it can be: a software key as bytes, a
    hardware pen as its public key alone (the app brings the signer back on
    the next load). ``meta`` is updated in place and written."""
    key = _software_key(pen)
    meta["pen"] = "software" if key is not None else "external"
    meta["public_key"] = encode_multikey(as_signer(pen).public_key)
    if key is not None:
        _write_secret(sdir / _SIGNING_KEY, _key_bytes(key))
    (sdir / _META).write_text(json.dumps(meta))  # the marker is durable before...
    if key is None:
        (sdir / _SIGNING_KEY).unlink(missing_ok=True)  # ...the old software pen goes


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


def _storage_for(sdir: Path | None, storage: Storage | None) -> Storage:
    """The one storage decision: the builder's own database, else the built-in
    sqlite in the state dir, else memory (an ephemeral peer)."""
    if storage is not None:
        return storage
    return SqliteStorage(sdir) if sdir is not None else MemoryStorage()


async def _fold_legacy_head(sdir: Path, did: str, heads: HeadStore) -> None:
    """A ``repo_head`` file from before 0.3 is read once and folded into the
    head store (its seq starts at 0: followers of that stream resync once,
    never again)."""
    legacy = sdir / _HEAD
    if legacy.exists() and await heads.load(did) is None:
        await heads.swap(did, None, Head(CID.decode(legacy.read_text().strip()), 0))


@dataclass
class Identity:
    """A bootstrapped identity: its DID, signing key, durable repo, and (for a
    ``did:web`` identity) its self-certifying continuity chain."""

    did: str
    #: what this identity signs with: a SoftwareSigner over ``signing_key``,
    #: or the app's own Signer for a pen in hardware
    signer: Signer
    storage: Storage
    repo: Repo
    #: the software private key, when the pen is one; None for a hardware pen
    signing_key: PrivateKey | None = None
    domain: str | None = None
    chain: ContinuityChain | None = None
    state_dir: Path | None = None
    curve: str = "k256"
    #: the freshly generated rotation PRIVATE key, returned exactly once at mint
    #: (store it cold — the recovery pen); None when supplied externally or loaded
    rotation_private_key: PrivateKey | None = field(default=None, repr=False)

    @property
    def store(self) -> BlockStore:
        """The repo's block store (``storage.blocks``)."""
        return self.storage.blocks

    # --- peers / servers ------------------------------------------------------

    def peer(self, *, resolver: Any = None, authorize: Any = None) -> Peer:
        from fastatp.peer import Peer

        return Peer(self.did, self.signer, resolver=resolver, authorize=authorize)

    def create_server(self, *, resolver: Any = None, **kwargs: Any) -> PeerServer:
        """A PeerServer hosting this identity's durable repo."""
        from fastatp.server.peer_server import PeerServer

        return PeerServer(self.peer(resolver=resolver), repo=self.repo, **kwargs)

    # --- served documents (for DIDServer / the continuity resolver) -----------

    def pretty_document(self) -> DIDDocument:
        if self.domain is None or self.chain is None:
            raise IdentityError("a did:key identity has no did:web pretty document")
        return build_pretty(self.domain, self.signer.public_key, self.chain.twin_did)

    def twin_document(self) -> DIDDocument:
        if self.chain is None:
            raise IdentityError("a did:key identity has no continuity twin")
        return self.chain.head

    # --- rotation -------------------------------------------------------------

    async def rotate(
        self, new_signing_key: PrivateKey | Signer, rotation_private_key: PrivateKey
    ) -> None:
        """Turn the page: sign a new document (with the new signing key) using
        the supplied rotation pen, append it to the chain, switch the serving
        key, and re-sign the repo head under the new key. The pen is checked
        against the current head's ``rotationKeys`` before anything changes.
        ``new_signing_key`` may be a :class:`Signer`: an identity can rotate
        onto a hardware pen, or off one."""
        if self.chain is None or self.domain is None:
            raise IdentityError(
                "only a did:web identity rotates; a did:key's name IS its key "
                "(a lost did:key is a new did:key, i.e. a new identity)"
            )
        new_signer = as_signer(new_signing_key)
        # prove the new pen before anything changes: a hardware pen that is
        # unavailable, or signs with a key other than the one it reports,
        # fails here, with the chain, the state_dir and the head untouched
        await sign_with(new_signer, b"fastatp: rotation probe")
        new_doc = build_did_document(
            did=self.chain.twin_did,
            signing_key=new_signer.public_key,
            also_known_as=[f"did:web:{self.domain}"],
            rotation_keys=list(self.chain.head.rotation_keys),
        )
        self.chain.append(new_doc, rotation_private_key)  # raises on a wrong pen
        # persist the identity state (extended chain, then the new pen) BEFORE
        # re-signing the repo head, so a crash mid-rotation always reloads a
        # consistent identity. The chain goes first and the pen's marker last:
        # a crash between them leaves a chain whose head names a pen the box
        # does not hold, which ``_load`` recognises and either completes (the
        # pen is handed in) or rolls back (the page is dropped). The repo head
        # loads by content address either way.
        if self.state_dir is not None:
            (self.state_dir / _CHAIN).write_text(json.dumps(self.chain.to_json()))
            meta = json.loads((self.state_dir / _META).read_text())
            _write_pen(self.state_dir, new_signing_key, meta)
        self.signing_key = _software_key(new_signing_key)
        self.signer = new_signer
        self.repo.signer = new_signer
        await self.repo.recommit()  # re-sign the head under the new key (persists new head)

    # --- repo migration ---------------------------------------------------------

    async def import_repo_car(self, data: bytes, *, verify: bool = True) -> None:
        """Bring an existing repo's full CAR into this identity (checkpoint load).

        With ``verify=True`` (default) the CAR's head commit must verify
        against THIS identity's signing key — the adopt-your-did:web story:
        same key, so the existing commit chain is already valid and nothing
        needs re-signing. Persists the new head so a reload resumes from it.
        """
        # the import writes its blocks, then swaps the head in the head store
        # (durable before anything is collected), then garbage-collects
        await self.repo.import_car(data, verify_key=self.signer.public_key if verify else None)

    # --- bootstrap ------------------------------------------------------------

    @classmethod
    async def adopt_web(
        cls,
        domain: str,
        signing_key: PrivateKey | Signer,
        state_dir: str | Path,
        *,
        rotation_public_keys: list[str] | None = None,
        persist_rotation_key: bool = False,
        storage: Storage | None = None,
    ) -> Identity:
        """Adopt an EXISTING did:web into FastATP: same DID, same signing key.

        The one difference from a fresh mint: the signing key is imported,
        not generated (a :class:`Signer` for a pen already in hardware; it is
        then needed again on every load). Everything else is identical — a fresh continuity
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
        return await cls._mint_did_web(
            domain,
            sdir,
            rotation_public_keys,
            persist_rotation_key,
            curve_name(as_signer(signing_key).public_key.curve),
            signing_key=signing_key,
            storage=storage,
        )

    @classmethod
    async def bootstrap(
        cls,
        *,
        domain: str | None = None,
        state_dir: str | Path | None = None,
        ephemeral_ok: bool = False,
        rotation_public_keys: list[str] | None = None,
        persist_rotation_key: bool = False,
        curve: str = "k256",
        signer: Signer | None = None,
        storage: Storage | None = None,
    ) -> Identity:
        """Stand up a durable peer: mint on the first call, load on every
        later one. ``signer`` puts the pen in hardware: it is used in place of
        a minted key, nothing secret is written, and it must be passed again
        on every load (its public key is checked against the recorded one).
        ``storage`` puts the repo in the builder's own database and must be
        passed again on every load too; without it the repo lives in the
        ``state_dir``'s sqlite."""
        sdir = Path(state_dir) if state_dir is not None else None

        if domain is None:
            return await cls._bootstrap_did_key(sdir, curve, signer, storage)

        if sdir is None:
            if not ephemeral_ok:
                raise IdentityError(
                    f"domain={domain!r} promises continuity, which requires a durable "
                    "state_dir (keys + genesis + chain). Refusing to mint an ephemeral "
                    "genesis under a durable name — that is the 'new occupant looks like "
                    "rotation' event this gate prevents. Pass a state_dir, or "
                    "ephemeral_ok=True for tests only."
                )
            return await cls._mint_did_web(
                domain,
                None,
                rotation_public_keys,
                persist_rotation_key,
                curve,
                signing_key=signer,
                storage=storage,
            )

        if (sdir / _META).exists():
            return await cls._load(sdir, signer, storage)
        sdir.mkdir(parents=True, exist_ok=True)
        return await cls._mint_did_web(
            domain,
            sdir,
            rotation_public_keys,
            persist_rotation_key,
            curve,
            signing_key=signer,
            storage=storage,
        )

    # --- bootstrap internals --------------------------------------------------

    @classmethod
    async def _bootstrap_did_key(
        cls, sdir: Path | None, curve: str, signer: Signer | None, storage: Storage | None
    ) -> Identity:
        if sdir is not None and (sdir / _META).exists():
            return await cls._load(sdir, signer, storage)
        pen: PrivateKey | Signer = signer if signer is not None else generate_keypair(curve)
        signing = as_signer(pen)
        curve = curve_name(signing.public_key.curve)
        did = encode_did_key(signing.public_key)
        kept = _storage_for(sdir, storage)
        if sdir is None:
            repo = await Repo.create(did, signing, storage=kept)
            return cls(
                did=did,
                signer=signing,
                storage=kept,
                repo=repo,
                signing_key=_software_key(pen),
                curve=curve,
            )
        sdir.mkdir(parents=True, exist_ok=True)
        repo = await Repo.create(did, signing, storage=kept)
        _write_pen(sdir, pen, {"kind": "did:key", "did": did, "domain": None, "curve": curve})
        return cls(
            did=did,
            signer=signing,
            storage=kept,
            repo=repo,
            signing_key=_software_key(pen),
            state_dir=sdir,
            curve=curve,
        )

    @classmethod
    async def _mint_did_web(
        cls,
        domain: str,
        sdir: Path | None,
        rotation_public_keys: list[str] | None,
        persist_rotation_key: bool,
        curve: str,
        *,
        signing_key: PrivateKey | Signer | None = None,  # imported or hardware; else minted
        storage: Storage | None = None,
    ) -> Identity:
        pen: PrivateKey | Signer = (
            signing_key if signing_key is not None else generate_keypair(curve)
        )
        signing = as_signer(pen)
        curve = curve_name(signing.public_key.curve)  # the pen decides, whatever curve= said
        rotation_private: PrivateKey | None = None
        if rotation_public_keys:
            rotation_dids = list(rotation_public_keys)  # generated off-box; secret never here
        else:
            rotation_private = generate_keypair(curve)
            rotation_dids = [encode_did_key(rotation_private.public_key())]

        chain = ContinuityChain.create(domain, signing.public_key, rotation_dids)
        did = f"did:web:{domain}"

        kept = _storage_for(sdir, storage)
        if sdir is None:  # ephemeral_ok test mode: mint in memory, persist nothing
            repo = await Repo.create(did, signing, storage=kept)
            return cls(
                did=did,
                signer=signing,
                storage=kept,
                repo=repo,
                signing_key=_software_key(pen),
                domain=domain,
                chain=chain,
                curve=curve,
                rotation_private_key=rotation_private,
            )

        repo = await Repo.create(did, signing, storage=kept)
        (sdir / _CHAIN).write_text(json.dumps(chain.to_json()))
        if persist_rotation_key and rotation_private is not None:
            warnings.warn(
                "persist_rotation_key=True writes the rotation PRIVATE key (the recovery "
                "pen) onto the serving box. The durability model recommends holding "
                "it colder than the box; a box compromise is now also a rotation-authority "
                "compromise. Prefer the returned rotation_private_key or rotation_public_keys=.",
                stacklevel=2,
            )
            _write_secret(sdir / _ROTATION_KEY, _key_bytes(rotation_private))
        # identity.json last: it is what bootstrap() treats as "an identity
        # lives here", so nothing is loadable until everything is written
        _write_pen(sdir, pen, {"kind": "did:web", "did": did, "domain": domain, "curve": curve})
        return cls(
            did=did,
            signer=signing,
            storage=kept,
            repo=repo,
            signing_key=_software_key(pen),
            domain=domain,
            chain=chain,
            state_dir=sdir,
            curve=curve,
            rotation_private_key=rotation_private,
        )

    @classmethod
    async def _load(
        cls, sdir: Path, signer: Signer | None = None, storage: Storage | None = None
    ) -> Identity:
        meta = json.loads((sdir / _META).read_text())
        curve = meta.get("curve", "k256")
        did = meta["did"]
        recorded = meta.get("public_key")
        chain = None
        if meta.get("kind") == "did:web":
            chain = ContinuityChain.from_json(json.loads((sdir / _CHAIN).read_text()))
            chain.verify()  # fail closed on a corrupt/tampered persisted chain
        head_key = (
            encode_multikey(chain.head.get_verification_method().public_key())
            if chain is not None
            else None
        )

        signing: Signer
        signing_key: PrivateKey | None
        if meta.get("pen") == "external":
            if signer is None:
                raise IdentityError(
                    f"{did}'s pen is in hardware (recorded public key {recorded}); its "
                    "state_dir holds no key. Pass signer= with the pen's Signer to load it."
                )
            if encode_multikey(signer.public_key) != recorded:
                raise IdentityError(
                    f"the signer passed for {did} is not its pen: it holds "
                    f"{encode_multikey(signer.public_key)}, the identity recorded {recorded}"
                )
            # a rotation onto hardware writes the marker before unlinking the
            # old software key; a crash between the two leaves that key here
            (sdir / _SIGNING_KEY).unlink(missing_ok=True)
            signing, signing_key = signer, None
        elif (
            signer is not None
            and head_key is not None
            and head_key == encode_multikey(signer.public_key)
        ):
            # a rotation onto this hardware pen was interrupted after the chain
            # named it and before the pen was recorded: the pen is in hand, so
            # finish the rotation now
            _write_pen(sdir, signer, meta)
            signing, signing_key = signer, None
        else:
            if signer is not None:
                raise IdentityError(
                    f"{did}'s pen is a software key in its state_dir; signer= does not "
                    "apply. To move the pen into hardware, rotate() onto the signer."
                )
            signing_key = load_private_key_bytes((sdir / _SIGNING_KEY).read_bytes(), curve)
            curve_name(signing_key.curve)  # sanity: the key matches the persisted curve
            signing = as_signer(signing_key)

        if chain is not None and head_key != encode_multikey(signing.public_key):
            # the chain was extended but the pen it names was never persisted:
            # a rotation interrupted between its two writes. The page was never
            # served (the pen that would sign under it was lost with the
            # crash), so drop it and carry on with the pen the box holds.
            pen_key = encode_multikey(signing.public_key)
            previous = (
                encode_multikey(chain.pages[-2].document.get_verification_method().public_key())
                if len(chain.pages) > 1
                else None
            )
            if previous != pen_key:
                raise IdentityError(
                    f"{did}'s state_dir is inconsistent: the chain head names {head_key}, "
                    f"the pen is {pen_key}, and no earlier page names it either"
                )
            chain = ContinuityChain(chain.pages[:-1])
            (sdir / _CHAIN).write_text(json.dumps(chain.to_json()))
            warnings.warn(
                f"{did}: a rotation onto {head_key} was interrupted before that pen was "
                f"persisted; its chain page was dropped and the identity continues on "
                f"{pen_key}. Rotate again.",
                stacklevel=2,
            )

        kept = _storage_for(sdir, storage)
        await _fold_legacy_head(sdir, did, kept.heads)
        repo = await Repo.open(did, signing, storage=kept)
        return cls(
            did=did,
            signer=signing,
            storage=kept,
            repo=repo,
            signing_key=signing_key,
            domain=meta.get("domain"),
            chain=chain,
            state_dir=sdir,
            curve=curve,
        )
