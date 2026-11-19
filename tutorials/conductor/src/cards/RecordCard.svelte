<script>
  // One record from a repo, in plain words first, with the raw JSON a click away.
  let { record } = $props();
  let open = $state(false);

  let [collection, rkey] = $derived(record.path.split('/'));
  let type = $derived(collection.split('.').at(-1));
  let summary = $derived(record.value.text ?? record.value.subject ?? '');
</script>

<button class="record" onclick={() => (open = !open)} title={record.path}>
  <span class="type">{type}</span>
  <span class="text">{summary}</span>
  <span class="cid">{record.cid.slice(0, 12)}…</span>
  {#if open}
    <pre>{JSON.stringify({ path: record.path, cid: record.cid, ...record.value }, null, 1)}</pre>
  {/if}
</button>

<style>
  .record {
    display: grid; grid-template-columns: auto 1fr auto; gap: 6px; align-items: baseline;
    width: 100%; text-align: left; padding: 3px 6px; margin: 2px 0;
    background: var(--color-surface-raised); border: 1px solid transparent; border-radius: 5px;
  }
  .record:hover { border-color: var(--color-border); }
  .type { color: var(--color-verified); font-family: var(--font-mono); font-size: 11px; }
  .text { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .cid { color: var(--color-text-muted); font-family: var(--font-mono); font-size: 11px; }
  pre {
    grid-column: 1 / -1; margin: 4px 0 0; font: 11px/1.35 var(--font-mono);
    white-space: pre-wrap; word-break: break-all; color: var(--color-text-muted);
  }
</style>
