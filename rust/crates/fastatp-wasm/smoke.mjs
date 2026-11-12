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

const {
  connection_zero,
  verify_proof,
  bundle_version,
  generate_pen,
  pen_did,
  sign_challenge,
  rotation_did_from_secret,
  rotate_pen,
  BrowserPeer,
} = glue;

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

// --- the knock: sign_challenge(pen, nonce, repo_did), no repo loaded ----------------
// one call whether the pen wants its own repo back (owner pull) or another
// peer's (the browser gate); the host decides which. the host mints
// secrets.token_urlsafe(32): 43 unpadded base64url chars
assert.equal(typeof BrowserPeer.prototype.sign_challenge, "undefined", "the knock is not a peer method");
const nonce = Buffer.from(crypto.getRandomValues(new Uint8Array(32))).toString("base64url");
const repoDid = "did:web:shelf.example:abc";
const pullSig = await sign_challenge(pen, nonce, repoDid);
assert.match(pullSig, /^[A-Za-z0-9_-]{86}$/, "unpadded base64url of 64 bytes");
const sigBytes = Buffer.from(pullSig, "base64url");
assert.equal(sigBytes.length, 64, "compact R||S");
// low-S: fastatp verifiers reject high-S, and raw WebCrypto returns it half the time
const P256_N = BigInt("0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551");
assert.ok(BigInt("0x" + sigBytes.subarray(32).toString("hex")) <= P256_N / 2n, "low-S");
// the signed bytes are the fixed, labelled message: label \n repo DID \n nonce
const pullMessage = new TextEncoder().encode(`fastatp-challenge-v1\n${repoDid}\n${nonce}`);
const ecdsaVerify = (publicKey, sig, message) =>
  subtle.verify({ name: "ECDSA", hash: "SHA-256" }, publicKey, sig, message);
assert.ok(await ecdsaVerify(pen.publicKey, sigBytes, pullMessage), "verifies over the labelled message");
assert.ok(!(await ecdsaVerify(otherPen.publicKey, sigBytes, pullMessage)), "another pen's key fails");
assert.ok(
  !(await ecdsaVerify(pen.publicKey, sigBytes, new TextEncoder().encode(nonce))),
  "the bare nonce was not what got signed",
);
assert.ok(
  !(await ecdsaVerify(
    pen.publicKey,
    sigBytes,
    new TextEncoder().encode(`fastatp-challenge-v1\ndid:web:shelf.example:other\n${nonce}`),
  )),
  "bound to the repo DID: cannot be spent on another repo",
);
// the pen only ever signs that shape: malformed nonce or DID is refused, so a
// host cannot slip commit bytes through as a "challenge"
await assert.rejects(sign_challenge(pen, "", repoDid), /nonce/);
await assert.rejects(sign_challenge(pen, "has space", repoDid), /nonce/);
await assert.rejects(sign_challenge(pen, "padded==", repoDid), /nonce/);
await assert.rejects(sign_challenge(pen, "A".repeat(257), repoDid), /nonce/);
await assert.rejects(sign_challenge(pen, nonce, "shelf.example"), /DID/);
await assert.rejects(sign_challenge(pen, nonce, "did:web:"), /DID/);
await assert.rejects(sign_challenge(await ecdsa("P-256", true), nonce, repoDid), /extractable/);
await assert.rejects(sign_challenge(null, nonce, repoDid), /null or undefined/);
// a device repo: the pen's own did:key is the repo DID
assert.ok(
  await ecdsaVerify(
    pen.publicKey,
    Buffer.from(await sign_challenge(pen, nonce, penDid), "base64url"),
    new TextEncoder().encode(`fastatp-challenge-v1\n${penDid}\n${nonce}`),
  ),
  "device repo challenge verifies",
);

