# TESSA_CORE — Complete Capability Catalogue

> Companion to `TESSA_CORE-GOAL.md`. The goal doc says *what finished means*; this says **everything Tessa can do**.
> Every capability is tagged with a permission tier (🟢 green / 🟡 amber / 🔴 red) and the phase it lands in.
> Tiers are enforced by `core/security/guard.py`, which is already built and proven.

---

## How to read this

| Tag | Meaning |
|---|---|
| 🟢 | Runs unattended, always |
| 🟡 | Unattended only inside a job Gerald explicitly scheduled |
| 🔴 | Always requires explicit approval — no exceptions |
| **P1–P12** | Phase it lands in |

**One rule governs all of it:** the model picks a tool *name* and structured *arguments*. Python owns execution. No LLM-generated string is ever executed. That is what makes 🟢 safe to run while he sleeps.

---

## A · VOICE & IDENTITY

The thing that makes Tessa *Tessa* rather than a script runner.

| Capability | Tier | Phase | Notes |
|---|---|---|---|
| Custom wake word "Tessa" | 🟢 | P3 | Picovoice Porcupine, always listening, <200 ms to chime |
| **Speaker verification — Gerald's voice only** | 🟢 | P3 | See below |
| Female voice, selectable | 🟢 | P2 | Piper (local, free) or ElevenLabs (alive, paid). Adapter for both |
| Personality profiles, hot-swappable | 🟢 | P2 | Markdown files under `core/config/personalities/` |
| Barge-in — interrupt mid-sentence | 🟢 | P2 | TTS stops within 120 ms |
| Streaming STT + streaming TTS | 🟢 | P2 | She starts speaking on the first sentence, not the full response |
| Push-to-talk fallback | 🟢 | P2 | Built before the wake word — simpler, proves the pipeline |
| Whisper mode / night voice | 🟢 | P5 | Quiet response during configured quiet hours |
| Nigerian Pidgin comprehension | 🟢 | P4 | Whisper handles it; the personality decides whether she answers in it |
| Voice macros — one phrase, many actions | 🟢 | P7 | "Tessa, work mode" → VS Code + terminal + Spotify + DND |
| Mute / stand down | 🟢 | P3 | "Tessa, stand down" — stops listening until summoned by hotkey |
| Echo cancellation | 🟢 | P2 | So she doesn't hear herself |

### Speaker verification — only his voice

**How:** SpeechBrain's pretrained ECAPA-TDNN (`speechbrain/spkrec-ecapa-voxceleb`) converts audio into a fixed-length speaker embedding. Gerald enrolls with ~10 short phrases at setup. Every wake-word hit is embedded and cosine-compared against his stored centroid. Below threshold → ignored, logged, sphere doesn't even wake.

**Honest limit:** a recording of his voice, or a good clone, can pass. Generative spoofing is an active research problem, not a solved one. So speaker verification is a **filter that stops other people in the room from commanding his laptop** — it is *not* the security boundary. Red-tier actions still require his explicit on-screen approval, always.

**Also enables:** per-speaker personalities later (a family member gets a restricted companion), and honest transcript attribution.

---

## B · SYSTEM CONTROL

| Capability | Tier | Phase | Notes |
|---|---|---|---|
| Launch any app by name | 🟢 | P1 | Fuzzy-matched against a Start Menu index |
| Open folder / file | 🟢 | P1 | Explorer, or the app he associates with it |
| **Open a specific browser AND profile** | 🟢 | P1 | `chrome.exe --profile-directory="Profile 2"` — profiles enumerated from `Local State` |
| Window control — focus, minimise, maximise, close, snap | 🟢 | P4 | `pygetwindow` + Win32 |
| Virtual desktop switching | 🟢 | P7 | |
| Volume, brightness, media keys | 🟢 | P1 | |
| Screenshot — full, region, active window | 🟢 | P4 | |
| **Screen understanding** — "what's on my screen?" | 🟢 | P8 | Screenshot → vision model → answer. Genuinely powerful |
| Clipboard read / write / history | 🟢 | P4 | |
| Process list, resource usage | 🟢 | P1 | |
| Kill a process | 🟡 | P4 | PID-targeted, never by name |
| Lock the machine | 🟢 | P1 | |
| **Sleep / hibernate / shutdown / restart** | 🔴 | P1 | Confirmation always — even scheduled |
| **Scheduled sleep or shutdown** | 🟡 | P5 | "Tessa, shut down at 2am if nothing's running" |
| Wake-on-schedule | 🟡 | P5 | Windows Task Scheduler wake timer |
| Battery, thermal, disk, RAM status | 🟢 | P1 | |
| Wi-Fi / Bluetooth / airplane toggle | 🟡 | P7 | |
| Display resolution & multi-monitor | 🟡 | P7 | |
| Printer control | 🟡 | P8 | |
| Allowlisted PowerShell | 🔴 | P1 | Fixed allowlist plus validated parameters. **Never a raw command string** |

