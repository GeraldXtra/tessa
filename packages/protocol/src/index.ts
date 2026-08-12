/**
 * @zoey/protocol — the wire contract, in code.
 *
 * This file is the executable form of CONTRACT.md. It is SHARED by
 * apps/console, apps/orb, and (via generated Python) core/.
 *
 * DO NOT EDIT WITHOUT OWNER APPROVAL. See CONTRACT.md §7.
 *
 * Two rules from the contract are load-bearing and are encoded here:
 *   §3.2  Unknown types and unknown payload fields MUST be ignored silently.
 *   §7.4  Enums are CLOSED SETS. Adding a value is a BREAKING change.
 */

/* ══════════════════════════════════════════════════════════ version & wire */

/**
 * Closed enums and PROTOCOL_VERSION are GENERATED from schema/enums.json.
 * They were previously hand-written here and in core/*.py; two hand-maintained
 * copies of one contract drift. Regenerate: node packages/protocol/build-enums.mjs
 */
export * from './enums.generated.ts';

import {
  PROTOCOL_VERSION,
  SPAWN_MODES,
  type Provenance,
  type SpawnMode,
  type Surface,
  type Tier,
  type AgentState,
  type JobStatus,
  type CreatedBy,
  type PtyReportEvent,
  type CloudState,
  type Decision,
  type SendableDecision,
  type Role,
  type NotificationLevel,
  type FsChangeKind,
} from './enums.generated.ts';

/** CONTRACT §1 — preferred port; the daemon walks upward if occupied. */
export const PREFERRED_PORT = 47600 as const;

/** CONTRACT §1 — surfaces discover the real port here, never hard-code it. */
export const RUNTIME_FILE_RELATIVE = 'Zoey\\runtime.json' as const;

/** CONTRACT §2.1 — the only accepted Origin values. */
export const ALLOWED_ORIGINS = ['zoey://console', 'zoey://orb'] as const;
export type AllowedOrigin = (typeof ALLOWED_ORIGINS)[number];

/** CONTRACT §2.1 — a connection must complete cmd.hello inside this window. */
export const HANDSHAKE_DEADLINE_MS = 3000 as const;

/** CONTRACT §1 — anything larger uses chunked transfer, never one frame. */
export const MAX_FRAME_BYTES = 1024 * 1024;


/* ═══════════════════════════════════════════════════════ envelope — §3 */

export interface Envelope<T extends string = string, P = Record<string, unknown>> {
  v: typeof PROTOCOL_VERSION;
  id: string;
  ts: string;
  type: T;
  corr: string | null;
  payload: P;
}

/* ═══════════════════════════════════════════════════ payloads — §4 events */

/* ---- shared ---- */

export interface EvtAgentState {
  companionId: string;
  state: AgentState;
  detail?: string;
}

export interface Companion {
  companionId: string;
  name: string;
  voice?: string;
  tools: string[];
  scope: string;
}

export interface EvtCompanionRoster { companions: Companion[] }

export interface EvtCompanionStatus {
  companionId: string;
  name: string;
  state: AgentState;
  busy: boolean;
  tools: string[];
  scope: string;
}

export interface EvtTranscriptDelta {
  companionId: string;
  messageId: string;
  role: Role;
  /** Monotonic per messageId. Consumers reassemble by seq (§3.3). */
  seq: number;
  delta: string;
  done: boolean;
}

export interface TranscriptMessage {
  messageId: string;
  role: Role;
  text: string;
  toolCalls?: { tool: string; args: Record<string, unknown> }[];
  ts: string;
}

export interface EvtTranscriptMessage { companionId: string; message: TranscriptMessage }

export interface JobStep { index: number; title: string; status: JobStatus }

export interface EvtJobCreated {
  jobId: string;
  kind: string;
  title: string;
  tier: Tier;
  createdBy: CreatedBy;
  steps: JobStep[];
}

export interface EvtJobProgress { jobId: string; stepIndex: number; pct?: number; note?: string }
export interface EvtJobUpdated { jobId: string; status: JobStatus; stepIndex?: number }
export interface EvtJobCompleted { jobId: string; status: JobStatus; result?: unknown; error?: string }

