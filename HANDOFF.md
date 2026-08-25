# TESSA_CORE — handoff to a new chat

Paste this as the first message in the new chat, then carry on.

## What this is

**TESSA_CORE** — an always-on personal agent for Windows. One Python daemon
(`core/`) and two Electron surfaces: **Console** (`apps/console/`) and **Orb**
(`apps/orb/`, the voice sphere). Frozen WebSocket protocol on 127.0.0.1.

- Repo: `C:\dev\tessa` — public at `github.com/GeraldXtra/tessa`
- Machine: i5-7200U 2C/4T, 1366×768 @ 60 Hz, HD 620, metered connection
- She is **Tessa**. The system is **TESSA_CORE**. Renamed from Zoey/ZOEY_OS.
- Owner: **Gerald**. He owns all git operations; sessions run none.

## Why he is building this — read before prioritising anything

The stated reason, in his words, is to **relieve stress by having Tessa run his X
presence**: read the timeline, understand tweets, draft engaging replies in his
voice, and post on his account. Everything else serves that or serves his daily
work.

What that means for the queue:

- **The drafting voice is the load-bearing piece.** A reply engine that does not
  sound like him produces work he has to rewrite, which is worse than writing it
  himself. The voice profile is learned from his existing posts — that is the
  mechanism, and "tell the model not to sound like AI" is not.
- **He logs into X once, by hand, in a dedicated Chrome profile.** Playwright
  drives that already-authenticated session. **Never store his X password** —
  spec forbids it, and scripted login against X's device checks is the most
  fragile thing that could be built.
- **Posting under his name is 🔴 red-tier and stays there.** She drafts, he
  approves. Replies are generated from tweets he did not write, which are
  `Provenance.external` — the highest-risk category in his own threat model.
  Batched approval in the morning digest gets him nearly all the relief without
  handing an injection vector a publish button.
- Images land at P8 (vision). Video is not in the catalogue at all — a genuine
  gap he would be specifying from scratch.

## How we work

Two Claude Code sessions run in parallel. **Session 1** owns `core/` and
`apps/console/`. **Session 2** owns `apps/orb/`. Neither edits `CONTRACT.md`,
`packages/*` or `docs/*` without an explicit scoped exception.

I write prompts; Gerald pastes them; he brings back the report; I say what
landed and what was skipped, then write the next one. Every prompt is numbered,
ends with "Report on each by number, don't summarize. Then STOP and report",
demands measured numbers rather than "verified", and fences scope explicitly.

Before sending any prompt he asks me to go through it repeatedly for gaps. Do
that — it has caught real bugs every time, including in the last four rounds.

**Hard rules for every prompt:**

- Number every item. State the ownership boundary. Fence scope: "this and
  nothing more."
- Demand measured numbers, never "verified".
- **Suspect the instrument before the code.** This has been right more times
  than not on this project.
- Check every millisecond figure against **16.67 / 33.3 / 50.0**. The display is
  60 Hz; anything else is a mix or an artefact.
- Invite disagreement with reasoning, not compliance. When a session pushes back
  with a measurement, the measurement wins.
- **Never suggest agent fleets larger than 8.** His concurrency cap is 2.
- Kills go through `scripts/safeproc.py` and `kill_if_descends_from_me()` only.
  Session 1 once killed 37 of his VS Code processes by iterating an image name
  while writing "by PID".
- Every prompt starts by reading `runtime.json` for the daemon — never assume a
  pid, and say plainly if none is running.
- `npm install` is the one place the two sessions collide. Announce before
  running one; never `npm ci`; never delete `node_modules`.
- **Make sessions write numbers to disk as they go**, not at the end. Two of the
  last three Orb runs were interrupted and one compacted; `NUMBERS.md` on disk is
  the only reason nothing was lost.
- Prompts are long. Write them to a file and present it rather than pasting
  inline.

## Machine facts that override the older docs

