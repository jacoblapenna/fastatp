<script>
  // One record from a repo, in plain words first, with the raw JSON a click away.
  // A record that just landed flashes once as it appears. The records it acts
  // on are named under it, by kind and owner; when a newer record names this
  // one, it glows where it is. A check that named it marks it: ✓, or sealed.
  import { live, refs, ownerOf } from '../lib/live.svelte.js';

  let { record } = $props();
  let el;
  let named = $derived(
    refs(record.value).map((uri) => ({ uri, kind: uri.split('/')[3]?.split('.').at(-1), owner: ownerOf(uri) })),
  );
  let mark = $derived(record.uri ? live.verified[record.uri] : undefined);
  $effect(() => {
    // a newer record just named this one: it lights up where it is
    const at = record.uri ? live.glow[record.uri] : undefined;
    if (!at || !el || Date.now() - at > 1500 || matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    el.animate(
      [
        { boxShadow: '0 0 0 2px var(--color-sealed), 0 0 14px var(--color-sealed)' },
        { boxShadow: '0 0 0 0 transparent' },
      ],
      { duration: 1600, easing: 'ease-out' },
    );
  });

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
    // otherwise its plain fields, and each listed object in a few words; the
    // time it was made and the records it names are shown elsewhere
    return Object.entries(v)
      .filter(([k, x]) => k !== '$type' && k !== 'created_at' && !(x && typeof x === 'object' && 'cid' in x))
      .map(([k, x]) =>
        isMoney(x)
          ? `${k} ${money(x)}`
          : Array.isArray(x)
            ? x.filter((o) => !(o && typeof o === 'object' && 'cid' in o)).map(item).join(' · ')
            : x !== null && typeof x !== 'object' && x !== '' ? `${k} ${short(x)}` : '')
      .filter(Boolean)
      .join(' · ');
  }

  // an amount as whole numbers with its scale ({amount: 3400, exponent: -2}) reads as $34.00
  const isMoney = (x) => x && typeof x === 'object' && Number.isInteger(x.amount) && Number.isInteger(x.exponent);
  const money = (x) => `${x.currency === 'USD' || !x.currency ? '$' : `${x.currency} `}${(x.amount * 10 ** x.exponent).toFixed(-x.exponent)}`;

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
<div class="record" class:ok={mark === 'ok'} class:sealed={mark === 'sealed'} class:wrong={mark === 'wrong'} use:flash bind:this={el} title={record.path}>
  <button class="row" onclick={() => (open = !open)} aria-expanded={open}>
    <span class="type">{type}{#if action}<span class="action"> · {action}</span>{/if}</span>
    <span class="text">{summary}</span>
    <span class="cid">{#if mark === 'ok'}✓ {:else if mark === 'wrong'}✗ {/if}{record.cid.slice(0, 12)}…</span>
  </button>
  {#if named.length}
    <!-- what this record acts on: named by its hash, not sent anywhere -->
    <div class="on">
      on:{#each named as n (n.uri)}<span class="named">{n.kind}{#if n.owner} · {n.owner}{/if}</span>{/each}
    </div>
  {/if}
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
  .record.ok { border-color: color-mix(in srgb, var(--color-verified) 55%, transparent); }
  .record.ok .cid { color: var(--color-verified); }
  .record.sealed { border: 1px dashed var(--color-text-muted); opacity: 0.75; }
  .record.wrong { border-color: var(--color-danger); }
  .on { display: flex; flex-wrap: wrap; gap: 4px; align-items: baseline; padding: 0 6px 4px; font: 10.5px var(--font-mono); color: var(--color-text-muted); }
  .named { padding: 0 5px; border: 1px solid var(--color-border); border-radius: 4px; color: var(--color-text-soft); }
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
