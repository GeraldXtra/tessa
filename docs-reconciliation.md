# docs/ reconciliation — a proposal, not an edit

**Written by the Core session. `docs/` is Gerald's; nothing in it has been touched.**
This file is a diff-in-prose he can apply, reject, or ignore.

Scope: `docs/STRUCTURE.md` (v2.2), `docs/TESSA_CORE-spec.md` (v3.2), and the
CAPABILITIES P-tier column. Verified against the files actually on disk on
2026-08-15, not against memory of having built them.

---

## The one-line summary

> The docs describe a system at **Phase 0 complete, Phase 1a next**.
> What is on disk is most of **Phase 1 and Phase 2**, a working voice loop, a
> nine-module tool surface, and a Console with a live terminal — **minus the
> entire persistence layer**, which the docs assume exists from Phase 1.

Both halves of that matter. The roadmap **understates** how much is built and
**overstates** one specific thing that is not built at all.

---

## A word on "BUILT, NOT PROVEN"

Three labels are used below, and the distinction is the point of the document.

| Label | Means |
|---|---|
| **BUILT + PROVEN** | Exists, and a test or a live daemon run exercises it. |
| **BUILT, NOT PROVEN** | The code exists and is wired, but the only evidence it works is a harness — no live run, or no test that would fail if it broke. |
| **NOT BUILT** | No file, or a file that does not do what the docs claim. |

A roadmap that overstates is worse than one that understates, so anything I
could not demonstrate today is marked NOT PROVEN even where I believe it works.

---

## 1. `docs/TESSA_CORE-spec.md` §9 — the phase table

### Proposed replacement rows

| Phase | Current text | Proposed | Why |
|---|---|---|---|
| **1** | `Text agent, 8–10 tools, guard wiring, §6 schema` | **PARTIAL** — text agent ✅, tools ✅ (9 modules), guard wiring ✅, **§6 schema ❌ NOT BUILT** | Everything but the database is done. The database is not started. |
| **2** | `Voice: push-to-talk → STT → agent → TTS. Instrument §4.` | **COMPLETE** except the Piper/ElevenLabs A/B | The whole chain runs in the daemon. `core/voice/tts/elevenlabs.py` does not exist, so the A/B has not happened. |
| **3** | `Wake word, always-on daemon, Windows service, tray, night mode` | **BLOCKED on the wake word** — see §4 below | Not "not started". Surveyed, costed, and blocked on a missing artefact. |
| **Console 1** | `Electron shell, ConPTY, tabs, profiles, lazy tree, hardening` | **PARTIAL** — shell ✅, ConPTY ✅, terminal ✅; **tabs, profiles, lazy tree ❌** | `src/renderer/terminal/` exists; `tree/`, `tabs/`, `blocks/`, `chat/` do not. |

### §10, the machine-constraints table — two rows are now false

| Row | Says | Reality |
|---|---|---|
| **`14.5 GB free on C:`** | `Whisper medium is ~1.5 GB — start with small` | **Free space is 29.1 GB, not 14.5.** And the daemon runs `base`, not `small`, by measurement — `small` is on disk (486.2 MB) and unused. `base` is 147.9 MB and transcribes his speech correctly. |
| **`Battery, unstable mains` → `SQLite WAL + job checkpointing`** | Implies WAL is in place | **There is no SQLite in this repo.** `grep -r sqlite3 --include=*.py` returns nothing. This is the single most misleading line in the docs — see §2. |

---

## 2. The persistence gap — the finding that matters most

`docs/STRUCTURE.md` lists `core/db/{connection,models,migrations}` as **P1**,
and `docs/TESSA_CORE-spec.md` §6 specifies the schema. The spec's machine-constraint
table names **"SQLite WAL + job checkpointing. Test by pulling the plug mid-job."**
as the answer to unstable mains.

**None of it exists.** What actually persists, and how:

| What | Where | Durability |
|---|---|---|
| Audit log | JSONL, hash-chained, append-only | Survives a crash; a torn final line is detectable |
| Cost ledger | `data/cost-ledger.jsonl` | Append-only |
| Conversation memory | `conversation.json`, rewritten whole | **A crash mid-write can lose the thread** — the code has a load-error path, which is the tell that this was known |
| Jobs, checkpoints, §6 tables | — | **Nothing. No job engine exists yet.** |

**This is not urgent, and I want to be clear about why I am flagging it anyway.**
No job engine exists, so there is nothing to checkpoint and nothing is currently
at risk. The problem is purely that the docs say the durability story is handled
when it has not been started — and he is publishing this repo. Someone reading
§10 would conclude power loss is a solved problem here.

