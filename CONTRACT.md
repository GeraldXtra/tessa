# CONTRACT.md — Zoey Surface/Daemon Protocol

**PROTOCOL_VERSION: 1**
Status: **APPROVED 2026-08-12 — FROZEN** · Owner: Gerald (Titan Wave LTD)
Created: 2026-08-12

> Frozen on the owner's approval. From this point every change follows §7:
> additive changes do not bump `PROTOCOL_VERSION`; breaking changes do, and require
> the owner's approval plus both surfaces updating together.
> **Neither surface session may edit this file.**

---

## 0. What this document is, and the rule that governs it

ZOEY_OS has **one daemon and two front-ends**:

| Surface | Directory | Built by | What it is |
|---|---|---|---|
| **Zoey Orb** | `apps/orb` | Orb session | Voice UI — particle sphere, calendar, live transcript, companion switcher, KNOWLEDGE VIEW |
| **Zoey Console** | `apps/console` | Console session | Terminal, file tree, blocks |
| **Zoey Core** | `core/` | shared | Python daemon — brain, tools, permission guard, audit, memory |

Both surfaces talk to the same daemon over the same local WebSocket. This file is the only thing preventing them from diverging.

> ### THE RULE
> **Neither surface session may edit this file.**
> A change is proposed to the owner as a diff with rationale; the owner applies it.
> Read this file at the start of every session. See §7 for versioning.

---

## 1. Transport

- The daemon binds **`127.0.0.1` only**. Never `0.0.0.0`, never `::`, never a LAN address.
- Protocol: **WebSocket**, path **`/v1`**. Text frames, UTF-8, exactly **one JSON object per frame**.
- Preferred port **47600**. If occupied, the next free port ascending (47601, 47602, …).
- **The port is discovered, never hard-coded.** On start the daemon writes:

**`%LOCALAPPDATA%\Zoey\runtime.json`**

```json
{
  "protocolVersion": 1,
  "port": 47600,
  "token": "9f2c...64 hex chars...",
  "pid": 12345,
  "startedAt": "2026-08-12T09:14:03.221Z"
}
```

- The file's ACL is **user-only**: inheritance disabled, owner + `SYSTEM` full control, no other principals.
- The file is deleted on clean shutdown.
- **A stale file whose `pid` is not a live process must be ignored, not trusted.** Surfaces verify liveness before connecting.
- Max frame size **1 MiB**. Anything larger (scrollback exports, file blobs) uses chunked transfer across multiple frames, never one giant frame.

---

## 2. Authentication handshake — non-negotiable, Phase 1

> **The threat:** any webpage the owner visits can execute `new WebSocket('ws://127.0.0.1:47600/v1')`. Loopback is **not** a security boundary. Without all three controls below, a random browser tab can command the agent, read the filesystem, and spawn shells.

### 2.1 The three controls

**1 — Per-launch token.**
32 random bytes (`secrets.token_hex(32)`), regenerated on **every daemon launch**. Read by the surface from `runtime.json`, which only the owner can read.
Sent in the **first frame's payload**. **Never in the URL, query string, or subprotocol** — those leak into logs, proxies, and crash dumps.

**2 — Origin allowlist.**
The daemon reads the `Origin` header of the upgrade request and accepts **only**:

```
zoey://console
zoey://orb
```

Everything else — any `http://` or `https://` origin, `file://`, `null`, or a missing header on a browser-shaped request — is **rejected and audit-logged**.

This is the control that actually stops the drive-by attack: browsers always attach `Origin` on a WebSocket handshake and **script cannot forge it**. A native client can set it freely, which is exactly why §2.3 requires the surface's WebSocket client to live in a **non-browser process**.

**3 — Handshake deadline.**
A connection that has not sent a valid `cmd.hello` within **3000 ms** is closed with **`4408`**.

### 2.2 Sequence

```
client → cmd.hello  { token, surface: "console" | "orb", surfaceVersion, protocolVersion: 1 }
daemon → res.hello  { ok: true, daemonVersion, protocolVersion, capabilities: [...], sessionId }
```

Close codes:

| Code | Meaning |
|---|---|
| `4401` | Unauthorized — bad/absent token, or disallowed `Origin` |
| `4408` | Handshake timeout — no valid `cmd.hello` within 3000 ms |
| `4409` | Protocol mismatch — client `protocolVersion` ≠ daemon `PROTOCOL_VERSION` |
| `4429` | Rate limited — too many failed attempts |

### 2.3 Additional binding rules

- **The WebSocket client must not live in a browser/renderer context.** In Electron it lives in the **main process**; the renderer reaches it over `contextBridge` IPC. Rationale: a renderer cannot set an arbitrary `Origin`, and holding the token in a web context puts it one XSS away from any rendered content.
- **Token rotates every daemon restart.** Surfaces re-read `runtime.json` on every reconnect and **never cache the token to disk**.
- Failed auth → close immediately, audit-log, apply 1 s per-source backoff. **Five failures within 60 s disables the listener** until the daemon is restarted.
- The daemon **never** logs the token value, in any log level.

### 2.4 Why there is no `mobile` surface

`Surface` is `console | orb` and deliberately excludes `mobile`.

A phone is a different device. It cannot read `%LOCALAPPDATA%\Zoey\runtime.json`, and it cannot reach `127.0.0.1`. Every control in §2 — the token file, the loopback bind, the `zoey://` Origin allowlist — is local-only *by construction*. Adding a `mobile` value would declare a capability that no part of this contract can serve.

Remote access is a different design (relay or push service, a different auth model, a different threat model) and will require its own contract revision regardless. Reserving the enum value now buys nothing and implies something untrue. The spec's answer for "check on jobs while away" is an **outbound bridge** — push notification, Telegram/WhatsApp — which is not a surface and needs no value here.

---

## 3. Message envelope

Every frame, both directions, is exactly this shape:

```jsonc
{
  "v": 1,                             // integer protocol version — REQUIRED
  "id": "01JAV3K2QF8YB9X4M7P0RTZC5N", // ULID, unique per message — REQUIRED
  "ts": "2026-08-12T09:14:03.221Z",   // ISO-8601 UTC, milliseconds — REQUIRED
  "type": "evt.agent.state",          // namespaced type — REQUIRED
  "corr": null,                       // `id` this responds to; null if unsolicited
  "payload": {}                       // type-specific object — REQUIRED (may be {})
}
```

### 3.1 Type namespaces — the anti-collision mechanism

| Prefix | Direction | Meaning |
|---|---|---|
| `cmd.*` | surface → daemon | Instruction. Always answered by `res.*` or `err.*` with `corr` set to the command's `id`. |
| `res.*` | daemon → surface | Success response. |
| `err.*` | daemon → surface | Failure response. Payload: `{ code, message, retryable }`. |
| `evt.*` | daemon → surface | Unsolicited event, broadcast to every subscriber. |

Ownership of sub-namespaces:

| Sub-namespace | Owner |
|---|---|
| `agent.*`, `job.*`, `transcript.*`, `companion.*`, `permission.*`, `audit.*`, `daemon.*`, `config.*` | **SHARED** — both surfaces implement |
| `pty.*`, `fs.*`, `window.*` | **Console only** |
| `voice.*`, `scene.*` | **Orb only** |

### 3.2 Forward-compatibility rule — MANDATORY

> An **unknown `type`**, or an **unknown field inside a known `payload`**, **MUST be ignored silently**.
> Never an error. Never a crash. Never a disconnect.

This single rule is what allows one surface to ship a new feature without breaking the other, and is why additive changes do not bump `PROTOCOL_VERSION` (§7).

### 3.3 Ordering and idempotency

- `evt.transcript.delta` carries a **monotonic `seq`** scoped to its `messageId`. Consumers reassemble by `seq` and must tolerate out-of-order arrival. (The PTY byte stream also carries a `seq`, but it never crosses this protocol — see §4.2.)
- All `cmd.*` are **idempotent by `id`**. A retried command with the same `id` must not execute twice.

---

## 4. Events — daemon → surface

### 4.1 Shared events (both surfaces subscribe)