- **MSVC and Rust ARE installed.** `CLAUDE.md` and `docs/TESSA_CORE-spec.md` both
  still say "No Rust, no MSVC — never `node-gyp`". **That constraint is stale.**
  It does not make `node-gyp` a good idea on a metered connection with limited
  disk, but it is no longer impossible, and a prompt should not refuse on those
  grounds. Reconcile the two docs when convenient.
- **He has a Gemini free-tier key and no Anthropic credit.** The brain runs on
  Gemini with adapters for Anthropic and local. **A live model call costs him
  nothing.** Session 1 once declined to prove the brain path citing "it costs
  money on his metered budget" — wrong on both counts, and the refusal should not
  be repeated.
- Disk and data are the real constraints, not toolchain.

## What works today

Voice in and out (Whisper, Piper, VAD, barge-in). Conversation sessions that
stay open until he closes them. 440-app index, 28 system tools, browser
automation, X integration. Gemini brain with adapters for Anthropic and local.
Persisted conversation memory. Authored personality in `tessa.md`. Approval
cards with editable payloads. Google Calendar (built, needs his OAuth trip).
Console: three shells, copy/paste with a guard, panes capped at 2, tabs, zoom,
file tree, watermark, **chat panel with provenance gutters**.

## His standing rulings — do not relitigate

- **Themes**: gold (default), magenta, cyan, violet, emerald, red. Amber, ember
  and yellow removed. Per-companion; the interface follows the active sphere.
- **Rails on the RIGHT**, drawers open right. Calendar permanent **bottom-left**.
  An approval card closes any open drawer and the drawer stays closed.
- **Three spheres** — main plus two companions, which keep their own colours.
- **No icon rail.** Type only.
- **Panes capped at 2**; tabs for more terminals.
- **`tcli`**, not zcli. (`zcli` appears **nowhere** in `CONTRACT.md` — §6.6 is
  entirely the deep-link grammar. Any prompt claiming otherwise is wrong.)
- **No fabricated data, ever.** Panels show what is real or say NO DATA.
- **Autostart**: daemon at login with `--voice`. He does not want to run it by
  hand. Orb open from boot too.
- **Wake phrase**: ONE model, trained on the full two-word phrase **`hey tessa`**
  — not the bare name, and not separate models per greeting. Transcription
  handles everything after the trigger, so no pre-roll buffer is needed. Greeting
  variants ("good morning Tessa" etc.) are **dropped**. One Colab run.
  Rationale: he says "Tessa" constantly — it is the project name — so a bare-name
  detector would fire all day; the two-word phrase kills that at the source.
- **Speaker verification is LAST in the queue.** Not a calibration problem — the
  enrolment path and the live path process audio differently. Do not relitigate
  its position. Consequence to state once and not argue: **until it is fixed, any
  voice in the room can wake her.**

## Live state, as of this handoff

- His stored Orb theme reads **`{"theme":"gold","v":2}`**
  (`%LOCALAPPDATA%\Tessa\orb-theme.json`). It was `red` before a `v:2` migration.
  **Unresolved: whether he changed it or the migration silently reset it.** If a
  migration reset it, the same class of bug is waiting for
  `console-settings.json` when packaging touches it.
- **Speaker verification is disabled** — the voiceprint was renamed to
  `owner.json.bak` because it rejected his own voice at ~0.2 against a 0.55
  threshold.
- The **chat panel is built but unusable until the daemon restarts** —
  `cmd.agent.message` has a handler now, but the running process predates it.
  **Session 1 cannot restart the daemon**: it is started from his own
  WindowsTerminal, so `kill_if_descends_from_me()` correctly refuses. This is
  permanent — every `core/` change needs a manual restart from him until
  autostart exists. **`tcli daemon restart` should be built for exactly this**,
  which makes it load-bearing rather than convenient.
- The Console now **subscribes to `transcript.*` and `agent.*` permanently**,
  whether or not a chat pane is open — JSON decoded on every turn with possibly
  nowhere to render it. Small but not zero. The fix is to subscribe on first chat
  pane open and `cmd.unsubscribe` on last close.
