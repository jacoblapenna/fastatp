<script>
  // One resource for now. A second one makes this a list and a route.
  import BrandWordmark from '$brand/BrandWordmark.svelte';
  import { theme } from '$theme';
  import MerkleTrees from './merkle-trees/MerkleTrees.svelte';
</script>

<div class="page">
  <header>
    <a class="home" href="/resources/"><BrandWordmark product="resources" /></a>
    <button onclick={theme.toggle}>{theme.current === 'dark' ? 'Light' : 'Dark'}</button>
  </header>
  <MerkleTrees />
</div>