export interface EvtPermissionRequest {
  requestId: string;
  tier: Tier;
  tool: string;
  args: Record<string, unknown>;
  /** REQUIRED, never optional — CONTRACT §6.2. */
  provenance: Provenance;
  expiresAt: string;
}

export interface EvtPermissionResolved {
  requestId: string;
  decision: Decision;
  decidedBy: Surface;
  remembered: boolean;
}

export interface EvtAuditAppended {
  entryId: string;
  /** Provenance, not CreatedBy — the audit log records `program` and `system`
   *  actions (daemon.start, auth.lockout) that are not job triggers. */
  actor: Provenance;
  tool: string;
  tier: Tier;
  summary: string;
  ts: string;
}

export interface EvtDaemonHealth {
  uptimeS: number;
  cpuPct: number;
  memMB: number;
  apiReachable: boolean;
  budgetSpent: number;
  budgetCap: number;
}

export interface EvtDaemonShutdown { reason: string; restarting: boolean }

export interface EvtNotification {
  level: NotificationLevel;
  title: string;
  body: string;
  actions: { id: string; label: string }[];
}

/* ---- Console-only ---- */

/**
 * NOTE (CONTRACT §4.2): the PTY *byte stream* is deliberately absent from this
 * protocol. Terminal output goes from an Electron utilityProcess straight to
 * the renderer over a MessagePort; it never traverses the Python daemon.
 * The daemon owns authorization, audit, and revocation — not the data path.
 */

export interface PtySessionInfo {
  sessionId: string;
  profileId: string;
  cwd: string;
  title: string;
  startedAt: string;
  busy: boolean;
}

/** Roster assembled by the daemon from `cmd.pty.report`. The Orb may subscribe. */
export interface EvtPtySessions { sessions: PtySessionInfo[] }

/** The daemon orders a session killed. The Console MUST comply and report back. */
export interface EvtPtyRevoke { sessionId: string; reason: string }

/** CONTRACT §4.2. Metadata only — never file contents (§6.3). */
export interface FsEntry {
  name: string;
  isDir: boolean;
  /** EndOfFile */
  size: number;
  /** AllocationSize — with `size`, yields hydration cost without touching the file. */
  allocSize: number;
  mtime: string;
  /** Raw Win32 FILE_ATTRIBUTE_* bitfield. */
  attrs: number;
  /** 0 = not a reparse point. Never followed, never hydrated. */
  reparseTag: number;
  cloudState: CloudState;
}

export interface EvtFsChildren {
  requestId: string;
  path: string;
  entries: FsEntry[];
  truncated: boolean;
  complete: boolean;
}

export interface EvtFsChanged { path: string; kind: FsChangeKind }

export interface EvtFsHydrationWarning {
  path: string;
  bytesToDownload: number;
  estimatedCostNGN: number;
}

/* ═════════════════════════════════════════════════ payloads — §5 commands */

export interface CmdHello {
  token: string;
  surface: Surface;
  surfaceVersion: string;
  protocolVersion: typeof PROTOCOL_VERSION;
}

export interface ResHello {
  ok: true;
  daemonVersion: string;
  protocolVersion: typeof PROTOCOL_VERSION;
  capabilities: string[];
  sessionId: string;
}

export interface CmdSubscribe { topics: string[] }
export interface CmdAgentMessage { companionId: string; text: string; attachments?: unknown[] }
export interface CmdAgentCancel { companionId: string; messageId?: string }
export interface CmdCompanionSwitch { companionId: string }
export interface CmdJobCreate { kind: string; title: string; args: Record<string, unknown>; tier: Tier }
export interface CmdJobRef { jobId: string }
/**
 * NOTE the type is `SendableDecision`, not `Decision`. A surface may only send
 * approve|deny. `expired` exists on `evt.permission.resolved` so the daemon can
 * tell the OTHER surface to dismiss a card whose 30-minute window lapsed — a
 * surface sending it would be a contract violation, so the type forbids it.
 */
