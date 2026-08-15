"""
core/brain/router.py — spec §5.3's first branch: local intents, answered free.

TWO THINGS CHANGED HERE AFTER GERALD ACTUALLY USED HER.

1. THE MATCHING WAS TOO LITERAL. An exact-phrase table missed three of his four
   real utterances — "How are you running?" and "What's the date?" both went to
   UNROUTED despite the intents existing. He will not learn my phrasings; the
   router has to meet him. It now normalises hard (contractions, filler openers,
   punctuation), scores KEYWORD SETS rather than matching whole strings, and
   collapses a transcript that is one phrase repeated.

2. SHE SOUNDED LIKE A SYSTEM MESSAGE. Every word she speaks today is a fixed
   string in this file — there is no model — so her character for the coming
   weeks is written here, not in a prompt. Every string below follows
   zoey.md: "Emperor" by default and "sir" when it is serious, a SHORT first
   sentence because Piper streams per sentence and the opener is the whole
   400 ms budget, and two or three phrasings per situation because one fixed
   string becomes a tic by the fiftieth time he hears it.

THE POSSESSIVE REGISTER IS ON ACTIONS ONLY. "You opened this one yourself
yesterday. Ask me next time." is right when she opens a folder and wrong as a
response to "what time is it" — used everywhere it stops being character and
becomes a verbal tic, which is the same failure as a single fixed string.
"""

from __future__ import annotations

import platform
import random
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Callable

from .intents import IntentParser, greeting
from .repair import repair


class Intent(str, Enum):
    TOOL = "tool"          # resolved to a structured tool call, see `calls`
    TIME = "time"
    DATE = "date"
    PRESENCE = "presence"
    STOP = "stop"
    STATUS = "status"
    UNROUTED = "unrouted"


@dataclass
class Routed:
    intent: Intent
    speech: str
    halts_speech: bool = False
    #: How confident the keyword match was, for reporting near-misses.
    score: float = 0.0
    #: Structured tool calls — NAME + ARGS, never a command string (invariant 4).
    calls: list = field(default_factory=list)

    @property
    def handled_locally(self) -> bool:
        return self.intent is not Intent.UNROUTED


# ── normalisation ────────────────────────────────────────────────────────────

_CONTRACTIONS = {
    "what's": "what is", "whats": "what is", "how's": "how is", "hows": "how is",
    "it's": "it is", "its": "it is", "i'm": "i am", "you're": "you are",
    "don't": "do not", "can't": "cannot", "won't": "will not", "that's": "that is",
    "there's": "there is", "let's": "let us", "we're": "we are",
}

#: Dropped from the FRONT only. Stripping "please" anywhere would mangle a
#: sentence that legitimately contains it.
_FILLERS = [
    "hello zoey", "hey zoey", "ok zoey", "okay zoey", "hi zoey",
    "i want you to", "i would like you to", "can you please", "could you please",
    "can you", "could you", "would you", "please", "hello", "hey", "hiya",
    "ok", "okay", "zoey", "so", "um", "uh", "just",
]


def normalise(text: str) -> str:
    t = text.lower().strip()
    t = re.sub(r"[^\w\s']", " ", t)          # punctuation out, apostrophes kept
    t = " ".join(t.split())
    for src, dst in _CONTRACTIONS.items():
        t = re.sub(rf"\b{re.escape(src)}\b", dst, t)
    changed = True
    while changed:                            # peel stacked openers
        changed = False
        for f in _FILLERS:
            if t.startswith(f + " ") or t == f:
                t = t[len(f):].strip()
                changed = True
                break
    return t.strip()


