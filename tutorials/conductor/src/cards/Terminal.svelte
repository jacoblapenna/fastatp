<script>
  // A machine's terminal: each command the guide ran there and what came back.
  // The latest command, and anything still running, is bright; what came
  // before is dimmed so the eye lands on what just happened. Long-running
  // ones (follow.py, tail.py) stay live, like a second terminal.
  let { m } = $props();
  let el;

  let shown = $derived(m.order.slice(-8).map((id) => m.procs[id]));

  $effect(() => {
    // follow every new line down
    shown.reduce((n, p) => n + p.lines.length, 0);
    if (el) el.scrollTop = el.scrollHeight;
  });
</script>

<div class="term" bind:this={el}>
  {#each shown as p, i (p.step)}
    <div class="proc" class:latest={p.running || i === shown.length - 1}>
      <div class="cmd">
        <span class="prompt">{m.name}$</span> {p.display.replace(`./lab.sh ${m.name} `, '')}
        {#if p.running && p.bg}<span class="tag live">running</span>{/if}
        {#if !p.running && p.code < 0}<span class="tag">stopped</span>
        {:else if !p.running && p.code}<span class="tag">exit {p.code}</span>{/if}
      </div>
      {#each p.lines.slice(-60) as line}
        <div class="out" class:bad={/^(refused|no route|connection lost|\{"error")/.test(line)}
          class:good={/verified/.test(line)}>{line}</div>
      {/each}
    </div>
  {:else}
    <div class="idle">nothing run here yet</div>
  {/each}
</div>

<style>
  .term {
    font: 11.5px/1.4 var(--font-mono); background: var(--color-terminal-bg); border-radius: 6px;
    padding: 6px 8px; overflow: auto; height: 100%; min-height: 0;
  }
  .proc { margin-top: 8px; opacity: 0.5; transition: opacity 0.3s; }
  .proc:first-child { margin-top: 0; }
  .proc.latest { opacity: 1; }
  .cmd { color: var(--color-text); word-break: break-all; }
  .prompt { color: var(--color-accent-text); }
  .out { color: var(--color-text-muted); white-space: pre-wrap; word-break: break-all; }
  .latest .out { color: var(--color-text-soft); }
  .out.good { color: var(--color-verified); }
  .out.bad { color: var(--color-danger); }
  .tag { font-size: 10px; border: 1px solid var(--color-border); border-radius: 4px; padding: 0 4px; margin-left: 4px; color: var(--color-text-muted); }
  .tag.live { color: var(--color-verified); border-color: var(--color-verified); animation: pulse 1.6s infinite; }
  .idle { color: var(--color-text-muted); font-style: italic; }
  @keyframes pulse { 50% { opacity: 0.45; } }
</style>
