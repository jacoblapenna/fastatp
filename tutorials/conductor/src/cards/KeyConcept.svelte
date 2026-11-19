<script>
  // A key concept from the guide, over everything: the demo waits, the things
  // it talks about light up, and "Got it" lets the story go on.
  import { md } from '../lib/md.js';

  let { step, onGotIt } = $props();
  let rings = $state([]);
  let top = $state(false); // the card sits wherever it hides the least of what it points at

  function place() {
    rings = (step.point ?? []).flatMap((target) => {
      const el = document.querySelector(`[data-anchor="${target}"]`)
        ?? document.querySelector(`[data-anchor="${target.split('.')[0]}"]`);
      if (!el) return [];
      const r = el.getBoundingClientRect();
      return [{ target, top: r.top - 6, left: r.left - 6, width: r.width + 12, height: r.height + 12 }];
    });
    const below = rings.filter((r) => r.top + r.height > innerHeight * 0.55).length;
    top = below > rings.length - below;
  }

  $effect(() => {
    step;
    requestAnimationFrame(place); // after the panels it opened have laid out
    const again = setTimeout(place, 450);
    addEventListener('resize', place);
    return () => {
      clearTimeout(again);
      removeEventListener('resize', place);
    };
  });

  function key(e) {
    if (e.key === 'Enter' || e.key === 'Escape') onGotIt();
  }
</script>

<svelte:window onkeydown={key} />

<div class="overlay">
  {#each rings as r (r.target)}
    <div class="ring" style="top:{r.top}px;left:{r.left}px;width:{r.width}px;height:{r.height}px"></div>
  {/each}
  <div class="card {step.style}" class:top>
    <div class="kicker">{{ key: '🔑 Key concept', important: '⚠️ Important', tip: '💡 Did you know' }[step.style]}</div>
    <h2>{@html md(step.title)}</h2>
    <div class="text">{@html md(step.body)}</div>
    <button onclick={onGotIt}>Got it</button>
  </div>
</div>

<style>
  .overlay { position: fixed; inset: 0; background: var(--color-backdrop); z-index: 10; }
  .ring {
    position: fixed; border: 2px solid var(--color-highlight); border-radius: 12px;
    box-shadow: 0 0 0 4px color-mix(in srgb, var(--color-highlight) 20%, transparent), 0 0 30px color-mix(in srgb, var(--color-highlight) 33%, transparent);
    animation: glow 1.8s ease-in-out infinite; pointer-events: none;
  }
  .card {
    position: fixed; left: 50%; bottom: 6vh; transform: translateX(-50%);
    width: min(760px, 92vw); max-height: 60vh; overflow: auto;
    background: var(--color-surface); border: 1px solid var(--color-highlight); border-radius: 14px;
    padding: 18px 22px; box-shadow: var(--shadow-strong);
  }
  .card.top { bottom: auto; top: 12vh; }
  .card.important { border-color: var(--color-plaintext); }
  .card.tip { border-color: var(--color-sealed); }
  .tip .kicker { color: var(--color-sealed); }
  .tip button { background: var(--color-sealed); }
  .kicker { color: var(--color-highlight); font-size: 12px; font-weight: 600; letter-spacing: 0.05em; }
  .important .kicker { color: var(--color-plaintext); }
  h2 { margin: 4px 0 8px; font-size: 19px; line-height: 1.3; }
  .text { margin: 0 0 14px; font-size: 14.5px; line-height: 1.55; }
  .text :global(ul), .text :global(ol) { margin: 6px 0 0; padding-left: 22px; }
  .text :global(li) { margin: 3px 0; }
  button { background: var(--color-highlight); color: var(--color-on-primary); border: none; font-weight: 600; padding: 7px 18px; }
  .important button { background: var(--color-plaintext); }
  @keyframes glow { 50% { box-shadow: 0 0 0 8px color-mix(in srgb, var(--color-highlight) 13%, transparent), 0 0 40px color-mix(in srgb, var(--color-highlight) 47%, transparent); } }
</style>
