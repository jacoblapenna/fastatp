<script>
  // One record from a repo, in plain words first, with the raw JSON a click away.
  let { record } = $props();
  let open = $state(false);

  let [collection, rkey] = $derived(record.path.split('/'));
  let type = $derived(collection.split('.').at(-1));
  // a subject is a plain string in some records and a reference (uri and cid) in others
  let subject = $derived(typeof record.value.subject === 'string' ? record.value.subject : record.value.subject?.uri);
  let summary = $derived(record.value.text ?? subject ?? '');
</script>

<!-- The row is the button; the JSON sits beside it, not inside, so it can be selected and copied. -->
<div class="record" title={record.path}>
  <button class="row" onclick={() => (open = !open)} aria-expanded={open}>
    <span class="type">{type}</span>
    <span class="text">{summary}</span>
    <span class="cid">{record.cid.slice(0, 12)}…</span>
  </button>
  {#if open}
    <pre>{JSON.stringify({ path: record.path, cid: record.cid, ...record.value }, null, 1)}</pre>
  {/if}
</div>

<style>
  .record {
    margin: 2px 0; background: var(--color-surface-raised);
    border: 1px solid transparent; border-radius: 5px;
  }
  .record:hover { border-color: var(--color-border); }
  .row {
    display: grid; grid-template-columns: auto 1fr auto; gap: 6px; align-items: baseline;
    width: 100%; text-align: left; padding: 3px 6px;
    background: none; border: none; border-radius: 5px;
  }
  .type { color: var(--color-verified); font-family: var(--font-mono); font-size: 11px; }
  .text { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .cid { color: var(--color-text-muted); font-family: var(--font-mono); font-size: 11px; }
  pre {
    margin: 0; padding: 2px 6px 5px; font: 11px/1.35 var(--font-mono);
    white-space: pre-wrap; word-break: break-all; color: var(--color-text-muted);
  }
</style>
