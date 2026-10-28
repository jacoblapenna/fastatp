// The hosted-peer client: the shipped wasm bundle driven exactly as a browser
// app would drive it (Node 22 has crypto.subtle / fetch — same artifact).
//
//   node client.mjs <peer_url> <shelf_url> <out_dir> [domain]
//
// Runs the whole HOSTED-PEERS.md lifecycle and writes a JSON report plus a
// copy of EVERY http body it ever sent (the no-secret audit trail):
//   1. connection zero (client-side): pens, paper root, twin-only genesis
//   2. shelf the chain; sign up (POST the genesis CAR)
//   3. write records client-side: self-capability, content, and a delegation
//      for "device 2" — then push the pen-signed CAR (the router)
//   4. device 2 passes the browser gate via signed challenge and syncs
//   5. a stranger pen with a valid challenge is refused
//   6. device 1 severs the delegation (deletes the record) and pushes;
//      device 2's next connect is refused: absence IS revocation
//
// "Device 2" is a second WebCrypto keypair in this process, minted through a
// second connection_zero bundle so its challenge signatures get the same
// P1363 + low-S handling as any pen (its unused twin/chain are discarded).
// On real hardware the enrollment ceremony is two devices exchanging exactly
// the two strings you see here: pen2's did:key one way, the delegation
// record's presence the other.

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
const { connection_zero } = glue;

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

// --- 1. connection zero: everything minted client-side -------------------------
const d1 = await connection_zero(domain);
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
  headers: { "content-type": "application/vnd.ipld.car" },
});

// --- 3. client-side writes, then push the pen-signed CAR ------------------------
const d2 = await connection_zero(domain); // "device 2": we use only its pen
report.pen2_did = d2.pen_did;
const device2 = d2.take_peer();

await peer.put_record(
  "dev.fastatp.auth.capability",
  "self",
  JSON.stringify({ $type: "dev.fastatp.auth.capability", subject: twin, actions: ["subscribe"] }),
);
await peer.put_record(
  "com.example.hosted.note",
  "note-1",
  JSON.stringify({ $type: "com.example.hosted.note", text: "hello hosted world", n: 1 }),
);
await peer.put_record(
  "dev.fastatp.auth.delegation",
  "pen2",
  JSON.stringify({ $type: "dev.fastatp.auth.delegation", issuer: twin, subject: d2.pen_did }),
);
await send("push-1", `${peerUrl}/push-car?did=${encodeURIComponent(twin)}`, {
  body: peer.export_car(),
  headers: { "content-type": "application/vnd.ipld.car" },
});

// --- 4. device 2 passes the gate and syncs ---------------------------------------
async function gatedGetRepo(name, device, penDid) {
  const challenge = await (await fetch(`${peerUrl}/xrpc/dev.fastatp.auth.getChallenge`)).json();
  const sig = await device.sign_challenge(challenge.nonce);
  const params = new URLSearchParams({ did: twin, pen: penDid, iss: twin, nonce: challenge.nonce, sig });
  const response = await fetch(`${peerUrl}/xrpc/com.atproto.sync.getRepo?${params}`);
  report.phases[name] = response.status;
  return response;
}

const sync = await gatedGetRepo("sync-device2", device2, d2.pen_did);
if (sync.status === 200) {
  const car = Buffer.from(await sync.arrayBuffer());
  const carPath = path.join(outDir, "device2-sync.car");
  await writeFile(carPath, car);
  report.files.device2_sync_car = carPath;
}

// --- 5. a stranger pen (valid challenge, no delegation) is refused ---------------
const stranger = await connection_zero(domain);
await gatedGetRepo("sync-stranger", stranger.take_peer(), stranger.pen_did);

// --- 6. severance: absence IS revocation ------------------------------------------
await peer.delete_record("dev.fastatp.auth.delegation", "pen2");
await send("push-severance", `${peerUrl}/push-car?did=${encodeURIComponent(twin)}`, {
  body: peer.export_car(),
  headers: { "content-type": "application/vnd.ipld.car" },
});
await gatedGetRepo("sync-device2-severed", device2, d2.pen_did);

await writeFile(path.join(outDir, "report.json"), JSON.stringify(report, null, 2));
console.log(JSON.stringify(report));
