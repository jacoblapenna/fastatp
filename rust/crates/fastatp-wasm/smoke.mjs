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

const { connection_zero, verify_proof, bundle_version } = glue;

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

// --- export + version -------------------------------------------------------------
const car = peer.export_car();
assert.ok(car.length > genesisCar.length, "repo grew past genesis");
assert.equal(bundle_version(), "0.1.0");

console.log("smoke: ALL PASS");
console.log(`  twin: ${bundle.twin_did}`);
console.log(`  pen:  ${bundle.pen_did}`);
console.log(`  wasm: ${wasmBytes.length} bytes`);
