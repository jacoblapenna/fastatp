<script>
  // A repo's records in the order they sort, which for TID keys is the order
  // they were signed: the newest at the bottom, followed down as they land
  // unless the viewer has scrolled up to read.
  import { tick } from 'svelte';
  import RecordCard from './RecordCard.svelte';

  let { records = [], grow = false } = $props();
  let box;
  let stick = true; // at the bottom: keep following the newest

  function scrolled() {
    if (box) stick = box.scrollTop + box.clientHeight >= box.scrollHeight - 24;
  }
  $effect(() => {
    records.length; // each new record
    tick().then(() => {
      if (box && stick) box.scrollTop = box.scrollHeight;
    });
  });
</script>

<div class="records" class:grow bind:this={box} onscroll={scrolled}>
  {#each records as record (record.path)}
    <RecordCard {record} />
  {:else}
    <div class="muted">empty</div>
  {/each}
</div>

<style>
  .records { max-height: 96px; overflow: auto; }
  .records.grow { max-height: none; flex: 1 1 0; min-height: 60px; }
  .muted { color: var(--color-text-muted); font-size: 11.5px; }
</style>