| Type | Payload | Notes |
|---|---|---|
| `evt.agent.state` | `{ companionId, state, detail? }` | `state` ∈ `idle` \| `listening` \| `thinking` \| `speaking` \| `working` \| `blocked`. Orb drives the sphere from this; Console drives its status bar from the same event. **`blocked` = waiting on your approval** — deliberately distinct from `working`, so that walking past the machine at 2am tells you "busy" from "stuck waiting for you". Orb renders it amber and static. |
| `evt.companion.roster` | `{ companions: [{ companionId, name, voice, tools[], scope }] }` | Full snapshot, sent on subscribe. |
| `evt.companion.status` | `{ companionId, name, state, busy, tools[], scope }` | Per-companion change. |
| `evt.transcript.delta` | `{ companionId, messageId, role, seq, delta, done }` | Streaming text. `role` ∈ `user` \| `assistant` \| `system` \| `tool`. `done:true` closes the message. |
| `evt.transcript.message` | `{ companionId, message: { messageId, role, text, toolCalls?, ts } }` | A complete, non-streamed message. |
| `evt.job.created` | `{ jobId, kind, title, tier, createdBy, steps: [{ index, title, status }] }` | `createdBy` ∈ `user` \| `agent` \| `schedule` \| `fileWatch` \| `email` \| `webhook` \| `systemEvent`. The last four are the Phase 5 trigger types (spec §3.4); they are neither `agent` nor `schedule`. |
| `evt.job.progress` | `{ jobId, stepIndex, pct?, note? }` | |
| `evt.job.updated` | `{ jobId, status, stepIndex? }` | `status` ∈ `queued` \| `running` \| `blocked` \| `succeeded` \| `failed` \| `cancelled` \| `needsReview`. **`blocked`** = approval outstanding, still live. **`needsReview`** = the approval window lapsed unanswered after 30 min (spec §5 rule 5) — not `failed` (nothing broke), not `cancelled` (nobody cancelled). |
| `evt.job.completed` | `{ jobId, status, result?, error? }` | Terminal event for a job. |
| `evt.permission.request` | `{ requestId, tier, tool, args, provenance, expiresAt }` | `tier` ∈ `green` \| `amber` \| `red`. **`provenance` REQUIRED** — see §6. Either surface may render the approval card. |
| `evt.permission.resolved` | `{ requestId, decision, decidedBy, remembered }` | `decision` ∈ `approve` \| `deny` \| `expired`. Broadcast so the *other* surface dismisses its card. **`expired` is daemon-emitted only** — see §5.1. |
| `evt.audit.appended` | `{ entryId, actor, tool, tier, summary, ts }` | Live audit viewer. |
| `evt.daemon.health` | `{ uptimeS, cpuPct, memMB, apiReachable, budgetSpent, budgetCap }` | Heartbeat every 5 s. |
| `evt.daemon.shutdown` | `{ reason, restarting }` | Surfaces show a reconnect state. |
| `evt.notification` | `{ level, title, body, actions[] }` | `level` ∈ `info` \| `warn` \| `error`. |

### 4.2 Console-only events

> **The PTY byte stream is deliberately NOT in this contract.**
> Terminal output does not traverse the daemon. A noisy `npm install` emits megabytes; base64-encoding that into JSON frames and pumping it through Python on a 2-core machine would add ~33% inflation plus JSON escaping to the hottest path in the app, for no benefit — the daemon has no reason to see every byte.
>
> **What the daemon owns:** *authorization* (may this session be spawned, in this directory, by this actor?), *audit* (what ran, when, triggered by what), and *revocation* (panic hotkey, budget cap, policy change).
> **What the Console owns:** the byte stream, from an Electron `utilityProcess` straight to the renderer over a `MessagePort`.
>
> This keeps the permission guard authoritative without putting Python in the data path.

| Type | Payload |
|---|---|
| `evt.pty.sessions` | `{ sessions: [{ sessionId, profileId, cwd, title, startedAt, busy }] }` — roster the daemon assembles from Console reports. **The Orb may subscribe** to show terminal activity. |
| `evt.pty.revoke` | `{ sessionId, reason }` — the daemon orders a session killed. The Console **must** comply and report back. |
| `evt.fs.children` | `{ requestId, path, entries[], truncated, complete }` — may span multiple frames |
| `evt.fs.changed` | `{ path, kind }` — `kind` ∈ `created` \| `modified` \| `deleted` \| `renamed` \| `hydrationChanged`. The last means a file moved between cloud-only and local: content unchanged, cost badge changed. |
| `evt.fs.hydrationWarning` | `{ path, bytesToDownload, estimatedCostNGN }` — see §6.3 |

