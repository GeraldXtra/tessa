"""
core/bus.py — the audio bus. ONE exclusive speaker (spec §5.2).

RULE 1 OF §5.2: the owner must never have to talk over her.

That is not a politeness feature. An assistant that keeps talking while you are
trying to interrupt it trains you to wait for it, and an assistant you have to
wait for is one you stop using. So push-to-talk does not queue behind speech and
does not duck it — it STOPS it, and the measured deadline is 120 ms.

The speaker is a single exclusive resource. Nothing here mixes streams: if she
is speaking and the owner presses the key, her audio is cut, not faded, because
a fade is 200 ms of her still talking over him.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable

import numpy as np
import sounddevice as sd

LEVEL_FLOOR_DB = -50.0
LEVEL_CEIL_DB = -6.0


def level_of(block: np.ndarray) -> float:
    if block.size == 0:
        return 0.0
    x = block.astype(np.float64)
    rms = float(np.sqrt(np.mean(x * x))) / 32768.0
    if rms <= 0.0:
        return 0.0
    db = 20.0 * math.log10(rms)
    level = (db - LEVEL_FLOOR_DB) / (LEVEL_CEIL_DB - LEVEL_FLOOR_DB)
    return round(min(1.0, max(0.0, level)), 3)


class Playback:
    def __init__(self, samples: np.ndarray, sample_rate: int) -> None:
        self.samples = samples
        self.sample_rate = sample_rate
        self.done = threading.Event()
        self.lock = threading.Lock()
        self.cut = False
        self.announced = False
        self.final_sent = False
        self.reported = False
        self.held: list[tuple[float, int, float | None]] = []
        self.seq = 0
        self.played = 0
        self.t_first: float | None = None
        self.t_end: float | None = None
        self.calls: list[float] = []
        self.frames: list[int] = []
        self.leads: list[float] = []

    @property
    def duration_s(self) -> float:
        return len(self.samples) / self.sample_rate if self.sample_rate else 0.0

    def summary(self) -> dict:
        gaps = sorted((b - a) * 1000.0 for a, b in zip(self.calls, self.calls[1:]))
        leads = sorted(x * 1000.0 for x in self.leads)
        frames = self.frames
        mode = max(set(frames), key=frames.count) if frames else 0

        def pct(values: list[float], q: float) -> float | None:
            return round(values[min(len(values) - 1, int(q * len(values)))], 1) if values else None

        return {
            "buffers": len(frames), "frames": mode, "rate": self.sample_rate,
            "periodP50Ms": pct(gaps, 0.5), "periodMaxMs": round(gaps[-1], 1) if gaps else None,
            "leadP50Ms": pct(leads, 0.5), "levels": self.seq,
            "audioS": round(self.duration_s, 3),
            "playedS": round(self.played / self.sample_rate, 3) if self.sample_rate else 0.0,
            "cut": self.cut,
        }


class AgentState(str, Enum):
    """Mirrors CONTRACT's evt.agent.state. Session 2's sphere renders these."""
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    #: A tool is running. Local tools flash past; browser automation takes real
    #: seconds, and that is when he needs to see something other than the state
    #: he was already in.
    WORKING = "working"


@dataclass
class BargeIn:
    """Measured interruption, so the 120 ms budget is a figure and not a hope."""
    requested_at: float
    stopped_at: float

    @property
    def latency_ms(self) -> float:
        return (self.stopped_at - self.requested_at) * 1000.0


class AudioBus:
    """
    Exclusive ownership of the output device, with interruptible playback.

    Playback runs on an OutputStream callback rather than `sd.play(blocking=True)`
    precisely so it can be stopped mid-buffer. `sd.play` with blocking would make
    barge-in impossible by construction — the call does not return until the
    audio is finished, which is the one thing that must not be true.
    """

    def __init__(self, on_state: Callable[[AgentState], None] | None = None,
                 on_level: Callable[[float, int, float | None, bool, bool], None] | None = None,
                 on_done: Callable[[Playback], None] | None = None) -> None:
        self._lock = threading.Lock()
        self._stream: sd.OutputStream | None = None
        self._samples: np.ndarray | None = None
        self._pos = 0
        self._done = threading.Event()
        self._stopping = False
        self._on_state = on_state
        self._state = AgentState.IDLE
        self._on_level = on_level
        self._on_done = on_done
        self._play: Playback | None = None

    # ── state ────────────────────────────────────────────────────────────────

    @property
    def state(self) -> AgentState:
        return self._state

    def set_state(self, state: AgentState, detail: dict | None = None) -> None:
        """
        CONTRACT §4.1 `evt.agent.state`, which already declares `detail?`.

        THE DEDUPE IS ON STATE ONLY, DELIBERATELY. Two consecutive `working`
        transitions with DIFFERENT detail are two different facts — "working:
        fs.list" then "working: fs.delete" — and swallowing the second would
        leave the Orb showing the wrong target for the rest of the turn. That is
        the safety dimension Session 2 asked for: the difference between "she is
        working" and "she is about to delete that file".
        """
        if state is self._state and detail is None:
            return
        self._state = state
        if self._on_state is not None:
            try:
                self._on_state(state, detail)
            except TypeError:
                # A caller that predates `detail` still works unchanged.
                self._on_state(state)

    @property
    def is_speaking(self) -> bool:
        return self._stream is not None and not self._done.is_set()

    @property
    def playing(self) -> Playback | None:
        return self._play if self.is_speaking else None

    # ── playback ─────────────────────────────────────────────────────────────

    def _callback(self, outdata: np.ndarray, frames: int, _t: object, _s: object) -> None:
        buf = self._samples
        play = self._play
        if buf is None or self._stopping:
            outdata[:] = 0
            raise sd.CallbackStop
        end = self._pos + frames
        chunk = buf[self._pos:end]
        if len(chunk) < frames:
            outdata[: len(chunk), 0] = chunk
            outdata[len(chunk):] = 0
            self._pos = len(buf)
            self._meter(play, outdata, frames, _t)
            raise sd.CallbackStop
        outdata[:, 0] = chunk
        self._pos = end
        self._meter(play, outdata, frames, _t)

    def _meter(self, play: Playback | None, outdata: np.ndarray, frames: int, t: object) -> None:
        if play is None:
            return
        try:
            now = time.perf_counter()
            level = level_of(outdata[:, 0])
            dac = float(getattr(t, "outputBufferDacTime", 0.0) or 0.0) if t is not None else 0.0
            cur = float(getattr(t, "currentTime", 0.0) or 0.0) if t is not None else 0.0
            lead = dac - cur if dac > 0.0 and cur > 0.0 and 0.0 <= dac - cur < 1.0 else None
            if play.t_first is None:
                play.t_first = now
            play.calls.append(now)
            play.frames.append(frames)
            play.played = min(len(play.samples), play.played + frames)
            if lead is not None:
                play.leads.append(lead)
            with play.lock:
                if play.final_sent:
                    return
                play.seq += 1
                if play.announced:
                    self._emit_level(play, level, play.seq, lead, False)
                else:
                    play.held.append((level, play.seq, lead))
        except Exception:
            pass

    def _emit_level(self, play: Playback, level: float, seq: int, lead: float | None, final: bool) -> None:
        if self._on_level is None:
            return
        try:
            self._on_level(level, seq, lead, final, play.cut)
        except Exception:
            pass

    def _announce(self, play: Playback) -> None:
        with play.lock:
            play.announced = True
            held, play.held = play.held, []
            if play.final_sent:
                return
            for level, seq, lead in held:
                self._emit_level(play, level, seq, lead, False)

    def _final(self, play: Playback) -> None:
        with play.lock:
            if play.final_sent:
                return
            play.final_sent = True
            play.t_end = time.perf_counter()
            if not play.announced:
                play.held = []
                return
            self._emit_level(play, 0.0, play.seq + 1, None, True)

    def _report(self, play: Playback) -> None:
        with play.lock:
            if play.reported:
                return
            play.reported = True
        if self._on_done is not None:
            try:
                self._on_done(play)
            except Exception:
                pass

    def speak(self, samples: np.ndarray, sample_rate: int, blocking: bool = False) -> None:
        """
        Take the speaker. Any current speech is stopped first — one speaker.

        SHE MUST GO IDLE WHEN THE AUDIO ENDS. She previously emitted `speaking`
        when playback started and NOTHING when it finished, so the sphere sat in
        `speaking` for hours — telling Gerald she was talking while she was
        silent. §5.1 exists so the sphere reads correctly from across the room,
        and a state that never clears makes it read wrong rather than not at all.
        `finished_callback` fires when the device actually drains, so the
        transition is driven by playback ending, not by a timer guessing.
        """
        self.stop("superseded")
        play = Playback(samples.astype(np.int16), sample_rate)

        def _finished() -> None:
            self._final(play)
            play.done.set()
            if self._play is play:
                self._done.set()
            # `stop()` sets `_stopping` before aborting, so a barge-in does not
            # come through here and wrongly report idle — the interrupting turn
            # owns the state from that point.
            if not self._stopping and not play.cut:
                self.set_state(AgentState.IDLE)
            self._report(play)

        with self._lock:
            self._samples = play.samples
            self._pos = 0
            self._stopping = False
            self._done.clear()
            self._play = play
            stream = None
            try:
                stream = sd.OutputStream(
                    samplerate=sample_rate, channels=1, dtype="int16",
                    callback=self._callback, finished_callback=_finished,
                )
                stream.start()
            except Exception:
                play.cut = True
                play.final_sent = True
                play.reported = True
                play.done.set()
                self._samples = None
                self._play = None
                self._done.set()
                if stream is not None:
                    try:
                        stream.close(ignore_errors=True)
                    except Exception:
                        pass
                raise
            self._stream = stream
        self.set_state(AgentState.SPEAKING)
        self._announce(play)
        if play.done.is_set() and not play.cut and self._play is play:
            self.set_state(AgentState.IDLE)
        if blocking:
            self._done.wait()
            self.set_state(AgentState.IDLE)
        return play

    def stop(self, reason: str = "barge-in") -> BargeIn | None:
        """
        Cut the speaker NOW. Returns the measured latency, or None if silent.

        `abort()` rather than `stop()` on the stream: `stop()` drains the buffer
        that is already queued in the driver, which is exactly the tail of her
        talking over him that this exists to prevent.
        """
        requested = time.perf_counter()
        with self._lock:
            stream = self._stream
            if stream is None:
                return None
            play = self._play
            playing = not self._done.is_set()
            if play is not None and playing:
                play.cut = True
            self._stopping = True
            try:
                stream.abort(ignore_errors=True)
            except Exception:  # noqa: BLE001
                pass
            try:
                stream.close(ignore_errors=True)
            except Exception:  # noqa: BLE001
                pass
            self._stream = None
            self._samples = None
            self._done.set()
            if playing:
                self._state = AgentState.IDLE
        stopped = time.perf_counter()
        if play is not None and playing:
            self._final(play)
            play.done.set()
            self._report(play)
        return BargeIn(requested_at=requested, stopped_at=stopped)

    def close(self) -> None:
        self.stop("shutdown")
        self.set_state(AgentState.IDLE)
