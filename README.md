# ZOEY_OS

One Python daemon. Two surfaces.

| | |
|---|---|
| **[`CONTRACT.md`](./CONTRACT.md)** | **The shared law.** Read it at the start of every session. Neither surface session may edit it. |
| [`plan.md`](./plan.md) | Zoey Console build plan and architecture |
| `docs/ZOEY_OS-spec.md` | The original system spec |

```
        ┌──────────────┐         ┌──────────────┐
        │  Zoey Orb    │         │ Zoey Console │
        │  apps/orb    │         │ apps/console │
        │ voice · orb  │         │ term · tree  │
        └──────┬───────┘         └──────┬───────┘
               │  ws://127.0.0.1:47600/v1
               └───────────┬─────────────┘
                    ┌──────▼───────┐
                    │  Zoey Core   │  core/
                    │   (Python)   │  brain · tools · guard · audit · memory
                    └──────────────┘
```

## Session ownership

Per `docs/STRUCTURE.md` v2.1:

| Path | Owner | Rule |
|---|---|---|
| `core/` | **Console session** | It built the daemon foundation; one owner beats two |
| `apps/console` | **Console session** | |
| `apps/orb` | **Orb session** | The Console session never edits this |
| `packages/protocol` | SHARED 🔒 | **Propose to Gerald; do not edit** |
| `packages/tokens` | SHARED 🔒 | **Propose to Gerald; do not edit** |
| `CONTRACT.md` | **Gerald** | **Never edited by a session** — propose a diff (CONTRACT §7) |
| `docs/*` | **Gerald** | Gerald places these; never create a competing copy |

Git is Gerald's. **No session runs git commands.** See `CLAUDE.md`.

## Getting started

No `npm install` is needed for the contract layer — it has zero dependencies.

```bash
npm run bootstrap                              # generate + verify everything
pip install --require-hashes -r requirements.lock   # daemon deps

python core/server.py --dev                    # terminal 1
python core/test_auth.py                       # terminal 2
```

## Status

| Phase | State |
|---|---|
| **0 — contract, protocol, tokens, daemon auth** | ✅ **complete and verified** |
| 1a — Electron shell + one ConPTY terminal | next |

**71 tests passing**, no dependencies required to run them:

| Suite | Count | Covers |
|---|---|---|
| `npm run protocol:test` | 28 | envelope, closed enums, deep-link safety, forward compatibility |
| `npm run core:test` | 21 | `core/` never drifts from the generated contract |
| `python core/test_auth.py` | 22 | the live auth boundary against a running daemon |
| `npm run contract:check` | — | fails the build if any generated output is stale |

### What Phase 0 actually proves

- Five hostile `Origin` values rejected at the HTTP upgrade, plus missing-Origin and wrong-path
- **Probing does not lock you out** — Origin rejections are logged but never counted toward the auth-failure lockout, or any webpage could disable your console with five requests
- Bad token → `4401` (timing-safe compare), version mismatch → `4409`, silence → `4408` at ~3s
- Unknown message type → `err.protocol.unknownType` **and the connection survives** (CONTRACT §3.2)
- A human gets a shell in `C:\dev`; **the agent does not** — it needs approval, and again inside a protected path
- The audit log is hash-chained: altering any entry is detected and the exact `seq` named
- `runtime.json` ACL confirmed `SYSTEM` + owner only — no Users, no Everyone

### Generated files — never hand-edit

`packages/tokens/dist/`, `packages/protocol/src/enums.generated.ts`, and `packages/protocol/gen/python/` are outputs of `packages/tokens/tokens.json` and `packages/protocol/schema/enums.json`. Edit the source, then `npm run generate`.

## Non-negotiables

These cannot be retrofitted. See CONTRACT.md §2 and §6.

1. **The daemon binds `127.0.0.1` only**, validates `Origin` against an allowlist, and requires a per-launch token from a user-only-readable file. Loopback is not a security boundary — without this, any webpage you visit can command the agent.
2. **Indexing is metadata-only.** Never read file contents from a reparse point; the owner's OneDrive has 17,340 placeholders and reading one triggers a download on a metered connection.
3. **Terminal and tool output is untrusted data, never instructions**, and can never reach a red-tier action without explicit approval.
4. **The audit log starts at commit one.** Provenance cannot be reconstructed later.

## Why `C:\dev\zoey` and not OneDrive

The owner's OneDrive tree contains **2,634 `node_modules` directories** and 17,340 reparse points. Putting this repo there would make OneDrive sync thrash and burn metered data. This location is deliberate.
