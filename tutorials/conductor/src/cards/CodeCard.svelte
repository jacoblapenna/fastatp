<script>
  // One lab file, on show while the guide talks about it. The machines fold
  // beside it: nothing has run yet, and the code is what to look at.
  let { code } = $props();

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
  let lines = $derived(highlight(code.text));
</script>

<section class="code" data-anchor="code">
  <header>{code.name} <span class="meta">{lines.length} lines · the app's own code</span></header>
  <pre>{#each lines as line, i}<span class="n">{i + 1}</span>{@html line}
{/each}</pre>
</section>

<style>
  .code {
    flex: 3 1 0; min-width: 0; display: flex; flex-direction: column;
    background: var(--color-surface); border: 1px solid var(--color-border); border-radius: 10px; overflow: hidden;
  }
  header {
    display: flex; gap: 6px; align-items: baseline; padding: 8px 10px; border-bottom: 1px solid var(--color-border);
    font: 600 13px var(--font-mono); color: var(--color-text);
  }
  .meta { margin-left: auto; font: 11px var(--font-mono); color: var(--color-text-muted); }
  pre {
    flex: 1; min-height: 0; margin: 0; padding: 8px 10px; overflow: auto;
    font: 12px/1.5 var(--font-mono); color: var(--color-text-soft); background: var(--color-terminal-bg); tab-size: 4;
  }
  .n { display: inline-block; width: 3.2em; color: var(--color-text-muted); opacity: 0.6; user-select: none; }
  pre :global(.cmt) { color: var(--color-verified); font-style: italic; }
  pre :global(.str) { color: var(--color-plaintext); }
  pre :global(.kw) { color: var(--color-primary); }
  pre :global(.deco) { color: var(--color-sealed); }
</style>
