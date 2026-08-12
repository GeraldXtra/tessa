# ZOEY_OS — Canonical Repository Structure
### v2.1 · Phase 0 complete and verified

> Companion to `docs/ZOEY_OS-spec.md` (v3.1). Replaces spec §3.
> **CONTRACT.md outranks both.**
> Repo root: `C:\dev\zoey`

---

## Phase 0 status ✅

| Check | Result |
|---|---|
| Auth tests | **22/22 pass** |
| Protocol tests | **24/24 pass** |
| Audit chain | Verifies; tamper test names the exact altered entry (`seq 10: content altered`) |
| Token file ACL | `icacls` returns `NT AUTHORITY\SYSTEM:(F)` and `GERALD\SERIOUS-PC:(F)` only — no Users, no Everyone |
| Redaction | Confirmed against `sk-ant-…`, Bearer JWTs, AWS secrets, `postgres://user:pass@…` |
| Guard | Human gets a shell in `C:\dev`; agent does not — needs approval, and again inside OneDrive |
| Port walk | Verified — a stray daemon held 47600, the walk correctly landed on 47601 |
| Repo size | ~151 KB, no dependencies installed yet |

---

## Status legend

| Mark | Meaning |
|---|---|
| ✅ | Built and verified on disk |
| ⬜ | Not yet created |
| **P0–P8** | Phase it appears in |
| 🔒 | Shared — a surface session proposes, Gerald approves |
| ⚙️ | Generated output, never hand-edited |
| 🚫 | Gitignored |

**Ownership, revised after Phase 0:**
The **Console session owns `core/`** — it has built the daemon foundation, and one owner beats two. It also owns `apps/console`.
The **Orb session owns `apps/orb` only** and is a pure consumer: it reads `CONTRACT.md` and `packages/*`, and never edits `core/`, `packages/`, or `apps/console`.
Neither edits `CONTRACT.md`.

---

## The tree

