"""
core/brain/conversation.py — the thread, carried between turns and across
restarts.

THE BUG THIS FIXES, FROM HIS OWN TRANSCRIPT

    ASSISTANT  ...shall we trace a quick example together?
    USER       Yes, please.
    ASSISTANT  I am ready, Emperor. What shall we start with?

She forgot her own offer one turn later. Every model call was standalone, so
"yes" had nothing to attach to. He asked what he should say to make it work; the
answer is that he should not have to say anything.

NAMED `conversation`, NOT `memory`. `core/brain/memory.py` already exists and is
something else entirely — it records which folders she has opened so the
possessive register ("you opened this one yourself yesterday") has real evidence
behind it. Two different things called memory in one package is how a caller
imports the wrong one.

────────────────────────────────────────────────────────────────────────────────
THE FENCE, AND WHY IT IS THE PART THAT MATTERED

Persisting conversation turns an injection from a session-long problem into a
PERMANENT one. If she summarises a hostile page and that summary is written to
disk, the page survives a reboot inside her context. That is strictly worse than
the same attack today, which dies with the process.

Three things hold:

  1. THE FETCHED MATERIAL IS NEVER STORED. A turn record holds his line and her
     reply. The page text, the search results, the timeline — none of it is in a
     turn record, because none of it was ever a turn. It lives in the
     `SessionContext` fence and dies there.

  2. TURNS THAT RAN WITH EXTERNAL CONTENT IN CONTEXT ARE MARKED. Her reply on
     such a turn is page-DERIVED even though it is her sentence — a summary of
     hostile text is still hostile text, restated.

  3. ON REPLAY, A MARKED REPLY IS RE-FENCED. It goes back to the model wrapped
     in a fresh nonce and labelled as a summary of untrusted material, so it
     cannot be read as her own prior instruction to herself. The user's line is
     replayed plainly — his words are the one trusted source (CONTRACT §6.2).

Why re-fence rather than simply refusing to persist those turns: he asks "read
me that page" and then "so what did it say about pricing?" — dropping the first
exchange breaks exactly the follow-up this module exists to enable. Re-fencing
keeps the thread and removes the authority, which is the same trade the live
fence already makes.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
STORE = ROOT / "data" / "memory" / "conversation.json"

#: TWELVE TURNS — six exchanges.
#:
#: His requirement was "long enough for a real exchange". Six back-and-forths
#: covers the pattern that broke: she offers, he accepts, she teaches, he asks a
#: follow-up, she answers, he asks another. Measured cost is ~1,100-1,200 tokens
#: at his typical turn length, which is affordable against Gemini's free tier and
#: is reported per call rather than assumed.
#:
#: Not larger, and the reason is latency rather than money: every token of
#: history is a token the model reads before it emits the first one, and
#: time-to-first-token is already the worst number in the voice loop at ~4.5 s.
MAX_TURNS = 12

#: Hard byte cap on the FILE, independent of the turn cap.
#:
#: A turn cap alone is not a size bound: one pasted stack trace or one long
#: dictated tweet can be tens of kilobytes, and twelve of those is a file that
#: eventually breaks something. 200 KB is generous for twelve spoken turns and
#: small enough that reading it at boot is free.
MAX_BYTES = 200 * 1024

#: Per-turn text cap, so a single enormous turn cannot consume the whole budget.
MAX_TURN_CHARS = 4_000


@dataclass
class Turn:
    role: str            # "user" | "assistant"
    text: str
    ts: float = field(default_factory=time.time)
    #: True when untrusted external content was in context during this turn.
    #: Her reply is then page-DERIVED and is re-fenced on replay.
    external: bool = False

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


class Conversation:
    """
    The last `MAX_TURNS` turns, on disk, corruption-tolerant.

    NOTHING HERE MAY PREVENT THE DAEMON FROM STARTING. A truncated write, a
    half-flushed file, a hand-edit — every one of them ends in "start empty, log
    it, continue". This project has already lost a day to a startup-blocking
    failure and a memory file is not worth a second one.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else STORE
        self.turns: list[Turn] = []
        self.load_error: str | None = None
        self.load()

    # ── disk ─────────────────────────────────────────────────────────────────

    def load(self) -> None:
        self.turns = []
        self.load_error = None
        if not self.path.exists():
            return
        try:
            raw = self.path.read_text(encoding="utf-8")
            data = json.loads(raw)
            rows = data.get("turns", []) if isinstance(data, dict) else data
            out: list[Turn] = []
            for r in rows:
                if not isinstance(r, dict):
                    continue
                role = str(r.get("role", ""))
                text = str(r.get("text", ""))
                if role not in ("user", "assistant") or not text:
                    continue
                out.append(Turn(role=role, text=text[:MAX_TURN_CHARS],
                                ts=float(r.get("ts", 0.0) or 0.0),
                                external=bool(r.get("external", False))))
            self.turns = out[-MAX_TURNS:]
        except Exception as exc:  # noqa: BLE001 — ANY failure starts empty
            # Deliberately broad. A corrupt memory file is a nuisance; a daemon
            # that will not start because of one is an outage.
            self.turns = []
            self.load_error = f"{type(exc).__name__}: {exc}"

    def save(self) -> None:
        """
        Atomic. Write a sibling temp file, fsync, then `os.replace`.

        A half-written conversation.json is the corruption case above, and the
        machine this runs on has unstable mains — the failure is not
        hypothetical, it is scheduled.
        """
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(
                {"version": 1, "turns": [t.to_json() for t in self.turns]},
                ensure_ascii=False, indent=1)
            while len(payload.encode("utf-8")) > MAX_BYTES and len(self.turns) > 1:
                # OLDEST OUT. The recent exchange is what "yes, please" attaches
                # to; the oldest is the one he has already moved on from.
                self.turns.pop(0)
                payload = json.dumps(
                    {"version": 1, "turns": [t.to_json() for t in self.turns]},
                    ensure_ascii=False, indent=1)
            tmp = self.path.with_suffix(".json.tmp")
            with tmp.open("w", encoding="utf-8", newline="\n") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        except Exception:  # noqa: BLE001
            pass    # a memory that cannot be saved must not fail the turn

    # ── use ──────────────────────────────────────────────────────────────────

    def add(self, role: str, text: str, *, external: bool = False) -> None:
        """
        Record one turn. EMPTY TEXT IS NEVER RECORDED.

        An empty turn in memory is a phantom exchange that the next model call
        reads as real — which is exactly the failure the empty-turn bug would
        have caused once memory existed.
        """
        clean = " ".join(str(text or "").split()).strip()
        if not clean:
            return
        self.turns.append(Turn(role=role, text=clean[:MAX_TURN_CHARS],
                               external=external))
        self.turns = self.turns[-MAX_TURNS:]
        self.save()

    def clear(self) -> int:
        n = len(self.turns)
        self.turns = []
        self.save()
        return n

    def messages(self) -> list[Any]:
        """
        The history as `core.brain.llm.Message`, oldest first, ready to prepend
        to the current question.

        A marked assistant turn is RE-FENCED here — see the module docstring.
        The fence is rebuilt fresh on every replay rather than stored, so the
        nonce cannot be learned from the file on disk.
        """
        from .llm import Message
        from .provenance import ExternalContent

        out: list[Any] = []
        for t in self.turns:
            if t.role == "assistant" and t.external:
                framed = ExternalContent(
                    source="an earlier answer that summarised untrusted content",
                    text=t.text).framed()
                out.append(Message(role="assistant", content=framed))
            else:
                out.append(Message(role=t.role, content=t.text))
        return out

    # ── reporting ────────────────────────────────────────────────────────────

    @property
    def approx_tokens(self) -> int:
        """~4 chars per token. Rough on purpose — it is a budget gauge."""
        return sum(len(t.text) for t in self.turns) // 4

    def describe(self) -> str:
        ext = sum(1 for t in self.turns if t.external)
        return (f"{len(self.turns)} turns, ~{self.approx_tokens} tokens"
                + (f", {ext} re-fenced" if ext else ""))


# ── clearing it, out loud ────────────────────────────────────────────────────
#
# LOCAL, AND NO MODEL CALL. Without a way to reach in, a bad exchange — a
# mistranscription she then reasoned from, a page she summarised — lives in her
# context until the buffer rolls over, and he has no idea it is there.
#
# Matched before routing, like a confirmation, because "forget that" resolves to
# no tool and would otherwise fall through to the model, which would cheerfully
# say it had forgotten while forgetting nothing.
_CLEAR_RE = __import__("re").compile(
    r"^\s*(?:zoey[\s,]*)?(?:"
    r"forget (?:that|this|it|what i said|the last (?:thing|bit)|everything)|"
    r"start (?:fresh|over|again)|"
    r"clear (?:your |the )?(?:memory|context|history|conversation)|"
    r"new (?:conversation|topic)|"
    r"wipe (?:your )?memory|"
    r"never mind that"
    r")\b", __import__("re").I)


def is_clear_request(text: str) -> bool:
    return bool(_CLEAR_RE.search(text or ""))


#: Two variants so it does not become a tic, the same reason the router lines
#: have variants.
CLEARED_LINES = (
    "Forgotten, Emperor. Clean slate.",
    "Gone, Emperor. We start fresh.",
)
