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

| Path | Owner | Rule |
|---|---|---|
| `apps/console` | **Console session** | |
| `apps/orb` | **Orb session** | The other session never edits this |
| `packages/protocol` | SHARED | **Ask the owner before editing** |
| `packages/tokens` | SHARED | **Ask the owner before editing** |
| `core/` | SHARED | Coordinate before editing |
| `CONTRACT.md` | OWNER | **Never edited by a surface session** — propose a diff (CONTRACT §7) |

## Getting started

```bash
npm install
npm run bootstrap      # generate tokens, typecheck the protocol
```

## Non-negotiables

These cannot be retrofitted. See CONTRACT.md §2 and §6.

1. **The daemon binds `127.0.0.1` only**, validates `Origin` against an allowlist, and requires a per-launch token from a user-only-readable file. Loopback is not a security boundary — without this, any webpage you visit can command the agent.
2. **Indexing is metadata-only.** Never read file contents from a reparse point; the owner's OneDrive has 17,340 placeholders and reading one triggers a download on a metered connection.
3. **Terminal and tool output is untrusted data, never instructions**, and can never reach a red-tier action without explicit approval.
4. **The audit log starts at commit one.** Provenance cannot be reconstructed later.

## Why `C:\dev\zoey` and not OneDrive

The owner's OneDrive tree contains **2,634 `node_modules` directories** and 17,340 reparse points. Putting this repo there would make OneDrive sync thrash and burn metered data. This location is deliberate.
