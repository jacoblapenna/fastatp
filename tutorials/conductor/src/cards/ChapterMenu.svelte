<script>
  // The chapters, over everything: one card each, a sentence or two on what
  // it covers and the key concepts it carries. Choosing one opens it; a
  // chapter builds on the ones before it, so choosing ahead plays them first,
  // fast and without stops, then opens the one chosen.
  import BrandMark from '../brand/BrandMark.svelte';
  import { live, send } from '../lib/live.svelte.js';

  let { chapters, current, onClose } = $props();
  const count = (n, one, many = one + 's') => `${n} ${n === 1 ? one : many}`;
  let chosen = $state(null); // the number picked, until the conductor opens it

  let busy = $derived(live.mode === 'playing' || live.mode === 'step' || live.concept !== null);
  let hurrying = $derived(live.until !== null);
  let ends = $derived(chapters.map((c, i) => chapters[i + 1]?.start ?? live.manifest?.steps.length ?? 0));

  function standing(c, i) {
    if (c.number === current && !live.done) return 'now';
    return live.cursor >= ends[i] - 1 ? 'done' : 'next';
  }
  function pick(c) {
    // one pick at a time: the conductor takes a chapter only when nothing is running
    if (chosen === null && send('chapter', { value: c.number })) chosen = c.number;
  }
  $effect(() => {
    // the chapter chosen has opened: the menu has done its job
    if (chosen !== null && current === chosen && !hurrying && live.mode !== 'playing') {
      chosen = null;
      onClose();
    }
  });
  function key(e) {
    if (e.key === 'Escape' && live.cursor >= 0) onClose();
  }
</script>

<svelte:window onkeydown={key} />

<div class="overlay">
  <div class="head">
    <div class="skein"><BrandMark variant="skein" size="84px" /></div>
    <div class="title">
      <h1>{live.manifest?.source?.replace('.md', '').replaceAll('_', ' ').toLowerCase() ?? 'the show'}, in {chapters.length} chapters</h1>
      <p>Each chapter builds on the one before. Choose one; the machines play their way to it.</p>
    </div>
    {#if live.cursor >= 0}
      <button class="close" onclick={onClose} aria-label="back to the show">✕</button>
    {/if}
  </div>

  <div class="cards">
    {#each chapters as c, i (c.number)}
      {@const s = standing(c, i)}
      <button class="card {s}" onclick={() => pick(c)} disabled={busy || chosen !== null || !live.connected}>
        <div class="meta">{[c.number, count(c.presses, 'press', 'presses'), count(c.commands, 'command'), c.turns ? count(c.turns, 'turn') + ' in the app' : ''].filter(Boolean).join(' · ')}</div>
        <h2>{c.title}</h2>
        <p class="blurb">{c.blurb}</p>
        <div class="concepts">
          {#each c.concepts as t}<div class="concept">🔑 {t}</div>{/each}
        </div>
        <div class="foot">
          {#if chosen === c.number && hurrying}playing the chapters before it…
          {:else if chosen === c.number}opening…
          {:else if s === 'now'}{live.done ? 'played' : 'playing now · resume'}
          {:else if s === 'done'}played · play again
          {:else}play{/if}
        </div>
      </button>
    {/each}
  </div>
</div>

<style>
  .overlay {
    position: fixed; inset: 0; z-index: 20; background: var(--color-bg);
    display: flex; flex-direction: column; padding: 28px 36px; gap: 20px;
  }
  .head { display: flex; align-items: center; gap: 20px; }
  .title { flex: 1; min-width: 0; }
  .close { align-self: flex-start; }
  h1 { margin: 0; font-size: 26px; }
  .head p { margin: 6px 0 0; color: var(--color-text-muted); }
  .close { padding: 4px 10px; }
  .cards { flex: 1; min-height: 0; display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 16px; }
  .card {
    display: flex; flex-direction: column; gap: 10px; text-align: left; min-height: 0; overflow: auto;
    padding: 20px 22px; border-radius: 14px; border: 1px solid var(--color-border);
    background: var(--color-surface); color: var(--color-text); transition: border-color 0.3s, box-shadow 0.3s;
  }
  .card:hover:not(:disabled) { border-color: var(--color-primary); box-shadow: 0 0 0 1px var(--color-primary); }
  .card:disabled { cursor: default; opacity: 0.7; }
  .card.now { border-color: var(--color-highlight); box-shadow: 0 0 0 1px var(--color-highlight); }
  .card.done { opacity: 0.8; }
  .meta { font: 12px var(--font-mono); color: var(--color-accent-text); }
  h2 { margin: 0; font-size: 21px; line-height: 1.2; }
  .blurb { margin: 0; font-size: 15px; line-height: 1.5; color: var(--color-text-soft); }
  .concepts { margin-top: auto; display: flex; flex-direction: column; gap: 4px; font-size: 13px; color: var(--color-text-muted); }
  .foot { border-top: 1px solid var(--color-border); padding-top: 10px; font-size: 12px; color: var(--color-text-muted); }
  .card.now .foot, .card:hover:not(:disabled) .foot { color: var(--color-text); }
</style>
