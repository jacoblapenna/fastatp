<script>
  // The LAN as an outsider sees it: every packet the machines put on the
  // cable. Plain http is readable by anyone on the path; WireGuard is sizes,
  // timing and nothing else. Folded to its counts until Part 4 asks for it,
  // the score points at it, or the viewer opens it.
  import { live, nameOf } from '../lib/live.svelte.js';

  let { pointed = false, kept = false } = $props();
  let chosen = $state(false);
  let open = $derived(chosen || pointed || kept);

  let rows = $derived(live.wire.slice(-80).reverse());
  let readable = $derived(live.wire.filter((w) => w.proto === 'http').length);
  let sealed = $derived(live.wire.filter((w) => w.proto === 'wg').length);
</script>

<div class="wire" class:open class:pointed data-anchor="wire">
  <button class="h4" onclick={() => (chosen = !open)} aria-expanded={open}>
    <span class="tri">{open ? '▾' : '▸'}</span> the wire, seen by nobody in particular
    <span class="readable">{readable} readable</span>
    <span class="sealed">{sealed} sealed</span>
  </button>
  {#if open}
    <div class="rows">
      {#each rows as w}
        <div class="row {w.proto}">
          <span class="who">{nameOf({ ip: w.src }) ?? w.src} → {nameOf({ ip: w.dst }) ?? w.dst}</span>
          <span class="badge">{w.proto === 'wg' ? 'WireGuard' : 'http'}</span>
          <span class="kind">{w.kind}</span>
          <span class="len">{w.len}B</span>
          {#if w.text}<div class="text">{w.text}</div>{/if}
        </div>
      {:else}
        <div class="muted">quiet</div>
      {/each}
    </div>
  {/if}
</div>

<style>
  .wire {
    display: flex; flex-direction: column; min-height: 0;
    background: var(--color-surface); border: 1px solid var(--color-border); border-radius: 10px; padding: 8px;
    transition: box-shadow 0.3s;
  }
  .wire.open { height: 100%; }
  .wire.pointed { box-shadow: 0 0 0 2px var(--color-highlight), 0 0 18px color-mix(in srgb, var(--color-highlight) 35%, transparent); }
  .h4 {
    display: flex; gap: 8px; align-items: baseline; width: 100%; margin: 0; padding: 0;
    background: none; border: none; text-align: left; cursor: pointer;
    font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--color-text-muted);
  }
  .h4:hover { color: var(--color-text); }
  .open .h4 { margin-bottom: 4px; }
  .tri { width: 10px; display: inline-block; }
  .h4 span.readable, .h4 span.sealed { text-transform: none; letter-spacing: 0; }
  .readable { color: var(--color-plaintext); }
  .sealed { color: var(--color-sealed); }
  .rows { overflow: auto; flex: 1; font: 11px/1.35 var(--font-mono); }
  .row { display: grid; grid-template-columns: 9em 6.5em 1fr 4em; gap: 6px; padding: 2px 0; border-bottom: 1px solid var(--color-border-subtle); }
  .badge { border-radius: 4px; padding: 0 4px; text-align: center; color: var(--color-on-primary); }
  .http .badge { background: var(--color-plaintext); }
  .wg .badge { background: var(--color-sealed); }
  .who { color: var(--color-text); }
  .kind { color: var(--color-text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .len { color: var(--color-text-muted); text-align: right; }
  .text { grid-column: 1 / -1; color: var(--color-plaintext); opacity: 0.8; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .muted { color: var(--color-text-muted); }
</style>