---

## C · FILES & STORAGE

| Capability | Tier | Phase | Notes |
|---|---|---|---|
| **Create folder or file in any specified directory** | 🟡 | P1 | Protected paths confirm regardless |
| Semantic search — "the invoice from that Lagos client" | 🟢 | P6 | LanceDB over an indexed corpus |
| Instant filename search | 🟢 | P4 | Everything SDK — far faster than Windows Search |
| Read file contents into context | 🟢 | P1 | |
| Move, rename, copy | 🟡 | P1 | |
| Batch organise — by type, date, project | 🟡 | P5 | "Organise my downloads" |
| Delete | 🔴 | P1 | **Recycle Bin only, never hard delete** |
| Duplicate finder | 🟢 | P6 | Hash-based |
| Disk cleanup — large files, stale builds, `node_modules` | 🟡 | P6 | Matters at ~20 GB free |
| Archive / extract — zip, 7z, tar | 🟡 | P4 | |
| Watch a folder for changes | 🟢 | P5 | Triggers jobs |
| **Cloud-file hydration firewall** | 🟢 | P1 | Never reads a OneDrive placeholder. 17,340 exist. Metered data |
| Backup a folder to a target | 🟡 | P8 | |

---

## D · DEVELOPMENT

This is his daily work. It deserves first-class tooling.

| Capability | Tier | Phase | Notes |
|---|---|---|---|
| **Open a project in VS Code** | 🟢 | P1 | `code <path>` via terminal |
| **Run a project — detect and execute** | 🟡 | P4 | Reads `package.json` / `requirements.txt` / `*.csproj`, picks the right command |
| **Open the Tessa Console at a path and run an install** | 🟡 | P4 | Exactly his ask: "open the console and run installation" |
| `npm` / `pip` / `yarn` install, incl. `-g` and `--user` | 🟡 | P1 | The Console's Step 5 exit criterion |
| Scaffold a new project | 🟡 | P6 | Vite, Express, FastAPI, Hardhat — his stacks |
| Git: status, log, diff, branch, commit | 🟡 | P5 | |
| Git: push, force, reset --hard | 🔴 | P5 | |
| Run tests, report pass/fail | 🟢 | P5 | |
| Tail logs, alert on error patterns | 🟢 | P5 | |
| **Which process is on port 3000 — and kill it** | 🟡 | P4 | The single most-wanted dev utility |
| Environment variable management | 🔴 | P6 | Touches secrets |
| Docker: list, start, stop containers | 🟡 | P7 | |
| Database queries — SQL Server, MongoDB | 🟡 | P8 | His stacks |
| Deploy trigger — Vercel, Render, Netlify | 🔴 | P8 | He uses all three |
| Code review on a diff | 🟢 | P8 | |
| **"What was I working on yesterday?"** | 🟢 | P6 | Git log + file activity + memory |

---

## E · BROWSER & WEB

| Capability | Tier | Phase | Notes |
|---|---|---|---|
| Web search + summarise | 🟢 | P4 | |
| Open a URL in a named browser and profile | 🟢 | P1 | |
| Deep research → written report saved to disk | 🟢 | P6 | |
| Extract page content | 🟢 | P4 | |
| Tab management | 🟢 | P7 | |
| **Full browser automation** — navigate, click, type, scroll | 🟡 | P4 | Playwright against a **persistent profile** |
| Fill a form | 🔴 | P4 | |
| Submit a form | 🔴 | P4 | |
| **X/Twitter: post a tweet** | 🔴 | P7 | See below |
| **X/Twitter: like, reply, repost, bookmark** | 🟡 | P7 | |
| X/Twitter: read timeline, search, summarise | 🟢 | P7 | |
| Download a file | 🟡 | P4 | **Always routed through the download gate — §I** |
| Bookmark management | 🟡 | P7 | |
| Price / stock / availability monitoring | 🟢 | P8 | Scheduled, alerts on change |
| Scrape to CSV or JSON | 🟡 | P8 | |
| Airdrop / testnet task automation | 🔴 | P9 | His Web3 interest. Red-tier — touches wallets |

