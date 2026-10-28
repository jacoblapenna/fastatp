//! Key handling, Multikey/did:key encoding, and low-S ECDSA signing.
//!
//! Interop-critical rules (mirrored from `fastatp/crypto.py`):
//!
//! - Curves: secp256k1 ("k256") and NIST P-256 ("p256").
//! - Signatures: ECDSA over SHA-256, low-S normalized, compact 64-byte R||S.
//!   Verification rejects high-S and DER-encoded signatures.
//! - Public keys travel as Multikey / did:key multibase (base58btc, "z"
//!   prefix) with the per-curve multicodec prefix over the compressed point.
//!
//! All primitives come from the RustCrypto crates; nothing is hand-rolled.
//! Signing goes through the [`Signer`] trait so the browser bindings can
//! substitute WebCrypto (non-extractable pens): the trait is
//! async-capable, and [`normalize_low_s`] is exposed on its own because
//! WebCrypto's raw P1363 output is not low-S guaranteed.

use core::future::Future;

use k256::ecdsa::signature::hazmat::PrehashVerifier;
use k256::ecdsa::signature::{Signer as _, Verifier as _};
use sha2::{Digest, Sha256};
use thiserror::Error;

#[derive(Debug, Error)]
pub enum CryptoError {
    #[error("unsupported curve {0:?}")]
    UnsupportedCurve(String),
    #[error("private key must be 32 bytes")]
    BadPrivateKeyLength,
    #[error("invalid private key")]
    BadPrivateKey,
    #[error("invalid multikey: {0}")]
    BadMultikey(String),
    #[error("invalid public key point")]
    BadPoint,
    #[error("invalid signature")]
    BadSignature,
    #[error("signing failed: {0}")]
    SigningFailed(String),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Curve {
    K256,
    P256,
}

impl Curve {
    /// Normalize a curve name to a [`Curve`] (accepts the same aliases as the
    /// Python `curve_name`).
    pub fn parse(name: &str) -> Result<Self, CryptoError> {
        match name.to_ascii_lowercase().as_str() {
            "k256" | "secp256k1" | "es256k" => Ok(Self::K256),
            "p256" | "secp256r1" | "prime256v1" | "es256" => Ok(Self::P256),
            other => Err(CryptoError::UnsupportedCurve(other.to_string())),
        }
    }

    pub fn name(self) -> &'static str {
        match self {
            Self::K256 => "k256",
            Self::P256 => "p256",
        }
    }