`entries[]` element shape:

```jsonc
{
  "name": "package.json",
  "isDir": false,
  "size": 1432,              // EndOfFile
  "allocSize": 4096,         // AllocationSize — used for hydration cost
  "mtime": "2026-07-02T11:04:55.000Z",
  "attrs": 32,               // raw Win32 FILE_ATTRIBUTE_* bitfield
  "reparseTag": 0,           // 0 = not a reparse point
  "cloudState": "local"      // "local" | "cloudOnly" | "pinned" | "partial" | "unknown"
}
```

### 4.3 Orb-only events

Listed so the Console never claims these names:
`evt.voice.wake` · `evt.voice.vad` · `evt.voice.partialTranscript` · `evt.voice.amplitude` · `evt.scene.state`

---

## 5. Commands — surface → daemon

### 5.1 Shared commands

| Type | Payload | Response |
|---|---|---|
| `cmd.hello` | `{ token, surface, surfaceVersion, protocolVersion }` | `res.hello` |
| `cmd.subscribe` | `{ topics[] }` — prefix globs, e.g. `["agent.*","job.*"]` | `res.subscribe` |
| `cmd.unsubscribe` | `{ topics[] }` | `res.ok` |
| `cmd.agent.message` | `{ companionId, text, attachments[]? }` | `res.agent.accepted { messageId }` |
| `cmd.agent.cancel` | `{ companionId, messageId? }` | `res.ok` |
| `cmd.companion.switch` | `{ companionId }` | `res.ok` |
| `cmd.job.create` | `{ kind, title, args, tier }` | `res.job.created { jobId }` |
| `cmd.job.cancel` | `{ jobId }` | `res.ok` |
| `cmd.job.retry` | `{ jobId }` | `res.ok` |
| `cmd.permission.respond` | `{ requestId, decision, remember? }` — `decision` ∈ `approve` \| `deny` **only**. A surface may never send `expired`; that value exists solely so the daemon can resolve a lapsed request and clear the other surface's card. | `res.ok` |
| `cmd.config.get` | `{ key }` | `res.config { key, value }` |
| `cmd.config.set` | `{ key, value }` | `res.config` |
| `cmd.audit.query` | `{ since?, limit, filter? }` | `res.audit { entries[] }` |
| `cmd.ping` | `{}` | `res.pong` |

### 5.2 Console-only commands

| Type | Payload | Response |
|---|---|---|
| `cmd.pty.requestSpawn` | `{ profileId, cwd, actor, purpose? }` — `actor` ∈ `human` \| `agent` \| `schedule` | `res.pty.grant { grantId, sessionId, expiresAt }`, or `err.permission.pending` followed by `evt.permission.request` |
| `cmd.pty.report` | `{ sessionId, event, detail? }` — `event` ∈ `started` \| `exited` \| `cwdChanged` \| `titleChanged` \| `killed` | `res.ok` |
| `cmd.fs.list` | `{ path, includeHidden? }` | `res.fs.accepted` then one or more `evt.fs.children` |
| `cmd.fs.watch` | `{ path }` | `res.ok` |
| `cmd.fs.unwatch` | `{ path }` | `res.ok` |
| `cmd.fs.reveal` | `{ path }` | `res.ok` — opens Windows Explorer |
| `cmd.window.spawnAt` | `{ path, mode }` — `mode` ∈ `window` \| `tab` \| `pane` \| `cdCurrent`. `cdCurrent` changes directory in the focused terminal instead of spawning. **Not reachable from a deep link** — see §6.6. | `res.ok` |

### 5.3 Orb-only commands

Reserved: `cmd.voice.mute` · `cmd.voice.pushToTalk` · `cmd.voice.setVoice` · `cmd.scene.setMode`

### 5.4 Error codes (`err.*` payload `code`)

