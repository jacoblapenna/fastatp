<script>
  // One lab file, beside the machine whose command runs it, open at the part
  // the command reaches. The other machines fold while it is up: the code is
  // what to look at, and the guide's line on what the file is for heads it.
  import { md } from '../lib/md.js';

  let { name, file, focus = '' } = $props();
  let pre;

  const KEYWORDS = new Set(
    'import from def class return await async if elif else for while in not and or is None True False try except finally raise with as pass lambda yield global del assert'.split(' ')
  );
  const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const span = (cls, s) => `<span class="${cls}">${esc(s)}</span>`;

  // A small tokenizer, line by line, carrying an open docstring across lines:
  // strings and comments are lit whole, keywords only in the code between them.
  function highlight(text) {
    let open = null; // the triple quote a docstring opened with
    return text.split('\n').map((line) => {
      let out = '';
      let i = 0;
      while (i < line.length) {
        const rest = line.slice(i);
        let m;
        if (open) {
          const j = rest.indexOf(open);
          if (j === -1) { out += span('str', rest); break; }
          out += span('str', rest.slice(0, j + 3)); i += j + 3; open = null;
        } else if ((m = rest.match(/^[rbfRBF]{0,2}("""|''')/))) {
          const q = m[1];
          const j = rest.indexOf(q, m[0].length);
          if (j === -1) { out += span('str', rest); open = q; break; }
          out += span('str', rest.slice(0, j + 3)); i += j + 3;
        } else if (rest[0] === '#') {
          out += span('cmt', rest); break;
        } else if ((m = rest.match(/^[rbfRBF]{0,2}("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')/))) {
          out += span('str', m[0]); i += m[0].length;
        } else if ((m = rest.match(/^@\w[\w.]*/))) {
          out += span('deco', m[0]); i += m[0].length;
        } else if ((m = rest.match(/^[A-Za-z_]\w*/))) {
          out += KEYWORDS.has(m[0]) ? span('kw', m[0]) : esc(m[0]); i += m[0].length;
        } else {
          out += esc(rest[0]); i += 1;
        }
      }
      return out;
    });
  }
  let raw = $derived(file.text.split('\n'));
  let lines = $derived(highlight(file.text));
  let at = $derived(focus ? raw.findIndex((l) => l.includes(focus)) : -1);

  $effect(() => {
    // open at the line the command reaches, a little below the top
    at;
    const target = pre?.querySelector('.line.at');
    if (target) pre.scrollTop = Math.max(0, target.offsetTop - pre.offsetTop - 28);
    else if (pre) pre.scrollTop = 0;
  });
</script>

<section class="code" data-anchor="code">
  <header>
    <span class="name">{name}</span>
    {#if file.gloss}<span class="gloss">{@html md(file.gloss)}</span>{/if}
  </header>
  <pre bind:this={pre}>{#each lines as line, i}<span class="line" class:at={i === at}><span class="n">{i + 1}</span>{@html line}</span>
{/each}</pre>
</section>

<style>
  .code {
    flex: 2.2 1 0; min-width: 0; display: flex; flex-direction: column;
    background: var(--color-surface); border: 1px solid var(--color-border); border-radius: 10px; overflow: hidden;
  }
  header { display: flex; gap: 10px; align-items: baseline; padding: 8px 10px; border-bottom: 1px solid var(--color-border); min-width: 0; }
  .name { font: 600 13px var(--font-mono); color: var(--color-text); flex: none; }
  .gloss { font-size: 12px; color: var(--color-text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .gloss :global(code) { font-size: 11px; }
  pre {
    flex: 1; min-height: 0; margin: 0; padding: 8px 0; overflow: auto;
    font: 12px/1.5 var(--font-mono); color: var(--color-text-soft); background: var(--color-terminal-bg); tab-size: 4;
  }
  .line { display: block; padding: 0 10px; }
  .line.at { background: color-mix(in srgb, var(--color-highlight) 16%, transparent); box-shadow: inset 3px 0 0 var(--color-highlight); }
  .n { display: inline-block; width: 3.2em; color: var(--color-text-muted); opacity: 0.6; user-select: none; }
  pre :global(.cmt) { color: var(--color-verified); font-style: italic; }
  pre :global(.str) { color: var(--color-plaintext); }
  pre :global(.kw) { color: var(--color-accent-text); }
  pre :global(.deco) { color: var(--color-sealed); }
</style>
