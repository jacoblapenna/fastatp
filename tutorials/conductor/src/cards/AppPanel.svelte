<script>
  // A machine's own web app, as a person meets it: in a browser. "this
  // browser" is the app on this page's own origin. "a second device" is the
  // same app on the other name for this computer (localhost and 127.0.0.1
  // are two origins), so it has storage of its own, and keys of its own:
  // to the browser, it is another device.
  import { tick } from 'svelte';
  import { live } from '../lib/live.svelte.js';

  let { app, device = 'this browser', pointed = false, wire = false } = $props();

  function origin() {
    if (device === 'this browser') return '';
    const other = location.hostname === '127.0.0.1' ? 'localhost' : '127.0.0.1';
    return `${location.protocol}//${other}${location.port ? `:${location.port}` : ''}`;
  }
  let src = $derived(`${origin()}/${app.machine}.lan${app.path}`);
  let anchor = $derived(device === 'this browser' ? 'app' : 'phone');
  let frame = $state(null);
  // a browser's reload: the app starts again from what this device keeps
  const reload = () => frame && (frame.src = src);

  // What this device asked the app, newest last: each request's method, path
  // and query, values cut short. The bodies are the signed chain and repo; a
  // password or a token would have to ride in one of these, and none does.
  let me = $derived(device === 'this browser' ? location.hostname : origin().split('//')[1]?.split(':')[0]);
  let sent = $derived(live.http.filter((h) => h.host === me && h.machine === app.machine));
  let strip = $state(null);
  let stick = true; // at the bottom: keep following the newest, as RepoList does
  const scrolled = () => strip && (stick = strip.scrollTop + strip.clientHeight >= strip.scrollHeight - 12);
  $effect(() => {
    sent.length; // each request as it lands
    tick().then(() => {
      if (strip && stick) strip.scrollTop = strip.scrollHeight;
    });
  });
  const short = (v) => (String(v).length > 12 ? `${String(v).slice(0, 10)}…` : v); // enough to see each field
  const shown = (h) =>
    h.path.replace(/^\/xrpc\//, '') +
    (Object.keys(h.query).length ? '?' + Object.entries(h.query).map(([k, v]) => `${k}=${short(v)}`).join('&') : '');
</script>

<section class="panel" class:pointed data-anchor={anchor}>
  <header>
    <span class="dot"></span>
    <strong>{device}</strong>
    <span class="where" title={src}>{src.replace(/^\/\//, '')}</span>
    <button class="reload" onclick={reload} title="reload, as a browser does" aria-label="reload the app">↻</button>
  </header>
  <iframe bind:this={frame} title="{app.machine}: {device}" {src} data-device={anchor}></iframe>
  <div class="sent" bind:this={strip} onscroll={scrolled} class:pointed={wire} data-anchor="{anchor}.wire">
    <div class="label">every request {device} made of {app.machine}: method, path, query (bodies are signed repos)</div>
    {#each sent as h, i (h.t + ':' + i)}
      <div class="line"><span class="method">{h.method}</span> {shown(h)} <span class:bad={h.status >= 400}>→ {h.status}</span></div>
    {:else}
      <div class="line quiet">nothing yet</div>
    {/each}
  </div>
</section>

<style>
  .panel {
    flex: 1.5 1 0; min-width: 0; display: flex; flex-direction: column;
    background: var(--color-surface); border: 2px solid var(--color-primary); border-radius: 10px; overflow: hidden;
  }
  .panel.pointed { box-shadow: 0 0 0 2px var(--color-highlight), 0 0 18px color-mix(in srgb, var(--color-highlight) 35%, transparent); }
  header { display: flex; gap: 8px; align-items: baseline; padding: 8px 10px; border-bottom: 1px solid var(--color-border); }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--color-primary); align-self: center; }
  .where { font: 11px var(--font-mono); color: var(--color-text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .reload { margin-left: auto; background: none; border: 1px solid var(--color-border); border-radius: 6px; color: var(--color-text-soft); cursor: pointer; padding: 0 7px; font-size: 13px; }
  .sent { border-top: 1px solid var(--color-border); padding: 6px 10px; background: var(--color-terminal-bg); font: 11px var(--font-mono); color: var(--color-text-soft); min-height: 76px; max-height: 96px; overflow-y: auto; }
  .sent.pointed { box-shadow: inset 0 0 0 2px var(--color-highlight); }
  .sent .label { color: var(--color-text-muted); font-family: var(--font-sans, system-ui); font-size: 11px; margin-bottom: 2px; }
  .sent .line { word-break: break-all; }
  .sent .method { color: var(--color-accent-text); }
  .sent .bad { color: var(--color-danger); }
  .sent .quiet { color: var(--color-text-muted); }
  iframe { flex: 1; width: 100%; border: 0; background: #fff; }
</style>
