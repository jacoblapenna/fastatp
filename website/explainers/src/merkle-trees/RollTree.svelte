<script>
  // Three notes, real keys, the real rule. Press for new keys and the tree is
  // built again: the notes keep their left-to-right order, and only their
  // height (and so the number of tree nodes) moves.
  import { layerOf, nodes, pairs, sha256, tid, tree } from '../lib/mst.js';

  const COLLECTION = 'com.example.tutorial.note';
  const NOTES = ['hello, LAN', 'second note', 'third note'];

  function newKeys() {
    const clock = Math.floor(Math.random() * 1024);
    let t = Date.now() * 1000 + Math.floor(Math.random() * 1000);
    return NOTES.map((text, slot) => {
      t += 2_000_000 + Math.floor(Math.random() * 3_000_000);
      const rkey = tid(t, clock);
      const key = `${COLLECTION}/${rkey}`;
      return { text, slot, rkey, key, layer: layerOf(key), pairs: pairs(sha256(key)) };
    });
  }
  const without = (keys, gone) => keys.filter((k) => !(gone && k.slot === 1)).map((k, i) => ({ ...k, i }));

  let run = $state.raw(newKeys());
  let deleted = $state(false);
  let said = $state('This is one run. Press the orange button for another.');
  let tally = $state.raw({});

  const count = (keys) => {
    const n = nodes(tree(keys)).length;
    tally = { ...tally, [n]: (tally[n] ?? 0) + 1 };
  };
  count(without(run, false));

  const keys = $derived(without(run, deleted));
  const root = $derived(tree(keys));
  const all = $derived(nodes(root));
  const runs = $derived(Object.values(tally).reduce((a, b) => a + b, 0));

  // the drawing: a column per note (so order never moves), a row per layer
  const LEFT = 78, COL = 176, CHIP = 156, TALL = 46, ROW = 96, PAD = 8;
  const WIDE = LEFT + 3 * COL + 8;
  const high = $derived(24 + (root.layer + 1) * ROW);
  const cx = (k) => LEFT + k.slot * COL + COL / 2;
  const y = (layer) => 22 + (root.layer - layer) * ROW;
  function box(n) {
    if (n.entries.length) {
      const xs = n.entries.map(cx);
      return { x: Math.min(...xs) - CHIP / 2 - PAD, w: Math.max(...xs) - Math.min(...xs) + CHIP + 2 * PAD };
    }
    const under = nodes(n).flatMap((m) => m.entries).map(cx);
    return { x: (Math.min(...under) + Math.max(...under)) / 2 - 62, w: 124 };
  }
  const links = $derived(
    all.flatMap((n) => {
      const b = box(n);
      return n.kids.filter(Boolean).map((kid) => {
        const kb = box(kid);
        const kx = kb.x + kb.w / 2;
        return { x1: Math.min(Math.max(kx, b.x + 14), b.x + b.w - 14), y1: y(n.layer) + TALL + 2 * PAD, x2: kx, y2: y(kid.layer) };
      });
    }),
  );
  const layers = $derived(Array.from({ length: root.layer + 1 }, (_, i) => root.layer - i));
  const printout = $derived(
    [
      `${keys.length + all.length + 1} blocks`,
      ...keys.map((k) => `  record     '${k.text}'`),
      ...all.map((n) => `  tree node  ${n.entries.length} entries`),
      '  commit',
    ].join('\n'),
  );

  function again() {
    run = newKeys();
    count(without(run, deleted));
    said = 'New run: the clock moved on, so all three keys are new and each rolled its layer again.';
  }
  function same() {
    run = [...run]; // the same keys, built again from nothing
    said = 'Rebuilt from the same keys: the tree is identical. Nothing but the keys goes in.';
  }
  function mode(gone) {
    deleted = gone;
    tally = {};
    count(without(run, gone));
    said = gone
      ? 'Same keys, second note gone: the tree is rebuilt from the two keys that remain.'
      : 'Same keys, all three notes.';
  }
  function many() {
    const next = { ...tally };
    for (let i = 0; i < 500; i++) {
      const n = nodes(tree(without(newKeys(), deleted))).length;
      next[n] = (next[n] ?? 0) + 1;
    }
    tally = next;
    said = '500 more runs counted. The picture still shows your last run.';
  }
</script>

