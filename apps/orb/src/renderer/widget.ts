/**
 * The ambient widget's renderer. No React, and that is a decision.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * WHY THERE IS NO REACT IN THIS FILE
 *
 * The full Orb's renderer mounts React, react-dom, the rails, the drawer, the
 * chat panel and the approval store. None of that has anything to draw here:
 * this window contains ONE canvas and shows ONE thing. Importing App.tsx to
 * reach the sphere would pull the entire surface into a bundle that runs all
 * day, and pay its parse cost on a two-core machine every launch.
 *
 * Sphere.tsx already documents that React is not in the animation path — the
 * engine reads state through a getter and never re-renders. So dropping React
 * here costs nothing except the component wrapper, which is 40 lines of ref
 * plumbing this file does not need.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * ⚠⚠ THE BATTERY DESIGN, IN ONE SENTENCE
 *
 * The loop is STOPPED whenever she is idle, and started only while she is
 * listening, thinking, speaking, working or blocked.
 *
 * The owner wanted the full fancy sphere rather than a simplified placeholder,
 * AND he wanted it in his corner all day. Those two fight, and this is the only
 * honest way to have both: the expensive thing runs only when it is saying
 * something. An idle widget is a still image with no rAF callback, no
 * compositor wakeups and no GPU work — see `setAnimating` in sphere-engine.ts
 * for why the engine's existing `fps === 0` gate was not sufficient.
 */

// The design tokens, as CSS custom properties. `applyTheme` reads them off the
// document, and the sphere's shader uniforms are resolved from them (CONTRACT
// §9) — without this import every token resolves empty and the sphere renders
// with no accent at all. app.css pulls it for the full Orb; this window does
// not load app.css, so it pulls it directly.
import '@tessa/tokens/css';

import type { AgentState } from '@tessa/protocol';

import type { SphereTier } from '../shared/ipc-contract.ts';
import { createSphereEngine } from './scene/sphere-engine.ts';
import { applyTheme, currentTheme } from './theme.ts';

/**
 * States that deserve animation. Everything else freezes.
 *
 * `idle` is the only frozen state, and that is deliberate rather than a
 * shortcut: `blocked` means she is waiting on an approval he has not given, and
 * a frozen sphere is exactly the wrong signal for the one state that needs him
 * to look at it. `working` can run for minutes unattended and is the other
 * thing he would want to see moving from across the room.
 */
const LIVE_STATES: ReadonlySet<string> = new Set<AgentState>([
  'listening',
  'thinking',
  'speaking',
  'working',
  'blocked',
]);

/**
 * ⚠ THE CHEAP-SPHERE TOGGLE.
 *
 * `?cheap=1` on the widget URL, or `--widget-cheap` on the command line, drops
 * the widget to the `low` tier — 6,800 particles instead of 15,600.
 *
 * It reuses the EXISTING tier machinery rather than introducing a second,
 * simpler sphere. A second sphere would be a second thing to keep looking like
 * the first one, and it would diverge the first time the real one was retuned.
 * `low` is already a measured, shipped configuration of the same shell.
 */
function cheapRequested(): boolean {
  const params = new URLSearchParams(window.location.search);
  return params.get('cheap') === '1';
}

const found = document.getElementById('orb') as HTMLCanvasElement | null;
if (!found) throw new Error('#orb is missing from widget.html');
// Narrowed into a const so the closures below do not have to re-prove it. A
// `let` that TypeScript cannot follow into a callback is why the null check
// above was not enough on its own.
const canvas: HTMLCanvasElement = found;

// The sphere's colours are design tokens read off the document (CONTRACT §9),
// so the theme has to be applied before the engine samples them.
applyTheme(currentTheme());

/**
 * The widget's own copy of agent state, read by the engine every frame.
 *
 * Not `state/store.ts`: that module pulls the notification store and the
 * connection store with it, and this window needs one string.
 */
let agentState: AgentState = 'idle';

const tier: SphereTier = cheapRequested() ? 'low' : 'med';

const engine = createSphereEngine({
  canvas,
  initialTier: tier,
  getState: () => agentState,
  onTierChange: (next, reason) => {
    console.log(`[widget] tier -> ${next} (${reason})`);
  },
});

