# Zoey Orb — state of the surface

Written for someone who has not seen this code. Facts and numbers, not history.
Last updated after the approval card and the theme system landed.

The Orb is an Electron voice surface: a particle sphere, a five-rail drawer, and
a HUD. It talks to the Python daemon in `core/` over one WebSocket held in the
**main** process (CONTRACT §2.3) — the renderer has no socket and no token.

---

## Works, and is measured

| Thing | State |
|---|---|
| **Window** | Clean launch fills the work area (content `1366x720`). Geometry persists across launches; launches carrying dev flags neither read nor write it. |
| **Rails** | Five (PULSE · SENTINEL · FLOW · INTEL · TRACE). PULSE and SENTINEL carry live data; the other three are `NO DATA` because their sources do not exist yet. |
| **HUD (§R.2)** | Top bar (state, connection, uptime, spend, mic), notification stack, under-sphere line. The stack and the line render **no DOM node at all** when empty. |
| **Transcript** | `evt.transcript.message` renders in TRACE and under the sphere with provenance gutters. Long answers show the first sentence plus `+N words`; TRACE is one line per row with opt-in expansion and follows the newest entry only if already scrolled to the bottom. |
| **Six states** | Driven by `evt.agent.state`. Arrival-to-drawn measured at **3.9–29.4 ms** over 10 pairings, against spec §4's 80 ms p95. |
| **State dwell** | Every state is visible for at least **400 ms**. Queue cap 3 (worst-case 1.6 s lag), drops the middle of a burst keeping first and last, collapses consecutive duplicates. 11/11 unit tests on a fake clock. In normal operation `queuedMs` is **0** — real changes arrive seconds apart, so the §4 budget is untouched. |
| **Heartbeat pulse** | Equatorial band, fired by `evt.daemon.health` arrival, never by a timer. Resting brightness equals the unlit shell to **−0.00%** — it leaves nothing behind when beats stop. |
| **Colour temperature** | `uCoolMix` tracks the daemon's `cpuPct`. This is the sphere's only colour language besides state. |
| **Push-to-talk** | `cmd.voice.pushToTalk { action }`. **Toggle** (default) holds the global chord `Ctrl+Alt+Shift+Space`; **hold** (`--ptt-mode=hold`) releases the chord and works focus-only, because `globalShortcut` has no key-release callback. A claim is capped at **90 s**. The indicator lights only from a daemon `res.ok`, never local intent. |
| **Dev driver** | `--dev-drive=` with `click` · `wait` · `state` · `type` · `dump` · `key` · `respond`, all through the real handlers. `type` goes via the prototype value setter so React's `onChange` actually fires; `dump` reads an element back into the process log so a claim can be checked against a recorded value; `respond` calls the bridge directly, bypassing the card's own guard, which is the only way to test main's one-shot rule. No foreground, no synthetic input. |
| **Probes** | `gl.readPixels` from inside the renderer: `full` / `column` / `limb` / `centre`. Five reads of a frozen sphere agree to **0.000 px**. |
| **Resource aura (§R.1)** | `.stage::before` scale+opacity driven from `evt.daemon.health.cpuPct`, quantised at 0.05 so nothing animates at rest, flattened to zero on disconnect AND on 15 s of heartbeat silence. Clamp alpha 0.70-1.00 / scale 0.97-1.18. **Measured invisible across the 0-1.2% cpuPct the daemon shows at idle (1-3 of 255); visible at the 24% observed during daemon startup.** Dormant by design, not dead. |
| **Depth shading (§R.1)** | A view-depth term on alpha (not tint), `uDepthFar` 0.42, `--force-depth=<0..1>` to override; 1.0 restores the pre-depth shell. Reads as volume in the first capture. Scales every state by 0.602-0.634, so it cannot reorder the six signatures, and it touches no position — turbulence, spin and breath are bit-identical. |
| **Frame budget** | MED, 8,000 particles, focused, at `thinking`, quiet machine: **cost p50 0.10 / p95 0.20 ms · raf 16.7/17.0 · shown 33.3/33.6 · 30.0 fps**. Budget is 12 ms. |
| **Themes** | Five (cyan · amber · violet · emerald · ember), `Ctrl+Shift+C/A/V/M/E`, persisted to `orb-theme.json`. Accent-derived values are injected with `setProperty` on `documentElement` — see the header of `renderer/theme.ts`; **tokens.json is not authoritative at runtime for those five properties**. Alarm colours (`--status-error/-warn/-active/-idle`) are deliberately excluded and do not theme. Stage background measured **`#000000`** in all five. |
| **Approval card** | §R.2 floating card for `evt.permission.request`. Editable payload sent as CONTRACT §5.1's `editedArgs` (only the changed keys, 16 KB cap shown live), explicit APPROVE/REJECT with no default and no timeout-approves, stacking at 3 visible, one-shot enforced in **main**. Daemon refusals render on the card and a rejected edit **keeps his text**. Frame shape confirmed on the wire against a live daemon; see "unlit" for what is still unproven. |
| **Approval lifecycle** | A pending request **survives this surface's disconnect** — cards stay, go un-actionable, and say the request is still live. It does **not** survive a daemon restart, detected by comparing the daemon instance (`pid@startedAt` from runtime.json) across handshakes, because `res.hello`'s `sessionId` is per-connection and cannot answer "same daemon?". The unchanged branch is proven against a live daemon. |

