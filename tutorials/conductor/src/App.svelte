<script>
  // One tab: a frame per machine, the whole picture under them, and the
  // guide's key concepts over everything when the score reaches them. What
  // the score points at is outlined and opened; everything else stays
  // folded so the eye has one place to go.
  import { live, view, connect, send, scriptState, visibleUpTo, stepBack, stepNext } from './lib/live.svelte.js';
  import Controls from './cards/Controls.svelte';
  import Caption from './cards/Caption.svelte';
  import MachinePanel from './cards/MachinePanel.svelte';
  import NetDiagram from './cards/NetDiagram.svelte';
  import WireLane from './cards/WireLane.svelte';
  import KeyConcept from './cards/KeyConcept.svelte';
  import CodeCard from './cards/CodeCard.svelte';
  import ChapterMenu from './cards/ChapterMenu.svelte';
  import BrandWordmark from './brand/BrandWordmark.svelte';
  import { theme } from './lib/theme.svelte.js';

  connect();

  let now = $state(Date.now());
  setInterval(() => (now = Date.now()), 400);

  // the step on show: the conductor's, or an earlier visible one while looking back
  let shown = $derived.by(() => {
    if (view.back === 0) return live.cursor;
    const seen = visibleUpTo(live.manifest, live.cursor);
    return seen[Math.max(0, seen.length - 1 - view.back)]?.id ?? live.cursor;
  });
  let script = $derived(scriptState(live.manifest, shown));
  // a key concept over everything: the live one the conductor waits on, or
  // one being looked at again
  let concept = $derived(
    view.back > 0
      ? script.step?.kind === 'concept' ? script.step : null
      : live.concept === null ? null : live.manifest?.steps[live.concept]
  );
  let menu = $state(true); // the chapters, over everything, until one is chosen
  let chapters = $derived(live.manifest?.chapters ?? []);
  let chapter = $derived(chapters.filter((c) => c.start <= live.cursor).at(-1) ?? null);
  function gotIt() {
    if (view.back > 0) view.back -= 1;
    else send('gotit');
  }
  function key(e) {
    // arrows and Enter press the buttons; an open key concept has Enter for Got it
    if (menu || concept || e.target.tagName === 'BUTTON' || e.target.tagName === 'SELECT') return;
    if (e.key === 'ArrowLeft') stepBack();
    if (e.key === 'ArrowRight' || e.key === 'Enter') stepNext();
  }
  // what the current step points at: a key concept's targets, or a caption's
  let pointed = $derived(new Set(concept?.point ?? (script.step?.kind === 'say' ? script.step.point ?? [] : [])));
  let panels = $derived(new Set([...pointed].map((t) => t.split('.')[0])));
  let kept = $derived(script.open); // sections a cue keeps open

  function size(name) {
    if (panels.has(name) || focused(name)) return 'open'; // pointed at, or running: open for this step
    if (script.code) return name === script.code.machine ? 'open' : 'collapsed'; // the code beside its machine
    return script.layout[name] ?? 'open';
  }
  function focused(name) {
    // the terminal to watch: the machine a command runs on, and the one the
    // last command ran on while a caption explains what it showed
    if (concept) return false;
    const kind = script.step?.kind;
    if (['run', 'restart', 'stop'].includes(kind)) return script.step.machine === name;
    if (kind === 'say' && !script.step.point?.length) return script.active === name;
    return false;
  }
</script>

<svelte:window onkeydown={key} />

<div class="app">
  <header>
    <div class="brand"><BrandWordmark /> <span>· playing {live.manifest?.source ?? ''}</span></div>
    <div class="right">
      <button class="chapters" onclick={() => (menu = true)} title="the chapters">
        {chapter ? `${chapter.number} · ${chapter.title}` : 'Chapters'} ▾
      </button>
      <Controls />
      <button class="theme" onclick={() => theme.toggle()} title="light or dark">
        {theme.current === 'dark' ? '☀' : '☾'}
      </button>
    </div>
  </header>
  <Caption {script} back={view.back} />

  <main class="machines">
    {#each live.manifest?.machines ?? [] as name (name)}
      <MachinePanel m={live.machines[name]} size={size(name)} active={script.active === name && !concept}
        focus={focused(name)} {pointed} {kept} {now} />
      {#if script.code?.machine === name && live.manifest?.files?.[script.code.file]}
        {#key script.code.file + script.code.focus}
          <CodeCard name={script.code.file} file={live.manifest.files[script.code.file]} focus={script.code.focus} />
        {/key}
      {/if}
    {/each}
  </main>

  <footer>
    <div class="fold"><NetDiagram {now} pointed={pointed.has('diagram')} kept={kept.has('diagram')} /></div>
    <div class="fold"><WireLane pointed={pointed.has('wire')} kept={kept.has('wire')} /></div>
  </footer>

  <div class="harness">
    the conductor plays the guide for you: these buttons are you typing its commands.
    The records, signatures and tunnels are real.
  </div>

  {#if concept && !menu}
    <KeyConcept step={concept} ask={script.ask} onGotIt={gotIt} />
  {/if}
  {#if menu}
    <ChapterMenu {chapters} current={chapter?.number ?? null} onClose={() => (menu = false)} />
  {/if}
</div>

<style>
  /* one column that never grows past the window: a wide card inside scrolls itself, not the page */
  .app { height: 100%; display: grid; grid-template-columns: minmax(0, 1fr); grid-template-rows: auto auto minmax(0, 1fr) auto auto; }
  header { display: flex; justify-content: space-between; align-items: center; padding: 8px 16px; border-bottom: 1px solid var(--color-border); }
  .brand > span { color: var(--color-text-muted); font-family: var(--font-mono); font-size: var(--text-sm); }
  .right { display: flex; gap: 10px; align-items: center; }
  .theme { padding: 2px 9px; }
  .chapters { font-weight: 600; }
  .machines { display: flex; gap: 10px; padding: 10px 12px; min-height: 0; }
  footer { display: grid; grid-template-columns: minmax(0, 1.1fr) minmax(0, 1fr); gap: 10px; padding: 0 12px 6px; align-items: start; }
  .fold { height: 240px; min-height: 0; }
  .harness { color: var(--color-text-muted); font-size: 11px; padding: 2px 16px 6px; }
</style>
