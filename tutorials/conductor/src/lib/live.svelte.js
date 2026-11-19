// Everything the page knows, built only from what the conductor verified: the
// score, where the conductor is in it, and what each player published.
// Nothing here is guessed; every card renders from this one object.

export const live = $state({
  connected: false,
  did: null, // the conductor's DID: whose repo the pictures and files are in
  manifest: null,
  cursor: -1,
  mode: 'paused',
  concept: null,
  speed: 1,
  done: false,
  until: null, // a chapter's first step the conductor is hurrying to
  machines: {}, // name -> what that machine reported
  wire: [], // packets the machines put on the LAN, newest last
});

// What one press of Next shows; what Back looks at again.
export const VISIBLE = ['run', 'restart', 'stop', 'say', 'concept', 'chapter'];

// Looking back: how many visible steps behind the conductor the page is
// showing. The machines stay where they are, because every step really ran;
// only the caption, and a key concept's card, are shown again.
export const view = $state({ back: 0 });

export function visibleUpTo(manifest, cursor) {
  return manifest ? manifest.steps.slice(0, cursor + 1).filter((s) => VISIBLE.includes(s.kind)) : [];
}

// The two buttons, and the keys that press them.
export const canBack = () => view.back < visibleUpTo(live.manifest, live.cursor).length - 1;
export const canNext = () => view.back > 0 || (live.mode !== 'playing' && live.mode !== 'step' && !live.done);
export function stepBack() {
  if (canBack()) view.back += 1;
}
export function stepNext() {
  if (view.back > 0) view.back -= 1; // back toward where the machines are
  else if (canNext()) send('step');
}

let socket;

export function connect() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  socket = new WebSocket(`${proto}://${location.host}/ws`);
  socket.onopen = () => (live.connected = true);
  socket.onclose = () => {
    live.connected = false;
    setTimeout(connect, 1000);
  };
  socket.onmessage = (m) => {
    const msg = JSON.parse(m.data);
    if (msg.type === 'hello') {
      live.did = msg.did;
      live.manifest = msg.manifest;
      live.machines = {};
      live.wire = [];
      for (const name of msg.manifest.machines) machine(name);
      status(msg);
      for (const ev of msg.events) apply(ev, false);
    } else if (msg.type === 'status') status(msg);
    else if (msg.type === 'event') apply(msg.event, true);
  };
}

export function send(type, extra = {}) {
  socket?.send(JSON.stringify({ type, ...extra }));
}

function status(msg) {
  if (msg.cursor !== live.cursor) view.back = 0; // the show moved on: stop looking back
  for (const k of ['cursor', 'mode', 'concept', 'speed', 'done', 'until']) live[k] = msg[k];
}

function machine(name) {
  return (live.machines[name] ??= {
    name, identity: null, repo: null, wg: [], stage: 'down', log: [], procs: {}, order: [],
  });
}

function apply(ev, fresh) {
  if (ev.type === 'wire') {
    live.wire.push({ ...ev, seen: fresh ? Date.now() : 0 });
    if (live.wire.length > 500) live.wire.splice(0, live.wire.length - 500);
    return;
  }
  const m = machine(ev.machine);
  if (ev.type === 'stage') m.stage = ev.state;
  else if (ev.type === 'view') {
    m.identity = ev.identity;
    if (ev.repo) m.repo = ev.repo;
    m.wg = ev.wg;
  } else if (ev.type === 'log') m.log.push(ev.line);
  else if (ev.type === 'proc' && ev.state === 'start') {
    m.procs[ev.step] = { step: ev.step, display: ev.display, bg: ev.bg, lines: [], running: true, code: null, ended: 0 };
    m.order.push(ev.step);
  } else if (ev.type === 'proc' && m.procs[ev.step]) {
    Object.assign(m.procs[ev.step], { running: false, code: ev.code, ended: fresh ? Date.now() : 1 });
  } else if (ev.type === 'out') m.procs[ev.step]?.lines.push(ev.line);
}

// --- the pictures, from the conductor's own repo ------------------------------

const pictures = new Map(); // path -> the SVG's text, once fetched

// A picture the guide shows, as a blob in the conductor's repo: fetched once
// with getBlob and inlined, so the theme's colours apply to it.
export async function picture(path) {
  if (pictures.has(path)) return pictures.get(path);
  const cid = live.manifest?.images?.[path]?.cid;
  if (!cid || !live.did) return null;
  const res = await fetch(`/xrpc/com.atproto.sync.getBlob?did=${encodeURIComponent(live.did)}&cid=${cid}`);
  if (!res.ok) return null;
  const text = await res.text();
  pictures.set(path, text);
  return text;
}

// --- who is who, from what the machines published about themselves ---------

export function nameOf({ key, ip, did }) {
  for (const m of Object.values(live.machines)) {
    const id = m.identity;
    if (!id) continue;
    if (key && id.wg_public_key === key) return m.name;
    if (ip && (id.lan_ip === ip || id.tunnel_ip === ip)) return m.name;
    if (did && (id.did === did || id.channel === did)) return m.name;
  }
  return null;
}

// --- where the script is ------------------------------------------------------

export function scriptState(manifest, cursor) {
  const s = { part: '', section: '', say: '', step: null, active: null, layout: {}, open: new Set(), code: null, chapter: null };
  if (!manifest) return s;
  for (const name of manifest.machines) s.layout[name] = 'open';
  for (const step of manifest.steps.slice(0, cursor + 1)) {
    if (step.kind === 'chapter') [s.chapter, s.part, s.section, s.say, s.code] = [step, '', '', '', null];
    if (step.kind === 'heading' && step.level === 2) [s.part, s.section] = [step.text, ''];
    if (step.kind === 'heading' && step.level === 3) s.section = step.text;
    if (step.kind === 'heading') [s.say, s.code] = ['', null];
    // a command shows the file it runs, beside its machine, until the next command
    if (step.kind === 'run') s.code = step.code ? { ...step.code, machine: step.machine } : null;
    if (step.kind === 'say') s.say = step.text;
    if (step.kind === 'run' || step.kind === 'restart') s.active = step.machine;
    if (step.kind === 'layout' && (step.action === 'open' || step.action === 'close')) {
      for (const target of step.targets) step.action === 'open' ? s.open.add(target) : s.open.delete(target);
    } else if (step.kind === 'layout') {
      if (step.action === 'focus') for (const name of manifest.machines) s.layout[name] = 'collapsed';
      for (const name of step.machines) s.layout[name] = step.action === 'collapse' ? 'collapsed' : 'open';
    }
  }
  s.step = manifest.steps[cursor] ?? null;
  return s;
}
