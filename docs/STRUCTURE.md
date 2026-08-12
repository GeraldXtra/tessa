# ZOEY_OS — Canonical Repository Structure
### v2.2 · Phase 0 complete · generated-output ruling corrected

> Companion to `docs/ZOEY_OS-spec.md` (v3.2). Replaces spec §3.
> **CONTRACT.md outranks both.**
> Repo root: `C:\dev\zoey`

---

## Phase 0 status ✅

| Check | Result |
|---|---|
| Protocol tests | **28/28** — envelope, closed enums, deep-link safety, forward compatibility |
| Core sync tests | **21/21** — `core/` cannot drift from the generated contract |
| Auth tests | **22/22** — live boundary against a running daemon |
| Freshness check | **5 checks** — build fails if any generated output is stale |
| **Total** | **71 passing, 0 failing** |
| Audit chain | Verifies; tamper test names the exact altered entry |
| Token file ACL | `SYSTEM` + owner only — no Users, no Everyone |
| Redaction | Confirmed against `sk-ant-…`, Bearer JWTs, AWS secrets, `postgres://user:pass@…` |
| Guard | Human gets a shell in `C:\dev`; agent does not — needs approval, again inside OneDrive |
| Port walk | A stray daemon held 47600; the walk correctly landed on 47601 |

---

## ⚠️ Correction to v2.1

**v2.1 marked `packages/protocol/gen/` as gitignored. That was wrong.** Both generated protocol outputs are **committed**:

- `packages/protocol/gen/python/` — imported by `core/` at daemon startup
- `packages/protocol/src/enums.generated.ts` — re-exported by `index.ts` for both surfaces

Ignoring them would make "clone the repo and run the daemon" depend on running a Node build step first — a hard cross-language coupling for no benefit. The staleness risk that ignoring was meant to solve is covered by `scripts/check-contract.mjs`, which fails the build on any drift from source.

`packages/tokens/dist/` **remains gitignored** — nothing imports it at runtime before a build.

---

## Status legend

| Mark | Meaning |
|---|---|
| ✅ | Built and verified |
| ⬜ | Not yet created |
| **P0–P8** | Phase it appears in |
| 🔒 | Shared — propose to Gerald, never edit |
| ⚙️ | Generated output, never hand-edited |
| 🚫 | Gitignored |
| 📦 | Generated **and committed** |

**Ownership:**
**Console session** owns `core/` and `apps/console`.
**Orb session** owns `apps/orb` only — pure consumer, reads `CONTRACT.md` and `packages/*`, edits neither.
Neither edits `CONTRACT.md`, `packages/`, or the other's app.
**Gerald owns all git operations.** No session runs git commands.

---

## The tree