export interface CmdPermissionRespond {
  requestId: string;
  decision: SendableDecision;
  remember?: boolean;
}
export interface CmdConfigGet { key: string }
export interface CmdConfigSet { key: string; value: unknown }
export interface CmdAuditQuery { since?: string; limit: number; filter?: string }

/**
 * CONTRACT §6.5 — no PTY session may be created without a grant.
 * A grant authorizes ONE session in ONE directory, and expires.
 */
export interface CmdPtyRequestSpawn {
  profileId: string;
  cwd: string;
  actor: Exclude<Provenance, 'program'>;
  purpose?: string;
}

export interface ResPtyGrant {
  grantId: string;
  sessionId: string;
  expiresAt: string;
}

export interface CmdPtyReport {
  sessionId: string;
  event: PtyReportEvent;
  detail?: string;
}

export interface CmdFsList { path: string; includeHidden?: boolean }
export interface CmdFsWatch { path: string }
export interface CmdFsReveal { path: string }
export interface CmdWindowSpawnAt { path: string; mode: SpawnMode }

export interface ErrPayload { code: ErrorCode; message: string; retryable: boolean }

/* ══════════════════════════════════════════════════════════ type registry */

/**
 * Every type name defined by the contract. The daemon uses this to decide
 * between "handle it" and `err.protocol.unknownType` (§7.6).
 *
 * Surfaces must NOT use this to reject inbound messages — §3.2 requires
 * unknown types to be ignored silently, so that one surface can ship a new
 * feature without breaking the other.
 */
export const SHARED_EVENTS = [
  'evt.agent.state',
  'evt.companion.roster',
  'evt.companion.status',
  'evt.transcript.delta',
  'evt.transcript.message',
  'evt.job.created',
  'evt.job.progress',
  'evt.job.updated',
  'evt.job.completed',
  'evt.permission.request',
  'evt.permission.resolved',
  'evt.audit.appended',
  'evt.daemon.health',
  'evt.daemon.shutdown',
  'evt.notification',
] as const;

export const CONSOLE_EVENTS = [
  'evt.pty.sessions',
  'evt.pty.revoke',
  'evt.fs.children',
  'evt.fs.changed',
  'evt.fs.hydrationWarning',
] as const;

export const ORB_EVENTS = [
  'evt.voice.wake',
  'evt.voice.vad',
  'evt.voice.partialTranscript',
  'evt.voice.amplitude',
  'evt.scene.state',
] as const;

export const SHARED_COMMANDS = [
  'cmd.hello',
  'cmd.subscribe',
  'cmd.unsubscribe',
  'cmd.agent.message',
  'cmd.agent.cancel',
  'cmd.companion.switch',
  'cmd.job.create',
  'cmd.job.cancel',
  'cmd.job.retry',
  'cmd.permission.respond',
  'cmd.config.get',
  'cmd.config.set',
  'cmd.audit.query',
  'cmd.ping',
] as const;

export const CONSOLE_COMMANDS = [
  'cmd.pty.requestSpawn',
  'cmd.pty.report',
  'cmd.fs.list',
  'cmd.fs.watch',
  'cmd.fs.unwatch',
  'cmd.fs.reveal',
  'cmd.window.spawnAt',
] as const;

export const ORB_COMMANDS = [
  'cmd.voice.mute',
  'cmd.voice.pushToTalk',
  'cmd.voice.setVoice',
  'cmd.scene.setMode',
] as const;

export const ALL_KNOWN_TYPES: ReadonlySet<string> = new Set<string>([
  ...SHARED_EVENTS, ...CONSOLE_EVENTS, ...ORB_EVENTS,
  ...SHARED_COMMANDS, ...CONSOLE_COMMANDS, ...ORB_COMMANDS,
]);

