# Zoey Console — Plan & Architecture

> **Read [`CONTRACT.md`](./CONTRACT.md) first.** It is the shared protocol between Zoey Console, Zoey Orb, and Zoey Core, and it is read-only for surface sessions. This document is the Console's build plan; the contract is the law.

Owner: Gerald (Titan Wave LTD) · Created 2026-08-12

**Provenance key**
- 🟢 **VERIFIED** — measured on this machine or checked against a primary source.
- 🔵 **FLEET (partial)** — from the 63-agent adversarial design fleet. **Only 14 reported, all from the PROPOSING team; zero opposition rebuttals arrived.** Un-cross-examined.
- ⚪ **JUDGEMENT** — resolving conflicts or filling gaps the fleet never reached.

---

## 1. What this is

A custom Windows console: real terminal, file tree, and — in later phases — a chat interface and an agentic command layer, with Claude Code running inside it.

It is **not** a standalone product. It is **one of two front-ends onto a single Python daemon**:

```
        ┌──────────────┐         ┌──────────────┐
        │  Zoey Orb    │         │ Zoey Console │
        │ (voice UI)   │         │ (this repo)  │
        │ apps/orb     │         │ apps/console │
        └──────┬───────┘         └──────┬───────┘
               │   ws://127.0.0.1:47600/v1
               └───────────┬─────────────┘
                    ┌──────▼───────┐
                    │  Zoey Core   │  core/
                    │ Python daemon│  brain · tools · guard · audit · memory
                    └──────────────┘
```

Zoey Orb is built in **a separate Claude Code session**. The two must not diverge — hence `CONTRACT.md`.

---

## 2. Hard environment constraints 🟢

Measured on this machine. Several kill otherwise-obvious choices.

| Fact | Value | Consequence |
|---|---|---|
| CPU | **i5-7200U — 2 cores / 4 threads** | Electron's main + renderer + GPU + utility processes contend for 2 physical cores. **No worker threads in Phase 1.** |
| RAM | 15.9 GB total, **5.9 GB free** | Scrollback must be capped, not unbounded. |
| GPU / display | Intel HD 620, driver 31.0.101.2130, **1366×768** | WebGL2 *should* work but the driver is a legacy branch. **Runtime GPU probe + DOM fallback is mandatory.** Grid is only ~6.2k cells, so DOM is survivable. |
| Power | **Laptop, battery present** | Power-loss resilience is not theoretical. |
| Free disk `C:` | **14.5 GB** of 476 | Rules out Rust + MSVC (3–7 GB). Budget ~3 GB here; prune builds. |
| Rust / MSVC | **Neither installed** | **No `node-gyp` compilation, ever.** Prebuilt native modules only. No C++/COM shell extension. |
| Node / npm | 25.9.0 / 11.0.0 | Electron path costs zero new toolchain. |
| Python | 3.12.7 | Daemon language. |
| Go | 1.25.4 | Installed, but a third language is rejected — §4.1. |
| PowerShell | **5.1 only. No `pwsh`, no `wt`.** | Causes a real shell-integration bug in Phase 2 — §7.1. |
| WSL2 | `Ubuntu-22.04`, `docker-desktop` | Hosted as profiles. Live FS watching there is impossible — §6.2. |
| Windows | 11 Pro **22631** (23H2). LongPaths **enabled**. | ConPTY present but predates several fixes — §4.1. |
| Claude Code | `@anthropic-ai/claude-code@2.1.228` global | Runs as a PTY child. Zero integration work for v1. |
| OneDrive tree | **79,353 entries at depth ≤4, 18.3 s to walk.** 17,340 reparse points. 2,634 `node_modules`. | **Eager listing is impossible.** Drives §6 entirely. |

**Package baseline** (npm registry, Aug 2026): `electron` **43.4.0** · `@xterm/xterm` **6.0.0** (scoped; unscoped `xterm` is superseded) · `@lydell/node-pty` **1.1.0 stable** (NOT the beta — corrected before install; see below).

### 2.1 The File Explorer requirement, honestly

"List all folders and files on open" taken literally means walking a tree where **depth 4 alone is 79k entries and 18 seconds**. That freezes the app on every launch and risks pulling files over metered data.

