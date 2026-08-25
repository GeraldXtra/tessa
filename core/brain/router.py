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
   tessa.md: "Emperor" by default and "sir" when it is serious, a SHORT first
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
from functools import lru_cache
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
    #: "Stop listening." Distinct from STOP, which only silences her speech.
    #: This one closes the ear. See `Routed.sleeps_wake`.
    SLEEP = "sleep"
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
    #: "Stop listening" / "go to sleep" — put the wake detector to sleep.
    #:
    #: SEPARATE FROM `halts_speech` because they are different requests that
    #: sound similar. "Stop" means stop TALKING and she keeps listening; "stop
    #: listening" means stop HEARING and she keeps her voice. Collapsing them
    #: would mean every "stop" mid-sentence also went deaf, and he would have no
    #: idea why the wake word had died.
    sleeps_wake: bool = False
    #: "I'm done for now" — close the CONVERSATION, keep the wake phrase armed.
    #:
    #: DISTINCT FROM `sleeps_wake`, and his own words are why: "she says
    #: something and goes quiet, and waits till I call her again." Waiting to be
    #: called again means the wake detector must stay live. Closing a session is
    #: the end of a conversation, not the end of listening for his name.
    #:
    #: Only an explicit "stop listening COMPLETELY" / "turn the wake word off"
    #: sets `sleeps_wake` as well, because that one really does make the chord
    #: the only way back.
    ends_session: bool = False

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
    "hello tessa", "hey tessa", "ok tessa", "okay tessa", "hi tessa",
    "i want you to", "i would like you to", "can you please", "could you please",
    "can you", "could you", "would you", "please", "hello", "hey", "hiya",
    "ok", "okay", "tessa", "so", "um", "uh", "just",
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
    # "can you hear" was unreachable: `normalise` peels "can you" as a filler,
    # so the text reaching the scorer was "hear me" and never matched. Keeping
    # the old key AND the post-strip form, because the filler list is allowed to
    # change and a rule that only works on one side of it is a trap.
    IntentSpec(Intent.PRESENCE, ("are you there", "you there", "hello", "hi",
                                 "are you awake", "you awake", "can you hear",
                                 "hear me", "you listening", "still there")),
    IntentSpec(Intent.STOP, ("stop", "mute", "stand down", "be quiet", "shut up",
                             "quiet", "enough", "cancel that"),
               # THE VETO IS WHAT KEEPS THE TWO APART. "stop listening" contains
               # "stop", so without this it scores 1.0 for STOP and 1.0 for
               # SLEEP — a tie decided by tuple order, which is not a decision.
               # He would say "stop listening" and she would go quiet and keep
               # listening, which is the exact opposite of what he asked for.
               veto=("listening", "sleep", "wake word")),
    # CLOSING A CONVERSATION. Matched on INTENT, never on an exact string,
    # because Whisper mangles short phrases — his own transcripts turned "stop
    # listening" into "Stop, listen" and "Stop List Me". Several phrasings for
    # each idea, all meaning the same thing.
    #
    # "THANK YOU" IS THE TRAP AND IT IS HANDLED BY REQUIRING HER NAME.
    # Bare "thank you" is NOT here: he says it mid-conversation without meaning
    # to stop, and closing on it would end the session every time he was polite.
    # "thank you Tessa" IS here, because addressing her by name while thanking
    # her is a sign-off — nobody says "thank you Tessa" in the middle of handing
    # over a task, they say "thanks". The name is the distinction, and it is
    # available because `strip_wake_name` only removes a LEADING address, so a
    # trailing "Tessa" survives to be matched.
    IntentSpec(Intent.SLEEP, ("stop listening", "go to sleep", "stop the wake word",
                              "sleep now", "stop listening to me", "go to sleep now",
                              "stop listening for me", "wake word off",
                              "turn off the wake word", "wake word",
                              # end-of-conversation phrasings
                              "i am done", "im done", "done for now",
                              "that is all", "thats all", "that will be all",
                              "we are finished", "were finished", "finished for now",
                              "nothing else", "that is everything",
                              # thanks ONLY when she is named
                              "thank you tessa", "thanks tessa", "thank you zoi",
                              "thanks zoi", "thank you zoe", "thanks zoe")),
)

