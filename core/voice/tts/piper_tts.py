"""
core/voice/tts/piper_tts.py — local neural TTS via Piper (onnxruntime).

VOICE: en_GB-jenny_dioco-medium. Female, which is Gerald's non-negotiable.

Why this one out of the fourteen female English voices available:

  * en_GB rather than en_US. Gerald speaks Nigerian English, whose vowel system
    and vocabulary sit closer to British than to American English. For a voice
    he will live with all day, "less foreign" beats "more common".
  * `medium` rather than `low` or `high`. `low` is 16 kHz and audibly rougher;
    `high` is 114 MB and nearly doubles inference cost on 2 cores for detail a
    laptop speaker cannot resolve. `medium` is 22.05 kHz and the standard trade.
  * jenny_dioco rather than alba (identical size, same tier): alba is
    Scottish-accented, jenny_dioco is conversational-neutral. A companion voice
    should not make an accent choice on the owner's behalf — and he can change
    it later through `cmd.voice.setVoice`, which §5.3 already reserves.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import numpy as np

from .base import TTSAdapter, VoiceInfo

ROOT = Path(__file__).resolve().parents[3]
VOICE_DIR = ROOT / "data" / "models" / "piper"

VOICE_ID = "en_GB-jenny_dioco-medium"
_REPO = "rhasspy/piper-voices"
_REMOTE = "en/en_GB/jenny_dioco/medium"


def ensure_voice() -> tuple[Path, Path]:
    """Download the voice once. Returns (onnx, config). Never re-downloads."""
    VOICE_DIR.mkdir(parents=True, exist_ok=True)
    onnx = VOICE_DIR / f"{VOICE_ID}.onnx"
    cfg = VOICE_DIR / f"{VOICE_ID}.onnx.json"
    if onnx.exists() and cfg.exists():
        return onnx, cfg

    import urllib.request

    base = f"https://huggingface.co/{_REPO}/resolve/main/{_REMOTE}"
    for target, name in ((onnx, f"{VOICE_ID}.onnx"), (cfg, f"{VOICE_ID}.onnx.json")):
        if target.exists():
            continue
        urllib.request.urlretrieve(f"{base}/{name}", target)
    return onnx, cfg


class PiperTTS(TTSAdapter):
    def __init__(self) -> None:
        from piper import PiperVoice

        onnx, cfg = ensure_voice()
        self._onnx_path = onnx
        self._voice = PiperVoice.load(str(onnx), config_path=str(cfg))
        rate = int(getattr(self._voice.config, "sample_rate", 22050))
        self._info = VoiceInfo(
            voice_id=VOICE_ID, name="Jenny (dioco)", language="en_GB",
            quality="medium", female=True, sample_rate=rate,
            size_bytes=onnx.stat().st_size + cfg.stat().st_size,
        )

    @property
    def voice(self) -> VoiceInfo:
        return self._info

    def stream(self, text: str) -> Iterator[np.ndarray]:
        """
        Yield audio as Piper produces it.

        Piper synthesises SENTENCE BY SENTENCE, so a multi-sentence reply does
        produce audio before the whole thing is done — but within a single
        sentence it is synthesise-then-return. The practical consequence is that
        time-to-first-sample is the cost of the FIRST SENTENCE, not of the whole
        utterance, and it is measured rather than assumed in `synthesise()`.
        """
        for chunk in self._voice.synthesize(text):
            audio = getattr(chunk, "audio_int16_array", None)
            if audio is None:
                raw = getattr(chunk, "audio_int16_bytes", None)
                if raw is None:
                    continue
                audio = np.frombuffer(raw, dtype=np.int16)
            yield np.asarray(audio, dtype=np.int16).reshape(-1)
