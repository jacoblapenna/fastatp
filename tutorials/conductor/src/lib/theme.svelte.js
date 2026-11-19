// Dark or light: the reader's system preference until they choose, then
// their choice, remembered. Everything else follows from data-theme.
let current = $state('dark');

function apply(value) {
  current = value;
  document.documentElement.setAttribute('data-theme', value);
}

export const theme = {
  get current() {
    return current;
  },
  toggle() {
    const next = current === 'dark' ? 'light' : 'dark';
    apply(next);
    try {
      localStorage.setItem('theme', next);
    } catch {
      // private mode: the choice lasts this visit only
    }
  },
  init() {
    let saved = null;
    try {
      saved = localStorage.getItem('theme');
    } catch {
      // private mode
    }
    const asked = new URLSearchParams(location.search).get('theme'); // ?theme=light, for screenshots and docs
    const pick = ['dark', 'light'].includes(asked) ? asked : saved;
    apply(pick ?? (matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'));
  },
};
