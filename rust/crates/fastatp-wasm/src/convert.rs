//! JSON ↔ data-model conversion at the JS boundary.
//!
//! Mirrors the Python JSON face (`datamodel.py`): the dollar-forms carry the
//! compound types — `{"$link": "<cid>"}` ↔ `Value::Link`, `{"$bytes":
//! "<base64>"}` ↔ `Value::Bytes` — and floats are refused exactly as the
//! data model refuses them.

use std::collections::BTreeMap;

use fastatp_core::cid::Cid;
use fastatp_core::dagcbor::Value;

pub fn json_to_value(json: &serde_json::Value) -> Result<Value, String> {
    Ok(match json {
        serde_json::Value::Null => Value::Null,
        serde_json::Value::Bool(b) => Value::Bool(*b),
        serde_json::Value::Number(n) => Value::Integer(
            n.as_i64()
                .ok_or_else(|| format!("non-integer number {n} has no data-model form"))?,
        ),
        serde_json::Value::String(s) => Value::from(s.as_str()),
        serde_json::Value::Array(items) => {
            Value::Array(items.iter().map(json_to_value).collect::<Result<_, _>>()?)
        }
        serde_json::Value::Object(entries) => {
            if entries.len() == 1 {
                if let Some(serde_json::Value::String(link)) = entries.get("$link") {
                    let cid = Cid::parse(link).map_err(|e| format!("bad $link: {e}"))?;
                    return Ok(Value::Link(cid));
                }
                if let Some(serde_json::Value::String(b64)) = entries.get("$bytes") {
                    let bytes = data_encoding::BASE64
                        .decode(pad_base64(b64).as_bytes())
                        .map_err(|e| format!("bad $bytes: {e}"))?;
                    return Ok(Value::Bytes(bytes));
                }
            }
            let mut map = BTreeMap::new();
            for (key, item) in entries {
                map.insert(key.clone(), json_to_value(item)?);
            }
            Value::Map(map.into_iter().collect())
        }
    })
}

pub fn value_to_json(value: &Value) -> Result<serde_json::Value, String> {
    Ok(match value {
        Value::Null => serde_json::Value::Null,
        Value::Bool(b) => serde_json::Value::Bool(*b),
        Value::Integer(i) => serde_json::Value::from(*i),
        Value::String(s) => serde_json::Value::from(s.as_str()),
        Value::Bytes(bytes) => serde_json::json!({
            "$bytes": data_encoding::BASE64_NOPAD.encode(bytes)
        }),
        Value::Link(cid) => serde_json::json!({ "$link": cid.to_string() }),
        Value::Array(items) => {
            serde_json::Value::Array(items.iter().map(value_to_json).collect::<Result<_, _>>()?)
        }
        Value::Map(entries) => {
            let mut object = serde_json::Map::new();
            for (key, item) in entries {
                object.insert(key.clone(), value_to_json(item)?);
            }
            serde_json::Value::Object(object)
        }
    })
}

/// Accept both padded and unpadded base64 in `$bytes` (atproto strips
/// padding; Python's decoder tolerates both).
fn pad_base64(b64: &str) -> String {
    let mut padded = b64.to_string();
    while !padded.len().is_multiple_of(4) {
        padded.push('=');
    }
    padded
}
