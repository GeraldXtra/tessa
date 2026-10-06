"""
core/system/schedule.py — the queue of ALREADY-APPROVED posts, and the tick
that sends them.

────────────────────────────────────────────────────────────────────────────────
⚠⚠ THE ONE INVARIANT: NOTHING ENTERS THIS QUEUE WITHOUT HIS APPROVAL

The approval is not removed by scheduling, it is MOVED EARLIER. He reviews the
whole batch once, on the cards; only what he approved is enqueued; the timer
then delivers something he has already read and said yes to. Firing time is not
a decision point, because the decision already happened.

That is made STRUCTURAL rather than promised: `enqueue()` takes an `approved`
flag and refuses without it, and the only caller that can pass a true one is
the red capability's handler — which `Executor.execute_answered`… which
`execute_approved` alone can reach, after stripping any forged flag from the
args. So "is in the queue" and "he approved it" are the same statement.

⚠ THE QUEUE FILE IS THEREFORE SECURITY-SENSITIVE. Anything that can write an
entry can post as him. It lives beside the vault under %LOCALAPPDATA%\\Tessa,
never in the repo and never in the audit chain, and is written atomically the
way `vault/store.py` writes: temp file, then replace, so a power cut cannot
leave a half-parsed queue.

────────────────────────────────────────────────────────────────────────────────
⚠ WHY A GRACE WINDOW, AND WHY MISSED IS NOT LATE

A daemon restart, a closed laptop, a night offline — the scheduled time passes
while nothing is running. Two bad answers are available and both are worse than
the third:

  * FIRE THEM ALL ON RESUME. Twenty tweets machine-gun out at once, hours late,
    in a burst that looks exactly like a bot to X and reads as nonsense to
    anyone following him. A 2pm thought posted at 4am is not the thought he
    approved.
  * FIRE THEM WHENEVER. Same thing, spread out, still late.

So an item whose time passed by more than `GRACE_S` is marked MISSED and never
sent. He is told. A post he approved for 2pm either goes out near 2pm or it
does not go out — being late is a decision he did not make.

`MAX_PER_TICK` is the second half of the same idea: even inside the window, one
per tick, so a resume with several legitimately-due items spaces them rather
than firing them together.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

#: Beside the vault. NOT in the repo, NOT in the audit chain.
DEFAULT_PATH = Path(os.environ.get("LOCALAPPDATA", "")) / "Tessa" / "schedule.json"

#: How late is too late. Fifteen minutes: long enough to survive a restart or a
#: laptop lid, short enough that nothing he approved for the morning surfaces
#: in the afternoon.
GRACE_S = 15 * 60

#: Even when several are legitimately due, send one per tick. A burst is the
#: bot pattern this whole design is trying not to look like.
MAX_PER_TICK = 1

PENDING, FIRED, MISSED, FAILED = "pending", "fired", "missed", "failed"


class ScheduleError(RuntimeError):
    pass


@dataclass
class Item:
    """One approved post, waiting for its time."""

    id: str
    kind: str                    # "tweet" | "reply"
    text: str
    at: float                    # epoch seconds
    request_id: str = ""         # the approval that created it — provenance, on the record
    reply_to_id: str = ""        # frozen target for a reply; empty for a standalone tweet
    topic: str = ""
    status: str = PENDING
    fired_at: float = 0.0
    note: str = ""
    created: float = field(default_factory=time.time)

    @property
    def pending(self) -> bool:
        return self.status == PENDING


class ScheduleStore:
    """
    The queue on disk. `clock` is injected so a proof can drive time without
    waiting for it — the same pattern `core/capabilities/vault` uses.
    """

    def __init__(self, path: Path | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.path = Path(path) if path else DEFAULT_PATH
        self.clock = clock
        self.items: list[Item] = []
        self.load()

    # ── persistence ──────────────────────────────────────────────────────────

    def load(self) -> None:
        """
        Read the queue. A corrupt file is reported, never silently emptied —
        losing twenty approved posts quietly is worse than refusing to start.
        """
        self.items = []
        try:
            if not self.path.exists():
                return
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ScheduleError(f"the schedule file is unreadable ({exc})") from None
        for row in raw.get("items", []):
            try:
                self.items.append(Item(**row))
            except TypeError:
                continue        # a row from a newer shape; skip it rather than crash

    def save(self) -> None:
        """Atomic: temp then replace, so a crash cannot leave half a queue."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        payload = {"version": 1, "saved_at": self.clock(),
                   "items": [asdict(i) for i in self.items]}
        tmp.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    # ── the gate ─────────────────────────────────────────────────────────────

    def enqueue(self, *, kind: str, text: str, at: float, approved: bool,
                request_id: str = "", reply_to_id: str = "", topic: str = "") -> Item:
        """
        Put an APPROVED post in the queue.

        ⚠ `approved` IS NOT A COURTESY. It is the whole boundary. The only
        caller able to pass True is the red capability's handler, which runs
        only from `execute_approved` — so an item in this queue is, by
        construction, one he read on a card and said yes to. A model-built
        call, a scheduled trigger, or anything else arrives with False and is
        refused here rather than posting later.
        """
        if not approved:
            raise ScheduleError(
                "a post cannot be scheduled without his approval — "
                "the card comes first, always")
        body = str(text or "").strip()
        if not body:
            raise ScheduleError("there is nothing to schedule")
        if kind not in ("tweet", "reply"):
            raise ScheduleError(f"{kind!r} is not something I can schedule")
        if kind == "reply" and not str(reply_to_id or "").strip():
            raise ScheduleError("a scheduled reply must carry the post id it answers")
        item = Item(id=secrets.token_hex(8), kind=kind, text=body, at=float(at),
                    request_id=request_id, reply_to_id=str(reply_to_id or ""),
                    topic=str(topic or ""))
        self.items.append(item)
        self.save()
        return item

    # ── the tick ─────────────────────────────────────────────────────────────

    def due(self, now: float | None = None) -> list[Item]:
        """Pending items whose time has come and has not gone."""
        t = self.clock() if now is None else now
        ready = [i for i in self.items if i.pending and i.at <= t and (t - i.at) <= GRACE_S]
        ready.sort(key=lambda i: i.at)
        return ready

    def stale(self, now: float | None = None) -> list[Item]:
        """Pending items whose time passed by more than the grace window."""
        t = self.clock() if now is None else now
        return [i for i in self.items if i.pending and (t - i.at) > GRACE_S]

    def sweep_missed(self, now: float | None = None) -> list[Item]:
        """
        Retire everything too late to send. Called before firing, so a stale
        item can never be picked up by `due()` on a later tick either.
        """
        gone = self.stale(now)
        for item in gone:
            item.status = MISSED
            item.note = "the scheduled time passed while nothing was running"
        if gone:
            self.save()
        return gone

    def mark(self, item: Item, status: str, note: str = "") -> None:
        item.status = status
        item.note = note
        if status == FIRED:
            item.fired_at = self.clock()
        self.save()          # persisted IMMEDIATELY: a crash after sending must
                             # never let the same post go out twice.

    def counts(self) -> dict[str, int]:
        out = {PENDING: 0, FIRED: 0, MISSED: 0, FAILED: 0}
        for i in self.items:
            out[i.status] = out.get(i.status, 0) + 1
        return out


