import { Fragment, memo, useMemo, useState } from 'react';

import {
  bytesText,
  chunkText,
  hersText,
  shortSha,
  uncheckedCount,
  visibleSegments,
  visibleString,
  type CardModel,
  type FileRow,
  type Row,
  type TextMode,
} from './card-view.ts';

export function Visible({ text, mode = 'text' }: { text: string; mode?: TextMode }) {
  const segs = useMemo(() => visibleSegments(text, mode), [text, mode]);
  return (
    <>
      {segs.map((s, i) =>
        'm' in s ? (
          <span key={i} className="esc">
            {s.m}
          </span>
        ) : (
          s.t
        ),
      )}
    </>
  );
}

export const DENSE_MARKS = 256;

const Chunk = memo(function Chunk({ text, mode }: { text: string; mode: TextMode }) {
  const segs = useMemo(() => visibleSegments(text, mode), [text, mode]);
  const marks = segs.reduce((n, s) => n + ('m' in s ? 1 : 0), 0);
  if (marks > DENSE_MARKS) {
    return (
      <div className="ck dense">
        <span className="dn">{marks.toLocaleString('en-US')} RUNS OF INVISIBLE OR CONTROL CHARACTERS IN THIS PART, SHOWN INLINE AS [U+XXXX]</span>
        {segs.map((seg) => ('t' in seg ? seg.t : seg.m)).join('')}
      </div>
    );
  }
  return (
    <div className="ck">
      {segs.map((seg, i) =>
        'm' in seg ? (
          <span key={i} className="esc">
            {seg.m}
          </span>
        ) : (
          seg.t
        ),
      )}
    </div>
  );
});

export function TextBox({ text, mode = 'text', label }: { text: string; mode?: TextMode; label: string }) {
  const chunks = useMemo(() => chunkText(text), [text]);
  return (
    <div className="code" role="document" aria-label={visibleString(label)} data-chunks={chunks.length}>
      {text.length === 0 ? <span className="dim">Empty.</span> : chunks.map((c, i) => <Chunk key={i} text={c} mode={mode} />)}
    </div>
  );
}

export function Sha({ value }: { value: string }) {
  const [full, setFull] = useState(false);
  const short = shortSha(value);
  if (!value) return <span className="dim">none</span>;
  if (short === value) return <span className="mono">{value}</span>;
  return (
    <span className="sha">
      <span className="mono">{full ? value : short}</span>
      <button type="button" className="shab" onClick={() => setFull(!full)} aria-label={full ? 'Show the short sha256' : 'Show the full sha256'}>
        {full ? 'SHORT' : 'FULL'}
      </button>
    </span>
  );
}

function RowView({ row }: { row: Row }) {
  return (
    <div className="arg" data-row={visibleString(row.label)}>
      <span className="an">
        {row.label}
        <span className="ro"> READ-ONLY</span>
      </span>
      <div className={`cfixed${row.mono ? ' mono' : ''}`}>{row.sha ? <Sha value={row.value} /> : <Visible text={row.value} />}</div>
    </div>
  );
}

function FileItem({ f, open, onOpen }: { f: FileRow; open: boolean; onOpen: () => void }) {
  return (
    <div className={`fi${open ? ' open' : ''}`} data-file={visibleString(f.file)}>
      <div className="fh">
        <button type="button" className="fn" onClick={onOpen} aria-expanded={open} disabled={f.text === null}>
          <Visible text={f.file} />
        </button>
        <span className={`hers${f.hers === true ? ' amb' : ''}`}>{hersText(f.hers)}</span>
      </div>
      <div className="fm">
        {f.orphan ? <span className="amb">TEXT ON THE CARD FOR A FILE NOT IN ITS LIST</span> : <Sha value={f.sha256} />}
        {f.bytes !== null ? <span>{bytesText(f.bytes)}</span> : null}
        {f.lines !== null ? <span>{f.lines.toLocaleString('en-US')} LINES</span> : null}
      </div>
      {f.path && f.path !== f.file ? (
        <div className="fp mono">
          <Visible text={f.path} />
        </div>
      ) : null}
      {f.text === null ? (
        <div className="fv">{f.view ? <Visible text={f.view} /> : <span className="dim">Its text is not on the card.</span>}</div>
      ) : null}
      {open && f.text !== null ? <TextBox text={f.text} label={`Text of ${f.file}`} /> : null}
    </div>
  );
}

