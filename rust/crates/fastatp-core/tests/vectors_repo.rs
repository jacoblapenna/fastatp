//! MST interop vectors, commit-proof fixture roots, CAR
//! round-trips, and prove/verify — the same consumption as tests/test_mst.py
//! and tests/test_repo.py on the Python side.

use std::collections::BTreeMap;
use std::path::PathBuf;

use serde::Deserialize;

use fastatp_core::cid::Cid;
use fastatp_core::crypto::{Curve, PrivateKey, SoftwareSigner};
use fastatp_core::dagcbor::Value;
use fastatp_core::mst::{self, MemoryBlockStore};
use fastatp_core::repo::{verify_proof, ProofError, Repo, WriteOp};

fn interop_dir() -> Option<PathBuf> {
    let dir =
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../reference/atproto-interop-tests");
    dir.is_dir().then_some(dir)
}

macro_rules! require_vectors {
    () => {
        match interop_dir() {
            Some(dir) => dir,
            None => {
                eprintln!("reference/atproto-interop-tests not present; skipping");
                return;
            }
        }
    };
}

fn record(text: &str) -> Value {
    Value::map(vec![
        ("$type", Value::String("com.example.note".into())),
        ("text", Value::String(text.into())),
    ])
}

fn signer(curve: Curve) -> SoftwareSigner {
    SoftwareSigner::new(PrivateKey::generate(curve))
}

// --- MST vectors -----------------------------------------------------------------

#[derive(Deserialize)]
struct CommonPrefixCase {
    left: String,
    right: String,
    len: usize,
}

#[test]
fn common_prefix_vectors() {
    let dir = require_vectors!();
    let raw = std::fs::read_to_string(dir.join("mst/common_prefix.json")).unwrap();
    let cases: Vec<CommonPrefixCase> = serde_json::from_str(&raw).unwrap();
    assert!(!cases.is_empty());
    for case in cases {
        assert_eq!(
            mst::common_prefix_len(&case.left, &case.right),
            case.len,
            "{} vs {}",
            case.left,
            case.right
        );
    }
}

#[derive(Deserialize)]
struct KeyHeightCase {
    key: String,
    height: u32,
}

#[test]
fn key_height_vectors() {
    let dir = require_vectors!();
    let raw = std::fs::read_to_string(dir.join("mst/key_heights.json")).unwrap();
    let cases: Vec<KeyHeightCase> = serde_json::from_str(&raw).unwrap();
    assert!(!cases.is_empty());
    for case in cases {
        assert_eq!(mst::key_height(&case.key), case.height, "{}", case.key);
    }
}

