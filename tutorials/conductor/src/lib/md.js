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

export function md(text = '') {
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
// the claim, the rest a click away. A body with a list is all claim.
export function lead(body = '') {
  if (body.includes('\n')) return { lead: body, rest: '' };
  const sentences = body.split(/(?<=[.!?])\s+(?=[A-Z`*"(])/);
  let lead = '';
  let i = 0;
  while (i < sentences.length && lead.split(/\s+/).filter(Boolean).length < 22) {
    lead += (lead ? ' ' : '') + sentences[i];
    i += 1;
  }
  return { lead, rest: sentences.slice(i).join(' ') };
}
