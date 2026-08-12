// GENERATED FILE — DO NOT EDIT.
// Source: packages/protocol/schema/enums.json
// Regenerate: node packages/protocol/build-enums.mjs
//
// CONTRACT.md §7.4 — sets marked `closed` are exhaustive. Adding a value to a
// closed set is a BREAKING change requiring a PROTOCOL_VERSION bump.

export const PROTOCOL_VERSION = 1 as const;

/**
 * CONTRACT §4.1 evt.agent.state. Orb drives the sphere from this; Console drives its status bar.
 *
 * CLOSED SET.
 */
export const AGENT_STATES = [
  "idle",  // nothing in flight
  "listening",  // capturing audio (Orb)
  "thinking",  // model call in flight
  "speaking",  // TTS playing (Orb)
  "working",  // executing tools, making progress unattended
  "blocked",  // ADDED pre-approval. Waiting on the owner's approval. Distinct from `working`: at 2am you need to tel
] as const;
export type AgentState = (typeof AGENT_STATES)[number];

/**
 * CONTRACT §4.1 evt.job.updated / evt.job.completed.
 *
 * CLOSED SET.
 */
export const JOB_STATUSES = [
  "queued",
  "running",
  "blocked",  // approval outstanding, still live
  "succeeded",  // spec v3.0 called this `done`; renamed to pair with `failed`
  "failed",
  "cancelled",
  "needsReview",  // ADDED pre-approval. An approval expired unanswered after 30 minutes (spec §5 rule 5). Deliberately n
] as const;
export type JobStatus = (typeof JOB_STATUSES)[number];

/**
 * CONTRACT §4.1 evt.job.created. What caused this job to exist.
 *
 * CLOSED SET.
 */
export const CREATED_BY = [
  "user",
  "agent",
  "schedule",  // cron / time trigger
  "fileWatch",  // ADDED pre-approval. Phase 5 file-watch trigger.
  "email",  // ADDED pre-approval. Phase 5 inbound mail trigger.
  "webhook",  // ADDED pre-approval. Phase 5 webhook trigger.
  "systemEvent",  // ADDED pre-approval, NOT in the owner's list. Spec §3.4/line 396 lists five trigger types: time, file
] as const;
export type CreatedBy = (typeof CREATED_BY)[number];

/**
 * CONTRACT §6.5 cmd.pty.report. Keeps the audit trail complete even though the daemon never sees the byte stream.
 *
 * CLOSED SET.
 */
export const PTY_REPORT_EVENTS = [
  "started",
  "exited",
  "cwdChanged",
  "titleChanged",
  "killed",
  "startFailed",  // ADDED pre-approval. Without it, a node-pty spawn failure leaves an issued grant with no terminal eve
] as const;
export type PtyReportEvent = (typeof PTY_REPORT_EVENTS)[number];

/**
 * CONTRACT §2.2 cmd.hello. Deliberately NOT extended with `mobile` — see the rationale in the report and CONTRACT §2.4.
 *
 * CLOSED SET.
 */
export const SURFACES = [
  "console",
  "orb",
] as const;
export type Surface = (typeof SURFACES)[number];

/**
 * CONTRACT §6.4. Defined once in core/config/permissions.yaml. Unchanged — spec §6 defines exactly three and no fourth is implied through Phase 8.
 *
 * CLOSED SET.
 */
export const TIERS = [
  "green",
  "amber",
  "red",
] as const;
export type Tier = (typeof TIERS)[number];

/**
 * CONTRACT §6.2. Where a byte, an action, or a piece of context came from. This is the prompt-injection boundary: everything except `human` is untrusted.
 *
 * CLOSED SET.
 */
export const PROVENANCE = [
  "human",  // the owner typed or clicked it — the only trusted source
  "program",  // process stdout/stderr on this machine
  "agent",  // model-proposed
  "schedule",  // an unattended trigger
  "external",  // ADDED pre-approval. Content fetched from off this machine: email bodies, web pages, remote READMEs. 
  "system",  // ADDED pre-approval. The daemon's own actions (daemon.start, auth.lockout). core/security/audit.py al
] as const;
export type Provenance = (typeof PROVENANCE)[number];

