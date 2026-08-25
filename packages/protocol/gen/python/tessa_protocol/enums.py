"""GENERATED FILE — DO NOT EDIT.

Source: packages/protocol/schema/enums.json
Regenerate: node packages/protocol/build-enums.mjs

CONTRACT.md §7.4 — sets marked closed are exhaustive. Adding a value to a
closed set is a BREAKING change requiring a PROTOCOL_VERSION bump.
"""

from typing import Final, Literal


PROTOCOL_VERSION: Final[int] = 1

# ── AgentState — CLOSED SET
# CONTRACT §4.1 evt.agent.state. Orb drives the sphere from this; Console drives its status bar.
AgentState = Literal["idle", "listening", "thinking", "speaking", "working", "blocked"]
AGENT_STATES: Final[frozenset[str]] = frozenset({"idle", "listening", "thinking", "speaking", "working", "blocked"})

# ── JobStatus — CLOSED SET
# CONTRACT §4.1 evt.job.updated / evt.job.completed.
JobStatus = Literal["queued", "running", "blocked", "succeeded", "failed", "cancelled", "needsReview"]
JOB_STATUSES: Final[frozenset[str]] = frozenset({"queued", "running", "blocked", "succeeded", "failed", "cancelled", "needsReview"})

# ── CreatedBy — CLOSED SET
# CONTRACT §4.1 evt.job.created. What caused this job to exist.
CreatedBy = Literal["user", "agent", "schedule", "fileWatch", "email", "webhook", "systemEvent"]
CREATED_BY: Final[frozenset[str]] = frozenset({"user", "agent", "schedule", "fileWatch", "email", "webhook", "systemEvent"})

# ── PtyReportEvent — CLOSED SET
# CONTRACT §6.5 cmd.pty.report. Keeps the audit trail complete even though the daemon never sees the byte stream.
PtyReportEvent = Literal["started", "exited", "cwdChanged", "titleChanged", "killed", "startFailed"]
PTY_REPORT_EVENTS: Final[frozenset[str]] = frozenset({"started", "exited", "cwdChanged", "titleChanged", "killed", "startFailed"})

# ── Surface — CLOSED SET
# CONTRACT §2.2 cmd.hello. Deliberately NOT extended with `mobile` — see the rationale in the report and CONTRACT §2.4.
Surface = Literal["console", "orb"]
SURFACES: Final[frozenset[str]] = frozenset({"console", "orb"})

# ── Tier — CLOSED SET
# CONTRACT §6.4. Defined once in core/config/permissions.yaml. Unchanged — spec §6 defines exactly three and no fourth is implied through Phase 8.
Tier = Literal["green", "amber", "red"]
TIERS: Final[frozenset[str]] = frozenset({"green", "amber", "red"})

# ── Provenance — CLOSED SET
# CONTRACT §6.2. Where a byte, an action, or a piece of context came from. This is the prompt-injection boundary: everything except `human` is untrusted.
Provenance = Literal["human", "program", "agent", "schedule", "external", "system"]
PROVENANCE: Final[frozenset[str]] = frozenset({"human", "program", "agent", "schedule", "external", "system"})

# ── SpawnMode — CLOSED SET
# CONTRACT §5.2 cmd.window.spawnAt and §6.6 deep links.
SpawnMode = Literal["window", "tab", "pane", "cdCurrent"]
SPAWN_MODES: Final[frozenset[str]] = frozenset({"window", "tab", "pane", "cdCurrent"})

# ── FsChangeKind — CLOSED SET
# CONTRACT §4.2 evt.fs.changed.
FsChangeKind = Literal["created", "modified", "deleted", "renamed", "hydrationChanged"]
FS_CHANGE_KINDS: Final[frozenset[str]] = frozenset({"created", "modified", "deleted", "renamed", "hydrationChanged"})

# ── CloudState — CLOSED SET
# CONTRACT §4.2 FsEntry.cloudState, derived from attributes alone — never by opening the file.
CloudState = Literal["local", "cloudOnly", "pinned", "partial", "unknown"]
CLOUD_STATES: Final[frozenset[str]] = frozenset({"local", "cloudOnly", "pinned", "partial", "unknown"})

# ── Decision — CLOSED SET
# CONTRACT §4.1 evt.permission.resolved and §5.1 cmd.permission.respond. NOTE the asymmetry: a surface may only SEND approve|deny. `expired` is daemon-emitted only.
Decision = Literal["approve", "deny", "expired"]
DECISIONS: Final[frozenset[str]] = frozenset({"approve", "deny", "expired"})
DECISIONS_SENDABLE: Final[frozenset[str]] = frozenset({"approve", "deny"})  # a surface may only SEND these

# ── Role — CLOSED SET
# CONTRACT §4.1 transcript messages. Unchanged — covers the full tool-use loop through Phase 8.
Role = Literal["user", "assistant", "system", "tool"]
ROLES: Final[frozenset[str]] = frozenset({"user", "assistant", "system", "tool"})

# ── NotificationLevel — CLOSED SET
# CONTRACT §4.1 evt.notification. Unchanged — severity, not category. Job outcomes ride evt.job.completed, not this.
NotificationLevel = Literal["info", "warn", "error"]
NOTIFICATION_LEVELS: Final[frozenset[str]] = frozenset({"info", "warn", "error"})

# ── ErrorCode — OPEN SET — handle unknown values gracefully
# CONTRACT §5.4. DELIBERATELY OPEN — see §7.4. Consumers MUST have a default branch. Diagnostic codes will keep accruing through Phase 8, and making every new one a breaking change would be absurd.
ERROR_CODES: Final[frozenset[str]] = frozenset({"protocol.unknownType", "protocol.badEnvelope", "auth.required", "permission.denied", "permission.pending", "permission.expired", "notFound", "busy", "rateLimited", "budgetExceeded", "unavailable", "internal"})

# ── CloseCode — OPEN SET — handle unknown values gracefully
# CONTRACT §2.2. DELIBERATELY OPEN. Standard RFC 6455 codes (1001 going away, 1009 message too big) are used unchanged and are not re-declared here.
CLOSE_UNAUTHORIZED: Final[int] = 4401
CLOSE_HANDSHAKE_TIMEOUT: Final[int] = 4408
CLOSE_PROTOCOL_MISMATCH: Final[int] = 4409
CLOSE_RATE_LIMITED: Final[int] = 4429
CLOSE_CODES: Final[frozenset[int]] = frozenset({4401, 4408, 4409, 4429})
