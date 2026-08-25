# TESSA_CORE — What "Finished" Means

> **Read this before anything else.** The spec says how to build it. The contract says how the parts talk. `TESSA_CORE-CAPABILITIES.md` lists every feature. This says **what Gerald is actually trying to end up with**, and it is the only document that answers "are we done?"

Owner: Gerald (Titan Wave LTD) · Lagos, Nigeria
Machine: Windows 11, i5-7200U, 1366×768, Intel HD 620, metered connection

---

## 1. The goal, in one sentence

**Gerald says "Tessa" out loud — and only his voice wakes her — and a personal AI agent living on his own Windows machine answers in a female voice he chose, with a personality he wrote, takes absolute control of his computer to do real work, defends the machine while she's at it, and keeps working while he sleeps.**

Everything else in this project exists to serve that sentence.

**Absolute means absolute.** Open any app, any folder, any file. Launch a specific browser in a specific profile, log into X through an already-authenticated session and post for him. Open the Tessa Console at a path and run an install. Create folders and files anywhere he names. Open a project in VS Code and run it. Set an alarm. Play a video. Schedule the laptop to sleep. Scan a download before it can execute, and quarantine it if it's dirty. Search the web, remember what it found, and tell him in the morning.

The full inventory — every capability, its permission tier and its phase — is `TESSA_CORE-CAPABILITIES.md`.

---

## 2. What the finished thing looks like — from Gerald's chair

These are the scenarios that define done. If all six work reliably, the project is finished.

**Morning.** He opens the laptop. The Orb is already running — it survived the reboot as a Windows service. The sphere is breathing slowly in `idle`. The left panel shows what Tessa did overnight: a counter of completed jobs, today's calendar, and anything that needed him but couldn't be done unattended.

**"Tessa, open the LedgerWatch folder."** She hears her name without a keypress, the sphere tightens and brightens into `listening`, and File Explorer opens. Under two seconds, start to finish.

**"Tessa, find the invoice from that Lagos client last month."** She searches his documents semantically, not by filename, and reads back what she found. The transcript panel shows the exchange.

**"Tessa, what's in my inbox?"** She summarises overnight email. He says "draft a reply to the second one" and she does — and *stops*, because sending is red-tier and needs his approval. The sphere goes amber and static: `blocked`. He can see at a glance she's waiting for him, not busy.

**Overnight.** He tells her before bed: "organise my downloads folder and pull together the receipts for last month." He shuts the lid. The daemon keeps running. In the morning there's a digest of what she did, what she couldn't, and one thing that needs a decision.

**Coding.** He opens Tessa Console instead of Windows Terminal. It lists his folders, he clicks one, a terminal opens rooted there. `npm install` works. `pip install` works. `claude` runs inside it as a full TUI. The Console shares Tessa's brain — same daemon, same permissions, same audit log.

---

## 3. Definition of done — Tessa Orb (the voice surface)

The Orb is what Tessa *looks like*. It is not a control panel; it is a presence — and a command centre.

**The sphere is living instrumentation, not decoration.** Every visual property carries data: particle displacement is live voice amplitude, the equatorial pulse is the daemon's heartbeat (if it stops, you see it), orbital rings are running jobs, satellites are companions, a red rim-flash is a security event. Full breakdown in `TESSA_CORE-CAPABILITIES.md` §R.1.

**Five rails replace the old Agenda / Jobs / Transcript**, because each answers a question he'll actually ask an agent with this much power:

| Rail | Answers |
|---|---|
| **PULSE** | Is my machine healthy? |
| **SENTINEL** | Is it safe? |
| **FLOW** | What is it doing for me? |
| **INTEL** | What does it know? |
| **TRACE** | What did we say? |

**Four modes, one app:** Full (working), Ambient (full-screen presence), Compact (a small always-on-top orb while he codes), Wall (all five rails, ≥1600px).

**Done when all of these are true:**

- He says her name and she wakes — no keypress, under 200 ms to acknowledge
- **She ignores every other voice.** Speaker verification enrolled to him alone
- She answers in a female voice he chose, with a personality he wrote and can swap
- The sphere's six states read correctly from across the room — especially `blocked` (waiting on him) versus `working` (busy)
- All five rails show live data. Nothing says `STATIC`
- Approval cards float over the sphere and interrupt — nothing else does
- `Ctrl+K` opens a command palette; a global hotkey summons her over any application
- The timeline scrubber replays what she did overnight, action by action
- Every line carries a provenance tag — human, program, agent, external — colour-coded, always on
- Companion satellites orbit, and the switcher works
- It runs in the tray, survives reboot, and is summoned by a global hotkey
- **It fills the screen.** Opens maximized, remembers its size and position, `F11` for true fullscreen. The sphere recentres on every resize and never clips

⚠️ **The three rails currently built — AGENDA, JOBS, TRANSCRIPT — are replaced.** They are live in `apps/orb` today. The five rails above are the target. This is a rework of shipped code, not a fresh design. Styling is specified in `TESSA_CORE-CAPABILITIES.md` §R.7; window behaviour in §R.8.

## 4. Definition of done — Tessa Console (the terminal surface)

From Gerald's original brief, in his words: *"I want my own custom Command Line/Console to have all the features that Command Prompt, PowerShell, WSL and all other ones have."*

