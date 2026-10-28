//! Self-certifying did:web continuity — the twin/chain convention (the
//! twin-only shelved shape), byte-exact with the Python
//! implementation: the slug hashes the canonical DAG-CBOR of a placeholder
//! genesis, so both implementations must build the *identical* document
//! structure. This module is what lets a browser peer run connection zero
//! client-side (mint its identity's name locally) and POST the result to any
//! shelf intake.
//!
//! No OS entropy or clock lives here: key material is always passed in, so
//! the wasm32 no-default-features build carries the whole module.

use data_encoding::BASE32;
use thiserror::Error;

use crate::crypto::{
    decode_did_key, decode_multikey, encode_did_key, encode_multikey, sign_message,
    verify_signature, CryptoError, PrivateKey,
};
use crate::dagcbor::{self, DagCborError, Value};

pub const DID_CONTEXT: &str = "https://www.w3.org/ns/did/v1";
pub const MULTIKEY_CONTEXT: &str = "https://w3id.org/security/multikey/v1";

/// The fixed base32-shaped token hashed in the slug position ("scid" * 8).
pub const SCID_PLACEHOLDER: &str = "scidscidscidscidscidscidscidscid";

const SLUG_HASH_BYTES: usize = 20; // 160 bits -> exactly 32 base32 chars