What ships instead — same experience, no hang:
- Drives and top-level folders render **instantly** (one shallow read).
- Children load **lazily on expand**, virtualized.
- `node_modules`, `.git`, `dist`, `build`, `venv` **collapsed by default**.
- Reparse points read **by attribute only** — never followed, never hydrated.
- Search across everything comes later, from a metadata index (§7.3) — never a content crawl of OneDrive.

---

## 3. Repository layout

```
C:\dev\zoey\                       # NOT in OneDrive — deliberate (2,634 node_modules would thrash sync)
├── CONTRACT.md                    # SHARED LAW. Read every session. Never edited by a surface session.
├── plan.md                        # this file
├── package.json                   # npm workspaces root
├── packages\
│   ├── protocol\                  # SHARED — ask owner before editing
│   │   ├── schema\*.json          #   JSON Schema = source of truth
│   │   ├── src\index.ts           #   generated TS types + envelope helpers + validator
│   │   └── gen\python\            #   generated TypedDicts for core/
│   └── tokens\                    # SHARED — ask owner before editing
│       ├── tokens.json            #   source of truth
│       ├── dist\tokens.css        #   generated
│       └── dist\tokens.py         #   generated
├── core\                          # SHARED — Python daemon
│   ├── server.py                  #   WS server + auth handshake (CONTRACT §2)
│   ├── security\{guard,audit,secrets}.py
│   ├── pty\supervisor.py
│   ├── fs\{enumerate,watch}.py    #   metadata-only (CONTRACT §6.3)
│   └── config\permissions.yaml    #   green/amber/red — from ZOEY_OS-spec §6
├── apps\
│   ├── console\                   # ← THIS SESSION OWNS THIS
│   │   ├── src\main\              #   Electron main: WS client, token read, window mgmt
│   │   ├── src\preload\           #   contextBridge only
│   │   └── src\renderer\          #   React: terminal, tree, tabs
│   └── orb\                       # ← ORB SESSION OWNS THIS. NEVER EDIT.
└── docs\ZOEY_OS-spec.md           # canonical copy
```

**Ownership rule.** This session touches `apps/console` and — with owner approval — `core/pty` and `core/fs`. It never touches `apps/orb`. Changes to `packages/*` or `CONTRACT.md` are **proposed to the owner, not made**.

---

## 4. Phase 1 architecture

Scope is deliberately tight: **tabs + ConPTY + xterm + lazy file tree + CONTRACT.md.** No command blocks, no AI chat, no indexer.

### 4.1 PTY layer ⚪ — resolving a fleet conflict

🔵 The fleet contradicted itself: the PTY-core agent proposed a standalone **Go** ConPTY host and rejected node-pty; the shell-profiles agent proposed **`@lydell/node-pty`** and rejected the Go host.

**Decision: `@lydell/node-pty` 1.1.0 STABLE. The Go host is rejected for Phase 1.**

⚠️ **Corrected before the first install, and this is what the repo actually runs.** An earlier
draft of this plan recorded `1.2.0-beta.15`, which holds npm's `latest` tag but was four days old.
Verified on the registry: **1.1.0 ships the same six prebuilt platform-specific
`optionalDependencies` and has NO `scripts` field at all**, so it cannot invoke `node-gyp` even as
a fallback — decisive on a machine with no MSVC to recover with. Confirmed on disk after install:
`@lydell/node-pty@1.1.0` with only `@lydell/node-pty-win32-x64` fetched (the `os`/`cpu` gates
prevented a six-platform download).

The Go agent rejected node-pty as *"NAN-based, ABI-pinned"* that would *"likely refuse to load on Node v25."* 🟢 **That is false for node-pty 1.x** — the registry shows it depends on `node-addon-api@^7.1.0`, i.e. it is a **Node-API** addon. N-API is ABI-stable across Node *and* Electron, which is exactly why no rebuild step is needed. The rejection rested on a false premise.

Against the Go host: hand-packing `COORD` and `PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE (0x00020016)` with no compiler validation — which that agent itself called *"where handle leaks hide"* — plus a third language, on 2 cores, solo, part-time.

`@lydell/node-pty` ships prebuilt per-platform binaries (`@lydell/node-pty-win32-x64`) as optional deps. **No compiler required** — which is what makes Electron viable here at all.

