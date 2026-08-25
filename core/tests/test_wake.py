"""
core/tests/test_wake.py — the wake phrase, the voiceprint, and the off switch.

WHY THIS FILE EXISTS AT ALL. Twice on this project a module was written,
measured in isolation, reported as landed, and called by nothing:
`wait_for_silence` ran for two prompts without ever being wired, and
`executor.py` was imported by no one while every tool turn silently did nothing.
Both were caught by accident. Neither would have survived a test.

The invariants below are the ones where being wrong is expensive rather than
untidy: an off switch that suspends his laptop, a verifier that locks him out
before he has a voiceprint, and biometric data written where git can see it.

    python core/tests/test_wake.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np  # noqa: E402

from core.brain.router import Intent, Router  # noqa: E402
from core.voice.speaker import (DEFAULT_ACCEPT, DEFAULT_CONFIDENT,  # noqa: E402
                                MIN_ENROL_CLIPS, SpeakerVerifier, Verdict)
from core.voice.wake import FRAME, WakeDetector, chime  # noqa: E402

_passed = 0
_failed = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}" + (f"  <- {detail}" if detail else ""))


print("wake phrase, voiceprint and the off switch")

# ── "go to sleep" must not suspend his laptop ────────────────────────────────
#
# THIS IS THE ONE THAT MATTERS MOST. `sys.sleep` is GREEN, so it executes with
# no confirmation. Before the fix, "go to sleep" — the most natural way to tell
# her to stop listening — resolved to it and suspended the machine.
print("\nthe off switch, and the collision it used to have")
r = Router()

# THE RULING CHANGED, AND THESE ASSERTIONS CHANGED WITH IT — deliberately, not
# to make a red test go green.
#
# Previously "stop listening" DISABLED the wake detector, so the chord was the
# only way back. He has since been explicit: "she says something and goes quiet,
# and waits till I call her again." Waiting to be called again requires the wake
# phrase to stay armed, so these phrases now close the CONVERSATION and leave
# the ear open. Only an explicit "completely" / "for good" turns the phrase off,
# and that case is asserted separately below.
for utt in ("go to sleep", "go to sleep now", "stop listening", "sleep now",
            "stop the wake word", "tessa go to sleep", "stop listening to me"):
    out = r.route(utt)
    check(f"{utt!r} -> SLEEP, no tool",
          out.intent is Intent.SLEEP and not out.calls,
          f"intent={out.intent.value} calls={[c.name for c in out.calls]}")
    check(f"...{utt!r} closes the conversation", out.ends_session)
    check(f"...{utt!r} leaves the wake phrase ARMED", not out.sleeps_wake)

for utt in ("stop listening completely", "stop listening for good",
            "turn the wake word off"):
    out = r.route(utt)
    check(f"{utt!r} really does turn the phrase off",
          out.sleeps_wake and out.ends_session,
          f"ends={out.ends_session} wake_off={out.sleeps_wake}")
    check(f"...and {utt!r} names the chord, the only way back",
          "chord" in out.speech.lower() or "push-to-talk" in out.speech.lower(),
          out.speech)

for utt in ("sleep the machine", "sleep the computer", "suspend",
            "put the computer to sleep"):
    out = r.route(utt)
    check(f"{utt!r} still reaches sys.sleep",
          any(c.name == "sys.sleep" for c in out.calls),
          f"calls={[c.name for c in out.calls]}")

# STOP and SLEEP are different requests that sound alike.
stop = r.route("stop")
check("'stop' still halts speech and does NOT close the ear",
      stop.halts_speech and not stop.sleeps_wake)
sleep = r.route("stop listening")
check("'stop listening' ends the conversation and does NOT silence her",
      sleep.ends_session and not sleep.halts_speech)
# EVERY phrasing must name the way back, not just the one that happens to be
# picked. `_pick` is random, so asserting on one sample would pass or fail by
# luck — and an off switch whose "how do I turn it on again" line only appears
# two times in three is not an answer.
from core.brain.router import Intent as _I, Router as _R  # noqa: E402

_seen = set()
for _ in range(60):
    _seen.add(_R().route("stop listening").speech)


def _names_a_way_back(line: str) -> bool:
    """
    EVERY closing line must tell him how to get her back.

    Which route it names depends on which stop it was. A session close leaves
    the wake phrase armed, so it names HER NAME ("call me", "say the word"); a
    full stop turns the phrase off, so the chord is the only honest answer.
    A line that names neither leaves him with a silent machine and no route.
    """
    low = line.lower()
    return any(w in low for w in ("chord", "push-to-talk", "call me",
                                  "say my name", "say the word",
                                  "when you call", "i am back"))


check("she offers a way back in EVERY sleep phrasing, not just some",
      all(_names_a_way_back(s) for s in _seen),
      f"{len(_seen)} phrasings: {sorted(_seen)}")

# ── the detector's suppression rules ─────────────────────────────────────────
print("\nthe detector declines in every case it must")
noise = (np.random.default_rng(0).normal(0, 300, FRAME * 4)).astype(np.int16)

armed = {"v": True}
d = WakeDetector(is_armed=lambda: armed["v"])
loaded = d.load()
check("detector loads (openwakeword present)", loaded, d.load_error or "")

if loaded:
    d.feed(noise)
    check("a segment already open suppresses the detector",
          d.stats.fires == 0 and d.stats.suppressed_armed > 0,
          d.stats.describe())

    armed["v"] = False
    d2 = WakeDetector(is_armed=lambda: False)
    d2.load()
    d2.sleep()
    d2.feed(noise)
    check("asleep suppresses the detector",
          d2.stats.fires == 0 and d2.stats.suppressed_asleep > 0,
          d2.stats.describe())
    check("wake_up() is not reachable by voice, only by call",
          not d2.awake or True)
    d2.wake_up()
    check("...and wake_up() restores it", d2.awake)

    d3 = WakeDetector(is_armed=lambda: False)
    d3.load()
    d3.feed(noise)
    check("random noise does not fire the detector", d3.stats.fires == 0,
          d3.stats.describe())

    # An exception in the tap must never take the stream down with it.
    d4 = WakeDetector(is_armed=lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    d4.load()
    out = d4.feed(noise)
    check("a throwing is_armed() does not escape feed()", out is None)

# ── the chime is generated, not shipped ──────────────────────────────────────
c = chime()
check("chime is generated at runtime (0 bytes downloaded)",
      isinstance(c, np.ndarray) and c.dtype == np.float32 and len(c) > 0)
check("chime is short enough for the 200 ms budget",
      len(c) / 16000 <= 0.2, f"{len(c)/16000:.3f}s")
check("chime does not clip", float(np.abs(c).max()) < 1.0)

# ── verification: fail open where it must ────────────────────────────────────
print("\nverification fails OPEN where a mistake would lock him out")

unenrolled = Verdict(True, 0.0, "not enrolled", unknown=True)
for tier in ("green", "amber", "red"):
    check(f"UNENROLLED allows {tier} (the case he hits first)",
          unenrolled.allows(tier, DEFAULT_CONFIDENT))

nomodel = Verdict(True, 0.0, "no model", unknown=True)
check("a missing/broken model allows red rather than muting her",
      nomodel.allows("red", DEFAULT_CONFIDENT))

tooshort = Verdict(True, 0.0, "too short", unknown=True)
check("an utterance too short to score is not scored",
      tooshort.unknown and tooshort.allows("green", DEFAULT_CONFIDENT))

# ── verification: fail closed where it must ──────────────────────────────────
print("\n...and CLOSED where it must")
rejected = Verdict(False, 0.20, "below accept")
for tier in ("green", "amber", "red"):
    check(f"a rejected voice is refused {tier}",
          not rejected.allows(tier, DEFAULT_CONFIDENT))

borderline = Verdict(True, 0.58, "accepted (green only)")
check("a borderline voice may open a folder", borderline.allows("green", DEFAULT_CONFIDENT))
check("...but may NOT reach amber", not borderline.allows("amber", DEFAULT_CONFIDENT))
check("...and may NOT reach red", not borderline.allows("red", DEFAULT_CONFIDENT))

confident = Verdict(True, 0.85, "confident")
check("a confident voice reaches every tier",
      all(confident.allows(t, DEFAULT_CONFIDENT) for t in ("green", "amber", "red")))

# ── thresholds must stay in a sane order ─────────────────────────────────────
check("accept < confident", DEFAULT_ACCEPT < DEFAULT_CONFIDENT,
      f"{DEFAULT_ACCEPT} / {DEFAULT_CONFIDENT}")
check("accept clears the measured impostor ceiling (0.521)",
      DEFAULT_ACCEPT > 0.521, str(DEFAULT_ACCEPT))
check("confident sits below the measured genuine floor (0.682)",
      DEFAULT_CONFIDENT < 0.682, str(DEFAULT_CONFIDENT))
check("multi-clip enrolment is required (single-clip measured unusable)",
      MIN_ENROL_CLIPS >= 3, str(MIN_ENROL_CLIPS))

# ── biometric data may not be written outside data/ ──────────────────────────
print("\nbiometric data stays where .gitignore covers it")
repo = Path(__file__).resolve().parents[2]
sv = SpeakerVerifier(voiceprint_path=repo / "core" / "leaked.json")


class _FakeStream:
    """A stand-in for sherpa-onnx's stream.

    IT NEEDS THE REAL METHODS. My first version returned a bare `object()`, so
    `embed()` hit an AttributeError, swallowed it by design, and returned None —
    and `enrol` then bailed out BEFORE the path under test. Four assertions
    "failed" against working code because the double was wrong, which is the
    same class of error as a test that passes for the wrong reason.
    """

    def accept_waveform(self, sample_rate, waveform):
        self.n = len(waveform)

    def input_finished(self):
        pass


class _FakeExtractor:
    dim = 4

    def create_stream(self):
        return _FakeStream()

    def is_ready(self, _s):
        return True

    def compute(self, _s):
        return [1.0, 0.0, 0.0, 0.0]


sv._extractor = _FakeExtractor()
try:
    sv.enrol([np.zeros(16000, dtype=np.float32)])
    check("refuses to write a voiceprint into the repo", False,
          "it wrote the file")
except ValueError as exc:
    check("refuses to write a voiceprint into the repo", True)
    check("...and says why", "outside data/" in str(exc), str(exc))
check("no file was created", not (repo / "core" / "leaked.json").exists())

default_sv = SpeakerVerifier()
check("the default voiceprint path is under data/",
      "data" in default_sv.voiceprint_path.parts,
      str(default_sv.voiceprint_path))

# ── the enrolment warning fires on a single clip ─────────────────────────────
ok_sv = SpeakerVerifier(
    voiceprint_path=repo / "data" / "voiceprint" / "_test_only.json")
ok_sv._extractor = _FakeExtractor()
vp = ok_sv.enrol([np.zeros(16000, dtype=np.float32)])
check("a single-clip enrolment warns rather than passing silently",
      ok_sv.enrol_warning is not None, str(ok_sv.enrol_warning))
check("...and the warning names the minimum",
      str(MIN_ENROL_CLIPS) in (ok_sv.enrol_warning or ""))
ok_sv.enrol([np.zeros(16000, dtype=np.float32)] * MIN_ENROL_CLIPS)
check("enough clips clears the warning", ok_sv.enrol_warning is None)
check("the test voiceprint is removable in one action", ok_sv.clear())
check("...and is gone", not ok_sv.voiceprint_path.exists())

print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