#[derive(Debug, Error)]
pub enum ContinuityError {
    #[error("a continuity genesis requires at least one rotation key")]
    NoRotationKeys,
    #[error("continuity chain has no pages")]
    Empty,
    #[error("genesis page must not carry a rotation signature")]
    GenesisSigned,
    #[error("twin id {0:?} is not a did:web with a domain and slug segment")]
    BadTwinId(String),
    #[error("twin id {twin:?} is not a path segment under its pretty DID {pretty:?}")]
    NotUnderPretty { twin: String, pretty: String },
    #[error(
        "slug {found:?} does not match the genesis hash {expected:?} (forged or tampered genesis)"
    )]
    SlugMismatch { found: String, expected: String },
    #[error("chain page {0} changed the twin id")]
    TwinIdChanged(usize),
    #[error("chain page {0} is missing its rotation signature")]
    MissingSignature(usize),
    #[error(
        "chain page {0} is not rotation-signed by its predecessor (broken chain / unauthorized page)"
    )]
    UnauthorizedPage(usize),
    #[error("the supplied rotation key is not a rotationKeys entry of the current head")]
    WrongPen,
    #[error("a chain page must keep the twin id {expected:?}, got {got:?}")]
    NotTwin { expected: String, got: String },
    #[error("malformed document: {0}")]
    BadDocument(String),
    #[error("crypto: {0}")]
    Crypto(#[from] CryptoError),
    #[error("encode: {0}")]
    Encode(#[from] DagCborError),
}

/// The exact document structure `fastatp.did.document.build_did_document`
/// emits via `to_json_dict()` — every field present, empty lists included,
/// because the slug hashes these bytes.
pub fn build_document(
    did: &str,
    signing_multikey: &str,
    also_known_as: &[String],
    rotation_keys: &[String],
) -> Value {
    Value::map(vec![
        (
            "@context",
            Value::Array(vec![
                Value::from(DID_CONTEXT),
                Value::from(MULTIKEY_CONTEXT),
            ]),
        ),
        ("id", Value::from(did)),
        (
            "alsoKnownAs",
            Value::Array(
                also_known_as
                    .iter()
                    .map(|s| Value::from(s.as_str()))
                    .collect(),
            ),
        ),
        (
            "verificationMethod",
            Value::Array(vec![Value::map(vec![
                ("id", Value::from(format!("{did}#atproto"))),
                ("type", Value::from("Multikey")),
                ("controller", Value::from(did)),
                ("publicKeyMultibase", Value::from(signing_multikey)),
            ])]),
        ),
        (
            "rotationKeys",
            Value::Array(
                rotation_keys
                    .iter()
                    .map(|s| Value::from(s.as_str()))
                    .collect(),
            ),
        ),
        ("service", Value::Array(vec![])),
    ])
}

/// Canonical DAG-CBOR bytes of a document (the rotation-signing payload and
/// the slug's hash input).
pub fn canonical_document_bytes(document: &Value) -> Result<Vec<u8>, DagCborError> {
    dagcbor::encode(document)
}

/// base32-lower(sha256(canonical DAG-CBOR of the placeholder genesis)[:20]).
pub fn derive_slug(placeholder_doc: &Value) -> Result<String, DagCborError> {
    let digest = crate::crypto::sha256(&canonical_document_bytes(placeholder_doc)?);
    Ok(BASE32.encode(&digest[..SLUG_HASH_BYTES]).to_lowercase())
}

/// The self-certifying genesis for `domain`; returns `(twin_did, genesis)`.
///
/// `bind_pretty = false` is the twin-only shelved shape — the one
/// a browser peer mints at connection zero.
pub fn build_genesis(
    domain: &str,
    signing_multikey: &str,
    rotation_keys: &[String],
    bind_pretty: bool,
) -> Result<(String, Value), ContinuityError> {
    if rotation_keys.is_empty() {
        return Err(ContinuityError::NoRotationKeys);
    }
    let also_known_as: Vec<String> = if bind_pretty {
        vec![format!("did:web:{domain}")]
    } else {
        Vec::new()
    };
    let placeholder = build_document(
        &format!("did:web:{domain}:{SCID_PLACEHOLDER}"),
        signing_multikey,
        &also_known_as,
        rotation_keys,
    );
    let slug = derive_slug(&placeholder)?;
    let twin = format!("did:web:{domain}:{slug}");
    let genesis = build_document(&twin, signing_multikey, &also_known_as, rotation_keys);
    Ok((twin, genesis))
}

/// One document the identity has had; `signature` is `None` only for the
/// genesis (self-certified by the slug commitment).
#[derive(Clone, Debug)]
pub struct ChainPage {
    pub document: Value,
    pub signature: Option<Vec<u8>>,
}

/// An append-only, rotation-signed log of an identity's DID documents.
#[derive(Clone, Debug)]
pub struct Chain {
    pub pages: Vec<ChainPage>,
}

fn doc_str(document: &Value, key: &str) -> Result<String, ContinuityError> {
    document
        .get(key)
        .and_then(Value::as_str)
        .map(str::to_owned)
        .ok_or_else(|| ContinuityError::BadDocument(format!("missing string field {key:?}")))
}

fn doc_str_list(document: &Value, key: &str) -> Result<Vec<String>, ContinuityError> {
    document
        .get(key)
        .and_then(Value::as_array)
        .ok_or_else(|| ContinuityError::BadDocument(format!("missing list field {key:?}")))?
        .iter()
        .map(|v| {
            v.as_str()
                .map(str::to_owned)
                .ok_or_else(|| ContinuityError::BadDocument(format!("non-string in {key:?}")))
        })
        .collect()
}

fn doc_signing_multikey(document: &Value) -> Result<String, ContinuityError> {
    let methods = document
        .get("verificationMethod")
        .and_then(Value::as_array)
        .ok_or_else(|| ContinuityError::BadDocument("missing verificationMethod".into()))?;
    let first = methods
        .first()
        .ok_or_else(|| ContinuityError::BadDocument("no verificationMethod entries".into()))?;
    doc_str(first, "publicKeyMultibase")
}

impl Chain {
    pub fn create(
        domain: &str,
        signing_multikey: &str,
        rotation_keys: &[String],
        bind_pretty: bool,
    ) -> Result<Chain, ContinuityError> {
        let (_twin, genesis) = build_genesis(domain, signing_multikey, rotation_keys, bind_pretty)?;
        Ok(Chain {
            pages: vec![ChainPage {
                document: genesis,
                signature: None,
            }],
        })
    }

    pub fn genesis(&self) -> &Value {
        &self.pages[0].document
    }

    pub fn head(&self) -> &Value {
        &self.pages[self.pages.len() - 1].document
    }

    pub fn twin_did(&self) -> Result<String, ContinuityError> {
        doc_str(self.genesis(), "id")
    }

    pub fn slug(&self) -> Result<String, ContinuityError> {
        let twin = self.twin_did()?;
        Ok(twin.rsplit(':').next().unwrap_or_default().to_owned())
    }

    /// Turn the page: sign `new_document` with a rotation key of the current
    /// head and append it (wrong pen fails before any mutation).
    pub fn append(
        &mut self,
        new_document: Value,
        rotation_private_key: &PrivateKey,
    ) -> Result<(), ContinuityError> {
        let twin = self.twin_did()?;
        let new_id = doc_str(&new_document, "id")?;
        if new_id != twin {
            return Err(ContinuityError::NotTwin {
                expected: twin,
                got: new_id,
            });
        }
        let pen = encode_did_key(&rotation_private_key.public_key());
        let head_rotation = doc_str_list(self.head(), "rotationKeys")?;
        if !head_rotation.contains(&pen) {
            return Err(ContinuityError::WrongPen);
        }
        let payload = canonical_document_bytes(&new_document)?;
        let signature = sign_message(rotation_private_key, &payload);
        self.pages.push(ChainPage {
            document: new_document,
            signature: Some(signature.to_vec()),
        });
        Ok(())
    }

    /// Fail-closed verification, mirroring the Python `ContinuityChain.verify`
    /// including the twin-only branch.
    pub fn verify(&self) -> Result<(), ContinuityError> {
        if self.pages.is_empty() {
            return Err(ContinuityError::Empty);
        }
        let genesis = self.genesis();
        if self.pages[0].signature.is_some() {
            return Err(ContinuityError::GenesisSigned);
        }
        let twin = doc_str(genesis, "id")?;
        let also_known_as = doc_str_list(genesis, "alsoKnownAs")?;
        let domain = if let Some(pretty) = also_known_as.first() {
            if !twin.starts_with(&format!("{pretty}:")) {
                return Err(ContinuityError::NotUnderPretty {
                    twin,
                    pretty: pretty.clone(),
                });
            }
            pretty
                .strip_prefix("did:web:")
                .ok_or_else(|| ContinuityError::BadTwinId(twin.clone()))?
                .to_owned()
        } else {
            // twin-only: the domain comes from the twin id itself
            let (prefix, slug_part) = twin
                .rsplit_once(':')
                .ok_or_else(|| ContinuityError::BadTwinId(twin.clone()))?;
            let domain = prefix
                .strip_prefix("did:web:")
                .ok_or_else(|| ContinuityError::BadTwinId(twin.clone()))?;
            if domain.is_empty() || slug_part.is_empty() {
                return Err(ContinuityError::BadTwinId(twin.clone()));
            }
            domain.to_owned()
        };

        // 1. the slug self-certifies the genesis (decode/re-encode the
        //    multikey so a non-canonical encoding cannot smuggle a mismatch,
        //    exactly as Python round-trips through PublicKey)
        let signing_multikey = encode_multikey(&decode_multikey(&doc_signing_multikey(genesis)?)?);
        let rotation_keys = doc_str_list(genesis, "rotationKeys")?;
        let placeholder = build_document(
            &format!("did:web:{domain}:{SCID_PLACEHOLDER}"),
            &signing_multikey,
            &also_known_as,
            &rotation_keys,
        );
        let expected = derive_slug(&placeholder)?;
        let found = twin.rsplit(':').next().unwrap_or_default().to_owned();
        if expected != found {
            return Err(ContinuityError::SlugMismatch { found, expected });
        }

        // 2. every subsequent page is rotation-signed by its predecessor,
        //    under an unchanged twin id
        for i in 1..self.pages.len() {
            let prev = &self.pages[i - 1].document;
            let page = &self.pages[i];
            if doc_str(&page.document, "id")? != twin {
                return Err(ContinuityError::TwinIdChanged(i));
            }
            let signature = page
                .signature
                .as_deref()
                .ok_or(ContinuityError::MissingSignature(i))?;
            let payload = canonical_document_bytes(&page.document)?;
            let authorized = doc_str_list(prev, "rotationKeys")?
                .iter()
                .map(|entry| decode_did_key(entry))
                .collect::<Result<Vec<_>, _>>()?
                .iter()
                .any(|key| verify_signature(key, &payload, signature));
            if !authorized {
                return Err(ContinuityError::UnauthorizedPage(i));
            }
        }
        Ok(())
    }

    /// The Python `ContinuityChain.to_json` shape as a data-model `Value`
    /// (`{"chain": [{"document": ..., "signature": b64-or-null}]}`); the JSON
    /// face lives in the callers (fastatp-cross / fastatp-wasm convert).
    pub fn to_value(&self) -> Value {
        Value::map(vec![(
            "chain",
            Value::Array(
                self.pages
                    .iter()
                    .map(|page| {
                        Value::map(vec![
                            ("document", page.document.clone()),
                            (
                                "signature",
                                match &page.signature {
                                    Some(sig) => Value::from(data_encoding::BASE64.encode(sig)),
                                    None => Value::Null,
                                },
                            ),
                        ])
                    })
                    .collect(),
            ),
        )])
    }

    pub fn from_value(value: &Value) -> Result<Chain, ContinuityError> {
        let entries = value
            .get("chain")
            .and_then(Value::as_array)
            .ok_or_else(|| ContinuityError::BadDocument("missing chain list".into()))?;
        let mut pages = Vec::with_capacity(entries.len());
        for entry in entries {
            let document = entry
                .get("document")
                .cloned()
                .ok_or_else(|| ContinuityError::BadDocument("page missing document".into()))?;
            let signature = match entry.get("signature") {
                None | Some(Value::Null) => None,
                Some(Value::String(b64)) => Some(
                    data_encoding::BASE64
                        .decode(b64.as_bytes())
                        .map_err(|e| ContinuityError::BadDocument(format!("bad signature: {e}")))?,
                ),
                Some(_) => {
                    return Err(ContinuityError::BadDocument(
                        "signature must be b64/null".into(),
                    ))
                }
            };
            pages.push(ChainPage {
                document,
                signature,
            });
        }
        if pages.is_empty() {
            return Err(ContinuityError::Empty);
        }
        Ok(Chain { pages })
    }
}
