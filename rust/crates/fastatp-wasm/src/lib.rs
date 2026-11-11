//! FastATP browser peer bindings.
//!
//! The pen is a WebCrypto ECDSA P-256 key created with `extractable: false` —
//! it never exists in WASM linear memory in any form; signing round-trips
//! through `crypto.subtle.sign` and is low-S-normalized in Rust. Networking
//! stays OUT of this crate (core philosophy: bytes in, bytes out); the app's
//! JS composes `fetch`/WebSocket around the byte-level operations exported
//! here. The same `--target web` bundle runs under Node 22 for the e2e tests
//! (globalThis.crypto.subtle, fetch, WebSocket all present).

mod convert;
mod webcrypto;

use std::cell::RefCell;
use std::rc::Rc;

use wasm_bindgen::prelude::*;
use zeroize::Zeroize;

use fastatp_core::challenge::{challenge_message, is_valid_did_syntax};
use fastatp_core::continuity::{build_document, Chain};
use fastatp_core::crypto::{
    decode_did_key, decode_multikey, encode_did_key, Curve, PrivateKey, PublicKey, Signer as _,
};
use fastatp_core::mst::MemoryBlockStore;
use fastatp_core::repo::{ExternalClock, Repo};

use convert::{json_to_value, value_to_json};
use webcrypto::{generate_key_pair, random_bytes, WebCryptoSigner};

fn js_error(context: &str, error: impl std::fmt::Debug) -> JsError {
    JsError::new(&format!("{context}: {error:?}"))
}

/// A public key given as a `did:key` or a bare multikey.
fn parse_public_key(did_key_or_multikey: &str) -> Result<PublicKey, JsError> {
    if did_key_or_multikey.starts_with("did:key:") {
        decode_did_key(did_key_or_multikey)
    } else {
        decode_multikey(did_key_or_multikey)
    }
    .map_err(|e| js_error("key", e))
}

/// Syntax only (spec: did); whether the DID resolves is the app's business.
fn check_did_syntax(did: &str) -> Result<(), JsError> {
    if is_valid_did_syntax(did) {
        Ok(())
    } else {
        Err(JsError::new(&format!("not a valid DID: {did:?}")))
    }
}

async fn checked_pen(key_pair: &JsValue) -> Result<(WebCryptoSigner, String), JsError> {
    let pen = WebCryptoSigner::from_key_pair(key_pair)
        .await
        .map_err(|e| js_error("pen", e))?;
    let did = format!("did:key:{}", pen.public_multikey());
    Ok((pen, did))
}

fn date_now_clock() -> Result<ExternalClock<fn() -> u64>, JsError> {
    fn micros() -> u64 {
        (js_sys::Date::now() * 1000.0) as u64
    }
    let clock_id = random_bytes(2).map_err(|e| js_error("entropy", e))?;
    Ok(ExternalClock::new(
        micros,
        u16::from_be_bytes([clock_id[0], clock_id[1]]),
    ))
}

/// Everything connection zero mints client-side (HOSTED-PEERS.md): the
/// identity's name is computed, not assigned. `rotation_secret` is the paper
/// root shown ONCE — this bundle is its carrier until it is freed (the wasm
/// copy is wiped then); the JavaScript string the getter returns is the
/// app's to show and forget, and the bindings cannot wipe it.
#[wasm_bindgen]
pub struct ConnectionZeroBundle {
    twin_did: String,
    pen_did: String,
    rotation_did: String,
    chain_json: String,
    genesis_car: Vec<u8>,
    rotation_secret: String,
    peer: Option<BrowserPeer>,
}

impl Drop for ConnectionZeroBundle {
    fn drop(&mut self) {
        self.rotation_secret.zeroize();
    }
}

#[wasm_bindgen]
impl ConnectionZeroBundle {
    #[wasm_bindgen(getter)]
    pub fn twin_did(&self) -> String {
        self.twin_did.clone()
    }

    #[wasm_bindgen(getter)]
    pub fn pen_did(&self) -> String {
        self.pen_did.clone()
    }

