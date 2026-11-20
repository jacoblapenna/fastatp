<script>
  // The conductor's baton. Every press of Next is "you typing the guide's
  // next command"; the conductor signs it into its own repo and cues the
  // players. Back looks at an earlier caption or key concept again without
  // touching the machines: what ran, ran. The keys live in App, which knows
  // when a key concept has them.
  import { live, view, canBack, canNext, stepBack, stepNext } from '../lib/live.svelte.js';

  let total = $derived(live.manifest?.steps.length ?? 0);
  let busy = $derived(live.mode === 'playing' || live.mode === 'step'); // a press still being performed
</script>

<div class="controls" data-anchor="controls">
  <button onclick={stepBack} disabled={!canBack()} title="look at the previous step again (←)">◀ Back</button>
  <button class="next" onclick={stepNext} disabled={!canNext()} title="the next step (→ or Enter)">
    {live.cursor < 0 ? 'Start ▶' : view.back === 0 && busy ? '…' : 'Next ▶'}
  </button>
  <div class="progress" title="step {live.cursor + 1} of {total}">
    <div style="width: {total ? ((live.cursor + 1) / total) * 100 : 0}%"></div>
  </div>
  <span class="dot" class:on={live.connected} title={live.connected ? 'connected' : 'reconnecting'}></span>
</div>

<style>
  .controls { display: flex; gap: 8px; align-items: center; }
  button:disabled { opacity: 0.4; cursor: default; }
  .next { background: var(--color-primary); color: var(--color-on-primary); border-color: var(--color-primary); font-weight: 600; min-width: 84px; }
  .progress { width: 140px; height: 6px; background: var(--color-surface-raised); border-radius: 3px; overflow: hidden; }
  .progress div { height: 100%; background: var(--color-primary); transition: width 0.3s; }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--color-danger); }
  .dot.on { background: var(--color-verified); }
</style>
