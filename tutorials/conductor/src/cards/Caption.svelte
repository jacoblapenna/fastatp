<script>
  // The guide's own words for what is happening now.
  import { md } from '../lib/md.js';

  let { script, back = 0 } = $props();
</script>

<div class="caption" class:reading={script.step?.kind === 'say'} class:back>
  <div class="where">
    {script.part}{#if script.section}<span class="sep">›</span>{script.section}{/if}
    {#if back}<span class="looking">↶ looking back · Next returns to where the machines are</span>{/if}
  </div>
  {#if script.say}<div class="text">{@html md(script.say)}</div>{/if}
  {#if script.step?.kind === 'run' || script.step?.kind === 'restart'}
    <div class="now"><span>{script.step.machine}</span> {script.step.display}</div>
  {:else if script.step?.kind === 'stop'}
    <div class="now"><span>{script.step.machine}</span> Ctrl-C</div>
  {/if}
</div>

<style>
  .caption { padding: 10px 16px; border-bottom: 1px solid var(--color-border); min-height: 86px; transition: background 0.3s; }
  .caption.reading { background: color-mix(in srgb, var(--color-primary) 9%, var(--color-surface)); }
  .caption.reading .text { font-size: 15.5px; color: var(--color-text); }
  .where { color: var(--color-primary); font-weight: 600; font-size: 13px; }
  .caption.back { background: color-mix(in srgb, var(--color-highlight) 8%, var(--color-surface)); }
  .looking { margin-left: 12px; color: var(--color-highlight); font-weight: 500; }
  .sep { color: var(--color-text-muted); margin: 0 6px; }
  .text { margin: 4px 0 0; max-width: 110ch; color: var(--color-text); }
  .text :global(ul), .text :global(ol) { margin: 4px 0 0; padding-left: 20px; }
  .text :global(li) { margin: 2px 0; }
  .now { margin-top: 6px; font-family: var(--font-mono); font-size: 12px; color: var(--color-text-muted); }
  .now span { color: var(--color-on-primary); background: var(--color-primary); border-radius: 4px; padding: 0 6px; margin-right: 4px; }
</style>
