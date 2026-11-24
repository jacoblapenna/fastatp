<script>
  // One record from a repo, in plain words first, with the raw JSON a click away.
  // A record that just landed flashes once as it appears.
  let { record } = $props();

  // once, when the row is added, and only for a record that landed a moment ago
  function flash(el) {
    const age = Date.now() - (record.landed || 0);
    if (age > 1200 || matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    el.animate(
      [
        { background: 'color-mix(in srgb, var(--color-highlight) 45%, var(--color-surface-raised))', borderColor: 'var(--color-highlight)' },
        { background: 'var(--color-surface-raised)', borderColor: 'transparent' },
      ],
      { duration: 1600 - age, easing: 'ease-out' },
    );
  }
  let open = $state(false);

  let [collection, rkey] = $derived(record.path.split('/'));
  let type = $derived(collection.split('.').at(-1));
  let action = $derived(typeof record.value.action === 'string' ? record.value.action : '');
  let summary = $derived(summarize(record.value));

  // a machine's DID reads as its name: did:web:alice.lan -> alice
  const short = (x) => String(x).replace(/did:web:([a-z0-9-]+)\.(lan|stage)(%3A\d+)?/g, '$1');

  function summarize(v) {
    // a subject is a plain string in some records and a reference (uri and cid) in others
    const subject = typeof v.subject === 'string' ? v.subject : v.subject?.uri;
    for (const said of [v.title, v.text, v.display, v.command, v.name, v.score, subject, v.record]) {
      if (typeof said === 'string' && said) return short(said);
    }
    // otherwise its plain fields, and each listed object in a few words
    return Object.entries(v)
      .filter(([k]) => k !== '$type')
      .map(([k, x]) =>
        Array.isArray(x) ? x.map(item).join(' · ') : x !== null && typeof x !== 'object' && x !== '' ? `${k} ${short(x)}` : '')
      .filter(Boolean)
      .join(' · ');
  }

  // who first, then its words, then its numbers by name: alice mi (octave 4, ticks 4)
  function item(o) {
    if (!o || typeof o !== 'object') return short(o);
    const values = Object.entries(o);
    const who = values.filter(([, x]) => typeof x === 'string' && x.startsWith('did:')).map(([, x]) => short(x));
    const words = values.filter(([, x]) => typeof x === 'string' && !x.startsWith('did:')).map(([, x]) => x);
    const numbers = values.filter(([, x]) => typeof x === 'number').map(([k, x]) => `${k} ${x}`);
    return [...who, ...words].join(' ') + (numbers.length ? ` (${numbers.join(', ')})` : '');
  }
</script>

<!-- The row is the button; the JSON sits beside it, not inside, so it can be selected and copied. -->
<div class="record" use:flash title={record.path}>
  <button class="row" onclick={() => (open = !open)} aria-expanded={open}>
    <span class="type">{type}{#if action}<span class="action"> · {action}</span>{/if}</span>
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
  .type { color: var(--color-verified); font-family: var(--font-mono); font-size: 11px; white-space: nowrap; }
  .action { color: var(--color-text-muted); }
  .text { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .cid { color: var(--color-text-muted); font-family: var(--font-mono); font-size: 11px; }
  pre {
    margin: 0; padding: 2px 6px 5px; font: 11px/1.35 var(--font-mono);
    white-space: pre-wrap; word-break: break-all; color: var(--color-text-muted);
  }
</style>
