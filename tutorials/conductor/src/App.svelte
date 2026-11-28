<script>
  // One tab: a frame per machine, the whole picture under them, and the
  // guide's key concepts over everything when the score reaches them. What
  // the score points at is outlined and opened; everything else stays
  // folded so the eye has one place to go.
  import { live, view, connect, send, scriptState, visibleUpTo, stepBack, stepNext } from './lib/live.svelte.js';
  import Controls from './cards/Controls.svelte';
  import Caption from './cards/Caption.svelte';
  import MachinePanel from './cards/MachinePanel.svelte';
  import ConductorPanel from './cards/ConductorPanel.svelte';
  import AppPanel from './cards/AppPanel.svelte';
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
    if (menu || concept || e.target.tagName === 'SELECT') return;
    // with a modifier held the key is the browser's (Alt+Left is its Back)
    if (e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
    if (e.key === 'ArrowLeft') stepBack();
    if (e.key === 'ArrowRight') stepNext();
    // a focused button takes Enter as its own press, so only step for Enter elsewhere
    if (e.key === 'Enter' && e.target.tagName !== 'BUTTON') stepNext();
  }
  // what the current step points at: a key concept's targets, or a caption's
  let pointed = $derived(new Set(concept?.point ?? (script.step?.kind === 'say' ? script.step.point ?? [] : [])));
  let panels = $derived(new Set([...pointed].map((t) => t.split('.')[0])));
  let kept = $derived(script.open); // sections a cue keeps open
  // A score that commits records shows the conductor's repo beside the
  // machines, and once a commit is what last happened, the repos are what
  // to watch: each machine's fills its panel until the next command.
  let commits = $derived(live.manifest?.steps.some((s) => s.kind === 'commit') ?? false);
  // Machines that keep private repos (Hoot's agents) show them, all at once,
  // whenever the step is not a command: the repos are the screen.
  let privateRepos = $derived(Object.values(live.machines).some((m) => m.private?.length));
  let app = $derived(live.manifest?.app ?? null); // a machine's own web app, embedded
  let command = $derived(['run', 'restart', 'stop'].includes(script.step?.kind));
  let repos = $derived((commits && script.last === 'commit') || (privateRepos && !command));

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
    {#if app}
      <!-- the app, as a person meets it; the machines beside it are backstage -->
      <AppPanel {app} pointed={pointed.has('app')} />
      {#if kept.has('phone') || pointed.has('phone')}
        <AppPanel {app} device="a second device" pointed={pointed.has('phone')} />
      {/if}
    {/if}
    {#each live.manifest?.machines ?? [] as name (name)}
      <MachinePanel m={live.machines[name]} size={size(name)} active={script.active === name && !concept}
        focus={focused(name) && !repos} {repos} {pointed} {kept} {now} backstage={!!app} />
      {#if script.code?.machine === name && live.manifest?.files?.[script.code.file]}
        {#key script.code.file + script.code.focus}
          <CodeCard name={script.code.file} file={live.manifest.files[script.code.file]} focus={script.code.focus} />
        {/key}
      {/if}
    {/each}
    {#if commits}<ConductorPanel {pointed} wide={repos} />{/if}
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
  /* a folded card is one line; the footer makes room only for an open one */
  .fold { min-height: 0; }
  .fold:has(:global(.open)) { height: 240px; } /* .open is on the card inside, another component */
  .harness { color: var(--color-text-muted); font-size: 11px; padding: 2px 16px 6px; }
</style>
