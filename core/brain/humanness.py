"""
core/brain/humanness.py — the anti-AI-tells filter on what she says.

────────────────────────────────────────────────────────────────────────────────
WHY A FILTER AT ALL, WHEN THE PERSONA ALREADY STEERS

`tessa.md` is the steering, and it is good steering — it already bans preamble,
numbered lists, markdown and emoji, and it teaches by transcript rather than by
adjective. But `persona.py` says the true thing about it: **a system prompt is a
request, not a control.** Gemini follows it most of the time. Most of the time
is not a property you can test, and "sounds human" is not a property you can
assert against a model's mood.

So this module is the testable half. It is a list of the exact strings that make
a reply sound like a machine wrote it, and a function that finds them.

────────────────────────────────────────────────────────────────────────────────
⚠⚠ WHY IT STRIPS SOME THINGS AND ONLY FLAGS OTHERS

This is the important decision in the file, and the brief left it open.

A post-generation rewrite that restructures a sentence can change what the
sentence MEANS. That is unacceptable here for a specific reason: a
human-sounding wrong answer is worse than a stiff right one, and a filter that
"improves" a refusal or a factual claim is a filter that can quietly turn
"I could not open it" into something friendlier and false.

So the split is by INFORMATION CONTENT, not by how annoying the pattern is:

  STRIPPED — text that carries none. A leading "Certainly!", a trailing "I hope
  this helps!", the "As an AI language model," disclaimer, the "It is important
  to note that" wind-up, and markdown characters that Piper reads aloud as
  punctuation. Removing these cannot change a claim, because they assert
  nothing. The sentence that survives is the sentence the model meant.

  FLAGGED ONLY — everything with meaning in it. A hedge stack, a reflexive
  three-point list, over-apologising, em-dash overuse. These are real AI tells
  and the persona fights them, but rewriting them means rewriting content, and
  I will not have a register filter editing facts. They are counted, reported,
  and testable; the fix for them lives in `tessa.md` where it belongs.

`humanise()` therefore returns the cleaned text AND the tells it saw, so the
flagged ones are visible rather than silently tolerated.

────────────────────────────────────────────────────────────────────────────────
SPEAKABILITY IS PART OF THE JOB

Everything she writes is read aloud by Piper, sentence by sentence. Markdown is
not a formatting preference here, it is noise the synthesiser pronounces:
`**really**` becomes "asterisk asterisk really asterisk asterisk". The persona
forbids it and the model still emits it occasionally, so the emphasis characters
are stripped while their CONTENT is kept. `**bold**` becomes `bold`, never
nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ── STRIPPED: pure filler, no information, safe to remove ────────────────────
#
# Each entry is (name, pattern, replacement). Anchored where anchoring is what
# makes them safe: "Of course" mid-sentence is ordinary English, "Of course!" at
# the very start of a reply is the wind-up.

_LEAD_ENTHUSIASM = (
    r"certainly|absolutely|of course|sure thing|sure|great question|"
    r"that'?s a great question|excellent question|good question|"
    r"i'?d be happy to help|i'?d be happy to|happy to help|glad to help"
)

def _keep_capital(m: re.Match[str]) -> str:
    """
    Drop the wind-up and CAPITALISE the word that becomes the new sentence start.

    Removing "It is important to note that " from mid-paragraph left "the file
    is locked" running on after a full stop in lower case. Capitalising is done
    HERE, on the letter this particular strip exposed, rather than by a general
    "capitalise after every full stop" pass — a blanket rule would capitalise
    `npm install` and `node_modules` at the start of a sentence, which is wrong
    in her English and wrong to hear.
    """
    return m.group(1).upper()


_STRIP: list[tuple[str, re.Pattern[str], object]] = [
    # "Certainly! ..." / "Great question. ..." — the wind-up before the answer.
    ("wind-up opener",
     re.compile(rf"^\s*(?:{_LEAD_ENTHUSIASM})\s*[!.,—-]*\s*", re.I), ""),
    # "As an AI language model, I ..." — the disclaimer nobody asked for.
    ("AI self-disclaimer",
     re.compile(r"\b(?:as an?\s+(?:ai|artificial intelligence)(?:\s+(?:language\s+)?model)?|"
                r"as a large language model|being an ai)\s*,?\s*", re.I), ""),
    # "It is important to note that X" -> "X". The lead-in asserts nothing.
    ("importance wind-up",
     re.compile(r"\b(?:it'?s|it is)\s+(?:important|worth|essential|crucial|good)\s+"
                r"(?:to\s+)?(?:note|remember|mention|understand|keep in mind|bear in mind)"
                r"\s*(?:that\s+)?(\w)", re.I), _keep_capital),
    # "In conclusion, X" / "Overall, X" / "In summary, X".
    #
    # ANCHORED TO A SENTENCE BOUNDARY, NOT ONLY A LINE START. The first version
    # used `^` with MULTILINE and missed "…is generated. In conclusion, here
    # are three things" — which is exactly where the model puts it, mid-
    # paragraph rather than on its own line. The lookbehind is fixed-width so
    # it stays a legal pattern.
    ("summary wind-up",
     re.compile(r"(?:^|(?<=[.!?]\s)|(?<=[.!?]\n))\s*"
                r"(?:in\s+conclusion|in\s+summary|to\s+summari[sz]e|overall|"
                r"in\s+short|to\s+sum\s+up)\s*[,:—-]*\s*(\w)", re.I | re.M), _keep_capital),
    # Trailing customer-service sign-off.
    ("helpful sign-off",
     re.compile(r"\s*(?:i\s+hope\s+(?:this|that)\s+helps?|hope\s+(?:this|that)\s+helps?|"
                r"let\s+me\s+know\s+if\s+(?:you\s+)?(?:have\s+any|there'?s\s+anything|"
                r"you\s+need\s+anything|this\s+helps)[^.!?]*|"
                r"feel\s+free\s+to\s+ask[^.!?]*)\s*[!.?]*\s*$", re.I), ""),
]

# Markdown Piper would pronounce. The CONTENT is always kept.
_MARKDOWN: list[tuple[str, re.Pattern[str], object]] = [
    ("bold/italic markers", re.compile(r"(\*{1,3}|_{2,3})(?=\S)(.+?)(?<=\S)\1", re.S), r"\2"),
    # SINGLE UNDERSCORE ITALICS, guarded so identifiers survive. `_italic_`
    # is emphasis and must go; `node_modules` and `st_file_attributes` are
    # names and must not. The word-boundary lookarounds are the whole
    # difference — the pair only matches when the underscores sit OUTSIDE the
    # word, which is never true inside an identifier.
    ("underscore italics",
     re.compile(r"(?<![\w_])_(?=\S)([^_\n]+?)(?<=\S)_(?![\w_])"), r"\1"),
    ("inline code ticks", re.compile(r"`{1,3}([^`]+)`{1,3}", re.S), r"\1"),
    ("heading hashes", re.compile(r"^\s{0,3}#{1,6}\s+", re.M), ""),
    # A STRAY HASH MID-LINE. Not a real heading, but Piper still pronounces it
    # "hash", so it goes. Guarded against `C#`, `#1` and `##` — it only fires
    # on a lone `#` that is followed by a space and a word, which is the
    # heading shape and nothing else.
    ("stray hash", re.compile(r"(?<![\w#])#(?=\s+\w)"), ""),
    ("bullet markers", re.compile(r"^\s{0,4}[-*•·]\s+", re.M), ""),
    ("stray emphasis", re.compile(r"(?<!\w)\*(?!\w)"), ""),
]

# ── FLAGGED ONLY: real tells, but rewriting them would rewrite meaning ───────

_FLAG: list[tuple[str, re.Pattern[str]]] = [
    ("no-personal-preferences",
     re.compile(r"\bi\s+(?:do\s*n[o']t|don'?t)\s+have\s+(?:personal\s+)?"
                r"(?:preferences?|opinions?|feelings?|experiences?|a\s+body|access\s+to)", re.I)),
    ("hedge stack",
     re.compile(r"\b(?:it\s+depends|generally|typically|usually|perhaps|possibly|"
                r"might|may|could)\b[^.?!]{0,60}?\b(?:however|but|although|though|"
                r"generally|typically|perhaps|possibly)\b", re.I)),
    ("over-apologising",
     re.compile(r"\b(?:i\s+apolog(?:ise|ize)|i'?m\s+(?:so\s+|very\s+|really\s+)?sorry)\b", re.I)),
    ("reflexive list",
     re.compile(r"(?:^|\n)\s*(?:\d+[.)]|first(?:ly)?[,:]|second(?:ly)?[,:]|third(?:ly)?[,:])\s+",
                re.I | re.M)),
    ("em-dash overuse", re.compile(r"—")),
    ("emoji", re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F900-\U0001F9FF]")),
    ("delve/tapestry register",
     re.compile(r"\b(?:delve|tapestry|realm|multifaceted|underscore[sd]?|"
                r"navigate\s+the\s+complexit|in\s+today'?s\s+world)\b", re.I)),
]

#: More em-dashes than this in one reply is a tell rather than a style.
EM_DASH_LIMIT = 2


@dataclass
class Result:
    text: str
    stripped: list[str] = field(default_factory=list)
    flagged: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.stripped and not self.flagged

    @property
    def tells(self) -> list[str]:
        return sorted(set(self.stripped) | set(self.flagged))


def _tidy(text: str, original: str = "") -> str:
    """Repair the seams a strip leaves behind, without touching wording."""
    out = re.sub(r"[ \t]{2,}", " ", text)
    out = re.sub(r"\n{3,}", "\n\n", out)
    out = re.sub(r"^[ \t]+", "", out, flags=re.M)
    out = re.sub(r"\s+([,.;:!?])", r"\1", out)
    out = out.strip()
    # A strip at the front can leave a lower-case sentence opener: "Certainly!
    # the file is locked" becomes "the file is locked", which should not start
    # in lower case.
    #
    # ⚠ ONLY WHEN THE ORIGINAL STARTED WITH A CAPITAL. Capitalising
    # unconditionally rewrote a reply that legitimately opened lower case —
    # "node_modules is generated" became "Node_modules is generated", which
    # changes a name she was quoting. The test is whether a capital was there
    # BEFORE and our strip removed it, which is exactly the case this repairs.
    started_upper = next((c.isupper() for c in original.strip() if c.isalpha()), False)
    if out and out[0].islower() and started_upper:
        out = out[0].upper() + out[1:]
    return out


def flags(text: str) -> list[str]:
    """Every FLAG-only tell present. Detection, no modification."""
    found: list[str] = []
    for name, pattern in _FLAG:
        hits = pattern.findall(text or "")
        if not hits:
            continue
        if name == "em-dash overuse" and len(hits) <= EM_DASH_LIMIT:
            continue
        found.append(name)
    return found


def humanise(text: str) -> Result:
    """
    Remove the filler, report the rest.

    Never raises and never returns empty for non-empty input: if stripping
    somehow consumed everything — a reply that was nothing BUT filler — the
    original is kept, because silence is worse than a stilted sentence.
    """
    original = str(text or "")
    if not original.strip():
        return Result(text=original)

    out = original
    stripped: list[str] = []
    # ⚠ MARKDOWN FIRST, THEN THE PHRASES, AND THE ORDER IS A BUG FIX.
    #
    # Run the other way round, "It's important to note that **node_modules** is
    # generated" left the asterisks in place while the wind-up pattern was
    # looking for a word character after "that". It found none, backtracked,
    # and ate the "t" of "that" instead — producing "That node_modules is
    # generated." Stripping the formatting first means every phrase pattern
    # sees the sentence the model actually meant.
    for name, pattern, repl in _MARKDOWN + _STRIP:
        new = pattern.sub(repl, out)
        if new != out:
            stripped.append(name)
            out = new

    out = _tidy(out, original)
    if not out.strip():
        return Result(text=original.strip(), stripped=[], flagged=flags(original))
    return Result(text=out, stripped=stripped, flagged=flags(out))


def describe(result: Result) -> str:
    """One line for the log, so a persistent tell is visible rather than guessed at."""
    if result.clean:
        return "clean"
    bits = []
    if result.stripped:
        bits.append("stripped " + ", ".join(result.stripped))
    if result.flagged:
        bits.append("FLAGGED " + ", ".join(result.flagged))
    return "; ".join(bits)
