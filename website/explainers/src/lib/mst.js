// The repo's tree, by the atproto rule, small enough to run in a page.
// The same steps as fastatp/repo/mst.py: a key's layer is the number of
// leading zero bit pairs in sha256(key), and the tree is rebuilt from the
// sorted keys. Nothing but the keys goes in, so the same keys always give
// the same tree.

const K = (() => {
  // the first 32 bits of the fractional parts of the cube roots of the first 64 primes
  const k = [];
  for (let n = 2; k.length < 64; n++) {
    let prime = true;
    for (let i = 2; i * i <= n; i++) if (n % i === 0) { prime = false; break; }
    if (prime) k.push(((Math.cbrt(n) % 1) * 4294967296) | 0);
  }
  return k;
})();

/** SHA-256 of a string's UTF-8 bytes, as 32 bytes. Synchronous, so a tree builds in one go. */
export function sha256(text) {
  const src = new TextEncoder().encode(text);
  const len = ((src.length + 9 + 63) >> 6) << 6;
  const bytes = new Uint8Array(len);
  bytes.set(src);
  bytes[src.length] = 0x80;
  const view = new DataView(bytes.buffer);
  view.setUint32(len - 4, src.length * 8);
  let h = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19];
  const w = new Int32Array(64);
  const rot = (x, n) => (x >>> n) | (x << (32 - n));
  for (let o = 0; o < len; o += 64) {
    for (let i = 0; i < 16; i++) w[i] = view.getInt32(o + i * 4);
    for (let i = 16; i < 64; i++) {
      const a = w[i - 15];
      const c = w[i - 2];
      w[i] = (w[i - 16] + (rot(a, 7) ^ rot(a, 18) ^ (a >>> 3)) + w[i - 7] + (rot(c, 17) ^ rot(c, 19) ^ (c >>> 10))) | 0;
    }
    let [a, b, c, d, e, f, g, hh] = h;
    for (let i = 0; i < 64; i++) {
      const t1 = (hh + (rot(e, 6) ^ rot(e, 11) ^ rot(e, 25)) + ((e & f) ^ (~e & g)) + K[i] + w[i]) | 0;
      const t2 = ((rot(a, 2) ^ rot(a, 13) ^ rot(a, 22)) + ((a & b) ^ (a & c) ^ (b & c))) | 0;
      hh = g; g = f; f = e; e = (d + t1) | 0; d = c; c = b; b = a; a = (t1 + t2) | 0;
    }
    h = [h[0] + a, h[1] + b, h[2] + c, h[3] + d, h[4] + e, h[5] + f, h[6] + g, h[7] + hh].map((x) => x | 0);
  }
  const out = new Uint8Array(32);
  const outView = new DataView(out.buffer);
  h.forEach((x, i) => outView.setInt32(i * 4, x));
  return out;
}

/** A hash as its bit pairs, first pair first: each is 0, 1, 2 or 3. */
export function pairs(digest) {
  const out = [];
  for (const byte of digest) for (let shift = 6; shift >= 0; shift -= 2) out.push((byte >> shift) & 3);
  return out;
}

/** A key's layer: how many zero pairs its hash starts with. */
export function layerOf(key) {
  const p = pairs(sha256(key));
  let n = 0;
  while (n < p.length && p[n] === 0) n++;
  return n;
}

const ALPHABET = '234567abcdefghijklmnopqrstuvwxyz';

/** A record key as FastATP makes one: microseconds since 1970, then a 10-bit clock id. */
export function tid(micros, clock) {
  let value = (BigInt(micros) << 10n) | BigInt(clock);
  let text = '';
  for (let i = 0; i < 13; i++) {
    text = ALPHABET[Number(value & 31n)] + text;
    value >>= 5n;
  }
  return text;
}

function build(items, layer) {
  const node = { layer, entries: [], kids: [] }; // kids[0] is left of every entry; kids[j] is right of entry j-1
  const below = (from, to) => (from === to ? null : build(items.slice(from, to), layer - 1));
  let start = 0;
  items.forEach((item, i) => {
    if (item.layer !== layer) return;
    node.kids.push(below(start, i));
    node.entries.push(item);
    start = i + 1;
  });
  node.kids.push(items.length ? below(start, items.length) : null);
  return node;
}

/** The tree for items sorted by key, each `{ key, layer, i }` with `i` its place in that order. */
export function tree(items) {
  const root = build(items, Math.max(0, ...items.map((item) => item.layer)));
  reach(root);
  return root;
}

function reach(node) {
  // lo and hi: the first and last place, in key order, of anything under this node
  node.lo = Infinity;
  node.hi = -1;
  for (const entry of node.entries) {
    node.lo = Math.min(node.lo, entry.i);
    node.hi = Math.max(node.hi, entry.i);
  }
  for (const kid of node.kids) {
    if (!kid) continue;
    reach(kid);
    node.lo = Math.min(node.lo, kid.lo);
    node.hi = Math.max(node.hi, kid.hi);
  }
}

/** Every node of a tree, top first. */
export function nodes(node, out = []) {
  if (!node) return out;
  out.push(node);
  node.kids.forEach((kid) => nodes(kid, out));
  return out;
}

/** The walk from the top node to the record at place `i`: one step per node read. */
export function pathTo(root, i) {
  const steps = [];
  let node = root;
  while (node) {
    const before = node.entries.filter((entry) => entry.i < i).length;
    const found = node.entries.some((entry) => entry.i === i);
    steps.push({ node, before, found });
    if (found) break;
    node = node.kids[before];
  }
  return steps;
}

/** `count` records written one after another by one peer, in key order. */
export function writeRecords(collection, count, gapMicros = 4_000_000) {
  const clock = Math.floor(Math.random() * 1024);
  let t = Date.now() * 1000;
  const items = [];
  for (let i = 0; i < count; i++) {
    t += 1000 + Math.floor(Math.random() * gapMicros);
    const rkey = tid(t, clock);
    const key = `${collection}/${rkey}`;
    items.push({ i, rkey, key, layer: layerOf(key) });
  }
  return items;
}