```
C:\dev\zoey\
│
├── CONTRACT.md                       ✅ P0 🔒  AWAITING APPROVAL
├── CLAUDE.md                         ✅ P0     101 lines, standing instructions
├── COPYRIGHT.md                      ✅ P0     proprietary, all rights reserved
├── plan.md                           ✅ P0     Console build plan
├── README.md                         ✅ P0     ⚠️ "## License" section MISSING
├── .gitignore                        ✅ P0
├── .gitattributes                    ✅ P0
├── .editorconfig                     ✅ P0
├── .env.example                      ✅ P0     names only, never values
├── package.json                      ✅ P0     npm workspaces (npm, not pnpm — spec §2.2)
├── pyproject.toml                    ✅ P0     pinned
├── requirements.lock                 ✅ P0     real sha256, --require-hashes works
│
├── scripts\
│   ├── check-contract.mjs            ✅ P0     fails build on stale generated output
│   ├── bootstrap.ps1                 ⬜ P0
│   ├── fetch-models.ps1              ⬜ P2
│   └── install-service.ps1           ⬜ P3
│
├── packages\
│   ├── protocol\                     🔒
│   │   ├── package.json              ✅ P0
│   │   ├── tsconfig.json             ✅ P0     allowImportingTsExtensions + noEmit
│   │   ├── build-enums.mjs           ✅ P0     enums.json → TS + Python
│   │   ├── schema\
│   │   │   ├── enums.json            ✅ P0 🔒  ★ SINGLE SOURCE OF TRUTH
│   │   │   │                                   15 enums, 13 closed, 77 values
│   │   │   ├── envelope.schema.json  ✅ P0
│   │   │   ├── events.schema.json    ⬜ P0
│   │   │   └── commands.schema.json  ⬜ P0
│   │   ├── src\
│   │   │   ├── index.ts              ✅ P0     re-exports the generated enums
│   │   │   └── enums.generated.ts    ✅ 📦     committed — index.ts imports it
│   │   ├── gen\python\zoey_protocol\
│   │   │   └── enums.py              ✅ 📦     committed — core/ imports it
│   │   └── test\smoke.ts             ✅ P0     28 passing, zero deps
│   │
│   └── tokens\                       🔒
│       ├── package.json              ✅ P0
│       ├── tokens.json               ✅ P0     47 tokens, source of truth
│       ├── build.mjs                 ✅ P0     explicit name map, not derived
│       └── dist\                     ✅ ⚙️ 🚫  tokens.css + tokens.py
│
├── core\                             🔒        ← CONSOLE SESSION OWNS
│   ├── server.py                     ✅ P0     WS daemon, Origin allowlist,
│   │                                            timing-safe compare, port walk
│   ├── test_auth.py                  ✅ P0     22 tests — should move to tests/
│   ├── tests\
│   │   ├── test_contract_sync.py     ✅ P0     21 tests — anti-drift guard
│   │   ├── unit\ · integration\      ⬜ P1
│   │   └── fixtures\{audio,llm}\     ⬜ P1/P2
│   ├── security\
│   │   ├── audit.py                  ✅ P0     hash-chained, redaction before write
│   │   ├── runtime.py                ✅ P0     empty file → lock ACL → verify →
│   │   │                                       then write secret (TOCTOU-safe)
│   │   ├── guard.py                  ✅ P0     ALLOW/CONFIRM/DENY, path-parts compare
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
│   └── telemetry\{logging,metrics,cost}.py                     ⬜ P1
│
├── apps\
│   ├── console\                      ⬜ P1a    ← NEXT UP. npm install (~250 MB)
│   │   ├── package.json              ⬜ P1
│   │   ├── electron.vite.config.ts   ⬜ P1
│   │   ├── src\main\
│   │   │   ├── index.ts              ⬜ P1
│   │   │   ├── ws-client.ts          ⬜ P1     the ONLY socket. Sets Origin.
│   │   │   ├── token.ts              ⬜ P1     reads runtime.json — port + token
│   │   │   ├── pty-host.ts           ⬜ P1     spawns the utilityProcess
│   │   │   ├── deeplink.ts           ⬜ P1     DeepLinkMode only, empty prompt
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
│           │   └── states.ts         ⬜ P4     SIX agent states → visual params
│           ├── panels\
│           │   ├── StatusBar · Rail · Calendar            ⬜ P4
│           │   ├── JobList · Transcript · Approval        ⬜ P4
│           │   └── Switcher                               ⬜ P7
│           ├── layout\               ⬜ P4     ⚠️ collapsed layout FIRST — spec §8.1
│           ├── knowledge\            ⬜ P6
│           └── styles\               ⬜ P4     imports packages/tokens
│
├── data\                             ✅ 🚫     created
│   ├── zoey.db                       ⬜ P1     encrypted at rest
│   ├── vectors\                      ⬜ P6
│   ├── models\                       ⬜ P2     whisper + piper + porcupine
│   ├── logs\                         ⬜ P1
│   └── audio\                        ⬜ P2     debug only, auto-purged
│
├── docs\
│   ├── ZOEY_OS-spec.md               ✅ P0     v3.2 — Gerald owns, sessions never edit
│   ├── STRUCTURE.md                  ✅ P0     this file — Gerald owns
│   └── decisions\                    ⬜
│       ├── 001-electron-over-tauri.md
│       ├── 002-lydell-node-pty.md
│       ├── 003-pty-bytes-bypass-daemon.md
│       ├── 004-origin-rejections-not-counted.md
│       ├── 005-enums-single-source.md
│       └── 006-npm-over-pnpm.md
│
└── .github\workflows\ci.yml          ⬜ P1     lint · typecheck · test · freshness
```

`%LOCALAPPDATA%\Zoey\runtime.json` lives outside the repo and holds the per-launch token and bound port.

---

## Remaining gaps

Down from eight to five.

| # | Gap | Why it matters |
|---|---|---|
| **1** | **README `## License` section** | Gerald's step didn't land. `COPYRIGHT.md` exists and is correct; the README pointer doesn't. |
| **2** | **`docs/decisions/`** | Six reversals have real reasoning behind them. Without records they get re-litigated by the next session. |
| **3** | **`.github/workflows/ci.yml`** | `check-contract.mjs` exists but nothing runs it automatically. |
| **4** | **`scripts/bootstrap.ps1`** | `npm run bootstrap` covers Node; nothing sets up the Python venv in one command. |
| **5** | **`core/test_auth.py` location** | Still at `core/` root; `core/tests/` now exists. |

---

## npm scripts

```
npm run generate         # tokens + enums → all generated output
npm run contract:check   # fail if any generated output is stale
npm run protocol:check   # tsc --noEmit on the protocol package
npm run protocol:test    # 28 protocol tests
npm run core:test        # 21 anti-drift tests
npm test                 # generate → contract:check → protocol:test → core:test
npm run bootstrap        # generate + full test run
```

Python deps: `pip install --require-hashes -r requirements.lock`

---

## Open layout decisions

1. **Build output** — `out/` vs `release/` for packaged installers. Pick one, gitignore it.
2. **`core/test_auth.py`** — move to `core/tests/` before more tests land.
3. **`events.schema.json` / `commands.schema.json`** — the envelope and enums are schema-backed; the event and command payloads are not yet. Worth closing in Phase 1 while the surface count is one.