🔵 Kept from the Go agent regardless:
- **Bundle the `Microsoft.Windows.Console.ConPTY` redistributable** (`conpty.dll` + `OpenConsole.exe`) and set `useConptyDll: true` — build 22631's in-box ConPTY predates fixes for `ClosePseudoConsole` hangs, lingering `conhost`, DCS passthrough, and reflow damage. Cost ~12 MB; `OpenConsole.exe` is on the LOLBAS list, so the installer needs an AV-allowlist note.
- **Job Objects** + `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` so closing a tab cannot orphan a dev server on port 3000.
- **Environment correctness:** build the child env from `CreateEnvironmentBlock` + a fresh `HKCU\Environment` read so `npm install -g` and `pip install --user` land where expected. **Assert at startup that the daemon is not LocalSystem** — Session 0 isolation silently redirects `APPDATA` and breaks every global install.

### 4.2 Rendering 🔵→⚪

`@xterm/xterm` 6.0.0 + `@xterm/addon-fit` + `@xterm/addon-webgl`, with a **pre-flight GPU probe before `term.open()`** and a **DOM fallback**. xterm 6 removed the canvas addon, so DOM is the *only* fallback rung. **Benchmark the DOM path in week 1** — do not assume GPU.

On 2 cores:
- Scrollback capped at **8,000 lines** (~16 MB), not 100k (~195 MB).
- **No Web Worker.** The fleet's off-thread decimation would contend with Electron's own processes. Revisit only with a benchmark.
- Bundle **JetBrains Mono woff2** — 🔵 not installed on this machine, and the ligature chain (`font-finder`, `font-ligatures`) is unmaintained since 2022. **Ligatures off in Phase 1.**
- 🔵 xterm 6.0.0 is a **breaking** release (canvas addon gone, `windowsMode` and `fastScrollModifier` removed, viewport rewritten) and most online guidance still targets v5. **Pin exact versions, never caret ranges.**

### 4.3 Process & window model ⚪

```
┌──────────────────────────────────────────────────────────┐
│ Electron MAIN process (Node)                             │
│  • reads %LOCALAPPDATA%\Zoey\runtime.json (port, token)  │
│  • owns the ONLY WebSocket client → 127.0.0.1:47600      │
│  • sets Origin: zoey://console   (a renderer cannot)     │
│  • window manager: spawnAt(path) → new BrowserWindow     │
│  • asks the daemon for a SPAWN GRANT before any PTY      │
└──────▲──────────────────────────────┬────────────────────┘
       │ contextBridge IPC            │ spawns
       │ ctxIsolation ON              │
       │ nodeIntegration OFF          │
       │ sandbox ON                   │
┌──────┴───────────────────┐   ┌──────▼────────────────────┐
│ RENDERER (React)         │   │ utilityProcess: PTY host  │
│  • xterm · tree · tabs   │◄──┤  @lydell/node-pty         │
│  • never sees the token  │ MessagePort (raw bytes)       │
│  • never opens a socket  │   │  one per window           │
└──────────────────────────┘   └───────────────────────────┘
                │ WS: grant requests, lifecycle reports, fs, permissions
┌───────────────▼──────────────────────────────────────────┐
│ Zoey Core (Python) — WS server, permission guard,        │
│ audit log, fs enumeration.  Also serves apps\orb.        │
│ Authorizes and audits PTYs. NEVER carries their bytes.   │
└──────────────────────────────────────────────────────────┘
```

**Why the WS client lives in main, not the renderer:** a renderer is a browser context — it cannot set an arbitrary `Origin`, and holding the token there puts it one XSS away from any rendered content. Main-only makes CONTRACT §2.2's Origin allowlist actually enforceable.

**Why the PTY byte stream does not go through the daemon** ⚪ — *corrected during Phase 0.* An earlier draft of the contract routed `evt.pty.data` (base64 PTY bytes) through Python. A noisy `npm install` emits megabytes; base64 inflates that ~33%, plus JSON escaping, on a 2-core CPU — the hottest path in the app running through the process with the least reason to see it. Corrected per CONTRACT §4.2/§6.5: **the daemon authorizes, audits, and can revoke; the Console owns the bytes.** The guard stays authoritative because no session may exist without a grant.