**Proposed:** mark `core/db/` **NOT BUILT (P1 outstanding)**, and change the
`Battery, unstable mains` consequence to *"SQLite WAL + job checkpointing —
**planned, not built.** Today: append-only JSONL for audit and cost;
`conversation.json` is rewritten whole and can be lost on a crash mid-write."*

---

## 3. `docs/STRUCTURE.md` — the tree

### 3a. Marked ⬜ (not built) but present on disk

Every one of these should flip to ✅:

```
core\config\settings.yaml          ⬜ P1  ->  ✅   (and it now carries voice/VAD tuning)
core\bus.py                        ⬜ P2  ->  ✅   audio arbitration, owns state broadcast
core\voice\stt.py                  ⬜ P2  ->  ✅   faster-whisper, as specified
core\voice\audio_io.py             ⬜ P2  ->  ✅   BUT see 3c — echo cancellation NOT built
core\voice\tts\base.py             ⬜ P2  ->  ✅
core\voice\tts\piper_tts.py        ⬜ P2  ->  ✅   (named piper_tts.py, not piper.py)
core\brain\router.py               ⬜ P1  ->  ✅
core\pty\grants.py                 ⬜ P1  ->  ✅
core\telemetry\cost.py             ⬜ P1  ->  ✅
core\telemetry\health.py           ⬜ P1  ->  ✅   (docs call this metrics.py)
apps\console\src\...\terminal\     ⬜ P1  ->  ✅   xterm + fit + webgl + GPU probe
apps\console\src\main\ws-client.ts ⬜ P1  ->  ✅
apps\console\src\main\token.ts     ⬜ P1  ->  ✅
apps\console\src\main\pty-host.ts  ⬜ P1  ->  ✅
```

### 3b. Present on disk but absent from the tree entirely

The map has no entry for these. They are roughly half the daemon:

```
core\brain\approvals.py       red-tier approval requests + expiry
core\brain\confirm.py         amber spoken-confirmation holds
core\brain\conversation.py    persisted, bounded, fence-aware thread
core\brain\executor.py        the tier gate — the security-critical one
core\brain\intents.py         the tools she can be asked for by voice
core\brain\llm\{base,gemini,anthropic_llm,local}.py
core\brain\memory.py
core\brain\persona.py         tessa.md loader
core\brain\phrasings.py
core\brain\provenance.py      THE INJECTION FENCE (docs call this security\sanitize.py)
core\brain\repair.py          transcript repair incl. strip_wake_name()
core\brain\tools_local.py     ToolCall
core\brain\tools_web.py
core\brain\unrouted.py        the handoff classifier
core\security\identity.py
core\tools\{base,browser,clip,files,procs,shell,sysctl,websearch,winman,x_tools}.py
core\tests\  (11 suites)
scripts\safeproc.py           ancestry-proven process kills
```

### 3c. Listed in the tree, still NOT BUILT

```
core\state.py                 ⬜ P1   spec §5 machine — state lives in bus.py instead
core\service.py               ⬜ P3   Windows service
core\db\*                     ⬜ P1   NOTHING. See §2.
core\fs\{enumerate,watch,hydration}.py  ⬜ P1  NOTHING — the hydration firewall
                                       is specified and unimplemented
core\pty\{registry,profiles}.py         ⬜ P1
core\pty\osc133\*.ps1                   ⬜ P2
core\voice\wake.py            ⬜ P3   BLOCKED — see §4
core\voice\vad.py             ⬜ P2   see 3d, the technology changed
core\voice\chime.py           ⬜ P3
core\voice\tts\elevenlabs.py  ⬜ P2   so the Phase 2 A/B has not happened
core\brain\{agent,context,personality,companions}.py
core\memory\*, core\autonomy\*
core\security\secrets.py      ⬜ P1   Windows Credential Manager. Keys are read
                                     from the ENVIRONMENT today.
apps\console\...\{tree,tabs,blocks,chat}\, deeplink.ts, windows.ts, grants.ts
apps\console\resources\fonts\ ⬜ P1   JetBrains Mono still not installed
```

**`core/fs/` deserves its own line.** CLAUDE.md security invariant 5 and CONTRACT
§6.3 both turn on the hydration firewall — 17,340 reparse points, metered data.
The *rule* is honoured today only because **nothing indexes the filesystem at
all**. The moment `core/fs/enumerate.py` is written, the firewall has to be
written with it, in the same change. Worth saying in the docs rather than
discovering later.

