# Zoey Orb — state of the surface

Written for someone who has not seen this code. Facts and numbers, not history.
Last updated after the `thinking` intensification work was abandoned.

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
| **Dev driver** | `--dev-drive=click:<sel>;wait:<ms>;state:<agentState>` runs real handlers via `HTMLElement.click()`. Five rails opened and captured in **28.5 s** with no foreground, no synthetic input, no maximise. |
| **Probes** | `gl.readPixels` from inside the renderer: `full` / `column` / `limb` / `centre`. Five reads of a frozen sphere agree to **0.000 px**. |
| **Frame budget** | MED, 8,000 particles, focused, at `thinking`, quiet machine: **cost p50 0.10 / p95 0.20 ms · raf 16.7/17.0 · shown 33.3/33.6 · 30.0 fps**. Budget is 12 ms. |

---

## Built but UNLIT — deliberately dark, not broken

- **SENTINEL status colour** — mechanism complete, `currentSentinelSource()` returns `null`. Waits on Defender integration (P6).
- **Notification stack** — complete. Has shown exactly one real message (a global-shortcut registration failure). Waits on `evt.notification`.
- **Provenance gutter** — only three of six values have tokens (`prov-human`, `prov-program`, `prov-agent`). `schedule`, `external` and `system` fall back to the *program* tint, which is the untrusted side, so the fallback is safe but wrong. **`external` is the prompt-injection category and should not stay quiet** — needs a `tokens.json` addition, which is Gerald's to approve.
- **Waveform ribbon (§R.1)** — not built. There are **zero `evt.voice.*` broadcast sites in `core/`**, so a ribbon would be animating nothing. A §4.3 payload diff is proposed and awaiting a ruling.
- **ACTIVE PTY GRANTS** — always `NO DATA`. `evt.pty.sessions` is broadcast at exactly one site, the end of `_h_pty_report`, so it only fires when the Console reports and never on subscribe. Daemon-side gap; the Orb's subscription is correct.

---

## Tried and REJECTED — do not repeat

- **Three `thinking` intensification levers** — turbulence amplitude (+35%, imperceptible, and deepens deformation), noise clock rate (2.2×, undetectable by instrument or eye), spin rate (0.34→0.75 rad/s, measured working, verdict: *"It's not working harder at all. Just spinning."*). All three are **rates**, and a rate cannot convey elapsed time to a viewer with no reference to compare against. See the block above `THINKING_TAU_MS` in `sphere-engine.ts`. **This sphere cannot express duration.**
- **Screen-capture measurement (GDI `CopyFromScreen`)** — photographs whatever owns the foreground; once returned the owner's browser instead of the Orb. Replaced by `webContents.capturePage()`.
- **Synthetic input (`keybd_event` / `SetCursorPos`)** — needs the foreground, which on a shared machine is a coin toss; cost more hours than any measurement here. Replaced by the dev driver.
- **Finding the window by `MainWindowTitle`** — empty until first paint, and it once resolved to *another user's* Orb. Use ancestry from a recorded launcher PID.

---

## Instrumentation notes you cannot recover from the source

- **Total brightness is conserved under rigid rotation.** `sum` cannot measure motion; it once called 12 of 318 visibly-moving frames "stalled". Use `pixelDelta`.
- **Differencing metrics saturate.** At the disc centre spin alone moves a particle ~2.6 px per frame against a 2–3 px particle, so the field is decorrelated regardless of what else changes. The **limb** is where rotation contributes least; the **centre** is where it contributes most. Pick the region for the question.
- **`probeFrame` calls `step(0)`**, which advances no clock — so it cannot resolve anything below the 33 ms frame interval, whatever the sampling rate.
- **`capturePage()` fails with `UnknownVizError` on an occluded window.** Chromium stops producing frames for one. `--capture-every` disables `CalculateNativeWinOcclusion` for exactly this reason.
- **A still frame cannot show a rate change.** Captures answer "did the shape deform", never "is it faster".
- **`focused` is sticky-false for a whole 120-frame window**, and unfocused windows fill at 10 fps — so the overlay can keep saying "unfocused" for ~12 s after focus returns. Windows marked `focused=true` are genuinely clean; discarding on `false` is conservative, not wrong.

---

## What I would do next, in order

1. **Close the daemon-side gaps that make finished UI look broken** — `evt.pty.sessions` needs a snapshot on subscribe, and `idle` is still never emitted when speech ends, so the sphere sits in `speaking`. Both are `core/`. Neither is a large change and both currently read as Orb bugs.
2. **Add the three missing provenance tokens**, `external` first. The gutter is the surface's only defence against model-proposed text being mistaken for the owner's, and a third of its vocabulary is missing.
3. **Leave duration alone unless it is worth new UI.** Three sphere levers have failed for a structural reason. If Gerald needs to see how long she has been thinking, it wants an affordance *outside* the sphere with an absolute reference — not a fourth sphere parameter.
4. **Exercise a packaged build.** Everything here has run under `electron-vite preview`. The production CSP path (`connect-src 'none'`), `app.isPackaged` gating, and the absence of the dev flags have never been tested end to end.
5. **Verify the reduced-motion path.** `prefers-reduced-motion` short-circuits the render loop to a single static frame. It is written and has never been run.
