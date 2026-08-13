"""
core/voice/tts/base.py — the adapter both engines slot into (spec §8).

Spec §8 says "adapter for both": Piper local, ElevenLabs cloud. Only Piper is
implemented today. This file exists so that adding ElevenLabs later is a new
class rather than a refactor of everything that calls TTS.

THE INTERFACE IS BUILT AROUND STREAMING, not around returning a WAV.

Spec §4 allows 400 ms from first LLM token to first audio out. That is only
reachable if synthesis begins before the sentence is finished and audio begins
before synthesis is finished. An interface whose primary method returns a
completed buffer makes the budget unreachable by construction, and no amount of
optimisation downstream recovers it — so `stream()` is the primary method and
`synthesise()` is the convenience wrapper over it, not the other way round.

An engine that cannot stream implements `stream()` by yielding one chunk. That
is honest: the caller sees a single late chunk and the measured time-to-first-
sample tells the truth about the engine rather than hiding it behind an API that
looks streaming.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import numpy as np


@dataclass(frozen=True)
class VoiceInfo:
    voice_id: str
    name: str
    language: str
    quality: str
    female: bool
    sample_rate: int
    size_bytes: int


@dataclass
class Synthesis:
    """A completed synthesis, plus the timings that decide the latency budget."""
    samples: np.ndarray            # int16 mono
    sample_rate: int
    #: Wall clock from the call to the FIRST audio sample being available. This
    #: is the number spec §4 constrains, not `total_s`.
    first_sample_ms: float
    total_s: float
    chunks: int

    @property
    def duration_s(self) -> float:
        return len(self.samples) / self.sample_rate if self.sample_rate else 0.0

    @property
    def streamed(self) -> bool:
        """More than one chunk means audio was available before synthesis finished."""
        return self.chunks > 1


class TTSAdapter(ABC):
    """Spec §8. Piper today; ElevenLabs slots in beside it unchanged."""

    @property
    @abstractmethod
    def voice(self) -> VoiceInfo: ...

    @abstractmethod
    def stream(self, text: str) -> Iterator[np.ndarray]:
        """Yield int16 mono chunks as they become available. Primary method."""

    def synthesise(self, text: str) -> Synthesis:
        """Consume `stream()` and time the first chunk separately from the rest."""
        t0 = time.perf_counter()
        first_ms: float | None = None
        chunks: list[np.ndarray] = []
        for chunk in self.stream(text):
            if first_ms is None:
                first_ms = (time.perf_counter() - t0) * 1000.0
            chunks.append(chunk)
        total = time.perf_counter() - t0
        samples = (
            np.concatenate(chunks).astype(np.int16) if chunks else np.zeros(0, dtype=np.int16)
        )
        return Synthesis(
            samples=samples,
            sample_rate=self.voice.sample_rate,
            first_sample_ms=first_ms if first_ms is not None else float("nan"),
            total_s=total,
            chunks=len(chunks),
        )

    def to_wav(self, syn: Synthesis, path: Path) -> int:
        import wave

        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as fh:
            fh.setnchannels(1)
            fh.setsampwidth(2)
            fh.setframerate(syn.sample_rate)
            fh.writeframes(syn.samples.tobytes())
        return path.stat().st_size
