import { hydrate, mount } from 'svelte';
import App from './App.svelte';
import { theme } from '$theme';
import '$brand/tokens.css';
import '$brand/base.css';
import './page.css';

theme.init();
// The built page already holds the article as HTML (prerender.js), so it reads
// with no JavaScript; here the live pieces wake up. In `npm run dev` it is empty.
const target = document.getElementById('app');
(target.firstElementChild ? hydrate : mount)(App, { target });
