"""
core/voice/denoise.py — RNNoise suppression, OFF by default and probably staying off.

────────────────────────────────────────────────────────────────────────────────
THREE THINGS I GOT WRONG IN THE SURVEY, CORRECTED HERE

1. "pyrnnoise 13.27 MB, prebuilt Windows wheel, NO DEPENDENCY TAIL."
   Wrong. `pyrnnoise` depends on `audiolab`, which depends on matplotlib.
   Installed cost was 88.43 MB, of which ~66 MB is matplotlib + pillow +
   fonttools — a plotting stack, for a noise suppressor. That is the exact tail
   I rejected `noisereduce` for.

2. The `audiolab` layer does not even work here. It needs an older PyAV API
   (`av.option`), and PyAV 18.1.0 was already installed on this machine by
   something else. `import pyrnnoise` raises ModuleNotFoundError.

3. RNNoise is a **48 kHz** model with 480-sample frames. This entire voice chain
   is 16 kHz. Suppression therefore costs an upsample and a downsample on every
   block, on two cores, inside the audio callback.

WHAT IS ACTUALLY USED. `pyrnnoise/rnnoise.py` is a self-contained ctypes binding
over `rnnoise.dll` (14.14 MB) and imports none of that tail. It is loaded BY PATH
so the package's `__init__` — which drags the broken chain in — never runs.
That makes the real cost of suppression **the 14.14 MB DLL**, and it means
Gerald can reclaim ~74 MB by uninstalling audiolab, matplotlib, pillow,
fonttools, contourpy, kiwisolver, cycler, smart_open and humanize.

────────────────────────────────────────────────────────────────────────────────
WHY IT IS OFF BY DEFAULT, BEYOND GERALD'S RULING

His room profiled at **RMS 2** against speech at 1348-4518. There is essentially
nothing to suppress. RNNoise is trained to strip stationary broadband noise —
fans, air conditioning, street hum — and a room that quiet has none of it, so
the most likely outcome of turning it on is that it removes something real.

────────────────────────────────────────────────────────────────────────────────
IF IT IS ON, IT IS ON FOR EVERYTHING

The chain order is: microphone -> [suppression] -> ring -> wake / VAD ->
verification -> transcription. Suppression sits at the top and is SHARED,
because the alternative is the worst kind of intermittent: the wake detector
seeing raw audio while the voiceprint is scored on processed audio, so
verification breaks only when suppression is enabled and only sometimes.

The voiceprint therefore records which chain it was enrolled through, and
`SpeakerVerifier.verify` refuses to trust a score when the two disagree rather
than silently returning a number that means nothing.
"""

from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

#: RNNoise's native rate. NOT the daemon's 16 kHz — see the module docstring.
RNNOISE_RATE = 48_000
RNNOISE_FRAME = 480
CHAIN_RATE = 16_000


