//! Cross-implementation bridge: a tiny CLI pytest drives so the
//! Rust and Python implementations verify each other's bytes.
//!
//! Identities are `did:key` throughout — both sides short-circuit resolution
//! for it, so the bridge needs no DID document plumbing.
//!
//! Subcommands:
//!   make-proof <out_dir> <curve> <collection> <rkey> <record_json>
//!       Mint a keypair, build a repo (with filler records so the MST is
//!       non-trivial), write proof.car, repo.car, and manifest.json.
//!   verify-proof <proof_path> <did_or_multikey> [collection/rkey]
//!       Verify a proof slice; print the verified record as JSON. Exit 1 on
//!       any refusal.
//!   import-car <car_path> <did_or_multikey>
//!       Load a full repo CAR (commit signature verified); print a summary.

use std::collections::BTreeMap;
use std::process::ExitCode;

use fastatp_core::continuity::{self, Chain};
use fastatp_core::crypto::{
    decode_did_key, decode_multikey, encode_did_key, encode_multikey, Curve, PrivateKey, PublicKey,
    SoftwareSigner,
};
use fastatp_core::dagcbor::Value;
use fastatp_core::repo::{verify_proof, Repo};

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let result = match args.first().map(String::as_str) {
        Some("make-proof") => make_proof(&args[1..]),
        Some("verify-proof") => verify(&args[1..]),
        Some("import-car") => import_car(&args[1..]),
        Some("make-genesis") => make_genesis(&args[1..]),
        Some("verify-chain") => verify_chain(&args[1..]),
        _ => Err(
            "usage: fastatp-cross <make-proof|verify-proof|import-car|make-genesis|verify-chain> ..."
                .into(),
        ),
    };
    match result {
        Ok(()) => ExitCode::SUCCESS,
        Err(message) => {
            eprintln!("fastatp-cross: {message}");
            ExitCode::FAILURE
        }
    }
}

fn parse_key(spec: &str) -> Result<PublicKey, String> {
    let decoded = if spec.starts_with("did:key:") {
        decode_did_key(spec)
    } else {
        decode_multikey(spec)
    };
    decoded.map_err(|e| format!("bad key {spec:?}: {e}"))
}

fn make_proof(args: &[String]) -> Result<(), String> {
    let [out_dir, curve, collection, rkey, record_json] = args else {
        return Err("make-proof <out_dir> <curve> <collection> <rkey> <record_json>".into());
    };
    let curve = Curve::parse(curve).map_err(|e| e.to_string())?;
    let key = PrivateKey::generate(curve);
    let public = key.public_key();
    let did = encode_did_key(&public);

    let record = json_to_value(
        &serde_json::from_str::<serde_json::Value>(record_json)
            .map_err(|e| format!("record is not JSON: {e}"))?,
    )?;

    let mut repo =
        pollster::block_on(Repo::new(&did, SoftwareSigner::new(key))).map_err(|e| e.to_string())?;
    // filler so the proof is a strict slice of a non-trivial tree
    for i in 0..4 {
        let filler = Value::map(vec![
            ("$type", Value::from("com.example.filler")),
            ("n", Value::from(i as i64)),
        ]);
        pollster::block_on(repo.put_record("com.example.filler", &format!("fill-{i}"), filler))
            .map_err(|e| e.to_string())?;
    }
    pollster::block_on(repo.put_record(collection, rkey, record)).map_err(|e| e.to_string())?;

    let out = std::path::Path::new(out_dir);
    std::fs::create_dir_all(out).map_err(|e| e.to_string())?;
    std::fs::write(
        out.join("proof.car"),
        repo.prove(collection, rkey).map_err(|e| e.to_string())?,
    )
    .map_err(|e| e.to_string())?;
    std::fs::write(
        out.join("repo.car"),
        repo.export_car().map_err(|e| e.to_string())?,
    )
    .map_err(|e| e.to_string())?;
    let manifest = serde_json::json!({
        "did": did,
        "multikey": encode_multikey(&public),
        "collection": collection,
        "rkey": rkey,
        "rev": repo.rev(),
    });
    std::fs::write(out.join("manifest.json"), manifest.to_string()).map_err(|e| e.to_string())?;
    println!("{manifest}");
    Ok(())
}

fn verify(args: &[String]) -> Result<(), String> {
    let (proof_path, key_spec, path) = match args {
        [proof, key] => (proof, key, None),
        [proof, key, path] => (proof, key, Some(path.as_str())),
        _ => return Err("verify-proof <proof_path> <did_or_multikey> [collection/rkey]".into()),
    };
    let proof = std::fs::read(proof_path).map_err(|e| e.to_string())?;
    let key = parse_key(key_spec)?;
    let verified = verify_proof(&proof, &key, path).map_err(|e| format!("refused: {e}"))?;
    let report = serde_json::json!({
        "did": verified.did,
        "rev": verified.rev,
        "collection": verified.collection,
        "rkey": verified.rkey,
        "record_cid": verified.record_cid.to_string(),
        "record": value_to_json(&verified.record)?,
    });
    println!("{report}");
    Ok(())
}

