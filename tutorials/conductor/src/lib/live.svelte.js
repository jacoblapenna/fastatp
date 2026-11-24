// Everything the page knows, built only from what the conductor verified: the
// score, where the conductor is in it, and what each player published.
// Nothing here is guessed; every card renders from this one object.

import { setGlossary } from './md.js';
import { play, playable, unlock } from './sound.js';

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
  commits: {}, // step -> a record the conductor signed for members, and their answers so far
  conductor: emptyRepo(), // the conductor's own repo: every record it signs, as it signs it
});

function emptyRepo() {
  return { before: 0, count: 0, rev: '', records: [] };
}

const KEEP = 400; // records the conductor's list keeps on screen; its count says how many there are

// What one press of Next shows; what Back looks at again.
export const VISIBLE = ['run', 'restart', 'stop', 'commit', 'say', 'concept', 'chapter', 'ask'];

// Looking back: how many visible steps behind the conductor the page is
// showing. The machines stay where they are, because every step really ran;
// only the caption, and a key concept's card, are shown again.
export const view = $state({ back: 0 });

export function visibleUpTo(manifest, cursor) {
  return manifest ? manifest.steps.slice(0, cursor + 1).filter((s) => VISIBLE.includes(s.kind)) : [];
}

// The two buttons, and the keys that press them.
export const canBack = () => view.back < visibleUpTo(live.manifest, live.cursor).length - 1;
export const canNext = () =>
  view.back > 0 || (live.connected && live.mode !== 'playing' && live.mode !== 'step' && !live.done);
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
      setGlossary(msg.manifest.glossary);
      live.manifest = msg.manifest;
      live.machines = {};
      live.wire = [];
      live.commits = {};
      live.conductor = emptyRepo();
      for (const name of msg.manifest.machines) machine(name);
      status(msg);
      for (const ev of msg.events) apply(ev, false);
    } else if (msg.type === 'status') status(msg);
    else if (msg.type === 'event') apply(msg.event, true);
  };
}

// A press reaches the conductor only over an open socket. False means it did
// not go: Next and the chapter cards are disabled while disconnected, a key
// concept's card stays open until its Got it lands, and this is the backstop.
export function send(type, extra = {}) {
  unlock(); // every press comes through here: the first one opens the speaker
  if (socket?.readyState !== WebSocket.OPEN) return false;
  socket.send(JSON.stringify({ type, ...extra }));
  return true;
}

function status(msg) {
  if (msg.cursor !== live.cursor) view.back = 0; // the show moved on: stop looking back
  for (const k of ['cursor', 'mode', 'concept', 'speed', 'done', 'until']) live[k] = msg[k];
}

// Only the machines the score names get a panel, and only a whole number is
// a step: a key from a message never reaches an object unchecked.
function machine(name) {
  if (!live.manifest?.machines?.includes(name)) return null;
  return (live.machines[name] ??= {
    name, identity: null, repo: null, wg: [], stage: 'down', log: [], procs: {}, order: [], landed: [],
  });
}

// A machine's repo as its view reports it, with the records the conductor saw
// land since: a view is a second behind, so a just-landed record would vanish
// and come back. One the view still lacks after a few seconds is let go.
function merged(records, m) {
  const now = Date.now();
  m.landed = m.landed.filter((r) => now - r.kept < 5000);
  const byPath = new Map(records.map((r) => [r.path, r]));
  for (const r of m.landed) if (byPath.has(r.path)) byPath.get(r.path).landed ||= r.landed;
  const extra = m.landed.filter((r) => !byPath.has(r.path));
  return { rev: m.repo?.rev ?? '', records: [...records, ...extra].sort((a, b) => (a.path < b.path ? -1 : 1)) };
}

// Each machine is heard from its own place, left to right in cast order.
function pan(name) {
  const cast = live.manifest?.machines ?? [];
  const i = cast.indexOf(name);
  return cast.length > 1 && i >= 0 ? -0.7 + (1.4 * i) / (cast.length - 1) : 0;
}