function FileList({ files }: { files: readonly FileRow[] }) {
  const first = files.findIndex((f) => f.text !== null);
  const [open, setOpen] = useState<number>(first);
  return (
    <div className="files" data-count={files.length}>
      <span className="an">
        FILES THAT WILL RUN, {files.length}
        <span className="ro"> READ-ONLY</span>
      </span>
      {files.length === 0 ? <div className="dim">The card lists no files.</div> : null}
      {files.map((f, i) => (
        <FileItem key={i} f={f} open={open === i} onOpen={() => setOpen(open === i ? -1 : i)} />
      ))}
    </div>
  );
}

type Part = 'keys' | 'blocks' | 'rows' | 'more' | 'rest' | 'files' | 'unchecked';

const ORDER: Readonly<Record<CardModel['kind'], readonly Part[]>> = {
  run: ['rest', 'files', 'unchecked', 'rows', 'more'],
  write: ['rows', 'blocks', 'more', 'rest'],
  type: ['keys', 'blocks', 'rows', 'more', 'rest'],
  app: ['rows', 'more', 'rest', 'files', 'unchecked'],
  plain: ['keys', 'blocks', 'rows', 'more', 'rest', 'files', 'unchecked'],
};

export function CardDetail({ model }: { model: CardModel }) {
  const parts: Record<Part, React.ReactNode> = {
    keys: model.keys ? (
      <div key="keys" className="arg" data-row="KEYS">
        <span className="an">
          THE EXACT KEYS SHE PRESSES
          <span className="ro"> READ-ONLY</span>
        </span>
        {model.keys.chords ? <div className="cfixed mono keys">{model.keys.chords.join(' ')}</div> : null}
        <div className="cfixed mono">
          <span className="dim">AS SENT: </span>
          <Visible text={model.keys.raw} />
        </div>
      </div>
    ) : null,
    blocks: model.blocks.map((b, i) => (
      <div key={`b${i}`} className="arg" data-row={visibleString(b.label)}>
        <span className="an">
          {b.label}
          <span className="ro"> READ-ONLY</span>
        </span>
        <TextBox text={b.text} mode={b.mode} label={b.label} />
      </div>
    )),
    rows: model.rows.map((r, i) => <RowView key={`r${i}`} row={r} />),
    more: model.more.map((r, i) => <RowView key={`m${i}`} row={r} />),
    rest: model.rest.map((r, i) => <RowView key={`x${i}`} row={r} />),
    files: model.kind === 'run' || model.files.length > 0 ? <FileList key="files" files={model.files} /> : null,
    unchecked:
      model.unchecked.length > 0 ? (
        <div key="unchecked" className="unc">
          <span className="an">
            NOT CHECKED, {model.unchecked.length}
            <span className="ro"> READ-ONLY</span>
          </span>
          {model.unchecked.map((u, i) => (
            <div key={i} className="ur">
              <span className="mono">
                <Visible text={u.folder} />
              </span>
              <span className="dim">{uncheckedCount(u.files)}</span>
              <span>
                <Visible text={u.why} />
              </span>
            </div>
          ))}
        </div>
      ) : null,
  };
  return (
    <div className="cd" data-kind={model.kind}>
      {model.heading ? <div className="ch2">{model.heading}</div> : null}
      {model.warnings.map((w, i) => (
        <div key={i} className="warn">
          <Visible text={w} />
        </div>
      ))}
      {ORDER[model.kind].map((part) => (
        <Fragment key={part}>{parts[part]}</Fragment>
      ))}
    </div>
  );
}