fn import_car(args: &[String]) -> Result<(), String> {
    let [car_path, key_spec] = args else {
        return Err("import-car <car_path> <did_or_multikey>".into());
    };
    let data = std::fs::read(car_path).map_err(|e| e.to_string())?;
    let key = parse_key(key_spec)?;
    let throwaway = SoftwareSigner::new(PrivateKey::generate(Curve::K256));
    let repo = Repo::from_car(
        &data,
        throwaway,
        Box::new(fastatp_core::tid::TidClock::random()),
        Some(&key),
    )
    .map_err(|e| format!("refused: {e}"))?;
    let report = serde_json::json!({
        "did": repo.did(),
        "rev": repo.rev(),
        "records": repo.record_count(None),
        "commit_cid": repo.commit_cid().to_string(),
    });
    println!("{report}");
    Ok(())
}

fn make_genesis(args: &[String]) -> Result<(), String> {
    let [out_dir, domain] = args else {
        return Err("make-genesis <out_dir> <domain>".into());
    };
    // rotation key stays cold (k256 like the Python tests); the pen is p256
    // because that is what a browser mints
    let rotation = PrivateKey::generate(Curve::K256);
    let rotation_did = encode_did_key(&rotation.public_key());
    let signing = PrivateKey::generate(Curve::P256);
    let signing_multikey = encode_multikey(&signing.public_key());

    let chain = Chain::create(
        domain,
        &signing_multikey,
        std::slice::from_ref(&rotation_did),
        false,
    )
    .map_err(|e| e.to_string())?;
    chain
        .verify()
        .map_err(|e| format!("fresh genesis failed self-check: {e}"))?;
    let twin = chain.twin_did().map_err(|e| e.to_string())?;
    let slug = chain.slug().map_err(|e| e.to_string())?;

    // a rotated twin: append page two under a fresh signing key
    let mut rotated = chain.clone();
    let new_signing = PrivateKey::generate(Curve::P256);
    let page_two = continuity::build_document(
        &twin,
        &encode_multikey(&new_signing.public_key()),
        &[],
        std::slice::from_ref(&rotation_did),
    );
    rotated
        .append(page_two, &rotation)
        .map_err(|e| e.to_string())?;
    rotated
        .verify()
        .map_err(|e| format!("rotated chain failed self-check: {e}"))?;

    let out = std::path::Path::new(out_dir);
    std::fs::create_dir_all(out).map_err(|e| e.to_string())?;
    std::fs::write(
        out.join("chain.json"),
        value_to_json(&chain.to_value())?.to_string(),
    )
    .map_err(|e| e.to_string())?;
    std::fs::write(
        out.join("chain2.json"),
        value_to_json(&rotated.to_value())?.to_string(),
    )
    .map_err(|e| e.to_string())?;
    let manifest = serde_json::json!({
        "twin_did": twin,
        "slug": slug,
        "rotation_did": rotation_did,
        "signing_multikey": signing_multikey,
        "domain": domain,
    });
    std::fs::write(out.join("manifest.json"), manifest.to_string()).map_err(|e| e.to_string())?;
    println!("{manifest}");
    Ok(())
}

fn verify_chain(args: &[String]) -> Result<(), String> {
    let [chain_path] = args else {
        return Err("verify-chain <chain.json>".into());
    };
    let raw = std::fs::read_to_string(chain_path).map_err(|e| e.to_string())?;
    let json: serde_json::Value =
        serde_json::from_str(&raw).map_err(|e| format!("not JSON: {e}"))?;
    let chain = Chain::from_value(&json_to_value(&json)?).map_err(|e| format!("refused: {e}"))?;
    chain.verify().map_err(|e| format!("refused: {e}"))?;
    let report = serde_json::json!({
        "twin": chain.twin_did().map_err(|e| e.to_string())?,
        "slug": chain.slug().map_err(|e| e.to_string())?,
        "pages": chain.pages.len(),
    });
    println!("{report}");
    Ok(())
}

// --- JSON <-> data model (records in the bridge use the dollar-forms the
// --- Python side emits; floats are refused exactly as the data model does)

fn json_to_value(json: &serde_json::Value) -> Result<Value, String> {
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
            let mut map = BTreeMap::new();
            for (key, item) in entries {
                map.insert(key.clone(), json_to_value(item)?);
            }
            Value::Map(map.into_iter().collect())
        }
    })
}

fn value_to_json(value: &Value) -> Result<serde_json::Value, String> {
    Ok(match value {
        Value::Null => serde_json::Value::Null,
        Value::Bool(b) => serde_json::Value::Bool(*b),
        Value::Integer(i) => serde_json::Value::from(*i),
        Value::String(s) => serde_json::Value::from(s.as_str()),
        Value::Bytes(_) => {
            return Err("the cross bridge does not carry bytes fields; use simple records".into())
        }
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
