<script>
  // The conductor's own repo, as it grows: every record it signs, the moment
  // it signs it. The cues for each step, every press of a button, and the
  // records the guide commits for the machines, in the order they were
  // signed, newest at the bottom. This list is the show.
  import RepoList from './RepoList.svelte';
  import { live } from '../lib/live.svelte.js';

  let { pointed = new Set(), wide = false } = $props();
  let c = $derived(live.conductor);
  let thisShow = $derived(c.count - c.before);
</script>

<section class="panel" class:wide class:pointed={pointed.has('conductor.repo')} data-anchor="conductor.repo">
  <header>
    <span class="dot" class:up={live.connected}></span>
    <strong>conductor.stage</strong>
    <span class="did">{live.did ?? ''}</span>
  </header>
  <div class="h4">
    public repo
    <span class="meta">{c.count} records{#if c.before} · {thisShow} this show{/if}{#if c.rev} · rev {c.rev}{/if}</span>
  </div>
  <RepoList records={c.records} grow />
</section>

<style>
  .panel {
    flex: 1 1 0; min-width: 0; display: flex; flex-direction: column; padding: 0 0 6px;
    background: var(--color-surface); border: 1px solid var(--color-border); border-radius: 10px;
    overflow: hidden; transition: flex 0.4s, box-shadow 0.3s;
  }
  .panel.wide { flex-grow: 1.35; }
  .panel.pointed { box-shadow: 0 0 0 2px var(--color-highlight), 0 0 18px color-mix(in srgb, var(--color-highlight) 35%, transparent); }
  header { display: flex; gap: 8px; align-items: baseline; padding: 8px 10px; border-bottom: 1px solid var(--color-border); }
  header .did { font: 11px var(--font-mono); color: var(--color-text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--color-danger); align-self: center; }
  .dot.up { background: var(--color-verified); }
  .h4 {
    display: flex; gap: 6px; align-items: baseline; margin: 6px 10px 3px;
    font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--color-text-muted);
  }
  .meta { margin-left: auto; text-transform: none; letter-spacing: 0; font-family: var(--font-mono); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .panel :global(.records) { margin: 0 6px; }
</style>