/** Payload shape for each type, for compile-time narrowing. */
export interface PayloadMap {
  'evt.agent.state': EvtAgentState;
  'evt.companion.roster': EvtCompanionRoster;
  'evt.companion.status': EvtCompanionStatus;
  'evt.transcript.delta': EvtTranscriptDelta;
  'evt.transcript.message': EvtTranscriptMessage;
  'evt.job.created': EvtJobCreated;
  'evt.job.progress': EvtJobProgress;
  'evt.job.updated': EvtJobUpdated;
  'evt.job.completed': EvtJobCompleted;
  'evt.permission.request': EvtPermissionRequest;
  'evt.permission.resolved': EvtPermissionResolved;
  'evt.audit.appended': EvtAuditAppended;
  'evt.daemon.health': EvtDaemonHealth;
  'evt.daemon.shutdown': EvtDaemonShutdown;
  'evt.notification': EvtNotification;
  'evt.pty.sessions': EvtPtySessions;
  'evt.pty.revoke': EvtPtyRevoke;
  'evt.fs.children': EvtFsChildren;
  'evt.fs.changed': EvtFsChanged;
  'evt.fs.hydrationWarning': EvtFsHydrationWarning;
  'cmd.hello': CmdHello;
  'cmd.subscribe': CmdSubscribe;
  'cmd.agent.message': CmdAgentMessage;
  'cmd.agent.cancel': CmdAgentCancel;
  'cmd.companion.switch': CmdCompanionSwitch;
  'cmd.job.create': CmdJobCreate;
  'cmd.job.cancel': CmdJobRef;
  'cmd.job.retry': CmdJobRef;
  'cmd.permission.respond': CmdPermissionRespond;
  'cmd.config.get': CmdConfigGet;
  'cmd.config.set': CmdConfigSet;
  'cmd.audit.query': CmdAuditQuery;
  'cmd.pty.requestSpawn': CmdPtyRequestSpawn;
  'cmd.pty.report': CmdPtyReport;
  'cmd.fs.list': CmdFsList;
  'cmd.fs.watch': CmdFsWatch;
  'cmd.fs.unwatch': CmdFsWatch;
  'cmd.fs.reveal': CmdFsReveal;
  'cmd.window.spawnAt': CmdWindowSpawnAt;
}

export type TypedEnvelope<K extends keyof PayloadMap> = Envelope<K, PayloadMap[K]>;

/* ══════════════════════════════════════════════════════════════ ULID (§3) */

const ULID_ALPHABET = '0123456789ABCDEFGHJKMNPQRSTVWXYZ'; // Crockford base32, no I L O U
let lastUlidMs = -1;
let lastUlidRandom: number[] = [];

/**
 * Monotonic ULID. Within the same millisecond the random component is
 * incremented rather than regenerated, so ids sort strictly by creation order —
 * which matters because `seq`-less consumers fall back to id ordering.
 */
export function ulid(now: number = Date.now()): string {
  let time = '';
  let t = now;
  for (let i = 9; i >= 0; i--) {
    time = ULID_ALPHABET[t % 32] + time;
    t = Math.floor(t / 32);
  }

  if (now === lastUlidMs) {
    // increment the previous random component, right to left, with carry
    for (let i = lastUlidRandom.length - 1; i >= 0; i--) {
      if (lastUlidRandom[i] < 31) { lastUlidRandom[i]++; break; }
      lastUlidRandom[i] = 0;
    }
  } else {
    lastUlidMs = now;
    lastUlidRandom = Array.from({ length: 16 }, () => Math.floor(Math.random() * 32));
  }

  return time + lastUlidRandom.map((n) => ULID_ALPHABET[n]).join('');
}

/* ═════════════════════════════════════════════════════════════── helpers */

export function makeEnvelope<K extends keyof PayloadMap>(
  type: K,
  payload: PayloadMap[K],
  corr: string | null = null,
): TypedEnvelope<K> {
  return {
    v: PROTOCOL_VERSION,
    id: ulid(),
    ts: new Date().toISOString().replace(/(\.\d{3})\d*Z$/, '$1Z'),
    type,
    corr,
    payload,
  };
}

const ULID_RE = /^[0-9A-HJKMNP-TV-Z]{26}$/;
const TS_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/;
const TYPE_RE = /^(cmd|res|err|evt)\.[a-z][a-zA-Z0-9]*(\.[a-z][a-zA-Z0-9]*)*$/;