---

## Built but UNLIT — deliberately dark, not broken

- **Edit-then-approve has never executed end to end, and cannot be driven from here.** Session 1 landed the handler (`core/server.py:1069 _h_permission_respond`) and the `editedArgs` field, and the frame shape is confirmed on the wire. What is missing is a way to CREATE a pending request without speaking: the red gate fires only inside a voice turn, `cmd.agent.message` is in `KNOWN_COMMANDS` but has no handler, and the only non-voice trigger is `--inject-wav`, a daemon **startup** flag. A surface cannot restart the daemon. Until someone speaks a red-tier command with an Orb attached, the last hop is unproven.
- **The card cannot tell him external content was in context.** `PendingApproval.external_at_request` exists (`core/brain/approvals.py:84`) and reaches the audit log, but it is **not in the `evt.permission.request` payload** (`approvals.py:213-221`). So the one §6.2 fact most relevant to authorising a red action — was a web page in the room when this was requested — is invisible on the card. Needs a §4.1 additive field.
- **SENTINEL status colour** — mechanism complete, `currentSentinelSource()` returns `null` unconditionally (`rails/sentinel-status.ts:67`). The rail can therefore never show red today. Waits on Defender integration (P6).
- **Notification stack** — complete. Has shown exactly one real message (a global-shortcut registration failure). Waits on `evt.notification`.
- **Provenance gutter** — only three of six values have tokens (`prov-human`, `prov-program`, `prov-agent`). `schedule`, `external` and `system` fall back to the *program* tint, which is the untrusted side, so the fallback is safe but wrong. **`external` is the prompt-injection category and should not stay quiet** — needs a `tokens.json` addition, which is Gerald's to approve.
- **`backdrop-filter: blur(12px)` is INERT on this machine.** §R.7 mandates it on the drawer and the approval card uses it too. Measured across the sphere's silhouette inside the card: luminance jumps **3.56x in 2 pixels** and individual ~2px particles stay resolvable. A 12px blur would smear that over ~24px. Consequence: the card is a 72%-opaque panel with a sharp particle field straight through it — its interior is **1.6x brighter** where the sphere sits behind, and the APPROVE glyphs measure **3.40:1** against their local background, under WCAG AA's 4.5:1. Raising `--panel` opacity for the approval card would fix it; it is a readability defect on a security surface and Gerald's to rule on.
- **Waveform ribbon (§R.1)** — not built. There are **zero `evt.voice.*` broadcast sites in `core/`**, so a ribbon would be animating nothing. A §4.3 payload diff is proposed and awaiting a ruling.
- **ACTIVE PTY GRANTS** — always `NO DATA`. `evt.pty.sessions` is broadcast at exactly one site, the end of `_h_pty_report`, so it only fires when the Console reports and never on subscribe. Daemon-side gap; the Orb's subscription is correct.

---

## Tried and REJECTED — do not repeat

- **Three `thinking` intensification levers** — turbulence amplitude (+35%, imperceptible, and deepens deformation), noise clock rate (2.2×, undetectable by instrument or eye), spin rate (0.34→0.75 rad/s, measured working, verdict: *"It's not working harder at all. Just spinning."*). All three are **rates**, and a rate cannot convey elapsed time to a viewer with no reference to compare against. See the block above `THINKING_TAU_MS` in `sphere-engine.ts`. **This sphere cannot express duration.**
- **Screen-capture measurement (GDI `CopyFromScreen`)** — photographs whatever owns the foreground; once returned the owner's browser instead of the Orb. Replaced by `webContents.capturePage()`.
- **Synthetic input (`keybd_event` / `SetCursorPos`)** — needs the foreground, which on a shared machine is a coin toss; cost more hours than any measurement here. Replaced by the dev driver.
- **Finding the window by `MainWindowTitle`** — empty until first paint, and it once resolved to *another user's* Orb. Use ancestry from a recorded launcher PID.
- **`--stop-beats-after` takes SECONDS.** Passing `25000` means seven hours, and the test silently proves nothing because the block never fires. Cost two runs.
- **Comparing `blocked`-state probe sums across launches that reached it differently.** `blocked` freezes at whatever rotation it had; a run that cycled five states first is a different frozen configuration from one launched straight into it. Enter it the same way in every leg or the numbers are not comparable.
- **Matching a keyboard shortcut on `event.code` alone.** Done twice now. `code` comes from the hardware scancode, and synthetic input — on-screen keyboards, remote desktop, accessibility tools, `keybd_event` — arrives with scancode 0 and no usable `code`. The theme shortcuts were written that way, took the foreground, and did nothing. Match `code` **then** `key`; see `themeForKey()`.

