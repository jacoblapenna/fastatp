//! Data-model interop vectors: the same oracle the Python side passes
//! (`tests/test_data_model_vectors.py`). The JSON <-> data-model bridge here
//! mirrors `fastatp/datamodel.py` (`$link`/`$bytes` forms, integer-like float
//! coercion, `$type`/blob validation) — it lives in this test because the
//! Rust core's `Value` enum IS the data model; the dollar-forms only exist at
//! the JSON boundary.

use std::path::PathBuf;
use std::sync::LazyLock;

use fastatp_core::cid::Cid;
use fastatp_core::dagcbor::{self, Value};

fn vectors_dir() -> Option<PathBuf> {
    let dir =
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../reference/atproto-interop-tests");
    dir.is_dir().then_some(dir)
}

/// Python's base64.b64decode(validate=True) checks the alphabet but not
/// trailing bits; padding is repaired before decoding, mirroring datamodel.py.
static BASE64_LENIENT: LazyLock<data_encoding::Encoding> = LazyLock::new(|| {
    let mut spec = data_encoding::Specification::new();
    spec.symbols
        .push_str("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/");
    spec.padding = Some('=');
    spec.check_trailing_bits = false;
    spec.encoding().expect("static spec")
});

fn b64_decode_padded(s: &str) -> Result<Vec<u8>, String> {
    let padded = format!("{}{}", s, "=".repeat((4 - s.len() % 4) % 4));
    BASE64_LENIENT
        .decode(padded.as_bytes())
        .map_err(|e| format!("invalid base64: {e}"))
}

fn b64_encode_unpadded(data: &[u8]) -> String {
    BASE64_LENIENT.encode(data).trim_end_matches('=').to_owned()
}

// --- from_json: mirrors fastatp/datamodel.py::from_json -----------------------

fn from_json(json: &serde_json::Value) -> Result<Value, String> {
    if !json.is_object() {
        return Err("top level of a data-model document must be an object".into());
    }
    from_json_inner(json, 0)
}

fn from_json_inner(json: &serde_json::Value, depth: u32) -> Result<Value, String> {
    if depth > 128 {
        return Err("data is nested too deeply".into());
    }
    match json {
        serde_json::Value::Null => Ok(Value::Null),
        serde_json::Value::Bool(b) => Ok(Value::Bool(*b)),
        serde_json::Value::String(s) => Ok(Value::String(s.clone())),
        serde_json::Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                Ok(Value::Integer(i))
            } else if let Some(f) = n.as_f64() {
                if f.fract() == 0.0 && f >= i64::MIN as f64 && f <= i64::MAX as f64 {
                    Ok(Value::Integer(f as i64))
                } else {
                    Err(format!("float {f} is not allowed (integers only)"))
                }
            } else {
                Err("number out of range".into())
            }
        }
        serde_json::Value::Array(items) => Ok(Value::Array(
            items
                .iter()
                .map(|item| from_json_inner(item, depth + 1))
                .collect::<Result<_, _>>()?,
        )),
        serde_json::Value::Object(map) => {
            if map.contains_key("$bytes") {
                if map.len() != 1 {
                    return Err("$bytes object must have exactly one key".into());
                }
                let value = map["$bytes"]
                    .as_str()
                    .ok_or("$bytes value must be a base64 string")?;
                return Ok(Value::Bytes(b64_decode_padded(value)?));
            }
            if map.contains_key("$link") {
                if map.len() != 1 {
                    return Err("$link object must have exactly one key".into());
                }
                let value = map["$link"]
                    .as_str()
                    .ok_or("$link value must be a CID string")?;
                let cid = Cid::parse(value).map_err(|e| format!("invalid CID in $link: {e}"))?;
                return Ok(Value::Link(cid));
            }
            if let Some(type_value) = map.get("$type") {
                let type_str = type_value
                    .as_str()
                    .filter(|s| !s.is_empty())
                    .ok_or("$type must be a non-empty string")?;
                if type_str == "blob" {
                    for key in ["ref", "mimeType", "size"] {
                        if !map.contains_key(key) {
                            return Err(format!("blob is missing required field {key:?}"));
                        }
                    }
                    if !map["mimeType"].is_string() {
                        return Err("blob mimeType must be a string".into());
                    }
                    if !map["size"].is_i64() && !map["size"].is_u64() {
                        return Err("blob size must be an integer".into());
                    }
                }
            }
            let entries = map
                .iter()
                .map(|(k, v)| Ok((k.clone(), from_json_inner(v, depth + 1)?)))
                .collect::<Result<Vec<_>, String>>()?;
            Ok(Value::Map(entries))
        }
    }
}

