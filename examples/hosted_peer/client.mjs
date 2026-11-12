// The hosted-peer client: the shipped wasm bundle driven exactly as a browser
// app would drive it (Node 22 has crypto.subtle / fetch — same artifact).
//
//   node client.mjs <peer_url> <shelf_url> <out_dir> [domain]
//
// Runs the whole HOSTED-PEERS.md lifecycle and writes a JSON report plus a
// copy of EVERY http body it ever sent (the no-secret audit trail):
//   1. connection zero (client-side): a pen the app minted and keeps, the
//      paper root, a twin-only genesis
//   2. shelf the chain; sign up (POST the genesis CAR)
//   3. write records client-side: content and a delegation for each enrolled
//      device — then push the pen-signed CAR (the router); device 1 reads its
//      own repo back as its owner, with no record admitting it; device 2
//      writes its OWN repo, pushes it, and reads it back as its owner
//   4. device 2 knocks (sign_challenge: its pen, the host's nonce, the repo it
//      wants) and reads the identity's repo as the identity, through its
//      delegation
//   5. a stranger pen with a valid knock is refused
//   6. device 1 severs device 2's delegation (deletes the record) and pushes;
//      device 2's next connect is refused: absence IS revocation; a replay
//      of the older signed checkpoint is refused and changes nothing
//   7. recovery: every device is lost; a new pen plus the paper secret turn
//      the chain, the new pen pulls the repo as its owner, prunes the old
//      devices, pushes; the old pen can neither pull nor push
//
// Every device here is a pen minted with generate_pen() in this process.
// Only device 2 bothers to create its own repo; the knock needs none, because
// it is about the repo being asked for. On real hardware the enrollment
// ceremony is two devices exchanging exactly the two strings you see here:
// the new device's did:key one way, the delegation record's presence the
// other.

