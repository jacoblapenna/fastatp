<script>
  // A repo of many records, and the walk from its top node to one of them.
  // Drawn on a canvas because a couple of thousand records is too many
  // elements; everything it says is also in the list of steps under it.
  import { onDestroy } from 'svelte';
  import { theme } from '$theme';
  import { nodes, pathTo, tree, writeRecords } from '../lib/mst.js';

  const COLLECTION = 'com.example.tutorial.note';
  const still = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const LEFT = 40, RIGHT = 14, ROW = 44, TALL = 16, TOP = 14;

  let power = $state(8); // the repo holds 2 ** power records
  let target = $state(99); // the place, in key order, of the record to find
  let root = $state.raw(tree(writeRecords(COLLECTION, 256)));
  let step = $state(0);
  let wide = $state(0);
  let canvas;
  let timer;

  const count = $derived(2 ** power);
  const all = $derived(nodes(root));
  const path = $derived(pathTo(root, target));
  const shown = $derived(path.slice(0, step));
  const done = $derived(step >= path.length);
  const looked = $derived(path.reduce((sum, s) => sum + s.node.entries.length, 0));
  const lengths = $derived.by(() => {
    const by = {};
    for (let i = 0; i < count; i++) {
      const n = pathTo(root, i).length;
      by[n] = (by[n] ?? 0) + 1;
    }
    return Object.entries(by).map(([n, records]) => ({ n: Number(n), records })).sort((a, b) => a.n - b.n);
  });

  function walk() {
    clearInterval(timer);
    step = still ? Infinity : 0;
    if (still) return;
    timer = setInterval(() => {
      step += 1;
      if (step >= path.length) clearInterval(timer);
    }, 600);
  }
  function aim(place) {
    target = Math.min(Math.max(0, Math.round(place) || 0), count - 1);
    walk();
  }
  function rekey() {
    root = tree(writeRecords(COLLECTION, count));
    walk();
  }
  function resize() {
    target = Math.min(target, count - 1);
    rekey();
  }
  function click(event) {
    const rect = canvas.getBoundingClientRect();
    aim(((event.clientX - rect.left - LEFT) / (rect.width - LEFT - RIGHT)) * (count - 1));
  }
  onDestroy(() => clearInterval(timer));
  walk();

  const ordinal = (n) => n + (n % 10 === 1 && n % 100 !== 11 ? 'st' : n % 10 === 2 && n % 100 !== 12 ? 'nd' : n % 10 === 3 && n % 100 !== 13 ? 'rd' : 'th');
  function gap(s) {
    const n = s.node.entries.length;
    if (s.before === 0) return 'before the first entry';
    if (s.before === n) return 'after the last entry';
    return `between the ${ordinal(s.before)} and ${ordinal(s.before + 1)} entries`;
  }

  $effect(() => {
    theme.current; // the canvas reads the theme's colors, so a switch redraws it
    if (!canvas || !wide) return;
    const top = root.layer;
    const high = TOP + (top + 1) * ROW + 30;
    const ratio = window.devicePixelRatio || 1;
    canvas.width = wide * ratio;
    canvas.height = high * ratio;
    canvas.style.height = `${high}px`;
    const g = canvas.getContext('2d');
    g.setTransform(ratio, 0, 0, ratio, 0, 0);
    g.clearRect(0, 0, wide, high);
    const css = getComputedStyle(document.documentElement);
    const color = (name) => css.getPropertyValue(name).trim();
    const MUTED = color('--color-text-muted'), BORDER = color('--color-border'), MARK = color('--color-primary'), TEXT = color('--color-text');

    const x = (i) => (count === 1 ? (LEFT + wide - RIGHT) / 2 : LEFT + (i * (wide - LEFT - RIGHT)) / (count - 1));
    const y = (layer) => TOP + (top - layer) * ROW;
    const box = (n) => {
      if (n.entries.length) return [x(n.entries[0].i) - 4, x(n.entries.at(-1).i) + 4];
      const mid = (x(n.lo) + x(n.hi)) / 2;
      return [mid - 4, mid + 4];
    };
    const seen = new Set(shown.map((s) => s.node));

    g.font = '11px system-ui, sans-serif';
    g.fillStyle = MUTED;
    g.textBaseline = 'middle';
    for (let layer = top; layer >= 0; layer--) g.fillText(`L${layer}`, 8, y(layer) + TALL / 2);

    for (const hotPass of [false, true]) {
      // the walk's lines last, so they sit on top
      for (const n of all) {
        const [a, b] = box(n);
        for (const kid of n.kids) {
          if (!kid || (seen.has(n) && seen.has(kid)) !== hotPass) continue;
          const [ka, kb] = box(kid);
          const kx = (ka + kb) / 2;
          g.beginPath();
          g.moveTo(Math.min(Math.max(kx, a), b), y(n.layer) + TALL);
          g.lineTo(kx, y(kid.layer));
          g.strokeStyle = hotPass ? MARK : BORDER;
          g.lineWidth = hotPass ? 2 : 1;
          g.stroke();
        }
      }
    }
    for (const n of all) {
      const [a, b] = box(n);
      const hot = seen.has(n);
      g.beginPath();
      if (g.roundRect) g.roundRect(a, y(n.layer), b - a, TALL, 4);
      else g.rect(a, y(n.layer), b - a, TALL);
      if (hot) {
        g.globalAlpha = 0.28;
        g.fillStyle = MARK;
        g.fill();
        g.globalAlpha = 1;
      }
      g.setLineDash(n.entries.length ? [] : [3, 3]);
      g.strokeStyle = hot ? MARK : MUTED;
      g.lineWidth = hot ? 2 : 1;
      g.stroke();
      g.setLineDash([]);
      for (const entry of n.entries) {
        const mine = hot && entry.i === target;
        g.beginPath();
        g.moveTo(x(entry.i), y(n.layer) + 3);
        g.lineTo(x(entry.i), y(n.layer) + TALL - 3);
        g.strokeStyle = mine ? TEXT : hot ? MARK : MUTED;
        g.lineWidth = mine ? 3 : 1;
        g.stroke();
      }
    }

    // the ruler along the bottom: every record in the order written, and the one we want
    const ruler = high - 18;
    const tx = x(target);
    g.beginPath();
    g.moveTo(x(0), ruler);
    g.lineTo(x(count - 1), ruler);
    g.strokeStyle = BORDER;
    g.lineWidth = 1;
    g.stroke();
    g.beginPath();
    g.setLineDash([2, 4]);
    g.moveTo(tx, TOP);
    g.lineTo(tx, ruler - 6);
    g.strokeStyle = MARK;
    g.stroke();
    g.setLineDash([]);
    g.beginPath();
    g.moveTo(tx, ruler - 1);
    g.lineTo(tx - 5, ruler - 9);
    g.lineTo(tx + 5, ruler - 9);
    g.closePath();
    g.fillStyle = MARK;
    g.fill();
    g.fillStyle = MUTED;
    g.textBaseline = 'alphabetic';
    g.textAlign = 'left';
    g.fillText('first written', x(0), high - 4);
    g.textAlign = 'right';
    g.fillText('last written', x(count - 1), high - 4);
    g.textAlign = 'left';
  });