    #[wasm_bindgen(getter)]
    pub fn rotation_did(&self) -> String {
        self.rotation_did.clone()
    }

    #[wasm_bindgen(getter)]
    pub fn chain_json(&self) -> String {
        self.chain_json.clone()
    }

    #[wasm_bindgen(getter)]
    pub fn genesis_car(&self) -> Vec<u8> {
        self.genesis_car.clone()
    }

    /// The paper rotation secret (hex). Show it once; it is never persisted
    /// and cannot be re-derived. Reading it does not clear it — clearing is
    /// the app's ceremony — but the peer keeps no copy.
    #[wasm_bindgen(getter)]
    pub fn rotation_secret(&self) -> String {
        self.rotation_secret.clone()
    }

    /// Hand over the live peer (once).
    pub fn take_peer(&mut self) -> Result<BrowserPeer, JsError> {
        self.peer
            .take()
            .ok_or_else(|| JsError::new("peer was already taken from this bundle"))
    }
}

/// The browser peer: an in-memory present-state repo whose commits are signed
/// by the non-extractable WebCrypto pen.
#[wasm_bindgen]
pub struct BrowserPeer {
    repo: Rc<RefCell<Repo<WebCryptoSigner>>>,
    pen_did: String,
}

/// The paper root: raw entropy from `crypto.getRandomValues`, built into a
/// k256 secret and shown once as hex. Returns `(rotation did:key, hex)`.
/// The raw bytes and the key are wiped before returning; the hex string is
/// the one carrier, and it goes into the bundle for the app to show.
fn mint_paper_root() -> Result<(String, String), JsError> {
    loop {
        let mut bytes = random_bytes(32).map_err(|e| js_error("entropy", e))?;
        // ~2^-128 retry probability: a 32-byte value at or past the curve order
        let Ok(key) = PrivateKey::from_secret_bytes(&bytes, Curve::K256) else {
            bytes.zeroize();
            continue;
        };
        let hex = data_encoding::HEXLOWER.encode(&bytes);
        bytes.zeroize();
        let did = encode_did_key(&key.public_key());
        drop(key); // SigningKey zeroizes on drop: the peer retains no rotation material
        return Ok((did, hex));
    }
}

/// The rotation `did:key` the paper secret (64 hex chars) derives, so a
/// recovery or typed-back screen can check what the user typed against the
/// chain's `rotationKeys` before anything is done with it. Nothing leaves the
/// page. The secret is in wasm memory only for the derivation, exactly as
/// `connection_zero` holds it when minting, and is wiped before returning.
#[wasm_bindgen]
pub fn rotation_did_from_secret(secret_hex: String) -> Result<String, JsError> {
    let key = paper_root_key(secret_hex)?;
    Ok(encode_did_key(&key.public_key())) // the key zeroizes on drop
}

/// Decode a typed paper secret into its k256 key, wiping every buffer that
/// held it. The `String` is consumed so the caller cannot keep it either.
fn paper_root_key(mut secret_hex: String) -> Result<PrivateKey, JsError> {
    let result = (|| {
        let typed = secret_hex.trim();
        let mut bytes = match typed.len() {
            64 => data_encoding::HEXLOWER_PERMISSIVE
                .decode(typed.as_bytes())
                .ok(),
            _ => None,
        }
        .ok_or_else(|| JsError::new("rotation secret must be 64 hex characters"))?;
        let key = PrivateKey::from_secret_bytes(&bytes, Curve::K256);
        bytes.zeroize();
        key.map_err(|_| JsError::new("not a valid rotation secret"))
    })();
    secret_hex.zeroize();
    result
}

