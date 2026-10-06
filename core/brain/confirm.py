"""
core/brain/confirm.py — the hold, and the thing that ends it.

WHAT WAS BROKEN

`destructive_hold()` has been in router.py for three prompts saying "Say it
again and I will do it." Nothing anywhere remembered that she had said it, so
saying it again produced the same hold, forever. The sentence was true about her
intent and false about her behaviour, which is the worst kind of line for an
assistant to have — he would have trusted it, tried it, and found the machine
simply refusing him twice.

WHAT A CONFIRMATION HAS TO GET RIGHT

  1. IT EXPIRES. A hold from ten minutes ago must not be completed by an
     unrelated "yes" while he is on the phone. 60 seconds, and then she has
     forgotten it and will ask again.
  2. IT IS SPECIFIC. "Delete the drafts folder" followed by "delete the logs
     folder" is TWO holds, not a confirmation of the first. Matching is on
     (tool, args), so the second replaces the first rather than arming it.
  3. THE REPEAT COUNTS, AND SO DOES A PLAIN YES. He will do both. Repeating the
     command is the phrasing she promised; "yes", "do it", "go ahead" is what
     people actually say to a machine that just asked them something.
  4. NO IS LOUDER THAN YES. A cancel word clears the hold immediately and she
     confirms that nothing happened. Ambiguity resolves toward not destroying
     things.
  5. ONE HOLD AT A TIME. A queue of pending destructive actions is a way to
     confirm the wrong one.

WHY 60 SECONDS

Long enough for him to look at the screen, read what she is about to delete, and
answer. Short enough that it cannot survive him walking away — which is the
scenario that matters, because an unattended "yes" is how the confirmation stops
being a control at all.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

#: A hold he has not answered inside this window is forgotten.
HOLD_TTL_S = 60.0

_YES = re.compile(
    r"^\s*(yes|yeah|yep|yup|do it|go ahead|go on|confirm|confirmed|proceed|"
    r"i confirm|that's right|thats right|affirmative|please do|do that)\b", re.I)

_NO = re.compile(
    r"^\s*(no|nope|don't|dont|do not|cancel|stop|leave it|forget it|never mind|"
    r"nevermind|abort|wait)\b", re.I)


@dataclass(frozen=True)
class Hold:
    tool: str
    args: dict[str, Any]
    detail: str
    at: float
    #: Who built the call that was held. Carried so that a "yes" re-runs the
    #: action with the SAME origin it arrived with — a model-initiated amber
    #: action confirmed by voice must not be re-dispatched as "human".
    #: Defaults to "schedule", the fail-safe actor: a hold armed without saying
    #: who asked is not the owner's, on the chain or on a repeat.
    origin: str = "schedule"

    def expired(self, now: float | None = None) -> bool:
        return ((now or time.monotonic()) - self.at) > HOLD_TTL_S

    def same_as(self, tool: str, args: dict[str, Any], origin: str) -> bool:
        """
        Same tool, same target, AND the same origin.

        `confirmed` is excluded from the comparison because the second utterance
        is the identical command — the flag is set by the ledger, not by him,
        and comparing it would make a repeat never match.

        ORIGIN IS PART OF THE MATCH, and it was not. A repeat is a confirmation,
        and a confirmation has to come from whoever was asked. Comparing tool
        and args alone meant an AGENT-built call identical to the one the OWNER
        was holding satisfied HIS hold — the agent rode his pending approval
        and the action ran on a "yes" he never gave. A repeat from a different
        origin is a different request: it does not confirm this one.
        """
        if tool != self.tool:
            return False
        if origin != self.origin:
            return False
        a = {k: v for k, v in args.items() if k != "confirmed"}
        b = {k: v for k, v in self.args.items() if k != "confirmed"}
        return a == b


class ConfirmLedger:
    """One pending hold, or none."""

    def __init__(self) -> None:
        self._hold: Hold | None = None

    @property
    def pending(self) -> Hold | None:
        if self._hold is not None and self._hold.expired():
            self._hold = None
        return self._hold

    def arm(self, tool: str, args: dict[str, Any], detail: str,
            origin: str = "schedule") -> None:
        self._hold = Hold(tool=tool, args=dict(args), detail=detail,
                          at=time.monotonic(), origin=origin)

    def clear(self) -> None:
        self._hold = None

    # ── the two ways a hold ends ─────────────────────────────────────────────

    def resolve_utterance(self, text: str) -> tuple[str, Hold | None]:
        """
        Read a bare "yes"/"no" against the pending hold.

        Returns ("confirm" | "cancel" | "none", hold). "none" means the
        utterance was not an answer at all and should be routed normally —
        which is what lets him say "actually, what time is it" mid-hold without
        that becoming a confirmation of anything.
        """
        held = self.pending
        if held is None:
            return "none", None
        t = (text or "").strip()
        if _NO.match(t):
            self._hold = None
            return "cancel", held
        if _YES.match(t):
            self._hold = None
            return "confirm", held
        return "none", held

    def resolve_repeat(self, tool: str, args: dict[str, Any],
                       origin: str = "schedule") -> Hold | None:
        """
        He said the same destructive thing again. That IS the confirmation, and
        it is the one she promised out loud.

        `origin` is who is repeating it, and it must match who armed the hold
        (see `Hold.same_as`). The default is the fail-safe one: a caller that
        does not say who is repeating can only confirm a hold that nobody
        claimed either, never the owner's. The executor always passes the
        resolved origin of the call in hand.
        """
        held = self.pending
        if held is not None and held.same_as(tool, args, origin):
            self._hold = None
            return held
        return None