- His `console-settings.json` is at **version 4 with 23 bindings**. Every new
  keybinding needs a migration or it is a chord that silently does nothing.
- **CONTRACT — items 1 and 2 are applied in this repo copy; 3 and 4 are open:**
  1. ✅ `cmd.calendar.today` added to §5.1 with the `calendar.*` namespace.
     **Its element shape is marked pending verification against `core/`'s
     emitter** — check it and correct the contract to match the code if they
     differ; it is additive either way.
  2. ✅ `Theme` closed enum (§7.4), §9.3 the theme-token completeness gate, and
     §5.4 reconciled with its own changelog (four `ErrorCode` values were
     recorded there but never tabulated — spellings flagged for confirmation
     against `enums.json`).
     **`scripts/check-contract.mjs` still needs the §9.3 gate written** —
     `scripts/` is his, so neither session will do it. It would have caught the
     token break that cost both sessions a round.
  3. ⬜ **`cmd.agent.message` — the CODE is wrong, not the contract.** §5.1
     already specifies `{ companionId, text, attachments[]? }` →
     `res.agent.accepted { messageId }`. Session 1 built `{ text }` →
     `res.agent.message`, dropping `companionId` and `messageId` and renaming the
     response type. Renaming a type and removing a field are §7.3 **breaking**
     changes; Session 1 cited §7.2 and was wrong. **Fix by conforming the code**,
     not by changing the contract. `companionId` matters — three spheres and
     per-companion memory scoping are coming, and the Orb already sends it.
  4. ⬜ `cmd.calendar.today` still missing from `packages/protocol`'s PayloadMap.
     A code change, not a contract change.
- **§9's brand palette is stale.** It still carries the original orange accent
  (`--accent: #FF6B1A`, `--sphere-hot: #FF3B00`) and predates the six-theme
  ruling. Left untouched rather than changed silently — **his call**, and worth
  its own round because both surfaces build against those tokens.

## The queue

**Session 1** — the chat pane **landed**. Next: packaging + `tcli` + autostart.
The prompt exists as `console-tcli-packaging.txt` but **must not be pasted as-is**
— see the defects below. Then command blocks and search; then the capability
framework (wifi, bluetooth, VLC, downloads, installs); then the brain/personality
upgrade **and the X drafting voice**; then the wake phrase; then the
speaker-verification fix, last.

> **`console-tcli-packaging.txt` — three known defects. Fix before pasting:**
> 1. **Item 1d asks the session to stop the daemon. It cannot** — ancestry
>    refuses it. Either Gerald stops it, or test the no-daemon path against a
>    `runtime.json` copy carrying a dead pid, which exercises the real CONTRACT
>    §1 stale-file path.
> 2. **It claims CONTRACT §6.6 names `zcli`. It does not** — zero matches
>    anywhere in the file. The "propose a diff for the name" item chases nothing.
> 3. **`@lydell/node-pty` cannot load from inside an asar.** Without an
>    `asarUnpack` entry for it and its ConPTY helper binaries, the packaged
>    Console installs, launches, shows the whole UI — and every shell fails to
>    spawn, looking exactly like a grant-gate bug. The `utilityProcess` entry
>    point has the same class of path problem.
>
> Also missing: the npm-collision rule, a byte ceiling on the electron-builder
> download, a requirement that **uninstall must not delete `%LOCALAPPDATA%\Tessa`**
> (it holds settings, theme, runtime.json and the voiceprint backup), SmartScreen
> expectations for an unsigned installer, and the fact that `tcli` on PATH will
> not work in an already-open Explorer until it restarts. Section 3's regression
> list also predates the chat pane and does not include it. **Recommend splitting
> into Prompt A (packaging) and Prompt B (tcli + autostart).**

**Session 2** — the grey face and the lattice are both **fixed**. Currently mid-run
on the light round (`orb-light-rebalance-v3.txt`). After that: the Orb packaged
and autostarted.

