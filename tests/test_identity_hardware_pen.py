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