---

## Colour — three things that were measured, not assumed

- **The sphere never showed its own token colours until now.** `tokenColor()` used
  `Color.set()`, which converts sRGB→linear; the fragment shader is a raw
  `ShaderMaterial` writing `gl_FragColor` with **zero** `colorspace_fragment`
  chunks, so nothing encoded back. Fixed with `setStyle(raw, LinearSRGBColorSpace)`.
  Verified by sampling the captures: every theme's rendered core is **6–32** RGB
  units from its declared token and **20–131** from what the old path would have
  emitted. Ember: declared `FF9E7A`, measured `FFA377`, old path `FF5732`.
- **The five supplied palettes are not on one ladder.** Contrast against black,
  measured: cyan core 19.67:1, amber 19.70, emerald 13.78, ember 10.42, violet
  **7.72**. Violet cannot reach cyan's brightness and stay violet — blue carries
  only 0.0722 of luminance — and on screen violet is visibly about half as bright
  as cyan. This is the owner's call, and the numbers are in `tokens.json`.
- **Ember collides with alarm red.** `--accent` under ember is `#E2603C`;
  `--status-error` is `#FF3B00`. Hue 13° against 14°, **ΔE2000 6.9 as rendered**
  — against 36.1 for amber, 47.6 violet, 57.8 cyan, 66.2 emerald. The tier WORD
  (§R.7) still distinguishes them; the colour-only signals (the 3px mic rule, the
  rail's 2px marker) do not. `#FF9163` would separate to ΔE 15.8.

## BASELINES INVALIDATED BY THE DEPTH TERM — read before comparing anything

The depth term scales total sphere brightness to **~0.62x** its former value. So:

- **Every `sum` / `lit` figure from `probeFrame` taken before 2026-08-15 evening is
  no longer comparable.** Total brightness at `blocked` went 3,746,270 -> 2,289,506.
- **The five theme UI captures reported the previous run show the PRE-depth sphere.**
  Comparing one of those to a current screenshot compares two different shells.
- **NOT invalidated: the colour-space verification.** At `vDepth = 0` the depth
  factor is exactly 1.0, so the NEAREST particles are untouched at any setting —
  brightest-pixel-versus-declared-token measurements still hold.
- **NOT invalidated: the card contrast figures.** The card is opaque; what is
  behind it cannot reach it, measured identical with the aura at rest and at max.
- **NOT invalidated: frame timing.** Re-measured after both changes, unchanged.

## Instrumentation notes you cannot recover from the source

- **Total brightness is conserved under rigid rotation.** `sum` cannot measure motion; it once called 12 of 318 visibly-moving frames "stalled". Use `pixelDelta`.
- **Differencing metrics saturate.** At the disc centre spin alone moves a particle ~2.6 px per frame against a 2–3 px particle, so the field is decorrelated regardless of what else changes. The **limb** is where rotation contributes least; the **centre** is where it contributes most. Pick the region for the question.
- **`probeFrame` calls `step(0)`**, which advances no clock — so it cannot resolve anything below the 33 ms frame interval, whatever the sampling rate.
- **`capturePage()` fails with `UnknownVizError` on an occluded window.** Chromium stops producing frames for one. `--capture-every` disables `CalculateNativeWinOcclusion` for exactly this reason.
- **A still frame cannot show a rate change.** Captures answer "did the shape deform", never "is it faster".
- **`focused` is sticky-false for a whole 120-frame window**, and unfocused windows fill at 10 fps — so the overlay can keep saying "unfocused" for ~12 s after focus returns. Windows marked `focused=true` are genuinely clean; discarding on `false` is conservative, not wrong.

---

## What I would do next, in order

1. **Land the daemon half of the approval card.** It is the only thing standing between five fully-built red tools and a working one: a `cmd.permission.respond` handler, a `resolve()` on `ApprovalGate`, and an `evt.permission.resolved` broadcast. Nothing in `apps/orb` needs to change when it arrives.
2. **Close the daemon-side gaps that make finished UI look broken** — `evt.pty.sessions` needs a snapshot on subscribe, and `idle` is still never emitted when speech ends, so the sphere sits in `speaking`. Both are `core/`. Neither is a large change and both currently read as Orb bugs.
3. **Add the three missing provenance tokens**, `external` first. The gutter is the surface's only defence against model-proposed text being mistaken for the owner's, and a third of its vocabulary is missing.
4. **Leave duration alone unless it is worth new UI.** Three sphere levers have failed for a structural reason. If Gerald needs to see how long she has been thinking, it wants an affordance *outside* the sphere with an absolute reference — not a fourth sphere parameter.
5. **Exercise a packaged build.** Everything here has run under `electron-vite preview`. The production CSP path (`connect-src 'none'`), `app.isPackaged` gating, and the absence of the dev flags have never been tested end to end.
6. **Verify the reduced-motion path.** `prefers-reduced-motion` short-circuits the render loop to a single static frame. It is written and has never been run.