#: Below this, she does not guess. Deliberately not lower: a router that fires
#: wrongly is worse than one that admits a miss, because the wrong thing has
#: already happened by the time he notices.
MATCH_THRESHOLD = 1.0

#: How long a silence makes his next word a RETURN rather than a continuation.
#:
#: TWENTY MINUTES, and the number is a trade between his two sentences. "When
#: we're done doing something and I come back again" must greet him; a pause
#: inside a task must not. Reading a page, taking a call, or thinking about
#: what to ask next runs to ten or fifteen minutes and is plainly the same
#: sitting. An hour away is plainly not. Twenty minutes clears the longest
#: ordinary pause and still greets him when he comes back from lunch.
#:
#: Erring long is the safer direction: a missed greeting is a small
#: disappointment, while being greeted every few minutes makes her feel
#: broken — the same reasoning that made the VAD silence window 1200 ms rather
#: than 600.
RETURN_GAP_S = 20 * 60

#: An utterance that is ONLY a greeting, with or without her name.
#:
#: Matched against the RAW text, because by the time `normalise` has run these
#: words are all in `_FILLERS` and nothing is left to recognise.
_GREETING_ONLY = re.compile(
    r"^\s*(?:hey|hi|hiya|hello|yo|greetings|"
    r"good\s+(?:morning|afternoon|evening|day))"
    r"(?:\s+(?:there|again|tessa|zoi|zoe|joey|zooey))*"
    r"\s*[.,!?]*\s*$", re.I)

#: Thanks with NO name attached — polite, mid-conversation, not a goodbye.
#: Anchored to the whole utterance: "thanks, now open my downloads" has a
#: command in it and must not land here.
_THANKS_ONLY = re.compile(
    r"^\s*(?:thanks|thank\s+you|thank\s+you\s+very\s+much|thanks\s+a\s+lot|"
    r"cheers|much\s+appreciated|appreciate\s+it)\s*[.,!]*\s*$", re.I)

#: Short, and none of them invite a reply.
_THANKS = [
    "Any time, Emperor.",
    "Of course, Emperor.",
    "Pleasure, sir.",
    "Always, Emperor.",
]

#: What she says when the conversation closes — his explicit ruling that she
#: says SOMETHING rather than just falling silent.
#:
#: Every one of these has to do two jobs: make clear she is GOING QUIET rather
#: than merely finishing a sentence, and make clear she is still reachable. A
#: line that only did the first would leave him unsure whether she had shut down;
#: a line that only did the second would not read as an ending at all. So each
#: names both the stopping and the way back, and each names HER NAME as the way
#: back rather than the chord, because closing a session leaves the wake phrase
#: armed.
_SESSION_CLOSE = [
    "Goodnight, Emperor. Call me when you need me.",
    "Right you are, Emperor. Say my name when you want me.",
    "Going quiet, sir. I am still here when you call.",
    "Done for now, Emperor. Just say the word.",
    "Standing down, Emperor. Call me and I am back.",
]

#: What she says when he addresses her again inside the gap — item 2d.
#: SHORT, because it is an acknowledgement and not a conversation, and none of
#: them repeat the time of day.
_ACKNOWLEDGE = [
    "Emperor?",
    "Still here.",
    "Yes, Emperor?",
    "Listening.",
    "Here, sir.",
]


@lru_cache(maxsize=512)
def _kw(keyword: str) -> re.Pattern[str]:
    return re.compile(rf"\b{re.escape(keyword)}\b")


def _has(norm: str, keyword: str) -> bool:
    """
    WORD BOUNDARIES, NOT SUBSTRINGS, and this was a live bug.

    `k in norm` matched PRESENCE's "hi" inside "t-HI-s", so "open this" scored
    as a greeting and she answered "Good morning, Emperor." to a command. The
    same flaw reaches further than that one case: "stop" matches "stopwatch",
    "date" matches "update", "doing" matches "undoing".

    Conversational keywords are WORDS. Matching them as substrings was never
    intended; it was just what `in` does.
    """
    return _kw(keyword).search(norm) is not None


