<script>
  // One machine, as that machine sees itself: its terminal, its own repo,
  // and its tunnel. The terminal is always open; the repo and the tunnel
  // fold to one line each until the viewer opens them, the score points at
  // them, or a cue keeps them open. Every section is an anchor a key
  // concept can point at.
  import Terminal from './Terminal.svelte';
  import RecordCard from './RecordCard.svelte';
  import WgPeers from './WgPeers.svelte';

  let { m, size, active, focus = false, pointed = new Set(), kept = new Set(), now } = $props();
  let id = $derived(m.identity);
  let chosen = $state({ repo: false, tunnel: false }); // what the viewer opened by hand

  const anchor = (s) => `${m.name}.${s}`;
  const isPointed = (s) => pointed.has(anchor(s));
  const shown = (s) => chosen[s] || isPointed(s) || kept.has(anchor(s));
  let open = $derived(m.wg.filter((p) => p.latest_handshake && now / 1000 - p.latest_handshake < 150).length);
</script>

<section class="panel {size}" class:active data-anchor={m.name}>
  <header>
    <span class="dot" class:up={m.stage === 'up'}></span>
    <strong>{m.name}.lan</strong>
    {#if id}<span class="did">{id.did}</span>{/if}
  </header>

  {#if size === 'collapsed'}
    <div class="summary">
      {m.repo?.records.length ?? 0} records · {m.wg.length} tunnel peers
      {#if m.order.length}· last: {m.procs[m.order.at(-1)].display.replace(`./lab.sh ${m.name} `, '')}{/if}
    </div>
  {:else}
    <div class="body">
      <div class="section term" class:focus class:pointed={isPointed('terminal')} data-anchor={anchor('terminal')}>
        <div class="h4">terminal</div>
        <Terminal {m} />
      </div>

      <div class="section" class:pointed={isPointed('repo')} data-anchor={anchor('repo')}>
        <button class="h4" onclick={() => (chosen.repo = !chosen.repo)} aria-expanded={shown('repo')}>
          <span class="tri">{shown('repo') ? '▾' : '▸'}</span> public repo
          <span class="meta">{m.repo?.records.length ?? 0} record{m.repo?.records.length === 1 ? '' : 's'}{#if m.repo} · rev {m.repo.rev}{/if}</span>
        </button>
        {#if shown('repo')}
          <div class="records">
            {#each m.repo?.records ?? [] as record (record.path)}
              <RecordCard {record} />
            {:else}
              <div class="muted">empty</div>
            {/each}
          </div>
          {#if id?.channel}
            <div class="channel" title={id.channel}>
              gated channel <code>{id.channel.slice(0, 22)}…</code>
              <span class="muted">(behind the doorman; not readable from here)</span>
            </div>
          {/if}
        {/if}
      </div>

      <div class="section" class:pointed={isPointed('tunnel')} data-anchor={anchor('tunnel')}>
        <button class="h4" onclick={() => (chosen.tunnel = !chosen.tunnel)} aria-expanded={shown('tunnel')}>
          <span class="tri">{shown('tunnel') ? '▾' : '▸'}</span> tunnel · wg0
          <span class="meta">
            {#if id}{id.tunnel_ip} · {/if}{m.wg.length} peer{m.wg.length === 1 ? '' : 's'}{#if open} · 🔒 open{/if}
          </span>
        </button>
        {#if shown('tunnel')}<WgPeers {m} {now} />{/if}
      </div>
    </div>
  {/if}
</section>

<style>
  .panel {
    flex: 1 1 0; min-width: 0; display: flex; flex-direction: column;
    background: var(--color-surface); border: 1px solid var(--color-border); border-radius: 10px;
    transition: flex 0.4s, border-color 0.3s, box-shadow 0.3s; overflow: hidden;
  }
  .panel.active { border-color: var(--color-primary); box-shadow: 0 0 0 1px var(--color-primary), 0 0 18px color-mix(in srgb, var(--color-primary) 20%, transparent); flex-grow: 1.35; }
  .panel.collapsed { flex: 0.45 1 0; }
  header { display: flex; gap: 8px; align-items: baseline; padding: 8px 10px; border-bottom: 1px solid var(--color-border); }
  header .did { font: 11px var(--font-mono); color: var(--color-text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--color-danger); align-self: center; }
  .dot.up { background: var(--color-verified); }
  .summary { padding: 8px 10px; color: var(--color-text-muted); font-size: 12px; }
  .body { flex: 1; min-height: 0; display: flex; flex-direction: column; padding: 6px 6px 6px; gap: 4px; }
  .section { flex: 0 0 auto; padding: 3px 4px; border-radius: 8px; transition: box-shadow 0.3s; }
  .section.term { flex: 1 1 0; display: flex; flex-direction: column; min-height: 90px; }
  .section.term.focus { box-shadow: 0 0 0 2px var(--color-primary); }
  .section.pointed {
    box-shadow: 0 0 0 2px var(--color-highlight), 0 0 18px color-mix(in srgb, var(--color-highlight) 35%, transparent);
    background: color-mix(in srgb, var(--color-highlight) 7%, transparent);
  }
  .h4 {
    display: flex; gap: 6px; align-items: baseline; width: 100%; margin: 0 0 3px; padding: 0;
    background: none; border: none; text-align: left;
    font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--color-text-muted);
  }
  button.h4 { cursor: pointer; }
  button.h4:hover { color: var(--color-text); }
  .tri { width: 10px; display: inline-block; }
  .meta { margin-left: auto; text-transform: none; letter-spacing: 0; font-family: var(--font-mono); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .records { max-height: 96px; overflow: auto; }
  .channel { font-size: 11.5px; margin-top: 3px; }
  .muted { color: var(--color-text-muted); font-size: 11.5px; }
</style>
