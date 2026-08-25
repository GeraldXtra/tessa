"""
core/voice/wake.py — "Hey Tessa", and the rules for when she is allowed to hear it.

────────────────────────────────────────────────────────────────────────────────
THE MODEL THIS SHIPS AGAINST IS NOT HER NAME, AND THAT IS DELIBERATE

openWakeWord ships six pretrained models: `alexa`, `hey_jarvis`, `hey_mycroft`,
`hey_rhasspy`, and two command models (`timer`, `weather`). **There is no
"Hey Tessa" and none can be made on this machine** — training needs the `full`
extra (torch + tensorflow-cpu 2.8.1 + datasets + deep-phonemizer) plus GB-scale
negative corpora, on two cores with no GPU over a metered link.

So this module is built and measured against `hey_jarvis` as a PROXY, because
the architecture and the per-frame cost are identical whatever the phrase is.
Swapping in a Colab-trained `hey_tessa.onnx` is ONE LINE IN settings.yaml
(`voice.wake.model`), never a code change. See `docs-reconciliation.md` §4 and
the Colab instructions in the report.

MEASURED ON THIS MACHINE (i5-7200U, 2C/4T), serially:
    detector alone, real time     0.72 s CPU / 29.9 s  =  0.6% of the machine
    detector on a live mic        0.72 s CPU / 30.1 s  =  0.6% of the machine
    per-frame inference           p50 5.63 ms · p95 9.31 ms · max 31.29 ms
                                  against an 80 ms frame budget
    beside a Whisper transcription that saturated the box at 77%:
                                  transcription 3.66 s -> 3.61 s (-1.5%, noise)
                                  0 of 46 detector frames late
That last line is the one that decided the feature: it never went deaf during
the heaviest thing this machine does.

────────────────────────────────────────────────────────────────────────────────
IT DOES NOT OPEN A STREAM. IT TAPS THE ONE THAT IS ALREADY OPEN.

Measured: a second `sd.InputStream` on the default device DOES work here — a
third and fourth do too, while the daemon already holds one, because the default
host API is MME and it shares. So sharing was never the blocker. It is still the
wrong design, for three reasons that outlive the measurement:

  1. `ArmedMicrophone`'s ring STOPS being fed while a segment is armed (the
     callback diverts into `_captured` and returns early). A detector on its own
     stream would keep firing DURING a segment and could re-trigger her while
     she is still listening to the last thing he said.
  2. Two streams are two device clients and two AGC state machines. The chain
     order requires one audio source of truth — the voiceprint must be scored
     through the same chain it was enrolled through, and noise suppression must
     be shared or it desynchronises them.
  3. Two callbacks are two more wakeups per block on two cores.

So `ArmedMicrophone` grew ONE hook and the detector hangs off it.

────────────────────────────────────────────────────────────────────────────────
WHAT SHE DOES WHEN SHE WAKES

Push-to-talk needs no acknowledgement because the keypress IS one. A wake word
has none, and spec §4 allows 200 ms for it.

DEFAULT: the state transition alone — `evt.agent.state` -> `listening`, which
the Orb already renders and which costs ~0 ms because the bus emits it anyway.

A chime is available (`voice.wake.chime: true`) and is OFF by default. When on it
is played BEFORE arming, and the ordering is not cosmetic:

    chime BEFORE arm  -> lands in the PRE-ROLL, which the VAD never reads
    chime AFTER  arm  -> lands in `_captured`, which the VAD DOES read

`_silence_loop` tracks `loudest` over `self._captured` only. A chime inside that
would raise `loudest`, which raises the relative silence threshold
(`max(floor_rms, loudest * 0.08)`), which makes her SLOWER to notice he stopped
talking. That is a regression in the one mechanism that took longest to get
right, so the chime is sequenced in front of the arm and never behind it.

────────────────────────────────────────────────────────────────────────────────
PROVENANCE — WHY A WAKE SEGMENT IS NOT A KEYPRESS

CONTRACT §6.2 makes `human` the only trusted provenance, and a push-to-talk
segment earns it honestly: a physical key was pressed, and nothing but the owner
can press it.

**A wake word cannot earn it on its own.** Anyone in the room, a television, or
a recording can say "Hey Tessa". So a wake-triggered segment is provenance
`human` ONLY once speaker verification has passed on the segment; until then it
is untrusted. That is exactly why verification scores the SEGMENT rather than
the sub-second wake phrase, and it is why a stranger can wake her but cannot
command her.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np

#: openWakeWord's native frame. 1280 samples = 80 ms at 16 kHz. Feeding it any
#: other size makes it re-buffer internally and the timing stops being legible.
FRAME = 1280
SAMPLE_RATE = 16_000

#: Default confidence to fire at.
#:
#: 0.5 is openWakeWord's own suggested operating point and is where the model
#: was calibrated. It is EXPOSED in settings.yaml rather than tuned here because
#: the right value depends on his room and his voice, and neither is knowable
#: from this file. The asymmetry to remember when he tunes it: a FALSE WAKE costs
#: him a sphere lighting up and a segment that transcribes to nothing, while a
#: MISSED WAKE costs him the feature. Prefer too sensitive over too deaf.
DEFAULT_THRESHOLD = 0.5

#: After firing, ignore the detector for this long.
#:
#: Without it one utterance of the phrase fires on several consecutive frames —
#: the model emits a high score for as long as the phrase is inside its window —
#: and she would arm, disarm and re-arm mid-sentence. 2.0 s is longer than the
#: phrase and shorter than any real follow-up command.
REFRACTORY_S = 2.0


@dataclass
class WakeEvent:
    """One firing, with the numbers that justify it."""
    phrase: str
    score: float
    at: float
    #: wall-clock ms from the frame arriving to the callback returning
    decide_ms: float


@dataclass
class WakeStats:
    frames: int = 0
    fires: int = 0
    suppressed_armed: int = 0
    suppressed_refractory: int = 0
    suppressed_asleep: int = 0
    infer_ms: list = field(default_factory=list)

    def describe(self) -> str:
        if not self.infer_ms:
            return f"{self.frames} frames, {self.fires} fires"
        s = sorted(self.infer_ms)
        p50 = s[len(s) // 2]
        p95 = s[int(len(s) * 0.95)] if len(s) > 1 else s[0]
        return (f"{self.frames} frames, {self.fires} fires, "
                f"infer p50 {p50:.2f} ms p95 {p95:.2f} ms, "
                f"suppressed: {self.suppressed_armed} armed / "
                f"{self.suppressed_refractory} refractory / "
                f"{self.suppressed_asleep} asleep")


class WakeDetector:
    """
    Continuous phrase detection over the microphone that is already open.

    Not a thread. `feed()` is called from the audio callback that already exists,
    so there is no second stream, no second AGC, and no extra wakeup per block.
    The inference is 5-9 ms against an 80 ms budget, which is why it is safe to
    run inline rather than hand off to a queue — a queue would add a hop and a
    thread for a job that finishes in a tenth of its own deadline.
    """

    def __init__(
        self,
        model_path: str | None = None,
        *,
        threshold: float = DEFAULT_THRESHOLD,
        refractory_s: float = REFRACTORY_S,
        on_wake: Callable[[WakeEvent], None] | None = None,
        is_armed: Callable[[], bool] | None = None,
    ) -> None:
        self.model_path = model_path
        self.threshold = threshold
        self.refractory_s = refractory_s
        self._on_wake = on_wake
        #: THE COEXISTENCE RULE, INJECTED. The detector asks the loop whether a
        #: segment is already open rather than tracking it itself, because two
        #: copies of "is she listening" is exactly how a state machine drifts.
        self._is_armed = is_armed or (lambda: False)

        self._buf = np.zeros(0, dtype=np.int16)
        self._last_fire = 0.0
        self._awake = True
        self.stats = WakeStats()
        self._model: Any = None
        self.load_error: str | None = None

    # ── lifecycle ────────────────────────────────────────────────────────────

    def load(self) -> bool:
        """
        Construct the ONNX model. Returns False and records why on failure.

        Never raises. A missing wake model must degrade to "push-to-talk still
        works" rather than taking the daemon down — he has a keypress that has
        never failed him and losing it over an optional feature would be absurd.
        """
        try:
            from openwakeword.model import Model
        except ImportError as exc:
            self.load_error = f"openwakeword not installed ({exc})"
            return False

        try:
            if self.model_path:
                p = Path(self.model_path)
                if not p.exists():
                    self.load_error = f"wake model not found: {p}"
                    return False
                self._model = Model(wakeword_models=[str(p)],
                                    inference_framework="onnx")
            else:
                # No path configured -> the bundled proxy, so the plumbing can
                # be exercised before his own model exists.
                self._model = Model(wakeword_models=["hey_jarvis"],
                                    inference_framework="onnx")
        except Exception as exc:  # noqa: BLE001
            self.load_error = f"{type(exc).__name__}: {exc}"
            return False
        return True

    @property
    def phrase(self) -> str:
        if self._model is None:
            return "(not loaded)"
        return next(iter(self._model.models.keys()), "(none)")

    # ── the off switch ───────────────────────────────────────────────────────

    def sleep(self) -> None:
        """
        "Stop listening." She stops evaluating; the stream stays open.

        The STREAM is deliberately not closed. `ArmedMicrophone` holds it open so
        push-to-talk keeps its pre-roll, and closing it here would silently
        remove the 1.0 s of recovered first-syllable that push-to-talk depends
        on — turning "stop listening to the wake word" into "make the key press
        worse", which is not what he asked for.
        """
        self._awake = False

    def wake_up(self) -> None:
        """
        Back on. NOT reachable by voice, and that is not an oversight.

        If she is not listening for a phrase, a phrase cannot turn her back on;
        anything that could would mean she never really stopped. The way back is
        the push-to-talk chord, which is always live, or `voice.wake.enabled` in
        settings.yaml plus a restart.
        """
        self._awake = True

    @property
    def awake(self) -> bool:
        return self._awake

    # ── the hot path ─────────────────────────────────────────────────────────

    def feed(self, block: np.ndarray) -> WakeEvent | None:
        """
        Called from the microphone callback with whatever block size the driver
        gave us. Buffers to exact 80 ms frames and evaluates each.

        MUST NOT RAISE. This runs inside the audio callback; an exception here
        kills the stream and takes push-to-talk with it.
        """
        # EVERYTHING IS INSIDE THE TRY, INCLUDING THE GUARDS.
        #
        # `self._is_armed()` used to be called above this block, outside the
        # exception handler — and it is an INJECTED CALLABLE, so it is the one
        # thing here most able to raise. A test that made it throw took the whole
        # of `feed` down, which in the daemon means the audio callback, which
        # means the stream, which means PUSH-TO-TALK. The docstring promised this
        # could not happen while the code allowed it.
        try:
            if self._model is None or not self._awake:
                if self._model is not None:
                    self.stats.suppressed_asleep += 1
                return None

            # THE SEGMENT OWNS THE MICROPHONE WHILE IT IS OPEN.
            #
            # Item 1e's second case: the wake word firing while a segment is
            # already open. If he says "Hey Tessa, remind me to tell Tessa
            # about..." the phrase occurs INSIDE his own command, and without
            # this the detector would fire mid-sentence, re-arm, and cut his
            # sentence in half. A segment that is already open is already
            # listening to him; there is nothing to wake.
            if self._is_armed():
                self.stats.suppressed_armed += 1
                return None

            self._buf = np.concatenate([self._buf, block.reshape(-1)])
            fired: WakeEvent | None = None

            while len(self._buf) >= FRAME:
                frame = self._buf[:FRAME]
                self._buf = self._buf[FRAME:]

                t0 = time.perf_counter()
                scores = self._model.predict(frame)
                infer_ms = (time.perf_counter() - t0) * 1000.0
                self.stats.frames += 1
                self.stats.infer_ms.append(infer_ms)

                phrase, score = max(scores.items(), key=lambda kv: kv[1])
                if score < self.threshold:
                    continue

                now = time.perf_counter()
                if now - self._last_fire < self.refractory_s:
                    self.stats.suppressed_refractory += 1
                    continue

                self._last_fire = now
                self.stats.fires += 1
                fired = WakeEvent(phrase=phrase, score=float(score), at=now,
                                  decide_ms=infer_ms)
                # DRAIN THE BUFFER ON A FIRE. Frames still queued behind this one
                # contain the tail of the same phrase and would be evaluated
                # against a model that has already decided.
                self._buf = np.zeros(0, dtype=np.int16)
                if self._on_wake is not None:
                    self._on_wake(fired)
                break

            return fired
        except Exception:  # noqa: BLE001
            # Deliberately silent and deliberately total. A detector that throws
            # inside the audio callback would stop the stream, and the stream is
            # push-to-talk's as well as the detector's.
            return None


def chime(duration_s: float = 0.08, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """
    A short two-tone blip, generated rather than shipped.

    Zero bytes over the connection and no asset to lose. Raised-cosine envelope
    because a square-edged tone clicks, and a click through his speakers is a
    transient the microphone will happily record.

    Returns float32 in [-1, 1], the shape AudioBus.speak already takes.
    """
    n = int(duration_s * sample_rate)
    t = np.arange(n) / sample_rate
    tone = 0.5 * np.sin(2 * np.pi * 880.0 * t) + 0.3 * np.sin(2 * np.pi * 1320.0 * t)
    envelope = 0.5 * (1 - np.cos(2 * np.pi * np.arange(n) / max(1, n - 1)))
    return (tone * envelope * 0.25).astype(np.float32)
