/**
 * §R.1's resource aura: "Ambient glow intensity tracks CPU and RAM.
 * Subtle — felt, not read."
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * WHAT DRIVES IT, AND WHY IT IS NOT WHAT I INTENDED
 *
 * The plan was to key this to `memMB` and leave `cpuPct` to the colour
 * temperature, so the sphere carried two independent facts instead of one fact
 * twice. Fifteen consecutive heartbeats from the live daemon killed that:
 *
 *     cpuPct  min 0    max 2.8   spread 2.8
 *     memMB   min 61.4 max 61.4  spread 0.0
 *
 * `memMB` is resident set size and it did not move by one tenth of a megabyte
 * in seventy-five seconds. An instrument keyed to a constant is decoration
 * wearing an instrument's clothes, and this project's standing rule is that a
 * surface never asserts a value it does not have.
 *
 * So the aura keys off `cpuPct`, and it therefore SHARES A SOURCE with the
 * colour temperature (`Sphere.tsx`, `cpuPct / LOAD_CEILING`). That is a real
 * cost and it is taken deliberately: the two are the same fact rendered at two
 * different distances of legibility — hue, which you have to be looking at the
 * sphere to read, and ambient brightness, which is peripheral and is what §R.1
 * means by "felt, not read". One fact, two acuities, rather than two facts.
 *
 * THE SEAM IS LEFT OPEN. If `memMB` ever starts moving — Whisper and Piper load
 * on demand, and a long context will show — `radiusFrom` below is the one line
 * that repoints the radius half at it, and the two instruments separate again.
 *
 * HONEST LIMIT, so nobody later reads this as a system monitor: `cpuPct` is the
 * DAEMON PROCESS's CPU normalised across cores, not machine load. The aura
 * swells when SHE is working. It says nothing about what else is on the box.
 * ─────────────────────────────────────────────────────────────────────────────
 */

import type { DaemonHealth } from '../shared/ipc-contract.ts';

/**
 * The normalised-CPU figure treated as "fully lit".
 *
 * The same 25 the colour temperature uses, on purpose: two renderings of one
 * fact must not disagree about what "hot" means, or the sphere would be warm
 * while the stage was still dim.
 */
const LOAD_CEILING = 25;

/**
 * The clamp. §5.1's whole point is that the sphere tells him her state from
 * across the room, and an aura with a wide swing competes with exactly that.
 *
 * ─── THE FLOOR WENT 0.70 -> 0.00, ON A MEASUREMENT, AND IT IS AN ARGUMENT ───
 *
 * The old floor was chosen so the resting stage looked exactly as it had before
 * this instrument existed. Measured against the reference, that resting look is
 * wrong. Median luminance of an annulus just outside the silhouette, minus the
 * median of the frame's own far background:
 *
 *                       1.05-1.20 R   1.20-1.45 R   1.45-1.80 R
 *   reference image11      -1.00         +0.00         +0.50
 *   build, floor 0.70      +5.84         +4.49         +2.35
 *
 * The reference has NO halo. Its numbers are noise around zero — and note it is
 * a photograph, so if anything it should show MORE glow than a screenshot, not
 * less. The build had six luminance units of navy sitting where CONTRACT §9.1
 * says the centre stage floats over pure void, and magnified it reads as a wash
 * across the whole frame. That wash is what he is looking at.
 *
 * A correction to the brief, which read this as the background being off true
 * black. It is not: the build's far background measures a median of 0.00
 * against the reference photograph's 8.57. The fault is local to the sphere —
 * a halo, not a wash — and only the halo is removed here.
 *
 * The instrument is not weakened by this; it is TRIPLED. At a floor of 0.70,
 * seventy percent of the aura's range was spent on a constant and load could
 * only ever move the remaining thirty. From zero the whole range is the signal,
 * and a glow that APPEARS is a stronger peripheral cue than one that swells —
 * which is what §R.1's "felt, not read" is asking for.
 *
 * What it costs: spec §3.8's ambient glow is gone at rest. That is the point.
 * He asked for the reference's black and the reference's black is black.
 */
const ALPHA_FLOOR = 0.0;
const ALPHA_CEIL = 1.0;
const SCALE_FLOOR = 0.97;
const SCALE_CEIL = 1.18;

/**
 * Quantisation, and it is what keeps the promise that nothing animates at rest.
 *
 * The transition below runs for 2.4 s and a heartbeat arrives every 5 s, so an
 * aura that repainted on every beat would be animating half the time — on a
 * machine whose compositing is in software. At rest `cpuPct` alternates between
 * 0 and 0.3, which is a load delta of 0.012 and invisible; bucketing means
 * those beats write nothing at all and the stage is genuinely static.
 */
const STEP = 0.05;

/** Read once. A pinned aura is the reduced-motion contract, not a slow one. */
const reducedMotion =
  typeof window !== 'undefined' &&
  window.matchMedia('(prefers-reduced-motion: reduce)').matches;

