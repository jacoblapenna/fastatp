<script>
  // The mark alone. Drawn in currentColor so the theme decides its color.
  let { size = '1.25em' } = $props();
</script>

<svg class="mark" viewBox="0 0 24 24" width={size} height={size} aria-hidden="true">
  <!-- three peers and the records between them -->
  <circle cx="5" cy="17" r="3" />
  <circle cx="19" cy="17" r="3" />
  <circle cx="12" cy="5" r="3" />
  <path d="M7.5 15.5 L10.5 7.5 M16.5 15.5 L13.5 7.5 M8 17 H16" />
</svg>

<style>
  .mark { color: var(--color-primary); vertical-align: -0.2em; }
  circle { fill: currentColor; }
  path { stroke: currentColor; stroke-width: 1.6; fill: none; stroke-linecap: round; }
</style>
