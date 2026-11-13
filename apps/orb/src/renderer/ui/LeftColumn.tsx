import { useEffect, useState } from 'react';

import { auditStore, connectionStore, useStore } from '../state/store.ts';
import { tickStore } from '../state/tick.ts';
import { Loading, NoData } from './bits.tsx';
import { hms, hmsOf, isHerAction, resultOf, tierOf } from './format.ts';
import { noteLive, noteStack } from './notes-view.ts';
import { auditLoadedStore, clearNote, notesStore, type Note } from './stores.ts';

const FADE_MS = 450;

function NoteCard({ note }: { note: Note }) {
  const [out, setOut] = useState(() => Date.now() >= note.until);
  useEffect(() => {
    if (!Number.isFinite(note.until)) {
      setOut(false);
      return;
    }
    const wait = note.until - Date.now();
    if (wait <= 0) {
      setOut(true);
      return;
    }
    const id = window.setTimeout(() => setOut(true), wait);
    return () => window.clearTimeout(id);
  }, [note.until]);
  const pinned = note.sticky && note.cleared === null;
  return (
    <div className={`note${note.kind ? ` ${note.kind}` : ''}${out ? ' out' : ''}${pinned ? ' pinned' : ''}`}>
      <div className="h">
        <b>{note.src}</b>
        <time>{hms(new Date(note.at))}</time>
      </div>
      <div className="m">{note.msg}</div>
      {pinned ? (
        <button type="button" className="clear" onClick={() => clearNote(note.id)}>
          CLEAR
        </button>
      ) : null}
    </div>
  );
}

export function Notes() {
  const notes = useStore(notesStore);
  const now = useStore(tickStore);
  const [, force] = useState(0);
  const live = notes.filter((n) => noteLive(n, Date.now(), FADE_MS));
  useEffect(() => {
    const next = live.reduce((m, n) => Math.min(m, n.until + FADE_MS), Number.POSITIVE_INFINITY);
    if (!Number.isFinite(next)) return;
    const id = window.setTimeout(() => force((x) => x + 1), Math.max(0, next - Date.now()) + 5);
    return () => window.clearTimeout(id);
  }, [notes, now]);
  const { shown, more } = noteStack(notes, Date.now(), FADE_MS, 3);
  return (
    <div className="notes" aria-live="polite">
      {shown.map((n) => (
        <NoteCard key={n.id} note={n} />
      ))}
      {more > 0 ? <div className="note more">+{more} MORE IN SIGNAL</div> : null}
    </div>
  );
}

export function TraceStack() {
  const audit = useStore(auditStore);
  const loaded = useStore(auditLoadedStore);
  const connection = useStore(connectionStore);
  const rows = audit.filter(isHerAction).slice(0, 5);
  let body;
  if (!loaded && audit.length === 0) {
    body = connection.phase === 'connected' ? <Loading rows={4} /> : <NoData why="No daemon link, so no audit stream." />;
  } else if (rows.length === 0) {
    body = <div className="dim">No actions in the audit stream yet.</div>;
  } else {
    body = rows.map((a) => {
      const tier = tierOf(a);
      const res = resultOf(a.summary);
      return (
        <div key={a.id} className="act">
          <time>{hmsOf(a.ts)}</time>
          <span className={`tier${tier ? ` t-${tier}` : ''}`}>{tier ? tier.toUpperCase() : ''}</span>
          <span className={`x-${tier ?? 'none'}`}>{a.summary}</span>
          <i className={`res ${res ? res.kind : ''}`}>{res ? res.word : ''}</i>
        </div>
      );
    });
  }
  return (
    <section className="pnl br">
      <div className="ph">
        <span className="lab">TRACE</span>
        <span className="lab">LATEST 5</span>
      </div>
      <div className={connection.phase === 'connected' ? '' : 'stale'}>{body}</div>
    </section>
  );
}