import { mkdir, readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

const [peerUrl, shelfUrl, outDir, domain = "shelf.example"] = process.argv.slice(2);
if (!peerUrl || !shelfUrl || !outDir) {
  console.error("usage: node client.mjs <peer_url> <shelf_url> <out_dir> [domain]");
  process.exit(2);
}

const here = path.dirname(fileURLToPath(import.meta.url));
const staticDir = path.resolve(here, "../../fastatp/browser/static");
const glue = await import(path.join(staticDir, "fastatp_wasm.js"));
await glue.default({ module_or_path: await readFile(path.join(staticDir, "fastatp_wasm_bg.wasm")) });
const { connection_zero, generate_pen, pen_did, sign_challenge, rotation_did_from_secret, rotate_pen, BrowserPeer } =
  glue;

const bodiesDir = path.join(outDir, "bodies");
await mkdir(bodiesDir, { recursive: true });
const report = { phases: {}, files: {} };
let sendCount = 0;

// every byte we ever send goes on disk for the no-secret audit
async function send(name, url, { method = "POST", body = null, headers = {} } = {}) {
  if (body !== null) {
    const bytes = typeof body === "string" ? Buffer.from(body) : Buffer.from(body);
    await writeFile(path.join(bodiesDir, `${String(sendCount++).padStart(2, "0")}-${name}.bin`), bytes);
  }
  const response = await fetch(url, { method, body, headers });
  report.phases[name] = response.status;
  return response;
}

const CAR = { "content-type": "application/vnd.ipld.car" };
const pushCar = (name, peer, did = twin) =>
  send(name, `${peerUrl}/push-car?did=${encodeURIComponent(did)}`, { body: peer.export_car(), headers: CAR });

// the knock: the host's nonce, signed by a pen, naming the repo it wants.
// `iss` names the identity the pen speaks for through a delegation record;
// omitted, the pen speaks only for itself (the owner's own pull).
async function knock(name, pen, penDid, { iss, repo = twin } = {}) {
  const challenge = await (await fetch(`${peerUrl}/xrpc/dev.fastatp.auth.getChallenge`)).json();
  const sig = await sign_challenge(pen, challenge.nonce, repo);
  const params = new URLSearchParams({ did: repo, pen: penDid, nonce: challenge.nonce, sig });
  if (iss) params.set("iss", iss);
  const response = await fetch(`${peerUrl}/xrpc/com.atproto.sync.getRepo?${params}`);
  report.phases[name] = response.status;
  return response;
}

async function saveCar(response, name) {
  const car = Buffer.from(await response.arrayBuffer());
  const carPath = path.join(outDir, `${name}.car`);
  await writeFile(carPath, car);
  report.files[name] = carPath;
  return car;
}

// --- 1. connection zero: everything minted client-side -------------------------
// the app mints the pen first and keeps the handle ("keep me signed in" is
// storing this in IndexedDB); connection zero signs up with it
const pen1 = await generate_pen();
const d1 = await connection_zero(domain, pen1);
const twin = d1.twin_did;
report.twin_did = twin;
report.pen_did = d1.pen_did;
report.rotation_secret = d1.rotation_secret; // test artifact: the value to grep FOR
const peer = d1.take_peer();

// --- 2. shelf the chain, then sign up -------------------------------------------
await send("shelf-put-chain", `${shelfUrl}/xrpc/dev.fastatp.shelf.putChain`, {
  body: d1.chain_json,
  headers: { "content-type": "application/json" },
});
await send("signup", `${peerUrl}/signup?did=${encodeURIComponent(twin)}`, {
  body: d1.genesis_car,
  headers: CAR,
});

// --- 3. client-side writes, then push the pen-signed CAR ------------------------
const pen2 = await generate_pen(); // "device 2": a phone
const pen2Did = await pen_did(pen2);
report.pen2_did = pen2Did;
const pen3 = await generate_pen(); // "device 3": a laptop, left enrolled until recovery
const pen3Did = await pen_did(pen3);
report.pen3_did = pen3Did;

await peer.put_record(
  "com.example.hosted.note",
  "note-1",
  JSON.stringify({ $type: "com.example.hosted.note", text: "hello hosted world", n: 1 }),
);
for (const [rkey, subject] of [
  ["pen2", pen2Did],
  ["pen3", pen3Did],
]) {
  await peer.put_record(
    "dev.fastatp.auth.delegation",
    rkey,
    JSON.stringify({ $type: "dev.fastatp.auth.delegation", issuer: twin, subject }),
  );
}
const firstCar = peer.export_car(); // kept to replay later
await pushCar("push-1", peer);

// device 1 reads its own repo back as the owner: no capability record, no
// delegation, no `iss` — the pen that writes the repo is the pen asking
await knock("owner-pull-device1", pen1, d1.pen_did);

// each device writes its OWN repo (its DID is its pen); the identity's repo
// only names it. device 2 creates one, pushes it (the host verifies the CAR
// against the DID itself and hosts it on first push), reads it back as its
// owner, and device 1's pen — the identity's main pen — is refused it
const device2 = await BrowserPeer.create(pen2Did, pen2);
await device2.put_record(
  "com.example.hosted.note",
  "d2-1",
  JSON.stringify({ $type: "com.example.hosted.note", text: "written on device 2", n: 1 }),
);
await pushCar("push-device2-repo", device2, pen2Did);
await knock("owner-pull-device2-repo", pen2, pen2Did, { repo: pen2Did });
await knock("stranger-pull-device2-repo", pen1, d1.pen_did, { repo: pen2Did });

// --- 4. device 2 knocks as the identity and syncs ---------------------------------
const sync = await knock("sync-device2", pen2, pen2Did, { iss: twin });
if (sync.status === 200) await saveCar(sync, "device2-sync");

// --- 5. a stranger pen (valid knock, no delegation) is refused -------------------
const stranger = await generate_pen();
await knock("sync-stranger", stranger, await pen_did(stranger));

// --- 6. severance: absence IS revocation ------------------------------------------
await peer.delete_record("dev.fastatp.auth.delegation", "pen2");
await pushCar("push-severance", peer);
await knock("sync-device2-severed", pen2, pen2Did, { iss: twin });
// a replay of the earlier, correctly signed checkpoint must not undo the
// severance: the host refuses a CAR whose rev is not newer than its head
await send("push-replay", `${peerUrl}/push-car?did=${encodeURIComponent(twin)}`, { body: firstCar, headers: CAR });
await knock("sync-device2-after-replay", pen2, pen2Did, { iss: twin });

// --- 7. recovery: every device is lost; the paper secret and a new pen ------------
// a fresh browser: nothing in IndexedDB, so it mints a pen that is nobody yet
const newPen = await generate_pen();
const newPenDid = await pen_did(newPen);
report.new_pen_did = newPenDid;
// the user types the paper secret; the page checks it against the chain's
// rotation key before anything is done with it (nothing leaves the page)
report.typed_back_ok = rotation_did_from_secret(d1.rotation_secret) === d1.rotation_did;
// turn the chain: a new page naming the new pen, signed by the paper secret
const rotated = await rotate_pen(d1.chain_json, d1.rotation_secret, newPen);
report.rotated_chain_pages = JSON.parse(rotated).chain.length;
await send("recovery-put-chain", `${shelfUrl}/xrpc/dev.fastatp.shelf.putChain`, {
  body: rotated,
  headers: { "content-type": "application/json" },
});
// the new pen pulls the repo as its owner: the host's rule reads the chain
// head, which names the new pen before it has signed anything
const pull = await knock("recovery-pull", newPen, newPenDid);
if (pull.status === 200) {
  const car = await saveCar(pull, "recovery-pull");
  // restore, verifying the head against the OLD pen (it signed that commit),
  // and sign on with the new one; the next commit sorts after the restored rev
  const restored = await BrowserPeer.restore(car, newPen, d1.pen_did);
  report.restored_rev = restored.rev();
  // prune the devices that were lost with the old pen, write, push
  await restored.delete_record("dev.fastatp.auth.delegation", "pen3");
  await restored.put_record(
    "com.example.hosted.note",
    "note-recovered",
    JSON.stringify({ $type: "com.example.hosted.note", text: "back, on a new pen", n: 2 }),
  );
  await pushCar("recovery-push", restored);
  report.recovered_rev = restored.rev();
}
// the old pen is out: it can neither pull as the owner nor push a commit
await knock("old-pen-pull", pen1, d1.pen_did);
await peer.put_record(
  "com.example.hosted.note",
  "note-stale",
  JSON.stringify({ $type: "com.example.hosted.note", text: "signed by the lost pen", n: 3 }),
);
await pushCar("old-pen-push", peer);
// and the pruned device's delegation is gone with it
await knock("sync-device3-pruned", pen3, pen3Did, { iss: twin });

await writeFile(path.join(outDir, "report.json"), JSON.stringify(report, null, 2));
console.log(JSON.stringify(report));