/// Recovery: turn the identity's chain onto a new main pen, signed by the
/// paper secret. Returns the longer chain as JSON for the app to POST to the
/// shelf (which accepts a longer chain for the same slug, never an edit).
///
/// The new page keeps the identity (`id`), its aliases and its rotation
/// keys, and names `key_pair` as the signing key. Every check runs before
/// anything is signed: the chain must verify as given, the pen must be a
/// non-extractable P-256 pair, and the secret must be one of the head's
/// rotation keys (a wrong secret is refused with the chain untouched). The
/// secret is in wasm memory only for the signature and is wiped before
/// returning. Afterwards the app restores the repo with the new pen, pushes,
/// and the host checks that push against the new head; the old pen is out.
#[wasm_bindgen]
pub async fn rotate_pen(
    chain_json: String,
    secret_hex: String,
    key_pair: JsValue,
) -> Result<String, JsError> {
    // the secret first: paper_root_key consumes and wipes the typed string on
    // every path, and the key it returns wipes itself on drop, so whichever
    // check below fails, no copy of the secret outlives this call
    let rotation_key = paper_root_key(secret_hex)?;
    let parsed: serde_json::Value =
        serde_json::from_str(&chain_json).map_err(|e| js_error("rotate: chain", e))?;
    let mut chain =
        Chain::from_value(&json_to_value(&parsed).map_err(|e| js_error("rotate: chain", e))?)
            .map_err(|e| js_error("rotate: chain", e))?;
    chain.verify().map_err(|e| js_error("rotate: chain", e))?;
    let (pen, _) = checked_pen(&key_pair).await?; // signs only its fixed pair probe

    let head = chain.head();
    let twin = chain.twin_did().map_err(|e| js_error("rotate: chain", e))?;
    let str_list = |key: &str| -> Result<Vec<String>, JsError> {
        head.get(key)
            .and_then(fastatp_core::dagcbor::Value::as_array)
            .ok_or_else(|| JsError::new(&format!("rotate: chain head has no {key:?}")))?
            .iter()
            .map(|v| v.as_str().map(str::to_owned))
            .collect::<Option<Vec<_>>>()
            .ok_or_else(|| JsError::new(&format!("rotate: chain head {key:?} is not strings")))
    };
    let also_known_as = str_list("alsoKnownAs")?;
    let rotation_keys = str_list("rotationKeys")?;
    let page = build_document(
        &twin,
        &pen.public_multikey(),
        &also_known_as,
        &rotation_keys,
    );

    // append checks the secret against the head's rotationKeys before mutating
    let appended = chain.append(page, &rotation_key);
    drop(rotation_key); // SigningKey zeroizes on drop
    appended.map_err(|e| JsError::new(&format!("rotate: {e}")))?;
    Ok(value_to_json(&chain.to_value())
        .map_err(|e| JsError::new(&e))?
        .to_string())
}

/// Run connection zero for `domain` (twin-only genesis): mint the paper
/// rotation root, self-sign the genesis document and chain page one, and
/// create the genesis repo — all client-side, before any server is involved.
///
/// `key_pair` is the pen (from `generate_pen()`, possibly loaded back from
/// IndexedDB). Pass `undefined` to mint one here instead; that pen is then
/// reachable only through the returned peer, so an app that wants to keep
/// the user signed in mints it first and passes it in.
#[wasm_bindgen]
pub async fn connection_zero(
    domain: String,
    key_pair: Option<JsValue>,
) -> Result<ConnectionZeroBundle, JsError> {
    let signer = match key_pair.filter(|v| !v.is_undefined() && !v.is_null()) {
        Some(pair) => checked_pen(&pair).await?.0,
        None => WebCryptoSigner::generate()
            .await
            .map_err(|e| js_error("webcrypto keygen", e))?,
    };
    let pen_multikey = signer.public_multikey();
    let pen_did = format!("did:key:{pen_multikey}");

    let (rotation_did, rotation_secret) = mint_paper_root()?;

    let chain = Chain::create(
        &domain,
        &pen_multikey,
        std::slice::from_ref(&rotation_did),
        false,
    )
    .map_err(|e| js_error("genesis", e))?;
    let twin_did = chain.twin_did().map_err(|e| js_error("genesis", e))?;
    let chain_json = value_to_json(&chain.to_value())
        .map_err(|e| JsError::new(&e))?
        .to_string();

    let repo = Repo::create(
        twin_did.clone(),
        signer,
        Box::new(MemoryBlockStore::new()),
        Box::new(date_now_clock()?),
    )
    .await
    .map_err(|e| js_error("genesis repo", e))?;
    let genesis_car = repo.export_car().map_err(|e| js_error("genesis repo", e))?;

    Ok(ConnectionZeroBundle {
        twin_did,
        pen_did: pen_did.clone(),
        rotation_did,
        chain_json,
        genesis_car,
        rotation_secret,
        peer: Some(BrowserPeer {
            repo: Rc::new(RefCell::new(repo)),
            pen_did,
        }),
    })
}

