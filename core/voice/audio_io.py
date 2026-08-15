"""
core/voice/audio_io.py — microphone capture for push-to-talk.

PUSH-TO-TALK ONLY. There is no wake word and no VAD in this module, and that is
a design decision rather than an omission: you hold a key, you release it, and
that IS the segment boundary. A voice-activity detector exists to guess where
speech starts and stops; a key press does not need to guess. That keeps Silero
and its torch dependency — hundreds of megabytes on a metered connection — out
of the sprint entirely.

16 kHz mono int16 throughout. Whisper resamples anything else to 16 kHz
internally, so capturing at 44.1 or 48 kHz would mean paying for the samples on
the wire and then throwing most of them away on a 2-core machine.
"""

from __future__ import annotations

import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16_000
CHANNELS = 1
DTYPE = "int16"


@dataclass(frozen=True)
class InputDevice:
    index: int
    name: str
    channels: int
    default_samplerate: float
    is_default: bool

    def describe(self) -> str:
        mark = " *DEFAULT*" if self.is_default else ""
        return (f"[{self.index:>2}] {self.name}  "
                f"ch={self.channels} rate={self.default_samplerate:.0f}Hz{mark}")


@dataclass(frozen=True)
class Capture:
    """A recorded segment, plus the numbers that prove it is not silence."""
    samples: np.ndarray          # int16, mono
    sample_rate: int
    duration_s: float
    peak: int                    # peak absolute amplitude, 0..32767
    rms: float
    first_sample_latency_ms: float

    @property
    def peak_dbfs(self) -> float:
        return -np.inf if self.peak == 0 else 20.0 * float(np.log10(self.peak / 32767.0))

    @property
    def is_silence(self) -> bool:
        """A WAV of zeros is the audio equivalent of a fabricated measurement."""
        return self.peak < 32          # ~ -60 dBFS


def list_input_devices() -> list[InputDevice]:
    """Every device that can actually record. Output-only devices are excluded."""
    default_in = sd.default.device[0] if isinstance(sd.default.device, (list, tuple)) else None
    out: list[InputDevice] = []
    for idx, dev in enumerate(sd.query_devices()):
        d: dict[str, Any] = dict(dev)
        if int(d.get("max_input_channels", 0)) < 1:
            continue
        out.append(InputDevice(
            index=idx,
            name=str(d.get("name", "?")).strip(),
            channels=int(d["max_input_channels"]),
            default_samplerate=float(d.get("default_samplerate", 0.0)),
            is_default=(idx == default_in),
        ))
    return out


def record(seconds: float, device: int | None = None) -> Capture:
    """
    Record a fixed-length segment.

    `first_sample_latency_ms` is measured from the call into the driver to the
    first frame actually delivered by the callback — not to the end of the
    recording. That is the number that matters for push-to-talk: it is how much
    of the owner's first syllable is lost between pressing the key and the
    stream being live.
    """
    frames_wanted = int(seconds * SAMPLE_RATE)
    chunks: list[np.ndarray] = []
    t_call = time.perf_counter()
    t_first: float | None = None

    def callback(indata: np.ndarray, _frames: int, _t: Any, status: Any) -> None:
        nonlocal t_first
        if t_first is None:
            t_first = time.perf_counter()
        if status:
            print(f"  [audio] stream status: {status}")
        chunks.append(indata.copy())

    with sd.InputStream(
        samplerate=SAMPLE_RATE, channels=CHANNELS, dtype=DTYPE,
        device=device, callback=callback,
    ):
        while sum(len(c) for c in chunks) < frames_wanted:
            time.sleep(0.01)

    samples = np.concatenate(chunks)[:frames_wanted].reshape(-1)
    latency_ms = ((t_first - t_call) * 1000.0) if t_first is not None else float("nan")
    peak = int(np.max(np.abs(samples.astype(np.int32)))) if samples.size else 0
    rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2))) if samples.size else 0.0

    return Capture(
        samples=samples, sample_rate=SAMPLE_RATE,
        duration_s=len(samples) / SAMPLE_RATE,
        peak=peak, rms=rms, first_sample_latency_ms=latency_ms,
    )


