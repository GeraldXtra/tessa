/**
 * The agent state, top centre of the stage. The one word that says what she is.
 *
 * ─── what happened to the status bar ───
 * This word used to live in the status bar, third from the left, between the
 * wordmark and the microphone chip, at 10px. That is a diagnostics strip: it
 * carries the connection phase, the heartbeat age, the spend counter and the
 * window controls, and it is also the frameless window's DRAG REGION, so it
 * cannot be removed or replaced by anything that is not a bar.
 *
 * The bar stays and keeps everything else. Only the state moves, because it is
 * the single most important fact on the surface and it was being read at the
 * same weight as the budget figure. Here it is centred, larger, and paired with
 * the sphere it describes — which is the composition's whole hierarchy claim:
 * one thing at the top, one thing in the middle, detail at the edges.
 *
 * ─── the detail line ───
 * CONTRACT §4.1's `evt.agent.state` payload is `{ companionId, state, detail? }`
 * and Session 1 has approved populating `detail` as `{ tool?, target?, note? }`
 * with `redact()` mandatory on `target` before broadcast. That renderer is item
 * 12 and lives here rather than in a panel: what she is TOUCHING is a
 * qualifier on what she IS, and putting it anywhere else makes the reader
 * assemble one fact out of two places.
 *
 * It draws nothing until the daemon sends it. The `detail` field is optional in
 * an additive change (§7.2), so absent is the normal case and must look normal.
 */

import { useStore } from '../state/store.ts';
import { agentStateStore, agentDetailStore } from '../state/store.ts';

/** What the dot means, in words, for a screen reader and a tooltip. */
const MEANING: Record<string, string> = {
  idle: 'waiting',
  listening: 'hearing you',
  thinking: 'working it out',
  speaking: 'answering',
  working: 'running a job',
  // CONTRACT §4.1: deliberately distinct from `working` — "busy" and "stuck
  // waiting for you" are different things to see when walking past at 2am.
  blocked: 'waiting on your approval',
};

export function StateChip() {
  const state = useStore(agentStateStore);
  const detail = useStore(agentDetailStore);

  // The target is rendered as TEXT, never as a link or a path the surface acts
  // on. CONTRACT §6.1: tool output is data. It arrives already redacted by the
  // daemon, and this side treats it as a string regardless.
  const parts = detail
    ? [detail.tool, detail.target, detail.note].filter((p): p is string => Boolean(p))
    : [];

  return (
    <div className="statechip" data-state={state}>
      <span className="statechip__word" title={MEANING[state] ?? state}>
        <span className="statechip__dot" aria-hidden="true" />
        {state}
      </span>
      {parts.length > 0 ? <span className="statechip__detail">{parts.join(' · ')}</span> : null}
    </div>
  );
}
