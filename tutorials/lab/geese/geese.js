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
  // The rotation secret is the paper backup that can replace a lost pen. A
  // real app shows it once, for a drawer; this one keeps no copy and sends none.
  // key: the pen's handle, kept to sign with in this tab (a knock to read your own
  // repo back); a WebCrypto key made unexportable, so it can't be sent even by mistake.
  const me = { did: born.twin_did, pen: born.pen_did, key: pen, peer: born.take_peer(), base };
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