#[test]
fn empty_tree_root_is_the_canonical_cid() {
    let mut store = MemoryBlockStore::new();
    let root = mst::build(&mut store, &BTreeMap::new()).unwrap();
    assert_eq!(
        root.to_string(),
        "bafyreie5737gdxlw5i64vzichcalba3z2v5n6icifvx5xytvske7mr3hpm"
    );
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct CommitProofFixture {
    comment: String,
    leaf_value: String,
    keys: Vec<String>,
    adds: Vec<String>,
    dels: Vec<String>,
    root_before_commit: String,
    root_after_commit: String,
}

#[test]
fn commit_proof_fixture_roots() {
    let dir = require_vectors!();
    let raw = std::fs::read_to_string(dir.join("firehose/commit-proof-fixtures.json")).unwrap();
    let fixtures: Vec<CommitProofFixture> = serde_json::from_str(&raw).unwrap();
    assert!(fixtures.len() >= 5);
    for fx in fixtures {
        let leaf: Cid = fx.leaf_value.parse().unwrap();
        let mut store = MemoryBlockStore::new();
        let mut entries: BTreeMap<String, Cid> =
            fx.keys.iter().map(|k| (k.clone(), leaf.clone())).collect();
        let root_before = mst::build(&mut store, &entries).unwrap();
        assert_eq!(
            root_before.to_string(),
            fx.root_before_commit,
            "{}",
            fx.comment
        );

        for key in &fx.dels {
            entries.remove(key);
        }
        for key in &fx.adds {
            entries.insert(key.clone(), leaf.clone());
        }
        let root_after = mst::build(&mut store, &entries).unwrap();
        assert_eq!(
            root_after.to_string(),
            fx.root_after_commit,
            "{}",
            fx.comment
        );

        // reading the tree back from blocks reproduces the map
        assert_eq!(
            mst::load(&store, &root_after).unwrap(),
            entries,
            "{}",
            fx.comment
        );
    }
}

#[test]
fn example_keys_build_read_and_are_deterministic() {
    let dir = require_vectors!();
    let raw = std::fs::read_to_string(dir.join("mst/example_keys.txt")).unwrap();
    let keys: Vec<&str> = raw
        .lines()
        .filter(|line| !line.is_empty() && !line.starts_with('#'))
        .collect();
    assert!(keys.len() > 20);
    let value = Cid::from_raw(b"value");
    let entries: BTreeMap<String, Cid> = keys
        .iter()
        .map(|k| ((*k).to_owned(), value.clone()))
        .collect();
    let mut store = MemoryBlockStore::new();
    let root = mst::build(&mut store, &entries).unwrap();
    assert_eq!(mst::load(&store, &root).unwrap(), entries);
    // point lookups agree with the full walk
    for key in &keys {
        assert_eq!(
            mst::get(&store, &root, key).unwrap().as_ref(),
            Some(&value),
            "{key}"
        );
    }
    // a proof path exists for every present key and starts at the root
    let path = mst::prove_path(&store, &root, keys[0]).unwrap();
    assert_eq!(path[0], root);
}

#[test]
fn key_validation_matches_python() {
    assert!(mst::validate_mst_key("app.bsky.feed.post/454397e440ec").is_ok());
    for bad in [
        "",
        "nokeypart",
        "/leading",
        "trailing/",
        "a//b",
        "col/rkey/extra",
        &"x".repeat(1300),
    ] {
        assert!(mst::validate_mst_key(bad).is_err(), "{bad:?}");
    }
}

#[test]
fn crafted_negative_prefix_len_is_rejected() {
    use fastatp_core::dagcbor;
    use fastatp_core::mst::BlockStore;

    let value = Cid::from_raw(b"v");
    let node = dagcbor::encode(&Value::map(vec![
        (
            "e",
            Value::Array(vec![Value::map(vec![
                ("k", Value::Bytes(b"x".to_vec())),
                ("p", Value::Integer(-1)),
                ("t", Value::Null),
                ("v", Value::Link(value)),
            ])]),
        ),
        ("l", Value::Null),
    ]))
    .unwrap();
    let cid = Cid::from_dag_cbor(&node);
    let mut store = MemoryBlockStore::new();
    store.put(cid.clone(), node);
    assert!(mst::load(&store, &cid).is_err());
}

// --- repo round-trips --------------------------------------------------------------

#[test]
fn repo_car_round_trip_preserves_root_and_records() {
    pollster::block_on(async {
        let signer = signer(Curve::K256);
        let verify_key = signer.public_key();
        let mut repo = Repo::new("did:web:example.com", signer).await.unwrap();
        repo.put_record("com.example.note", "one", record("first"))
            .await
            .unwrap();
        repo.put_record("com.example.note", "two", record("second"))
            .await
            .unwrap();
        repo.put_record("com.example.config", "main", record("config"))
            .await
            .unwrap();

        let car = repo.export_car().unwrap();
        let reloaded = Repo::from_car(
            &car,
            signer_like_for_reload(),
            Box::new(fastatp_core::repo::ExternalClock::new(|| 1, 0)),
            Some(&verify_key),
        )
        .unwrap();
        assert_eq!(reloaded.commit_cid(), repo.commit_cid()); // byte-identical root
        assert_eq!(reloaded.did(), "did:web:example.com");
        assert_eq!(
            reloaded.get_record("com.example.note", "two").unwrap(),
            Some(record("second"))
        );
        assert_eq!(reloaded.record_count(None), 3);
        assert_eq!(reloaded.list_records("com.example.note").len(), 2);
    });
}

fn signer_like_for_reload() -> SoftwareSigner {
    // reloading a CAR does not need the original pen; a different signer only
    // matters at the NEXT commit (which re-signs everything)
    signer(Curve::P256)
}

#[test]
fn present_state_gc_holds_block_count_steady() {
    pollster::block_on(async {
        let mut repo = Repo::new("did:web:example.com", signer(Curve::K256))
            .await
            .unwrap();
        repo.put_record("com.example.config", "keep", record("durable"))
            .await
            .unwrap();
        let baseline = repo.block_count();
        for i in 0..20 {
            let rkey = format!("t{i}");
            repo.put_record("com.example.telemetry", &rkey, record("x"))
                .await
                .unwrap();
            repo.delete_record("com.example.telemetry", &rkey)
                .await
                .unwrap();
        }
        // FIFO churn on one collection leaves store size at the steady state
        assert_eq!(repo.block_count(), baseline);
        assert_eq!(
            repo.get_record("com.example.config", "keep").unwrap(),
            Some(record("durable"))
        );
    });
}

#[test]
fn batch_writes_validate_against_accumulated_state() {
    pollster::block_on(async {
        let mut repo = Repo::new("did:web:example.com", signer(Curve::K256))
            .await
            .unwrap();
        let dupe = vec![
            WriteOp::create("com.example.note", "same", record("a")),
            WriteOp::create("com.example.note", "same", record("b")),
        ];
        assert!(repo.apply_writes(dupe).await.is_err());
        assert!(repo
            .apply_writes(vec![WriteOp::delete("com.example.note", "absent")])
            .await
            .is_err());
        assert!(repo.apply_writes(Vec::new()).await.is_err());
    });
}

// --- prove / verify -------------------------------------------------------------------

#[test]
fn prove_verify_round_trip_and_negatives() {
    pollster::block_on(async {
        let signer = signer(Curve::K256);
        let good_key = signer.public_key();
        let mut repo = Repo::new("did:web:example.com", signer).await.unwrap();
        repo.put_record("com.example.note", "hello", record("hi"))
            .await
            .unwrap();
        // several records so the proof slice is a strict subset of the repo
        for i in 0..12 {
            repo.put_record("com.example.filler", &format!("r{i}"), record("filler"))
                .await
                .unwrap();
        }
        let proof = repo.prove("com.example.note", "hello").unwrap();

        let verified = verify_proof(&proof, &good_key, None).unwrap();
        assert_eq!(verified.did, "did:web:example.com");
        assert_eq!(verified.collection, "com.example.note");
        assert_eq!(verified.rkey, "hello");
        assert_eq!(verified.record, record("hi"));
        assert_eq!(verified.rev, repo.rev());

        // explicit path selection works too
        let by_path = verify_proof(&proof, &good_key, Some("com.example.note/hello")).unwrap();
        assert_eq!(by_path.record_cid, verified.record_cid);

        // wrong key refuses
        let wrong = PrivateKey::generate(Curve::K256).public_key();
        assert!(matches!(
            verify_proof(&proof, &wrong, None),
            Err(ProofError::SignatureInvalid)
        ));

        // a path the proof does not witness refuses
        assert!(matches!(
            verify_proof(&proof, &good_key, Some("com.example.filler/r3")),
            Err(ProofError::NotWitnessed(_))
        ));

        // tampering with any byte of the record block breaks the content
        // address and the whole proof is refused at decode
        let mut tampered = proof.clone();
        let needle = b"hi";
        let pos = tampered
            .windows(needle.len())
            .rposition(|w| w == needle)
            .unwrap();
        tampered[pos] ^= 0x01;
        assert!(verify_proof(&tampered, &good_key, None).is_err());
    });
}

#[test]
fn proof_is_a_slice_not_the_whole_repo() {
    pollster::block_on(async {
        let signer = signer(Curve::P256);
        let key = signer.public_key();
        let mut repo = Repo::new("did:key:zProof", signer).await.unwrap();
        for i in 0..40 {
            // unique contents: identical records would dedup to one block and
            // legitimately witness several keys (same as the Python side)
            repo.put_record(
                "com.example.bulk",
                &format!("r{i:02}"),
                record(&format!("x{i}")),
            )
            .await
            .unwrap();
        }
        let proof = repo.prove("com.example.bulk", "r07").unwrap();
        let full = repo.export_car().unwrap();
        assert!(
            proof.len() < full.len() / 2,
            "proof should be a small slice"
        );
        let verified = verify_proof(&proof, &key, None).unwrap();
        assert_eq!(verified.rkey, "r07");
    });
}

#[test]
fn commit_event_reports_ops_and_blocks() {
    pollster::block_on(async {
        let mut repo = Repo::new("did:web:example.com", signer(Curve::K256))
            .await
            .unwrap();
        let event = repo
            .put_record("com.example.note", "n", record("v1"))
            .await
            .unwrap();
        assert_eq!(event.ops.len(), 1);
        assert_eq!(event.ops[0].action, "create");
        assert_eq!(event.ops[0].path, "com.example.note/n");
        assert!(event.ops[0].cid.is_some());
        assert!(event.since.is_some()); // genesis came before
        let update = repo
            .put_record("com.example.note", "n", record("v2"))
            .await
            .unwrap();
        assert_eq!(update.ops[0].action, "update");
        assert!(update.ops[0].prev.is_some());
        assert!(update.rev > event.rev); // rev clock is strictly monotonic
        let deletion = repo.delete_record("com.example.note", "n").await.unwrap();
        assert_eq!(deletion.ops[0].action, "delete");
        assert!(deletion.ops[0].cid.is_none());
    });
}

#[test]
fn empty_witness_proof_is_distinguished_from_ambiguity() {
    // A "proof" that is just the signed commit + MST nodes with no record
    // block witnesses nothing: EmptyProof, not Ambiguous(0).
    use fastatp_core::crypto::{Curve, PrivateKey, SoftwareSigner};
    use fastatp_core::repo::{verify_proof, ProofError, Repo};

    let key = PrivateKey::generate(Curve::K256);
    let public = key.public_key();
    let did = fastatp_core::crypto::encode_did_key(&public);
    let mut repo = pollster::block_on(Repo::new(&did, SoftwareSigner::new(key))).unwrap();
    let record = fastatp_core::dagcbor::Value::map(vec![(
        "$type",
        fastatp_core::dagcbor::Value::from("com.example.note"),
    )]);
    pollster::block_on(repo.put_record("com.example.note", "n", record)).unwrap();

    let proof = repo.prove("com.example.note", "n").unwrap();
    // strip the record block out of the CAR: re-encode without the one block
    // whose bytes decode to the record (the only non-commit, non-MST block)
    let (roots, blocks) = fastatp_core::car::decode_car(&proof).unwrap();
    let record_cid = repo.get_record("com.example.note", "n").unwrap();
    assert!(record_cid.is_some());
    let kept: Vec<_> = blocks
        .into_iter()
        .filter(|(cid, _)| {
            // keep commit + MST nodes; drop the record leaf block
            repo.list_records("com.example.note")
                .iter()
                .all(|(_, leaf)| leaf != cid)
        })
        .collect();
    let stripped = fastatp_core::car::encode_car(&roots, &kept);
    match verify_proof(&stripped, &public, None) {
        Err(ProofError::EmptyProof) => {}
        other => panic!("expected EmptyProof, got {other:?}"),
    }
}
