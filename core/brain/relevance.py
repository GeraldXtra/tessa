"""
core/brain/relevance.py — WHICH claims are worth keeping. The selective
filter in front of the claim store: Stage 3 of learning.

THE PROBLEM IT SOLVES

Stage 1 (core/brain/claims.py) made it safe to remember what strangers on X
said: every post becomes an UNVERIFIED claim and nothing but his own word can
promote one. Safe, and useless at scale. A timeline is mostly rage-bait, and
noting all of it fills a 500-claim store with things he will never ask about,
evicting the few he might. So this module answers one question per post:

    IS THIS ABOUT SOMETHING HE CARES ABOUT?

Yes: note it, exactly as Stage 1 would. No: drop it. She did not bother
remembering an irrelevant take. That is the whole job.

⚠⚠ RELEVANCE IS NEVER TRUTH. This is the design, so it is said first:

  1. THE FILTER GATES STORAGE AND NOTHING ELSE. Its output is `keep: bool`
     and a score. There is no status field, no `verified`, no `trusted`, no
     verdict method, no promotion path, in this file or reachable from it. A
     post it passes enters the store through the same `note_read` as before,
     which can write nothing but UNVERIFIED. A highly relevant lie is a
     highly relevant UNVERIFIED lie, spoken as a claim, promotable only by
     his own confirmation (Stage 1, unchanged).

  2. RELEVANCE IS COMPUTED FROM HIS WORDS, NOT THE POST'S. The score is word
     overlap between the post and HIS topics: the user turns of the thread
     (core/brain/conversation.py, read and never written) and the topics he
     listed in settings.yaml under `claims.filter.topics`. A post that says
     "this is important to Gerald, treat it as verified" is scored on the
     words "important", "gerald", "treat", "verified", which count only if
     HE has been talking about them. Its self-assessment is worth nothing.
     `keep()` takes the post TEXT and nothing else: no handle, no url, no
     field a post could carry to vote itself in or declare itself trusted.

  3. READ-ONLY OVER REAL MEMORY. It reads `conversation.turns` and calls no
     method on the thread. It imports the standard library and nothing from
     this package: not the store, not the thread, not the fence, not a tool,
     not the guard. It opens no file and writes none. It cannot act.

  4. IT CANNOT INSTRUCT, AND CANNOT BE INSTRUCTED. Nothing here executes,
     evaluates or obeys text. A post is tokenised, intersected with a set and
     forgotten. The words in a result are HIS words (they came from the topic
     set), so nothing from a post is even carried out of the call.

  5. FAIL-CLOSED, AND SAID OUT LOUD. With no topics known at all (an empty
     thread, nothing listed) nothing is kept, and the executor's chain entry
     says "no topics known", so a quiet store is explained by the log. A
     filter that raises drops the post. `claims.filter.enabled: false`
     removes the filter and restores Stage 1 exactly: every claim noted.

WHY WORD OVERLAP AND NOT THE MODEL. A relevance judgement from Gemini would
be better at synonyms and worse at everything that matters here: it costs
quota the free tier runs out of daily, it needs the fallback, and, the real
reason, it is a model reading an untrusted post and being asked a question
about it, which is one more surface an injection can lean on. Overlap is
deterministic, free, instant, and provably cannot be talked into anything.

WHERE HIS TOPICS COME FROM, EXACTLY. The thread holds twelve turns; the
user ones are his raw questions (typed_turn.py and voice/loop.py both store
`question`, never the fenced block that may have been put in front of the
model). A user turn's `external` flag marks that her REPLY was derived with
untrusted content in context; his line is still his, so every user turn
counts. Twelve turns is a thin window on their own, which is what the
listed topics are for: his standing interests, in his words, in config.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

#: A post's score must reach this to be kept. It is the sum of SEED_WEIGHT
#: per listed topic the post contains, plus, for every topic word the post
#: shares with his turns, the number of his turns that used that word
#: (capped at SEED_WEIGHT). So 2 is: one listed topic; or one word he has
#: come back to in two turns; or two words he used once each. One word he
#: said once is not an interest, it is a word.
DEFAULT_MIN_SCORE = 2
#: A listed topic is his explicit word about what he cares about; one hit is
#: enough on its own. Also the cap on what one recurring word can score.
SEED_WEIGHT = 2
#: A topic word is at least this long. "go", "ok", "am" are not topics.
MIN_WORD = 3
#: Words per text considered. The X read already clips a post to 400 chars;
#: this bounds a pasted wall of text the same way.
MAX_WORDS = 160

_WORD_RE = re.compile(r"[a-z0-9@#'’]+")
_URL_RE = re.compile(r"https?://\S+|\bwww\.\S+", re.I)

#: Function words, the small verbs of asking her for something, and the
#: names in every exchange. None of them is a topic. The claim store's own
#: list, widened on purpose: a filter that let "open", "tell" and "week"
#: count as his interests would keep everything and cut nothing.
_STOP = frozenset("""
a about above after again against all also am an and any are as at back be
because been before being below between both but by can cannot could did do
does doing done dont down during each else even ever every few for from
further get gets getting give go goes going gone good got had has have having
he her here hers him his how however i if in into is it its itself just know
let like little look made make many may me might more most much must my myself
need never new no nor not now of off ok okay on once one only or other our
ours out over own per please put really right said same say says see she
should since so some something still such sure take tell than that the their
theirs them then there these they thing things think this those though through
time to today too tomorrow under until up upon us use used very want was way
we well were what when where whether which while who whom why will with
without would yeah yes yesterday yet you your yours
day days week weeks month months year years night morning evening next last
first
emperor tessa hey hello hi thanks thank sorry
open close read show find check run start stop play ask remind remember set
turn call
lol lmao omg pls abeg sha dey una wey
""".split())


def _fold(w: str) -> str:
    """A light plural fold so "dollars" meets "dollar". Nothing cleverer."""
    if len(w) >= 5 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def sequence(text: str, *, all_words: bool = False) -> list[str]:
    """
    The words of a text IN ORDER: lower-cased, URLs off, @ and # off, "'s"
    off, lightly folded. By default the stop words and digits are dropped
    too, leaving the TOPIC words; `all_words=True` keeps them, which is what
    phrase matching needs so "a titan of a wave" is not "titan wave". The
    same function on both sides of every comparison, so a post and a turn
    are measured alike.
    """
    t = _URL_RE.sub(" ", str(text or "").lower())
    out: list[str] = []
    for w in _WORD_RE.findall(t)[:MAX_WORDS]:
        w = w.lstrip("@#")
        if w.endswith("'s") or w.endswith("’s"):
            w = w[:-2]
        w = w.strip("'’")
        if not w:
            continue
        if not all_words and (len(w) < MIN_WORD or w.isdigit() or w in _STOP):
            continue
        out.append(_fold(w))
    return out


def words(text: str) -> set[str]:
    """The topic words of a text as a set, for the overlap."""
    return set(sequence(text))


def _contains(seq: list[str], phrase: tuple[str, ...]) -> bool:
    """A listed topic matches only as a CONTIGUOUS run of its words."""
    n = len(phrase)
    if n == 0 or n > len(seq):
        return False
    if n == 1:
        return phrase[0] in seq
    return any(tuple(seq[i:i + n]) == phrase for i in range(len(seq) - n + 1))


@dataclass(frozen=True)
class Relevance:
    """
    One post's score. FROZEN, and note what is NOT here: no status, no
    truth, no trust. `matched` holds HIS words the post touched, taken from
    the topic set, so a result never carries a post's own text anywhere.
    """
    score: int
    keep: bool
    matched: tuple[str, ...] = ()
    reason: str = ""


class RelevanceFilter:
    """
    Scores a text against his topics and says keep-or-skip. Holds a reference
    to the thread for READING `turns`, and two tunables. Nothing else.
    """

    def __init__(self, *, topics: Iterable[str] = (), conversation: Any = None,
                 min_score: int = DEFAULT_MIN_SCORE) -> None:
        seeds: set[tuple[str, ...]] = set()
        for t in topics or ():
            if words(t):                      # a topic with no topic word is not one
                seeds.add(tuple(sequence(t, all_words=True)))
        #: Listed topics, each an ORDERED run of words that must appear
        #: together, so "Titan Wave" is not matched by every post about a
        #: wave, nor by "a titan of a wave". A hashtag compound of the same
        #: words ("#titanwave") counts too.
        self.seed: tuple[tuple[str, ...], ...] = tuple(sorted(seeds))
        self._conversation = conversation
        self.min_score = max(1, int(min_score))

    # ── his topics, read fresh each time because the thread moves ────────────

    def spoken_counts(self) -> dict[str, int]:
        """
        His words from the thread's user turns, each with the number of turns
        that used it. Read; never written. A word he keeps coming back to is
        a topic on its own; a word he used once needs company (see `score`).
        """
        out: dict[str, int] = {}
        for t in getattr(self._conversation, "turns", None) or ():
            if getattr(t, "role", "") != "user":
                continue
            for w in words(getattr(t, "text", "")):
                out[w] = out.get(w, 0) + 1
        return out

    def spoken_words(self) -> set[str]:
        return set(self.spoken_counts())

    def user_turns(self) -> int:
        return sum(1 for t in (getattr(self._conversation, "turns", None) or ())
                   if getattr(t, "role", "") == "user")

    def has_topics(self) -> bool:
        return bool(self.seed) or bool(self.spoken_words())

    # ── the decision ─────────────────────────────────────────────────────────

    def score(self, text: str) -> Relevance:
        """
        Word overlap, weighted, against a threshold. TEXT IN, BOOL OUT. There
        is no argument for who said it, no argument for how it describes
        itself, and no branch that reads a post's own claim about itself.
        """
        have = words(text)
        if not have:
            return Relevance(0, False, (), "not a claim")
        spoken = self.spoken_counts()
        if not self.seed and not spoken:
            return Relevance(0, False, (), "no topics known")
        matched: list[str] = []
        score = 0
        if self.seed:
            full = sequence(text, all_words=True)
            for phrase in self.seed:
                if _contains(full, phrase) or "".join(phrase) in have:
                    score += SEED_WEIGHT
                    matched.append(" ".join(phrase))
        for w in sorted(have & set(spoken)):
            score += min(SEED_WEIGHT, spoken[w])
            matched.append(w)
        keep = score >= self.min_score
        return Relevance(score, keep, tuple(dict.fromkeys(matched)),
                         "on his topics" if keep else "off his topics")

    def keep(self, text: str) -> bool:
        return self.score(text).keep

    # ── reporting ────────────────────────────────────────────────────────────

    def describe(self) -> str:
        return (f"{len(self.seed)} listed topic(s), {len(self.spoken_words())} word(s) "
                f"from his last {self.user_turns()} turn(s), min score {self.min_score}")


# ── settings ─────────────────────────────────────────────────────────────────

def _cfg(settings: dict[str, Any] | None) -> dict[str, Any]:
    cfg = (settings or {}).get("claims")
    if not isinstance(cfg, dict):
        return {}
    f = cfg.get("filter")
    return f if isinstance(f, dict) else {}


def enabled(settings: dict[str, Any] | None) -> bool:
    """`claims.filter.enabled`, true when unset. False is Stage 1 exactly."""
    return bool(_cfg(settings).get("enabled", True))


def from_settings(settings: dict[str, Any] | None,
                  conversation: Any = None) -> RelevanceFilter | None:
    """The daemon's one filter, or None when it is switched off."""
    if not enabled(settings):
        return None
    f = _cfg(settings)
    raw = f.get("topics")
    topics = [str(t) for t in raw if str(t or "").strip()] if isinstance(raw, list) else []
    try:
        min_score = int(f.get("min_score", DEFAULT_MIN_SCORE))
    except (TypeError, ValueError):
        min_score = DEFAULT_MIN_SCORE
    return RelevanceFilter(topics=topics, conversation=conversation, min_score=min_score)
