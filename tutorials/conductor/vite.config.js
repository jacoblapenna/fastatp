import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

// `npm run dev` serves the page with hot reload and forwards the live
// socket to a running conductor, so a card can be edited while the lab plays.
export default defineConfig({
  plugins: [svelte()],
  server: { proxy: { '/ws': { target: 'ws://localhost:8080', ws: true } } },
});
