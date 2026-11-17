"""An Identity whose pen is in hardware: bootstrapped, reloaded, rotated.

The signer stands in for a TPM: it holds a key the identity never sees and
returns DER. Nothing secret reaches the state directory; the app brings the
signer back on every load and it is checked against the recorded pen.
"""

import json

import pytest

from fastatp import Identity, SoftwareSigner, crypto
from fastatp.exceptions import IdentityError

DOMAIN = "hw.example"


class _HardwareSigner:
    """A pen the process cannot hold: not a PrivateKey, signs on request."""

    def __init__(self, curve: str = "p256") -> None:
        self._inner = SoftwareSigner(crypto.generate_keypair(curve))

    @property
    def public_key(self):
        return self._inner.public_key

    async def sign(self, message: bytes) -> bytes:
        return await self._inner.sign(message)


def _meta(sdir):
    return json.loads((sdir / "identity.json").read_text())


async def test_bootstrap_on_a_hardware_pen_writes_no_secret(tmp_path):
    sdir = tmp_path / "id"
    pen = _HardwareSigner()
    ident = await Identity.bootstrap(domain=DOMAIN, state_dir=sdir, signer=pen)

    assert ident.signer is pen
    assert ident.signing_key is None
    assert ident.curve == "p256"  # inferred from the pen, whatever curve= said
    assert not (sdir / "signing.key").exists()
    meta = _meta(sdir)
    assert meta["pen"] == "external"
    assert meta["public_key"] == crypto.encode_multikey(pen.public_key)
    assert ident.repo.commit.verify(pen.public_key)
    pretty = ident.pretty_document()
    assert pretty.get_verification_method().public_key() == pen.public_key
    assert ident.twin_document().get_verification_method().public_key() == pen.public_key


async def test_a_hardware_pen_identity_reloads_only_with_its_pen(tmp_path):
    sdir = tmp_path / "id"
    pen = _HardwareSigner()
    first = await Identity.bootstrap(domain=DOMAIN, state_dir=sdir, signer=pen)
    await first.repo.put_record("com.example.note", "n", {"$type": "com.example.note", "t": 1})

    again = await Identity.bootstrap(domain=DOMAIN, state_dir=sdir, signer=pen)
    assert again.did == first.did and again.repo.rev == first.repo.rev
    assert again.signer is pen and again.signing_key is None

    with pytest.raises(IdentityError, match="pen is in hardware"):
        await Identity.bootstrap(domain=DOMAIN, state_dir=sdir)
    with pytest.raises(IdentityError, match="not its pen"):
        await Identity.bootstrap(domain=DOMAIN, state_dir=sdir, signer=_HardwareSigner())


async def test_a_software_identity_refuses_a_stray_signer(tmp_path):
    sdir = tmp_path / "id"
    await Identity.bootstrap(domain=DOMAIN, state_dir=sdir)
    with pytest.raises(IdentityError, match="signer= does not apply"):
        await Identity.bootstrap(domain=DOMAIN, state_dir=sdir, signer=_HardwareSigner())


async def test_rotate_moves_the_pen_into_hardware_and_back(tmp_path):
    sdir = tmp_path / "id"
    ident = await Identity.bootstrap(domain=DOMAIN, state_dir=sdir)
    rotation = ident.rotation_private_key
    assert rotation is not None and (sdir / "signing.key").exists()

    pen = _HardwareSigner()
    await ident.rotate(pen, rotation)
    assert ident.signer is pen and ident.signing_key is None
    assert not (sdir / "signing.key").exists()
    assert _meta(sdir)["pen"] == "external"
    assert ident.repo.commit.verify(pen.public_key)  # the head was re-signed
    assert ident.pretty_document().get_verification_method().public_key() == pen.public_key
    with pytest.raises(IdentityError, match="pen is in hardware"):
        await Identity.bootstrap(domain=DOMAIN, state_dir=sdir)
    reloaded = await Identity.bootstrap(domain=DOMAIN, state_dir=sdir, signer=pen)
    assert reloaded.chain is not None and len(reloaded.chain.pages) == 2

    back = crypto.generate_keypair("k256")
    await reloaded.rotate(back, rotation)
    assert reloaded.signing_key is back and (sdir / "signing.key").exists()
    assert _meta(sdir)["pen"] == "software"
    plain = await Identity.bootstrap(domain=DOMAIN, state_dir=sdir)  # no signer needed again
    assert plain.signer.public_key == back.public_key()


async def test_adopt_web_with_a_pen_already_in_hardware(tmp_path):
    sdir = tmp_path / "id"
    pen = _HardwareSigner()
    ident = await Identity.adopt_web(DOMAIN, pen, sdir)
    assert ident.did == f"did:web:{DOMAIN}" and ident.signer is pen
    assert ident.chain is not None
    assert ident.chain.head.get_verification_method().public_key() == pen.public_key
    assert not (sdir / "signing.key").exists()
    loaded = await Identity.bootstrap(domain=DOMAIN, state_dir=sdir, signer=pen)
    assert loaded.did == ident.did