#[wasm_bindgen]
impl BrowserPeer {
    /// A fresh, empty repo for `did`, signed by the pen `key_pair`.
    ///
    /// Any DID works: an identity's own DID, or a pen's own `did:key` for a
    /// device repo. The bindings do not decide which pen signs which repo;
    /// the app does, and whoever verifies the repo checks the signer is the
    /// right one. Refuses a pen that is extractable or not a matching
    /// ECDSA P-256 pair.
    pub async fn create(did: String, key_pair: JsValue) -> Result<BrowserPeer, JsError> {
        check_did_syntax(&did)?;
        let (signer, pen_did) = checked_pen(&key_pair).await?;
        let repo = Repo::create(
            did,
            signer,
            Box::new(MemoryBlockStore::new()),
            Box::new(date_now_clock()?),
        )
        .await
        .map_err(|e| js_error("create repo", e))?;
        Ok(BrowserPeer {
            repo: Rc::new(RefCell::new(repo)),
            pen_did,
        })
    }

    /// Rebuild a peer from a repo CAR (stored locally or fetched from its
    /// host) and the pen that will sign its next commits.
    ///
    /// `verify` (a `did:key` or multikey) checks the stored head commit's
    /// signature; give the key that signed it, which after a recovery is the
    /// old pen, not `key_pair`. Without it, only content addressing is
    /// checked. The next commit sorts after the restored one even if this
    /// device's clock is behind.
    pub async fn restore(
        car: Vec<u8>,
        key_pair: JsValue,
        verify: Option<String>,
    ) -> Result<BrowserPeer, JsError> {
        let verify_key = verify.as_deref().map(parse_public_key).transpose()?;
        let (signer, pen_did) = checked_pen(&key_pair).await?;
        let repo = Repo::from_car(
            &car,
            signer,
            Box::new(date_now_clock()?),
            verify_key.as_ref(),
        )
        .map_err(|e| js_error("restore", e))?;
        Ok(BrowserPeer {
            repo: Rc::new(RefCell::new(repo)),
            pen_did,
        })
    }

    /// The repo's DID (the twin for a connection-zero peer).
    pub fn did(&self) -> String {
        self.repo.borrow().did().to_string()
    }

    pub fn pen_did(&self) -> String {
        self.pen_did.clone()
    }

    pub fn rev(&self) -> String {
        self.repo.borrow().rev().to_string()
    }

    /// Commit a record (JSON) and return its proof slice — the unit that
    /// rides push intake.
    ///
    /// The RefCell borrow is held across the WebCrypto await deliberately:
    /// wasm is single-threaded, and a reentrant call mid-commit would be a
    /// genuine logic error — panicking loudly beats interleaved commits.
    #[allow(clippy::await_holding_refcell_ref)]
    pub async fn put_record(
        &self,
        collection: String,
        rkey: String,
        record_json: String,
    ) -> Result<Vec<u8>, JsError> {
        let parsed: serde_json::Value =
            serde_json::from_str(&record_json).map_err(|e| js_error("record json", e))?;
        let value = json_to_value(&parsed).map_err(|e| JsError::new(&e))?;
        let repo = self.repo.clone();
        let mut repo = repo.borrow_mut();
        repo.put_record(&collection, &rkey, value)
            .await
            .map_err(|e| js_error("put_record", e))?;
        repo.prove(&collection, &rkey)
            .map_err(|e| js_error("prove", e))
    }