let lastBucket = -1;
let lastBeatAt = 0;

/**
 * Three missed heartbeats, matching the status bar's own staleness rule.
 *
 * Found while working out how to TEST the death path: `applyAura(null)` fires
 * on a connection phase change, but a socket that stays open while the daemon
 * stops beating is a real failure mode — a wedged health thread, a suspended
 * laptop — and in that case nothing would have called it. The aura would have
 * held its last value indefinitely, which is precisely the frozen-instrument
 * lie this file claims not to tell. It has to go flat on silence too.
 */
const STALE_AFTER_MS = 15_000;

/**
 * DEV ONLY. `--force-aura=<0..1>` pins the load, because the decisive question
 * about this instrument cannot be answered any other way.
 *
 * `cpuPct` is the daemon's own CPU and it spans 0–2.8% at rest; making it climb
 * means making HER work, which needs a voice turn this session cannot give. So
 * the only way to find out whether the aura is visible across its real range is
 * to render that range directly and measure the pixels.
 */
let forcedLoad: number | 'cycle' | null = null;
let cyclePhase = 0;

export function setForcedAuraLoad(load: number | 'cycle' | null): void {
  forcedLoad = load;
}

function radiusFrom(load: number): number {
  // The seam named in the header: point this at a normalised memMB the day it
  // starts moving, and the aura stops sharing a source with the temperature.
  return SCALE_FLOOR + (SCALE_CEIL - SCALE_FLOOR) * load;
}

/**
 * Apply the aura for one heartbeat, or extinguish it when there is no daemon.
 *
 * `null` means the link is not up. The aura goes to zero rather than holding
 * its last value, and that is the same rule the equatorial pulse follows: a
 * frozen instrument is a lie in the shape of a reading. A dark stage under a
 * status bar that says DAEMON OFFLINE is two things agreeing.
 */
export function applyAura(health: DaemonHealth | null): void {
  const root = document.documentElement;

  if (!health) {
    lastBucket = -1;
    lastBeatAt = 0;
    root.style.setProperty('--aura-alpha', '0');
    root.style.setProperty('--aura-scale', '1');
    return;
  }

  lastBeatAt = Date.now();
  // `cycle` alternates the full range on every heartbeat, which is the WORST
  // case the compositor can be asked for: a 2.4 s transform-and-opacity
  // transition on a full-stage radial, starting again every 5 s. Frame cost has
  // to be measured against that rather than against a static value, because at
  // a static value this instrument does nothing at all.
  let load: number;
  if (forcedLoad === 'cycle') {
    cyclePhase = 1 - cyclePhase;
    load = cyclePhase;
  } else if (forcedLoad !== null) {
    load = forcedLoad;
  } else {
    load = Math.max(0, Math.min(1, health.cpuPct / LOAD_CEILING));
  }

  if (reducedMotion) {
    // Pinned at the floor, written once. No transition, no updates. The floor
    // is now zero, so reduced motion means a black stage — which is both the
    // reference's appearance and the least motion available.
    if (lastBucket !== 0) {
      lastBucket = 0;
      root.style.setProperty('--aura-alpha', String(ALPHA_FLOOR));
      root.style.setProperty('--aura-scale', String(SCALE_FLOOR));
    }
    return;
  }

  const bucket = Math.round(load / STEP);
  if (bucket === lastBucket) return;
  lastBucket = bucket;

  const quantised = bucket * STEP;
  root.style.setProperty(
    '--aura-alpha',
    (ALPHA_FLOOR + (ALPHA_CEIL - ALPHA_FLOOR) * quantised).toFixed(3),
  );
  root.style.setProperty('--aura-scale', radiusFrom(quantised).toFixed(3));
}

/**
 * Flatten the aura if the heartbeats have stopped while the socket stayed up.
 *
 * Returns true on the transition, so the caller can log it once rather than
 * every tick. Called from the same 1 s sweep the approval expiry uses — one
 * timer, not two.
 */
export function auraSweep(now: number = Date.now()): boolean {
  if (lastBeatAt === 0) return false;
  if (now - lastBeatAt < STALE_AFTER_MS) return false;
  lastBeatAt = 0;
  lastBucket = -1;
  const root = document.documentElement;
  root.style.setProperty('--aura-alpha', '0');
  root.style.setProperty('--aura-scale', '1');
  return true;
}

/** Dev only: what the aura would be showing, for the metrics log. */
export function auraState(): string {
  const root = document.documentElement;
  return (
    `alpha=${root.style.getPropertyValue('--aura-alpha') || '(unset)'} ` +
    `scale=${root.style.getPropertyValue('--aura-scale') || '(unset)'} ` +
    `bucket=${lastBucket} reducedMotion=${reducedMotion}`
  );
}