**Wake requires the Orb packaged first.** "She wakes and the Orb window appears"
cannot cold-start Electron plus Three.js inside a 200 ms budget. Waking must
*show* a window resident in the tray since boot. Note the split: **hearing and
answering needs only the daemon** — that works before the Orb is packaged; only
the window appearing depends on packaging.

**Gerald owes two browser trips**, neither blocking yet:
- **Google Cloud OAuth** — blocks the moment the calendar panel is the work.
- **A Colab run** for `hey_tessa.onnx` — blocks at the wake phrase, second-to-last
  in Session 1's queue. **Budget for more than one run:** these models are
  usually trained on synthetic TTS samples, which may not match a Nigerian
  accent. If it rejects him that is a false-reject problem, and the fix is adding
  his own recordings — **not lowering the threshold.** He has already been burned
  once by a threshold that looked fine and was not.

## Findings that cost hours — do not rediscover

**On measurement instruments — this project's recurring failure mode:**

- **Aggregate metrics cannot see particle structure.** Coverage and mean
  luminance are identical whether sprites are discrete or fused. Measure a
  SINGLE particle: fwhm, gap, gap/fwhm, edge profile, count.
- **Single-particle photometry cannot see ARRANGEMENT either.** Every
  photometric number reads identically for an ordered lattice and a random
  scatter. That blind spot hid a dead lattice for several rounds.
- **`R` measures ANISOTROPY, not ORDER.** A perfect hexagonal lattice scores
  R = 0.526, not 1.0 — six equidistant neighbours make "nearest" a coin flip.
  A constant was fitted with R and shipped wrong. **Use local angular order
  (psi6, k=6 neighbours) instead**, always scored against synthetic Poisson and
  hexagonal controls run through the same code.
- **A golden-angle/Fibonacci sphere has NO GLOBAL PERIODICITY.** Fourier and
  autocorrelation tests return a false negative on a genuine lattice. Do not use
  them as the decider.
- **Nearest-neighbour spacing must be measured in a CENTRAL PATCH** (r ≤ 0.4R,
  foreshortening under 8%). Disc-wide, projection compresses spacing toward the
  limb regardless of arrangement and pushes a real lattice toward the random
  figure. 2-D Poisson gives sd/mean = 0.523.
- **The crescent trap: any global threshold on a reference image finds only the
  crescent**, which is 3–5× brighter than the face. It dragged the disc-centre
  detector 261 px left, four times running. The working detector is v5:
  luminance band-pass, floored local normaliser, radial-density radius.
- **Chroma cannot find face dots** — the face's band-passed chroma (max 2.58)
  sits below background noise (p90 3.83), because 4:2:0 destroys chroma on
  isolated dots. Luminance works (face p50 6.20 vs background 0.54). Chroma still
  separates the disc from UI.
- **Photographs of a screen carry a lifted black point and a camera tone curve.**
  Background-subtract every reference figure before computing any ratio, and
  remember a tone curve compresses highlights — making a reference look **more
  evenly lit than it is**.
- **Validate a detector against a hand measurement before trusting it**, and
  re-check the fit by eye per frame. One frame was excluded that way after its
  numbers looked fine.

**On the reference images:**

- **All 16 at `reference/v2/` are photographs** of a screen, JPEGs with a `.png`
  extension. Load by content, not extension.
- **Validated frames are image2 (835px), image5 (770px), image9 (776px).**
  **image3 is EXCLUDED from measurement** — its disc fit came out oversized and
  offset. It is fine to look at; it is the frame Gerald points to. It reads
  LISTENING.
- **Nearly every frame is SPEAKING; his captures are IDLE.** State-match before
  comparing outline or depth, or you are comparing a deformation to a base shape.
- **`FACE_SAT` was once fitted to a compression artefact** — 4:2:0 chroma
  subsampling averages an isolated face dot's colour with the black around it,
  while merged limb dots protect each other. That produced a false "face is
  desaturated" reading. Do not match reference face saturation (0.077) or limb
  saturation (0.540); both are JPEG artefacts.