// --- connection_zero with a pen the app already holds -------------------------------
const kept = await generate_pen(); // what "keep me signed in" stores in IndexedDB
const keptDid = await pen_did(kept);
const withPen = await connection_zero("shelf.example", kept);
assert.equal(withPen.pen_did, keptDid, "the bundle's pen is the one passed in");
assert.notEqual(withPen.twin_did, bundle.twin_did, "a new identity all the same");
const keptPeer = withPen.take_peer();
assert.equal(keptPeer.pen_did(), keptDid);
const keptProof = await keptPeer.put_record(
  "com.example.note",
  "k-1",
  JSON.stringify({ $type: "com.example.note", text: "signed by the kept pen" }),
);
assert.equal(JSON.parse(verify_proof(keptProof, keptDid, null)).did, withPen.twin_did);
// the clone IndexedDB hands back after a reload works the same
assert.equal((await connection_zero("shelf.example", structuredClone(kept))).pen_did, keptDid);
// omitted, undefined and null all mint a fresh pen, as before
for (const absent of [undefined, null]) {
  const minted = await connection_zero("shelf.example", absent);
  assert.match(minted.pen_did, /^did:key:zDn/);
  assert.notEqual(minted.pen_did, keptDid);
}
await assert.rejects(connection_zero("shelf.example", await ecdsa("P-256", true)), /extractable/);
await assert.rejects(connection_zero("shelf.example", { privateKey: kept.privateKey }), /publicKey/);

// --- rotation_did_from_secret: the typed-back check, nothing leaves the page ---------
assert.equal(rotation_did_from_secret(bundle.rotation_secret), bundle.rotation_did);
assert.equal(rotation_did_from_secret(withPen.rotation_secret), withPen.rotation_did);
assert.equal(
  rotation_did_from_secret(` ${bundle.rotation_secret.toUpperCase()} `),
  bundle.rotation_did,
  "case and surrounding whitespace are forgiven: the user typed it",
);
assert.notEqual(rotation_did_from_secret(other.rotation_secret), bundle.rotation_did);
const typo = bundle.rotation_secret.slice(0, 63) + (bundle.rotation_secret.endsWith("0") ? "1" : "0");
assert.notEqual(rotation_did_from_secret(typo), bundle.rotation_did, "one wrong character is a different key");
assert.throws(() => rotation_did_from_secret(""), /64 hex/);
assert.throws(() => rotation_did_from_secret("abc"), /64 hex/);
assert.throws(() => rotation_did_from_secret("zz".repeat(32)), /64 hex/);
assert.throws(() => rotation_did_from_secret("00".repeat(32)), /not a valid rotation secret/);
assert.throws(() => rotation_did_from_secret("ff".repeat(32)), /not a valid rotation secret/);
assert.match(rotation_did_from_secret("01".repeat(32)), /^did:key:zQ3sh/, "any in-range scalar derives a k256 did:key");

// --- BrowserPeer.create / restore ----------------------------------------------------
// a device repo: the pen's own did:key, signed by that pen
const devicePeer = await BrowserPeer.create(penDid, pen);
assert.equal(devicePeer.did(), penDid);
assert.equal(devicePeer.pen_did(), penDid);
const deviceProof = await devicePeer.put_record(
  "com.example.note",
  "d-1",
  JSON.stringify({ $type: "com.example.note", text: "from a device repo" }),
);
assert.equal(JSON.parse(verify_proof(deviceProof, penDid, null)).did, penDid);

// any DID string works; the app decides which pen signs which repo
const named = await BrowserPeer.create("did:web:example.com:alice", pen);
assert.equal(named.did(), "did:web:example.com:alice");
await assert.rejects(BrowserPeer.create("example.com", pen), /not a valid DID/);
await assert.rejects(BrowserPeer.create("did:web:", pen), /not a valid DID/);
await assert.rejects(BrowserPeer.create(penDid, await ecdsa("P-256", true)), /extractable/);