def collapse_repetition(text: str) -> str:
    """
    A transcript that is one phrase repeated IS that phrase.

    He pressed the chord five times not knowing it toggled, and Whisper returned
    "What time is it?" five times over. That is one question, not five, and
    routing it as a 60-character string missed the intent entirely.
    """
    t = " ".join(text.split())
    if not t:
        return t
    for n in range(1, 9):                     # try phrase lengths 1..8 words
        words = t.split()
        if len(words) < n * 2:
            break
        unit = " ".join(words[:n])
        if len(words) % n == 0:
            if all(" ".join(words[i:i + n]) == unit for i in range(0, len(words), n)):
                return unit
    # Sentence-level repetition, where punctuation was stripped unevenly.
    parts = [p.strip() for p in re.split(r"(?<=[.?!])\s+", text) if p.strip()]
    if len(parts) > 1 and len({p.lower().strip(" .?!") for p in parts}) == 1:
        return parts[0]
    return t


# ── keyword intents ──────────────────────────────────────────────────────────
#
# ANY of `must_any` plus optional `boost` words. Scored rather than matched, so
# "how are you running" hits STATUS on both "how are you" and "running" without
# needing that exact string to have been imagined in advance.

@dataclass(frozen=True)
class IntentSpec:
    intent: Intent
    must_any: tuple[str, ...]
    boost: tuple[str, ...] = ()
    veto: tuple[str, ...] = ()


_SPECS = (
    IntentSpec(Intent.TIME, ("what time", "the time", "time is it", "clock"),
               boost=("now",), veto=("date", "day", "long")),
    IntentSpec(Intent.DATE, ("date", "what day", "day is it", "today"),
               veto=("time is it",)),
    IntentSpec(Intent.STATUS, ("how are you", "status", "how are things",
                               "running", "how is it going", "doing"),
               boost=("cpu", "memory", "uptime", "system", "yourself")),
    IntentSpec(Intent.PRESENCE, ("are you there", "you there", "hello", "hi",
                                 "are you awake", "you awake", "can you hear")),
    IntentSpec(Intent.STOP, ("stop", "mute", "stand down", "be quiet", "shut up",
                             "quiet", "enough", "cancel that")),
)

#: Below this, she does not guess. Deliberately not lower: a router that fires
#: wrongly is worse than one that admits a miss, because the wrong thing has
#: already happened by the time he notices.
MATCH_THRESHOLD = 1.0


def score_intents(norm: str) -> list[tuple[Intent, float]]:
    out: list[tuple[Intent, float]] = []
    for spec in _SPECS:
        if any(v in norm for v in spec.veto):
            continue
        hits = sum(1 for k in spec.must_any if k in norm)
        if not hits:
            continue
        score = float(hits) + 0.5 * sum(1 for b in spec.boost if b in norm)
        out.append((spec.intent, score))
    return sorted(out, key=lambda x: -x[1])


# ── her voice ────────────────────────────────────────────────────────────────
#
# Two or three phrasings each. zoey.md's rules, and the FIRST SENTENCE IS SHORT
# in every one of them — measured: 13 chars is 296 ms, 47 chars is 660 ms,
# against a 400 ms budget.

_PRESENCE = [
    "Here, Emperor.",
    "I am here, Emperor.",
    "Right here. What do you need?",
]

# The UNROUTED line must describe what she can ACTUALLY do. The previous
# version listed "time, date and how I am running" — true for one night, a lie
# the moment the tools were reconnected, and a lie she would have kept telling
# him. It names CATEGORIES rather than reciting sixteen tool names, because a
# list is not something anyone can hold from a spoken sentence.
# THE ACKNOWLEDGEMENT OPENER IS GONE, and it was the loudest thing wrong with
# her. Every one of these began "I heard you, Emperor" / "Heard, Emperor" /
# "I caught that, Emperor", so across eight consecutive turns Gerald was greeted
# eight times. His rule was: greet on first contact, and when he greets her.
# Telling him she heard him is not information — she answered, so obviously she
# heard him — and repeated every turn it reads as a tic.
#
# These now survive only as the last-resort line for something the brain also
# could not take, so they are rarer AND shorter.
_UNROUTED = [
    "Not that one yet, Emperor. Files, windows, your machine, the browser and X are mine.",
    "That is not mine yet, Emperor. I can open things, run your machine, search the web.",
    "Not yet, Emperor. Files, apps, processes, the browser — those I can do.",
]

