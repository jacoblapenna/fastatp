import { mount } from 'svelte';
import App from './App.svelte';
import { theme } from './lib/theme.svelte.js';
import './brand/tokens.css';
import './brand/base.css';

theme.init();
mount(App, { target: document.getElementById('app') });