```
C:\dev\zoey\
│
├── CONTRACT.md                       ✅ P0 🔒  403 lines. AWAITING APPROVAL.
├── plan.md                           ✅ P0     Console build plan, Phase 0 marked done
├── README.md                         ✅ P0     ⚠️ status blurb stale — edit was
│                                                blocked by a classifier outage
├── .gitignore                        ✅ P0     excludes runtime.json — holds the token
├── package.json                      ✅ P0     npm workspaces root
├── CLAUDE.md                         ⬜ P0     ⚠️ STILL MISSING
├── .gitattributes                    ⬜ P0     ⚠️ CRLF/LF — see below
├── .editorconfig                     ⬜ P0
├── .env.example                      ⬜ P0     names only, never values
├── pyproject.toml                    ⬜ P0     ⚠️ GAP #1 — see below
├── requirements.lock                 ⬜ P0
│
├── packages\
│   ├── protocol\                     🔒
│   │   ├── package.json              ✅ P0
│   │   ├── tsconfig.json             ✅ P0
│   │   ├── schema\
│   │   │   ├── envelope.schema.json  ✅ P0
│   │   │   ├── events.schema.json    ⬜ P0
│   │   │   └── commands.schema.json  ⬜ P0
│   │   ├── src\index.ts              ✅ P0     526 lines: closed enums, envelope,
│   │   │                                       ULID, deep-link parser
│   │   ├── gen\python\               ⬜ ⚙️ 🚫  ⚠️ GAP #2 — core/ hand-maintains
│   │   │                                       a second copy of the contract
│   │   └── test\smoke.ts             ✅ P0     24 passing, zero deps
│   │
│   └── tokens\                       🔒
│       ├── package.json              ✅ P0
│       ├── tokens.json               ✅ P0     47 tokens, source of truth
│       ├── build.mjs                 ✅ P0     explicit name map, not derived
│       └── dist\                     ✅ ⚙️ 🚫  tokens.css + tokens.py
│
├── core\                             🔒        ← CONSOLE SESSION OWNS
│   ├── server.py                     ✅ P0     583 lines. Loopback bind, Origin
│   │                                            allowlist, timing-safe token
│   │                                            compare, 3s deadline, port walk
│   ├── test_auth.py                  ✅ P0     22 tests — move to core/tests/
│   ├── security\
│   │   ├── audit.py                  ✅ P0     hash-chained, append-only,
│   │   │                                       redaction before write
│   │   ├── runtime.py                ✅ P0     empty file → lock ACL → verify →
│   │   │                                       then write secret (TOCTOU-safe)
│   │   ├── guard.py                  ✅ P0     ALLOW/CONFIRM/DENY. Protected paths
│   │   │                                       compared by path PARTS, not prefix
│   │   ├── secrets.py                ⬜ P1     Windows Credential Manager
│   │   └── sanitize.py               ⬜ P1     external-content delimiting
│   ├── config\
│   │   ├── permissions.yaml          ✅ P0     green / amber / red
│   │   ├── settings.yaml             ⬜ P1     quiet hours, budgets, devices
│   │   ├── companions.yaml           ⬜ P7
│   │   └── personalities\zoey.md     ⬜ P1
│   ├── state.py                      ⬜ P1     spec §5 machine, single owner
│   ├── bus.py                        ⬜ P2     audio arbitration (spec §5.2)
│   ├── service.py                    ⬜ P3     Windows service entry point
│   │
│   ├── db\                           ⬜ P1
│   │   ├── connection.py             ⬜ P1     SQLite WAL
│   │   ├── models.py                 ⬜ P1     spec §6 tables
│   │   └── migrations\001_init.sql   ⬜ P1     forward-only
│   │
│   ├── pty\                          ⬜ P1     grants + audit ONLY — never bytes
│   │   ├── grants.py                 ⬜ P1     requestSpawn → grant, expiry
│   │   ├── registry.py               ⬜ P1     roster from cmd.pty.report
│   │   ├── profiles.py               ⬜ P1     cmd / PS 5.1 / Git Bash / WSL
│   │   └── integration\
│   │       ├── powershell51.ps1      ⬜ P2     ⚠️ $([char]27), NOT `e
│   │       └── bash.sh               ⬜ P2
│   │
│   ├── fs\                           ⬜ P1
│   │   ├── enumerate.py              ⬜ P1     shallow + lazy expand
│   │   ├── watch.py                  ⬜ P1     ⚠️ impossible over WSL2 9P
│   │   └── hydration.py              ⬜ P1     size − alloc, never opens a file
│   │
│   ├── voice\                        ⬜ P2
│   │   ├── wake.py                   ⬜ P3     Porcupine
│   │   ├── vad.py                    ⬜ P2     Silero
│   │   ├── stt.py                    ⬜ P2     faster-whisper
│   │   ├── audio_io.py               ⬜ P2     devices, echo cancellation
│   │   ├── chime.py                  ⬜ P3     fires from the wake thread
│   │   └── tts\{base,piper,elevenlabs}.py  ⬜ P2
│   │
│   ├── brain\                        ⬜ P1
│   │   ├── agent.py                  ⬜ P1     tool-use loop
│   │   ├── router.py                 ⬜ P1     intent routing (spec §5.3)
│   │   ├── context.py                ⬜ P1     compaction
│   │   ├── personality.py            ⬜ P1
│   │   └── companions.py             ⬜ P7     orchestrator
│   │
│   ├── tools\                        ⬜ P1     one MCP server per domain
│   │   ├── registry.py               ⬜ P1     discovery + tier tagging
│   │   ├── fs_server.py              ⬜ P1
│   │   ├── system_server.py          ⬜ P1
│   │   ├── browser_server.py         ⬜ P2
│   │   ├── mail_server.py            ⬜ P5
│   │   ├── dev_server.py             ⬜ P5
│   │   └── knowledge_server.py       ⬜ P6
│   │
│   ├── memory\{store,vectors,indexer,graph}.py                 ⬜ P6
│   ├── autonomy\{scheduler,queue,triggers,approvals,digest}.py ⬜ P5
│   ├── telemetry\{logging,metrics,cost}.py                     ⬜ P1
│   └── tests\                        ⬜ P1     move test_auth.py here
│       ├── unit\ · integration\
│       └── fixtures\{audio,llm}\
│
├── apps\
│   ├── console\                      ⬜ P1a    ← NEXT UP. Needs npm install (~250 MB)
│   │   ├── package.json              ⬜ P1
│   │   ├── electron.vite.config.ts   ⬜ P1
│   │   ├── src\main\
│   │   │   ├── index.ts              ⬜ P1
│   │   │   ├── ws-client.ts          ⬜ P1     the ONLY socket. Sets Origin.
│   │   │   ├── token.ts              ⬜ P1     reads runtime.json — port + token
│   │   │   ├── pty-host.ts           ⬜ P1     spawns the utilityProcess
│   │   │   ├── deeplink.ts           ⬜ P1     parseDeepLink, empty prompt
│   │   │   └── windows.ts            ⬜ P1
│   │   ├── src\pty-host\index.ts     ⬜ P1     utilityProcess: @lydell/node-pty
│   │   │                                       → MessagePort → renderer
│   │   ├── src\preload\index.ts      ⬜ P1     contextBridge only
│   │   ├── src\renderer\
│   │   │   ├── terminal\             ⬜ P1     xterm + fit + webgl, GPU probe
│   │   │   ├── tree\                 ⬜ P1     lazy, virtualized
│   │   │   ├── tabs\                 ⬜ P1
│   │   │   ├── blocks\               ⬜ P2
│   │   │   ├── chat\                 ⬜ P3
│   │   │   └── styles\               ⬜ P1     imports packages/tokens
│   │   ├── resources\fonts\          ⬜ P1     JetBrains Mono woff2 — NOT installed
│   │   └── tests\                    ⬜ P1
│   │
│   └── orb\                          ⬜ P4     ← ORB SESSION OWNS. NEVER EDIT.
│       ├── package.json              ⬜ P4
│       ├── src\main\
│       │   ├── index.ts              ⬜ P4
│       │   ├── ws-client.ts          ⬜ P4     same protocol package
│       │   ├── tray.ts               ⬜ P4
│       │   └── hotkey.ts             ⬜ P4     global summon
│       ├── src\preload\index.ts      ⬜ P4
│       └── src\renderer\
│           ├── scene\
│           │   ├── Sphere.tsx        ⬜ P4     Three.js Points
│           │   ├── shaders\          ⬜ P4     vert + frag GLSL
│           │   └── states.ts         ⬜ P4     agent state → visual params
│           ├── panels\
│           │   ├── StatusBar · Rail · Calendar            ⬜ P4
│           │   ├── JobList · Transcript · Approval        ⬜ P4
│           │   └── Switcher                               ⬜ P7
│           ├── layout\               ⬜ P4     ⚠️ collapsed layout FIRST — spec §8.1
│           ├── knowledge\            ⬜ P6
│           └── styles\               ⬜ P4     imports packages/tokens
│
├── data\                             ⬜ 🚫     ⚠️ GAP #3 — Phase 1 blocker
│   ├── zoey.db                       ⬜ P1     encrypted at rest
│   ├── vectors\                      ⬜ P6
│   ├── models\                       ⬜ P2     whisper + piper + porcupine
│   ├── logs\                         ⬜ P1
│   └── audio\                        ⬜ P2     debug only, auto-purged
│
├── scripts\
│   ├── check-contract.mjs            ⬜ P0     ⚠️ GAP #4 — see below
│   ├── bootstrap.ps1                 ⬜ P0
│   ├── fetch-models.ps1              ⬜ P2
│   └── install-service.ps1           ⬜ P3
│
├── docs\
│   ├── ZOEY_OS-spec.md               ⬜ P0     ⚠️ README links it. Link is DEAD.
│   │                                            Gerald places v3.1 — session must
│   │                                            NOT create its own copy.
│   ├── STRUCTURE.md                  ⬜ P0     this file — Gerald places it
│   └── decisions\
│       ├── 001-electron-over-tauri.md      ⬜
│       ├── 002-lydell-node-pty.md          ⬜
│       ├── 003-pty-bytes-bypass-daemon.md  ⬜  the biggest architectural call so far
│       └── 004-origin-rejections-not-counted.md ⬜  the DoS fix and why
│
└── .github\workflows\ci.yml          ⬜ P1
```

`%LOCALAPPDATA%\Zoey\runtime.json` lives outside the repo by design and holds the per-launch token and the actual bound port.

---

## Remaining gaps, in priority order

| # | Gap | Why it matters now |
|---|---|---|
| **1** | **`pyproject.toml` / `requirements.lock`** | The daemon imports `websockets` from global Python with nothing pinned. It runs today on one machine. Reinstall Python, or move laptop, and it dies with no record of what it needed. `core/` is now ~1,200 lines — this only gets worse. |
| **2** | **`packages/protocol/gen/python/`** | `core/` hand-maintains a second Python copy of the contract. Two hand-written copies of one contract **will** drift, and generation is the entire reason the JSON Schema exists. Closing this before Phase 1 code piles on is far cheaper than after. |
| **3** | **`data/`** | Nowhere for the DB, models, or logs. Phase 1 blocker. |
| **4** | **`scripts/check-contract.mjs`** | Nothing detects stale generated output. See below. |
| **5** | **`CLAUDE.md`** | Every session re-derives the ownership rules from scratch without it. |
| **6** | **`.gitattributes`** | PowerShell needs CRLF, bash needs LF. Git will rewrite them and silently break the OSC 133 snippets in Phase 2. |
| **7** | **`docs/ZOEY_OS-spec.md`** | README links it; the link is dead. The Orb session cannot start without it. **Gerald places this — the session must not create a competing copy.** |
| **8** | **README status blurb** | Stale — the edit was blocked by a classifier outage mid-run. |

---

## The stale-generation guard

`packages/tokens/dist/` and `packages/protocol/gen/` are outputs, correctly gitignored. Nothing yet verifies they're current.

Failure mode: someone edits `tokens.json`, forgets `npm run tokens`, commits. The Orb renders last week's colours for days. Same for the protocol — worse, because a drifted enum is a runtime bug, not a visual one.

`scripts/check-contract.mjs` regenerates both into a temp directory and **fails the build if either differs**. Three lines of CI that prevent the single most likely cross-session failure.

---

## `.gitattributes`

```gitattributes
* text=auto eol=lf
*.ps1   text eol=crlf
*.cmd   text eol=crlf
*.bat   text eol=crlf
*.sh    text eol=lf
*.png binary
*.wav binary
*.onnx binary
*.woff2 binary
```

---

## Open layout decisions

1. **Package manager** — npm workspaces is in place and works. pnpm would save real disk with two Electron apps sharing dependencies, which matters at 14.5 GB free. **Decide before `npm install` runs for `apps/console`** — switching after means re-downloading everything on metered data.
2. **`core/test_auth.py`** — currently at the `core/` root. Move to `core/tests/` before more tests land.
3. **Build output** — `out/` vs `release/` for packaged installers. Pick one, gitignore it.
4. **Python types** — generate from `packages/protocol/schema/` (gap 2), or formally accept a hand-maintained mirror and add a test that asserts the two match.