_NEAR_MISS = [
    "Say that again, Emperor?",
    "Not quite, Emperor. Once more?",
]

_SILENCE = [
    "I did not catch anything, Emperor.",
    "Nothing came through, Emperor.",
]

# Distinct from silence on purpose. "Nothing came through" sends him looking for
# a bug; "you came through very quietly" sends him to the microphone, which is
# where the problem actually is. Naming the real cause is the difference between
# a useful failure and a polite one.
_TOO_QUIET = [
    "You came through very quietly, Emperor. Closer to the microphone?",
    "That was almost too quiet to hear, Emperor. Say it again, a little closer.",
]


def _pick(pool: list[str]) -> str:
    return random.choice(pool)


class Router:
    """
    `health_sample` is injected so STATUS reports the SAME numbers
    `evt.daemon.health` broadcasts. Two sources for one fact is how a status
    report starts quietly lying.
    """

    def __init__(self, health_sample: Callable[[], dict] | None = None) -> None:
        self._health = health_sample
        self._tools = IntentParser()
        #: Turn counter for this session. GREETING IS FIRST CONTACT ONLY.
        #:
        #: His rule, and it was not implemented: greet when the session opens
        #: and when he greets her. Not every sentence. Eight turns produced
        #: eight greetings because the acknowledgement was baked into the
        #: UNROUTED strings rather than being a decision anything made.
        self.turns = 0
        #: Set per turn by `repair` — did he actually say her name.
        self.addressed_by_name = False

    @property
    def first_contact(self) -> bool:
        return self.turns <= 1

    def route(self, text: str) -> Routed:
        """
        TOOLS FIRST, CONVERSATION SECOND, and the order is deliberate.

        Tool patterns are NARROW — an explicit verb, a port number, a version
        word. Conversational matching is broad keyword scoring, which is what
        made "How's it going" work, and broad scoring asked first would swallow
        "open my downloads" on the word "open" appearing near something else.
        Narrow-then-broad cannot mis-fire in that direction.

        This is the reconnection: `route()` previously never reached the tool
        layer at all, so every tool in tools_local.py was unreachable and she
        told Gerald she could not open his downloads — which she could, and had
        been able to for two days.
        """
        self.turns += 1
        # REPAIR BEFORE ANYTHING MATCHES. Her own name in any spelling, verb
        # concatenation ("OpenGoogle.com"), and spoken domains ("google dot
        # com") are all fixed once, at the front, so no downstream matcher has
        # to know that speech arrives glued together. See core/brain/repair.py.
        raw, self.addressed_by_name = repair(collapse_repetition(text or ""))
        norm = normalise(raw)
        if not norm:
            return Routed(Intent.UNROUTED, _pick(_SILENCE))

        parsed = self._tools.parse(raw)
        if parsed.question:
            return Routed(Intent.TOOL, parsed.question, score=1.0)
        if parsed.calls:
            return Routed(Intent.TOOL, "", score=1.0, calls=list(parsed.calls))

        ranked = score_intents(norm)
        if not ranked:
            return Routed(Intent.UNROUTED, _pick(_UNROUTED))
        intent, score = ranked[0]
        if score < MATCH_THRESHOLD:
            return Routed(Intent.UNROUTED, _pick(_NEAR_MISS), score=score)

        return self._answer(intent, score)

    def _answer(self, intent: Intent, score: float) -> Routed:
        now = datetime.now()

        if intent is Intent.TIME:
            # %-I is a glibc extension and raises on Windows.
            clock = now.strftime("%I:%M %p").lstrip("0")
            return Routed(intent, _pick([
                f"{clock}, Emperor.",
                f"It is {clock}.",
                f"{clock}. Still early enough." if now.hour < 22 else f"{clock}, Emperor. Late.",
            ]), score=score)

        if intent is Intent.DATE:
            day = str(now.day)
            return Routed(intent, _pick([
                f"It is {now.strftime('%A')}. The {day}th of {now.strftime('%B')}, Emperor.",
                f"{now.strftime('%A')}, Emperor. The {day}th of {now.strftime('%B %Y')}.",
            ]), score=score)

        if intent is Intent.PRESENCE:
            # He greeted her, so she greets back — that is the half of his rule
            # that always applied. On any later turn a bare "you there?" gets
            # the short form instead of a fresh salutation.
            if self.first_contact:
                return Routed(intent, greeting(now) + " Ready when you are.", score=score)
            return Routed(intent, _pick(_PRESENCE), score=score)

        if intent is Intent.STOP:
            return Routed(intent, "", halts_speech=True, score=score)

        if intent is Intent.STATUS:
            if self._health is None:
                return Routed(intent, "I cannot read myself right now, sir.", score=score)
            h = self._health()
            up = float(h.get("uptimeS", 0.0))
            hours, rem = divmod(int(up), 3600)
            mins = rem // 60
            uptime = f"{hours} hours and {mins} minutes" if hours else f"{mins} minutes"
            return Routed(intent, _pick([
                (f"Running well, Emperor. Up {uptime}, "
                 f"CPU {float(h.get('cpuPct', 0)):.0f} percent, "
                 f"memory {float(h.get('memMB', 0)):.0f} megabytes. "
                 f"Spent {float(h.get('budgetSpent', 0)):.0f} of {float(h.get('budgetCap', 0)):.0f} naira today."),
                (f"All good, Emperor. {uptime} up, "
                 f"CPU at {float(h.get('cpuPct', 0)):.0f} percent, "
                 f"{float(h.get('memMB', 0)):.0f} megabytes of memory. "
                 f"Nothing spent yet today." if float(h.get("budgetSpent", 0)) == 0 else
                 f"All good, Emperor. Up {uptime}."),
            ]), score=score)

        return Routed(Intent.UNROUTED, _pick(_UNROUTED), score=score)


