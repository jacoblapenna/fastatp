<script>
  // The work, as records: a lane for each machine, a dot for each record in
  // the lane of the party that signed it, oldest at the top. Every strong ref
  // inside a record (a uri and a cid) is an arrow into its dot from the lane
  // of the record it points at: that party's record was read, and acted on,
  // here. Nothing in here knows what a ride is. A check that names a record
  // (custody.py's lines) marks its dot: a tick for verified, dashed for sealed.
  import { tick } from 'svelte';
  import { live } from '../lib/live.svelte.js';

  let { pointed = false } = $props();
  let box;
  let width = $state(600);
  let stick = true;

  const ROW = 24;
  const TOP = 8;

  // every {uri, cid} inside a record, wherever it sits
  function refs(value, out = []) {
    if (Array.isArray(value)) value.forEach((v) => refs(v, out));
    else if (value && typeof value === 'object') {
      if (typeof value.uri === 'string' && value.uri.startsWith('at://') && 'cid' in value) out.push(value.uri);
      else Object.values(value).forEach((v) => refs(v, out));
    }
    return out;
  }
  const repoOf = (uri) => uri.slice(5).split('/')[0];

  let lanes = $derived((live.manifest?.machines ?? []).filter((n) => live.machines[n]?.private?.length));
  let rows = $derived.by(() => {
    const owner = {}; // a private repo's DID -> the machine that keeps it
    const records = [];
    for (const name of lanes) {
      for (const repo of live.machines[name].private) {
        owner[repo.did] = name;
        for (const r of repo.records) records.push({ ...r, machine: name });
      }
    }
    records.sort(
      (a, b) => String(a.value.created_at ?? '').localeCompare(String(b.value.created_at ?? '')) || a.uri.localeCompare(b.uri),
    );
    const out = [];
    for (const r of records) {
      const kind = r.path.split('/')[0].split('.').at(-1);
      const from = [...new Set(refs(r.value).map((u) => owner[repoOf(u)]).filter((m) => m && m !== r.machine))];
      const last = out.at(-1);
      // a run of the same record from the same party, acting on the same parties: one row
      if (last && last.machine === r.machine && last.kind === kind && last.from.join() === from.join()) {
        last.uris.push(r.uri);
        continue;
      }
      out.push({ machine: r.machine, kind, from, uris: [r.uri], refs: refs(r.value) });
    }
    return out;
  });

  const x = (name) => {
    const i = lanes.indexOf(name);
    return ((i + 0.5) * width) / Math.max(lanes.length, 1);
  };
  const mark = (row) => {
    const marks = row.uris.map((u) => live.verified[u]).filter(Boolean);
    return marks.includes('wrong') ? 'wrong' : marks.includes('ok') ? 'ok' : marks.includes('sealed') ? 'sealed' : '';
  };
  // an arrow from a party whose record here was found sealed is drawn dashed
  const sealedFrom = (row, from) => row.refs.some((u) => live.verified[u] === 'sealed' && laneOf(u) === from);
  function laneOf(uri) {
    for (const name of lanes) if (live.machines[name].private.some((r) => r.did === repoOf(uri))) return name;
    return null;
  }

  function scrolled() {
    if (box) stick = box.scrollTop + box.clientHeight >= box.scrollHeight - 24;
  }
  $effect(() => {
    rows.length;
    tick().then(() => {
      if (box && stick) box.scrollTop = box.scrollHeight;
    });
  });
</script>

{#if lanes.length}
  <section class="map" class:pointed data-anchor="map">
    <div class="h4">
      the work, as records <span class="meta">each arrow: a record read, and acted on, by the party it points to · ✓ checked · dashed: sealed from the checker</span>
    </div>
    <div class="lanes" bind:clientWidth={width}>
      {#each lanes as name (name)}<span style="left: {x(name)}px">{name}</span>{/each}
    </div>
    <div class="rows" bind:this={box} onscroll={scrolled}>
      <svg width={width} height={TOP * 2 + rows.length * ROW} role="img" aria-label="records and the records they act on, by party">
        <defs>
          <marker id="head" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M0,0 L8,4 L0,8 z" fill="var(--color-text-muted)" />
          </marker>
        </defs>
        {#each lanes as name (name)}
          <line class="lane" x1={x(name)} x2={x(name)} y1="0" y2={TOP * 2 + rows.length * ROW} />
        {/each}
        {#each rows as row, i (row.uris[0])}
          {@const y = TOP + i * ROW + ROW / 2}
          {#each row.from as from, k (from)}
            <!-- two arrows into one dot are spread apart, so neither hides the other -->
            {@const dy = (k - (row.from.length - 1) / 2) * 4}
            <line class="arrow" class:dashed={sealedFrom(row, from)} x1={x(from)} y1={y + dy} x2={x(row.machine) + (x(from) < x(row.machine) ? -7 : 7)} y2={y + dy / 3} marker-end="url(#head)" />
          {/each}
          <circle class="dot {mark(row)}" cx={x(row.machine)} cy={y} r="5" />
          <!-- the label sits on the side no arrow comes in from -->
          {@const left = row.from.some((f) => x(f) > x(row.machine))}
          <text class="label" x={x(row.machine) + (left ? -9 : 9)} y={y + 4} text-anchor={left ? 'end' : 'start'}>
            {row.kind}{row.uris.length > 1 ? ` ×${row.uris.length}` : ''}{mark(row) === 'ok' ? ' ✓' : mark(row) === 'wrong' ? ' ✗' : ''}
          </text>
        {/each}
      </svg>
    </div>
  </section>
{/if}

<style>
  .map {
    display: flex; flex-direction: column; min-height: 0; height: 100%;
    background: var(--color-surface); border: 1px solid var(--color-border); border-radius: 10px; padding: 6px 8px;
  }
  .map.pointed { box-shadow: 0 0 0 2px var(--color-highlight), 0 0 18px color-mix(in srgb, var(--color-highlight) 35%, transparent); }
  .h4 { display: flex; gap: 8px; align-items: baseline; font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--color-text-muted); }
  .meta { text-transform: none; letter-spacing: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .lanes { position: relative; height: 18px; margin-top: 4px; font: 11px var(--font-mono); }
  .lanes span { position: absolute; transform: translateX(-50%); white-space: nowrap; color: var(--color-text); }
  .rows { flex: 1 1 0; min-height: 0; overflow-y: auto; overflow-x: hidden; }
  .lane { stroke: var(--color-border); stroke-width: 1; }
  .arrow { stroke: var(--color-text-muted); stroke-width: 1.3; }
  .arrow.dashed { stroke-dasharray: 4 3; }
  .dot { fill: var(--color-surface-raised); stroke: var(--color-primary); stroke-width: 1.6; }
  .dot.ok { fill: var(--color-verified); stroke: var(--color-verified); }
  .dot.sealed { fill: none; stroke: var(--color-text-muted); stroke-dasharray: 2 2; }
  .dot.wrong { fill: var(--color-danger); stroke: var(--color-danger); }
  .label { font: 11px var(--font-mono); fill: var(--color-text); }
</style>