**On the code:**

- **Six times** this repo has shipped code called by nothing — `executor.py`,
  `wait_for_silence`, speaker verification, `scrollback`, `pickShell`, the tab
  actions. Trace every validated value to the line that applies it.
- **A `var()` naming a missing token is guaranteed-invalid** and resolves to
  nothing, silently. Retiring a token is a cross-surface break. CONTRACT §9.3 now
  mandates the gate; `scripts/check-contract.mjs` still needs it written.
- **ConPTY renders the alternate screen itself** — testing for `?1049h` on
  Windows tests the wrong thing. Use `less` and watch scrollback restore.
- **cmd executes a multi-line paste on arrival**; PowerShell and Git Bash do
  not. The paste guard exists for that.
- **Neither Node nor Python sees a OneDrive placeholder's reparse bit.** Only
  `GetFileAttributesW`. And his OneDrive is entirely local — 0 of 4,000 files
  dehydrated — so the hydration risk is theoretical on this machine.
- **Zoom used to unmount the other pane and kill its shell.** A build running in
  a zoomed-away pane was destroyed silently, with a fresh prompt where the output
  had been. Caught because the PTY grant count read 2 where it should read 1.
  Fixed — hidden panes now keep their box and their process. **Grant count across
  zoom/unzoom is the regression test.**
- **Layout chords died with a chat pane focused** — only terminals registered a
  key handler, so `Ctrl+Shift+W` could not close the pane he was typing in. Fixed.
- **The audit chain has one known fork** at line 71 from two daemons
  overlapping. Not new, not tampering.

## Open questions — NOT findings, do not treat as settled