def score_intents(norm: str) -> list[tuple[Intent, float]]:
    out: list[tuple[Intent, float]] = []
    for spec in _SPECS:
        if any(_has(norm, v) for v in spec.veto):
            continue
        hits = sum(1 for k in spec.must_any if _has(norm, k))
        if not hits:
            continue
        score = float(hits) + 0.5 * sum(1 for b in spec.boost if _has(norm, b))
        out.append((spec.intent, score))
    return sorted(out, key=lambda x: -x[1])


# ── her voice ────────────────────────────────────────────────────────────────
#
# Two or three phrasings each. tessa.md's rules, and the FIRST SENTENCE IS SHORT
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
        #: When he last said anything at all, and when she last greeted him.
        #:
        #: HIS RULE: "when we're done doing something and I come back again and
        #: say Hey Tessa, she should also reply back." So a greeting is not once
        #: per session, it is once per RETURN — and a return is defined by a gap
        #: of silence, which is the only signal available without asking him.
        self.last_turn_at: float | None = None
        self.last_greeted_at: float | None = None
        #: Only ever set from something OBSERVED. See `greeting`'s docstring:
        #: she never invents a reason to have noticed something.
        self._seen_after_three = False
        self._greet_variant = 0
        #: Snapshot of "was this a return", taken in `route` before the
        #: activity timestamp moves. None when `address_only` is called directly.
        self._was_return: bool | None = None

    @property
    def first_contact(self) -> bool:
        return self.turns <= 1

    def _note_activity(self, now: datetime | None = None) -> None:
        now = now or datetime.now()
        if 3 <= now.hour < 5:
            self._seen_after_three = True
        self.last_turn_at = now.timestamp()

    def mark_conversation_closed(self) -> None:
        """
        A session just ended, so the NEXT thing he says is a return.

        ITEM 3h. Without this, closing a conversation and re-opening it thirty
        seconds later would fall inside the 20-minute return gap and get an
        acknowledgement — "Still here." — when a greeting is plainly right. The
        gap exists to guess whether he went away; a session close is him SAYING
        he went away, which beats any guess.
        """
        self.last_turn_at = None

    def is_return(self, now: datetime | None = None) -> bool:
        """
        Has he been away long enough that this is a RETURN rather than a pause?

        Never spoken to before counts as a return — that is first contact.
        """
        if self.last_turn_at is None:
            return True
        now = now or datetime.now()
        return (now.timestamp() - self.last_turn_at) >= RETURN_GAP_S

    def address_only(self, now: datetime | None = None) -> Routed:
        """
        He said her name and nothing else. That is a greeting, not a command.

        THREE OUTCOMES, and the middle one is the point:

          first contact, or back after a gap  -> the full time-aware greeting
          said again inside the gap           -> an ACKNOWLEDGEMENT, not a repeat
          nothing else                        -> never silence

        Item 2d: repeating "Good evening, Emperor" every thirty seconds is how a
        greeting stops being a greeting and becomes a noise the machine makes.
        Acknowledging is the honest version of "I heard you, I already said
        hello" — short, in her voice, and it does not pretend the first one did
        not happen.
        """
        now = now or datetime.now()
        # Use the snapshot taken before `last_turn_at` moved, when there is one.
        returning = self._was_return if self._was_return is not None else self.is_return(now)
        self._was_return = None
        self._note_activity(now)

        if returning:
            self.last_greeted_at = now.timestamp()
            # USE THEN INCREMENT, so the FIRST greeting of a session is
            # variant 0 — the full "Good evening, Emperor." Incrementing first
            # made first contact the clipped "Evening, Emperor.", which is the
            # right line for the fourth return and the wrong one for hello.
            variant = self._greet_variant
            self._greet_variant += 1
            fact = None
            # A REAL FACT OR NONE. `_seen_after_three` is only true if she
            # actually observed him talking to her between 03:00 and 05:00.
            if self._seen_after_three and now.hour >= 5:
                fact = "You were up past three."
                self._seen_after_three = False
            return Routed(Intent.PRESENCE,
                          greeting(now, variant=variant, late_fact=fact),
                          score=1.0)

        return Routed(Intent.PRESENCE, _pick(_ACKNOWLEDGE), score=1.0)

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
            # ── HE SAID HER NAME AND NOTHING ELSE ────────────────────────────
            #
            # This is item 2a, and the old behaviour was actively wrong. "Hey
            # Tessa" reaches here with `raw` empty — `repair` has stripped the
            # name — so it fell through to `_SILENCE`: "I did not catch that."
            # She heard him perfectly and told him she had not.
            #
            # The distinction between a greeting and a command is exactly
            # whether anything survived the strip. Nothing did, so there is no
            # command, so he was saying hello.
            # A BARE GREETING WITH NO NAME LANDS HERE TOO, and it was broken
            # before any of this: "hello" is in `_FILLERS`, so `normalise`
            # peeled it and left an empty string, and she answered "I did not
            # catch anything, Emperor." to a word she had heard perfectly.
            #
            # Item 2e requires the chord plus "hello" to greet, so the test is
            # against the ORIGINAL utterance rather than the stripped one —
            # after stripping there is by definition nothing left to look at.
            if self.addressed_by_name or _GREETING_ONLY.match(text or ""):
                return self.address_only()
            return Routed(Intent.UNROUTED, _pick(_SILENCE))

        # ── A BARE THANK-YOU IS NOT A COMMAND AND NOT A GOODBYE ─────────────
        #
        # Checked before intent scoring so it cannot fall through to UNROUTED,
        # where she answered "That is not mine yet, Emperor. Files, windows,
        # your machine..." to someone being polite. In a conversation session he
        # will say this several times an hour, and reciting her capability list
        # each time would be the most irritating thing in the loop.
        #
        # It deliberately does NOT close the session — that needs her name, see
        # the SLEEP spec. This just accepts the thanks and stays listening.
        if _THANKS_ONLY.match(text or ""):
            self._was_return = self.is_return()
            self._note_activity()
            return Routed(Intent.PRESENCE, _pick(_THANKS), score=1.0)
        # ORDER MATTERS AND IT BIT ME. `_note_activity` sets `last_turn_at`,
        # which is the ONLY thing `is_return` reads — so calling it before the
        # routing decision made every greeting look like a continuation, and a
        # fresh "hi" answered "Yes, Emperor?" instead of greeting him.
        # The answer is snapshotted here, before the timestamp moves.
        self._was_return = self.is_return()
        self._note_activity()

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

        return self._answer(intent, score, norm)

    def _answer(self, intent: Intent, score: float, norm: str = "") -> Routed:
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
            # ONE RULE, NOT TWO. He greeted her in words ("hello", "you there?")
            # rather than by name alone, but it is the same question and it gets
            # the same answer, so this defers to `address_only` instead of
            # keeping a parallel greeting policy that could drift from it.
            #
            # `first_contact` is subsumed: `is_return()` is true when
            # `last_turn_at` is None, which is exactly first contact. The old
            # `turns <= 1` test stays on the class because other code reads it,
            # but the greeting decision no longer depends on two definitions of
            # "have we spoken".
            #
            # This is also what keeps push-to-talk working unchanged: pressing
            # the chord and saying "hello" routes to PRESENCE and lands here,
            # with no knowledge of whether a wake phrase was involved.
            return self.address_only(now)

        if intent is Intent.STOP:
            return Routed(intent, "", halts_speech=True, score=score)

        if intent is Intent.SLEEP:
            # TWO STRENGTHS OF "STOP", AND HE MEANS THE WEAKER ONE ALMOST ALWAYS.
            #
            # WEAK (the default): close the conversation, stay reachable. His
            # words — "she says something and goes quiet, and waits till I call
            # her again" — require the wake phrase to remain armed, so what she
            # says names HER NAME as the way back rather than the chord.
            #
            # STRONG (explicit only): stop listening for the phrase as well.
            # Then a phrase cannot bring her back — if it could, she never
            # stopped — so the chord is the only route and she says so.
            full_off = bool(re.search(
                r"\b(?:completely|entirely|for good|altogether|"
                r"turn (?:the )?wake word off|off the wake word)\b", norm))
            if full_off:
                return Routed(intent, _pick([
                    "Not listening, Emperor. Press the chord when you want me.",
                    "Ear closed, sir. The chord still works.",
                    "Sleeping. Use push-to-talk to bring me back, Emperor.",
                ]), sleeps_wake=True, ends_session=True, score=score)
            return Routed(intent, _pick(_SESSION_CLOSE),
                          ends_session=True, score=score)

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