### The X automation, done safely

**Never store his X password.** Two supported paths:

1. **Persistent profile (default).** He logs into X once in a dedicated Chrome profile. Playwright drives that already-authenticated session. Tessa never sees the credential, 2FA is already satisfied, and revoking access is deleting a profile folder.
2. **Official X API.** Cleaner and more reliable for posting, but it's a paid tier for write access.

Posting publicly under his name is 🔴 — she drafts, shows him the text, and waits. Likes and bookmarks are 🟡. Reading is 🟢.

---

## F · COMMUNICATIONS

| Capability | Tier | Phase | Notes |
|---|---|---|---|
| Gmail: read, search, summarise | 🟢 | P5 | |
| **Overnight inbox triage + morning summary** | 🟢 | P5 | Categorised, priority-flagged |
| Gmail: draft | 🟡 | P5 | |
| **Gmail: send** | 🔴 | P5 | Outbound allowlist — only addresses in his contacts |
| Calendar: read agenda | 🟢 | P5 | |
| Calendar: create / move / cancel | 🟡 | P5 | |
| WhatsApp send via Twilio | 🔴 | P8 | He's built this before, in LedgerWatch |
| **Telegram bot — remote bridge** | 🟢 | P5 | Check jobs and approve actions from his phone. **This is the answer to "mobile", not a third surface** |
| Push notifications to phone | 🟢 | P5 | ntfy.sh or Pushover |
| Slack / Discord | 🟡 | P9 | |

---

## G · MEDIA & ENTERTAINMENT

| Capability | Tier | Phase | Notes |
|---|---|---|---|
| **Play a video** — local or YouTube | 🟢 | P4 | "Tessa, play the last Man City highlights" |
| Music control — Spotify, local | 🟢 | P4 | Play, pause, skip, playlist, volume |
| Screen recording | 🟡 | P8 | |
| Screenshot annotation | 🟢 | P8 | |
| Image conversion, resize, format | 🟡 | P7 | |
| Read a document aloud | 🟢 | P6 | |

---

## H · TIME & SCHEDULING

| Capability | Tier | Phase | Notes |
|---|---|---|---|
| **Set an alarm** | 🟢 | P5 | Rings even if the UI is closed — it's the daemon |
| Timers — one or many, named | 🟢 | P5 | |
| **Natural-language reminders** | 🟢 | P5 | "Remind me Thursday to submit the Aptech assignment" |
| Recurring / cron jobs | 🟡 | P5 | |
| Countdown to a deadline | 🟢 | P5 | |
| Pomodoro / focus sessions | 🟢 | P7 | |
| Scheduled sleep or shutdown | 🟡 | P5 | |
| Wake the machine on a schedule | 🟡 | P5 | |
| Time-zone conversion | 🟢 | P5 | |
| **Do Not Disturb / focus mode** | 🟢 | P7 | Silences her and Windows notifications |

---

## I · SENTINEL — SECURITY & THREAT DEFENCE

The panel that replaces "Jobs". This is where Tessa earns trust.

### Antivirus — driving Microsoft Defender, not replacing it

Building a real AV engine needs signed kernel drivers (no MSVC on this machine), a signature pipeline, and it would fight Defender for the same hooks. **A half-built scanner is worse than none — it creates false confidence.** So Tessa becomes a *console over a real engine*:

| Capability | Tier | Phase | Mechanism |
|---|---|---|---|
| Scan a file or folder on demand | 🟢 | P6 | `Start-MpScan -ScanType CustomScan -ScanPath <path>` |
| Quick scan / full system scan | 🟡 | P6 | `MpCmdRun.exe -Scan -ScanType 1\|2` |
| **Boot-time offline scan** — catches rootkits Defender can't touch while running | 🔴 | P6 | `Start-MpWDOScan` |
| Read threat history | 🟢 | P6 | `Get-MpThreatDetection` |
| Defender health — definitions age, real-time protection on/off | 🟢 | P6 | `Get-MpComputerStatus` |
| Force a definition update | 🟢 | P6 | `MpCmdRun.exe -SignatureUpdate` |
| Quarantine / restore / remove a threat | 🔴 | P6 | |