// restore: same head, same records, verified against the pen that signed it
const stored = devicePeer.export_car();
const restoredRev = devicePeer.rev();
const restored = await BrowserPeer.restore(stored, cloned, penDid);
assert.equal(restored.did(), penDid);
assert.equal(restored.rev(), restoredRev, "restore keeps the head");
assert.deepEqual(restored.export_car(), stored, "restore is byte-identical");
await assert.rejects(
  BrowserPeer.restore(stored, pen, await pen_did(otherPen)),
  /restore/,
  "wrong verify key refused",
);
await assert.rejects(BrowserPeer.restore(stored, await ecdsa("P-256", true), penDid), /extractable/);
await assert.rejects(BrowserPeer.restore(new Uint8Array([1, 2, 3]), pen, null), /restore/);

// a device clock far behind the restored head still writes a later rev
const realNow = Date.now;
Date.now = () => 0;
try {
  const behind = await BrowserPeer.restore(stored, pen, penDid);
  await behind.put_record("com.example.note", "d-2", JSON.stringify({ $type: "com.example.note", text: "late" }));
  assert.ok(behind.rev() > restoredRev, `rev ${behind.rev()} must sort after ${restoredRev}`);
} finally {
  Date.now = realNow;
}

// --- recovery: rotate_pen turns the chain onto a new pen, signed by the paper secret --
const newPen = await generate_pen();
const newPenDid = await pen_did(newPen);
const rotatedJson = await rotate_pen(bundle.chain_json, bundle.rotation_secret, newPen);
const rotated = JSON.parse(rotatedJson);
assert.equal(rotated.chain.length, 2, "one page turned");
assert.deepEqual(rotated.chain[0], chain.chain[0], "genesis untouched");
const head = rotated.chain[1];
assert.equal(head.document.id, bundle.twin_did, "same identity");
assert.deepEqual(head.document.rotationKeys, [bundle.rotation_did], "rotation keys kept");
assert.deepEqual(head.document.alsoKnownAs, [], "aliases kept");
assert.equal(
  head.document.verificationMethod[0].publicKeyMultibase,
  newPenDid.slice("did:key:".length),
  "the head names the new pen",
);
assert.match(head.signature, /^[A-Za-z0-9+/]+=*$/, "rotation-signed");
// a rotation is a longer chain; it can be turned again from its own output
const again = JSON.parse(await rotate_pen(rotatedJson, bundle.rotation_secret, await generate_pen()));
assert.equal(again.chain.length, 3);
assert.deepEqual(again.chain.slice(0, 2), rotated.chain, "earlier pages untouched");
// refusals, each before anything is signed
await assert.rejects(rotate_pen(bundle.chain_json, other.rotation_secret, newPen), /rotation/, "wrong secret");
await assert.rejects(rotate_pen(bundle.chain_json, "abc", newPen), /64 hex/);
await assert.rejects(rotate_pen(bundle.chain_json, "00".repeat(32), newPen), /not a valid rotation secret/);
await assert.rejects(rotate_pen(bundle.chain_json, bundle.rotation_secret, await ecdsa("P-256", true)), /extractable/);
await assert.rejects(rotate_pen("not json", bundle.rotation_secret, newPen), /chain/);
const tamperedChain = structuredClone(chain);
tamperedChain.chain[0].document.rotationKeys = [other.rotation_did];
await assert.rejects(
  rotate_pen(JSON.stringify(tamperedChain), other.rotation_secret, newPen),
  /chain/,
  "a chain whose genesis no longer matches its slug is refused, even with its own secret",
);
const strippedChain = structuredClone(rotated);
strippedChain.chain[1].signature = null;
await assert.rejects(rotate_pen(JSON.stringify(strippedChain), bundle.rotation_secret, newPen), /chain/);

// the repo side of recovery: restore with the OLD pen as verify key, sign on with the new pen
const takenOver = await BrowserPeer.restore(stored, newPen, penDid);
assert.equal(takenOver.pen_did(), newPenDid);
const afterProof = await takenOver.put_record(
  "com.example.note",
  "d-3",
  JSON.stringify({ $type: "com.example.note", text: "signed by the new pen" }),
);
assert.equal(JSON.parse(verify_proof(afterProof, newPenDid, null)).rkey, "d-3");
assert.throws(() => verify_proof(afterProof, penDid, null), /refused/, "old pen no longer signs it");

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
