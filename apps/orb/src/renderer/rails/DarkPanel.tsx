/**
 * ARSENAL, RECALL and SIGNAL — three rails that are BUILT AND DARK.
 *
 * ─── what "built and dark" means, and why it is not the same as empty ───
 * Each of these answers a question the owner has asked repeatedly and that
 * nothing on this surface currently answers. Each is real on the daemon side —
 * the tools exist, the memory is on disk, the voice chain is configured — and
 * invisible, because CONTRACT §5 has no command that would fetch any of it.
 *
 * So each rail is built to the point where the ONLY thing missing is a command,
 * and it says exactly which one. That is deliberately different from the three
 * rails cut last round: FLOW and INTEL had no producer AND no proposal, so they
 * were three permanent NO DATA panels announcing that the app was empty. These
 * three name the specific missing message and what it would carry, so opening
 * one is informative rather than disappointing, and the diff that lights it up
 * is written down rather than remembered.
 *
 * ─── the proposals, in full ───
 * All three are ADDITIVE under CONTRACT §7.2 — a new `cmd.*`/`res.*` pair and
 * no change to any existing type or field — so none of them bumps
 * PROTOCOL_VERSION, and a daemon that has not implemented one answers
 * `err.protocol.unknownType` (§5.4), which §3.2 requires this surface to
 * survive silently. It does: the panel simply stays dark.
 *
 * Every one of them is READ-ONLY by construction. None takes an argument that
 * selects code to run, none returns a path the surface then acts on, and none
 * of the returned strings is ever treated as anything but text (§6.1).
 *
 *   ARSENAL — "what can she actually do"
 *     cmd.tools.list   { }
 *     res.tools.list   { tools: [{ name, tier, summary, phrasings: string[] }] }
 *
 *     `tier` is the existing closed Tier enum, so the surface can colour it with
 *     the same rules the approval card already uses. `phrasings` is the part
 *     that answers the question as asked: not the tool's identifier but the
 *     words that reach it, which is what "what can I say to her" means.
 *     Deliberately NOT included: the arg schema. Rendering a tool's parameters
 *     invites the surface to build a form that invokes it, and CONTRACT §6.5's
 *     whole shape is that surfaces never originate execution.
 *
 *   RECALL — "what does she remember"
 *     cmd.memory.stats { }
 *     res.memory.stats { turns, oldestTs, newestTs, bytes, lastClearedTs|null }
 *
 *     COUNTS AND TIMESTAMPS ONLY, and that is a security boundary rather than a
 *     scope decision. Conversation contents are the highest-value thing in this
 *     process; a command that returns them puts every past turn one socket away,
 *     and TRACE already shows the live conversation through a path that is
 *     provenance-tagged. "How much is held, how old, when was it last cleared"
 *     is the question, and none of those answers is content.
 *
 *   SIGNAL — "what is the voice chain doing"
 *     cmd.voice.status { }
 *     res.voice.status { stt: { model, device }, tts: { voice, rate },
 *                        vad: { threshold, silenceMs }, wake: { enabled, phrase },
 *                        verified: boolean }
 *
 *     `phrase` is the wake word, which the owner chose and which is therefore
 *     his to see. No audio, no buffer, no partial transcript — those have their
 *     own events in §4.3 and belong in TRACE if anywhere.
 *
 * If a sixth rail is ever wanted, the gap is COST — spend by tool over time.
 * The status bar already shows a running total against a cap, and the one
 * question it cannot answer is which tool is spending it. That needs an
 * `evt.budget.*` the daemon does not emit either, so it is named here and not
 * built, on the same terms as these three.
 */

import type { RailId } from '../state/store.ts';

interface DarkSpec {
  /** The question the owner is asking, in his words. */
  question: string;
  /** The exact command that would light this panel. */
  command: string;
  /** The response shape, one field per line. */
  shape: readonly string[];
  /** What is deliberately NOT in the proposal, and why. */
  withheld: string;
}

const SPECS: Record<string, DarkSpec> = {
  arsenal: {
    question: 'What can she actually do?',
    command: 'cmd.tools.list  { }',
    shape: [
      'res.tools.list { tools: [ {',
      '  name      string',
      '  tier      green | amber | red',
      '  summary   string',
      '  phrasings string[]',
      '} ] }',
    ],
    withheld: 'no argument schema — a surface that can render a tool call is a surface that can originate one',
  },
  recall: {
    question: 'What does she remember?',
    command: 'cmd.memory.stats  { }',
    shape: [
      'res.memory.stats {',
      '  turns          number',
      '  oldestTs       iso',
      '  newestTs       iso',
      '  bytes          number',
      '  lastClearedTs  iso | null',
      '}',
    ],
    withheld: 'counts and timestamps only, never contents — past turns are the highest-value data here',
  },
  signal: {
    question: 'What is the voice chain doing?',
    command: 'cmd.voice.status  { }',
    shape: [
      'res.voice.status {',
      '  stt   { model, device }',
      '  tts   { voice, rate }',
      '  vad   { threshold, silenceMs }',
      '  wake  { enabled, phrase }',
      '  verified  boolean',
      '}',
    ],
    withheld: 'no audio, no buffer, no partial transcript',
  },
};

export function DarkPanel({ id }: { id: RailId }) {
  const spec = SPECS[id];
  if (!spec) return null;

  return (
    <div className="dark">
      <p className="dark__question">{spec.question}</p>

      <p className="dark__state">
        Built. Dark until the daemon answers one command.
      </p>

      <div className="dark__block">
        <span className="dark__label">proposed · additive, no version bump</span>
        <code className="dark__cmd">{spec.command}</code>
        <pre className="dark__shape">{spec.shape.join('\n')}</pre>
      </div>

      <p className="dark__withheld">
        <span className="dark__label">withheld</span>
        {spec.withheld}
      </p>
    </div>
  );
}
