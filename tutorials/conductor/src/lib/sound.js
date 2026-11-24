// The page's speaker. The machines have none of their own, so the page plays
// a member's note when that member's signed record of it lands: the sound is
// what alice wrote, at the pitch and for the length alice wrote, not what the
// conductor asked for.
//
// Browsers keep a page silent until someone presses something on it, so the
// first press of Play, Next or Got it opens the speaker (unlock, from send).
// A viewer who never presses anything hears nothing, and nothing breaks.

let ctx = null;

export function unlock() {
  try {
    ctx ??= new AudioContext();
    if (ctx.state === 'suspended') ctx.resume();
  } catch {
    ctx = null; // no WebAudio here (a headless check): stay silent
  }
}

// A record that says what was played: a pitch as a whole number with its
// power of ten, and how long it sounded. The integers are exact in the
// record; they become a float only here, at the speaker, because that is
// what WebAudio takes.
export function playable(record) {
  const { pitch, pitch_exponent: exponent, duration_ms: ms } = record ?? {};
  if (![pitch, exponent, ms].every(Number.isInteger) || pitch < 1 || ms < 1) return null;
  return { hz: pitch * 10 ** exponent, ms };
}

// pan: -1 is left, 1 is right, so each machine is heard from its own place.
export function play({ hz, ms }, pan = 0) {
  if (!ctx || ctx.state !== 'running' || !(hz >= 20 && hz <= 20000)) return;
  const start = ctx.currentTime + 0.01;
  const end = start + ms / 1000;
  const tone = new OscillatorNode(ctx, { type: 'triangle', frequency: hz });
  const level = new GainNode(ctx, { gain: 0 });
  // a short rise and fall, so a note starts and stops without a click; a
  // very short note gets a rise and a fall to fit inside it
  const rise = Math.min(0.02, (end - start) / 4);
  const fall = Math.min(0.08, (end - start) / 2);
  level.gain.setValueAtTime(0, start);
  level.gain.linearRampToValueAtTime(0.18, start + rise);
  level.gain.setValueAtTime(0.18, Math.max(start + rise, end - fall));
  level.gain.linearRampToValueAtTime(0, end);
  tone.connect(level).connect(new StereoPannerNode(ctx, { pan })).connect(ctx.destination);
  tone.start(start);
  tone.stop(end + 0.02);
}