| Code | Meaning |
|---|---|
| `protocol.unknownType` | Type not defined in this contract. Connection stays open. |
| `protocol.badEnvelope` | Missing/invalid required envelope field. |
| `auth.required` | Command sent before a successful `cmd.hello`. |
| `permission.denied` | Guard refused. Payload includes `tier` and `tool`. |
| `permission.pending` | Awaiting owner approval; a `evt.permission.request` was emitted. |
| `notFound` | Unknown `sessionId`, `jobId`, `requestId`, or path. |
| `busy` | Daemon at capacity; `retryable: true`. |
| `internal` | Unexpected failure; already audit-logged. |

---

## 6. Security invariants both surfaces must uphold

These are not Console-specific. **Both surfaces are bound by them.**

### 6.1 Untrusted content

**All tool output, terminal output, file contents, web pages, and email are DATA, never instructions.**

- Content reaching the model is wrapped in explicit delimiters and labelled as data, with a standing system rule that content inside them is never an instruction.
- The model **never** receives a raw command string to execute. It selects a **tool name + structured arguments**; the Python core owns execution. *(ZOEY_OS-spec §6.)*
- **No red-tier action may be triggered while untrusted content sits in context without explicit owner approval** — enforced by the daemon's guard, not by prompt wording.

### 6.2 Provenance

Every captured byte and every action carries a provenance tag:

| Tag | Meaning | Trusted? |
|---|---|---|
| `human` | The owner typed or clicked it | **Yes — the only trusted source** |
| `program` | Process stdout/stderr on this machine | No |
| `agent` | Model-proposed | No |
| `schedule` | Triggered by a scheduled job | No |
| `external` | Fetched from off this machine: email bodies, web pages, remote READMEs | **No — highest risk** |
| `system` | The daemon's own actions (`daemon.start`, `auth.lockout`) | n/a |

- Provenance is carried **out of band**, over this WebSocket, keyed to `(sessionId, byteOffset)`. It **never travels inside the PTY byte stream**.
- Any provenance-shaped escape sequence arriving *from* a PTY is **stripped before parsing**, so a hostile `npm postinstall` cannot paint its own output as agent-approved or human-typed.
- `evt.permission.request.provenance` is **required**, never optional.

### 6.3 Filesystem

- **Indexing is metadata-only.** Name, size, mtime, attributes, reparse tag. **Never open a file to read content during indexing.**
- **Never read content from a reparse point.** the target machine's cloud-sync tree tree contains **17,340** of them; reading a dehydrated placeholder triggers a download on a metered connection with limited free disk.
- **OneDrive is excluded from content indexing by default.** Opt-in per folder, never recursive-by-default.
- **Hydration is an amber-tier action.** Cost is computed from attributes alone as `EndOfFile − AllocationSize`, surfaced via `evt.fs.hydrationWarning` before any recall happens.
- Deletion is **Recycle Bin only. Never hard delete.** *(ZOEY_OS-spec §3.3.)*

### 6.4 Permission tiers

Tiers are defined once, in **`core/config/permissions.yaml`** — the model already specified in ZOEY_OS-spec §6. **Surfaces render tiers; they never define or evaluate them.** The daemon is the only authority.

### 6.5 Execution authorization

- **No PTY session may be created without a grant.** The Console calls `cmd.pty.requestSpawn` and waits for `res.pty.grant`. The daemon applies the tier policy, protected-path policy, and audit before granting. A Console that spawns without a grant is a contract violation.
- A grant authorizes **one session, in one directory**. It is not reusable and it expires.
- The daemon may revoke at any time via `evt.pty.revoke` — panic hotkey, budget cap, or policy change. **The Console must comply and report back.**
- Session lifecycle is reported via `cmd.pty.report` so the audit log stays complete even though the byte stream never reaches the daemon.

### 6.6 Deep-link safety — `zoey://`

Any webpage can trigger a registered protocol handler. Therefore:

- **The `zoey://` grammar carries a path and a display mode. Nothing else.**
- **There is no `cmd=` parameter, and none may ever be added** — not even an allowlisted one. `zoey://run?cmd=...` would be a remote-code-execution vector reachable from a hostile webpage.
- A window opened from a deep link **always starts with an empty prompt**. It never pre-fills, never auto-runs.
- The path is resolved and validated against protected-path policy before a window opens.

```
zoey://open?path=<url-encoded-absolute-path>&mode=window|tab|pane
```

