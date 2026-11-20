<script>
  // The mark. One goose where it is small (a header, a tab); the skein of
  // five, the lead bird in the accent, where there is room for it. The birds
  // behind the lead are drawn in currentColor, so the text around them
  // decides their color.
  import { goose, skein, skeinScale as s } from './brand.js';

  let { variant = 'goose', size = '1.25em' } = $props();
</script>

{#if variant === 'skein'}
  <svg class="mark skein" viewBox="0 8 100 84" height={size} aria-hidden="true">
    {#each skein as [x, y], i}
      <path class:lead={i === 0} d={goose} transform="translate({x - 50 * s} {y - 49 * s}) scale({s})" />
    {/each}
  </svg>
{:else}
  <svg class="mark" viewBox="0 18 100 62" height={size} aria-hidden="true">
    <path class="lead" d={goose} />
  </svg>
{/if}

<style>
  .mark { width: auto; vertical-align: -0.2em; flex: none; }
  path { fill: currentColor; }
  .lead { fill: var(--color-mark); }
</style>
