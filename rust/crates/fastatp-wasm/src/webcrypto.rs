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
    encode_multikey, normalize_low_s, CryptoError, Curve, PublicKey, Signer,
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

/// A pen: the WebCrypto private-key handle plus its precomputed multikey.
/// Clone shares the same underlying JS key object (single-threaded wasm).
#[derive(Clone)]
pub struct WebCryptoSigner {
    private: CryptoKey,
    multikey: String,
}

impl WebCryptoSigner {
    pub async fn generate() -> Result<Self, JsValue> {
        let subtle = crypto()?.subtle();
        let algorithm = EcKeyGenParams::new("ECDSA", "P-256");
        let usages = Array::of2(&JsValue::from_str("sign"), &JsValue::from_str("verify"));
        // NOTE: no cast to js_sys::Object — Node 22's webcrypto returns the
        // pair as a null-prototype dictionary, which fails `instanceof Object`
        let pair: JsValue = JsFuture::from(subtle.generate_key_with_object(
            &algorithm, false, // extractable: false — the property everything rests on
            &usages,
        )?)
        .await?;
        let private: CryptoKey =
            Reflect::get(&pair, &JsValue::from_str("privateKey"))?.dyn_into()?;
        let public: CryptoKey = Reflect::get(&pair, &JsValue::from_str("publicKey"))?.dyn_into()?;
        if private.extractable() {
            return Err(JsValue::from_str(
                "pen private key came back extractable; refusing to use it",
            ));
        }

        let raw = JsFuture::from(subtle.export_key("raw", &public)?).await?;
        let point = Uint8Array::new(&raw).to_vec(); // SEC1 uncompressed (65 bytes)
        let verifying = p256::ecdsa::VerifyingKey::from_sec1_bytes(&point)
            .map_err(|_| JsValue::from_str("exported public key is not a valid P-256 point"))?;
        let multikey = encode_multikey(&PublicKey::P256(verifying));
        Ok(Self { private, multikey })
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
