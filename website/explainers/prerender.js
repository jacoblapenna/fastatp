// Writes the article into dist/index.html, so the page reads with no
// JavaScript and a crawler sees the text. The script then hydrates it.
import { readFileSync, rmSync, writeFileSync } from 'node:fs';
import { page } from './dist-ssr/entry-server.js';

const { head, body } = page();
const file = new URL('./dist/index.html', import.meta.url);
const html = readFileSync(file, 'utf8');
if (!html.includes('<!--app-->') || !html.includes('<!--head-->')) throw new Error('index.html lost its markers');
writeFileSync(file, html.replace('<!--head-->', head).replace('<!--app-->', body));
rmSync(new URL('./dist-ssr', import.meta.url), { recursive: true });
console.log(`prerendered ${body.length} characters of article`);
