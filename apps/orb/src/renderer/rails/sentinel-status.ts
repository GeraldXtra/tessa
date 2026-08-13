/**
 * SENTINEL's status colour — the mechanism, and why it is dark today.
 *
 * §R.7: "Only SENTINEL carries a status colour, because it IS a status: green
 * when Defender is healthy and the quarantine is empty, amber on a stale
 * definition or a pending approval, red on an active detection. When SENTINEL
 * is red, the rail label is red EVEN WHILE ANOTHER DRAWER IS OPEN — a threat is
 * never hidden behind a closed panel."
 *
 * ─── the trap, and the call ───
 * Green is not decoration. It is an ASSERTION that Defender is healthy and the
 * quarantine is empty. Defender integration is P6 and does not exist: there is
 * no source that could support that claim. Painting the rail green today would
 * be exactly the fabricated data that is banned — and worse than a fake job
 * count, because this particular lie says "you are safe".
 *
 * So the mechanism is built and exercised in full, and the input that would
 * light it does not exist. `null` means "no security source" and renders like
 * every other rail, at --text-muted. It is not a fourth colour and it is not
 * "unknown" dressed as a state; it is the absence of a claim.
 *
 * When Defender lands in P6, the only change needed is a real
 * `SentinelSource` — nothing in the rail, the CSS, or the red-while-closed
 * behaviour has to be revisited.
 */

/** The three states §R.7 defines, plus the honest absence of a source. */
export type SentinelStatus = 'green' | 'amber' | 'red' | null;

/**
 * What a real security source would have to provide. Nothing implements this
 * yet; it is here so the shape of the missing input is explicit rather than
 * imagined later.
 */
export interface SentinelSource {
  defenderHealthy: boolean;
  definitionsStale: boolean;
  quarantineCount: number;
  pendingApprovals: number;
  activeDetections: number;
}

/**
 * Fold a security source into a rail colour.
 *
 * Ordered worst-first: a detection outranks a stale definition, which outranks
 * everything being fine. With no source at all the answer is `null` — never
 * green, because "I cannot see" and "all clear" are different answers and only
 * one of them is safe to show in green.
 */
export function sentinelStatus(source: SentinelSource | null): SentinelStatus {
  if (!source) return null;
  if (source.activeDetections > 0) return 'red';
  if (source.definitionsStale || source.pendingApprovals > 0) return 'amber';
  if (source.defenderHealthy && source.quarantineCount === 0) return 'green';
  return 'amber';
}

/**
 * The live source. Deliberately null until P6.
 *
 * Audit entries and PTY grants are real and SENTINEL renders them, but neither
 * is a security VERDICT — a busy shell is not a threat, and an audit line is a
 * record, not an assessment. Deriving a colour from them would be inventing a
 * judgement the system has not made.
 */
export function currentSentinelSource(): SentinelSource | null {
  return null;
}