- **`mode` is a strict subset of `SpawnMode`.** `cdCurrent` is deliberately NOT reachable from a deep link: it mutates an already-open terminal rather than creating one, so a hostile page reaching it could silently change the working directory of a session you are actively typing into — and the next `rm -rf .` or `git clean -fd` would land somewhere you did not intend. **Deep links may only ever CREATE.**

Adding any parameter to this grammar is a **breaking change** under §7.3.

---

## 7. Versioning rule

1. **`PROTOCOL_VERSION`** is the single integer at the top of this file. It appears in every envelope as `v` and in the handshake.

2. **Additive changes do NOT bump the version.** A new `evt.*`/`cmd.*` type, or a new **optional** payload field, is legal because §3.2 mandates that unknown types and fields are ignored. Append it to the tables above and to §8.

3. **Breaking changes DO bump the version.** Renaming or removing a type or field, changing a field's type, changing the handshake, or **adding a value to a closed enum**. These require:
   - the owner's explicit approval,
   - both surface sessions updating together,
   - the daemon rejecting mismatched clients with close code `4409`.

4. **Enums are defined once, in `packages/protocol/schema/enums.json`**, and TypeScript and Python are **generated** from it. Never hand-maintain a copy — that file is the authority, and `scripts/check-contract.mjs` fails the build if the generated output drifts from it.

   **CLOSED sets** — consumers switch on these exhaustively, so adding a value is a **breaking** change:

   | Enum | Values |
   |---|---|
   | `AgentState` | `idle` `listening` `thinking` `speaking` `working` `blocked` |
   | `JobStatus` | `queued` `running` `blocked` `succeeded` `failed` `cancelled` `needsReview` |
   | `Tier` | `green` `amber` `red` |
   | `Provenance` | `human` `program` `agent` `schedule` `external` `system` |
   | `CreatedBy` | `user` `agent` `schedule` `fileWatch` `email` `webhook` `systemEvent` |
   | `Role` | `user` `assistant` `system` `tool` |
   | `CloudState` | `local` `cloudOnly` `pinned` `partial` `unknown` |
   | `FsChangeKind` | `created` `modified` `deleted` `renamed` `hydrationChanged` |
   | `SpawnMode` | `window` `tab` `pane` `cdCurrent` |
   | `DeepLinkMode` | `window` `tab` `pane` — strict subset of `SpawnMode`, see §6.6 |
   | `Decision` | `approve` `deny` `expired` — a surface may only SEND `approve`/`deny` |
   | `PtyReportEvent` | `started` `exited` `cwdChanged` `titleChanged` `killed` `startFailed` |
   | `NotificationLevel` | `info` `warn` `error` |
   | `Surface` | `console` `orb` — see §2.4 |

   **OPEN sets** — new values may be added at any time **without** a version bump. Consumers **MUST** have a default branch:

   | Enum | Why open |
   |---|---|
   | `ErrorCode` | Diagnostic codes accrue continuously; making each one breaking would be absurd. |
   | `CloseCode` | Ditto, plus standard RFC 6455 codes (`1001`, `1009`) are used unchanged. |

   **Naming:** multi-word values are **camelCase** on the wire (`needsReview`, `fileWatch`, `cloudOnly`). The database may use `snake_case` internally — the wire and the schema are not the same thing.

5. **Neither surface session edits this file.** Propose a diff with rationale to the owner; the owner applies it.

6. **The daemon is the referee.** A type not defined here gets `err.protocol.unknownType` and an audit entry — the connection is not dropped.

---

## 8. Changelog