The settled interpretation: **host the real shells, reimplement nothing.** Every shell runs as a real child process under ConPTY, so every feature is inherited for free. The Console adds the layer above.

**Done when:**

- **Tabs and panes** — multiple terminals in one window
- **Every shell profile launches** — cmd, PowerShell 5.1, Git Bash, Ubuntu-22.04, docker-desktop
- **File tree in the sidebar** — his whole file system, lazy-loaded, fast at the OneDrive root
- **Click a folder → a terminal opens rooted there.** This was in his first sentence and it is not optional
- **Everything runs** — `npm install`, `npm install -g`, `pip install`, `pip install --user`, `git`, and **`claude` as a full TUI** with alt-screen, resize, colour and Ctrl+C
- **Command blocks** — Warp-style, each command visually separated with its own output and exit status
- **Chat panel** — talk to Tessa inside the terminal; she proposes commands, never runs them unreviewed
- **Provenance is visible** — a gutter showing what he typed, what a program printed, and what the agent proposed. Different colours, at a glance
- **Packaged and installable** — a real app with an installer, not a dev-mode launch

---

## 5. Definition of done — Tessa Core (the daemon)

Core *is* Tessa. The two UIs are windows onto it; both can close and nothing stops.

**Done when it owns the full pipeline:**

```
VOICE ──► BRAIN ──► PERMISSION GUARD ──► TOOLS
   MEMORY (SQLite + LanceDB) · SCHEDULER · AUDIT
```

- **Voice** — wake word, speech-to-text, text-to-speech, barge-in
- **Brain** — Claude API tool-use loop, an intent router that answers simple commands locally with zero network, personality, per-companion state
- **Tools** — filesystem, app launching, window control, browser automation, Gmail, calendar, git, dev commands. Each an MCP server
- **Guard** — green/amber/red tiers, protected paths, approval flow. Already built and proven
- **Memory** — episodic, semantic and procedural. Semantic search over his own documents
- **Scheduler** — jobs that run overnight, survive power cuts, and report in the morning
- **Audit** — hash-chained, tamper-evident, secrets redacted. Already built and proven
- **Runs as a Windows service** — survives reboots without him launching anything

---

## 6. What is explicitly NOT the goal

Every one of these was proposed and cut. Do not reopen them.

| Not the goal | Why |
|---|---|
| A Warp competitor | The Console is Tessa's terminal, not a product |
| A new shell language | He needs to run his projects, not learn a language |
| Reimplementing CMD / PowerShell / WSL builtins | Infinite scope, strictly worse result. Host the real binaries |
| A cloud service or multi-user product | This runs on one machine, for one person |
| A mobile app | A phone cannot reach `127.0.0.1` or read the token file. Remote status is an **outbound bridge** — push notification or Telegram — not a surface |
| A sandbox | The Console's job is running arbitrary commands. It is auditable and permission-gated. It is not isolated, and must never be described as one |
| Anything requiring MSVC or Rust | Neither is installed, and the connection is metered |

---

## 7. Where it stands today

Honest assessment against sections 3–5:

| Surface | State | Roughly |
|---|---|---|
| **Core** | Auth boundary, permission guard, hash-chained audit — all built and proven. **No brain, no voice, no tools, no jobs, no memory.** | ~15% |
| **Orb** | Shell, layout, sphere with six states, live handshake and heartbeat. All 11 verification steps pass. **No voice. Microphone actively denied. Every panel is placeholder data.** | ~20% |
| **Console** | Hardened shell, ConPTY in a utilityProcess, xterm on WebGL. **One window, one terminal. No tabs, no file tree, no chat, no blocks. Currently spawns a PTY without a daemon grant — a live contract violation, deliberately flagged in code.** | ~25% |

**The part Gerald actually wants — talking to Tessa and having her do things — does not exist yet.** The foundation under it is unusually solid: a frozen protocol, a proven security boundary, and two surfaces that both authenticate against the same daemon. But the agent itself is not built.

Realistic remaining effort against the **full** catalogue: **12–18 months part-time**, alongside a Semester One ADSE course and Titan Wave client work.

But the phasing means nothing is wasted if he stops early. **At Phase 5 he already has a voice agent that recognises only him, controls his machine, and works overnight** — the original promise, delivered. Phases 6–9 are depth, not foundation. See `TESSA_CORE-CAPABILITIES.md` §S.

---

## 8. The one big ordering decision — Gerald's to make

The voice pipeline lives in `core/`, which the Console session owns. That creates a choice:

**Option A — finish the Console first (the current plan).**
Console Phase 1a → 1b → 1c → 1d, roughly 6–7 weeks, then core grows voice. The Orb sits blocked that entire time. A polished terminal arrives months before Tessa can speak.

**Option B — voice first.**
Close the Console's grant gap (Step 4), get Phase 1a to a usable single terminal, then jump `core/` straight to the voice pipeline. Tessa talks months earlier; the terminal stays rough for longer.

**Ask Gerald which he wants before writing the next Console prompt.** It is the largest remaining decision and no document can make it for him.

---

## 9. The test that decides it

When the project is finished, this works, unprompted, on a cold-booted machine:

> Gerald walks into the room, says **"Tessa, what happened last night?"**, and she tells him — in her own voice, from the work she actually did while he was asleep.

Everything in this repository exists to make that one moment real.
