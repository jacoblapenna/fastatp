<script>
  // The guide's own words for what is happening now.
  import { md } from '../lib/md.js';
  import { picture } from '../lib/live.svelte.js';

  let { script, back = 0 } = $props();
  let svg = $state(null); // the caption's picture, from the conductor's repo

  $effect(() => {
    const path = script.step?.kind === 'say' ? script.step.sketch : null;
    svg = null;
    if (path) picture(path).then((text) => (svg = text));
  });
</script>

<div class="caption" class:reading={script.step?.kind === 'say'} class:back class:opening={script.step?.kind === 'chapter'}>
  <div class="where">
    {#if script.chapter}<span class="chapter">Chapter {script.chapter.number}</span>{/if}
    {script.part}{#if script.section}<span class="sep">›</span>{script.section}{/if}
    {#if back}<span class="looking">↶ looking back · Next returns to where the machines are</span>{/if}
  </div>
  {#if script.step?.kind === 'chapter'}
    <h1>{script.step.title}</h1>
  {/if}
  <div class="row">
    <div class="words">
      {#if script.say}<div class="text">{@html md(script.say)}</div>{/if}
      {#if script.ask}
        <div class="ask" class:fresh={script.step?.kind === 'ask'}>
          <span class="before">{script.step?.kind === 'ask' ? 'before you press Next' : 'the question was'}</span>
          <span>{@html md(script.ask)}</span>
        </div>
      {/if}
    </div>
    {#if svg}<div class="picture">{@html svg}</div>{/if}
  </div>
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
  .where { color: var(--color-accent-text); font-weight: 600; font-size: 13px; }
  .caption.back { background: color-mix(in srgb, var(--color-highlight) 8%, var(--color-surface)); }
  .caption.opening { background: color-mix(in srgb, var(--color-primary) 14%, var(--color-surface)); }
  .chapter { color: var(--color-text-muted); font-weight: 500; margin-right: 10px; }
  h1 { margin: 6px 0 2px; font-size: 26px; line-height: 1.2; }
  .looking { margin-left: 12px; color: var(--color-accent-text); font-weight: 500; }
  .sep { color: var(--color-text-muted); margin: 0 6px; }
  .row { display: flex; gap: 18px; align-items: flex-start; }
  .words { flex: 1 1 0; min-width: 0; }
  .picture { flex: 0 0 auto; width: min(420px, 38%); max-height: 190px; background: var(--color-terminal-bg); border: 1px solid var(--color-border); border-radius: 8px; padding: 6px; }
  .picture :global(svg) { width: 100%; height: auto; max-height: 176px; display: block; }
  .text { margin: 4px 0 0; max-width: 110ch; color: var(--color-text); }
  .ask {
    margin: 8px 0 2px; display: flex; gap: 12px; align-items: baseline; max-width: 110ch;
    background: var(--color-terminal-bg); border: 1px solid var(--color-border); border-radius: 8px; padding: 7px 12px;
    font-size: 15px; color: var(--color-text-soft);
  }
  .ask.fresh { border-color: var(--color-highlight); color: var(--color-text); font-size: 15.5px; }
  .before { flex: none; color: var(--color-accent-text); font-size: 11px; font-weight: 600; letter-spacing: 0.05em; text-transform: uppercase; }
  .text :global(ul), .text :global(ol) { margin: 4px 0 0; padding-left: 20px; }
  .text :global(li) { margin: 2px 0; }
  .now { margin-top: 6px; font-family: var(--font-mono); font-size: 12px; color: var(--color-text-muted); }
  .now span { color: var(--color-on-primary); background: var(--color-primary); border-radius: 4px; padding: 0 6px; margin-right: 4px; }
</style>
