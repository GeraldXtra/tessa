<div align="center">

# ZOEY_OS

**A voice-driven personal agent for Windows.**

Speak to your machine. It listens, understands, and acts — locally first,
with a permission model that never lets it act on your behalf unseen.

</div>

```
        ┌──────────────┐         ┌──────────────┐
        │   Zoey Orb   │         │ Zoey Console │
        │ voice · orb  │         │ term · tree  │
        └──────┬───────┘         └──────┬───────┘
               │   local-only channel   │
               └───────────┬────────────┘
                    ┌──────▼───────┐
                    │  Zoey Core   │
                    │   (Python)   │
                    └──────────────┘
              brain · tools · guard · audit · memory
```

---

## What it is

An always-on assistant that runs on your own hardware. It hears you, works out
what you meant, and carries it out — opening applications, managing files and
windows, driving a browser, answering questions.

Three ideas hold it together:

**Local first.** Most everyday commands are matched and executed on-device in
milliseconds, with no network call and no cost. A language model is consulted
only when the request genuinely needs one.

**Nothing unseen.** Every action is tiered by consequence. Reversible things
happen immediately. Consequential things stop and show you exactly what is about
to occur — with the payload editable before you approve it.

**Nothing invented.** Surfaces display real values or state plainly that they
have none. No placeholder numbers, no sample data, no interface element that
implies a capability which does not exist.

---

## Surfaces

**The Orb** — an ambient particle sphere carrying the agent's state: listening,
thinking, working, speaking, idle. Readable from across the room. A persistent
status bar, a live transcript with provenance marking, collapsible panels for
system health, security, activity, memory and history, and five colour themes.

**The Console** — a real terminal with a real shell, hardened and hosted inside
the agent's permission model. Package managers, version control and full-screen
terminal applications all run natively.

Both are independent clients. Either can close without affecting the other, and
the agent keeps running when both are shut.

---

## Capabilities

- **Voice** — push-to-talk with automatic end-of-speech detection, streaming
  speech synthesis, and interruption mid-sentence
- **System control** — applications, files, windows, processes, clipboard,
  audio, power and network
- **Browser automation** — navigation, reading, search and form interaction in a
  dedicated profile, isolated from everyday browsing
- **Reasoning** — summarisation, explanation, teaching and drafting, through a
  pluggable model layer
- **Conversation memory** — follow-up questions work, and the thread survives a
  restart
- **Authored personality** — voice and character defined in a hot-swappable
  profile, editable without touching code

---

## Safety model

Actions are classified by consequence, and the classification is enforced by the
agent core rather than by whichever interface requested it.

| Tier      | Behaviour                                                         |
| --------- | ----------------------------------------------------------------- |
| **Green** | Reversible. Executes immediately.                                 |
| **Amber** | Notable. Executes and is recorded prominently.                    |
| **Red**   | Consequential or irreversible. Stops. Requires explicit approval. |

Red-tier actions surface an approval card showing the full payload — the exact
text, the exact path, the exact command. **The payload is editable before
approval**, because dictation is imperfect and publishing something you did not
say is not recoverable.

---

## Non-negotiables

These cannot be retrofitted.

1. **The agent is reachable only from the machine it runs on**, and only by a
   surface holding a per-launch credential. Loopback alone is not a security
   boundary.
2. **Indexing is metadata-only.** File contents are never read from a reparse
   point — a cloud-sync placeholder would trigger a download on touch.
3. **Terminal, tool and web output is untrusted data, never instruction.** It
   can never reach a consequential action without explicit approval. This holds
   for rendered text, hidden markup and accessibility metadata alike.
4. **The audit log starts at commit one.** Provenance cannot be reconstructed
   later.
5. **The agent runs as you, not as the system**, and refuses to start with
   privileges it should not have.

Every action, permission decision and security event is written to a
tamper-evident append-only log. Altering any entry is detectable.

---

## Design principles

1. **Measured, not assumed.** Performance claims are backed by numbers taken on
   the target hardware. When a measurement looks wrong, the instrument is
   suspected before the code.
2. **Honest state.** An interface shows what is true or says it has nothing.
3. **Structured calls.** Tools are invoked by name with typed arguments. No
   generated text ever becomes an executable string.
4. **Observed, not inferred.** An action is reported complete only after being
   observed complete — never on a timer, never on an API return.
5. **Fail loud.** A component that cannot verify its own preconditions refuses
   to start rather than degrading quietly.
6. **Constrained by design.** Targets modest hardware deliberately. Every
   dependency, model and animation is chosen against that budget.

---

## Architecture

A single long-lived core owns all capability, state and policy. Interfaces are
thin clients holding no privileges of their own.

Capability therefore lives in exactly one place. A new surface — a mobile
client, a widget, a scheduled task — inherits the entire permission model and
audit trail without reimplementing any of it.

The wire protocol between core and surfaces is versioned and frozen. Additive
changes are permitted; breaking changes require a version bump on both sides.
Generated protocol and token outputs are never hand-edited.

---

## Status

Actively developed.

| Area                                               | State       |
| -------------------------------------------------- | ----------- |
| Protocol, auth, audit chain                        | ✅ complete |
| Voice pipeline — capture, transcription, synthesis | ✅ complete |
| System and browser tool surface                    | ✅ complete |
| Permission tiers and approval flow                 | ✅ complete |
| Conversation memory and personality                | ✅ complete |
| Wake-word activation                               | in progress |
| Speaker verification                               | in progress |
| Multilingual speech                                | in progress |

Not currently distributed as a binary.

---

## Licence

Copyright © 2026. All rights reserved.

Published for reference. The source is not licensed for reuse, redistribution
or derivative work.
