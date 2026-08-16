/**
 * FIVE rails: TRACE, SENTINEL, ARSENAL, RECALL, SIGNAL.
 *
 * The first two are live. The last three are BUILT AND DARK — each names the
 * one additive command that would light it, in DarkPanel.tsx. §R.3's original
 * five are not these five: PULSE moved into the right panel, and FLOW and INTEL
 * are still gone for the reason below.
 *
 * ─── why three left ───
 * PULSE's contents are now permanently on screen in the right column, which was
 * the entire point of building the column — a rail whose data is already
 * visible has no reason to be opened. FLOW and INTEL have never had a source:
 * there are no `evt.job.*` broadcasts in core/ and no knowledge graph, so both
 * said NO DATA every time they were opened.
 *
 * Three rails saying NO DATA is a permanent statement that this app is mostly
 * empty, which is precisely the complaint this composition answers. Re-adding
 * one is a single line in this table plus its panel import — the panels are
 * still on disk, unimported, waiting for producers rather than deleted.
 *
 * One table so the rail tabs, the drawer titles and the panel routing cannot
 * disagree with each other — the previous three-rail build kept those in three
 * places and that is how a dead name survives a rename.
 */

import type { ReactNode } from 'react';

import type { RailId } from '../state/store.ts';
import { DarkPanel } from './DarkPanel.tsx';
import { SentinelPanel } from './SentinelPanel.tsx';
import { TracePanel } from './TracePanel.tsx';

export interface RailDef {
  id: RailId;
  /** Shown on the rail and as the drawer title. Uppercased by CSS, not here. */
  label: string;
  /** The question the rail answers, per §R.3. Not rendered; it is why it exists. */
  answers: string;
  render: () => ReactNode;
}

export const RAILS: readonly RailDef[] = [
  { id: 'trace', label: 'Trace', answers: 'What did we say?', render: () => <TracePanel /> },
  { id: 'sentinel', label: 'Sentinel', answers: 'Is it safe?', render: () => <SentinelPanel /> },
  // Built and dark. Each renders its own missing command; see DarkPanel.tsx.
  { id: 'arsenal', label: 'Arsenal', answers: 'What can she do?', render: () => <DarkPanel id="arsenal" /> },
  { id: 'recall', label: 'Recall', answers: 'What does she remember?', render: () => <DarkPanel id="recall" /> },
  { id: 'signal', label: 'Signal', answers: 'What is the voice chain doing?', render: () => <DarkPanel id="signal" /> },
];

export function railById(id: RailId): RailDef {
  const found = RAILS.find((r) => r.id === id);
  // RAILS is exhaustive over RailId by construction; this keeps the return
  // type non-optional without a non-null assertion.
  return found ?? RAILS[0]!;
}