async def test_a_did_key_identity_on_a_hardware_pen(tmp_path):
    pen = _HardwareSigner()
    ephemeral = await Identity.bootstrap(signer=pen)
    assert ephemeral.did == crypto.encode_did_key(pen.public_key)
    assert ephemeral.repo.commit.verify(pen.public_key)

    sdir = tmp_path / "dev"
    durable = await Identity.bootstrap(state_dir=sdir, signer=pen)
    assert durable.did == ephemeral.did and _meta(sdir)["pen"] == "external"
    same = await Identity.bootstrap(state_dir=sdir, signer=pen)
    assert same.did == durable.did
    with pytest.raises(IdentityError, match="pen is in hardware"):
        await Identity.bootstrap(state_dir=sdir)


async def test_ephemeral_did_web_on_a_hardware_pen():
    pen = _HardwareSigner()
    ident = await Identity.bootstrap(domain=DOMAIN, ephemeral_ok=True, signer=pen)
    assert ident.signer is pen and ident.state_dir is None
    assert ident.peer().signer is pen


# --- a rotation that is interrupted between its writes ------------------------------


class _UnavailableSigner(_HardwareSigner):
    """A pen whose device is not answering."""

    async def sign(self, message: bytes) -> bytes:
        raise OSError("TPM: device not responding")


def _snapshot(sdir):
    return {name: (sdir / name).read_bytes() for name in ("identity.json", "signing.key")}


def _restore(sdir, snap):
    for name, raw in snap.items():
        (sdir / name).write_bytes(raw)


async def test_rotate_probes_the_new_pen_before_changing_anything(tmp_path):
    sdir = tmp_path / "id"
    ident = await Identity.bootstrap(domain=DOMAIN, state_dir=sdir)
    before = _snapshot(sdir)
    chain_before = (sdir / "chain.json").read_bytes()
    head = ident.repo.commit_cid

    with pytest.raises(OSError, match="not responding"):
        await ident.rotate(_UnavailableSigner(), ident.rotation_private_key)
    assert ident.chain is not None and len(ident.chain.pages) == 1
    assert ident.repo.commit_cid == head
    assert _snapshot(sdir) == before and (sdir / "chain.json").read_bytes() == chain_before


async def test_a_rotation_interrupted_before_the_pen_is_persisted_rolls_back(tmp_path):
    """The chain was extended, the crash came before the new pen was written:
    the page names a pen the box never held, so loading drops it."""
    sdir = tmp_path / "id"
    ident = await Identity.bootstrap(domain=DOMAIN, state_dir=sdir)
    old_pub = ident.signer.public_key
    rotation = ident.rotation_private_key
    before = _snapshot(sdir)
    await ident.rotate(crypto.generate_keypair("k256"), rotation)
    _restore(sdir, before)  # chain.json has two pages; pen and marker are the old ones

    with pytest.warns(UserWarning, match="interrupted"):
        loaded = await Identity.bootstrap(domain=DOMAIN, state_dir=sdir)
    assert loaded.signer.public_key == old_pub
    assert loaded.chain is not None and len(loaded.chain.pages) == 1
    assert len(json.loads((sdir / "chain.json").read_text())["chain"]) == 1
    await loaded.rotate(crypto.generate_keypair("k256"), rotation)  # and rotating again works
    assert len(loaded.chain.pages) == 2


async def test_an_interrupted_hardware_rotation_completes_when_the_pen_is_handed_back(tmp_path):
    sdir = tmp_path / "id"
    ident = await Identity.bootstrap(domain=DOMAIN, state_dir=sdir)
    rotation = ident.rotation_private_key
    before = _snapshot(sdir)
    pen = _HardwareSigner()
    await ident.rotate(pen, rotation)
    _restore(sdir, before)  # the chain names the pen; the box still records the old key

    completed = await Identity.bootstrap(domain=DOMAIN, state_dir=sdir, signer=pen)
    assert completed.signer is pen and completed.signing_key is None
    assert _meta(sdir)["pen"] == "external" and not (sdir / "signing.key").exists()
    assert completed.chain is not None and len(completed.chain.pages) == 2
    again = await Identity.bootstrap(domain=DOMAIN, state_dir=sdir, signer=pen)
    assert again.signer is pen  # the completed state loads normally from here on


async def test_an_interrupted_hardware_rotation_rolls_back_without_the_pen(tmp_path):
    sdir = tmp_path / "id"
    ident = await Identity.bootstrap(domain=DOMAIN, state_dir=sdir)
    old_pub = ident.signer.public_key
    before = _snapshot(sdir)
    await ident.rotate(_HardwareSigner(), ident.rotation_private_key)
    _restore(sdir, before)

    with pytest.warns(UserWarning, match="interrupted"):
        loaded = await Identity.bootstrap(domain=DOMAIN, state_dir=sdir)
    assert loaded.signer.public_key == old_pub and len(loaded.chain.pages) == 1


async def test_an_inconsistent_state_dir_fails_closed(tmp_path):
    sdir = tmp_path / "id"
    await Identity.bootstrap(domain=DOMAIN, state_dir=sdir)
    other = await Identity.bootstrap(domain=DOMAIN, state_dir=tmp_path / "other")
    (sdir / "chain.json").write_bytes((tmp_path / "other" / "chain.json").read_bytes())
    with pytest.raises(IdentityError, match="inconsistent"):
        await Identity.bootstrap(domain=DOMAIN, state_dir=sdir)
    assert other.did  # (the other identity is only a source of a foreign chain)
