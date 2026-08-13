"""
core/brain/router.py — spec §5.3's FIRST BRANCH ONLY: exact local matches.

WHAT THIS IS AND IS NOT

This is the branch that answers on-machine, in zero milliseconds, with no
network and no API key. It exists because a large fraction of what anyone says
to an assistant all day is "what time is it" and "are you there", and paying a
round trip plus a token bill for those is absurd. It also means Zoey still
answers those when the connection is down, which on a metered Lagos link is not
a hypothetical.

It is NOT the agent loop, and it deliberately cannot become one by accident:
there is no fall-through to a model, no tool dispatch, no plan step. Anything
not matched here returns UNROUTED and the caller says so out loud.

NO PERSONALITY. The sentences below are plain and correct. Character lands
tomorrow, and writing it now would mean writing it twice — worse, it would mean
tomorrow's voice being a rewrite of a voice Gerald had already started getting
used to.

UNROUTED SPEAKS. The alternative to "I do not have that yet" is silence, and
silence is indistinguishable from a crash. Saying what she cannot do is more
useful than saying nothing, and it is honest about the state of the system.
"""

from __future__ import annotations

import platform
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Callable


class Intent(str, Enum):
    TIME = "time"
    DATE = "date"
    PRESENCE = "presence"
    STOP = "stop"
    STATUS = "status"
    UNROUTED = "unrouted"


@dataclass(frozen=True)
class Routed:
    intent: Intent
    speech: str
    #: True only for STOP — the caller must halt the speaker rather than reply.
    halts_speech: bool = False

    @property
    def handled_locally(self) -> bool:
        return self.intent is not Intent.UNROUTED


def _normalise(text: str) -> str:
    """
    Lowercase, strip terminal punctuation and filler.

    Deliberately conservative: this is an EXACT-match branch, and a normaliser
    that tries to be clever is how "delete the build folder" starts matching
    "stop". Anything ambiguous should fall through to UNROUTED, where it is
    visible, rather than be guessed at here.
    """
    t = text.lower().strip()
    for ch in ".,!?;:":
        t = t.replace(ch, "")
    t = " ".join(t.split())
    for lead in ("zoey ", "hey zoey ", "ok zoey "):
        if t.startswith(lead):
            t = t[len(lead):]
            break
    return t.strip()


#: Exact phrases only. A phrase appears here because Gerald says it, not because
#: it seemed likely — the list grows from transcripts, not from imagination.
_EXACT: dict[str, Intent] = {
    "what time is it": Intent.TIME,
    "whats the time": Intent.TIME,
    "what is the time": Intent.TIME,
    "the time": Intent.TIME,
    "whats the date": Intent.DATE,
    "what is the date": Intent.DATE,
    "what day is it": Intent.DATE,
    "todays date": Intent.DATE,
    "are you there": Intent.PRESENCE,
    "hello": Intent.PRESENCE,
    "hi": Intent.PRESENCE,
    "you there": Intent.PRESENCE,
    "stop": Intent.STOP,
    "mute": Intent.STOP,
    "stand down": Intent.STOP,
    "be quiet": Intent.STOP,
    "how are you": Intent.STATUS,
    "status": Intent.STATUS,
    "hows it going": Intent.STATUS,
    "system status": Intent.STATUS,
}


class Router:
    """
    `health_sample` is injected rather than imported so STATUS reports the SAME
    numbers `evt.daemon.health` broadcasts. Two sources for one fact is how a
    status report starts quietly lying.
    """

    def __init__(self, health_sample: Callable[[], dict] | None = None) -> None:
        self._health = health_sample

    def route(self, text: str) -> Routed:
        norm = _normalise(text)
        intent = _EXACT.get(norm, Intent.UNROUTED)

        # %-I / %-d are glibc extensions and raise on Windows, so the leading
        # zero is stripped in Python rather than in the format string.
        if intent is Intent.TIME:
            now = datetime.now()
            return Routed(intent, f"It is {now.strftime('%I:%M %p').lstrip('0')}.")

        # ── THE FIRST-SENTENCE RULE ──────────────────────────────────────────
        #
        # Piper streams at SENTENCE granularity, so time-to-first-audio is the
        # cost of the first sentence alone. Measured on this machine: a 15-char
        # opener is 235 ms, a 31-char opener is 685 ms, a 47-char opener is
        # 660 ms — and spec §4 allows 400 ms.
        #
        # So every answer below opens SHORT and puts the detail in a second
        # sentence, which synthesises while the first is already playing. This
        # is a constraint on how Zoey talks, not on the TTS layer, and it is
        # cheaper to honour here than to optimise around later.
        if intent is Intent.DATE:
            now = datetime.now()
            day = str(now.day)
            return Routed(intent, (
                f"It is {now.strftime('%A')}. "
                f"The date is {day} {now.strftime('%B %Y')}."
            ))

        if intent is Intent.PRESENCE:
            return Routed(intent, "Yes, I am here.")

        if intent is Intent.STOP:
            return Routed(intent, "", halts_speech=True)

        if intent is Intent.STATUS:
            if self._health is None:
                return Routed(intent, "I cannot read my own health right now.")
            h = self._health()
            up = float(h.get("uptimeS", 0.0))
            hours, rem = divmod(int(up), 3600)
            mins = rem // 60
            uptime = f"{hours} hours and {mins} minutes" if hours else f"{mins} minutes"
            # Short opener first — see THE FIRST-SENTENCE RULE above.
            return Routed(intent, (
                "I am running. "
                f"Uptime is {uptime}. "
                f"CPU is at {float(h.get('cpuPct', 0)):.0f} percent, "
                f"memory {float(h.get('memMB', 0)):.0f} megabytes. "
                f"Today I have spent {float(h.get('budgetSpent', 0)):.0f} naira "
                f"of a {float(h.get('budgetCap', 0)):.0f} naira cap."
            ))

        # UNROUTED. No fall-through, no model, no tool. She says so.
        # Short opener first — see THE FIRST-SENTENCE RULE above.
        return Routed(Intent.UNROUTED, (
            "I heard you. "
            "I do not have that yet. "
            "Right now I can tell you the time, the date, and how I am running."
        ))


def platform_note() -> str:
    return f"{platform.system()} {platform.release()}"