> `MpCmdRun.exe` is **not on PATH**. The current build lives in `C:\ProgramData\Microsoft\Windows Defender\Platform\<version>\`, and most operations need elevation.

### The download gate — his actual ask

| Capability | Tier | Phase | Notes |
|---|---|---|---|
| **Watch the Downloads folder in real time** | 🟢 | P6 | |
| **Scan every new file on arrival** | 🟢 | P6 | Defender custom scan, before he can double-click it |
| **VirusTotal second opinion** | 🟢 | P6 | SHA-256 lookup against 70+ engines. Free tier: 4 req/min. **Hash only — never uploads his files** |
| **Quarantine on detection** — move to a vault, strip execute | 🟢 | P6 | Automatic. Then it tells him |
| Flag and warn before execution | 🟢 | P6 | Toast + Orb alert |
| Release from quarantine | 🔴 | P6 | Explicit approval, always |
| Mark-of-the-Web check | 🟢 | P6 | Was it downloaded from the internet? |
| Certificate / signature verification | 🟢 | P6 | Is that installer actually signed by who it claims? |

### System hardening audit

| Capability | Tier | Phase |
|---|---|---|
| Startup program audit — what runs at boot and why | 🟢 | P7 |
| Open port audit — what's listening, and which process | 🟢 | P7 |
| Suspicious process detection — unsigned binaries in temp dirs | 🟢 | P7 |
| Firewall status and rules | 🟢 | P7 |
| Windows Update status | 🟢 | P7 |
| Scheduled task audit | 🟢 | P7 |
| **Breach check** — has his email appeared in a known breach | 🟢 | P8 |
| Weekly security digest | 🟢 | P7 |

### Tessa's own security surface

| Capability | Tier | Phase |
|---|---|---|
| Hash-chained tamper-evident audit log | — | ✅ **Built** |
| Green/amber/red permission tiers | — | ✅ **Built** |
| Protected-path confirmation | — | ✅ **Built** |
| Secret redaction before write | — | ✅ **Built** |
| **Prompt-injection containment** — external content is data, never instruction | 🟢 | P4 |
| **Panic kill switch** — global hotkey, kills the daemon instantly | 🟢 | P3 |
| Nightly token budget cap — hard stop | 🟢 | P5 |
| Encrypted memory database at rest | 🟢 | P6 |

---

## J · PULSE — SYSTEM INTELLIGENCE

The panel that replaces "Agenda". Live vitals, and what Tessa is touching *right now*.

| Capability | Tier | Phase |
|---|---|---|
| Live CPU, RAM, disk, network sparklines | 🟢 | P4 |
| Top processes by resource use | 🟢 | P4 |
| **What Tessa is doing this second** — active tool, target, elapsed | 🟢 | P4 |
| Disk space with a projection — "full in ~11 days at this rate" | 🟢 | P6 |
| **Metered data usage tracker** | 🟢 | P5 | Critical in Lagos |
| API spend today / this month | 🟢 | P5 |
| Battery health and time remaining | 🟢 | P4 |
| Thermal state — is it throttling? | 🟢 | P6 |
| Uptime, last reboot, pending restarts | 🟢 | P4 |
| Network — connected SSID, latency, is the connection actually up | 🟢 | P5 |

---

## K · INTEL — MEMORY & KNOWLEDGE

The panel that replaces "Transcript" as a rail. The transcript stays, but as an overlay.

| Capability | Tier | Phase |
|---|---|---|
| Episodic memory — what happened, when | 🟢 | P6 |
| Semantic memory — facts, preferences, people, projects | 🟢 | P6 |
| Procedural memory — learned recipes: "when he says X he means this sequence" | 🟢 | P7 |
| **Knowledge graph, rendered** — entities and how they connect | 🟢 | P6 |
| Document index over chosen folders | 🟢 | P6 |
| **Explicit teaching** — "Tessa, remember that…" | 🟢 | P6 |
| Forgetting and editing memory | 🟢 | P6 |
| Voice notes, transcribed and indexed | 🟢 | P6 |
| **Project context awareness** — knows LedgerWatch from Gamers Store from TESSA_CORE | 🟢 | P7 |
| Learning from correction — she gets it wrong once, not twice | 🟢 | P8 |
| Memory backup and restore | 🟢 | P8 |

---

## L · FLOW — AUTONOMY & AUTOMATION

The overnight engine. This is "works while you sleep."

| Capability | Tier | Phase |
|---|---|---|
| Job queue with retry and exponential backoff | 🟢 | P5 |
| **Checkpointing — survives a power cut mid-job** | 🟢 | P5 |
| Triggers: time, file-watch, email arrival, webhook, system event | 🟢 | P5 |
| **Approval gates with 30-minute expiry** → `needsReview`, never auto-approved | 🟢 | P5 |
| **Morning digest** — what she did, what she couldn't, what needs him | 🟢 | P5 |
| Dry-run mode — show the plan, execute nothing | 🟢 | P5 |
| Multi-step workflows with conditionals | 🟢 | P7 |
| Recorded macros — do it once, save it as a command | 🟢 | P7 |
| Parallel job execution across companions | 🟢 | P7 |
| **Proactive suggestions (opt-in)** — "your disk is nearly full, want me to clean the stale builds?" | 🟢 | P8 |

---

## M · COMPANIONS — THE MULTI-AGENT LAYER

Each with its own voice, personality, and tool allowlist. The switcher under the sphere.

| Companion | Owns | Phase |
|---|---|---|
| **Tessa** | General assistant, orchestrator, the voice he talks to | P2 |
| **Sentinel** | Security, scans, quarantine, audit, the download gate | P7 |
| **Forge** | Development — git, builds, tests, deploys, project scaffolding | P7 |
| **Scout** | Research, web, summarisation, monitoring | P7 |
| **Ledger** | Finance — invoices, expenses, receipts. Ties to his LedgerWatch work | P9 |

Orchestrator dispatches by intent. Others may work in parallel; only the mic-holder may speak.

---

## N · RESILIENCE — the Lagos requirements

| Capability | Phase | Why |
|---|---|---|
| **Offline mode** — local STT + local TTS + local intent handlers | P8 | Basic commands work with no internet at all |
| Power-cut resilience — SQLite WAL + job checkpointing | P5 | Test by pulling the plug mid-job |
| Auto-restart on crash | P3 | Windows service recovery |
| Graceful API degradation — queue, don't crash | P5 | |
| Metered-data awareness — warn before a large download | P5 | |
| Local-first routing — 60–80% of utterances never touch the network | P4 | Biggest cost and latency lever in the system |
| Config hot-reload | P5 | |

---

## O · PRODUCTIVITY & WELLBEING

He is a Semester One ADSE student running a company. This domain protects the thing that actually limits output — him.

| Capability | Tier | Phase | Notes |
|---|---|---|---|
| **Assignment & deadline tracker** | 🟢 | P5 | Aptech coursework, with escalating reminders as a date approaches |
| Study timer with subject tracking | 🟢 | P7 | "Tessa, 45 minutes on C pointers" |
| **Lecture / meeting transcription** | 🟢 | P8 | Records, transcribes, summarises, extracts action items |
| Flashcards generated from a document | 🟢 | P8 | Feed it a PDF, get spaced-repetition cards |
| Note capture by voice, indexed and searchable | 🟢 | P6 | |
| **20-20-20 eye-strain reminder** | 🟢 | P7 | Every 20 min, look 20 ft away, 20 seconds. He codes for hours |
| Break and posture reminders | 🟢 | P7 | Suppressed during focus sessions |
| **Sleep-schedule enforcement** | 🟡 | P7 | "It's 2am and you have class at 8" — she can dim, mute, or shut down |
| Daily / weekly review — what he actually did | 🟢 | P8 | From git, files, and calendar. Not self-reported |
| Habit tracking | 🟢 | P9 | |
| Screen-time by application | 🟢 | P7 | |

---

## P · BUSINESS & CONTENT

Titan Wave, and the Emperor brand on X and TikTok.

| Capability | Tier | Phase | Notes |
|---|---|---|---|
| **Invoice generation** from a template | 🟡 | P9 | Ties into his LedgerWatch work |
| **Receipt capture → expense report** | 🟢 | P9 | Photo or PDF in, categorised spreadsheet out |
| Client follow-up tracking | 🟢 | P9 | Who owes a reply, who owes money |
| Proposal drafting from a brief | 🟢 | P9 | |
| **Content idea generation** from what's trending in his niche | 🟢 | P8 | |
| Draft a post in his voice | 🟡 | P8 | Learned from his existing posts |
| **Schedule posts across X and TikTok** | 🔴 | P9 | Publishing is always red-tier |
| Engagement analytics — what performed, what didn't | 🟢 | P9 | |
| Competitor / market monitoring | 🟢 | P9 | Scheduled, alerts on change |
| **Crypto & airdrop monitoring** | 🟢 | P9 | Price alerts, testnet task deadlines, gas thresholds. Read-only |
| Wallet balance checks | 🟢 | P9 | Read-only. **Tessa never signs a transaction** |

---

## Q · ADVANCED INTELLIGENCE

The capabilities that make her feel less like a command runner and more like something that understands.

| Capability | Tier | Phase | Notes |
|---|---|---|---|
| **Screen understanding** — "what am I looking at?" | 🟢 | P8 | Screenshot → vision model. Works on anything: an error dialog, a chart, a design |
| **"Fix this error"** — she reads the screen and diagnoses | 🟢 | P8 | Point her at a stack trace visually, no copy-paste |
| **OCR from screen or image** | 🟢 | P8 | Pull text out of a screenshot, a PDF scan, a photo |
| Document Q&A — ask questions of a file | 🟢 | P6 | |
| Translation, spoken or written | 🟢 | P8 | |
| **Image generation** for content or mockups | 🟡 | P9 | |
| Summarise anything — a page, a video, a thread, a repo | 🟢 | P6 | |
| **Multi-step reasoning with a visible plan** | 🟢 | P4 | She shows the plan before executing. Dry-run always available |
| **Clarifying questions instead of guessing** | 🟢 | P4 | Ambiguous request → she asks, she doesn't assume |
| Confidence signalling — "I'm not sure" is a valid answer | 🟢 | P4 | |
| **Model routing** — Haiku for classification, Sonnet for work, Opus for hard reasoning | 🟢 | P4 | Cost control that the user never sees |
| Context compaction — long conversations don't degrade | 🟢 | P4 | |
| **Learning from correction** — wrong once, not twice | 🟢 | P8 | Writes to procedural memory |

---

## R · THE DASHBOARD — the Orb command centre

The sphere is the centre stage. Everything else orbits it, literally and structurally. Designed for 1366×768 first; richer at ≥1600px.

### R.1 · The sphere itself — living instrumentation

The sphere is not decoration. Every visual property carries data.

| Element | Encodes | Phase |
|---|---|---|
| **Particle displacement** | Live voice amplitude — hers when speaking, his when listening | P4 |
| **Six state signatures** | idle · listening · thinking · speaking · working · blocked | ✅ Built |
| **Colour temperature** | Cool at rest → hot under load | P4 |
| **Equatorial pulse** | The daemon's 5-second heartbeat. If it stops, the pulse stops — a dead daemon is visible instantly | P4 |
| **Orbital job rings** | One thin arc per running job, filling as it completes. Five jobs, five rings | P5 |
| **Companion satellites** | Small spheres in outer orbit, one per companion, brightening when active | P7 |
| **Threat flare** | A red rim-flash when Sentinel flags something. Impossible to miss from across the room | P6 |
| **Resource aura** | Ambient glow intensity tracks CPU and RAM. Subtle — felt, not read | P6 |
| **Waveform ribbon** | A live audio trace beneath the sphere while she speaks | P4 |
| **Confidence halo** | Ring opacity tracks how sure she is of the current answer | P8 |
| **Density = particle count** | Visibly denser on capable hardware, honest about degradation | ✅ Built |

### R.2 · Persistent HUD — always visible, never in the way

| Region | Shows | Phase |
|---|---|---|
| **Top bar** | Companion name · state · connection + uptime · data used today · API spend today | P4 |
| **Top-right** | Notification stack — dismissible, stacked, auto-fading | P4 |
| **Under the sphere** | Live transcript, one line, fading. The last thing said | P4 |
| **Bottom-left** | Companion switcher — ◀ TESSA ▶ with satellites | P7 |
| **Bottom-right** | Quick-action dock — pinned macros, one click or one keystroke | P7 |
| **Floating** | Approval cards. They interrupt, over the sphere, amber. Nothing else does | P5 |

### R.3 · The five rails

The sphere holds the centre; rails open as overlays, one at a time on a 1366px display.

| Rail | Answers | Contents | Phase |
|---|---|---|---|
| **PULSE** | *Is my machine healthy?* | CPU/RAM/disk/network sparklines · top processes · **what Tessa is touching this second** · disk projection · metered data · API spend · battery · thermal · uptime | P4 |
| **SENTINEL** | *Is it safe?* | Defender status & definition age · threat history · **quarantine vault** · pending approvals · active PTY grants · live audit stream · startup & port audit | P6 |
| **FLOW** | *What is it doing for me?* | Running jobs · scheduled jobs · triggers · **calendar and agenda** · overnight queue · morning digest · alarms and timers | P5 |
| **INTEL** | *What does it know?* | **Knowledge graph, rendered** · memory browser · indexed documents · project context · teach/forget controls | P6 |
| **TRACE** | *What did we say?* | Full transcript, per-companion tabs · searchable · exportable · **provenance-tagged** so agent-proposed text is never mistaken for his | P4 |

### R.4 · Command surfaces — because voice isn't always right

Sometimes he's in a call, or it's 2am, or the command is a file path.

| Surface | Trigger | Phase |
|---|---|---|
| **Command palette** | `Ctrl+K` — type any command, fuzzy-matched, with preview | P5 |
| **Global spotlight** | A system-wide hotkey summons a translucent Tessa bar over whatever is on screen. Type or speak, it answers, it vanishes | P7 |
| **Quick-action dock** | Pinned macros — "work mode", "shut down at 2am", "scan downloads" | P7 |
| **Drag-and-drop target** | Drop a file on the sphere → "what do you want done with this?" | P8 |
| **Tray menu** | State, mute, panic stop, open rails — without focusing the window | P4 |
| **Push-to-talk** | Hold a key, speak. No wake word needed | P2 |

### R.5 · Modes — the same app, four postures

| Mode | Looks like | For | Phase |
|---|---|---|---|
| **Full** | Sphere + HUD + one rail | Working at the desk | P4 |
| **Ambient** | Full-screen sphere, HUD faded, no rails | Left running. A presence, not a tool | P7 |
| **Compact** | A small always-on-top orb in a corner | Coding — she's there, not in the way | P7 |
| **Wall** | All five rails open at once | ≥1600px or a second monitor. The full command centre | P8 |

### R.6 · Advanced dashboard features

| Feature | What it does | Phase |
|---|---|---|
| **Timeline scrubber** | Drag back through the night. See every action, every state change, every approval, in order. Replay what she did while he slept | P8 |
| **Live activity feed** | A ticker of every tool call as it happens, colour-coded by tier. Green scrolls past; red stops and waits | P5 |
| **Approval queue** | Everything waiting on him, in one place, with full context and a dry-run preview of each | P5 |
| **Cost meter** | Tokens and naira, today and this month, with the nightly cap as a visible bar | P5 |
| **Job Gantt** | Overnight work laid out on a timeline — what ran when, what blocked, what failed | P7 |
| **Threat map** | Quarantined files, where each came from, what flagged it, what he decided | P6 |
| **Memory heatmap** | What she references most. Shows what she actually knows versus what she's stored | P8 |
| **Latency HUD** | The §4 budget, live. Wake→chime, speech→audio, tool→confirm. Regressions become visible instead of felt | P4 |
| **Provenance gutter** | Every line tagged human / program / agent / external. **Colour-coded, always on** | P4 |
| **Dry-run overlay** | Before any amber or red action: exactly what will happen, file by file | P5 |
| **"Explain that"** | Point at anything on the dashboard, ask why. She explains the decision and cites the audit entry | P8 |
| **Keyboard-complete** | Every action reachable without a mouse. Rails, palette, approvals, mode switching | P5 |

### R.7 · Visual design specification — the rails

The screenshots define the *language*. This defines how the new rails speak it. Every value below comes from `packages/tokens` — **no hard-coded hex, ever.** A lint rule fails the build on any literal.

**Rail tabs (the 48px left rail)**
- Vertical text, rotated 180°, reading bottom-to-top
- `--fs-label` 10px · `--font-mono` · uppercase · `--label-tracking` 0.14em
- Resting `--text-muted` · hover `--text` · active `--accent`
- Active marker: a 2px `--accent` bar on the rail's inner edge, 24px tall, vertically centred on the label
- **No icons.** The rail is type only — that is the whole aesthetic
- Order top to bottom: PULSE · SENTINEL · FLOW · INTEL · TRACE

**Drawer**
- 320px wide, full height, right of the rail
- `--panel` background · 1px `--panel-border` on the inner edge only
- `--panel-radius` 12px on the outer corners, square against the rail
- `backdrop-filter: blur(12px)` — the sphere shows through, dimmed
- Slides in 180 ms `cubic-bezier(.2,.8,.2,1)`. **Slides, never fades.** Respects `prefers-reduced-motion` by snapping instantly
- One drawer at a time below 1600px. `Esc` closes

**Inside a drawer**
- Section headers: `--fs-label` uppercase mono, 0.14em, `--text-muted`, 1px `--panel-border` underline, `--sp-4` above
- Body: `--fs-base` 13px `--font-mono`
- Row height 28px, `--sp-3` horizontal padding, alternating rows at 2% white — barely there
- **All numbers `font-variant-numeric: tabular-nums`** so live values don't jitter as digits change
- Empty state: the word `NO DATA` in `--text-muted`, `--fs-label`. Never a graphic, never an illustration

**Data display**
- Sparklines: 1px stroke, 40px tall, no axes, no grid, no legend
- Normal `--status-active` · warning `--status-warn` · critical `--status-error`
- Bars: 4px tall, `--radius-pill`, track at 6% white
- Tier badges: pill, `--fs-label`, 2px/6px padding — green `--status-active`, amber `--status-warn`, red `--status-error`, always with the tier word, never colour alone
- Timestamps: relative under an hour (`4m ago`), absolute beyond (`14:32`)

**Per-rail identity — disciplined**
Every rail uses `--accent` for its active state. Only **SENTINEL** carries a status colour, because it *is* a status: green when Defender is healthy and the quarantine is empty, amber on a stale definition or a pending approval, red on an active detection. When SENTINEL is red, the rail label is red **even while another drawer is open** — a threat is never hidden behind a closed panel.

**Motion budget**
Drawer slide 180 ms · value updates cross-fade 120 ms · sparklines redraw on a 1 Hz tick, not per sample · **nothing on the rails animates continuously.** The sphere is the only thing that moves at rest. Everything else must earn its motion.

**Forbidden, both surfaces**
No emoji · no icon fonts · no blue-purple gradients · no drop shadows · no rounded-rectangle "cards" on the centre stage · no borders wider than 1px · no font other than the mono stack.

---

### R.8 · Window & display behaviour

**Current defect:** the Orb launches at 1382×736 on a 1366×768 display — wider than the screen and 32px short of the height. It does not fill the display and does not remember its size.

| Behaviour | Requirement | Phase |
|---|---|---|
| **First launch** | Opens **maximized** — fills the work area exactly, taskbar respected | P4 |
| **Subsequent launches** | Restores the last size, position and maximized state | P4 |
| **State persistence** | `width`, `height`, `x`, `y`, `isMaximized` written to `%LOCALAPPDATA%\Tessa\orb-window.json` on move/resize, debounced 400 ms | P4 |
| **Bounds clamping** | On restore, verify the saved position falls inside a currently-connected display. If not, discard and maximize. A saved position on a disconnected monitor must never open the window offscreen | P4 |
| **Minimum size** | 900×600. Below that the collapsed layout breaks | P4 |
| **True fullscreen** | `F11` toggles borderless fullscreen — this is Ambient mode's natural home | P7 |
| **Sphere recentering** | The sphere recentres and rescales on every resize, debounced to one reflow per 100 ms. It must never sit off-centre or clip | P4 |
| **DPI changes** | Handle `display-metrics-changed`. Re-probe the refresh rate — **the frame divider depends on it**, and an external monitor at a different Hz would silently break the pacer | P4 |
| **Multi-monitor** | Opens on the display it was last closed on | P7 |
| **Layout breakpoints** | <1600px: rail + sphere + one drawer. ≥1600px: two drawers permitted. ≥1920px: Wall mode available | P4 |

**Implementation note:** use `screen.getPrimaryDisplay().workAreaSize`, not `.size` — `size` includes the taskbar and produces a window taller than the usable area, which is close to the current bug.

---

## S · SCOPE — the honest number

The original TESSA_CORE was ~3–4 months part-time. **This catalogue is roughly four times that** — call it **12–18 months** at his pace, alongside Aptech and Titan Wave.

That is not a reason to cut it. It *is* a reason to order it so every phase is independently useful and nothing is wasted if he stops early:

| Phase | What he gains | Cumulative feel |
|---|---|---|
| **P1–P3** | She talks. Wake word, **his voice only**, ten real system commands, panic switch | Already life-changing |
| **P4** | The Orb comes alive — PULSE and TRACE with live data, browser control, media, screenshots, the latency HUD | It looks like the screenshots |
| **P5** | She works while he sleeps — job queue, alarms, email triage, morning digest, Telegram bridge, approval queue, command palette | The original promise, delivered |
| **P6** | She remembers and she defends — memory, semantic search, knowledge graph, Defender integration, **the download gate** | Trustworthy |
| **P7** | Companions, macros, spotlight, hardening audits, X automation, compact and ambient modes | Powerful |
| **P8** | Screen understanding, offline mode, timeline scrubber, proactive suggestions, learning from correction | Outstanding |
| **P9+** | Business and content automation, invoicing, crypto monitoring, deploys | Complete |

**If he stops at P5 he has a voice agent that recognises only him, controls his machine, and works overnight.** Everything after that is depth, not foundation.
