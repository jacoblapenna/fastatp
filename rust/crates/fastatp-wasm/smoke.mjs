// Smoke test for the built browser-peer bundle, run under Node 22 (which has
// globalThis.crypto.subtle — the same web-target bundle that ships in the
// wheel). Run with:  node smoke.mjs
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
import assert from "node:assert/strict";

const here = path.dirname(fileURLToPath(import.meta.url));
const staticDir = path.resolve(here, "../../../fastatp/browser/static");

const glue = await import(path.join(staticDir, "fastatp_wasm.js"));
const wasmBytes = await readFile(path.join(staticDir, "fastatp_wasm_bg.wasm"));
await glue.default({ module_or_path: wasmBytes });

const { connection_zero, verify_proof, bundle_version, generate_pen, pen_did } = glue;

// --- connection zero ---------------------------------------------------------
const bundle = await connection_zero("shelf.example");
assert.match(bundle.pen_did, /^did:key:zDn/, "pen must be p256");
assert.match(bundle.twin_did, /^did:web:shelf\.example:[a-z2-7]{32}$/, "twin-only slug DID");
assert.match(bundle.rotation_did, /^did:key:zQ3sh/, "rotation root is k256");
assert.equal(bundle.rotation_secret.length, 64, "32-byte hex paper secret");

const chain = JSON.parse(bundle.chain_json);
assert.equal(chain.chain.length, 1, "genesis-only chain");
assert.equal(chain.chain[0].signature, null, "genesis carries no rotation signature");
assert.equal(chain.chain[0].document.id, bundle.twin_did);
assert.deepEqual(chain.chain[0].document.alsoKnownAs, [], "twin-only: no pretty alias");
assert.deepEqual(chain.chain[0].document.rotationKeys, [bundle.rotation_did]);

const genesisCar = bundle.genesis_car;
assert.ok(genesisCar.length > 100, "genesis repo CAR present");

// --- records: put -> prove -> verify round-trip --------------------------------
const peer = bundle.take_peer();
assert.equal(peer.did(), bundle.twin_did);
assert.equal(peer.pen_did(), bundle.pen_did);
assert.throws(() => bundle.take_peer(), /already taken/);

const record = { $type: "com.example.note", text: "hello from the browser", n: 42 };
const proof = await peer.put_record("com.example.note", "n-1", JSON.stringify(record));
assert.ok(proof.length > 100, "proof slice produced");

const verified = JSON.parse(verify_proof(proof, bundle.pen_did, null));
assert.equal(verified.did, bundle.twin_did);
assert.equal(verified.collection, "com.example.note");
assert.equal(verified.rkey, "n-1");
assert.deepEqual(verified.record, record, "record round-trips through the proof");

// tampered proof refused
const tampered = new Uint8Array(proof);
tampered[tampered.length - 1] ^= 1;
assert.throws(() => verify_proof(tampered, bundle.pen_did, null), /refused|Malformed/);

// wrong key refused
const other = await connection_zero("shelf.example");
assert.throws(() => verify_proof(proof, other.pen_did, null), /refused/);

// --- challenge signing ----------------------------------------------------------
const sig = await peer.sign_challenge("test-nonce-abc");
assert.match(sig, /^[A-Za-z0-9_-]{80,96}$/, "base64url unpadded 64-byte signature");
const sig2 = await peer.sign_challenge("test-nonce-abc");
assert.ok(sig.length === sig2.length, "signatures well-formed across calls");

// --- pens as handles: generate_pen / pen_did ----------------------------------------
const pen = await generate_pen();
assert.equal(pen.privateKey.extractable, false, "pen private key is non-extractable");
assert.equal(pen.privateKey.algorithm.name, "ECDSA");
assert.equal(pen.privateKey.algorithm.namedCurve, "P-256");
const penDid = await pen_did(pen);
assert.match(penDid, /^did:key:zDn[1-9A-HJ-NP-Za-km-z]+$/, "p256 did:key");
assert.equal(await pen_did(pen), penDid, "same pair, same did:key");
assert.notEqual(await pen_did(await generate_pen()), penDid, "each pen is new");

// IndexedDB stores a pen by structured clone; the clone stays non-extractable
// and still signs (pen_did signs and verifies a probe to check the pair)
const cloned = structuredClone(pen);
assert.equal(cloned.privateKey.extractable, false, "clone stays non-extractable");
assert.equal(await pen_did(cloned), penDid, "the cloned pen is the same pen and still signs");

const subtle = globalThis.crypto.subtle;
const ecdsa = (namedCurve, extractable) =>
  subtle.generateKey({ name: "ECDSA", namedCurve }, extractable, ["sign", "verify"]);
await assert.rejects(pen_did(await ecdsa("P-256", true)), /extractable/, "extractable pen refused");
await assert.rejects(pen_did(await ecdsa("P-384", false)), /P-256/, "wrong curve refused");
const otherPen = await generate_pen();
await assert.rejects(
  pen_did({ privateKey: pen.privateKey, publicKey: otherPen.publicKey }),
  /not a pair/,
  "halves of two different pens refused",
);
await assert.rejects(
  pen_did({ privateKey: pen.publicKey, publicKey: pen.publicKey }),
  /private key/,
  "a public key in the privateKey slot refused",
);
await assert.rejects(pen_did(null), /null or undefined/);
await assert.rejects(pen_did({ privateKey: pen.privateKey }), /publicKey/);

// --- export + version -------------------------------------------------------------
const car = peer.export_car();
assert.ok(car.length > genesisCar.length, "repo grew past genesis");
// bundle_version() is this crate's CARGO_PKG_VERSION, so compare it against
// the crate's own Cargo.toml rather than a literal that every release breaks
const cargoToml = await readFile(path.join(here, "Cargo.toml"), "utf8");
const crateVersion = cargoToml.match(/^version = "([^"]+)"$/m)?.[1];
assert.ok(crateVersion, "version not found in fastatp-wasm/Cargo.toml");
assert.equal(bundle_version(), crateVersion);

console.log("smoke: ALL PASS");
console.log(`  twin: ${bundle.twin_did}`);
console.log(`  pen:  ${bundle.pen_did}`);
console.log(`  wasm: ${wasmBytes.length} bytes`);