/**
 * Structural validation of the ENVELOPE only.
 *
 * Deliberately does NOT validate payload fields: CONTRACT §3.2 requires
 * unknown payload fields to be ignored, so strict payload validation here
 * would break forward compatibility between the two surfaces.
 */
export function isEnvelope(x: unknown): x is Envelope {
  if (typeof x !== 'object' || x === null) return false;
  const e = x as Record<string, unknown>;
  return (
    e.v === PROTOCOL_VERSION &&
    typeof e.id === 'string' && ULID_RE.test(e.id) &&
    typeof e.ts === 'string' && TS_RE.test(e.ts) &&
    typeof e.type === 'string' && TYPE_RE.test(e.type) &&
    (e.corr === null || (typeof e.corr === 'string' && ULID_RE.test(e.corr))) &&
    typeof e.payload === 'object' && e.payload !== null && !Array.isArray(e.payload)
  );
}

export function isKnownType(type: string): boolean {
  return ALL_KNOWN_TYPES.has(type);
}

/** CONTRACT §2.1 — reject anything not exactly on the allowlist. */
export function isAllowedOrigin(origin: string | undefined | null): origin is AllowedOrigin {
  return typeof origin === 'string' && (ALLOWED_ORIGINS as readonly string[]).includes(origin);
}

/**
 * CONTRACT §6.3 — hydration cost from attributes alone.
 * Never opens the file, never triggers a OneDrive recall.
 */
export function hydrationBytes(entry: Pick<FsEntry, 'size' | 'allocSize'>): number {
  return Math.max(0, entry.size - entry.allocSize);
}

/* ══════════════════════════════════════════════ deep links — CONTRACT §6.6 */

export const DEEP_LINK_SCHEME = 'zoey' as const;

/**
 * Modes reachable from a deep link — a STRICT SUBSET of SPAWN_MODES.
 *
 * `cdCurrent` is excluded on purpose: it mutates the state of an already-open
 * terminal rather than creating a fresh one. A hostile page that could reach it
 * would be able to silently change the working directory of a session the owner
 * is actively typing into — the next `rm -rf .` or `git clean -fd` would land
 * somewhere they did not intend. Deep links may only ever CREATE.
 */
export const DEEP_LINK_MODES = ['window', 'tab', 'pane'] as const;
export type DeepLinkMode = (typeof DEEP_LINK_MODES)[number];

export interface DeepLink { path: string; mode: DeepLinkMode }

/**
 * Parse a `zoey://` deep link.
 *
 * Any webpage can trigger a registered protocol handler, so this parser is a
 * security boundary, not a convenience. It accepts EXACTLY two parameters —
 * `path` and `mode` — and rejects the URL outright if any other parameter is
 * present. That strictness is deliberate: it means a future `cmd=` parameter
 * cannot be smuggled in and silently honoured by an older build.
 *
 * Callers must still validate `path` against protected-path policy, and must
 * open the resulting window with an EMPTY prompt — never pre-filled, never
 * auto-run.
 *
 * @returns the parsed link, or null if the URL is not a valid, safe deep link.
 */
export function parseDeepLink(raw: string): DeepLink | null {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return null;
  }

  if (url.protocol !== `${DEEP_LINK_SCHEME}:`) return null;
  if (url.hostname !== 'open') return null;

  // Reject anything beyond the two permitted parameters.
  for (const key of url.searchParams.keys()) {
    if (key !== 'path' && key !== 'mode') return null;
  }

  const path = url.searchParams.get('path');
  if (!path) return null;

  const mode = url.searchParams.get('mode') ?? 'window';
  if (!(DEEP_LINK_MODES as readonly string[]).includes(mode)) return null;

  return { path, mode: mode as DeepLinkMode };
}

export function buildDeepLink(link: DeepLink): string {
  const p = new URLSearchParams({ path: link.path, mode: link.mode });
  return `${DEEP_LINK_SCHEME}://open?${p.toString()}`;
}