// --- to_json: mirrors fastatp/datamodel.py::to_json ---------------------------

fn to_json(value: &Value) -> serde_json::Value {
    match value {
        Value::Null => serde_json::Value::Null,
        Value::Bool(b) => serde_json::Value::Bool(*b),
        Value::Integer(i) => serde_json::Value::from(*i),
        Value::String(s) => serde_json::Value::String(s.clone()),
        Value::Bytes(b) => serde_json::json!({ "$bytes": b64_encode_unpadded(b) }),
        Value::Link(cid) => serde_json::json!({ "$link": cid.to_string() }),
        Value::Array(items) => serde_json::Value::Array(items.iter().map(to_json).collect()),
        Value::Map(entries) => serde_json::Value::Object(
            entries
                .iter()
                .map(|(k, v)| (k.clone(), to_json(v)))
                .collect(),
        ),
    }
}

fn load(dir: &std::path::Path, name: &str) -> Vec<serde_json::Value> {
    let text = std::fs::read_to_string(dir.join("data-model").join(name))
        .unwrap_or_else(|e| panic!("cannot read {name}: {e}"));
    serde_json::from_str(&text).unwrap_or_else(|e| panic!("cannot parse {name}: {e}"))
}

#[test]
fn fixtures_encode_decode_cid() {
    let Some(dir) = vectors_dir() else {
        eprintln!("reference/atproto-interop-tests not present; skipping");
        return;
    };
    let fixtures = load(&dir, "data-model-fixtures.json");
    assert!(fixtures.len() >= 3);
    for fx in &fixtures {
        let tree = from_json(&fx["json"]).expect("fixture json must be valid data model");
        let encoded = dagcbor::encode(&tree).expect("fixture must encode");
        let expected = b64_decode_padded(fx["cbor_base64"].as_str().unwrap()).unwrap();
        assert_eq!(encoded, expected, "canonical encoding mismatch");
        // CID identical to atproto
        assert_eq!(
            Cid::from_dag_cbor(&encoded).to_string(),
            fx["cid"].as_str().unwrap()
        );
        // decode -> JSON form round-trip
        let decoded = dagcbor::decode(&encoded).expect("own bytes must decode");
        assert_eq!(to_json(&decoded), fx["json"]);
    }
}

#[test]
fn data_model_valid() {
    let Some(dir) = vectors_dir() else {
        eprintln!("reference/atproto-interop-tests not present; skipping");
        return;
    };
    for case in &load(&dir, "data-model-valid.json") {
        let note = case["note"].as_str().unwrap_or("?");
        let tree = from_json(&case["json"])
            .unwrap_or_else(|e| panic!("valid case {note:?} rejected: {e}"));
        dagcbor::encode(&tree).unwrap_or_else(|e| panic!("valid case {note:?} failed: {e}"));
    }
}

#[test]
fn data_model_invalid() {
    let Some(dir) = vectors_dir() else {
        eprintln!("reference/atproto-interop-tests not present; skipping");
        return;
    };
    for case in &load(&dir, "data-model-invalid.json") {
        let note = case["note"].as_str().unwrap_or("?");
        assert!(
            from_json(&case["json"]).is_err(),
            "invalid case {note:?} was accepted"
        );
    }
}

#[test]
fn integer_like_float_coerces() {
    let tree = from_json(&serde_json::json!({"a": 123.0})).unwrap();
    assert_eq!(tree.get("a").unwrap(), &Value::Integer(123));
}
