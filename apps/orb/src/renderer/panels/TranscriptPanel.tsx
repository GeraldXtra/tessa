/**
 * TRANSCRIPT — per-companion tabs and the message list.
 *
 * Roles come from `Role` in @zoey/protocol (user | assistant | system | tool),
 * another closed set.
 *
 * The left gutter on each message is the PROVENANCE gutter, using the
 * --prov-human / --prov-agent / --prov-program tokens from CONTRACT §9. It is
 * the same visual language the Console uses for terminal output, and it exists
 * because of §6.2: `human` is the only trusted source. Anything the model said,
 * and anything a tool returned, is data — and it should not look identical to
 * something the owner typed.
 */

import { useState } from 'react';

import type { Role } from '@zoey/protocol';

import { Placeholder } from './Placeholder.tsx';

interface Line {
  id: string;
  role: Role;
  text: string;
}

const THREADS: readonly { companion: string; lines: readonly Line[] }[] = [
  {
    companion: 'Zoey',
    lines: [
      { id: 'a', role: 'user', text: 'What is running right now?' },
      {
        id: 'b',
        role: 'assistant',
        text: 'Two jobs. The metadata index is on step two, and the invoice move is waiting on your approval.',
      },
      { id: 'c', role: 'tool', text: 'jobs.list → 5 records' },
      {
        id: 'd',
        role: 'system',
        text: 'Approval window for job j3 lapsed after 30 minutes; marked needsReview.',
      },
    ],
  },
  {
    companion: 'Atlas',
    lines: [
      { id: 'e', role: 'user', text: 'Summarise the ConPTY notes.' },
      {
        id: 'f',
        role: 'assistant',
        text: 'Build 22631 predates several ConPTY fixes, so the redistributable is bundled rather than relying on the in-box copy.',
      },
    ],
  },
];

export function TranscriptPanel() {
  const [active, setActive] = useState(0);
  const thread = THREADS[active] ?? THREADS[0];

  return (
    <>
      <Placeholder note="Sample transcript. Live streaming arrives with evt.transcript.delta." />

      <div className="tabs" role="tablist">
        {THREADS.map((entry, index) => (
          <button
            key={entry.companion}
            type="button"
            role="tab"
            className="tabs__tab"
            data-active={index === active}
            aria-selected={index === active}
            onClick={() => setActive(index)}
          >
            {entry.companion}
          </button>
        ))}
      </div>

      <ol className="transcript">
        {thread?.lines.map((line) => (
          <li key={line.id} className="transcript__line" data-role={line.role}>
            <span className="transcript__role">{line.role}</span>
            <span className="transcript__text">{line.text}</span>
          </li>
        ))}
      </ol>

      <p className="panel-footnote">
        The gutter marks provenance. Only what you typed is trusted — CONTRACT §6.2.
      </p>
    </>
  );
}