/**
 * FIT THE DISC INSIDE 132 px.
 *
 * At its natural size the sphere's projected radius is 43% of canvas height, so
 * the disc is 86% of the frame and its rim would touch the window edge — where,
 * on a transparent window with no border, it would simply be cut off. 0.82
 * leaves a margin of about 12 px all round, so the glow has somewhere to fall
 * off into instead of ending at a hard rectangle.
 */
engine.setFit(0.82);

/* ── the freeze, and the whole point of the widget ─────────────────────────── */

let animating = false;

function applyState(next: AgentState): void {
  agentState = next;
  const shouldAnimate = LIVE_STATES.has(next);
  if (shouldAnimate === animating) {
    // Still animating, but the state changed — the engine reads it per frame,
    // so there is nothing to do. Frozen and still idle: also nothing.
    if (!shouldAnimate) engine.setAnimating(false);
    return;
  }
  animating = shouldAnimate;
  engine.setAnimating(shouldAnimate);
  console.log(`[widget] ${next} — ${shouldAnimate ? 'animating' : 'FROZEN (0 rAF)'}`);
}

/**
 * MEASUREMENT / PREVIEW: `?state=speaking` pins the starting state.
 *
 * Without it the two battery conditions cannot be sampled independently — the
 * animating one would need the daemon to be mid-turn at the exact moment the
 * sampler ran. A pushed `evt.agent.state` still overrides it immediately, so
 * this changes nothing about how the widget behaves in his hands.
 */
const forcedState = new URLSearchParams(window.location.search).get('state');
if (forcedState && LIVE_STATES.has(forcedState)) {
  applyState(forcedState as AgentState);
} else {
  // Start frozen. The daemon's first `evt.agent.state` wakes it if she is
  // mid-turn when the widget launches; until then a still sphere is the truth.
  engine.setAnimating(false);
}

window.tessa.onAgentState(({ state }) => {
  applyState(state as AgentState);
});

/**
 * The heartbeat pulse is NOT subscribed here, deliberately.
 *
 * The full Orb rides `evt.daemon.health` every 5 s to fire the equatorial pulse
 * (Sphere.tsx, §R.1). Doing that in the widget would wake this process every
 * five seconds forever to animate a ripple nobody asked for — and it would
 * require the loop to be running to show it, which would defeat the freeze
 * entirely. "She is alive" is carried by the sphere BEING there; "she is doing
 * something" is carried by it moving.
 */

/* ── click-through, except the disc ────────────────────────────────────────── */

/**
 * ⚠ The hit test is a CIRCLE, not the window rectangle.
 *
 * The window is 132x132 and the sphere is a ~108 px disc inside it, so a
 * rectangular test would claim the four corners — about 21% of the square — and
 * swallow clicks there for no reason. On a 1366x768 screen this square sits
 * over the top-right of whatever is maximised, which is where the close button
 * is.
 */
const DISC_RADIUS_FRAC = 0.42;

let interactive = false;

function setInteractive(over: boolean): void {
  if (over === interactive) return;
  interactive = over;
  canvas.classList.toggle('over', over);
  window.tessa.widgetInteractive(over);
}

window.addEventListener('mousemove', (event) => {
  const cx = window.innerWidth / 2;
  const cy = window.innerHeight / 2;
  const r = Math.min(window.innerWidth, window.innerHeight) * DISC_RADIUS_FRAC;
  const dx = event.clientX - cx;
  const dy = event.clientY - cy;
  setInteractive(dx * dx + dy * dy <= r * r);
});

// Leaving the window entirely: no mousemove fires past the edge, so give the
// clicks back explicitly or the corner stays claimed after the pointer has gone.
window.addEventListener('mouseleave', () => setInteractive(false));
document.addEventListener('mouseleave', () => setInteractive(false));

window.addEventListener('click', () => {
  if (!interactive) return;
  console.log('[widget] clicked — expanding to the full Orb');
  window.tessa.widgetExpand();
});

console.log(
  `[widget] ready — tier ${tier}${cheapRequested() ? ' (CHEAP)' : ''}, ` +
    `frozen until she does something`,
);
