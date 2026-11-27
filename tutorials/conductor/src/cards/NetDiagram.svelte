<script>
  // The whole picture, which no single machine has: who follows whom, over
  // which wire, which tunnels are open, and every packet crossing the LAN.
  // Built only from what each machine reported about itself.
  import { live, nameOf } from '../lib/live.svelte.js';

  let { now, pointed = false, kept = false } = $props();
  let chosen = $state(false);
  let open = $derived(chosen || pointed || kept);

  let names = $derived(live.manifest?.machines ?? []);
  // a tunnel address is derived from the machine's key: a long IPv6 address,
  // of which the last two groups are enough to tell the machines apart
  const short = (ip) => (ip ? '…' + ip.split(':').slice(-2).join(':') : '?');
  // three machines sit as they always have; a bigger cast spreads wider, in narrower boxes
  let wide = $derived(names.length > 3);
  let step = $derived((wide ? 540 : 420) / Math.max(1, names.length - 1));
  let x = $derived(Object.fromEntries(names.map((n, i) => [n, (wide ? 50 : 110) + i * step])));
  let boxW = $derived(Math.min(144, step - 8));

  // tunnels: a peer entry on either side; open once a handshake is fresh
  let tunnels = $derived.by(() => {
    const edges = {};
    for (const m of Object.values(live.machines)) {
      for (const p of m.wg) {
        const other = nameOf({ key: p.public_key });
        if (!other) continue;
        const k = [m.name, other].sort().join('|');
        const e = (edges[k] ??= { a: m.name, b: other, sides: new Set(), fresh: false });
        e.sides.add(m.name);
        if (p.latest_handshake && now / 1000 - p.latest_handshake < 150) e.fresh = true;
      }
    }
    // open only while both kernels list each other: evicting removes one side,
    // and the other side's last handshake no longer means anything
    return Object.values(edges).map((e) => ({ ...e, up: e.fresh && e.sides.size === 2 }));
  });

  // follows: a follow.py or tail.py running on one machine, pointed at another
  let follows = $derived.by(() => {
    const out = [];
    for (const m of Object.values(live.machines)) {
      for (const id of m.order) {
        const p = m.procs[id];
        const hit = p.display.match(/(follow|tail)\.py\s+(?:([^\s-]\S*)\s+)?([^\s-]\S*)/) // a flag such as --cursor is never the host;
        if (!hit || (!p.running && now - p.ended > 8000)) continue;
        const [, script, did, host] = hit;
        const target = nameOf({ ip: host }) ?? host.replace('.lan', '');
        if (!names.includes(target)) continue;
        const text = p.lines.join('\n');
        out.push({
          from: target, to: m.name, raw: script === 'tail',
          channel: /CH|did:key/.test(did ?? ''), tunnel: /^10\./.test(host),
          verified: (text.match(/verified/g) ?? []).length,
          failed: /refused|no route/.test(text), running: p.running,
        });
      }
    }
    return out;
  });

  let pulses = $derived(live.wire.filter((w) => w.seen && now - w.seen < 1400).slice(-40));

  function arc(a, b, lift) {
    return `M ${x[a]} 118 Q ${(x[a] + x[b]) / 2} ${118 - lift} ${x[b]} 118`;
  }
  function under(a, b, drop) {
    return `M ${x[a]} 176 Q ${(x[a] + x[b]) / 2} ${176 + drop} ${x[b]} 176`;
  }
</script>

<div class="diagram" class:open class:pointed data-anchor="diagram">
<button class="h4" onclick={() => (chosen = !open)} aria-expanded={open}>
  <span class="tri">{open ? '▾' : '▸'}</span> the whole picture, which no machine has
  <span class="meta">{tunnels.filter((t) => t.up).length} tunnel{tunnels.filter((t) => t.up).length === 1 ? '' : 's'} open · {follows.filter((f) => f.running).length} following</span>