function apply(ev, fresh) {
  const step = Number(ev.step);
  if (ev.type === 'repo') {
    live.conductor.before = live.conductor.count = Number(ev.count) || 0;
    return;
  }
  if (ev.type === 'signed') {
    const c = live.conductor;
    // a record updated in place (the show now, the tempo) moves to the end: it was just signed
    const i = c.records.findIndex((r) => r.path === ev.path);
    if (i >= 0) c.records.splice(i, 1);
    c.records.push({ path: ev.path, cid: ev.cid, value: ev.value, landed: fresh ? Date.now() : 0 });
    if (c.records.length > KEEP) c.records.splice(0, c.records.length - KEEP);
    Object.assign(c, { count: ev.count, rev: ev.rev });
    return;
  }
  if (ev.type === 'record') {
    const m = machine(ev.machine);
    if (!m) return;
    // a record in the machine's own repo, verified by the conductor the moment
    // it landed: shown now, until the machine's own view of its repo has it too
    const rec = { path: ev.path, cid: ev.cid, value: ev.value, landed: fresh ? Date.now() : 0, kept: Date.now() };
    m.landed = [...m.landed.filter((r) => r.path !== ev.path), rec];
    if (m.repo) m.repo = merged(m.repo.records, m);
    else m.repo = { rev: '', records: [rec] };
    return;
  }
  if (ev.type === 'commit' && Number.isInteger(step)) {
    live.commits[step] = { uri: ev.uri, machines: ev.machines ?? [], answers: {}, silent: null, index: ev.index ?? 1, of: ev.of ?? 1 };
    return;
  }
  if (ev.type === 'commit-end' && live.commits[step]) {
    live.commits[step].silent = ev.silent ?? [];
    return;
  }
  if (ev.type === 'answer') {
    if (!machine(ev.machine)) return;
    const note = playable(ev.record);
    live.commits[step] && (live.commits[step].answers[ev.machine] = { text: ev.record?.text ?? '', ms: note?.ms ?? 0 });
    // only a note landing now is played: a tab opened late does not hear the past all at once
    if (fresh && note) play(note, pan(ev.machine));
    return;
  }
  if (ev.type === 'wire') {
    live.wire.push({ ...ev, seen: fresh ? Date.now() : 0 });
    if (live.wire.length > 500) live.wire.splice(0, live.wire.length - 500);
    return;
  }
  const m = machine(ev.machine);
  if (!m) return;
  if (['proc', 'out'].includes(ev.type) && !Number.isInteger(step)) return;
  if (ev.type === 'stage') m.stage = ev.state;
  else if (ev.type === 'view') {
    m.identity = ev.identity;
    if (ev.repo) m.repo = ev.repo;
    m.wg = ev.wg;
  } else if (ev.type === 'log') m.log.push(ev.line);
  else if (ev.type === 'proc' && ev.state === 'start') {
    m.procs[step] = { step, display: ev.display, bg: ev.bg, lines: [], running: true, code: null, ended: 0 };
    // a chapter played again runs the same step again: it moves to the end, once
    const earlier = m.order.indexOf(step);
    if (earlier >= 0) m.order.splice(earlier, 1);
    m.order.push(step);
  } else if (ev.type === 'proc' && m.procs[step]) {
    Object.assign(m.procs[step], { running: false, code: ev.code, ended: fresh ? Date.now() : 1 });
  } else if (ev.type === 'out') m.procs[step]?.lines.push(ev.line);
}

// --- the pictures, from the conductor's own repo ------------------------------

const pictures = new Map(); // whose repo and which bytes -> the SVG's text, once fetched

// A picture the guide shows, as a blob in the conductor's repo: fetched once
// with getBlob and inlined, so the theme's colours apply to it.
export async function picture(path) {
  const cid = live.manifest?.images?.[path]?.cid;
  if (!cid || !live.did) return null;
  // kept by DID and CID, not by path: a conductor that restarted with a
  // changed picture has a different CID, and the old bytes are not shown for it
  const key = `${live.did}/${cid}`;
  if (pictures.has(key)) return pictures.get(key);
  try {
    const res = await fetch(`/xrpc/com.atproto.sync.getBlob?did=${encodeURIComponent(live.did)}&cid=${cid}`);
    if (!res.ok) return null;
    const text = await res.text();
    pictures.set(key, text);
    return text;
  } catch {
    return null; // the conductor is away: no picture this time, ask again next step
  }
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
  const s = { part: '', section: '', say: '', ask: '', step: null, active: null, layout: {}, open: new Set(), code: null, chapter: null, last: null };
  if (!manifest) return s;
  for (const name of manifest.machines) s.layout[name] = 'open';
  let answered = false; // the step before this one was a key concept: its question is answered
  for (const step of manifest.steps.slice(0, cursor + 1)) {
    if (answered) s.ask = '';
    answered = step.kind === 'concept';
    if (step.kind === 'chapter') [s.chapter, s.part, s.section, s.say, s.code, s.ask] = [step, '', '', '', null, ''];
    if (step.kind === 'heading' && step.level === 2) [s.part, s.section] = [step.text, ''];
    if (step.kind === 'heading' && step.level === 3) s.section = step.text;
    if (step.kind === 'heading') [s.say, s.code, s.ask] = ['', null, ''];
    // a command shows the file it runs, beside its machine, until the next command
    if (step.kind === 'run') s.code = step.code ? { ...step.code, machine: step.machine } : null;
    if (step.kind === 'commit') s.code = step.code && step.machines.length ? { ...step.code, machine: step.machines[0] } : null;
    if (step.kind === 'say') [s.say, s.ask] = [step.text, ''];
    if (step.kind === 'ask') s.ask = step.text; // stays up through the command it asks about
    if (step.kind === 'run' || step.kind === 'restart') s.active = step.machine;
    // what last happened: a command (watch its terminal) or a commit (watch the repos)
    if (['run', 'restart', 'stop', 'commit'].includes(step.kind)) s.last = step.kind;
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
