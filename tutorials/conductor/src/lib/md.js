// Just enough markdown for captions and key concepts: the guide's own
// inline code, bold, italics and links, and its bulleted or numbered lists,
// one item per line. Escaped first, so nothing in the text can become
// markup it did not ask for.
function inline(text) {
  return text
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1');
}

// The words the guide's key concepts define, from the score. A caption that
// uses one gets it marked, the first time in that caption, and the word's
// line shows on hover or focus. The definitions live in the markdown only.
let terms = null;

export function setGlossary(glossary = []) {
  const plain = (s) => s.replace(/[`*]/g, '');
  const entries = glossary.map((g) => ({ word: g.word, text: plain(g.text) }));
  if (!entries.length) return (terms = null);
  // longest first, so "DID document" wins over "DID"; a word in capitals
  // (DID, CID) matches only in capitals, so the verb "did" is left alone
  const one = (w) => {
    const body = w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const any = w === w.toUpperCase() ? body : [...body].map((c) => (/[a-z]/i.test(c) ? `[${c.toLowerCase()}${c.toUpperCase()}]` : c)).join('');
    return /y$/i.test(w) ? `${any.slice(0, -4)}(?:y|ies)` : `${any}s?`;
  };
  const sorted = [...entries].sort((a, b) => b.word.length - a.word.length);
  terms = {
    pattern: new RegExp(`\\b(?:${sorted.map((e) => `(${one(e.word)})`).join('|')})\\b`, 'g'),
    entries: sorted,
  };
}

// Mark glossary words in rendered html: only in running text, never inside
// code, bold (a word being defined) or a tag.
function mark(html) {
  if (!terms) return html;
  const seen = new Set();
  let shut = 0;
  return html
    .split(/(<[^>]+>)/)
    .map((part) => {
      if (part.startsWith('<')) {
        if (/^<(code|strong)\b/.test(part)) shut += 1;
        else if (/^<\/(code|strong)>/.test(part)) shut -= 1;
        return part;
      }
      if (shut) return part;
      return part.replace(terms.pattern, (hit, ...groups) => {
        const i = groups.findIndex((g, n) => n < terms.entries.length && g !== undefined);
        if (i < 0 || seen.has(i)) return hit;
        seen.add(i);
        const text = terms.entries[i].text.replace(/"/g, '&quot;');
        return `<abbr class="term" tabindex="0" data-def="${text}">${hit}</abbr>`;
      });
    })
    .join('');
}

export function md(text = '') {
  return mark(render(text));
}

function render(text = '') {
  const escaped = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  let out = '';
  let list = null;
  for (const line of escaped.split('\n')) {
    const item = line.match(/^(- |\d+\. )(.*)$/);
    if (item) {
      const kind = item[1] === '- ' ? 'ul' : 'ol';
      if (list !== kind) {
        if (list) out += `</${list}>`;
        out += `<${kind}>`;
        list = kind;
      }
      out += `<li>${inline(item[2])}</li>`;
    } else {
      if (list) out += `</${list}>`;
      list = null;
      out += (out && !out.endsWith('>') ? ' ' : '') + inline(line);
    }
  }
  if (list) out += `</${list}>`;
  return out;
}

// A key concept's words, split for the card: the first sentence or two as
// the claim, the rest a click away. A body that opens with a list is all
// claim (a vocabulary card); a list further down goes with the rest.
export function lead(body = '') {
  const cut = body.indexOf('\n');
  if (/^(- |\d+\. )/.test(body)) return { lead: body, rest: '' };
  const prose = cut < 0 ? body : body.slice(0, cut);
  const list = cut < 0 ? '' : body.slice(cut);
  const sentences = prose.split(/(?<=[.!?])\s+(?=[A-Z`*"(])/);
  let lead = '';
  let i = 0;
  while (i < sentences.length && lead.split(/\s+/).filter(Boolean).length < 22) {
    lead += (lead ? ' ' : '') + sentences[i];
    i += 1;
  }
  return { lead, rest: (sentences.slice(i).join(' ') + list).trim() };
}
