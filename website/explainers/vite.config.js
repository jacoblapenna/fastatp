import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

// The brand (tokens, base styles, the goose) and the theme switch are the
// conductor's own files, used in place and never copied, so a rebrand in
// one place reaches the tutorials' screen and these pages alike.
const conductor = fileURLToPath(new URL('../../tutorials/conductor/src', import.meta.url));

export default defineConfig({
  // served from a folder of the site (/resources/merkle-trees/), so its files are found beside it
  base: './',
  plugins: [svelte()],
  resolve: { alias: { $brand: `${conductor}/brand`, $theme: `${conductor}/lib/theme.svelte.js` } },
  server: { fs: { allow: ['../..'] } },
});
