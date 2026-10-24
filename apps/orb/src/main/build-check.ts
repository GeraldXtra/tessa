import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';

export interface BuildStamp {
  source: string;
  part: string;
  builtAt: string;
  command: string;
}

export interface BuildCheck {
  ok: boolean;
  problems: string[];
  stamps: Record<string, BuildStamp | null>;
}

const PARTS = ['main', 'preload', 'renderer'] as const;
const ENTRY: Readonly<Record<(typeof PARTS)[number], string>> = {
  main: 'index.js',
  preload: 'index.js',
  renderer: 'index.html',
};

function readStamp(path: string): BuildStamp | null {
  try {
    const o = JSON.parse(readFileSync(path, 'utf8')) as Partial<BuildStamp>;
    if (typeof o.source !== 'string' || typeof o.builtAt !== 'string') return null;
    return { source: o.source, part: String(o.part ?? ''), builtAt: o.builtAt, command: String(o.command ?? '') };
  } catch {
    return null;
  }
}

function when(iso: string): string {
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return iso;
  const d = new Date(t);
  const p = (n: number): string => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

export function checkBuild(outDir: string): BuildCheck {
  const problems: string[] = [];
  const stamps: Record<string, BuildStamp | null> = {};
  for (const part of PARTS) {
    if (!existsSync(join(outDir, part, ENTRY[part]))) problems.push(`out\\${part}\\${ENTRY[part]} is missing.`);
    stamps[part] = readStamp(join(outDir, part, 'stamp.json'));
    if (!stamps[part]) problems.push(`out\\${part} has no build stamp, so it cannot be matched with the other parts.`);
  }
  const html = join(outDir, 'renderer', 'index.html');
  if (existsSync(html)) {
    for (const m of readFileSync(html, 'utf8').matchAll(/(?:src|href)="\.\/(assets\/[^"]+)"/g)) {
      const rel = m[1] as string;
      if (!existsSync(join(outDir, 'renderer', rel))) problems.push(`out\\renderer\\${rel.replace(/\//g, '\\')} is missing.`);
    }
  }
  const sources = new Set(PARTS.map((p) => stamps[p]?.source).filter((s): s is string => typeof s === 'string'));
  if (sources.size > 1) {
    const lines = PARTS.filter((p) => stamps[p]).map((p) => {
      const s = stamps[p] as BuildStamp;
      return `out\\${p} was built by ${s.command} at ${when(s.builtAt)} from source ${s.source}.`;
    });
    problems.push(`The parts were built from different source:\n${lines.join('\n')}`);
  }
  return { ok: problems.length === 0, problems, stamps };
}

export function buildFailureText(check: BuildCheck, outDir: string): string {
  return (
    `The Orb did not start because ${outDir} is not one whole build.\n\n` +
    `${check.problems.join('\n')}\n\n` +
    'npm run dev rebuilds only out\\main and out\\preload, and a build that stopped part way leaves the parts unmatched.\n\n' +
    `Fix: in ${dirname(outDir)} run npm run build, then start the Orb again.`
  );
}

export function buildSummary(check: BuildCheck): string {
  return ['main', 'preload', 'renderer']
    .map((p) => {
      const s = check.stamps[p];
      return s ? `${p} ${s.command} ${when(s.builtAt)}` : `${p} unstamped`;
    })
    .join(', ');
}