</button>
{#if open}
<svg viewBox="0 0 640 280">
  <defs>
    <marker id="head" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">
      <path d="M0 0 L10 5 L0 10 z" fill="context-stroke" />
    </marker>
  </defs>

  <text x="12" y="18" class="legend">
    <tspan class="signed">━ follows, verified</tspan>
    <tspan dx="12" class="sealed">━ WireGuard tunnel</tspan>
    <tspan dx="12" class="readable">● plain http</tspan>
    <tspan dx="12" class="sealed">● sealed</tspan>
  </text>

  <line x1="30" y1="258" x2="610" y2="258" class="bus" />
  <text x="610" y="274" class="bus-label" text-anchor="end">LAN 192.168.77.0/24</text>

  {#each tunnels as t}
    {@const lift = 40 + Math.abs(x[t.a] - x[t.b]) * 0.18}
    <path d={arc(t.a, t.b, lift)} class="tunnel" class:up={t.up} />
    <!-- with more than three machines the arcs crowd: the header counts them instead -->
    {#if !wide || !t.up}
      <text x={(x[t.a] + x[t.b]) / 2} y={118 - lift / 2 - 6} class="label" class:sealed={t.up} text-anchor="middle">
      {t.up ? '🔒 tunnel open' : `${[...t.sides].join(', ')} knocking, unanswered`}
    </text>
    {/if}
  {/each}

  {#each follows as f, i}
    {@const d = f.tunnel ? arc(f.from, f.to, 40 - i * 4) : under(f.from, f.to, 46 + i * 10)}
    <path {d} class="follow" class:raw={f.raw} class:failed={f.failed} class:done={!f.running} marker-end="url(#head)" />
    <text x={(x[f.from] + x[f.to]) / 2} y={f.tunnel ? 92 - i * 2 : 206 + i * 10} class="label" text-anchor="middle">
      {f.raw ? 'raw wire, unverified' : f.failed ? 'refused' : `${f.from}${f.channel ? "'s channel" : ''} → ${f.to} · ${f.verified} verified`}
    </text>
  {/each}

  {#each names as n}
    {@const m = live.machines[n]}
    <line x1={x[n]} y1="176" x2={x[n]} y2="258" class="drop" />
    <g data-anchor="diagram.{n}">
      <rect x={x[n] - boxW / 2} y="118" width={boxW} height="58" rx="10" class="box" class:off={m?.stage !== 'up'} />
      <text x={x[n]} y="137" text-anchor="middle" class="name">{wide ? n : `${n}.lan`}</text>
      <text x={x[n]} y="153" text-anchor="middle" class="sub">
        {m?.repo?.records.length ?? 0} record{m?.repo?.records.length === 1 ? '' : 's'}
      </text>
      <text x={x[n]} y="167" text-anchor="middle" class="sub">
        <title>{m?.identity?.tunnel_ip ?? ''}</title>
        wg {short(m?.identity?.tunnel_ip)}
      </text>
    </g>
  {/each}

  {#each pulses as p (p)}
    {@const a = nameOf({ ip: p.src })}
    {@const b = nameOf({ ip: p.dst })}
    {#if a && b}
      <circle r="4" cy="258" class={p.proto === 'wg' ? 'sealed-dot' : 'readable-dot'}>
        <animate attributeName="cx" from={x[a]} to={x[b]} dur="1.1s" fill="freeze" />
      </circle>
    {/if}
  {/each}
</svg>
{/if}
</div>

<style>
  .diagram {
    display: flex; flex-direction: column; min-height: 0;
    background: var(--color-surface); border: 1px solid var(--color-border); border-radius: 10px; padding: 8px;
    transition: box-shadow 0.3s;
  }
  .diagram.open { height: 100%; }
  .diagram.pointed { box-shadow: 0 0 0 2px var(--color-highlight), 0 0 18px color-mix(in srgb, var(--color-highlight) 35%, transparent); }
  .h4 {
    display: flex; gap: 8px; align-items: baseline; width: 100%; margin: 0; padding: 0;
    background: none; border: none; text-align: left; cursor: pointer;
    font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--color-text-muted);
  }
  .h4:hover { color: var(--color-text); }
  .open .h4 { margin-bottom: 4px; }
  .tri { width: 10px; display: inline-block; }
  .meta { margin-left: auto; text-transform: none; letter-spacing: 0; font-family: var(--font-mono); }
  svg { width: 100%; flex: 1; min-height: 0; display: block; }
  .legend { font: 10px var(--font-mono); }
  .signed { fill: var(--color-verified); }
  .sealed { fill: var(--color-sealed); }
  .readable { fill: var(--color-plaintext); }
  .bus { stroke: var(--color-border); stroke-width: 3; }
  .bus-label { fill: var(--color-text-muted); font: 10px var(--font-mono); }
  .drop { stroke: var(--color-border); stroke-width: 2; }
  .box { fill: var(--color-surface-raised); stroke: var(--color-border); }
  .box.off { opacity: 0.4; }
  .name { fill: var(--color-text); font: 600 13px var(--font-sans); }
  .sub { fill: var(--color-text-muted); font: 10px var(--font-mono); }
  .label { fill: var(--color-text-muted); font: 10px var(--font-mono); }
  .tunnel { fill: none; stroke: var(--color-text-muted); stroke-width: 2; stroke-dasharray: 5 5; }
  .tunnel.up { stroke: var(--color-sealed); stroke-width: 4; stroke-dasharray: none; }
  .follow { fill: none; stroke: var(--color-verified); stroke-width: 2; }
  .follow.raw { stroke: var(--color-plaintext); stroke-dasharray: 3 4; }
  .follow.failed { stroke: var(--color-danger); }
  .follow.done { opacity: 0.35; }
  .sealed-dot { fill: var(--color-sealed); }
  .readable-dot { fill: var(--color-plaintext); }
</style>
