// The article as HTML, for prerender.js: the page reads before any script runs.
import { render } from 'svelte/server';
import App from './App.svelte';

export const page = () => render(App);
