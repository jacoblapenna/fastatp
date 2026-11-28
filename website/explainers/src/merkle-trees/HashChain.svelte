<script>
  // What holds what: the commit holds the tree node's hash, the node holds each record's.
</script>

<figure>
  <div class="drawing">
    <svg
      viewBox="0 0 640 250"
      role="img"
      aria-label="A signed commit holds the hash of the tree node, and the tree node holds the hash of each record. Changing one record changes every hash on the path above it."
    >
      <defs>
        <marker id="chain-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">
          <path d="M0 0 L10 5 L0 10 z" class="head" />
        </marker>
      </defs>
      <rect class="box" x="230" y="16" width="180" height="50" rx="6" />
      <text x="320" y="38" text-anchor="middle" font-size="13" font-weight="600">commit</text>
      <text x="320" y="55" text-anchor="middle" font-size="11" class="dim">the only signed block</text>
      <line class="arrow" x1="320" y1="66" x2="320" y2="104" marker-end="url(#chain-arrow)" />
      <text x="330" y="90" font-size="11" class="hot">holds the tree node's hash</text>
      <rect class="node" x="230" y="106" width="180" height="42" rx="6" />
      <text x="320" y="132" text-anchor="middle" font-size="13" font-weight="600">tree node</text>
      <line class="arrow" x1="280" y1="148" x2="170" y2="188" marker-end="url(#chain-arrow)" />
      <line class="arrow" x1="360" y1="148" x2="470" y2="188" marker-end="url(#chain-arrow)" />
      <text x="150" y="168" text-anchor="middle" font-size="11" class="hot">holds each record's hash</text>
      <rect class="box" x="80" y="190" width="180" height="44" rx="6" />
      <text x="170" y="217" text-anchor="middle" font-size="13">record: "hello, LAN"</text>
      <rect class="box" x="380" y="190" width="180" height="44" rx="6" />
      <text x="470" y="217" text-anchor="middle" font-size="13">record: "second note"</text>
    </svg>
  </div>
  <figcaption>
    Change one byte of a record and its hash changes. The tree node held the old hash, so the tree
    node's bytes must change, so <em>its</em> hash changes, and so on up to the commit. One
    signature on the commit therefore covers everything below it.
  </figcaption>
</figure>

<style>
  svg { display: block; width: 100%; height: auto; min-width: 520px; }
  text { fill: var(--color-text); font-family: var(--font-sans); }
  .dim { fill: var(--color-text-muted); }
  .hot { fill: var(--color-accent-text); }
  .box { fill: var(--color-surface-raised); stroke: var(--color-border); }
  .node { fill: none; stroke: var(--color-primary); stroke-width: 1.5; }
  .arrow { stroke: var(--color-text-muted); }
  .head { fill: var(--color-text-muted); }
</style>