    #[allow(clippy::await_holding_refcell_ref)]
    pub async fn delete_record(&self, collection: String, rkey: String) -> Result<(), JsError> {
        let repo = self.repo.clone();
        let mut repo = repo.borrow_mut();
        repo.delete_record(&collection, &rkey)
            .await
            .map_err(|e| js_error("delete_record", e))?;
        Ok(())
    }

    /// Current-state CAR of the whole repo (checkpoint / connection-zero POST).
    pub fn export_car(&self) -> Result<Vec<u8>, JsError> {
        self.repo
            .borrow()
            .export_car()
            .map_err(|e| js_error("export_car", e))
    }
}

/// Mint a new pen: a WebCrypto `CryptoKeyPair` whose private key is ECDSA
/// P-256 and non-extractable. The page can store it in IndexedDB (structured
/// clone keeps it non-extractable) and hand it back after a reload; its bytes
/// never enter wasm memory or JavaScript. The pair passes the same check as
/// every pen handed in before it is returned.
#[wasm_bindgen]
pub async fn generate_pen() -> Result<JsValue, JsError> {
    let pair = generate_key_pair()
        .await
        .map_err(|e| js_error("webcrypto keygen", e))?;
    // check what the browser actually returned before the app can store it
    checked_pen(&pair).await?;
    Ok(pair)
}

/// The `did:key` of a pen, for showing it during enrollment or checking a
/// stored pen is the right one. Refuses anything that is not a matching,
/// non-extractable ECDSA P-256 pair, as every entry point taking a pen does.
#[wasm_bindgen]
pub async fn pen_did(key_pair: JsValue) -> Result<String, JsError> {
    Ok(checked_pen(&key_pair).await?.1)
}

/// The knock: sign a host-issued nonce naming the repo this pen wants —
/// its own (owner pull) or one it is admitted to by capability record (the
/// browser gate). Which of those it is, the host decides; the pen just
/// knocks. Takes only the pen and the DID of the repo being asked for, so
/// no repo needs to be loaded. Signs the fixed, labelled message
/// `fastatp-challenge-v1\n<repo DID>\n<nonce>` and nothing else: a
/// malformed nonce or DID is refused, so a pen can never be handed commit
/// bytes dressed up as a challenge. Returns the signature as unpadded
/// base64url (compact 64-byte, low-S), ready for the `sig` parameter.
#[wasm_bindgen]
pub async fn sign_challenge(
    key_pair: JsValue,
    nonce: String,
    repo_did: String,
) -> Result<String, JsError> {
    let message = challenge_message(&repo_did, &nonce)
        .map_err(|e| JsError::new(&format!("challenge: {e}")))?;
    let (pen, _) = checked_pen(&key_pair).await?;
    let signature = pen
        .sign(&message)
        .await
        .map_err(|e| js_error("sign_challenge", e))?;
    Ok(data_encoding::BASE64URL_NOPAD.encode(&signature))
}

/// Verify a record proof against a did:key or multikey; returns the verified
/// record and provenance as JSON. Any failure is a thrown error — fail closed.
#[wasm_bindgen]
pub fn verify_proof(
    proof: &[u8],
    did_key_or_multikey: &str,
    path: Option<String>,
) -> Result<String, JsError> {
    let key = parse_public_key(did_key_or_multikey)?;
    let verified = fastatp_core::repo::verify_proof(proof, &key, path.as_deref())
        .map_err(|e| js_error("refused", e))?;
    let report = serde_json::json!({
        "did": verified.did,
        "rev": verified.rev,
        "collection": verified.collection,
        "rkey": verified.rkey,
        "record_cid": verified.record_cid.to_string(),
        "record": value_to_json(&verified.record).map_err(|e| JsError::new(&e))?,
    });
    Ok(report.to_string())
}

/// The bundle's own version (stamped at build; the wheel's VERSION file and
/// the Python package must agree — the mount helper enforces it).
#[wasm_bindgen]
pub fn bundle_version() -> String {
    env!("CARGO_PKG_VERSION").to_string()
}
