<script>
  // The mark and the name, as the page header shows them.
  import BrandMark from './BrandMark.svelte';
  import { brand } from './brand.js';

  let { product = brand.product } = $props();
</script>

<span class="wordmark"><BrandMark /> {brand.name} <span class="product">{product}</span></span>

<style>
  .wordmark { font-weight: 700; letter-spacing: 0.02em; white-space: nowrap; }
  .product { font-weight: 400; color: var(--color-text-muted); }
</style>
