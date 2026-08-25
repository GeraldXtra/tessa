"""
core/brain/unrouted.py — what to do when the local router has no match.

THE BUG THIS FILE EXISTS TO FIX

Gerald asked "Tessa, what is a closure in JavaScript?" It transcribed perfectly.
She said "Heard. That is not mine yet." He asked eight things; one worked.

The engine was there the whole time. `make_engine()` was built, wired into
settings.yaml, reported at boot, counted in the PULSE health frame — and
imported by exactly two files in the repo: `server.py`, which constructs it, and
a test. `core/voice/loop.py`, `core/brain/router.py` and `core/brain/intents.py`
contained ZERO references to it. `route()` returned `Routed(Intent.UNROUTED,
_pick(_UNROUTED))` and that sentence went straight to Piper.

That is the fourth time in this project: `wait_for_silence` called by nothing,
`executor.py` imported by nothing, `tessa.md` read by nothing, and now an engine
answered by nothing. Built, measured, reported, unreachable.

THE DESIGN, STATED PLAINLY

The router is FIRST because it is free, offline and 0.3 ms, and it handles the
majority of what he says. The model is the DEFAULT FALLBACK, not a special
intent — anything the router cannot resolve goes to the brain unless there is a
positive reason not to.

There are exactly three positive reasons, and this module's whole job is telling
them apart from a question:

  FRAGMENT   — not a command at all. "The". A mis-triggered segment.
  ACTION     — a real instruction for a capability she does not have. "Order me
               a pizza." The model must NOT answer this, because a fluent
               paragraph about pizza ordering reads as though something
               happened. She says she cannot.
  LIVE_DATA  — a question whose answer is not in any model's weights and changes
               hourly. "What is the weather." Asking the model produces a
               confident non-answer; searching produces the actual number.

Everything else is a QUESTION and belongs to the brain.

WHY THE DEFAULT IS THE MODEL AND NOT A REFUSAL

The failure modes are not symmetric. Sending a question to the model that it
handles badly costs a few seconds and a mediocre answer. Refusing a question it
would have answered well is what he experienced seven times out of eight, and it
is the reason he stopped using her.
"""

from __future__ import annotations

import re
from enum import Enum

from .repair import repair


def _repaired(text: str) -> str:
    """Her name off, concatenation split, domains recovered — once, here."""
    return repair(text or "")[0]


class Disposition(str, Enum):
    FRAGMENT = "fragment"
    ACTION = "action"
    UNRESOLVED = "unresolved"
    LIVE_DATA = "live_data"
    QUESTION = "question"


# ── FRAGMENT ─────────────────────────────────────────────────────────────────
#
# "The" became a full turn with a spoken reply. It must not.
#
# THE RULE IS DELIBERATELY NARROW, and it is checked ONLY on text the router has
# already failed to match. That ordering is the safety property: "downloads" and
# "stop" both RESOLVE, so they never reach this function at all and cannot be
# discarded by it however aggressive it gets. A fragment test applied before
# routing would have to be clever; applied after, it only has to be careful.
#
# Discard when, after the wake word is stripped, the whole utterance is at most
# two words AND every word is a filler with no content. "The" goes. "Open
# downloads" never arrives. "Weather" (one content word) survives and becomes a
# question.
_FILLER = {
    "the", "a", "an", "and", "but", "so", "or", "of", "to", "in", "on", "at",
    "is", "it", "that", "this", "was", "were", "be", "am", "are", "i", "you",
    "um", "uh", "erm", "eh", "ah", "oh", "hmm", "mm", "mmm", "yeah", "well",
    "like", "just", "right", "okay", "ok", "thanks", "thank", "then",
    "actually", "anyway", "please",
}

MAX_FRAGMENT_WORDS = 2


def is_fragment(text: str) -> bool:
    words = [w for w in re.findall(r"[a-z0-9']+", (text or "").lower()) if w]
    if not words:
        return True
    if len(words) > MAX_FRAGMENT_WORDS:
        return False
    return all(w in _FILLER for w in words)


# ── ACTION she has no tool for ───────────────────────────────────────────────
#
# The model must never be handed these. Asked "order me a pizza", a model
# answers helpfully and at length, and that paragraph is indistinguishable from
# a pizza having been ordered. An assistant that describes doing things it did
# not do is worse than one that refuses.
#
# The list is verbs that need an ACCOUNT or a SERVICE she has no integration
# for. It is deliberately short: every verb here is a question she will refuse,
# so a false positive costs a real answer. Anything doubtful goes to the model.
_ACTION_VERBS = (
    r"order|buy|purchase|book|reserve|pay|transfer|send\s+money|withdraw|"
    r"call|ring|dial|text|whatsapp|dm|email|e-?mail|message\s+\w+|"
    r"schedule|remind\s+me|set\s+(?:a\s+)?(?:alarm|timer|reminder)|"
    r"subscribe|unsubscribe|cancel\s+my|"
    r"print|scan|fax|"
    r"turn\s+(?:on|off)\s+the\s+(?:light|lights|heating|ac|tv)"
)
_ACTION_RE = re.compile(rf"^\s*(?:please\s+)?(?:{_ACTION_VERBS})\b", re.I)