# ── action confirmations — the possessive register lives HERE ────────────────
#
# On ACTIONS only. She notices when he did a thing himself that she could have
# done. Used on every reply it would be a tic; used when she opens something he
# opened yesterday, it is the character Gerald asked for.

_DONE = [
    "Done, Emperor.",
    "Open, Emperor.",
    "There you go, Emperor.",
]

_DONE_POSSESSIVE = [
    "Done, Emperor. You opened this one yourself yesterday, by the way. I noticed. Ask me next time.",
    "Open, Emperor. You did this one by hand last time. I would rather it were mine.",
    "There. You have been doing these yourself, Emperor. Give them to me.",
]

_FAILED = [
    "That failed, sir. {reason} {alternative}",
    "It did not open, sir. {reason} {alternative}",
]

_DESTRUCTIVE_HOLD = [
    "That is destructive, sir. {detail} Say it again and I will do it.",
    "Hold on, sir. {detail} Confirm once more and it is done.",
]


def action_done(*, he_did_it_himself: bool = False) -> str:
    return _pick(_DONE_POSSESSIVE if he_did_it_himself else _DONE)


def action_failed(reason: str, alternative: str) -> str:
    # The period is restored, not just stripped. Without it the reason and the
    # alternative ran together into one sentence — and Piper streams PER
    # SENTENCE, so a missing full stop is not a typo, it is one long opener that
    # blows the 400 ms first-sentence budget on exactly the turns where she is
    # already delivering bad news.
    return _pick(_FAILED).format(reason=reason.rstrip(". ") + ".", alternative=alternative)


def destructive_hold(detail: str) -> str:
    return _pick(_DESTRUCTIVE_HOLD).format(detail=detail.rstrip(".") + ".")


def ambiguous(names: list[str]) -> str:
    listed = ", ".join(n.title() for n in names[:3])
    return _pick([
        f"Two of those, Emperor. {listed}?",
        f"Which one, Emperor? {listed}.",
    ])


def platform_note() -> str:
    return f"{platform.system()} {platform.release()}"
