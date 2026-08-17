"""
core/voice/session.py — one wake, then a conversation until HE ends it.

────────────────────────────────────────────────────────────────────────────────
HIS WORDS, WHICH ARE THE SPEC

  "When I wake her with the wake phrase and she answers, we have a little chat,
   then I give her what to do. After doing the task, she should be up for
   listening for the next task. I don't want to be toggling to talk all the
   time."

  "No session closing on silence. I will be the one to tell her the task is
   done."

So: opens on a wake phrase OR a keypress, stays open across any number of turns,
and closes ONLY when he says so. There is no idle timeout and no turn limit, by
his explicit ruling — if he walks away for an hour and comes back and speaks,
she is still there.

────────────────────────────────────────────────────────────────────────────────
WHAT RE-ARMS, AND WHEN — the part that is easy to get wrong

The microphone is re-armed when PLAYBACK DRAINS, not when the turn's code
finishes. `AudioBus` emits `idle` from its output stream's `finished_callback`,
which is the only moment that is actually true.

Arming any earlier means she records HERSELF. Piper's audio leaves the speaker,
the microphone picks it up, Whisper transcribes it, and she answers her own
sentence — forever. There is no acoustic echo cancellation in this daemon, and
that is precisely why the segment must not be open while she is talking.

THE CONSEQUENCE, STATED PLAINLY: voice barge-in DURING her speech is not
supported. He cannot talk over her and be heard. The keypress still barges in —
measured at 34 ms p95 — and that remains his interrupt. Claiming otherwise would
be a lie he discovers on his first impatient sentence.

────────────────────────────────────────────────────────────────────────────────
WHY THIS IS ONLY SAFE WITH SPEAKER VERIFICATION

An open session is a microphone live in a room with no keypress gating it. The
thing that makes that acceptable rather than reckless is that a voice which is
not his is DISCARDED SILENTLY — not answered, not refused aloud. A spoken
refusal every time someone else in the room talks would be worse than not
listening at all.

That is enforced in the voice loop, not here, but it is the reason this module
is allowed to exist.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class ConversationSession:
    """
    Process state, deliberately. It cannot and must not survive a restart.

    A session is "the microphone is currently open and listening for him". If
    the daemon dies, the microphone closes with it, so a session that claimed to
    survive would be describing a microphone that is not open — the indicator
    would be lying about the one fact it exists to report.
    """

    open: bool = False
    opened_at: float = 0.0
    opened_by: str = ""          # "wake" | "chord"
    turns: int = 0
    #: Set while a tool is executing, so a re-arm cannot race a running action.
    busy: bool = False
    _history: list = field(default_factory=list)

    def start(self, by: str) -> bool:
        """Open a session. Returns False if one was already open (idempotent)."""
        if self.open:
            return False
        self.open = True
        self.opened_at = time.time()
        self.opened_by = by
        self.turns = 0
        return True

    def note_turn(self) -> None:
        self.turns += 1

    def end(self) -> dict:
        """Close it and hand back what the audit line needs."""
        detail = {
            "openedBy": self.opened_by,
            "turns": self.turns,
            "durationS": round(time.time() - self.opened_at, 1) if self.opened_at else 0.0,
        }
        self.open = False
        self.opened_at = 0.0
        self.opened_by = ""
        self.turns = 0
        return detail

    @property
    def duration_s(self) -> float:
        return (time.time() - self.opened_at) if self.open and self.opened_at else 0.0

    def describe(self) -> str:
        if not self.open:
            return "session: closed"
        return (f"session: OPEN {self.duration_s:.0f}s, {self.turns} turn(s), "
                f"opened by {self.opened_by}")