# ── the posters ──────────────────────────────────────────────────────────────
#
# Module-level defaults so `fire_due` reaches the SAME hardened round-3 path the
# card uses, and so a proof can substitute a recorder without the production
# path ever having a second, unguarded poster in it.

def _post_tweet(text: str) -> dict[str, Any]:
    from core.tools import x_tools

    return x_tools.post(text=text, _approved_by_surface=True)


def _post_reply(text: str, reply_to_id: str) -> dict[str, Any]:
    from core.tools import x_tools

    return x_tools.reply(text=text, reply_to_id=reply_to_id, _approved_by_surface=True)


def fire_due(store: ScheduleStore, *, now: float | None = None,
             post_tweet: Callable[..., Any] = _post_tweet,
             post_reply: Callable[..., Any] = _post_reply,
             max_per_tick: int = MAX_PER_TICK) -> dict[str, Any]:
    """
    One tick. Retires what is too late, sends at most `max_per_tick` of what is
    due, and reports.

    `_approved_by_surface=True` is passed to the tool because the item in hand
    could only have been enqueued through the card. That is the one place this
    module asserts an approval, and it rests on `enqueue`'s refusal above
    rather than on trust.
    """
    t = store.clock() if now is None else now
    missed = store.sweep_missed(t)
    sent: list[Item] = []
    failed: list[Item] = []
    for item in store.due(t)[:max_per_tick]:
        try:
            if item.kind == "reply":
                post_reply(text=item.text, reply_to_id=item.reply_to_id)
            else:
                post_tweet(text=item.text)
        except Exception as exc:  # noqa: BLE001
            # A FAILED SEND IS NOT RETRIED. X refusing, a stale selector, a
            # rate limit — retrying a post whose outcome is unknown is how the
            # same tweet goes out twice. It is recorded and left for him.
            store.mark(item, FAILED, f"{type(exc).__name__}: {exc}"[:160])
            failed.append(item)
            continue
        store.mark(item, FIRED)
        sent.append(item)
    return {"sent": sent, "missed": missed, "failed": failed,
            "counts": store.counts(), "at": t}