### 3d. Technology changed — the docs name the wrong library

| Doc says | Reality | Why |
|---|---|---|
| `voice\vad.py ⬜ P2 Silero` | VAD is **built**, lives in `audio_io.py`, and is **RMS-based, not Silero** | Silero drags torch — hundreds of MB on a metered link. The RMS watcher with a hybrid absolute+relative floor is measured working on his voice. |
| `voice\wake.py ⬜ P3 Porcupine` | The decision is **openWakeWord** | Porcupine's free tier requires an access key and a per-keyword cloud build. openWakeWord is fully local. |
| `tools\*_server.py — one MCP server per domain` | `core/tools/` is an **in-process registry** with tier tags validated against `permissions.yaml` at import. No MCP, no subprocess, no stdio. | Nine MCP subprocesses on 2 cores is the wrong trade here. **This is a real architectural divergence and should be recorded as a decision, not left as drift.** |
| `security\sanitize.py ⬜ P1` | The fence is **built**, as `core/brain/provenance.py` | Same function, different home. |

### 3e. Two stale status lines

- Line 65: `CONTRACT.md ✅ P0 🔒 AWAITING APPROVAL` — it is **APPROVED 2026-08-12 — FROZEN**.
- Line 69: `README.md ✅ P0 ⚠️ "## License" section MISSING` — **this warning should be removed, not satisfied.** CLAUDE.md forbids adding a LICENSE file, an SPDX header, or any OSS licence text. A ⚠️ that invites a fix which is explicitly banned is a trap for a future session. If anything belongs there it is a pointer to `COPYRIGHT.md`.

---

## 4. The wake word — record it as BLOCKED, not pending

Surveyed in full this run. Recording the outcome so the trip is not repeated:

- openWakeWord runs on **onnxruntime, no torch** for inference (torch is the
  `full` extra, which is for *training*).
- **No "Hello Tessa" or "Hey Tessa" model exists.** The complete shipped set is
  `alexa`, `hey_jarvis`, `hey_mycroft`, `hey_rhasspy`, plus `timer` and
  `weather` command models.
- Training one locally needs the `full` extra (torch + `tensorflow-cpu==2.8.1` +
  `datasets` + `deep-phonemizer`) and GB-scale negative corpora, on 2 cores with
  no GPU over a metered link. **Not viable on this machine.**
- The viable path is the project's Colab notebook: free GPU, ~1 MB `.onnx`
  brought back.

**Proposed spec §11 row:** *"Wake word — model must be trained in Colab; no
pretrained 'Hey Tessa' exists. Local training is not viable on this hardware."*

---

## 5. `docs/TESSA_CORE-CAPABILITIES.md` — the P-tier column

I have **not** rewritten this one, and I want to say why rather than let it look
like an oversight: the P-tier column is a per-capability claim about what she can
do, and getting it right means demonstrating each capability, not reading the
source. Several are **BUILT, NOT PROVEN** — wired and untested against his voice.

What I can state with evidence today:

| Capability | Status |
|---|---|
| Push-to-talk → STT → route → TTS | **BUILT + PROVEN** — live daemon, his voice |
| Tool execution (files, windows, processes, clipboard, system) | **BUILT + PROVEN** — 98 assertions |
| Red-tier gate (no execution on voice alone) | **BUILT + PROVEN** — 57 assertions |
| Injection fence | **BUILT + PROVEN** — 63 assertions |
| Conversation memory | **BUILT + PROVEN** — 45 assertions |
| Browser + X tools | **BUILT, NOT PROVEN** — harness only; `x.post`/`x.reply` are red and have never executed |
| Gemini / Anthropic / local brain | **BUILT, PARTLY PROVEN** — Gemini measured end-to-end; the local Qwen path measured at 0.9 tok/s with the Orb running and is not a daily brain |
| Wake word | **NOT BUILT — BLOCKED** |
| Speaker verification | **NOT BUILT** — viable, ~31.6 MB, no torch |
| Noise suppression | **NOT BUILT** — viable, 13.3 MB |
| Scheduler, jobs, digest | **NOT BUILT** |
| Memory / knowledge view | **NOT BUILT** |
| File tree, hydration firewall | **NOT BUILT** |

---

## 6. What I would change first, if only one thing

**The `Battery, unstable mains` row in spec §10.** Every other item here is a
roadmap being pessimistic about itself, which is harmless. That one is the
roadmap claiming a durability guarantee the code does not provide, in a
published repo, on a machine that genuinely does lose mains power.
