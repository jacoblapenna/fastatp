// npm test   (node --test, no dependencies)
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { test } from 'node:test';
import { layerOf, nodes, pathTo, sha256, tid, tree, writeRecords } from './mst.js';

const hex = (bytes) => Buffer.from(bytes).toString('hex');

test('sha256 is the real one', () => {
  for (const text of ['', 'abc', 'com.example.tutorial.note/3mz2b56hcz64m', 'x'.repeat(200)]) {
    assert.equal(hex(sha256(text)), createHash('sha256').update(text).digest('hex'));
  }
});

test('a record key is the one fastatp.tid.encode_tid makes', () => {
  assert.equal(tid(0, 0), '2222222222222');
  assert.equal(tid(1, 0), '2222222222322');
  assert.equal(tid(1795000000000000, 513), '3n2lg2pyg22k3'); // all three from encode_tid itself
});

test('a layer is the count of leading zero pairs', () => {
  for (const key of writeRecords('com.example.tutorial.note', 500).map((r) => r.key)) {
    const digest = createHash('sha256').update(key).digest();
    let bits = '';
    for (const byte of digest) bits += byte.toString(2).padStart(8, '0');
    assert.equal(layerOf(key), Math.floor(bits.indexOf('1') / 2));
  }
});

test('the same keys give the same tree, and every record is found in key order', () => {
  const items = writeRecords('com.example.tutorial.note', 300);
  assert.ok(items.every((item, i) => i === 0 || items[i - 1].key < item.key));
  const shape = (root) => nodes(root).map((n) => `${n.layer}:${n.entries.map((e) => e.i)}`).join('|');
  const root = tree(items);
  assert.equal(shape(root), shape(tree(items)));
  for (const item of items) {
    const walk = pathTo(root, item.i);
    assert.ok(walk.at(-1).found);
    assert.ok(walk.length <= root.layer + 1);
  }
  const inOrder = [];
  (function visit(n) {
    if (!n) return;
    visit(n.kids[0]);
    n.entries.forEach((e, j) => { inOrder.push(e.i); visit(n.kids[j + 1]); });
  })(root);
  assert.deepEqual(inOrder, items.map((item) => item.i));
});