def _load_binding() -> Any | None:
    """
    Load `pyrnnoise.rnnoise` by path, skipping the package `__init__`.

    `import pyrnnoise` executes `__init__.py`, which imports `pyrnnoise.pyrnnoise`,
    which imports `audiolab`, which fails on PyAV 18. The ctypes binding itself
    is clean and has no such dependency, so it is loaded directly.
    """
    candidates = [
        Path(sys.prefix) / "Lib" / "site-packages" / "pyrnnoise" / "rnnoise.py",
        Path(sys.prefix) / "lib" / "site-packages" / "pyrnnoise" / "rnnoise.py",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            spec = importlib.util.spec_from_file_location("_zoey_rnnoise", path)
            if spec is None or spec.loader is None:
                continue
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
        except Exception:  # noqa: BLE001
            return None
    return None


class Suppressor:
    """
    16 kHz in, 16 kHz out, RNNoise at 48 kHz in the middle.

    STATEFUL AND ORDER-DEPENDENT. RNNoise carries state across frames, so blocks
    must be fed in order and a reset is needed between unrelated recordings.
    Feeding a fresh segment through a state warmed on a previous one changes the
    output, which would make the same audio score differently depending on what
    preceded it — an unacceptable property for something the voiceprint is
    compared through.
    """

    def __init__(self) -> None:
        self._mod = None
        self._state = None
        self._tail = np.zeros(0, dtype=np.float32)
        self.load_error: str | None = None
        self.frames = 0
        self.vad_probs: list[float] = []

    def load(self) -> bool:
        self._mod = _load_binding()
        if self._mod is None:
            self.load_error = ("pyrnnoise's ctypes binding could not be loaded "
                               "(is pyrnnoise installed?)")
            return False
        try:
            from scipy.signal import resample_poly  # noqa: F401
        except ImportError as exc:
            # scipy arrived as an openWakeWord dependency. If the wake word is
            # ever removed, this goes with it — say so rather than crash later.
            self.load_error = f"scipy needed for 16k<->48k resampling ({exc})"
            return False
        try:
            self._state = self._mod.create()
        except Exception as exc:  # noqa: BLE001
            self.load_error = f"{type(exc).__name__}: {exc}"
            return False
        return True

    def reset(self) -> None:
        """New recording, new state. See the class docstring."""
        if self._mod is None:
            return
        if self._state is not None:
            try:
                self._mod.destroy(self._state)
            except Exception:  # noqa: BLE001
                pass
        self._state = self._mod.create()
        self._tail = np.zeros(0, dtype=np.float32)
        self.frames = 0
        self.vad_probs = []

    def close(self) -> None:
        if self._mod is not None and self._state is not None:
            try:
                self._mod.destroy(self._state)
            except Exception:  # noqa: BLE001
                pass
            self._state = None

    def process(self, audio: np.ndarray, sample_rate: int = CHAIN_RATE) -> np.ndarray:
        """
        Denoise a whole array. Returns the same dtype and length it was given.

        Length is preserved deliberately: everything downstream — the ring, the
        pre-roll arithmetic, the VAD's block accounting, the segment duration in
        the audit line — assumes samples in equals samples out. A suppressor that
        quietly returned a different number of samples would shift the pre-roll
        and nothing would report it.
        """
        if self._mod is None or self._state is None:
            return audio

        from scipy.signal import resample_poly

        a = np.asarray(audio).reshape(-1)
        was_int = np.issubdtype(a.dtype, np.integer)
        f = (a.astype(np.float32) / 32768.0) if was_int else a.astype(np.float32)
        n_in = len(f)
        if n_in == 0:
            return audio

        up = resample_poly(f, RNNOISE_RATE, sample_rate).astype(np.float32)

        # RNNoise wants int16-scaled floats, in whole 480-sample frames.
        scaled = up * 32768.0
        usable = (len(scaled) // RNNOISE_FRAME) * RNNOISE_FRAME
        out = np.empty_like(scaled)
        for i in range(0, usable, RNNOISE_FRAME):
            frame = scaled[i:i + RNNOISE_FRAME].astype(np.int16)
            try:
                den, prob = self._mod.process_frame(self._state, frame)
                out[i:i + RNNOISE_FRAME] = np.asarray(den, dtype=np.float32)
                self.vad_probs.append(float(prob))
                self.frames += 1
            except Exception:  # noqa: BLE001
                out[i:i + RNNOISE_FRAME] = scaled[i:i + RNNOISE_FRAME]
        if usable < len(scaled):
            # The ragged tail is passed through rather than dropped. Dropping it
            # would shorten the signal; zeroing it would punch a hole in the last
            # 10 ms of every segment, which is where the end of his sentence is.
            out[usable:] = scaled[usable:]

        down = resample_poly(out / 32768.0, sample_rate, RNNOISE_RATE).astype(np.float32)

        # LENGTH IS RESTORED EXACTLY. resample_poly's output length is a ceiling
        # computation and can be off by a sample or two either way.
        if len(down) < n_in:
            down = np.concatenate([down, np.zeros(n_in - len(down), dtype=np.float32)])
        down = down[:n_in]

        if was_int:
            return np.clip(down * 32768.0, -32768, 32767).astype(np.int16)
        return down

    def describe(self) -> str:
        if self._mod is None:
            return f"suppression: UNAVAILABLE ({self.load_error})"
        mean_vad = float(np.mean(self.vad_probs)) if self.vad_probs else float("nan")
        return (f"suppression: RNNoise @{RNNOISE_RATE} Hz, {self.frames} frames, "
                f"mean speech probability {mean_vad:.3f}")


def timed_process(sup: Suppressor, audio: np.ndarray,
                  sample_rate: int = CHAIN_RATE) -> tuple[np.ndarray, float]:
    """Process and report the wall clock, for the hot-path budget."""
    t0 = time.perf_counter()
    out = sup.process(audio, sample_rate)
    return out, (time.perf_counter() - t0) * 1000.0