- **sd/mean and psi6 disagree about the reference.** psi6 says lattice
  (0.437–0.524 against Poisson's 0.375); sd/mean says the reference (0.260–0.271)
  is *more* disordered than the shipped build (0.227) — the opposite verdict.
  Session 2 weights psi6 because it averages over six neighbours and survives
  positional noise, and because merging pulls centroids. But its own pipeline
  control shows merging inflates sd/mean only from 0.037 to ~0.08, not to 0.26.
  **Something else is inflating it and it is unexplained.**
- **image9 may be over-detected.** Yield 1.18 (more dots than density predicts),
  the smallest fwhm (5.75 px) and the lowest psi6 (0.437) — the signature of
  noise or split components. Drop it and the reference reads 0.514–0.524, mapping
  to jitter ≈0.10–0.11 rather than the shipped 0.12.
- **Is psi6 state-independent?** Session 2 asserted it without measuring, but
  `states.ts` applies per-state `turbulence` (idle .022, speaking .03, thinking
  .07) which displaces particles exactly as lattice jitter does. Only image9 has a
  confirmed state chip. A probably-idle build may have been fitted to a
  probably-speaking reference.
- **Is the reference's even lighting real, or photographic?** Live question in the
  current light round: limb/face reads 1.42 in the reference against 2.12 in the
  build, but a camera tone curve compresses highlights and a lifted black point
  inflates both figures. `crescent peak−bg` is 58.06 in the reference against
  130.08 in the build — less than half the contrast while the face reads brighter,
  which is the signature of a raised black floor. **If it proves photographic, the
  correct answer is to change nothing.**

## Things that have gone wrong repeatedly

- **Network drops mid-run.** Roughly half of all sessions have been interrupted.
  Write resume prompts that carry the state, list what landed as "do not redo",
  and say "write the report first if context tightens".
- **Context exhaustion.** Sessions have run out entirely and compacted. When one
  compacts, the resume prompt must carry every measurement and ruling — the
  summary alone loses the numbers. **Files on disk survive compaction; context
  does not.** Point the session at its own `NUMBERS.md` and instrument scripts.
- **Stale builds.** Someone has measured a bundle that predated their own edits
  at least three times. Confirm build timestamps against the last source change.
  The Orb's renderer bundle is at `apps/orb/out/renderer/`, served by
  `electron-vite preview` (`shots.ps1:34`) — so mtime IS the right staleness
  check there. `C:\dev\tessa\out\` has never existed; a session once looked there
  and reported a missing renderer.
- **Attachments arriving empty.** Long inline pastes and some file uploads have
  reached me as blank. **If a report looks empty, check the file on disk before
  concluding anything** — it has been present and readable when the context copy
  was not.
- **Reports lost to a drop before they were written.** The report is the
  deliverable; the work is worth nothing he cannot read.
- **Two sessions on 2 cores contaminate any timing measurement.** Never run a
  frame-budget window while the other session is doing anything heavy.

## Session 2's instrument — reuse it, do not rebuild it

Under the temp scratchpad for this repo
(`…\Temp\claude\C--dev-tessa\<id>\scratchpad\`):

- **`NUMBERS.md`** — every measurement, appended as produced. ~222 lines.
- **`arrange.py`** — ~257 lines: `dots()`, `fit_disc()`, `disc_from_density()`,
  `structure()`, `aspect_of()`, `patch_psi()`, plus the moiré and synthetic
  Poisson / hexagonal / Fibonacci controls, and the reference-pipeline simulator
  (upscale → blur 1.2 px → JPEG 4:2:0 q75 → optional sensor noise).

**Search for them, never assume the path.** If they are gone, rebuilding them is
a full round on its own and should be prompted as one. A scratch file named
`struct.py` shadowed the stdlib module once — do not reintroduce that name.

`--force-sphere` slot 7 is the lattice jitter at runtime, so a jitter comparison
needs no rebuild.

## Where the surfaces stand

**Orb** — three spheres, eight rails on the right, calendar bottom-left, sphere at
40.2% of window width, ~15,600 particles at 30 fps, tier med (ladder: high 24,000
/ med 15,600 / low 6,800 — **low has never been measured**).

Two faults **closed**:
- The grey-white face was `FACE_SAT_DEFAULT = 0.45` mixing 55% white into every
  body particle via `sat = uFaceSat + (1-uFaceSat)*vFresnel` — the face got 0.45,
  the limb 1.0. Now 1.0 in `sphere-engine.ts` and `companions.ts`.
- The sphere was a **scatter, not a lattice**. The generator was always Fibonacci;
  `LATTICE_JITTER_DEFAULT = 0.40` at `sphere-engine.ts:836` was deliberately
  destroying its order — measured psi6 0.343 against synthetic Poisson's 0.375,
  i.e. *less* ordered than random. Now **0.12**, matching the reference's
  0.437–0.524.

Still open, both **photometric, not geometric** (proven — ordering moved the
crescent half-max by exactly zero):
- Face particle luminance **42.9** against the reference's 97.3
- Crescent half-max width **5.0%** against 22.5%
- Ribbon merging **matches exactly** at 27.5%
- Companion brightness has **never been measured**
- `ALPHA_MAX = 0.55` caps a single particle at 255 × 0.55² = **77**, below the
  reference's face. `bodyBright = 1.0`; at ≥1.25 the face pins on that ceiling.

**Console** — three shells with a picker, copy/paste with a multi-line guard,
panes capped at 2, tabs, zoom, file tree, watermark, theme following the Orb, and
a **chat pane** that reaches the same agent as voice: one shared conversation,
router first, same tiers, same injection fence, provenance gutters, red-tier
pointing at the Orb's approval card. Not yet packaged; launched with
`npm run start -w @tessa/console`.

**Daemon** — started by hand in a PowerShell window. Autostart is written into
the packaging prompt and not yet built.

## Tone

He is building this at 2am on a slow machine over a metered connection. Be direct
about what will not work rather than optimistic — he would rather hear that
something takes three prompts than be told it will be done by four o'clock.

When a session pushes back on my instruction with a measurement, **the measurement
wins.** In the last two rounds a session correctly refuted three of my hypotheses
and one of my theories about its own past instrument. That is the system working,
not a session being difficult.