</script>

<div class="lab">
  <div class="controls">
    <label class="field">
      Records
      <input type="range" min="3" max="11" step="1" bind:value={power} oninput={resize} />
      <output>{count}</output>
    </label>
    <label class="field">
      Find record #
      <input type="number" min="1" max={count} value={target + 1} onchange={(e) => aim(Number(e.currentTarget.value) - 1)} />
    </label>
  </div>
  <div class="controls">
    <button class="primary" onclick={rekey}>New keys, same record</button>
    <button onclick={() => aim(Math.floor(Math.random() * count))}>Pick a random record</button>
    <button onclick={walk}>Replay the walk</button>
  </div>

  <figure>
    <div class="canvas" bind:clientWidth={wide}>
      <canvas bind:this={canvas} onclick={click} aria-label="A tree of {count} records, with the walk to record {target + 1} in orange"></canvas>
    </div>
    <figcaption>
      Each outline is a tree node and each tick inside it is a record, placed left to right in the
      order written. Orange is the walk. Click anywhere on the tree to walk to the record at that
      position.
    </figcaption>
  </figure>

  <ol class="steps" aria-live="polite">
    {#each shown as s}
      {@const n = s.node.entries.length}
      {@const where = `Layer ${s.node.layer}, ${n ? `${n} ${n > 1 ? 'entries' : 'entry'}` : 'an empty node'}`}
      {#if s.found}
        <li class="found">{where}: record #{target + 1} is here.</li>
      {:else if !n}
        <li>{where}: only one way down.</li>
      {:else}
        <li>{where}: our key sorts {gap(s)}, so take that pointer down.</li>
      {/if}
    {/each}
  </ol>
  <div class="verdict">
    {#if done}
      Read {path.length} of {all.length} tree nodes, holding {looked} of the {count} keys. The other
      {all.length - path.length} nodes were never touched.
    {/if}
  </div>

  <div>
    <strong class="head">This tree: {all.length} tree nodes for {count} records. How long is the walk to each record?</strong>
    <div class="tally">
      {#each lengths as l (l.n)}
        <div class="row">
          <span>{l.n} node{l.n > 1 ? 's' : ''} read</span>
          <span class="bar"><i style:width="{(100 * l.records) / count}%"></i></span>
          <span class="n">{l.records} · {Math.round((100 * l.records) / count)}%</span>
        </div>
      {/each}
    </div>
  </div>
</div>

<style>
  .field { display: inline-flex; align-items: center; gap: 8px; font-size: 14px; color: var(--color-text-soft); }
  .field input[type='number'] { width: 84px; font: 14px var(--font-mono); padding: 7px 8px; border-radius: var(--radius-md); border: 1px solid var(--color-border); background: var(--color-bg); color: var(--color-text); }
  .field input[type='range'] { accent-color: var(--color-primary); width: 130px; }
  .field output { font-family: var(--font-mono); min-width: 4ch; color: var(--color-text); }
  .canvas { background: var(--color-bg); border: 1px solid var(--color-border-subtle); border-radius: var(--radius-lg); min-width: 0; }
  canvas { display: block; width: 100%; cursor: crosshair; border-radius: var(--radius-lg); }
  .steps { margin: 0; padding-left: 22px; display: flex; flex-direction: column; gap: 4px; font-size: 14px; color: var(--color-text-soft); min-height: 5em; }
  .found { color: var(--color-text); font-weight: 600; }
  .verdict { font-size: 15px; color: var(--color-text); min-height: 1.6em; }
  .head { font-size: 14px; display: block; margin-bottom: 10px; }
</style>
