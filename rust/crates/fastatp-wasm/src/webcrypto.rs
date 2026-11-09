//! The WebCrypto pen: non-extractable ECDSA P-256 via `crypto.subtle`.
//!
//! `extractable: false` is the load-bearing property (HOSTED-PEERS.md): the
//! private key never exists in WASM linear memory — bytes in a JS-inspectable
//! buffer would be extractable by definition. WebCrypto returns raw 64-byte
//! R||S (IEEE P1363); low-S normalization happens here in Rust, which needs
//! no private key.

use js_sys::{Array, Reflect, Uint8Array};
use wasm_bindgen::prelude::*;
use wasm_bindgen_futures::JsFuture;
use web_sys::{CryptoKey, EcKeyGenParams, EcdsaParams};

use fastatp_core::crypto::{
    encode_multikey, normalize_low_s, verify_signature, CryptoError, Curve, PublicKey, Signer,
};

/// `globalThis.crypto` — deliberately not `window()`-based so the same bundle
/// runs in browsers, workers, and Node 22.
fn crypto() -> Result<web_sys::Crypto, JsValue> {
    Reflect::get(&js_sys::global(), &JsValue::from_str("crypto"))?
        .dyn_into::<web_sys::Crypto>()
        .map_err(|_| JsValue::from_str("globalThis.crypto is not available"))
}

pub fn random_bytes(n: usize) -> Result<Vec<u8>, JsValue> {
    let mut buffer = vec![0u8; n];
    crypto()?.get_random_values_with_u8_array(&mut buffer)?;
    Ok(buffer)
}

/// Signed and verified once when a pair is wrapped, to prove the halves match.
const PAIR_PROBE: &[u8] = b"fastatp pen pair check";

/// Mint a WebCrypto ECDSA P-256 pair whose private key is non-extractable.
pub async fn generate_key_pair() -> Result<JsValue, JsValue> {
    let subtle = crypto()?.subtle();
    let algorithm = EcKeyGenParams::new("ECDSA", "P-256");
    let usages = Array::of2(&JsValue::from_str("sign"), &JsValue::from_str("verify"));
    // NOTE: no cast to js_sys::Object — Node 22's webcrypto returns the
    // pair as a null-prototype dictionary, which fails `instanceof Object`
    JsFuture::from(subtle.generate_key_with_object(
        &algorithm, false, // extractable: false — the property everything rests on
        &usages,
    )?)
    .await
}

fn key_field(pair: &JsValue, field: &str) -> Result<CryptoKey, JsValue> {
    if pair.is_null() || pair.is_undefined() {
        return Err(JsValue::from_str(
            "expected a key pair, got null or undefined",
        ));
    }
    Reflect::get(pair, &JsValue::from_str(field))?
        .dyn_into()
        .map_err(|_| JsValue::from_str(&format!("key pair has no CryptoKey `{field}`")))
}

fn check_p256(key: &CryptoKey, expected_type: &str) -> Result<(), JsValue> {
    let algorithm = key.algorithm()?;
    let field = |name: &str| {
        Reflect::get(&algorithm, &JsValue::from_str(name))
            .ok()
            .and_then(|v| v.as_string())
            .unwrap_or_default()
    };
    if key.type_() != expected_type || field("name") != "ECDSA" || field("namedCurve") != "P-256" {
        return Err(JsValue::from_str(&format!(
            "{expected_type} key must be an ECDSA P-256 {expected_type} key, got {} {} {}",
            field("name"),
            field("namedCurve"),
            key.type_(),
        )));
    }
    Ok(())
}

/// A pen: the WebCrypto private-key handle plus its precomputed multikey.
/// Clone shares the same underlying JS key object (single-threaded wasm).
#[derive(Clone)]
pub struct WebCryptoSigner {
    private: CryptoKey,
    multikey: String,
}

impl WebCryptoSigner {
    /// Mint a fresh pen and wrap it: the same checks as `from_key_pair`.
    pub async fn generate() -> Result<Self, JsValue> {
        Self::from_key_pair(&generate_key_pair().await?).await
    }

    /// Wrap a WebCrypto key pair (a `CryptoKeyPair`, or the plain
    /// `{privateKey, publicKey}` object IndexedDB hands back) as a pen.
    ///
    /// Refuses anything that is not a non-extractable ECDSA P-256 private
    /// key with a matching public key: the private half must be a handle the
    /// page can use but never read. The match is proved by signing a fixed
    /// probe and verifying it, so a pair stitched from two different keys
    /// fails here instead of producing signatures nobody can verify.
    pub async fn from_key_pair(pair: &JsValue) -> Result<Self, JsValue> {
        let private = key_field(pair, "privateKey")?;
        let public = key_field(pair, "publicKey")?;
        check_p256(&private, "private")?;
        check_p256(&public, "public")?;
        if private.extractable() {
            return Err(JsValue::from_str(
                "pen private key is extractable; refusing it (mint pens with extractable: false)",
            ));
        }

        let subtle = crypto()?.subtle();
        let raw = JsFuture::from(subtle.export_key("raw", &public)?).await?;
        let point = Uint8Array::new(&raw).to_vec(); // SEC1 uncompressed (65 bytes)
        let verifying = p256::ecdsa::VerifyingKey::from_sec1_bytes(&point)
            .map_err(|_| JsValue::from_str("exported public key is not a valid P-256 point"))?;
        let public_key = PublicKey::P256(verifying);
        let pen = Self {
            private,
            multikey: encode_multikey(&public_key),
        };

        let probe = pen.sign_webcrypto(PAIR_PROBE).await?;
        if !verify_signature(&public_key, PAIR_PROBE, &probe) {
            return Err(JsValue::from_str(
                "privateKey and publicKey are not a pair; refusing them",
            ));
        }
        Ok(pen)
    }

    async fn sign_webcrypto(&self, message: &[u8]) -> Result<[u8; 64], JsValue> {
        let subtle = crypto()?.subtle();
        let params = EcdsaParams::new("ECDSA", &JsValue::from_str("SHA-256"));
        let data = message.to_vec();
        let signed =
            JsFuture::from(subtle.sign_with_object_and_u8_array(&params, &self.private, &data)?)
                .await?;
        let p1363 = Uint8Array::new(&signed).to_vec();
        normalize_low_s(Curve::P256, &p1363)
            .map_err(|e| JsValue::from_str(&format!("normalize: {e}")))
    }
}

impl Signer for WebCryptoSigner {
    fn sign(
        &self,
        message: &[u8],
    ) -> impl core::future::Future<Output = Result<[u8; 64], CryptoError>> + '_ {
        let message = message.to_vec();
        async move {
            self.sign_webcrypto(&message)
                .await
                .map_err(|e| CryptoError::SigningFailed(format!("{e:?}")))
        }
    }

    fn public_multikey(&self) -> String {
        self.multikey.clone()
    }

    fn curve(&self) -> Curve {
        Curve::P256
    }
}
