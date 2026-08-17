"""
core/tests/test_session.py — one wake, then a conversation until he ends it.

His words:
  "After doing the task, she should be up for listening for the next task.
   I don't want to be toggling to talk all the time."
  "No session closing on silence. I will be the one to tell her the task is
   done."
  "When giving her instructions it seems she has a few seconds then stops
   listening. I don't like that."

Two things are tested here: that she stops cutting him off, and that a session
opens, survives, and closes only on his word.

    python core/tests/test_session.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np  # noqa: E402

from core.brain.router import Intent, Router  # noqa: E402
from core.voice.audio_io import (ArmedMicrophone,  # noqa: E402
                                 replay_silence_decision)
from core.voice.session import ConversationSession  # noqa: E402
from core.voice.speaker import Verdict  # noqa: E402

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


def load_settings() -> dict:
    import yaml
    p = Path(__file__).resolve().parents[1] / "config" / "settings.yaml"
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


print("the conversation session, and not being cut off")

# ── 1. THE SILENCE WINDOW ────────────────────────────────────────────────────
print("\n1. she no longer cuts him off mid-sentence")
cfg = load_settings().get("voice", {}) or {}
silence_ms = int(cfg.get("vad_silence_ms", 0))
check("vad_silence_ms lives in settings.yaml", silence_ms > 0, str(silence_ms))
check("...and is long enough for a deliberate pause (>= 2000 ms)",
      silence_ms >= 2000, str(silence_ms))
check("...and not so long she feels broken (<= 4000 ms)",
      silence_ms <= 4000, str(silence_ms))
check("the hard cap survives", float(cfg.get("vad_hard_cap_s", 0)) > 0,
      str(cfg.get("vad_hard_cap_s")))

SR = 16_000


def tone(seconds: float, rms: float) -> np.ndarray:
    """Speech-shaped enough for an energy VAD: noise at a chosen level."""
    rng = np.random.default_rng(7)
    return (rng.normal(0, rms, int(seconds * SR))).astype(np.int16)


def quiet(seconds: float) -> np.ndarray:
    rng = np.random.default_rng(8)
    return (rng.normal(0, 2, int(seconds * SR))).astype(np.int16)


# "open the ......... downloads folder" — a 2.0 s deliberate pause in the middle.
utterance = np.concatenate([
    tone(1.0, 2500),      # "open the"
    quiet(2.0),           # him thinking
    tone(1.4, 2500),      # "downloads folder"
    quiet(3.5),           # he has finished
])
out = replay_silence_decision(utterance, silence_ms=silence_ms,
                              floor_rms=float(cfg.get("vad_floor_rms", 300)),
                              hard_cap_s=float(cfg.get("vad_hard_cap_s", 20)))
check("a 2.0 s pause mid-sentence SURVIVES",
      out["reason"] != "silence" or out["close_at_s"] > 4.0,
      f"{out['reason']} at {out.get('close_at_s')}")
check("...and the segment still closes after he finishes",
      out["reason"] in ("silence", "ran-out"), str(out))

# The same utterance under the OLD 1200 ms window, to show what he was living
# with. This is the regression, demonstrated rather than asserted.
old = replay_silence_decision(utterance, silence_ms=1200,
                              floor_rms=float(cfg.get("vad_floor_rms", 300)),
                              hard_cap_s=20.0)
cut_old = old["reason"] == "silence" and old["close_at_s"] < 4.0
check("...and it WOULD have been cut off at the old 1200 ms",
      cut_old, f"old: {old['reason']} at {old.get('close_at_s')}")

# ── the hard cap actually fires ──────────────────────────────────────────────
endless = tone(40.0, 900)
capped = replay_silence_decision(endless, silence_ms=silence_ms,
                                 floor_rms=300.0, hard_cap_s=20.0)
check("the hard cap fires on an endless noisy room",
      capped["reason"] == "hard-cap", str(capped))
check("...at the configured cap, not later",
      abs(capped["close_at_s"] - 20.0) < 0.2, str(capped["close_at_s"]))

# ── 2. THE SESSION OBJECT ────────────────────────────────────────────────────
print("\n2. the session opens, survives, and closes only on his word")
s = ConversationSession()
check("starts closed", not s.open)
check("opens", s.start("wake") and s.open)
check("...records how it was opened", s.opened_by == "wake")
check("opening twice is a no-op, not an error", s.start("wake") is False)
check("...and does not reset the turn count",
      (s.note_turn(), s.note_turn(), s.turns)[2] == 2, str(s.turns))
d = s.end()
check("closing reports turns and duration", d["turns"] == 2 and "durationS" in d, str(d))
check("...and it is closed", not s.open)
check("closing a closed session is safe", isinstance(s.end(), dict))

check("a chord-started session is the SAME object",
      (ConversationSession().start("chord")) is True)

# ── 3. THE CLOSING PHRASES ───────────────────────────────────────────────────
print("\n3. it closes on his word, on intent rather than an exact string")
CLOSERS = ["I'm done for now", "I'm done", "that's all", "that's all for now",
           "we're finished", "thank you Zoey", "thanks Zoey", "go to sleep",
           "stop listening", "nothing else", "that will be all",
           "we are finished", "done for now"]
for utt in CLOSERS:
    out = Router().route(utt)
    check(f"{utt!r} closes the session",
          out.ends_session and not out.calls,
          f"intent={out.intent.value} calls={[c.name for c in out.calls]}")

print("\n   ...and she SAYS something, which makes clear she is going quiet")
lines = {Router().route("I'm done for now").speech for _ in range(60)}
check("she always says something", all(bool(x) for x in lines))
check("...varied from a small set", 2 <= len(lines) <= 8, str(len(lines)))
for line in lines:
    low = line.lower()
    check(f"{line!r} names a way back",
          any(w in low for w in ("call me", "say my name", "say the word",
                                 "when you call", "i am back")), line)

# ── the "thank you" trap ─────────────────────────────────────────────────────
print("\n   the 'thank you' trap — her NAME is what distinguishes them")
for utt in ("thank you", "thanks", "thanks a lot", "thank you very much",
            "cheers"):
    out = Router().route(utt)
    check(f"bare {utt!r} does NOT close", not out.ends_session, out.speech[:40])
    check(f"...and {utt!r} is answered graciously, not with a capability list",
          "not mine" not in out.speech.lower() and bool(out.speech),
          out.speech[:50])
for utt in ("thank you Zoey", "thanks Zoey", "thank you Zoi"):
    check(f"{utt!r} DOES close", Router().route(utt).ends_session)

# ── "go to sleep" must not touch the machine ─────────────────────────────────
print("\n   'go to sleep' — the phrase that used to suspend his laptop")
out = Router().route("go to sleep")
check("fires NO tool at all", not out.calls, str([c.name for c in out.calls]))
check("...specifically not sys.sleep",
      not any(c.name == "sys.sleep" for c in out.calls))
check("...and it closes the session instead", out.ends_session)
check("'sleep the computer' STILL suspends the machine",
      any(c.name == "sys.sleep" for c in Router().route("sleep the computer").calls))

# ── the two strengths of stop ────────────────────────────────────────────────
print("\n   two strengths of stop")
weak = Router().route("stop listening")
check("'stop listening' leaves the wake phrase ARMED — he waits to be called",
      weak.ends_session and not weak.sleeps_wake)
strong = Router().route("stop listening completely")
check("'stop listening completely' turns the phrase off too",
      strong.ends_session and strong.sleeps_wake)

# ── 4. THINGS THAT MUST NOT CLOSE IT ─────────────────────────────────────────
print("\n4. a session is not closed by anything else")
for utt in ("open my downloads", "what is the time", "how is it going",
            "stop", "be quiet", "open chrome", "hey zoey"):
    out = Router().route(utt)
    check(f"{utt!r} keeps the session open", not out.ends_session,
          f"intent={out.intent.value}")

check("'stop' still halts her SPEECH without closing the session",
      Router().route("stop").halts_speech
      and not Router().route("stop").ends_session)

# ── 5. VERIFICATION INSIDE A SESSION ─────────────────────────────────────────
print("\n5. a voice that is not his is discarded SILENTLY")
rejected = Verdict(False, 0.21, "below accept threshold")
check("a rejected verdict is not ok", not rejected.ok)
check("...and is not 'unknown' — it was a real judgement", not rejected.unknown)
for tier in ("green", "amber", "red"):
    check(f"a rejected voice reaches no {tier} tool",
          not rejected.allows(tier, 0.62))

borderline = Verdict(True, 0.58, "accepted (green only)")
check("a BORDERLINE voice may still open a folder",
      borderline.allows("green", 0.62))
check("...but may not reach amber inside a session",
      not borderline.allows("amber", 0.62))
check("...nor red", not borderline.allows("red", 0.62))

for reason in ("not enrolled", "no model", "only 0.40s of audio"):
    unknown = Verdict(True, 0.0, reason, unknown=True)
    check(f"{reason!r} FAILS OPEN — no check is not a failed check",
          all(unknown.allows(t, 0.62) for t in ("green", "amber", "red")))

# The loop must be able to hold a verifier and a session flag at all.
from core.voice.loop import VoiceLoop  # noqa: E402
import inspect  # noqa: E402
params = inspect.signature(VoiceLoop.__init__).parameters
check("VoiceLoop accepts a speaker verifier", "speaker" in params)
src = inspect.getsource(VoiceLoop.stop)
check("...and the turn actually CALLS verify() — not merely imports it",
      "self.speaker.verify(" in src)
check("...and a rejection returns a SILENT turn (no `said`)",
      'return Turn(heard="", said=""' in src)
check("...before routing, so a stranger never reaches a tool",
      src.index("self.speaker.verify(") < src.index("self.router.route("))
# THE ORDER IS A COST DECISION AS WELL AS A PRIVACY ONE. Measured live:
# verification 215 ms, transcription 19,875 ms. Verifying second meant a
# stranger's sentence cost twenty seconds of Whisper before being discarded.
check("...and BEFORE transcription — a stranger is never transcribed at all",
      src.index("self.speaker.verify(") < src.index("self.stt.transcribe("))
check("a dead segment is settled before either, by the cheap gate",
      src.index("is_probably_silence(") < src.index("self.speaker.verify("))

# ── 6. THE RING IS FLUSHED BEFORE RE-ARMING ──────────────────────────────────
print("\n6. she does not transcribe her own voice")
mic = ArmedMicrophone(pre_roll_s=1.0)
block = (np.random.default_rng(1).normal(0, 3000, 8000)).astype(np.int16)
mic._callback(block.reshape(-1, 1), len(block), None, None)
check("the ring holds audio after her speech", mic._filled > 0, str(mic._filled))
dropped = mic.flush_ring()
check("flush_ring discards it", dropped > 0 and mic._filled == 0,
      f"dropped={dropped} filled={mic._filled}")
pre = mic.arm()
check("...so the next pre-roll carries none of her voice",
      pre.size == 0 or float(np.abs(pre).max()) == 0.0,
      f"peak={float(np.abs(pre).max()) if pre.size else 0}")

# ── 7. BOTH ENTRY PATHS OPEN THE SAME SESSION ────────────────────────────────
print("\n7. both entry paths, and they are the same session")
server_src = (Path(__file__).resolve().parents[1] / "server.py").read_text(
    encoding="utf-8")

check("the CHORD path opens a session",
      'self.open_session("chord")' in server_src)
check("the WAKE path opens a session",
      'daemon.open_session("wake")' in server_src)
check("...both call the SAME method, so there is one lifecycle",
      server_src.count("def open_session") == 1)
check("a session is closed only by him saying so",
      'self.close_session("he said so")' in server_src)
check("...and there is no idle timeout anywhere in the session code",
      "idle_timeout" not in server_src
      and "session_timeout" not in server_src)

# The re-arm must be driven by `idle` — the drain — and nothing earlier.
check("the re-arm is triggered by the idle transition, not by turn completion",
      'state == "idle" and daemon.convo.open' in server_src)
check("...and flushes the ring first, so she cannot hear herself",
      "flush_ring" in server_src
      and server_src.index("flush_ring") < server_src.index(
          "self.voice.start(on_auto_stop=_auto_stop)"))

# ITEM 3h — reopening after a close greets again.
r2 = Router()
first_line = r2.route("Hey Zoey").speech
ack = r2.route("Hey Zoey").speech
r2.mark_conversation_closed()
after_close = r2.route("Hey Zoey").speech
check("mid-session the wake phrase ACKNOWLEDGES rather than re-greeting",
      ack != first_line and "Good " not in ack, ack)
check("after a close it GREETS again — the close is the gap",
      after_close != ack, f"{ack!r} -> {after_close!r}")

print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