def is_unavailable_action(text: str) -> bool:
    return bool(_ACTION_RE.search((text or "").strip()))


# ── LIVE DATA ────────────────────────────────────────────────────────────────
#
# Not in the weights, changes hourly. A model asked these produces a fluent
# "I cannot know that", which is true and useless when she is holding a browser.
_LIVE_TOPICS = (
    r"weather|temperature|forecast|rain|raining|humid|"
    r"news|headlines|latest\s+on|"
    r"score|fixture|kick\s*off|"
    r"price|cost\s+of|exchange\s+rate|dollar|naira\s+rate|bitcoin|stock|shares|"
    r"traffic|flight|petrol|fuel\s+price"
)
_LIVE_RE = re.compile(rf"\b(?:{_LIVE_TOPICS})\b", re.I)

#: A time word turns a general question into a live one: "how does inflation
#: work" is for the model, "what is inflation right now" is not.
_NOW_RE = re.compile(
    r"\b(?:today|tonight|right\s+now|currently|current|this\s+(?:week|morning|"
    r"evening|afternoon)|latest|now)\b", re.I)


def needs_live_data(text: str) -> bool:
    t = (text or "")
    return bool(_LIVE_RE.search(t)) or (
        bool(_NOW_RE.search(t)) and bool(re.match(r"^\s*(?:what|who|when|where|how)\b", t, re.I)))


# ── the decision ─────────────────────────────────────────────────────────────

# ── A TOOL VERB SHE OWNS, WITH A TARGET THAT DID NOT RESOLVE ────────────────
#
# THIS IS THE MOST IMPORTANT RULE IN THE FILE AND IT WAS MISSING.
#
# "Tessa, Open My Taluts" — Whisper's mangling of "documents". The folder did not
# resolve, so the router returned UNROUTED, so it went to the model as a
# question, and the model said:
#
#     "On it, Emperor. I am opening Taluts for you now."
#
# It opened nothing. It cannot open anything. That sentence is a fabrication
# handed to him in her voice, and it is strictly worse than the "not mine yet"
# it replaced — he would have gone looking for a folder that never opened.
#
# The signal is precise: the utterance STARTS with a verb she genuinely has a
# tool for. That makes it an INSTRUCTION, not a question, and an instruction
# whose object she could not resolve has exactly one honest answer: name the
# word she could not place and ask.
_OWNED_VERBS = (
    r"open|close|show|find|search|read|play|pause|kill|delete|remove|move|copy|"
    r"rename|make|create|minimi[sz]e|maximi[sz]e|click|type|focus|switch\s+to|"
    r"bring|lock|sleep|mute|repost|retweet|like|reveal|screenshot"
)
_OWNED_VERB_RE = re.compile(rf"^\s*(?:please\s+)?(?:{_OWNED_VERBS})\b", re.I)


def is_unresolved_command(text: str) -> bool:
    return bool(_OWNED_VERB_RE.search((text or "").strip()))


def unresolved_refusal(text: str) -> str:
    """
    Name the word she could not place. Never guess, never narrate.

    Repaired first, for the same reason `classify` is: the raw transcript starts
    with her name, so the "object" would otherwise come back as "My Taluts"
    with the verb still buried in the middle.
    """
    text = _repaired(text)
    # The FIRST token is the verb she recognised ("Open"); the rest is the
    # object she could not place, and that is the part worth saying back.
    words = re.findall(r"[A-Za-z0-9'.:\\-]+", text or "")[1:]
    target = " ".join(words[-2:]) if words else ""
    target = re.sub(r"^(?:my|the|a|an)\s+", "", target, flags=re.I)
    target = target.strip(" .,?!")
    if target:
        return (f"I do not know what {target} is, Emperor. "
                f"Say it again, or give me the full path.")
    return "I did not catch what to open, Emperor. Say it again?"


def classify(text: str) -> Disposition:
    """
    Called ONLY on utterances the local router could not resolve.

    Order matters: fragment first (it is not a request at all), then the
    positive reasons to withhold the model, then the model.

    THE TEXT IS REPAIRED HERE, not by the caller. Every verb test in this module
    is anchored to the start of the utterance, and his transcripts begin with
    her name — "Tessa, Open My Taluts" does not start with "Open", so
    `is_unresolved_command` missed it and the instruction went to the model
    anyway. Repairing inside `classify` means no caller can forget, which is the
    same reasoning as fencing inside the executor rather than in each handler.
    """
    t = _repaired(text)
    if is_fragment(t):
        return Disposition.FRAGMENT
    if is_unavailable_action(t):
        return Disposition.ACTION
    # BEFORE the model, always. An instruction she half-understood must never be
    # handed to something that will narrate having carried it out.
    if is_unresolved_command(t):
        return Disposition.UNRESOLVED
    if needs_live_data(t):
        return Disposition.LIVE_DATA
    return Disposition.QUESTION


#: What she says for an action she has no tool for. Names the gap and does not
#: pretend, per tessa.md's rule on failure.
_ACTION_REFUSAL = (
    "That is not something I can do yet, Emperor. I have no tool for it. "
    "Files, windows, processes, your machine, the browser and X are mine."
)


def action_refusal() -> str:
    return _ACTION_REFUSAL
