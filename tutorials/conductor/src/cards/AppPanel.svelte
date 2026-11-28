<script>
  // A machine's own web app, as a person meets it: in a browser. "this
  // browser" is the app on this page's own origin. "a second device" is the
  // same app on the other name for this computer (localhost and 127.0.0.1
  // are two origins), so it has storage of its own, and keys of its own:
  // to the browser, it is another device.
  let { app, device = 'this browser', pointed = false } = $props();

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
</script>

<section class="panel" class:pointed data-anchor={anchor}>
  <header>
    <span class="dot"></span>
    <strong>{device}</strong>
    <span class="where" title={src}>{src.replace(/^\/\//, '')}</span>
    <button class="reload" onclick={reload} title="reload, as a browser does">↻</button>
  </header>
  <iframe bind:this={frame} title="{app.machine}: {device}" {src} data-device={anchor}></iframe>
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
  iframe { flex: 1; width: 100%; border: 0; background: #fff; }
</style>
