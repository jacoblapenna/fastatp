<script>
  // One resource for now. A second one makes this a list and a route.
  // The header is the site's own: website/build.py puts it above this page
  // when it carries the build to fastatp.com, so `npm run dev` shows none.
  import MerkleTrees from './merkle-trees/MerkleTrees.svelte';
</script>

<div class="page">
  <MerkleTrees />
</div>
