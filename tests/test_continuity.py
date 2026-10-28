"""Self-certifying did:web continuity primitives.

The slug commits to the genesis hash; each chain page turn is rotation-signed by
its predecessor; verify() fails closed on any tamper, fork, or broken link."""

import pytest

from fastatp import crypto
from fastatp.did.continuity import (
    SCID_PLACEHOLDER,
    ContinuityChain,
    build_genesis,
    build_pretty,
    derive_slug,
)
from fastatp.did.document import build_did_document
from fastatp.exceptions import DIDDocumentInvalid

DOMAIN = "example.com"
PRETTY = "did:web:example.com"


def _keys():
    return crypto.generate_keypair("k256"), crypto.generate_keypair("k256")


def _rot_did(rk):
    return crypto.encode_did_key(rk.public_key())


def _page_doc(twin, signing_pub, rot_dids):
    return build_did_document(
        did=twin, signing_key=signing_pub, also_known_as=[PRETTY], rotation_keys=rot_dids
    )


def test_slug_is_deterministic_and_well_formed():
    sk, rk = _keys()
    twin1, _ = build_genesis(DOMAIN, sk.public_key(), [_rot_did(rk)])
    twin2, _ = build_genesis(DOMAIN, sk.public_key(), [_rot_did(rk)])
    assert twin1 == twin2  # deterministic
    slug = twin1.rsplit(":", 1)[-1]
    assert len(slug) == 32
    assert all(c in "abcdefghijklmnopqrstuvwxyz234567" for c in slug)  # base32-lower
    assert twin1.startswith(PRETTY + ":")


def test_genesis_chain_self_certifies():
    sk, rk = _keys()
    chain = ContinuityChain.create(DOMAIN, sk.public_key(), [_rot_did(rk)])
    chain.verify()  # slug == genesis hash, alsoKnownAs -> pretty
    assert chain.pretty_did == PRETTY
    assert chain.head is chain.genesis


def test_rotation_appends_a_signed_page():
    sk, rk = _keys()
    chain = ContinuityChain.create(DOMAIN, sk.public_key(), [_rot_did(rk)])
    sk2 = crypto.generate_keypair("k256")
    new_doc = _page_doc(chain.twin_did, sk2.public_key(), [_rot_did(rk)])

    chain.append(new_doc, rk)  # signed by the head's rotation key
    chain.verify()
    # the head now serves the rotated signing key; slug/twin unchanged (continuity)
    assert chain.head.get_verification_method().public_key_multibase == crypto.encode_multikey(
        sk2.public_key()
    )
    assert chain.twin_did == build_genesis(DOMAIN, sk.public_key(), [_rot_did(rk)])[0]


def test_append_with_wrong_pen_fails_early():
    sk, rk = _keys()
    chain = ContinuityChain.create(DOMAIN, sk.public_key(), [_rot_did(rk)])
    wrong = crypto.generate_keypair("k256")  # not a rotationKeys entry
    new_doc = _page_doc(chain.twin_did, sk.public_key(), [_rot_did(rk)])
    with pytest.raises(DIDDocumentInvalid, match="not a rotationKeys entry"):
        chain.append(new_doc, wrong)
    assert len(chain.pages) == 1  # nothing appended


def test_append_rejects_id_change():
    sk, rk = _keys()
    chain = ContinuityChain.create(DOMAIN, sk.public_key(), [_rot_did(rk)])
    off_twin = _page_doc("did:web:evil.example:aaaa", sk.public_key(), [_rot_did(rk)])
    with pytest.raises(DIDDocumentInvalid, match="keep the twin id"):
        chain.append(off_twin, rk)


def test_tampered_genesis_fails_closed():
    """A hijacker who edits the served genesis (e.g. swaps rotation keys) breaks
    the slug commitment."""
    sk, rk = _keys()
    chain = ContinuityChain.create(DOMAIN, sk.public_key(), [_rot_did(rk)])
    attacker_rot = crypto.generate_keypair("k256")
    # forge: keep the slug, but serve a genesis with the attacker's rotation key
    chain.pages[0].document.rotation_keys = [_rot_did(attacker_rot)]
    with pytest.raises(DIDDocumentInvalid, match="does not match the genesis hash"):
        chain.verify()


def test_forged_successor_signature_fails_closed():
    """A domain thief serves a new head but cannot rotation-sign it from the
    previous page's key."""
    sk, rk = _keys()
    chain = ContinuityChain.create(DOMAIN, sk.public_key(), [_rot_did(rk)])
    thief = crypto.generate_keypair("k256")
    thief_doc = _page_doc(chain.twin_did, thief.public_key(), [_rot_did(thief)])
    # forge a page signed by the THIEF's key (not a rotationKeys entry of head)
    from fastatp.did.continuity import ChainPage
    from fastatp.did.document import canonical_document_bytes

    bogus_sig = crypto.sign_message(thief, canonical_document_bytes(thief_doc))
    chain.pages.append(ChainPage(thief_doc, bogus_sig))
    with pytest.raises(DIDDocumentInvalid, match="not rotation-signed"):
        chain.verify()


def test_json_round_trip_preserves_verifiability():
    sk, rk = _keys()
    chain = ContinuityChain.create(DOMAIN, sk.public_key(), [_rot_did(rk)])
    sk2 = crypto.generate_keypair("k256")
    chain.append(_page_doc(chain.twin_did, sk2.public_key(), [_rot_did(rk)]), rk)

    restored = ContinuityChain.from_json(chain.to_json())
    restored.verify()
    assert restored.twin_did == chain.twin_did
    assert len(restored.pages) == 2
    assert restored.pages[0].signature is None
    assert restored.pages[1].signature == chain.pages[1].signature


def test_key_loss_is_a_visible_rebirth_not_a_silent_swap():
    """With the pen lost, a new identity is a NEW genesis -> NEW slug/twin. It
    verifies on its own, but it is a different DID: a visible rebirth, never a
    quiet substitution under the old name."""
    sk, rk = _keys()
    original = ContinuityChain.create(DOMAIN, sk.public_key(), [_rot_did(rk)])
    new_sk, new_rk = _keys()
    reborn = ContinuityChain.create(DOMAIN, new_sk.public_key(), [_rot_did(new_rk)])
    original.verify()
    reborn.verify()
    assert reborn.twin_did != original.twin_did  # different slug = visible rebirth


def test_placeholder_is_valid_base32_shape():
    assert len(SCID_PLACEHOLDER) == 32
    assert all(c in "abcdefghijklmnopqrstuvwxyz234567" for c in SCID_PLACEHOLDER)
    # build_pretty binds alsoKnownAs -> twin
    sk, rk = _keys()
    twin, _ = build_genesis(DOMAIN, sk.public_key(), [_rot_did(rk)])
    pretty = build_pretty(DOMAIN, sk.public_key(), twin)
    assert pretty.id == PRETTY
    assert pretty.also_known_as == [twin]
    assert callable(derive_slug)  # imported symbol used