**Why a `utilityProcess` rather than the main process** 🔵 — a native-module crash in `node-pty` takes down whatever process hosts it. In main, that kills *every* window. In a per-window `utilityProcess`, it kills one tab's backend and the app survives.

**Click a folder → new console window:**
1. Renderer: tree click → `window.zoey.spawnAt(path, mode)` over contextBridge.
2. Main: validate `path` is a real directory; reject reparse-point traversal outside allowed roots.
3. Main → daemon: `cmd.pty.requestSpawn { profileId, cwd: path, actor: 'human' }`.
4. Daemon applies tier + protected-path policy, writes an audit entry, returns `res.pty.grant`. If the path is protected, the owner sees an approval card first.
5. Main spawns the `utilityProcess` PTY host, creates the `BrowserWindow`, hands the renderer a `MessagePort`.
6. Bytes flow PTY → utilityProcess → MessagePort → xterm. Main reports `started` / `exited` back to the daemon for the audit log.

Modifiers: **plain click** = default (configurable), **Shift-click** = new window, **Ctrl-click** = new tab.

### 4.4 How "everything CMD / PowerShell / WSL can do" is honestly satisfied 🔵

**We host the real binaries under ConPTY. We reimplement nothing.** No built-in `cd`, no emulated `dir`/`ls`. Every feature of `cmd.exe`, PowerShell 5.1, `bash`, and each WSL distro is inherited **because it is genuinely that program running**. Reimplementing even PowerShell's parser would be years of work for a strictly worse result.

Profile discovery, 🟢 verified on this machine:
- **WSL distros** from `HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Lxss` (confirmed: `Ubuntu-22.04`, `docker-desktop`); fallback `wsl.exe -l -q` **with `WSL_UTF8=1` injected** — without it the output is UTF-16 and parses as mojibake (observed in the very first probe of this project).
- **Git Bash** from `HKLM\SOFTWARE\GitForWindows`.
- **Docker** via `\\.\pipe\dockerDesktopLinuxEngine` — 🟢 currently absent because Docker Desktop isn't running, so the provider **degrades to "engine offline", never throws**.
- Profiles live in `profiles.jsonc` (comments allowed), validated with `zod`, carrying `schemaVersion` from day one.

Still to be built, because hosting doesn't give it: tabs/panes, file tree, block semantics (Phase 2), chat (Phase 3), the security guard, cross-shell history.

---

## 5. Security

See `CONTRACT.md` §6 for the invariants binding **both** surfaces. Console-specific implementation below.

### 5.1 Phase 1 non-negotiables — cannot be retrofitted

1. **WS auth** — loopback bind + Origin allowlist + per-launch token + 3 s handshake deadline (CONTRACT §2). Without this any webpage commands the agent.
2. **Electron hardening** — `contextIsolation: true`, `nodeIntegration: false`, `sandbox: true`, no remote module, strict CSP. Token stays in main.
3. **Audit log from commit one** — append-only, hash-chained, every command tagged `actor ∈ {user, agent, schedule}`. **Retrofitting provenance is impossible — the information is gone by then.**
4. **Protected paths** — `C:\dev\zoey`, `C:\Users\<user>\OneDrive`, and system directories, marked in `permissions.yaml`. Writes/deletes there confirm regardless of origin.
5. **Secret redaction before any write** — history, logs, session records. A secret written unredacted once is leaked permanently.

### 5.2 Honest residual risk ⚪

This tool's purpose is executing arbitrary commands. **Anything the owner types runs.** What is actually protected: the AI cannot act unreviewed; secrets don't reach logs or model context; destructive operations on protected paths require confirmation; everything is auditable.

**It is not a sandbox and must never be described as one.** True isolation means running commands inside WSL2/Docker by default, which breaks normal Windows dev work. Available as an opt-in tier later — not the default.

---

## 6. Filesystem behaviour

### 6.1 Metadata-only indexing

Per CONTRACT §6.3. Name, size, mtime, attributes, reparse tag. **Never open a file to read content during indexing. Never read content from a reparse point.**

