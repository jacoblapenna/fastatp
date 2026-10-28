//! The continuity chain self-certifies, fails closed, and
//! round-trips its JSON-shaped Value form (cross-impl proof lives in
//! tests/test_cross_continuity.py on the Python side).

use fastatp_core::continuity::{build_document, Chain, ContinuityError, SCID_PLACEHOLDER};
use fastatp_core::crypto::{encode_did_key, encode_multikey, Curve, PrivateKey};

const DOMAIN: &str = "shelf.example";

fn mint() -> (Chain, PrivateKey, String) {
    let rotation = PrivateKey::generate(Curve::K256);
    let rotation_did = encode_did_key(&rotation.public_key());
    let signing = PrivateKey::generate(Curve::P256);
    let chain = Chain::create(
        DOMAIN,
        &encode_multikey(&signing.public_key()),
        std::slice::from_ref(&rotation_did),
        false,
    )
    .unwrap();
    (chain, rotation, rotation_did)
}

fn page_two(chain: &Chain, rotation_did: &str) -> fastatp_core::dagcbor::Value {
    let new_signing = PrivateKey::generate(Curve::P256);
    build_document(
        &chain.twin_did().unwrap(),
        &encode_multikey(&new_signing.public_key()),
        &[],
        &[rotation_did.to_owned()],
    )
}

#[test]
fn twin_only_genesis_self_certifies() {
    let (chain, _rotation, _rotation_did) = mint();
    chain.verify().unwrap();
    let twin = chain.twin_did().unwrap();
    assert!(twin.starts_with(&format!("did:web:{DOMAIN}:")));
    assert_eq!(chain.slug().unwrap().len(), 32);
    assert!(!twin.contains(SCID_PLACEHOLDER));
}

#[test]
fn pretty_bound_genesis_also_verifies() {
    let rotation = PrivateKey::generate(Curve::K256);
    let signing = PrivateKey::generate(Curve::K256);
    let chain = Chain::create(
        DOMAIN,
        &encode_multikey(&signing.public_key()),
        &[encode_did_key(&rotation.public_key())],
        true,
    )
    .unwrap();
    chain.verify().unwrap();
}

#[test]
fn tampered_rotation_keys_fail_the_slug() {
    let (mut chain, _rotation, _rotation_did) = mint();
    let stranger = encode_did_key(&PrivateKey::generate(Curve::K256).public_key());
    let doc = &mut chain.pages[0].document;
    // swap the rotationKeys list inside the genesis map
    if let fastatp_core::dagcbor::Value::Map(entries) = doc {
        for (key, value) in entries.iter_mut() {
            if key == "rotationKeys" {
                *value =
                    fastatp_core::dagcbor::Value::Array(vec![fastatp_core::dagcbor::Value::from(
                        stranger.as_str(),
                    )]);
            }
        }
    }
    match chain.verify() {
        Err(ContinuityError::SlugMismatch { .. }) => {}
        other => panic!("expected SlugMismatch, got {other:?}"),
    }
}

#[test]
fn append_verifies_and_wrong_pen_is_refused_before_mutation() {
    let (mut chain, rotation, rotation_did) = mint();
    let wrong_pen = PrivateKey::generate(Curve::K256);

    let doc = page_two(&chain, &rotation_did);
    match chain.append(doc.clone(), &wrong_pen) {
        Err(ContinuityError::WrongPen) => {}
        other => panic!("expected WrongPen, got {other:?}"),
    }
    assert_eq!(chain.pages.len(), 1); // nothing appended

    chain.append(doc, &rotation).unwrap();
    chain.verify().unwrap();
    assert_eq!(chain.pages.len(), 2);
}

#[test]
fn stripped_page_signature_fails_closed() {
    let (mut chain, rotation, rotation_did) = mint();
    let doc = page_two(&chain, &rotation_did);
    chain.append(doc, &rotation).unwrap();
    chain.pages[1].signature = None;
    match chain.verify() {
        Err(ContinuityError::MissingSignature(1)) => {}
        other => panic!("expected MissingSignature, got {other:?}"),
    }
}

#[test]
fn signed_genesis_is_refused() {
    let (mut chain, _rotation, _rotation_did) = mint();
    chain.pages[0].signature = Some(vec![0u8; 64]);
    match chain.verify() {
        Err(ContinuityError::GenesisSigned) => {}
        other => panic!("expected GenesisSigned, got {other:?}"),
    }
}

#[test]
fn value_round_trip_preserves_verification() {
    let (mut chain, rotation, rotation_did) = mint();
    let doc = page_two(&chain, &rotation_did);
    chain.append(doc, &rotation).unwrap();

    let reloaded = Chain::from_value(&chain.to_value()).unwrap();
    reloaded.verify().unwrap();
    assert_eq!(reloaded.pages.len(), 2);
    assert_eq!(reloaded.twin_did().unwrap(), chain.twin_did().unwrap());
}
