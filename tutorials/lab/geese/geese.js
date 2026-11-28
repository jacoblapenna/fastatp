// You, in a browser tab: an identity whose pen is minted here and never leaves.
//
// The same module runs in the page (index.html) and under Node, which has
// the same WebCrypto and fetch a browser has. It takes the browser peer it
// drives as an argument, so each side loads that its own way.
//
// What crosses the wire, all of it: a chain of public DID documents to the
// shelf, and repos whose every commit is signed here. No password, no token,
// no key. The host can drop or delay what it is sent; it can't forge it.

// A record key that sorts by time: microseconds since 1970, and a random
// clock id, in atproto's sortable base32. Two keys made in the same tab never
// collide, because the microseconds only move forward.
const S32 = '234567abcdefghijklmnopqrstuvwxyz';
const CLOCK = Math.floor(Math.random() * 1024);
let last = 0;
export function tid() {
  last = Math.max(last + 1, Date.now() * 1000);
  let n = (BigInt(last) << 10n) | BigInt(CLOCK);
  let key = '';
  for (let i = 0; i < 13; i++, n >>= 5n) key = S32[Number(n & 31n)] + key;
  return key;
}

// base: where the app's host is, ending in a slash ("http://geese.lan/").
// The page passes its own; nothing here names a host.
async function post(base, path, body, type) {
  const response = await fetch(new URL(path, base), { method: 'POST', body, headers: { 'content-type': type } });
  if (!response.ok) throw new Error(`${path}: ${response.status} ${await response.text()}`);
  return response.json();
}
const CAR = 'application/vnd.ipld.car';

// Connection zero: a new identity, made entirely in this tab, then housed.
// The pen is a WebCrypto key that can't be exported; this tab holds a handle
// to it, and it signs here or nowhere.
export async function signUp(fastatp, base) {
  const home = await (await fetch(new URL('whoami', base))).json();
  const domain = home.did.replace('did:web:', ''); // the host your repo lives on
  const pen = await fastatp.generate_pen();
  const born = await fastatp.connection_zero(domain, pen);
  // key: the pen's handle, kept to sign with in this tab (a knock to read your own
  // repo back); a WebCrypto key made unexportable, so it can't be sent even by mistake.
  const me = { did: born.twin_did, pen: born.pen_did, key: pen, peer: born.take_peer(), base, devices: {} };
  // The rotation secret is the paper backup that can replace a lost pen. It
  // is handed back once, for the page to show once; nothing keeps or sends it.
  me.paper = born.rotation_secret;
  await post(base, 'xrpc/dev.fastatp.shelf.putChain', born.chain_json, 'application/json');
  await post(base, `signup?did=${encodeURIComponent(me.did)}`, born.genesis_car, CAR);
  return me;
}

// One record, signed here, then the whole repo pushed home for the host to check.
export async function write(me, record, rkey = tid()) {
  const collection = record.$type;
  await me.peer.put_record(collection, rkey, JSON.stringify(record));
  const { accepted } = await post(
    me.base,
    `push-car?did=${encodeURIComponent(me.did)}`,
    me.peer.export_car(),
    CAR,
  );
  return { uri: `at://${me.did}/${collection}/${rkey}`, rev: accepted };
}

// --- logging in: a signature on a one-time challenge, nothing else ---------

// Read a repo as its owner, or as a device the owner let in: ask the host for
// a one-time challenge, sign it with this tab's pen, and send the signature
// with the request. No password, no token: the signature is good for this one
// request and nothing else. `iss` is the account the pen speaks for, when the
// pen is a second device's rather than the account's own.
export async function knock(fastatp, base, { did, pen, key, iss }) {
  const { nonce } = await (await fetch(new URL('xrpc/dev.fastatp.auth.getChallenge', base))).json();
  const sig = await fastatp.sign_challenge(key, nonce, did);
  const params = new URLSearchParams({ did, pen, nonce, sig });
  if (iss) params.set('iss', iss);
  return fetch(new URL(`xrpc/com.atproto.sync.getRepo?${params}`, base));
}

// The account's own pen logs in: it reads its repo back, checks the commit
// was signed by itself, and can write again from where it left off.
export async function logIn(fastatp, base, saved) {
  const answer = await knock(fastatp, base, { did: saved.did, pen: saved.pen, key: saved.key });
  if (!answer.ok) throw Object.assign(new Error(`the host said no (${answer.status})`), { status: answer.status });
  const car = new Uint8Array(await answer.arrayBuffer());
  const peer = await fastatp.BrowserPeer.restore(car, saved.key, saved.pen);
  return { ...saved, peer, base };
}

// --- a second device: its own pen, let in by a record ---------------------

// Approve another device: a record in the account's repo saying "this pen
// speaks for me". Its key never came here; only its public name did.
export async function addDevice(me, penDid) {
  const rkey = tid();
  const record = { $type: 'dev.fastatp.auth.delegation', issuer: me.did, subject: penDid };
  // kept before the push: if the push fails, the record is still in this
  // repo and goes with the next one, so its Remove button must exist
  me.devices[penDid] = rkey;
  await write(me, record, rkey);
}

// Sign a device out everywhere: delete the record. Absence is revocation.
export async function removeDevice(me, penDid) {
  const rkey = me.devices[penDid];
  if (!rkey) return;
  await me.peer.delete_record('dev.fastatp.auth.delegation', rkey);
  await post(me.base, `push-car?did=${encodeURIComponent(me.did)}`, me.peer.export_car(), CAR);
  delete me.devices[penDid];
}

// --- keep me signed in: this device remembers its pen ---------------------

// The pen is a CryptoKey the page can't read the bytes of; IndexedDB can keep
// the handle itself. Keeping it is a choice the app makes on purpose: a pen
// left in a browser is that browser being you until it is removed.
function store(mode, use) {
  return new Promise((resolve, reject) => {
    const open = indexedDB.open('geese', 1);
    open.onupgradeneeded = () => open.result.createObjectStore('me');
    open.onerror = () => reject(open.error);
    open.onsuccess = () => {
      const tx = open.result.transaction('me', mode);
      const request = use(tx.objectStore('me'));
      tx.oncomplete = () => resolve(request?.result);
      tx.onerror = () => reject(tx.error);
    };
  });
}
export const remember = (me) =>
  store('readwrite', (s) => s.put({ did: me.did, pen: me.pen, key: me.key, devices: me.devices }, 'me'));
export const recall = () => store('readonly', (s) => s.get('me'));
export const forget = () => store('readwrite', (s) => s.delete('me'));