🔵 **Hydration Firewall** (adopted from the fleet — genuinely good): compute bytes-to-download as `EndOfFile − AllocationSize` from attributes alone. Show per row, aggregate per folder — *"expanding this costs 3.4 GB"*. `fs.hydrate` is an **amber** rule in `permissions.yaml`, so a `grep -r`, a build, or Claude Code reading files is intercepted with an exact figure before it spends metered data. Surfaced as `evt.fs.hydrationWarning`.

### 6.2 Known limitation, surfaced in the UI 🔵

**Live file-watching inside WSL2 is impossible from Windows** — `ReadDirectoryChangesW` returns `ERROR_INVALID_FUNCTION` over 9P (microsoft/WSL#7674). WSL paths get manual refresh, and **the UI says so** rather than pretending. Requirement parity with WSL cannot cover this without an in-distro agent.

---

## 7. Deferred design — so Phase 1 doesn't block it

### 7.1 Shell integration for blocks — Phase 2 🟢

OSC 133 marks: `A` prompt start · `B` command start · `C` output start · `D;<exit>` command end. Verified traps:

| Shell | Works? | Catch |
|---|---|---|
| **PowerShell 5.1** | Yes, with exit codes | **Microsoft's official sample uses `` `e ``, which does not exist before PowerShell 6.** This machine has 5.1 only. Must use `$([char]27)` or the prompt emits literal garbage. |
| **cmd.exe** | Partially | Microsoft states plainly cmd **cannot read the previous exit code in the prompt** — boundaries yes, pass/fail colouring no. Accept it. |
| **bash (WSL)** | Fully | Needs `PS0` (bash ≥ 4.4; Ubuntu 22.04 ships 5.1). |
| **No integration** | Degraded | Heuristic segmentation; no exit codes. Must stay fully usable. |

The Console injects these into a **transient profile at launch — it never edits the user's real `$PROFILE`.**

### 7.2 Windows Explorer context menu — Phase 5 🟢

Ship a **legacy registry verb** only (`HKCU\Software\Classes\Directory\shell\...` + `Directory\Background\shell\...`). Windows 11 demotes those to "Show more options"; the primary menu requires `IExplorerCommand` under app identity via a **Sparse MSIX package + a C++ COM DLL — i.e. MSVC, which isn't installed.** Cheap to ship the legacy verb; the real requirement is satisfied inside the app anyway.

### 7.3 Later phases

Metadata search index (SQLite), command palette, history with redaction, chat pane, NL→command with dry-run, agentic task engine with checkpointing, Console-as-MCP-server for Claude Code.

---

## 8. Roadmap ⚪

One part-time developer, 2-core laptop, intermittent power. 🔵 The fleet's per-domain estimates summed to well over a year for the full vision; this is the deliberately reduced path.

| Phase | Deliverable | Exit criterion | Est. |
|---|---|---|---|
| **0** ✅ | Monorepo, `CONTRACT.md`, `packages/protocol` + `packages/tokens` generating, daemon skeleton with **auth working** | ✅ **MET** — 22/22 auth tests pass, 24/24 protocol tests pass, audit chain verifies, ACL confirmed user-only | done |
| **1a-0** ✅ | PTY smoke test in plain Node | ✅ **MET** — binary loads 14 ms, `napi=10`, spawn+resize+echo+exit clean on 22631 | done |
| **1a-1** ✅ | Electron shell, one hardened window | ✅ **MET** — cold start 425–576 ms, main RSS ~74–108 MB, `nodeAccess=none`, `contextBridge=ok` | done |
| **1a-2** ✅ | PTY in a `utilityProcess` + MessagePort | ✅ **MET** — `worker_threads` probe PASSES in stock Electron 43 (39–86 ms), so rung 1 holds; echo round-trips; teardown frees the tree verified by PID | done |
| **1a-3** ⚠️ | xterm + GPU probe | **PARTIAL** — rung selected and addon attached (`webgl`, ANGLE/D3D11); **keystroke→glyph NOT measured**, harness aborts, see `latency.ts` header. Target restated to **33.3 ms p95** (16 ms is unreachable at 60 Hz). | — |
| **1b** | Tabs + shell profiles (cmd, PS 5.1, Git Bash, both WSL distros) | Every profile launches; Ctrl+C interrupts; closing a tab kills the process tree | 1.5 wk |
| **1c** | Lazy file tree + click-folder-to-spawn | Tree paints <200 ms at OneDrive root; click opens console there; hydration cost shown | 2 wk |
| **1d** | Security hardening + audit log + protected paths | Hash-chain verifies; protected-path write prompts; secrets redacted | 1 wk |
| — | **Phase 1 done — usable daily driver** | | **~6–7 wk** |
| **2** | Blocks (OSC 133), history + redaction, command palette | Blocks segment in PS 5.1 and bash; cmd degrades gracefully | 3–4 wk |
| **3** | Chat pane, NL→command with explain/dry-run, Console as MCP server | AI cannot execute unreviewed; provenance visible | 4–5 wk |
| **4** | Agentic task engine, checkpointing, budget caps | A task survives a power cut and resumes | 3–4 wk |
| **5** | Packaging, installer, context-menu verb, auto-update | Clean install on a fresh profile | 2 wk |

---

## 9. Cut list ⚪

Rejected deliberately. Do not re-litigate.

| Cut | Why |
|---|---|
| Tauri v2 | Rust + MSVC ≈ 3–7 GB on metered data against 14.5 GB free. |
| Custom Go ConPTY host | Third language; hand-rolled syscalls, no compiler validation; the premise for rejecting node-pty was factually wrong (§4.1). |
| A custom Zoey shell language | 🔵 Fleet proposed it. Cut. The need is to run projects, not learn a new language. |
| Reimplementing CMD/PowerShell/WSL builtins | Infinite scope, strictly worse result. Host the real binaries. |
| Primary-menu Win11 shell extension | Needs MSVC + Sparse MSIX. Legacy verb instead. |
| Web Worker stream decimation (Phase 1) | 2 physical cores. Benchmark before adding contention. |
| Ligatures (Phase 1) | Dependency chain unmaintained since 2022. |
| USN journal reader / Time-Scrubber | Needs an elevated Windows Service + NTFS; degrades silently in the real configuration. |
| Sixel / inline images (Phase 1) | Gated on ConPTY DCS passthrough that 22631 lacks. |
| Four-panel layout at launch | 888px of chrome on a 1366px display leaves 478px of terminal. |
| Content indexing of OneDrive | Metadata only. CONTRACT §6.3. |

---

## 10. Verification

**Phase 0 — contract & auth boundary**
- Bad token → `4401`. Disallowed Origin (`http://evil.com`) → `4401` + audit entry. No `cmd.hello` in 3 s → `4408`. Test all three with a raw `ws` script.
- `icacls %LOCALAPPDATA%\Zoey\runtime.json` shows only the owner SID and SYSTEM.
- **Browser drive-by test:** a page running `new WebSocket('ws://127.0.0.1:47600/v1')` in Chrome must be rejected on Origin and logged.
- Unknown-type test: `cmd.nonexistent.thing` → `err.protocol.unknownType`, **connection stays open**. An unknown field in a known payload is ignored.

**Phase 1 — it actually works**
- `npm install` in a scratch dir; **`npm install -g <pkg>` lands in `C:\Users\<user>\AppData\Roaming\npm\node_modules`**; `pip install --user` succeeds.
- `claude` runs interactively in a tab and renders correctly.
- Ctrl+C interrupts `ping -t`. Closing a tab running a dev server frees port 3000 (`netstat -ano | findstr :3000` empty).
- Every profile launches: cmd, PowerShell 5.1, Git Bash, `Ubuntu-22.04`, `docker-desktop`.
- Tree paints <200 ms at OneDrive root; expanding `Udemy Course Folders` stays responsive; `node_modules` collapsed by default; hydration cost shown for a cloud folder.
- Click a folder → new window with that cwd; `cd` confirms.
- **Renderer benchmark on this hardware:** GPU probe result recorded; if WebGL2 is unavailable, DOM sustains a 50k-line `dir /s` without freezing the UI.
- Power-cut test: kill the daemon mid-`npm install`; relaunch; session state and audit chain intact.

**Continuous**
- `packages/protocol` schema validation over every fixture message in CI.
- Lint fails the build on any hard-coded hex colour in `apps/console` — tokens only.