/**
 * CONTRACT §5.2 cmd.window.spawnAt and §6.6 deep links.
 *
 * CLOSED SET.
 */
export const SPAWN_MODES = [
  "window",
  "tab",
  "pane",
  "cdCurrent",  // ADDED pre-approval. Change directory in the focused terminal instead of spawning a new one. This is 
] as const;
export type SpawnMode = (typeof SPAWN_MODES)[number];

/**
 * CONTRACT §4.2 evt.fs.changed.
 *
 * CLOSED SET.
 */
export const FS_CHANGE_KINDS = [
  "created",
  "modified",
  "deleted",
  "renamed",
  "hydrationChanged",  // ADDED pre-approval. A OneDrive file switching between cloud-only and local is not create/modify/dele
] as const;
export type FsChangeKind = (typeof FS_CHANGE_KINDS)[number];

/**
 * CONTRACT §4.2 FsEntry.cloudState, derived from attributes alone — never by opening the file.
 *
 * CLOSED SET.
 */
export const CLOUD_STATES = [
  "local",
  "cloudOnly",
  "pinned",
  "partial",
  "unknown",  // ADDED pre-approval. Microsoft documents FILE_ATTRIBUTE_UNPINNED as 'internal use only', and Dropbox/
] as const;
export type CloudState = (typeof CLOUD_STATES)[number];

/**
 * CONTRACT §4.1 evt.permission.resolved and §5.1 cmd.permission.respond. NOTE the asymmetry: a surface may only SEND approve|deny. `expired` is daemon-emitted only.
 *
 * CLOSED SET.
 */
export const DECISIONS = [
  "approve",
  "deny",
  "expired",  // ADDED pre-approval, and a direct consequence of accepting needsReview. evt.permission.request carrie
] as const;
export type Decision = (typeof DECISIONS)[number];

/** Values a SURFACE may send. Others are daemon-emitted only. */
export const DECISIONS_SENDABLE = [
  "approve",
  "deny",
] as const;
export type SendableDecision = (typeof DECISIONS_SENDABLE)[number];

/**
 * CONTRACT §4.1 transcript messages. Unchanged — covers the full tool-use loop through Phase 8.
 *
 * CLOSED SET.
 */
export const ROLES = [
  "user",
  "assistant",
  "system",
  "tool",
] as const;
export type Role = (typeof ROLES)[number];

/**
 * CONTRACT §4.1 evt.notification. Unchanged — severity, not category. Job outcomes ride evt.job.completed, not this.
 *
 * CLOSED SET.
 */
export const NOTIFICATION_LEVELS = [
  "info",
  "warn",
  "error",
] as const;
export type NotificationLevel = (typeof NOTIFICATION_LEVELS)[number];

/**
 * CONTRACT §5.4. DELIBERATELY OPEN — see §7.4. Consumers MUST have a default branch. Diagnostic codes will keep accruing through Phase 8, and making every new one a breaking change would be absurd.
 *
 * OPEN SET — consumers MUST have a default branch.
 */
export const ERROR_CODES = [
  "protocol.unknownType",
  "protocol.badEnvelope",
  "auth.required",
  "permission.denied",
  "permission.pending",
  "permission.expired",  // ADDED. Pairs with Decision.expired.
  "notFound",
  "busy",
  "rateLimited",  // ADDED. CloseCode 4429 existed with no in-band equivalent for a request refused without closing the s
  "budgetExceeded",  // ADDED. Spec §6 mandates a hard nightly budget cap. A job refused on budget is not permission.denied 
  "unavailable",  // ADDED. Spec §3.9 requires graceful degradation when the Claude API is unreachable: queue, don't cras
  "internal",
] as const;
export type ErrorCode = (typeof ERROR_CODES)[number];

/**
 * CONTRACT §2.2. DELIBERATELY OPEN. Standard RFC 6455 codes (1001 going away, 1009 message too big) are used unchanged and are not re-declared here.
 *
 * OPEN SET — consumers MUST have a default branch.
 */
export const CLOSE_CODES = {
  Unauthorized: 4401,
  HandshakeTimeout: 4408,
  ProtocolMismatch: 4409,
  RateLimited: 4429,
} as const;
export type CloseCode = (typeof CLOSE_CODES)[keyof typeof CLOSE_CODES];
