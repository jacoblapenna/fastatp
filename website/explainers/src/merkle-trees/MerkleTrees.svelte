<script>
  // The repo's tree: why its order never changes and its shape does.
  import HashChain from './HashChain.svelte';
  import RollTree from './RollTree.svelte';
  import WalkTree from './WalkTree.svelte';

  // The live pieces roll real keys and draw on a canvas, so they start in the
  // browser. The article around them is written into the page when it is built
  // (prerender.js) and reads without them.
  let live = $state(false);
  $effect(() => {
    live = true;
  });
</script>

<article>
  <section>
    <span class="kicker">The repo's tree</span>
    <h1>The order never changes. The shape does.</h1>
    <p class="lede">
      Write the same notes into a fresh repo twice and count its blocks. You may get a different
      number of tree nodes each time. Nothing is wrong, because "order" and "shape" are two
      separate facts about the tree.
    </p>
    <div class="answer">
      <div>
        <b>Order: always the same</b>
        <span>Records are kept sorted by key. The keys are timestamps, so that is the order they were written in. Every run.</span>
      </div>
      <div>
        <b>Shape: differs per run</b>
        <span>How many tree nodes there are, and which node a record sits in, is worked out from a hash of each key. New keys, new shape.</span>
      </div>
    </div>
  </section>

  <section>
    <span class="kicker">Step 1</span>
    <h2>A hash is a fingerprint of some bytes</h2>
    <p>
      Feed any bytes to SHA-256 and you get a short, fixed-size number back. The same bytes always
      give the same number. Change one byte and the number is completely different, in a way nobody
      can predict or steer.
    </p>
    <p>In the repo, every block is stored under the hash of its own bytes. That hash is the block's <strong>CID</strong>.</p>
  </section>

  <section>
    <span class="kicker">Step 2</span>
    <h2>"Merkle" means each block holds the hashes of the blocks under it</h2>
    {#if live}<HashChain />{:else}<p class="said">This part is live and needs JavaScript.</p>{/if}
    <p>
      That is the whole "Merkle" idea. Notice it says nothing about how many tree nodes there are.
      Any shape works, as long as each block holds the hashes of the ones under it.
    </p>
    <p>
      Why have tree nodes at all? Without them the index would be one long list of every key in a
      single block. Every write would rewrite and re-hash the whole list, and proving one record is
      in your repo would mean sending someone all of it. With a tree, a write changes only the
      nodes between that record and the commit.
    </p>
  </section>

  <section>
    <span class="kicker">Step 3</span>
    <h2>"Search tree" means the entries are kept sorted by key</h2>
    <p>
      Each record is filed under a key: its collection name, a slash, and a record key, such as
      <code>com.example.tutorial.note/3mz2b56hcz64m</code>. The tree keeps those keys in sorted
      order so a record can be found without reading everything.
    </p>
    <p>
      FastATP makes record keys from the clock, so sorted order is the order the notes were
      written. <strong>This is the part that is constant.</strong> Read the tree left to right and
      you always get the first note, then the second, then the third.
    </p>
  </section>

  <section>
    <span class="kicker">Step 4</span>
    <h2>The surprise: a key's hash decides how high it sits</h2>
    <p>
      A tree needs some rule for which entries sit near the top and which sit lower. atproto's rule
      is: hash the key, then count how many pairs of zero bits the hash starts with. That count is
      the entry's <strong>layer</strong>.
    </p>
    <div class="tablewrap">
      <table>
        <thead><tr><th>Hash starts with</th><th>Layer</th><th>How often</th></tr></thead>
        <tbody>
          <tr><td class="mono"><span class="rest">01…, 10… or 11…</span></td><td>0 (the bottom)</td><td>3 keys in 4</td></tr>
          <tr><td class="mono"><span class="zero">00</span> <span class="rest">then a non-zero pair</span></td><td>1</td><td>about 1 in 5</td></tr>
          <tr><td class="mono"><span class="zero">00 00</span> <span class="rest">then a non-zero pair</span></td><td>2</td><td>about 1 in 20</td></tr>
        </tbody>
      </table>
    </div>
    <p>
      Think of a ruler. Every tick sits at its exact place along the ruler, but some ticks are tall
      and most are short. The key is the place; the layer is only the tick's height.
    </p>
    <p>
      The height behaves like rolling a die for each key. And because a record key contains the
      time down to the microsecond, <strong>every run rolls fresh dice</strong>, even though the
      note says the same thing.
    </p>
    <ul>
      <li>Entries on the same layer share one tree node.</li>
      <li>An entry one layer up gets a node of its own, above the others.</li>
      <li>If the gap is two layers or more, empty nodes fill it.</li>
    </ul>
  </section>

  <section>
    <span class="kicker">Try it</span>
    <h2>Alice writes three notes. Roll the keys again.</h2>
    <p>
      This runs the real rule in your browser: real record keys from the clock, real SHA-256, and
      the same tree-building steps the library uses.
    </p>
    {#if live}<RollTree />{:else}<p class="said">This part is live and needs JavaScript.</p>{/if}
    <p>
      Empty nodes look wasteful here, and they only show up in tiny repos. A key reaches layer 5
      about once in a thousand; with three records nothing fills the layers under it. In a repo of
      a few thousand records every layer is occupied and there are no gaps.
    </p>
  </section>

  <section>
    <span class="kicker">Why build it this way</span>
    <h2>Because the shape depends on nothing but the keys</h2>
    <p>
      The dice are not random in the usual sense. Press "Rebuild from the same keys" and you get the
      identical tree every time. The shape does not depend on who built it, when, or in what order
      the records were added.
    </p>
    <p>
      That is the payoff. Two machines that hold the same records always arrive at the same tree,
      byte for byte, and so at the same top hash. They can tell whether they agree by comparing one
      hash, and when they differ they can walk down to find exactly which records are missing. A
      tree whose shape depended on the order of writes could not do that.
    </p>
    <p>
      The hash also keeps the tree balanced with no extra work. It scatters any keys evenly, even
      perfectly sequential ones, so about one entry in four is promoted at each layer.
    </p>
    <div class="tablewrap">
      <table>
        <thead><tr><th>Same on every run</th><th>Different on every run</th></tr></thead>
        <tbody>
          <tr><td>The notes and their text</td><td>The record keys (they contain the time)</td></tr>
          <tr><td>The order of the entries</td><td>Each key's layer</td></tr>
          <tr><td>One commit, one block per record</td><td>How many tree nodes, and their sizes</td></tr>
          <tr><td>Every block holds the hashes of those under it</td><td>Every hash, since the keys and times differ</td></tr>
        </tbody>
      </table>
    </div>
  </section>

  <section>
    <span class="kicker">Walk it</span>
    <h2>Finding one record in a big repo</h2>
    <p>
      To find or check one record you start at the top node and walk down. At each node the keys
      are sorted, so you can see which gap your key falls in, and you follow that one pointer.
      Everything else in the repo is never read.
    </p>
    <p>
      The cost is the length of that walk, not the size of the repo. Because about one key in four
      is promoted to each higher layer, the walk grows by roughly one node each time the repo gets
      four times bigger.
    </p>
    {#if live}<WalkTree />{:else}<p class="said">This part is live and needs JavaScript.</p>{/if}
    <p>
      Checking the record afterwards is the same walk in reverse: hash the record and compare it
      with the hash its node holds, hash that node and compare with its parent, and so on up to the
      commit, whose signature you check once.
    </p>
  </section>

  <section>
    <span class="kicker">Read more</span>
    <h2>Where to go from here</h2>
    <ul>
      <li><a href="https://atproto.com/specs/repository">atproto's repository spec</a>: the section on the MST gives the layer rule in a few paragraphs.</li>
      <li><a href="https://inria.hal.science/hal-02303490">Merkle Search Trees</a>, Auvolat and Taïani, 2019: the paper the design comes from.</li>
      <li>In FastATP, <code>fastatp/repo/mst.py</code>: <code>key_height</code> is the layer rule in ten lines, and <code>_build_node</code> is the whole builder.</li>
    </ul>
  </section>
</article>

<style>
  .answer { background: var(--color-surface); border: 1px solid var(--color-border); border-radius: var(--radius-lg); padding: 18px 20px; display: grid; gap: 14px; grid-template-columns: 1fr 1fr; }
  .answer div { min-width: 0; }
  .answer b { display: block; color: var(--color-text); margin-bottom: 2px; }
  .answer span { color: var(--color-text-muted); font-size: 14px; }
  @media (max-width: 520px) { .answer { grid-template-columns: 1fr; } }
</style>