    /// The multicodec prefix (unsigned-varint bytes) for this curve's
    /// compressed public keys.
    fn multicodec_prefix(self) -> [u8; 2] {
        match self {
            Self::K256 => [0xe7, 0x01], // secp256k1-pub (0xe7)
            Self::P256 => [0x80, 0x24], // p256-pub (0x1200)
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PublicKey {
    K256(k256::ecdsa::VerifyingKey),
    P256(p256::ecdsa::VerifyingKey),
}

impl PublicKey {
    pub fn curve(&self) -> Curve {
        match self {
            Self::K256(_) => Curve::K256,
            Self::P256(_) => Curve::P256,
        }
    }

    /// SEC1 compressed point bytes (33 bytes).
    pub fn compressed_point(&self) -> Vec<u8> {
        match self {
            Self::K256(vk) => vk.to_encoded_point(true).as_bytes().to_vec(),
            Self::P256(vk) => vk.to_encoded_point(true).as_bytes().to_vec(),
        }
    }

    fn from_sec1(curve: Curve, point: &[u8]) -> Result<Self, CryptoError> {
        match curve {
            Curve::K256 => k256::ecdsa::VerifyingKey::from_sec1_bytes(point)
                .map(Self::K256)
                .map_err(|_| CryptoError::BadPoint),
            Curve::P256 => p256::ecdsa::VerifyingKey::from_sec1_bytes(point)
                .map(Self::P256)
                .map_err(|_| CryptoError::BadPoint),
        }
    }
}

#[derive(Debug, Clone)]
pub enum PrivateKey {
    K256(k256::ecdsa::SigningKey),
    P256(p256::ecdsa::SigningKey),
}

impl PrivateKey {
    pub fn curve(&self) -> Curve {
        match self {
            Self::K256(_) => Curve::K256,
            Self::P256(_) => Curve::P256,
        }
    }

    pub fn public_key(&self) -> PublicKey {
        match self {
            Self::K256(sk) => PublicKey::K256(*sk.verifying_key()),
            Self::P256(sk) => PublicKey::P256(*sk.verifying_key()),
        }
    }

    /// Load a raw 32-byte private scalar (mirrors `load_private_key_bytes`).
    pub fn from_secret_bytes(data: &[u8], curve: Curve) -> Result<Self, CryptoError> {
        if data.len() != 32 {
            return Err(CryptoError::BadPrivateKeyLength);
        }
        match curve {
            Curve::K256 => k256::ecdsa::SigningKey::from_slice(data)
                .map(Self::K256)
                .map_err(|_| CryptoError::BadPrivateKey),
            Curve::P256 => p256::ecdsa::SigningKey::from_slice(data)
                .map(Self::P256)
                .map_err(|_| CryptoError::BadPrivateKey),
        }
    }

    /// Generate a new private key from OS randomness (native builds; browser
    /// pens are minted by WebCrypto and never exist in this address space).
    #[cfg(feature = "rand")]
    pub fn generate(curve: Curve) -> Self {
        match curve {
            Curve::K256 => Self::K256(k256::ecdsa::SigningKey::random(&mut rand_core::OsRng)),
            Curve::P256 => Self::P256(p256::ecdsa::SigningKey::random(&mut rand_core::OsRng)),
        }
    }
}

// --- Multikey / did:key -------------------------------------------------------

/// Multibase (base58btc) multicodec-prefixed compressed public key.
pub fn encode_multikey(public_key: &PublicKey) -> String {
    let mut data = public_key.curve().multicodec_prefix().to_vec();
    data.extend_from_slice(&public_key.compressed_point());
    format!("z{}", bs58::encode(data).into_string())
}

/// Decode a Multikey multibase string to a public key.
pub fn decode_multikey(multikey: &str) -> Result<PublicKey, CryptoError> {
    let rest = multikey
        .strip_prefix('z')
        .ok_or_else(|| CryptoError::BadMultikey("must be base58btc ('z' prefix)".into()))?;
    let data = bs58::decode(rest)
        .into_vec()
        .map_err(|e| CryptoError::BadMultikey(e.to_string()))?;
    for curve in [Curve::K256, Curve::P256] {
        let prefix = curve.multicodec_prefix();
        if let Some(point) = data.strip_prefix(prefix.as_slice()) {
            return PublicKey::from_sec1(curve, point);
        }
    }
    Err(CryptoError::BadMultikey(
        "unsupported multicodec prefix".into(),
    ))
}

pub fn encode_did_key(public_key: &PublicKey) -> String {
    format!("did:key:{}", encode_multikey(public_key))
}

pub fn decode_did_key(did_key: &str) -> Result<PublicKey, CryptoError> {
    let multikey = did_key
        .strip_prefix("did:key:")
        .ok_or_else(|| CryptoError::BadMultikey("not a did:key".into()))?;
    decode_multikey(multikey)
}

// --- compact low-S ECDSA ------------------------------------------------------

/// ECDSA-SHA256 over `message`, low-S normalized, compact 64-byte R||S.
/// (RFC 6979 deterministic nonces — interoperable with the Python side's
/// randomized nonces; both verify identically.)
pub fn sign_message(private_key: &PrivateKey, message: &[u8]) -> [u8; 64] {
    match private_key {
        PrivateKey::K256(sk) => {
            let sig: k256::ecdsa::Signature = sk.sign(message);
            let sig = sig.normalize_s().unwrap_or(sig);
            sig.to_bytes().into()
        }
        PrivateKey::P256(sk) => {
            let sig: p256::ecdsa::Signature = sk.sign(message);
            let sig = sig.normalize_s().unwrap_or(sig);
            sig.to_bytes().into()
        }
    }
}

/// Verify a compact 64-byte signature; rejects high-S and non-compact forms
/// (a DER signature is never 64 bytes of valid R||S scalars).
pub fn verify_signature(public_key: &PublicKey, message: &[u8], signature: &[u8]) -> bool {
    if signature.len() != 64 {
        return false;
    }
    match public_key {
        PublicKey::K256(vk) => {
            let Ok(sig) = k256::ecdsa::Signature::from_slice(signature) else {
                return false; // zero or non-canonical r/s fails closed
            };
            if sig.normalize_s().is_some() {
                return false; // high-S MUST be rejected
            }
            vk.verify(message, &sig).is_ok()
        }
        PublicKey::P256(vk) => {
            let Ok(sig) = p256::ecdsa::Signature::from_slice(signature) else {
                return false;
            };
            if sig.normalize_s().is_some() {
                return false;
            }
            vk.verify(message, &sig).is_ok()
        }
    }
}

/// Verify a compact signature over an externally computed SHA-256 digest.
/// Same rejection rules as [`verify_signature`].
pub fn verify_prehashed(public_key: &PublicKey, digest: &[u8; 32], signature: &[u8]) -> bool {
    if signature.len() != 64 {
        return false;
    }
    match public_key {
        PublicKey::K256(vk) => {
            let Ok(sig) = k256::ecdsa::Signature::from_slice(signature) else {
                return false;
            };
            if sig.normalize_s().is_some() {
                return false;
            }
            vk.verify_prehash(digest, &sig).is_ok()
        }
        PublicKey::P256(vk) => {
            let Ok(sig) = p256::ecdsa::Signature::from_slice(signature) else {
                return false;
            };
            if sig.normalize_s().is_some() {
                return false;
            }
            vk.verify_prehash(digest, &sig).is_ok()
        }
    }
}

/// Low-S normalize a compact 64-byte R||S signature (`s' = n - s` when
/// `s > n/2`). WebCrypto returns raw P1363 with no low-S guarantee; the
/// browser signer runs its output through this — it needs no private key.
pub fn normalize_low_s(curve: Curve, signature: &[u8]) -> Result<[u8; 64], CryptoError> {
    if signature.len() != 64 {
        return Err(CryptoError::BadSignature);
    }
    match curve {
        Curve::K256 => {
            let sig = k256::ecdsa::Signature::from_slice(signature)
                .map_err(|_| CryptoError::BadSignature)?;
            Ok(sig.normalize_s().unwrap_or(sig).to_bytes().into())
        }
        Curve::P256 => {
            let sig = p256::ecdsa::Signature::from_slice(signature)
                .map_err(|_| CryptoError::BadSignature)?;
            Ok(sig.normalize_s().unwrap_or(sig).to_bytes().into())
        }
    }
}

/// SHA-256 helper shared by CID hashing and prehashed verification.
pub fn sha256(data: &[u8]) -> [u8; 32] {
    Sha256::digest(data).into()
}

// --- the signer seam -------------------------------------------------

/// Where the pen lives is a deployment property, not an architecture fork:
/// natively a [`SoftwareSigner`] holds the key in memory; in the browser a
/// WebCrypto signer awaits `crypto.subtle.sign` on a non-extractable key and
/// low-S-normalizes the result. Async-capable for exactly that reason.
///
/// (RPITIT rather than a boxed future: signers are used generically, not as
/// `dyn Signer`; the wasm bindings wrap their own concrete signer type.)
pub trait Signer {
    /// Sign `message`, returning a low-S compact 64-byte R||S signature.
    fn sign(&self, message: &[u8]) -> impl Future<Output = Result<[u8; 64], CryptoError>> + '_;

    /// The Multikey of the verifying half (what a DID document carries).
    fn public_multikey(&self) -> String;

    /// The signer's curve.
    fn curve(&self) -> Curve;
}

/// An in-memory signer for native peers and tests.
#[derive(Debug, Clone)]
pub struct SoftwareSigner {
    key: PrivateKey,
}

impl SoftwareSigner {
    pub fn new(key: PrivateKey) -> Self {
        Self { key }
    }

    pub fn public_key(&self) -> PublicKey {
        self.key.public_key()
    }
}

impl Signer for SoftwareSigner {
    fn sign(&self, message: &[u8]) -> impl Future<Output = Result<[u8; 64], CryptoError>> + '_ {
        core::future::ready(Ok(sign_message(&self.key, message)))
    }

    fn public_multikey(&self) -> String {
        encode_multikey(&self.key.public_key())
    }

    fn curve(&self) -> Curve {
        self.key.curve()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn keys() -> Vec<PrivateKey> {
        vec![
            PrivateKey::from_secret_bytes(&[7u8; 32], Curve::K256).unwrap(),
            PrivateKey::from_secret_bytes(&[7u8; 32], Curve::P256).unwrap(),
        ]
    }

    #[test]
    fn sign_verify_round_trip_and_high_s_rejected() {
        for key in keys() {
            let public = key.public_key();
            let sig = sign_message(&key, b"hello world");
            assert!(verify_signature(&public, b"hello world", &sig));
            assert!(!verify_signature(&public, b"tampered", &sig));

            // the complement (high-S) form must be rejected
            let high_s = complement_s(key.curve(), &sig);
            assert!(!verify_signature(&public, b"hello world", &high_s));
            // and normalize_low_s maps it back to the accepted form
            let renormalized = normalize_low_s(key.curve(), &high_s).unwrap();
            assert_eq!(renormalized, sig);
        }
    }

    fn complement_s(curve: Curve, sig: &[u8; 64]) -> [u8; 64] {
        // s' = n - s computed through the scalar arithmetic of each curve
        let mut out = *sig;
        let mut s_bytes = [0u8; 32];
        s_bytes.copy_from_slice(&sig[32..]);
        match curve {
            Curve::K256 => {
                use k256::elliptic_curve::PrimeField;
                let s = k256::Scalar::from_repr(s_bytes.into()).unwrap();
                out[32..].copy_from_slice(&(-s).to_repr());
            }
            Curve::P256 => {
                use p256::elliptic_curve::PrimeField;
                let s = p256::Scalar::from_repr(s_bytes.into()).unwrap();
                out[32..].copy_from_slice(&(-s).to_repr());
            }
        }
        out
    }

    #[test]
    fn multikey_round_trip() {
        for key in keys() {
            let public = key.public_key();
            let multikey = encode_multikey(&public);
            assert!(multikey.starts_with('z'));
            let decoded = decode_multikey(&multikey).unwrap();
            assert_eq!(encode_multikey(&decoded), multikey);
            let did = encode_did_key(&public);
            assert!(did.starts_with("did:key:z"));
            assert_eq!(encode_multikey(&decode_did_key(&did).unwrap()), multikey);
        }
    }

    #[test]
    fn wrong_length_and_garbage_signatures_fail_closed() {
        let key = &keys()[0];
        let public = key.public_key();
        assert!(!verify_signature(&public, b"m", &[0u8; 63]));
        assert!(!verify_signature(&public, b"m", &[0u8; 65]));
        assert!(!verify_signature(&public, b"m", &[0u8; 64])); // zero scalars
        assert!(!verify_signature(&public, b"m", &[0xFFu8; 64])); // non-canonical
    }

    #[test]
    fn software_signer_signs_low_s() {
        for key in keys() {
            let signer = SoftwareSigner::new(key);
            let sig = pollster::block_on(signer.sign(b"the pen writes")).unwrap();
            let public = decode_multikey(&signer.public_multikey()).unwrap();
            assert!(verify_signature(&public, b"the pen writes", &sig));
        }
    }
}
