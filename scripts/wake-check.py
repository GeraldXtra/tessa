"""
scripts/wake-check.py — say the phrase, see the score.

    python scripts/wake-check.py

Records your microphone for ~12 seconds and prints what the wake model actually
scores, frame by frame. Nothing else in the daemon is touched and nothing is
saved to disk.

⚠ THE DAEMON CAN KEEP RUNNING. Measured on this machine and written down in
core/voice/wake.py: a second input stream on the default device works here,
because the default host API is MME and it shares. If this script cannot open
the microphone anyway, stop the daemon, run it again, and start the daemon
after — but try it first, because stopping the daemon is a nuisance and is
probably not needed.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
cls
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SECONDS = 12
FRAME = 1280            # 80 ms at 16 kHz — openWakeWord's native frame
RATE = 16_000


def main() -> int:
    import sounddevice as sd
    import yaml

    cfg = yaml.safe_load((ROOT / "core/config/settings.yaml").read_text(encoding="utf-8")) or {}
    wake_cfg = ((cfg.get("voice") or {}).get("wake") or {})
    threshold = float(wake_cfg.get("threshold", 0.5))
    model_path = (wake_cfg.get("model") or "").strip()

    from core.voice.wake import WakeDetector

    det = WakeDetector(model_path=model_path or None, threshold=threshold)
    if not det.load():
        print(f"THE MODEL DID NOT LOAD: {det.load_error}")
        return 2

    phrase = det.phrase
    print()
    print("=" * 68)
    print(f"  wake model loaded : {phrase}")
    print(f"  threshold         : {threshold}")
    print(f"  microphone        : {sd.query_devices(kind='input')['name']}")
    print("=" * 68)
    print()
    print(f"  ⚠ THE PHRASE IS \"{phrase.replace('_', ' ').upper()}\" — not 'Hey Tessa'.")
    print()
    print("  When it says GO, say the phrase THREE times, with a pause between.")
    print("  Speak normally, at the distance you would really use.")
    print()

    scores: list[float] = []
    peaks: list[tuple[float, float]] = []
    loudest = 0.0
    t_start = [0.0]

    def on_block(indata, frames, time_info, status):  # noqa: ANN001, ARG001
        nonlocal loudest
        pcm = (indata[:, 0] * 32767.0).astype(np.int16)
        rms = float(np.sqrt(np.mean(pcm.astype(np.float64) ** 2))) if pcm.size else 0.0
        loudest = max(loudest, rms)
        # Score the frame directly rather than through `feed()`, because feed()
        # applies the refractory window and the armed-suppression rules — this
        # script wants the RAW model output, including the frames feed() would
        # have swallowed.
        got = det._model.predict(pcm)          # noqa: SLF001
        s = float(max(got.values())) if got else 0.0
        scores.append(s)
        if s > 0.05:
            peaks.append((time.perf_counter() - t_start[0], s))

    for n in (3, 2, 1):
        print(f"  starting in {n}...", end="\r", flush=True)
        time.sleep(1)
    print("  >>> GO — say it three times <<<          ")
    t_start[0] = time.perf_counter()
    with sd.InputStream(samplerate=RATE, channels=1, dtype="float32",
                        blocksize=FRAME, callback=on_block):
        for i in range(SECONDS):
            print(f"  listening... {SECONDS - i:2d}s   "
                  f"loudest so far {loudest:6.0f} rms", end="\r", flush=True)
            time.sleep(1)
    print(" " * 60, end="\r")

    top = sorted(scores, reverse=True)[:10]
    fired = [s for s in scores if s >= threshold]
    print()
    print("=" * 68)
    print(f"  frames scored     : {len(scores)}")
    print(f"  loudest audio     : {loudest:.0f} rms   "
          f"({'plenty' if loudest > 800 else 'QUIET — is this the right mic?'})")
    print(f"  highest score     : {max(scores) if scores else 0:.3f}")
    print(f"  top ten scores    : {', '.join(f'{s:.3f}' for s in top)}")
    print(f"  frames >= {threshold}    : {len(fired)}")
    print("=" * 68)
    print()
    if loudest < 200:
        print("  VERDICT: the microphone barely heard anything. Wrong input device,")
        print("           or muted. Fix that before reading the scores.")
    elif not scores:
        print("  VERDICT: no frames were scored at all — the stream never delivered.")
    elif max(scores) >= threshold:
        print(f"  VERDICT: IT FIRES. Peak {max(scores):.3f} against a {threshold} bar.")
        print("           The wake word will work. Restart the daemon and use it.")
    elif max(scores) >= 0.2:
        print(f"  VERDICT: CLOSE. Peak {max(scores):.3f}, bar {threshold}.")
        print(f"           Lowering voice.wake.threshold to about "
              f"{max(0.15, round(max(scores) * 0.7, 2))} should catch it.")
    else:
        print(f"  VERDICT: THE MODEL DOES NOT RECOGNISE WHAT YOU SAID. Peak "
              f"{max(scores):.3f}.")
        print(f"           If you were saying 'Hey Tessa', that is expected — this")
        print(f"           model only knows '{phrase.replace('_', ' ')}'. Run it again")
        print(f"           saying '{phrase.replace('_', ' ')}' and compare.")
    print()
    if peaks:
        print("  when it heard something (seconds, score):")
        for t, s in peaks[:12]:
            print(f"    {t:5.1f}s  {s:.3f}  {'#' * int(s * 40)}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
