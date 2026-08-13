"""
core/voice/stt.py — speech to text, faster-whisper on CTranslate2.

NO `av`. faster-whisper pulls PyAV (27.6 MB) to decode arbitrary media, but
`transcribe()` also accepts a float32 numpy array in [-1, 1] at 16 kHz, and
`audio_io.read_wav()` produces exactly that. Feeding the array directly skips
the decoder entirely — one less moving part on the hot path, and the 27.6 MB
stays unimported even though pip installs it as a hard dependency.

int8 throughout. This is an i5-7200U with 2 cores and no usable GPU; int8 is the
only quantisation that makes CTranslate2's CPU kernels worth having, and the
accuracy cost on a model this size is smaller than the accent variance we are
actually up against.

Models are cached under data/models/ (docs/STRUCTURE.md). Never re-downloaded.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
MODEL_ROOT = ROOT / "data" / "models"

CPU_THREADS = 4

#: Vocabulary prime, passed as `initial_prompt` on every transcription.
#:
#: MEASURED: it is not a cost, it is a SAVING. base on a 4 s clip went 5.782 s
#: plain -> 2.822 s primed, and tiny went 4.114 s -> 1.772 s. Priming narrows
#: the decoder's search, so it emits fewer tokens and hallucinates less, and
#: decoder cost scales with tokens emitted. An earlier "hotwords cost 7x" figure
#: was contamination from a concurrent benchmark, not a property of the API.
#:
#: `initial_prompt` rather than `hotwords`: measured accuracy was identical on
#: this fixture, latency was within noise of each other, and initial_prompt is
#: plain Whisper decoder conditioning rather than faster-whisper-specific
#: machinery — one less thing that behaves differently after an upgrade.
#:
#: It fixes exactly the class of error the accent produces: proper nouns.
#: Zui -> Zoey, "larger watch" -> LedgerWatch, Aptek -> Aptech. Generic English
#: was already verbatim without it.
VOCABULARY_PRIME = "Zoey, LedgerWatch, Aptech, naira, Titan Wave, ZOEY_OS, Lagos"


@dataclass
class Transcript:
    text: str
    language: str
    language_probability: float
    audio_s: float
    wall_s: float
    segments: list[str] = field(default_factory=list)

    @property
    def realtime_factor(self) -> float:
        """Audio seconds per processing second. >1 is faster than real time."""
        return self.audio_s / self.wall_s if self.wall_s > 0 else float("inf")


class WhisperSTT:
    def __init__(self, size: str = "small", compute_type: str = "int8") -> None:
        from faster_whisper import WhisperModel  # imported late: ~2 s of import cost

        MODEL_ROOT.mkdir(parents=True, exist_ok=True)
        self.size = size
        self.compute_type = compute_type
        t0 = time.perf_counter()
        self.model = WhisperModel(
            size,
            device="cpu",
            compute_type=compute_type,
            cpu_threads=CPU_THREADS,
            download_root=str(MODEL_ROOT),
        )
        self.load_s = time.perf_counter() - t0

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16_000) -> Transcript:
        """
        `audio` is float32 in [-1, 1] at 16 kHz — the shape read_wav() returns.

        beam_size=1 (greedy) on purpose: beam search multiplies decoder passes,
        and on 2 cores that is the difference between missing the latency budget
        and missing it badly. If accuracy needs the beam, that is a trade to make
        with the measured numbers in hand, not by default.
        """
        t0 = time.perf_counter()
        segments, info = self.model.transcribe(
            audio, beam_size=1, language="en", vad_filter=False,
            initial_prompt=VOCABULARY_PRIME,
        )
        # faster-whisper is lazy: the generator is where the work happens, so the
        # timer must span its consumption, not just the call.
        texts = [s.text for s in segments]
        wall = time.perf_counter() - t0
        return Transcript(
            text="".join(texts).strip(),
            language=info.language,
            language_probability=float(info.language_probability),
            audio_s=len(audio) / sample_rate,
            wall_s=wall,
            segments=[t.strip() for t in texts],
        )


def model_disk_bytes(size: str) -> int:
    """Bytes this model occupies under data/models/."""
    total = 0
    for p in MODEL_ROOT.rglob("*"):
        if p.is_file() and size in str(p).lower():
            total += p.stat().st_size
    return total
