<script>
  // What this machine's kernel will let through wg0, as `wg show wg0` says it.
  import { nameOf } from '../lib/live.svelte.js';

  let { m, now } = $props();

  function age(ts) {
    if (!ts) return 'never';
    const s = Math.max(0, Math.round(now / 1000 - ts));
    return s < 120 ? `${s}s ago` : `${Math.round(s / 60)}m ago`;
  }
</script>

<div class="wg">
  {#each m.wg as p}
    <div class="peer" class:up={p.latest_handshake && now / 1000 - p.latest_handshake < 150}>
      <span class="who">{nameOf({ key: p.public_key }) ?? p.public_key.slice(0, 8) + '…'}</span>
      <span>{p.allowed_ips}</span>
      <span class="muted">handshake {age(p.latest_handshake)} · ↓{p.rx}B ↑{p.tx}B</span>
    </div>
  {:else}
    <div class="muted">no peers: the kernel answers nobody</div>
  {/each}
</div>

<style>
  .wg { font: 11.5px/1.5 var(--font-mono); }
  .peer { display: flex; gap: 8px; flex-wrap: wrap; color: var(--color-text-muted); }
  .peer .who { color: var(--color-text); }
  .peer.up .who { color: var(--color-sealed); }
  .peer.up .who::before { content: '🔒 '; }
  .muted { color: var(--color-text-muted); }
</style>