| Version | Date | Change |
|---|---|---|
| 1 | 2026-08-12 | Initial contract. Awaiting owner approval. |
| 1 *(pre-approval revision 2)* | 2026-08-12 | **Enum audit — the last cheap moment before §7.3 makes additions breaking.** Added `AgentState.blocked`; `JobStatus.needsReview`; `CreatedBy.fileWatch/email/webhook/systemEvent`; `PtyReportEvent.startFailed`; `Provenance.external/system`; `SpawnMode.cdCurrent`; `CloudState.unknown`; `FsChangeKind.hydrationChanged`; `Decision.expired` (daemon-emitted only); `ErrorCode.permission.expired/rateLimited/budgetExceeded/unavailable`. Declared `ErrorCode` and `CloseCode` **open** sets. Added §2.4 (no `mobile` surface) and `DeepLinkMode` as a strict subset of `SpawnMode` (§6.6). Enums moved to `packages/protocol/schema/enums.json` as the single generated source of truth. `PROTOCOL_VERSION` stays 1 — pre-approval. |
| 1 *(pre-approval revision)* | 2026-08-12 | **Removed the PTY byte stream from the protocol.** The first draft carried `evt.pty.data` (base64 terminal output), `cmd.pty.write`, `cmd.pty.resize`, and `cmd.pty.kill` through the daemon. On a 2-core machine that put megabytes of `npm install` output through Python with ~33% base64 inflation plus JSON escaping — the hottest path in the app, in the process with the least reason to see it. Replaced with `cmd.pty.requestSpawn` / `cmd.pty.report` / `evt.pty.revoke` / `evt.pty.sessions`: the daemon **authorizes, audits, and revokes**; the Console owns the bytes (§4.2, §6.5). Added §6.6 deep-link safety. Revised **before** approval, so `PROTOCOL_VERSION` stays 1. |

---

## 9. Design tokens

Source of truth: **`packages/tokens/tokens.json`**. CSS custom properties and a Python constants module are **generated** from it. **Neither surface hard-codes a hex value.**

Colour values below are authoritative, from `ZOEY_OS-spec.md` §3.8.

```css
/* ---- Brand colour (ZOEY_OS-spec §3.8) ---- */
--bg-void:       #08080A;
--bg-ambient:    #0D1524;
--panel:         rgba(18,18,22,0.72);
--panel-border:  rgba(255,255,255,0.06);
--accent:        #FF6B1A;
--accent-dim:    #B84D12;
--sphere-hot:    #FF3B00;
--sphere-cool:   #FFA94D;
--status-active: #22C55E;
--text:          #E8E8EA;
--text-muted:    #6B6B72;

/* ---- Semantic (reuses brand values, adds no new hues) ---- */
--status-warn:   #FFA94D;   /* = sphere-cool */
--status-error:  #FF3B00;   /* = sphere-hot  */
--status-idle:   #6B6B72;   /* = text-muted  */

/* ---- Provenance gutter (§6.2) ---- */
--prov-human:    transparent;
--prov-program:  rgba(107,107,114,0.55);
--prov-agent:    rgba(255,107,26,0.75);

/* ---- Type scale — 1.250 major third, 13px base ---- */
--font-mono: "JetBrains Mono", ui-monospace, "Cascadia Mono", Consolas, monospace;
--fs-label:  10px;
--fs-sm:     11px;
--fs-base:   13px;
--fs-md:     16px;
--fs-lg:     20px;
--fs-xl:     25px;
--lh-tight:  1.2;
--lh-base:   1.5;
--label-transform: uppercase;
--label-tracking:  0.14em;

/* ---- Spacing — 4px base ---- */
--sp-1: 4px;   --sp-2: 8px;   --sp-3: 12px;  --sp-4: 16px;
--sp-5: 24px;  --sp-6: 32px;  --sp-7: 48px;

/* ---- Radii ---- */
--panel-radius: 12px;
--radius-sm:    6px;
--radius-pill:  999px;

/* ---- Layout rails (ZOEY_OS-spec §3.8) ---- */
--rail-w:        48px;
--panel-left-w:  240px;
--panel-right-w: 280px;
--transcript-w:  320px;
```

### 9.1 Standing style rules — both surfaces

- No emoji. No icon soup. No blue-purple gradients.
- Labels are uppercase mono, 10px, 0.14em tracking.
- Centre stage floats over pure void — no card background on the centre stage.

### 9.2 Viewport constraint — affects both surfaces

> The owner's display is **1366×768**.
> `rail 48 + left 240 + right 280 + transcript 320 = **888px** of chrome`, leaving **~478px** of usable centre stage.

**Both surfaces must be usable at 1366×768.** The full four-panel layout is a **≥1600px** layout only. Below that, panels collapse to overlays or drawers. The Console defaults to *rail + terminal + one collapsible side panel*; the Orb must define its own sub-1600px fallback.

---

*— end of contract —*
