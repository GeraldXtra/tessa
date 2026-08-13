/**
 * INTEL — "What does it know?" (§R.3)
 *
 * Shell only. The knowledge graph, memory browser, indexed documents, project
 * context and teach/forget controls are all P6, and `core/memory/` does not
 * exist yet — there is no store to read and nothing to render.
 *
 * The old KNOWLEDGE VIEW toggle folds in here when P6 lands. It has been
 * removed rather than left pointing at nothing.
 */

import { NoData, Section } from './primitives.tsx';

export function IntelPanel() {
  return (
    <>
      <Section title="Knowledge graph">
        <NoData />
      </Section>
      <Section title="Memory">
        <NoData />
      </Section>
      <Section title="Indexed documents">
        <NoData />
      </Section>
      <Section title="Project context">
        <NoData />
      </Section>
    </>
  );
}