def write_wav(capture: Capture, path: Path) -> int:
    """Write 16-bit PCM mono. Returns bytes written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(CHANNELS)
        fh.setsampwidth(2)
        fh.setframerate(capture.sample_rate)
        fh.writeframes(capture.samples.astype(np.int16).tobytes())
    return path.stat().st_size


class ArmedMicrophone:
    """
    A continuously-open input stream with a rolling pre-roll buffer.

    ─────────────────────────────────────────────────────────────────────────────
    WHY ARM RATHER THAN OPEN

    `record()` opens the stream on demand, and measured on this machine the first
    frame arrives 134.99 ms after the call. If push-to-talk opened the stream on
    key-down, the owner would lose the first ~135 ms of their first syllable —
    every single time, silently, and worst on exactly the short commands voice is
    for. "Zoey" begins with a plosive; 135 ms is most of it.

    So the stream is held open and always writing into a ring. `arm()` does not
    start capture, it marks a position — and then reaches BACKWARDS into the ring
    for `pre_roll_s` of audio that was already recorded before the key went down.
    The lost syllable is recovered rather than avoided.

    ─────────────────────────────────────────────────────────────────────────────
    THE PRIVACY CONSEQUENCE, STATED PLAINLY

    This means the microphone is LIVE whenever the daemon is running, not only
    while the key is held. That is a real change in what the machine is doing and
    it must not be buried in a docstring:

      * Audio is held in memory only, in a fixed-size ring that overwrites
        itself. Nothing is written to disk until a segment is claimed.
      * `voice.ptt.start` in the audit log no longer means "the microphone
        opened". It means "the owner claimed a segment". Those were the same
        event when the stream opened on key-down and they are not any more, and
        an audit entry that quietly changes meaning is worse than one that is
        missing. The daemon should audit stream open/close separately from
        segment claims.
      * The ring is the exposure. Its depth is the number of seconds of the
        owner's life that exist in RAM at any moment, so it is kept small and
        stated rather than generous and unmentioned.
    ─────────────────────────────────────────────────────────────────────────────
    """

    def __init__(self, pre_roll_s: float = 1.0, device: int | None = None) -> None:
        self.pre_roll_s = pre_roll_s
        self.device = device
        self._ring = np.zeros(int(pre_roll_s * SAMPLE_RATE), dtype=np.int16)
        self._write = 0            # next write index, wraps
        self._filled = 0           # how much of the ring is real audio
        self._stream: sd.InputStream | None = None
        self._captured: list[np.ndarray] | None = None
        self._armed_at: float | None = None
        self._first_after_arm: float | None = None

    @property
    def ring_bytes(self) -> int:
        return int(self._ring.nbytes)

    def _callback(self, indata: np.ndarray, _frames: int, _t: Any, status: Any) -> None:
        block = indata.reshape(-1)
        if self._captured is not None:
            if self._first_after_arm is None:
                self._first_after_arm = time.perf_counter()
            self._captured.append(block.copy())
            return
        n = len(block)
        ring = self._ring
        size = len(ring)
        if n >= size:
            ring[:] = block[-size:]
            self._write = 0
            self._filled = size
            return
        end = self._write + n
        if end <= size:
            ring[self._write:end] = block
        else:
            first = size - self._write
            ring[self._write:] = block[:first]
            ring[: n - first] = block[first:]
        self._write = end % size
        self._filled = min(size, self._filled + n)

    def open(self) -> None:
        """Start the stream. From here the microphone is live."""
        if self._stream is not None:
            return
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE, channels=CHANNELS, dtype=DTYPE,
            device=self.device, callback=self._callback, blocksize=0,
        )
        self._stream.start()

    def close(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def _snapshot_ring(self) -> np.ndarray:
        """The ring in chronological order — this is the pre-roll."""
        size = len(self._ring)
        if self._filled < size:
            return self._ring[: self._write].copy()
        return np.concatenate([self._ring[self._write:], self._ring[: self._write]])

    def rms_of(self, block: np.ndarray) -> float:
        return float(np.sqrt(np.mean(block.astype(np.float64) ** 2))) if block.size else 0.0

    def watch_for_silence(
        self,
        *,
        on_close: Callable[[str], None],
        silence_ms: int = 1200,
        floor_rms: float = 150.0,
        relative_ratio: float = 0.08,
        hard_cap_s: float = 20.0,
        poll_ms: int = 50,
    ) -> threading.Thread:
        """
        Close the segment when HE stops talking — as a BACKGROUND WATCHER.

        THIS IS THE WIRING THAT WAS MISSING. `wait_for_silence` was written,
        measured in isolation, reported as landed, and called by nothing. Gerald
        has been told for two prompts that "press once and it ends on silence"
        works. It has never once run: his 22.06 s segment ended because he
        pressed the chord a second time, and the 20 s hard cap never fired
        because the function containing it was never entered.

        A blocking call could not be wired without stalling the socket handler,
        which is why it never got connected. A watcher thread can.

        SILENCE WINDOW = 1200 ms, and the number is not arbitrary. Natural
        between-clause pauses in connected speech run 200-500 ms, and a
        mid-sentence hesitation ("open the... downloads folder") sits around
        600-900 ms. 1200 ms clears the longest of those with margin. Shorter and
        it truncates him mid-thought, which is a worse failure than pressing
        twice — he loses the second half of a sentence and does not know why.

        FLOOR is hybrid: `max(floor_rms, loudest_so_far * relative_ratio)`.
        Silence is "much quieter than what I just heard", so the segment still
        closes after Windows AGC has wound the gain down over a long stream, in
        a different room, or on a different microphone. The absolute 150 is the
        lower bound that stops a dead-quiet room from being read as speech —
        his room profiled at RMS 2 (max 191) against speech at 1348-4518, so the
        floor was never what kept his segment open.

        HARD CAP = 20 s regardless. A noisy room, a fan, a passing conversation
        must never record indefinitely — the microphone is already always-live
        and an unbounded segment turns that into an unbounded recording.

        A SECOND PRESS outranks all of it: `cancel_watch` sets `stop_evt` and the
        loop returns "cancelled" without calling `on_close`, because a manual
        stop is the caller's own business and must not be reported back to it as
        an automatic one.
        """
        stop_evt = threading.Event()
        self._watch_stop = stop_evt

        def run() -> None:
            reason = self._silence_loop(
                silence_ms=silence_ms, floor_rms=floor_rms,
                relative_ratio=relative_ratio, hard_cap_s=hard_cap_s,
                poll_ms=poll_ms, stop_evt=stop_evt,
            )
            if reason != "cancelled":
                on_close(reason)

        t = threading.Thread(target=run, name="zoey-vad", daemon=True)
        t.start()
        return t

    def cancel_watch(self) -> None:
        """A second press ends the segment immediately — his escape hatch."""
        evt = getattr(self, "_watch_stop", None)
        if evt is not None:
            evt.set()

    def _silence_loop(
        self, *, silence_ms: int, floor_rms: float, relative_ratio: float,
        hard_cap_s: float, poll_ms: int, stop_evt: threading.Event,
    ) -> str:
        t0 = time.perf_counter()
        last_speech = t0
        heard_any = False
        loudest = 0.0
        seen = 0
        while True:
            if stop_evt.is_set():
                return "cancelled"
            now = time.perf_counter()
            if now - t0 >= hard_cap_s:
                return "hard-cap"
            chunks = self._captured
            if chunks is not None and len(chunks) > seen:
                for blk in chunks[seen:]:
                    r = self.rms_of(blk)
                    loudest = max(loudest, r)
                    # RELATIVE floor: silence is "much quieter than what I just
                    # heard", not "below a fixed number". A fixed threshold
                    # cannot survive AGC winding the level down, a different
                    # room, or a different microphone — and the level here was
                    # measured moving 979 -> 264 RMS over a 15 s hold. The
                    # absolute floor stays as a lower bound so a dead-silent
                    # room still counts as silence.
                    threshold = max(floor_rms, loudest * relative_ratio)
                    if r >= threshold:
                        last_speech = now
                        heard_any = True
                seen = len(chunks)
            if heard_any and (now - last_speech) * 1000.0 >= silence_ms:
                return "silence"
            time.sleep(poll_ms / 1000.0)

    def arm(self) -> np.ndarray:
        """
        Claim a segment. Returns the pre-roll captured BEFORE this instant.

        Nothing is opened here — the stream is already running — so there is no
        driver latency to pay and the returned array is audio that already
        existed when the key went down.
        """
        pre = self._snapshot_ring()
        self._captured = []
        self._armed_at = time.perf_counter()
        self._first_after_arm = None
        self._pre_roll = pre
        return pre

    def disarm(self) -> Capture:
        """Stop claiming. Returns pre-roll + everything captured while armed."""
        post = self._captured or []
        self._captured = None
        tail = np.concatenate(post) if post else np.zeros(0, dtype=np.int16)
        samples = np.concatenate([self._pre_roll, tail]).astype(np.int16)
        latency = (
            (self._first_after_arm - self._armed_at) * 1000.0
            if self._first_after_arm is not None and self._armed_at is not None
            else 0.0
        )
        peak = int(np.max(np.abs(samples.astype(np.int32)))) if samples.size else 0
        rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2))) if samples.size else 0.0
        return Capture(
            samples=samples, sample_rate=SAMPLE_RATE,
            duration_s=len(samples) / SAMPLE_RATE,
            peak=peak, rms=rms, first_sample_latency_ms=latency,
        )

    def __enter__(self) -> ArmedMicrophone:
        self.open()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    """Read a 16-bit PCM WAV back as float32 in [-1, 1], the shape Whisper wants."""
    with wave.open(str(path), "rb") as fh:
        rate = fh.getframerate()
        raw = fh.readframes(fh.getnframes())
    pcm = np.frombuffer(raw, dtype=np.int16)
    return (pcm.astype(np.float32) / 32768.0), rate


def replay_silence_decision(
    samples: np.ndarray,
    *,
    block_ms: int = 50,
    silence_ms: int = 1200,
    floor_rms: float = 150.0,
    relative_ratio: float = 0.08,
    hard_cap_s: float = 20.0,
) -> dict:
    """
    Run the SAME silence decision over a recorded segment, offline.

    Deliberately shares the thresholds and the comparison with the live watcher
    so this is not a second implementation that can drift from it. It is the
    test that should have existed from the start: repeatable, needing neither
    his voice nor a speaker, and it would have caught the unwired detector on
    day one.

    SCALE, and this was a real bug in this function. The live watcher measures
    int16 RMS (0..32767) and the thresholds above are on that scale, but
    `read_wav()` returns float32 in [-1, 1] because that is what Whisper wants.
    Handed the natural output of `read_wav`, this replay compared a loudest
    block of 0.219 against a floor of 150 and reported `heard_any=False` — it
    "proved" his own speech was silence. Sharing the thresholds with the live
    path is worthless if the units are not shared too, so the scale is now
    coerced here rather than left to every caller to remember.
    """
    samples = np.asarray(samples)
    if np.issubdtype(samples.dtype, np.floating):
        samples = samples * 32768.0
    n = int(block_ms * 16_000 / 1000)
    last_speech_s = None
    heard_any = False
    loudest = 0.0
    for i in range(0, len(samples) - n + 1, n):
        t_s = (i + n) / 16_000.0
        if t_s >= hard_cap_s:
            return {"reason": "hard-cap", "close_at_s": hard_cap_s,
                    "heard_any": heard_any, "loudest": loudest}
        r = float(np.sqrt(np.mean(samples[i:i + n].astype(np.float64) ** 2)))
        loudest = max(loudest, r)
        threshold = max(floor_rms, loudest * relative_ratio)
        if r >= threshold:
            last_speech_s = t_s
            heard_any = True
        elif heard_any and last_speech_s is not None:
            if (t_s - last_speech_s) * 1000.0 >= silence_ms:
                return {"reason": "silence", "close_at_s": t_s,
                        "last_speech_s": last_speech_s, "heard_any": True,
                        "loudest": loudest}
    return {"reason": "ran-out", "close_at_s": len(samples) / 16_000.0,
            "heard_any": heard_any, "loudest": loudest}