<div class="lab">
  <div class="controls">
    <button class="primary" onclick={again}>Run it again (new keys)</button>
    <button onclick={same}>Rebuild from the same keys</button>
    <span class="seg" role="group" aria-label="Which records are in the repo">
      <button aria-pressed={!deleted} onclick={() => mode(false)}>Three notes</button><button
        aria-pressed={deleted}
        onclick={() => mode(true)}>Second note deleted</button>
    </span>
  </div>
  <div class="said" aria-live="polite">{said}</div>

  <figure>
    <div class="drawing">
      <svg viewBox="0 0 {WIDE} {high}" role="img" aria-label="{all.length} tree nodes holding {keys.length} notes in the order they were written">
        {#each layers as layer (layer)}
          <line class="rule" x1={LEFT - 6} y1={y(layer) + TALL + PAD + 20} x2={WIDE} y2={y(layer) + TALL + PAD + 20} />
          <text class="dim" x="10" y={y(layer) + TALL / 2 + PAD + 4} font-size="12">layer {layer}</text>
        {/each}
        {#each links as l}
          <line class="link" x1={l.x1} y1={l.y1} x2={l.x2} y2={l.y2} />
        {/each}
        {#each all as n}
          {@const b = box(n)}
          <rect class={n.entries.length ? 'node' : 'empty'} x={b.x} y={y(n.layer)} width={b.w} height={TALL + 2 * PAD} rx="8" />
          {#if !n.entries.length}
            <text class="dim" x={b.x + b.w / 2} y={y(n.layer) + TALL / 2 + PAD + 4} text-anchor="middle" font-size="12">empty node</text>
          {/if}
          {#each n.entries as k (k.key)}
            <rect class="chip" x={cx(k) - CHIP / 2} y={y(n.layer) + PAD} width={CHIP} height={TALL} rx="5" />
            <text x={cx(k)} y={y(n.layer) + PAD + 19} text-anchor="middle" font-size="13" font-weight="600">{k.text}</text>
            <text class="dim key" x={cx(k)} y={y(n.layer) + PAD + 36} text-anchor="middle" font-size="11">…/{k.rkey}</text>
          {/each}
        {/each}
      </svg>
    </div>
    <figcaption>
      Each orange outline is one tree node. The notes stay in the same left-to-right order on every
      run; only their height, and so the number of nodes, moves.
    </figcaption>
  </figure>

  <div class="tablewrap">
    <table>
      <thead><tr><th>Note</th><th>Record key</th><th>Its hash starts</th><th>Layer</th></tr></thead>
      <tbody>
        {#each keys as k (k.key)}
          <tr>
            <td>{k.text}</td>
            <td class="mono">{k.rkey}</td>
            <td class="mono">
              {#each k.pairs.slice(0, Math.max(4, k.layer + 2)) as pair, i}<span class={i < k.layer ? 'zero' : 'rest'}
                  >{pair.toString(2).padStart(2, '0')}</span
                >{' '}{/each}<span class="rest">…</span>
            </td>
            <td>{k.layer}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>

  <pre class="printout">{printout}</pre>

  <div>
    <div class="controls head">
      <strong>{runs} run{runs === 1 ? '' : 's'} so far, by number of tree nodes</strong>
      <button onclick={many}>Run 500 more</button>
    </div>
    <div class="tally">
      {#each Object.keys(tally).map(Number).sort((a, b) => a - b) as n (n)}
        <div class="row">
          <span>{n} tree node{n > 1 ? 's' : ''}</span>
          <span class="bar"><i style:width="{(100 * tally[n]) / runs}%"></i></span>
          <span class="n">{tally[n]} · {Math.round((100 * tally[n]) / runs)}%</span>
        </div>
      {/each}
    </div>
  </div>
</div>

<style>
  svg { display: block; width: 100%; height: auto; min-width: 520px; }
  text { fill: var(--color-text); font-family: var(--font-sans); }
  .dim { fill: var(--color-text-muted); }
  .key { font-family: var(--font-mono); }
  .chip { fill: var(--color-surface-raised); stroke: var(--color-border); }
  .node { fill: none; stroke: var(--color-primary); stroke-width: 1.5; }
  .empty { fill: none; stroke: var(--color-text-muted); stroke-dasharray: 4 4; }
  .link { stroke: var(--color-text-muted); }
  .rule { stroke: var(--color-border-subtle); }
  .seg { display: inline-flex; }
  .seg button:first-child { border-radius: var(--radius-md) 0 0 var(--radius-md); }
  .seg button:last-child { border-radius: 0 var(--radius-md) var(--radius-md) 0; margin-left: -1px; }
  .printout { margin: 0; font: 13px/1.5 var(--font-mono); background: var(--color-code-bg); border: 1px solid var(--color-code-border); border-radius: var(--radius-md); padding: 10px 12px; color: var(--color-code-text); white-space: pre-wrap; }
  .head { margin-bottom: 10px; }
  .head strong { font-size: 14px; }
</style>
