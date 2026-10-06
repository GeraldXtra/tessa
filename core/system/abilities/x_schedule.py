"""
core/system/abilities/x_schedule.py — draft a batch, approve it once, post it
across the day.

    "post five tweets about the audit work today"
        -> prepare_batch()  drafts five, in his voice
        -> five RED cards, all pending at once
        -> he reads them ONE SITTING: approve / edit the wording / deny
        -> each APPROVAL enqueues that one post at its time
        -> the tick sends them, one per tick, through the round-3 path

────────────────────────────────────────────────────────────────────────────────
⚠⚠ THE APPROVAL IS NOT REMOVED. IT IS MOVED TO THE FRONT.

`system.x.post_tweet` is RED. Dispatching it raises a card and stops, exactly as
round 3 does — the batch is simply N of those cards outstanding at once, which
the gate already supports (MAX_PENDING is 32). What changes is what APPROVAL
does: with `at` empty it posts immediately, and with `at` set it puts the
already-approved post in the queue.

That is why nothing unapproved can ever be scheduled. The enqueue happens
INSIDE the handler, and the handler runs only from `execute_approved`, after the
forgeable-flag strip. `schedule.enqueue()` refuses without the approval flag as
a second, independent check.

⚠ `at` AND `topic` ARE FROZEN on the card. The wording is his to correct — that
is what the card is for — but the TIME is not, for the same reason round 3
freezes a reply's target: he approved a specific thing to happen at a specific
moment, and an edited frame must not be able to move it. A reply's
`reply_to_id` stays frozen through the queue too, so a scheduled reply lands
under the post he read.
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Any

from core.system.capability import Capability, Param

#: The most posts one instruction may queue. Not a technical limit — a brake.
#: See the report's note on what a high-volume timer looks like to X.
MAX_BATCH = 12

_HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def _at_epoch(at: str, clock: Any = time.time) -> float:
    """
    "14:30" or "+90m" or an epoch, to epoch seconds. A time already past today
    is refused rather than quietly rolled to tomorrow — posting a day late is
    not what he asked for, and guessing which day he meant is how that happens.
    """
    from core.tools.base import ToolError

    raw = str(at or "").strip()
    if not raw:
        return 0.0
    now = float(clock())
    m = re.fullmatch(r"\+(\d{1,4})\s*([mh])", raw, re.I)
    if m:
        mult = 60 if m.group(2).lower() == "m" else 3600
        return now + int(m.group(1)) * mult
    if _HHMM.match(raw):
        hh, mm = (int(x) for x in raw.split(":"))
        today = datetime.fromtimestamp(now).replace(hour=hh, minute=mm, second=0, microsecond=0)
        when = today.timestamp()
        if when < now - 60:
            raise ToolError(f"{raw} is already past today",
                            "Give me a later time, or say plus ninety minutes.")
        return when
    try:
        return float(raw)
    except ValueError:
        raise ToolError(f"I cannot read {raw!r} as a time",
                        "Say it as 14:30, or as plus ninety minutes.") from None


def _store():
    from core.system import schedule

    return schedule.ScheduleStore()


def prepare_batch(topic: str, n: int = 3, first: str = "+30m",
                  every_minutes: int = 90) -> list[dict[str, Any]]:
    """
    Draft N posts about `topic` and return the ARGUMENTS for N red calls.

    Publishes nothing and schedules nothing. The caller dispatches each dict,
    which raises one card each; the batch is those cards.

    ⚠ SPACED, NOT SIMULTANEOUS. Even approved, N posts landing in one minute is
    the pattern that gets an account actioned. Defaults to ninety minutes apart.
    """
    from core.brain import x_voice
    from core.tools.base import ToolError

    from .x_draft import _engine

    count = max(1, min(int(n), MAX_BATCH))
    style = x_voice.load_profile()
    if style is None:
        raise ToolError("I have not learned your voice yet",
                        "Say learn my voice from X first, and I will read your profile once.")
    subject = str(topic or "").strip()
    if not subject:
        raise ToolError("no topic came through", "Tell me what they should be about.")

    start = _at_epoch(first)
    engine = _engine()
    out: list[dict[str, Any]] = []
    for i in range(count):
        # Each draft is a standalone post, so there is no source tweet and
        # nothing to fence — the topic is HIS words, not a stranger's.
        source = {"id": "", "handle": "", "text": subject}
        draft = x_voice.draft_reply(
            source, style, engine,
            guidance=(f"Write post {i + 1} of {count} about: {subject}. "
                      f"A standalone post, not a reply. Make it different from the others."))
        out.append({
            "text": draft.text,
            "at": str(start + i * every_minutes * 60),
            "topic": subject,
        })
    return out


def post_tweet(text: str, at: str = "", topic: str = "",
               _approved_by_surface: bool = False, _request_id: str = "") -> dict[str, Any]:
    """
    RED. Post now, or — when `at` is given — put the APPROVED post in the queue.

    Reached only through the approval card. `_approved_by_surface` is threaded
    from the framework, never fabricated (the laundering bug round 3 found), so
    the tool below and `schedule.enqueue` both get the real value.
    """
    from core.system import schedule
    from core.tools import x_tools

    approved = bool(_approved_by_surface)
    body = str(text or "").strip()

    if not str(at or "").strip():
        result = x_tools.post(text=body, _approved_by_surface=approved)
        return {"chars": result.get("chars", len(body)), "when": "now",
                "at": "", "queued": 0, "topic": topic,
                "verdict": f"Posted, Emperor. {result.get('chars', len(body))} characters."}

    when = _at_epoch(at)
    store = _store()
    item = store.enqueue(kind="tweet", text=body, at=when, approved=approved,
                         topic=topic, request_id=str(_request_id or ""))
    clock_s = datetime.fromtimestamp(when).strftime("%H:%M")
    pending = store.counts().get("pending", 0)
    return {"chars": len(body), "when": clock_s, "at": at, "queued": 1,
            "id": item.id, "topic": topic, "pending": pending,
            "verdict": f"Queued for {clock_s}, Emperor. {pending} waiting."}


def schedule_status() -> dict[str, Any]:
    """What is waiting, what went, what was missed. Reads the queue only."""
    store = _store()
    counts = store.counts()
    nxt = sorted((i for i in store.items if i.pending), key=lambda i: i.at)
    when = (datetime.fromtimestamp(nxt[0].at).strftime("%H:%M") if nxt else "")
    missed = [i for i in store.items if i.status == "missed"]
    return {
        "pending": counts.get("pending", 0), "fired": counts.get("fired", 0),
        "missed": len(missed), "failed": counts.get("failed", 0),
        "next_at": when,
        "summary": (f"{counts.get('pending', 0)} waiting, Emperor"
                    + (f", next at {when}" if when else "")
                    + f". {counts.get('fired', 0)} sent"
                    + (f", {len(missed)} missed while I was down" if missed else "")
                    + "."),
    }


CAPABILITIES = [
    Capability(
        name="system.x.post_tweet", capability="x.publish", tier="red",
        run=post_tweet,
        params=(Param("text", str, doc="The post he approved."),
                Param("at", str, default="", doc="When to send it, or now."),
                Param("topic", str, default="", doc="What the batch is about.")),
        frozen=("at", "topic"),
        phrasings=("post that tweet", "queue that one"),
        success="{verdict}",
        audit="POST tweet at {at}",
        hold="post {text}",
        note="RED, card-only. With `at` set, approval ENQUEUES rather than posting — the "
             "approval moves to the front, it is not removed. `at` and `topic` are FROZEN "
             "so an edited card can change the wording but never the moment. Nothing "
             "reaches core/system/schedule.py without the real approval flag.",
    ),
    Capability(
        name="system.x.schedule_status", capability="x.read", tier="green",
        run=schedule_status,
        phrasings=("what is queued", "what tweets are scheduled", "schedule status"),
        success="{summary}",
        audit="read schedule status",
        note="Read-only. Reports waiting, sent, missed and failed — a missed post is one "
             "whose time passed while the daemon was down, and it is never sent late.",
    ),
]
