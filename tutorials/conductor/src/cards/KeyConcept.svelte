<script>
  // A key concept from the guide, over everything: the demo waits, the things
  // it talks about light up, and "Got it" lets the story go on. The card is
  // picture first where the concept has a shape, then the claim in a sentence
  // or two, with the rest of the guide's words a click away.
  import { md, lead } from '../lib/md.js';
  import { picture } from '../lib/live.svelte.js';

  let { step, ask = '', onGotIt } = $props();
  let rings = $state([]);
  let top = $state(false); // the card sits wherever it hides the least of what it points at
  let more = $state(false);

  let svg = $state(null); // the concept's picture, from the conductor's repo
  let split = $derived(lead(step.body));

  $effect(() => {
    const path = step.sketch;
    svg = null;
    if (!path) return;
    // a slow picture must not land on a later step: only the newest ask may answer
    let current = true;
    picture(path).then((text) => current && (svg = text));
    return () => (current = false);
  });

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
    more = false;
    requestAnimationFrame(place); // after the panels it opened have laid out
    const again = setTimeout(place, 450);
    addEventListener('resize', place);
    return () => {
      clearTimeout(again);
      removeEventListener('resize', place);
    };
  });

  function key(e) {
    if (e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return; // with a modifier held the key is the browser's
    if (e.key === 'Escape') return onGotIt();
    if (e.key !== 'Enter') return;
    // Enter on one of the card's own buttons is that button's press: Got it
    // once, or "why it matters" opening. Anywhere else, Enter is Got it, and
    // a button left focused under the card (Next, after a click) stays unpressed.
    if (e.target.closest?.('.card') && e.target.tagName === 'BUTTON') return;
    e.preventDefault();
    onGotIt();
  }
</script>

<svelte:window onkeydown={key} />

<div class="overlay">
  {#each rings as r (r.target)}
    <div class="ring" style="top:{r.top}px;left:{r.left}px;width:{r.width}px;height:{r.height}px"></div>
  {/each}
  <div class="card {step.style}" class:top class:pictured={step.sketch}>
    {#if step.sketch}
      <div class="picture">{#if svg}{@html svg}{/if}</div>
    {/if}
    <div class="words">
      {#if ask}<div class="asked"><span class="before">you were asked</span> {@html md(ask)}</div>{/if}
      <div class="kicker">{{ key: '🔑 Key concept', important: '⚠️ Important', tip: '💡 Did you know' }[step.style]}</div>
      <h2>{@html md(step.title)}</h2>
      <div class="lead">{@html md(split.lead)}</div>
      {#if split.rest}
        <button class="more" onclick={() => (more = !more)} aria-expanded={more}>
          {more ? '▾' : '▸'} {more ? 'less' : 'why it matters, and what it costs'}
        </button>
        {#if more}<div class="rest">{@html md(split.rest)}</div>{/if}
      {/if}
      <button class="gotit" onclick={onGotIt}>Got it ▶</button>
    </div>
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
    width: min(760px, 92vw); max-height: 70vh; overflow: auto;
    background: var(--color-surface); border: 1px solid var(--color-highlight); border-radius: 14px;
    padding: 18px 22px; box-shadow: var(--shadow-strong);
    display: grid; grid-template-columns: minmax(0, 1fr); gap: 20px;
  }
  .card.pictured { width: min(1040px, 94vw); grid-template-columns: 400px minmax(0, 1fr); }
  .card.top { bottom: auto; top: 10vh; }
  .card.important { border-color: var(--color-plaintext); }
  .card.tip { border-color: var(--color-sealed); }
  .picture {
    background: var(--color-terminal-bg); border: 1px solid var(--color-border); border-radius: 10px;
    padding: 10px; min-height: 240px; display: flex; align-items: center;
  }
  .picture :global(svg) { width: 100%; height: 100%; }
  .words { display: flex; flex-direction: column; gap: 8px; min-width: 0; }
  .asked { font-size: 13.5px; color: var(--color-text-soft); background: var(--color-surface-raised); border-radius: 8px; padding: 6px 10px; }
  .before { color: var(--color-accent-text); font-size: 11px; font-weight: 600; letter-spacing: 0.05em; text-transform: uppercase; margin-right: 6px; }
  .kicker { color: var(--color-accent-text); font-size: 12px; font-weight: 600; letter-spacing: 0.05em; }
  .important .kicker { color: var(--color-plaintext); }
  .tip .kicker { color: var(--color-sealed); }
  h2 { margin: 0; font-size: 21px; line-height: 1.25; }
  .lead { font-size: 16px; line-height: 1.5; color: var(--color-text); }
  .lead :global(ul), .rest :global(ul) { margin: 6px 0 0; padding-left: 22px; }
  .lead :global(li), .rest :global(li) { margin: 3px 0; }
  .more { align-self: flex-start; background: none; border: none; padding: 0; color: var(--color-accent-text); font-weight: 600; font-size: 13px; }
  .rest { font-size: 13.5px; line-height: 1.55; color: var(--color-text-soft); background: var(--color-surface-raised); border-radius: 8px; padding: 10px 12px; }
  .gotit { margin-top: auto; align-self: flex-start; background: var(--color-highlight); color: var(--color-on-primary); border: none; font-weight: 600; padding: 8px 20px; }
  .important .gotit { background: var(--color-plaintext); }
  .tip .gotit { background: var(--color-sealed); }
  @keyframes glow { 50% { box-shadow: 0 0 0 8px color-mix(in srgb, var(--color-highlight) 13%, transparent), 0 0 40px color-mix(in srgb, var(--color-highlight) 47%, transparent); } }
  @media (max-width: 900px) { .card.pictured { grid-template-columns: minmax(0, 1fr); } .picture { min-height: 200px; } }
</style>
