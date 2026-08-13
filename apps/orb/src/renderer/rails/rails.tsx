/**
 * The five rails, in the order §R.3 fixes: PULSE · SENTINEL · FLOW · INTEL · TRACE.
 *
 * One table so the rail tabs, the drawer titles and the panel routing cannot
 * disagree with each other — the previous three-rail build kept those in three
 * places and that is how a dead name survives a rename.
 */

import type { ReactNode } from 'react';

import type { RailId } from '../state/store.ts';
import { FlowPanel } from './FlowPanel.tsx';
import { IntelPanel } from './IntelPanel.tsx';
import { PulsePanel } from './PulsePanel.tsx';
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
  { id: 'pulse', label: 'Pulse', answers: 'Is my machine healthy?', render: () => <PulsePanel /> },
  { id: 'sentinel', label: 'Sentinel', answers: 'Is it safe?', render: () => <SentinelPanel /> },
  { id: 'flow', label: 'Flow', answers: 'What is it doing for me?', render: () => <FlowPanel /> },
  { id: 'intel', label: 'Intel', answers: 'What does it know?', render: () => <IntelPanel /> },
  { id: 'trace', label: 'Trace', answers: 'What did we say?', render: () => <TracePanel /> },
];

export function railById(id: RailId): RailDef {
  const found = RAILS.find((r) => r.id === id);
  // RAILS is exhaustive over RailId by construction; this keeps the return
  // type non-optional without a non-null assertion.
  return found ?? RAILS[0]!;
}
