//! Crypto + TID conformance driven by the same interop vectors the Python
//! side passes (`reference/atproto-interop-tests`): signature fixtures
//! (valid low-S accepted; high-S and DER rejected), W3C did:key vectors,
//! and the TID syntax vectors.

use std::path::PathBuf;

use fastatp_core::crypto::{decode_did_key, encode_did_key, verify_signature, Curve, PrivateKey};
use fastatp_core::tid::decode_tid;

fn interop_dir() -> Option<PathBuf> {
    let dir =
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../reference/atproto-interop-tests");
    if dir.is_dir() {
        Some(dir)
    } else {
        eprintln!("skipping: reference/atproto-interop-tests not present");
        None
    }
}

/// Minimal base64 decode (standard alphabet, padding optional) — the
/// fixtures are tiny and this keeps base64 out of the dependency tree.
fn b64(s: &str) -> Vec<u8> {
    const ALPHABET: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut buffer: u32 = 0;
    let mut bits = 0u32;
    let mut out = Vec::new();
    for &c in s.as_bytes() {
        if c == b'=' {
            break;
        }
        let value = ALPHABET
            .iter()
            .position(|&a| a == c)
            .unwrap_or_else(|| panic!("bad base64 char {c:?}")) as u32;
        buffer = (buffer << 6) | value;
        bits += 6;
        if bits >= 8 {
            bits -= 8;
            out.push((buffer >> bits) as u8);
        }
    }
    out
}

#[test]
fn signature_fixtures() {
    let Some(dir) = interop_dir() else { return };
    let raw = std::fs::read_to_string(dir.join("crypto/signature-fixtures.json")).unwrap();
    let fixtures: Vec<serde_json::Value> = serde_json::from_str(&raw).unwrap();
    assert!(fixtures.len() >= 6);
    let mut accepted = 0;
    let mut rejected = 0;
    for fx in &fixtures {
        let public = decode_did_key(fx["publicKeyDid"].as_str().unwrap()).unwrap();
        let message = b64(fx["messageBase64"].as_str().unwrap());
        let signature = b64(fx["signatureBase64"].as_str().unwrap());
        let expected = fx["validSignature"].as_bool().unwrap();
        assert_eq!(
            verify_signature(&public, &message, &signature),
            expected,
            "{}",
            fx["comment"]
        );
        if expected {
            accepted += 1;
        } else {
            rejected += 1;
        }
    }
    // the fixture set exercises both outcomes (incl. high-S and DER rejects)
    assert!(accepted >= 2, "expected valid fixtures on both curves");
    assert!(rejected >= 2, "expected high-S/DER rejection fixtures");
}

#[test]
fn didkey_vectors() {
    let Some(dir) = interop_dir() else { return };
    for (file, curve) in [
        ("crypto/w3c_didkey_K256.json", Curve::K256),
        ("crypto/w3c_didkey_P256.json", Curve::P256),
    ] {
        let raw = std::fs::read_to_string(dir.join(file)).unwrap();
        let fixtures: Vec<serde_json::Value> = serde_json::from_str(&raw).unwrap();
        assert!(!fixtures.is_empty());
        for fx in &fixtures {
            let secret = if let Some(hex_bytes) = fx["privateKeyBytesHex"].as_str() {
                hex::decode(hex_bytes).unwrap()
            } else {
                bs58::decode(fx["privateKeyBytesBase58"].as_str().unwrap())
                    .into_vec()
                    .unwrap()
            };
            let key = PrivateKey::from_secret_bytes(&secret, curve).unwrap();
            assert_eq!(
                encode_did_key(&key.public_key()),
                fx["publicDidKey"].as_str().unwrap(),
                "{file}"
            );
        }
    }
}

fn syntax_lines(dir: &std::path::Path, name: &str) -> Vec<String> {
    std::fs::read_to_string(dir.join("syntax").join(name))
        .unwrap()
        .lines()
        .map(str::trim)
        .filter(|l| !l.is_empty() && !l.starts_with('#'))
        .map(str::to_string)
        .collect()
}

#[test]
fn tid_syntax_vectors() {
    let Some(dir) = interop_dir() else { return };
    let valid = syntax_lines(&dir, "tid_syntax_valid.txt");
    let invalid = syntax_lines(&dir, "tid_syntax_invalid.txt");
    assert!(!valid.is_empty() && !invalid.is_empty());
    for tid in &valid {
        assert!(decode_tid(tid).is_ok(), "must accept {tid:?}");
    }
    for tid in &invalid {
        assert!(decode_tid(tid).is_err(), "must reject {tid:?}");
    }
}
