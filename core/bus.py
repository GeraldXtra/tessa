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

import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable

import numpy as np
import sounddevice as sd


class AgentState(str, Enum):
    """Mirrors CONTRACT's evt.agent.state. Session 2's sphere renders these."""
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"


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

    def __init__(self, on_state: Callable[[AgentState], None] | None = None) -> None:
        self._lock = threading.Lock()
        self._stream: sd.OutputStream | None = None
        self._samples: np.ndarray | None = None
        self._pos = 0
        self._done = threading.Event()
        self._stopping = False
        self._on_state = on_state
        self._state = AgentState.IDLE

    # ── state ────────────────────────────────────────────────────────────────

    @property
    def state(self) -> AgentState:
        return self._state

    def set_state(self, state: AgentState) -> None:
        if state is self._state:
            return
        self._state = state
        if self._on_state is not None:
            self._on_state(state)

    @property
    def is_speaking(self) -> bool:
        return self._stream is not None and not self._done.is_set()

    # ── playback ─────────────────────────────────────────────────────────────

    def _callback(self, outdata: np.ndarray, frames: int, _t: object, _s: object) -> None:
        buf = self._samples
        if buf is None or self._stopping:
            outdata[:] = 0
            raise sd.CallbackStop
        end = self._pos + frames
        chunk = buf[self._pos:end]
        if len(chunk) < frames:
            outdata[: len(chunk), 0] = chunk
            outdata[len(chunk):] = 0
            self._pos = len(buf)
            raise sd.CallbackStop
        outdata[:, 0] = chunk
        self._pos = end

    def speak(self, samples: np.ndarray, sample_rate: int, blocking: bool = False) -> None:
        """Take the speaker. Any current speech is stopped first — one speaker."""
        self.stop("superseded")
        with self._lock:
            self._samples = samples.astype(np.int16)
            self._pos = 0
            self._stopping = False
            self._done.clear()
            self._stream = sd.OutputStream(
                samplerate=sample_rate, channels=1, dtype="int16",
                callback=self._callback, finished_callback=self._done.set,
            )
            self._stream.start()
        self.set_state(AgentState.SPEAKING)
        if blocking:
            self._done.wait()
            self.set_state(AgentState.IDLE)

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
        stopped = time.perf_counter()
        return BargeIn(requested_at=requested, stopped_at=stopped)

    def close(self) -> None:
        self.stop("shutdown")
        self.set_state(AgentState.IDLE)
