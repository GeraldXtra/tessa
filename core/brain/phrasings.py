"""
core/brain/phrasings.py — spoken English to tool NAME + ARGS.

THE RULE THIS FILE IS BUILT ON

Coverage loses to fuzziness. A table with two hundred exact phrases fails the
first time he says the two hundred and first, and he does not get told which
two hundred were the right ones — he just learns the thing is unreliable and
stops using it. So every rule here matches a VERB PLUS A SHAPE, not a sentence,
and the noun it operates on goes through the same alias-and-plural resolution
that made "open my download" work after Whisper dropped the s.

ORDER IS SEMANTIC, NOT COSMETIC. The table is scanned top to bottom and the
first match wins, so the specific rules sit above the general ones:

  * `kill 14284` must be read as a PID before `kill` is read as anything else,
    and `kill port 8080` must never reach it at all.
  * `run git status` is a shell command; `what's running` is a process list.
    The first is anchored to the start of the utterance so the second cannot
    collide with it.
  * `copy X to Y` is a file copy; `copy that` is the clipboard. The presence of
    a destination is what separates them, because that is what separates them
    in English.

WHAT IS DELIBERATELY NOT HERE

No rule constructs a command string. Every `args` dict below is built from
CAPTURED GROUPS assigned to NAMED PARAMETERS — a path, a name, an integer, a
direction. `shell.execute` is the single tool that receives free text, it is
RED, it holds, and it refuses anything whose provenance is not `human`
(core/tools/shell.py).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from .tools_local import ToolCall, folder_for

#: He says all three and Whisper returns all three. One fragment,
#: reused, rather than three rules that drift.
_WHATS = r"\b(?:what'?s|whats|what\s+is)"

Builder = Callable[[re.Match[str]], dict[str, Any]]


# ── shared extraction ────────────────────────────────────────────────────────

def _target(text: str) -> str | None:
    """
    Resolve a spoken noun to a path: a known folder (with aliases and plurals),
    or a literal drive path he read out.
    """
    if not text:
        return None
    t = text.strip(" .,?!\"'")
    m = re.search(r"([a-z]:\\[^\s\"']+)", t, re.I)
    if m:
        return m.group(1)
    hit = folder_for(t)
    return str(hit) if hit else None


def _need_target(text: str) -> str:
    got = _target(text)
    if got is None:
        # Hand back the raw words. `files._resolve` will fail with a real reason
        # and she will say which name did not resolve — better than a silent
        # UNROUTED that tells him nothing about why.
        return text.strip(" .,?!\"'")
    return got


def _clean(s: str) -> str:
    return " ".join(s.split()).strip(" .,?!\"'")


#: "the second one" is how he refers to a post she just read out. Whisper
#: returns words for small numbers as often as digits, so both forms resolve.
_ORDINALS = {"first": 1, "one": 1, "second": 2, "two": 2, "third": 3, "three": 3,
             "fourth": 4, "four": 4, "fifth": 5, "five": 5}


def _ordinal(raw: str | None) -> int:
    if not raw:
        return 1
    r = str(raw).strip().lower()
    if r.isdigit():
        return max(1, int(r))
    return _ORDINALS.get(r, 1)


#: How he names a post for the thread read: a status id he read out, a small
#: number or ordinal from the list she read to him, or a demonstrative.
_THREAD_REF = (r"\d{5,25}|\d{1,2}|first|second|third|fourth|fifth|one|two|three|four|five|"
               r"that(?:\s+one)?|it|last\s+one")


#: How he names a post for like/unlike: an id he read out, an ordinal into the
#: last read, or a demonstrative for the first one. NOT "last one" — for a
#: public act an ambiguous reference stays unrouted rather than guessed.
_ENGAGE_REF = (r"\d{5,25}|\d{1,2}|first|second|third|fourth|fifth|one|two|three|four|five|"
               r"that(?:\s+one)?|this(?:\s+one)?|it")

#: Words that name MANY, for the bulk rules below.
_BULK = r"(?:all|every|everything|everyone|everybody|each|whoever|anyone|anybody)"

#: DIRECT MESSAGES (round 4, 2026-09-12): the verbs, the words that are NOT
#: a recipient, the optional lead-in, and what separates the recipient from
#: the words. `_NOT_HANDLE` keeps "message the team", "dm me" and "message
#: everyone" from being read as an @name — the bulk words are listed so the
#: BULK rule (above the single-recipient one) is the only route for them,
#: where the tool refuses by shape. `_DM_LIST` is the shape of MORE THAN ONE
#: recipient: "a and b", "@a, @b", "a, b saying" / "a, b, c" — while "DM
#: @ada, thanks" (Whisper's comma at his pause) stays one recipient.
_DM_VERB = r"(?:dm|d\.m\.|direct[- ]message|private[- ]message)"
#: A STATUS after "message" is not a recipient: "message received, loud and
#: clear" and "message sent, thanks" name no account (they used to reach
#: x.send_dm as a DM to @received / a bulk "sent, thanks" and be refused).
_DM_STATUS = r"received|sent|delivered|failed|says|from"
_NOT_HANDLE = (r"the|a|an|me|it|that|this|them|him|her|us|my|your|our|their|his|its|x|twitter|"
               r"all|every|everyone|everybody|everything|each|anyone|anybody|whoever|someone|"
               r"somebody|people|folks|back|later|now|please|about|to|for|"
               rf"{_DM_STATUS}")
_LEAD = r"^\s*(?:tessa[,\s]+)?(?:please\s+)?(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?)?"
_DM_SEP = r"(?:saying|with|that\s+says|:|,|-|—)"
_DM_LIST = (r"@?[A-Za-z0-9_]{1,15}\s*(?:\band\b|&|\+)\s*@?[A-Za-z0-9_]{1,15}|"
            r"@[A-Za-z0-9_]{1,15}\s*,\s*@[A-Za-z0-9_]{1,15}|"
            r"@?[A-Za-z0-9_]{1,15}\s*,\s*@?[A-Za-z0-9_]{1,15}"
            r"(?=\s*(?:,|\band\b|&|\+|saying|with|that\s+says|:|[.!?]*\s*$))")

#: How he names a post for `share`, which is a generic verb: an ordinal, an id
#: or "that (one)" — never "it" or "this", so "share it" and "share that file"
#: cannot become a public repost (round 3, 2026-09-12; they used to). Since
#: the media round (2026-09-22) `share` is the post's LINK (x.share_link, a
#: green read with no press) and no longer a repost; the same strictness.
_SHARE_REF = (r"\d{5,25}|\d{1,2}|first|second|third|fourth|fifth|one|two|three|four|five|"
              r"that(?:\s+one)?")

#: THE SENTENCES ROUND (2026-10-06) — fragments for the profile, follow-by-name
#: and like-by-author rules. `_AT_HANDLE` is an @ that STARTS a word, so an
#: e-mail address ("ada@example.com") and "remind me @ 5pm" are never a handle;
#: `.format(g=...)` names its group. `_ACCOUNT_NAME` is a spoken name for the
#: tool to resolve through X's people search — never a pronoun or an article,
#: and never the start of "follow up", "follow the link", "follow the readme".
_AT_HANDLE = r"(?<![\w.@])@(?P<{g}>[A-Za-z0-9_]{{1,15}})"
_PROFILE_NOUN = (r"(?:bio|biography|profile\s+bio|profile\s+description|description|"
                 r"about(?:\s+section)?|intro)\b")
_ON_X = r"(?:\s+(?:on|for|in)\s+(?:x|twitter))?"
_PROFILE_LEAD = r"(?:^|(?<=\s))"
_ACCOUNT_NAME = (r"(?!(?:the|a|an|it|that|this|these|those|he|she|they|them|you|me|my|your|our|his|her|"
                 r"up|along|through|suit|back|everyone|everybody|all|x|twitter)\b)[A-Za-z0-9][A-Za-z0-9 .'&_-]{0,40}?")
#: "abeg", "pls", "help me", "make you", "can you" — how he opens a request.
_ASK_LEAD = (r"^\s*(?:tessa[,\s]+)?(?:(?:abeg|pls|please)[,\s]+)?"
             r"(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?|help\s+me\s+|make\s+you\s+|go\s+(?:and\s+)?)?")
_ASK_TAIL = r"(?:\s+(?:please|pls|abeg|for\s+me))*\s*[.!?]*\s*$"


def _like_by_author(m: "re.Match[str]") -> dict[str, Any]:
    """
    "like the newest post from @premierleague" / "like their first 3 post" ->
    x.like {author, nth[, count]}. No author and no possessive is NOT this
    rule (ValueError = the rule did not fire): "like the first post" names a
    post she read, and stays with the ordinal rule below.
    """
    author = (m["at"] or m["who"] or "").strip()
    if not author and m["poss"]:
        author = "their"
    if not author:
        raise ValueError("no author")
    nth = {"second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4,
           "fifth": 5, "5th": 5}.get((m["ord"] or "").lower(), 1)
    out: dict[str, Any] = {"author": author, "nth": nth}
    count = _count(m["n"])
    if count > 1:
        # intents.py turns a count into a plan — ONE hold now, the rest named.
        out["count"] = count
    return out


_REPLY_TARGET = (r"(?:(?P<poss>their|his|her|its)\s+|(?P<at>@[A-Za-z0-9_]{1,15})(?:'s|s'|’s)\s+|the\s+)?"
                 r"(?:(?P<ord>second|third|fourth|fifth|2nd|3rd|4th|5th)\s+)?"
                 r"(?:newest|latest|last|most\s+recent|recent|new|first|top)\s+(?:post|tweet)"
                 r"(?:\s+(?:from|by|of|on)\s+(?P<who>@[A-Za-z0-9_]{1,15}))?")


def _reply_by_author(m: "re.Match[str]") -> dict[str, Any]:
    author = (m["at"] or m["who"] or "").strip()
    if not author and m["poss"]:
        author = "their"
    text = next((m[k] for k in ("t1", "t2", "t3", "t4", "t5") if m.groupdict().get(k)), "")
    if not author or not text.strip():
        raise ValueError("no author or no words")
    nth = {"second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4,
           "fifth": 5, "5th": 5}.get((m["ord"] or "").lower(), 1)
    return {"text": " ".join(text.split()), "author": author, "nth": nth}


def _post_args(ref: str | None) -> dict[str, Any]:
    """
    `x.like` / `x.unlike` name a post the way `x.read_thread` does: a status
    id he read out (5+ digits) is `post_id`; an ordinal is an `index` into
    the last read; "that" / "it" is the first one. The handler resolves the
    index to an id and holds on the id.
    """
    r = " ".join(str(ref or "").split()).lower()
    if r.isdigit() and len(r) >= 5:
        return {"post_id": r}
    if not r or r in ("that", "that one", "this", "this one", "it"):
        return {"index": 1}
    return {"index": _ordinal(r)}


def _need(m: Any) -> str:
    """An unrepost with no post reference at all is not a route (ValueError = rule did not fire)."""
    raise ValueError("no post reference")


def _count(raw: str | None) -> int:
    """A spoken or written small count ("three", "5"); 0 means "not said"."""
    r = " ".join(str(raw or "").split()).lower()
    if not r:
        return 0
    if r.isdigit():
        return int(r)
    return int(_UNITS.get(r, 0))


def _thread_args(ref: str | None) -> dict[str, Any]:
    """
    `x.read_thread` names a post by STATUS ID when he read one out (5+
    digits), by the position in the last read otherwise ("post two"), and by
    nothing at all for "read the replies" — the handler then takes the first
    post of the last read and says whose it was.
    """
    r = " ".join(str(ref or "").split()).lower()
    if not r or r in ("that", "that one", "it", "last one"):
        return {}
    if r.isdigit() and len(r) >= 5:
        return {"post_id": r}
    return {"index": _ordinal(r)}


# ── spoken numbers, for the levels the capability tools take ─────────────────
#
# `system.volume.set` and `system.brightness.set` take an ABSOLUTE level, and
# Whisper returns "forty" as often as "40" — measured on this machine: "set
# brightness to eighty" reached the table with the word, matched the rule that
# required `\d`, failed it, and fell through to the BARE brightness rule, so
# she READ the brightness back instead of setting it. A set that silently
# becomes a get is the worst shape of failure: it answers, so it looks like it
# worked.
#
# DELIBERATELY A CLOSED TABLE, NOT A PARSER. Zero to a hundred is the entire
# domain of both tools, so this is thirty entries and a compound rule, all of
# it exhaustively testable. Anything outside it raises ValueError, which
# `match()` already treats as "this rule did not fire" — so an unparseable
# value falls through to UNROUTED and she says she did not catch it, rather
# than acting on a number nobody said.

_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
#: `fourty` is not English and Whisper writes it anyway.
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}

_TENS_RE = "|".join(_TENS)
_UNITS_RE = "|".join(sorted(_UNITS, key=len, reverse=True))

#: One number, spoken or written. Longest alternatives first so `six` cannot
#: win over `sixteen`.
_NUM = (rf"(?:\d{{1,3}}|(?:a|one)\s+hundred|hundred|"
        rf"(?:{_TENS_RE})(?:[\s-]+(?:{_UNITS_RE}))?|(?:{_UNITS_RE}))")


def _spoken_number(raw: str | None) -> int | None:
    """40, "forty", "forty five", "forty-five", "a hundred". None if it is none of those."""
    t = " ".join(str(raw or "").lower().replace("-", " ").split())
    if not t:
        return None
    if t.isdigit():
        return int(t)
    if t in ("a hundred", "one hundred", "hundred"):
        return 100
    total = None
    for w in t.split():
        if w in _TENS:
            total = (total or 0) + _TENS[w]
        elif w in _UNITS:
            total = (total or 0) + _UNITS[w]
        elif w != "a":
            return None
    return total


def _level(raw: str | None) -> int:
    """
    A level for the set tools. Raises ValueError when the words are not a
    number, so `match()` falls through rather than inventing one.

    OUT OF RANGE IS DELIBERATELY NOT REJECTED HERE. "set volume to two
    hundred" parses fine as a number and the capability refuses it with a
    reason he can act on ("level must be at most 100"). Rejecting it in the
    router instead would send the same sentence to UNROUTED, where all he
    hears is that she did not catch it — which is untrue, and unhelpful.
    """
    n = _spoken_number(raw)
    if n is None:
        raise ValueError(f"not a spoken number: {raw!r}")
    return n


# ── argument builders for the read-only observers ────────────────────────────
#
# Both read `m.string` — the whole clause — rather than adding more capture
# groups to already-long alternations. The values they produce are bounded
# here AND declared as bounded parameters on the capability, so a spoken
# number can never widen a query beyond what the tool accepts.

_EVENT_LOGS = ("system", "application", "setup")


def _eventlog_args(m: re.Match[str]) -> dict[str, Any]:
    """Which log, and how many records. Defaults to the last 10 of System."""
    said = (m.string or "").lower()
    log = "System"
    for name in _EVENT_LOGS:
        # `application` before `system`, so "the application log" is not read
        # as System merely because the word system appears elsewhere.
        if re.search(rf"\b{name}\b", said):
            log = name.capitalize()
            if name != "system":
                break
    count = re.search(r"\b(?:last|latest|recent)?\s*(\d{1,2})\b", said)
    args: dict[str, Any] = {"log": log}
    if count:
        args["count"] = max(1, min(int(count.group(1)), 50))
    return args


def _services_args(m: re.Match[str]) -> dict[str, Any]:
    """A named service, or all of them; `running` narrows it when no name was said."""
    name = _clean(m["name"] or "") if "name" in (m.groupdict() or {}) else ""
    said = (m.string or "").lower()
    args: dict[str, Any] = {"name": name}
    if not name and re.search(r"\brunning\b", said):
        args["running_only"] = True
    return args


# ── argument builders for the dev/files batch ────────────────────────────────

#: Spoken priority words -> the closed level set `system.process.priority` takes.
#: `realtime` is passed through ON PURPOSE so the TOOL refuses it by name,
#: with the list of what she will set, rather than the router silently
#: rounding it down to "high".
_LEVEL_WORDS = {
    "raise": "raise", "boost": "raise", "increase": "raise", "bump": "raise",
    "higher": "raise", "more": "raise",
    "lower": "lower", "reduce": "lower", "drop": "lower", "decrease": "lower",
    "less": "lower",
    "idle": "low", "low": "low", "normal": "normal", "high": "high",
}


def _priority_level(raw: str | None) -> str:
    w = " ".join((raw or "").lower().replace("-", " ").split())
    if w.startswith("real"):
        return "realtime"
    if w in ("above normal", "below normal"):
        return w
    return _LEVEL_WORDS.get(w, w)


def _details_args(raw: str | None) -> dict[str, Any]:
    """A number is a pid; anything else is part of a name. Pronouns are neither."""
    v = _clean(raw or "")
    if v.isdigit():
        return {"pid": int(v)}
    if not v or not _is_nameable(v):
        raise ValueError("not a process")
    return {"name": v}


def _kill_name(raw: str | None) -> dict[str, Any]:
    """
    "kill chrome" is NOT a kill. It becomes `proc.find` so she reads back the
    matching pids and he names ONE. A pronoun ("kill it") is a mistranscribed
    sentence and falls through to the honest miss, exactly as it did before.
    """
    name = _clean(raw or "")
    if not name or name.isdigit() or not _is_nameable(name):
        raise ValueError("not a process name")
    return {"name": name}


def _split_where(phrase: str) -> tuple[str, str]:
    """
    "notes in documents" -> ("notes", "documents"). The LAST " in " wins, so a
    name containing the word survives ("build in progress in documents").
    Returns ("", "") for the name when there is nothing left.
    """
    text = _clean(phrase)
    lowered = text.lower()
    cut = lowered.rfind(" in ")
    if cut == -1:
        return text, ""
    return _clean(text[:cut]), _clean(text[cut + 4:])


def _create_path(phrase: str) -> str:
    """
    A spoken "<name> [in <folder>]" to a full path.

    Resolution is Python's, never the model's: `folder_for` is the same alias
    resolver "open my downloads" uses, so "documents", "downloads" and "my
    desktop" all land where he means. An unrecognised destination falls back to
    Documents rather than guessing at the filesystem root.

    Raises ValueError when there is no usable name, which `match()` treats as
    "this rule did not fire" — so a garbled create falls through to UNROUTED
    instead of making a folder called nothing.
    """
    name, where = _split_where(phrase)
    if not name:
        raise ValueError("no name to create")
    # A name carrying a separator is a PATH he read out, not a name.
    if any(sep in name for sep in ("\\", "/", ":")):
        return name
    if any(ch in name for ch in '<>:"|?*'):
        raise ValueError(f"not a usable name: {name!r}")
    base = None
    if where:
        hit = folder_for(where)
        if hit is not None:
            base = Path(str(hit))
        elif any(sep in where for sep in ("\\", "/", ":")):
            base = Path(where)
    if base is None:
        base = Path.home() / "Documents"
    return str(base / name)


def _code_path(phrase: str) -> str:
    """
    What "open X in vscode" means by X.

    Deliberately CONSERVATIVE: a literal path, or a known folder, or a project
    directory the filename index already knows about. Anything else raises
    ValueError so the clause falls through to intents.py's existing VS Code
    branch, which has its own resolution and already works. This rule can only
    ever ADD precision; it cannot take a phrase away from what handled it
    before.
    """
    text = _clean(phrase)
    if not text:
        raise ValueError("no target")
    # Strip the filler he actually says: "the tessa project", "my notes file".
    stripped = re.sub(r"^(?:the|my|this|that)\s+", "", text, flags=re.I)
    stripped = re.sub(r"\s+(?:project|folder|repo|repository|file)$", "", stripped, flags=re.I)
    if any(sep in text for sep in ("\\", "/", ":")):
        return text
    # A PRONOUN IS NOT A PROJECT NAME. "open this in vs code" means the thing
    # in hand, and the index would happily match some unrelated directory
    # containing the letters "this". Falling through leaves it to intents.py's
    # VS Code branch, which opens the editor itself — the existing behaviour.
    if not _is_nameable(stripped):
        raise ValueError(f"{stripped!r} is a pronoun, not a target")
    hit = folder_for(stripped)
    if hit is not None:
        return str(hit)
    # Something the index knows. Names only — the index never opens a file.
    #
    # AN EXACT NAME BEATS A DIRECTORY. "open plan.md in vscode" names a FILE,
    # and an earlier version only accepted directories, so it fell through and
    # opened the editor with nothing in it. Order: exact basename match first
    # (he named the thing), then a directory (he named a project), then
    # nothing rather than a guess.
    try:
        from core.system.winapi import fileindex

        hits = fileindex.get_index().find(stripped, limit=25)
        for candidate in hits:
            if Path(candidate).name.lower() == stripped.lower():
                return candidate
        for candidate in hits:
            if Path(candidate).is_dir():
                return candidate
    except Exception:  # noqa: BLE001 — never let the index break routing
        pass
    raise ValueError(f"cannot resolve {stripped!r} to a path")


_SET_VERB = re.compile(r"\b(?:set|put|change|make)\b.*\b(?:to|at)\b", re.I)


def _no_stranded_set(m: re.Match[str]) -> dict[str, Any]:
    """
    `{}` for a genuine query, ValueError for a SET whose level did not parse.

    See the brightness get rule for why. Raising here is how a rule declines
    a clause it matched textually but must not act on.
    """
    if _SET_VERB.search(m.string or ""):
        raise ValueError("a set with an unreadable level is not a get")
    return {}


# ── INPUT AND SCREEN HELPERS ─────────────────────────────────────────────────
#
# ⚠ THE KEY NAMES ARE A CLOSED SET, TAKEN FROM THE MECHANISM'S OWN TABLE.
#
# If this pattern accepted any word, "press the send button" would match and be
# handed to a keyboard capability that would then refuse it — turning a browser
# click into a confusing failure. Narrowing it here means the browser rule below
# still gets its own sentences, and it also means the two tables cannot drift:
# a key spelled here that `winapi/inputs.py::VK` does not have is caught by the
# proof rather than at the moment he says it.
_KEYNAME = (r"enter|return|tab|escape|esc|space|backspace|delete|insert|home|end|"
            r"page\s?up|page\s?down|up|down|left|right|f1[0-2]|f[1-9]|[a-z0-9]")
_MODWORD = r"ctrl|control|alt|shift|win|windows"
_KEYCOMBO = rf"(?:(?:{_MODWORD})(?:\s*[+\-]\s*|\s+))*(?:{_KEYNAME})"

_MOD_CANON = {"control": "ctrl", "windows": "win", "cmd": "win", "command": "win"}


def _keys(raw: str | None) -> str:
    """Spoken combination to the mechanism's spelling: "control c" -> "ctrl+c"."""
    text = " ".join(str(raw or "").lower().replace("-", " ").replace("+", " ").split())
    text = re.sub(r"\bpage\s+(up|down)\b", r"page\1", text)
    parts = [_MOD_CANON.get(p, p) for p in text.split() if p and p != "key"]
    if not parts:
        raise ValueError("no key named")
    return "+".join(parts)


def _typed(raw: str | None) -> str:
    """
    What to type, with the quotes he speaks around it removed.

    NOT `_clean`: that lower-cases and strips punctuation for matching a tool
    name, and the whole point here is to reproduce his words exactly. Typing
    "email bob about friday" when he said "Email Bob about Friday." would be a
    quiet corruption of the one capability whose entire job is fidelity.
    """
    text = str(raw or "").strip()
    # ⚠ A DESTINATION MEANS THE BROWSER, AND THE VETO IS HERE RATHER THAN IN A
    # LOOKAHEAD. "type my email into the search box" is `browser.type`, which
    # finds the field and types into it; the first version of this rule tried to
    # exclude it with `(?!.*\bin(?:to)?\s+(?:the\s+)?\S+\s*$)` and that only
    # matched a ONE-WORD field name, so "the search box" slipped through and the
    # whole phrase — destination included — would have been typed as literal
    # text. Vetoing here drops the rule so the browser rule below takes it,
    # which is the same mechanism `_no_stranded_set` uses.
    if re.search(r"\binto\b|\bin\s+the\s+\S+(?:\s+\S+)?\s*(?:field|box|bar)\b", text, re.I):
        raise ValueError("that names a destination — it is a browser type")
    if len(text) > 1 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1].strip()
    if not text:
        raise ValueError("nothing to type")
    return text


def _scroll_amount(direction: str | None, size: str | None) -> int:
    d = (direction or "down").lower()
    if d in ("top", "bottom"):
        return 25 if d == "top" else -25
    notches = {"little": 1, "bit": 2, "lot": 8}.get((size or "").lower(), 3)
    return notches if d in ("up", "left") else -notches


#: Words that mean he is talking about the BROWSER, not the screen. A tail
#: containing one of these vetoes the screen rule so the browser rule below
#: gets the sentence — the same ValueError mechanism `_no_stranded_set` uses.
_BROWSER_WORDS = ("page", "site", "website", "tab", "browser", "chrome", "article")


def _shot_target(m: "re.Match[str]") -> dict:
    tail = (m.groupdict().get("tail") or "").lower()
    whole = m.string.lower()
    if any(w in tail or f"of the {w}" in whole for w in _BROWSER_WORDS):
        raise ValueError("that is a browser screenshot")
    if "window" in tail or "window" in whole:
        return {"what": "window"}
    return {"what": "screen"}


#: Spoken spellings of the two radios, normalised to what `system.radio.set`
#: declares as its closed `radio` choices.
def _radio(raw: str | None) -> str:
    t = " ".join(str(raw or "").lower().replace("-", " ").split())
    if t.startswith("blue"):
        return "bluetooth"
    if t.replace(" ", "").startswith("wifi") or t.replace(" ", "") == "wifi":
        return "wifi"
    raise ValueError(f"not a radio: {raw!r}")


# ── the table ────────────────────────────────────────────────────────────────
#
# (pattern, tool name, args builder, spoken opener)
#
# The opener is what she says WHILE the tool runs. Short, because Piper streams
# per sentence and the first one is the whole 400 ms budget.

# ── software: install / uninstall resolve a spoken NAME to an exact winget id ──
#
# `system.software.install` and `.uninstall` are RED and take an exact catalog
# id, FROZEN on the card (core/system/abilities/software_change.py). The
# builders below do the name -> id step AT ROUTING TIME through the same
# read-only search the green tool uses, so what reaches the card is
# "VideoLAN.VLC", never "vlc" to be looked up again later against a catalog
# that may have changed between the card and the run. When the name does not
# resolve to ONE package the builder raises ValueError and `match()` falls
# through to the next rule — the same regex, routed to the green search, which
# reads the candidates back so he can name the one he meant. Nothing here ever
# builds a path, a URL or a command: the id must pass `software_change.ID_RE`
# and the argv it lands in is a constant.

def _software_query(raw: str | None) -> str:
    what = _clean(raw or "")
    if not what or not _is_nameable(what):
        raise ValueError("no program named")     # "install it" is not a request
    return what


def _resolve_software(raw: str | None, scope: str) -> dict[str, Any]:
    from core.system.abilities import software_change

    what = _software_query(raw)
    try:
        hit = software_change.resolve(what, scope)
    except software_change.Refused as r:
        raise ValueError(str(r.args[0])) from None
    except Exception as exc:  # noqa: BLE001 — a winget hiccup is not a route
        raise ValueError(type(exc).__name__) from None
    if not hit.id:
        raise ValueError(hit.why)
    return {"id": hit.id, "name": hit.name}


# ── registry / services: an EXACT target resolved at ROUTING time ─────────
#
# `system.registry.set` and `system.service.start` / `.stop` / `.set_startup`
# are RED and take an EXACT target, FROZEN on the card
# (core/system/abilities/registry_write.py, service_control.py). The builders
# below do the spoken-name -> exact-target step HERE, so what reaches the card
# is `Spooler`, not "print spooler" to be looked up again later against a
# service table that may have changed between the card and the run.
#
# NOTHING HERE JUDGES. What may be written and what may be controlled is
# decided by `judge()` / `protection()` at the card (`describe`) and AGAIN
# after approval. These builders only get the shape right; a shape they
# cannot make raises ValueError and `match()` falls through to the next rule.

#: A literal backslash. A registry key is spelled with them, and this file
#: cannot carry one in an f-string expression.
BS = chr(92)

#: The same backslash, escaped for a regex — `{_BS_RE}` inside an rf-pattern
#: is ONE literal backslash to match, not an escape of whatever follows it.
_BS_RE = BS + BS


def _registry_args(key: str | None, name: str | None, value: str | None) -> dict[str, Any]:
    """
    A spoken registry target -> {key, name, value}. When no value NAME was
    said separately, the LAST part of the key path is it: "set registry
    HKCU...Explorer Advanced HideFileExt to 0" is the value `HideFileExt`
    under the key above it. A hive on its own is not a key.
    """
    k = " ".join(str(key or "").split()).strip(" .!?").strip('"' + "'")
    n = " ".join(str(name or "").split()).strip(" .!?").strip('"' + "'")
    v = " ".join(str(value or "").split()).strip(" .!?").strip('"' + "'")
    if not k or BS not in k:
        raise ValueError("not a registry key")
    if not n:
        k, _, n = k.rpartition(BS)
        if not k or BS not in k:
            raise ValueError("no value name under that key")
    if not n or not v:
        raise ValueError("nothing to set")
    return {"key": k, "name": n, "value": v}


#: A bare article is what is left when `(?:the\s+)?` did not take it: "start
#: the service" names nothing, and a lookup for a service called "the" is a
#: worse answer than "I did not catch that". Same idea as `_is_nameable`.
_BARE_WORDS = frozenset({"the", "a", "an", "that", "this", "it", "them", "those", "these", "my", "his"})


def _service_query(raw: str | None) -> str:
    """The spoken service name, or NO ROUTE. Both the card rule and its green
    read-back partner go through here, so neither can fire on nothing."""
    what = _clean(raw or "")
    if not what or not _is_nameable(what) or what.lower() in _BARE_WORDS:
        raise ValueError("no service named")
    return what


def _resolve_service(raw: str | None) -> dict[str, Any]:
    """
    What he said -> ONE service, by its exact Windows key name. Unresolved
    (nothing matched, or several did) raises ValueError, and the green
    observer rule below the control rules reads the candidates back instead.
    """
    from core.system.abilities import service_control

    what = _service_query(raw)
    try:
        hit = service_control.resolve(what)
    except Exception as exc:  # noqa: BLE001 — a service-table hiccup is not a route
        raise ValueError(type(exc).__name__) from None
    if not hit.name:
        raise ValueError(hit.why)
    return {"name": hit.name, "display": hit.display}


#: Spoken start modes -> the closed set `system.service.set_startup` takes.
_START_MODES: dict[str, str] = {
    "automatic": "automatic", "automatically": "automatic", "auto": "automatic",
    "manual": "manual", "manually": "manual", "on demand": "manual", "demand": "manual",
    "disabled": "disabled", "never": "disabled", "off": "disabled",
    "delayed": "delayed", "delayed start": "delayed", "delayed automatic": "delayed",
}


def _service_startup_args(m: re.Match[str]) -> dict[str, Any]:
    """The resolved service plus the start mode; no mode said is no route."""
    said = " ".join((m.groupdict().get("mode") or "").split()).lower()
    mode = _START_MODES.get(said, "")
    if not mode:
        raise ValueError("no start mode")
    args = _resolve_service(m["name"])
    args["mode"] = mode
    return args


# ── power: the machine nouns, the anchored lead/tail, and the spoken delay ──
#
# `_TAIL` ENDS THE CLAUSE. After the verb only the machine, "now", a delay
# and "please" may follow. That anchoring is what keeps "shut down chrome"
# and "restart the print spooler" out of the power tools: they fall through
# to whatever handled them before, or to an honest miss.
_MACHINE = r"(?:machine|laptop|pc|computer|system|windows|box|desktop)"
_DET = r"(?:the\s+|my\s+|this\s+)?"
_LEAD = r"^\s*(?:please\s+)?(?:(?:can|could|would)\s+you\s+(?:please\s+)?)?"

#: The pieces the registry and service rules are built from. Spelled once:
#: a hive-prefixed key (regedit's "Computer" prefix tolerated), a value that
#: is any run of printable text, a service name, the closed spoken start
#: modes, and the polite tail every rule ends with.
_REG_KEY = r"(?P<key>(?:computer" + _BS_RE + r")?hk[a-z_]+" + _BS_RE + r".+?)"
_REG_VALUE = r"(?P<value>\S.{0,200}?)"
_SVC_NAME = r"(?P<name>[\w.@\-' ]{2,48}?)"
_SVC_TAIL = r"(?:\s+for\s+me)?(?:\s+please)?\s*[.!?]*\s*$"
_MODE_RE = (r"(?P<mode>automatic(?:ally)?|auto|manual(?:ly)?|on\s+demand|demand|"
            r"disabled|delayed(?:\s+start|\s+automatic)?|never|off)")


_DELAY = (rf"(?:\s+in\s+(?:(?P<n>{_NUM})\s*|(?P<half>half\s+an?\s+)|an?\s+)?"
          r"(?P<unit>minutes?|mins?|min|seconds?|secs?|sec|hours?|hrs?|hr))?")
_TAIL = rf"(?:\s+{_DET}{_MACHINE})?(?:\s+now)?{_DELAY}(?:\s+please)?\s*[.!?]*\s*$"
_SHUT = (rf"(?:shut\s*-?\s*(?:it\s+)?down|shut\s+{_DET}{_MACHINE}\s+down"
         rf"|power\s+(?:it\s+)?(?:off|down)|power\s+{_DET}{_MACHINE}\s+(?:off|down)"
         rf"|(?:turn|switch)\s+{_DET}{_MACHINE}\s+off|(?:turn|switch)\s+off\s+{_DET}{_MACHINE})")
_POWER_NOUN = (r"(?:shut\s*-?\s*down|restart|reboot|hibernat(?:e|ion)|log\s*-?\s*(?:off|out)|"
               r"sign\s*-?\s*out|power\s*-?\s*(?:off|down)|countdown|timer|power\s+action)")


def _delay_args(m: re.Match[str]) -> dict[str, Any]:
    """
    "in ten minutes" -> {"delay_s": 600}; nothing spoken -> {} (the
    capability's default, sixty seconds). OUT OF RANGE IS NOT REJECTED HERE —
    "in five seconds" reaches the tool and is refused with the window he can
    act on ("ten seconds to an hour"), the same reasoning as `_level`.
    """
    gd = m.groupdict()
    unit = (gd.get("unit") or "").lower()
    if not unit:
        return {}
    if gd.get("half"):
        n: float = 0.5
    elif gd.get("n"):
        v = _spoken_number(gd["n"])
        if v is None:
            raise ValueError(f"not a spoken number: {gd['n']!r}")
        n = v
    else:
        n = 1
    mult = 3600 if unit.startswith("h") else 60 if unit.startswith("m") else 1
    return {"delay_s": int(round(n * mult))}


_RULES: list[tuple[re.Pattern[str], str, Builder, str]] = [

    # ── SHELL. Anchored, so only a sentence that STARTS with the verb is a
    #    command. "what's running" and "run of the mill" cannot reach it.
    (re.compile(r"^(?:run|execute)\s+(?P<cmd>\S.*)$", re.I),
     "shell.execute", lambda m: {"command": _clean(m["cmd"])},
     "Reading that back."),

    # ── DIRECT MESSAGES (X features round 4, the LAST, 2026-09-12): read
    #    his inbox or one conversation (green, PRIVATE), send ONE private
    #    message (red, card, recipient + message frozen). ABOVE the X reads:
    #    "read my conversation with ada" is a DM, not a public thread; ABOVE
    #    x.reply / x.post: "reply to ada's dm saying thanks" and "message ada
    #    saying post it" are private messages, never a public reply or tweet.
    #    EVERY RULE HERE IS ANCHORED to the start of the utterance (`_LEAD`):
    #    a send needs one @name-shaped recipient, so "what's the error
    #    message" and "send a message to the team" cannot reach a red tool,
    #    and "message <name>" also needs the words, so "message received" is
    #    not a send; a read needs dm / direct message / an X marker /
    #    messages-with-a-handle, so "read the message" and "read my emails"
    #    stay where they were, and "tweet that I read my dms" stays a tweet.
    #    SENDS BEFORE READS: "dm ada saying read my dms" is a send. BULK
    #    FIRST, on purpose: "dm everyone", "message all my followers", "dm
    #    @ada and @bob" reach the tool with the words as the recipient and are
    #    refused by shape, out loud and on the chain — a poisoned tweet's game
    #    is mass DM, and the router must never quietly pick one of them.
    (re.compile(rf"{_LEAD}(?:{_DM_VERB}|message|send\s+(?:a\s+)?(?:{_DM_VERB}|message)\s+to)\s+"
                rf"(?P<what>(?!(?:{_DM_STATUS})\b)(?:{_BULK}|(?:all\s+)?(?:of\s+)?my\s+(?:followers|contacts|friends|followings?)|"
                rf"(?:the\s+)?(?:people|accounts|users|folks)\s+who|{_DM_LIST})\b[^.]*?)"
                rf"(?:\s*{_DM_SEP}\s*(?P<text>.+))?\s*$", re.I),
     "x.send_dm", lambda m: {"handle": _clean(m["what"]), "text": _clean(m["text"] or "")},
     "One at a time."),
    # ONE recipient. "dm ada" with no words reaches the tool, which says what
    # to give it; "message <name>" needs the words (the rule after).
    (re.compile(rf"{_LEAD}{_DM_VERB}\s+@?(?P<h>(?!(?:{_NOT_HANDLE})\b)[A-Za-z0-9_]{{1,15}})"
                rf"(?:\s+on\s+(?:x|twitter))?\s*(?:{_DM_SEP}\s*(?P<text>.+))?\s*$", re.I),
     "x.send_dm", lambda m: {"handle": m["h"], "text": _clean(m["text"] or "")},
     "Reading it back."),
    (re.compile(rf"{_LEAD}message\s+@?(?P<h>(?!(?:{_NOT_HANDLE})\b)[A-Za-z0-9_]{{1,15}})"
                rf"(?:\s+on\s+(?:x|twitter))?\s*{_DM_SEP}\s*(?P<text>.+)\s*$", re.I),
     "x.send_dm", lambda m: {"handle": m["h"], "text": _clean(m["text"] or "")},
     "Reading it back."),
    # "send ada a dm: hi" / "send a message to @ada saying hi" / "send @ada a private message saying hi"
    (re.compile(rf"{_LEAD}send\s+(?:@?(?P<h>(?!(?:{_NOT_HANDLE})\b)[A-Za-z0-9_]{{1,15}})\s+(?:a\s+)?(?:{_DM_VERB}|message)|"
                rf"(?:a\s+)?(?:{_DM_VERB}|message)\s+to\s+@?(?P<h2>(?!(?:{_NOT_HANDLE})\b)[A-Za-z0-9_]{{1,15}}))"
                rf"(?:\s+on\s+(?:x|twitter))?\s*(?:{_DM_SEP}\s*(?P<text>.+))?\s*$", re.I),
     "x.send_dm", lambda m: {"handle": m["h"] or m["h2"], "text": _clean(m["text"] or "")},
     "Reading it back."),
    # "reply to ada's dm saying thanks" — a PRIVATE reply, never x.reply.
    (re.compile(rf"{_LEAD}(?:reply|respond|answer)\s+(?:to\s+)?@?(?P<h>[A-Za-z0-9_]{{1,15}})(?:'s|s'|’s)\s+"
                rf"(?:{_DM_VERB}|message)s?(?:\s+on\s+(?:x|twitter))?\s*(?:{_DM_SEP}\s*(?P<text>.+))?\s*$", re.I),
     "x.send_dm", lambda m: {"handle": m["h"], "text": _clean(m["text"] or "")},
     "Reading it back."),
    # ...and a reply to someone's dm / message the rule above could not parse
    # ("reply to ada's dm bob saying x" — a mangled tail) is STILL a private
    # reply: it reaches x.send_dm with nothing to send and is refused out loud
    # ("tell me what to say to them"). Before this it fell through to the
    # legacy x.reply rule, which made the WHOLE utterance a public reply to
    # post one — his private words, public, by position, one card away.
    (re.compile(rf"{_LEAD}(?:reply|respond|answer)\s+(?:to\s+)?@?(?P<h>[A-Za-z0-9_]{{1,15}})(?:'s|s'|’s)\s+"
                rf"(?:{_DM_VERB}|message)s?\b.*$", re.I),
     "x.send_dm", lambda m: {"handle": m["h"], "text": ""},
     "Reading it back."),
    # READ the inbox: dm(s) / direct messages / messages-with-an-X-marker.
    (re.compile(rf"{_LEAD}(?:(?:read|check|open|show\s+me|get\s+me|pull\s+up|any(?:\s+new)?|"
                r"do\s+i\s+have(?:\s+any)?(?:\s+new)?|what(?:'s|\s+is)\s+in|is\s+there\s+anything\s+in)\s+"
                r"(?:me\s+)?(?:my\s+|the\s+)?"
                r"(?:(?:x|twitter)\s+(?:dms?|direct\s+messages?|messages?|inbox)|dms?|d\.m\.s?|direct\s+messages?|"
                r"inbox\s+on\s+(?:x|twitter)|messages?\s+on\s+(?:x|twitter))"
                r"(?:\s+on\s+(?:x|twitter))?\s*[.!?]*\s*$|"
                r"(?:my\s+)?(?:dms|direct\s+messages)\s*[.!?]*\s*$|"
                r"who\s+(?:has\s+)?(?:dm'?d|dmed|messaged)\s+me\b(?:\s+on\s+(?:x|twitter))?\s*[.!?]*\s*$)", re.I),
     "x.read_dm", lambda m: {"handle": ""},
     "Checking your messages."),
    # READ one conversation, by @name.
    (re.compile(rf"{_LEAD}(?:(?:read|check|open|show\s+me|get\s+me|pull\s+up)\s+(?:me\s+)?(?:my\s+|the\s+)?"
                r"(?:(?:x|twitter)\s+)?(?:dms?|d\.m\.s?|direct\s+messages?|messages?|conversation|chat|inbox)\s+"
                r"(?:with|from)\s+@?(?P<h>(?!(?:x|twitter|the|my|that|this|it|me|him|her|them)\b)[A-Za-z0-9_]{1,15})"
                r"(?:\s+on\s+(?:x|twitter))?\s*[.!?]*\s*$|"
                r"what\s+(?:did|has|have)\s+@?"
                r"(?P<h2>(?!(?:x|twitter|the|my|that|this|it|he|she|they|people|everyone|everybody|anyone)\b)"
                r"[A-Za-z0-9_]{1,15})\s+(?:just\s+)?(?:message|messaged|dm|dm'?d|dmed|send|sent|write|written)"
                r"\s+(?:to\s+)?me\b(?:\s+on\s+(?:x|twitter))?\s*[.!?]*\s*$|"
                r"(?:read|check|open|show\s+me|get\s+me|pull\s+up)\s+(?:me\s+)?@?"
                r"(?P<h3>(?!(?:my|the|that|this|me|your|her|his|our|their|its)\b)[A-Za-z0-9_]{1,15})(?:'s|s'|’s)\s+"
                r"(?:dms?|d\.m\.s?|direct\s+messages?|messages?)(?:\s+on\s+(?:x|twitter))?\s*[.!?]*\s*$)", re.I),
     "x.read_dm", lambda m: {"handle": (m["h"] or m["h2"] or m["h3"] or "").lstrip("@")},
     "Reading your messages."),

    # ── THE PROFILE HEADER (the sentences round, 2026-10-06). AN @HANDLE IS AN
    #    X SIGNAL ON ITS OWN: his "read @mcityXtra_ bio and tell me what's in
    #    the bio" reached fs.read + fs.list ("@mcityXtra_ bio is not there",
    #    twice). A bio, a follower count, "who is @x" or "what does @x's
    #    profile say" is x.read_profile — the HEADER, not the posts (x.read_user
    #    keeps "read ada's profile" and "what has ada been posting"). Every
    #    alternative needs an @ that starts a word (so "ada@example.com" and
    #    "remind me @ 5pm" cannot match) AND one of the profile nouns or
    #    questions, so "what does @echo off do" stays where it was. A NAME
    #    without @ needs an explicit "on X" and is resolved by the tool.
    (re.compile(_PROFILE_LEAD +
                r"(?:(?:read|show(?:\s+me)?|check|get(?:\s+me)?|give\s+me|pull\s+up|open|see|tell\s+me|"
                r"what(?:'s|\s+is|\s+does|\s+do)?(?:\s+in|\s+on)?)\s+(?:me\s+)?(?:the\s+)?)?"
                rf"{_AT_HANDLE.format(g='h')}(?:'s|s'|’s)?(?:\s+(?:x|twitter))?\s+{_PROFILE_NOUN}"
                rf"{_ON_X}(?:\s+(?:say|says|for\s+me|please|pls|abeg))?\s*[.!?]*\s*$|"
                r"\b(?:read|show(?:\s+me)?|check|get(?:\s+me)?|give\s+me|tell\s+me|what(?:'s|\s+is))\s+(?:me\s+)?"
                rf"(?:the\s+)?(?:bio|biography|description)\s+(?:of|for|on)\s+{_AT_HANDLE.format(g='h2')}"
                rf"{_ON_X}\s*[.!?]*\s*$|"
                rf"^\s*(?:tessa[,\s]+)?(?:who(?:'s|\s+is|\s+be)|wetin\s+be|tell\s+me\s+(?:about|who(?:\s+is)?))\s+"
                rf"{_AT_HANDLE.format(g='h3')}{_ON_X}\s*[.!?]*\s*$|"
                rf"\bhow\s+many\s+(?:followers|people)\s+(?:does|do|has|have|did)\s+{_AT_HANDLE.format(g='h4')}"
                r"(?:\s+(?:have|got|get|follow))?\b|"
                rf"\bhow\s+many\s+followers\s+{_AT_HANDLE.format(g='h5')}\s+(?:have|has|get|got)\b|"
                rf"\bhow\s+many\s+people\s+(?:are\s+)?follow(?:s|ing)?\s+{_AT_HANDLE.format(g='h6')}|"
                rf"{_AT_HANDLE.format(g='h7')}(?:'s|s'|’s)?\s+(?:followers?|following)\s+count\b|"
                rf"\bwhat\s+(?:does|do)\s+{_AT_HANDLE.format(g='h8')}(?:'s|s'|’s)\s+(?:x\s+|twitter\s+)?profile\s+say\b|"
                rf"\bwhat(?:'s|\s+is)\s+on\s+{_AT_HANDLE.format(g='h9')}(?:'s|s'|’s)\s+(?:x\s+|twitter\s+)?profile\b|"
                rf"^\s*(?:tessa[,\s]+)?who(?:'s|\s+is)\s+(?P<n1>{_ACCOUNT_NAME})\s+on\s+(?:x|twitter)\s*[?.!]*\s*$|"
                r"\b(?:read|show(?:\s+me)?|check|what(?:'s|\s+is)(?:\s+in)?)\s+(?:me\s+)?"
                rf"(?P<n2>{_ACCOUNT_NAME})(?:'s|s'|’s)\s+(?:x\s+|twitter\s+)?bio\s+on\s+(?:x|twitter)\s*[.!?]*\s*$",
                re.I),
     "x.read_profile",
     lambda m: {"handle": next(v for k, v in m.groupdict().items() if v).strip()},
     "Reading their profile."),

    # ── X READS (the reading round, 2026-09-12): search, thread, profile.
    #    ABOVE the chrome-profile block because its list/show rule is keyed
    #    to the bare word `profile` and would swallow "show me ada's profile";
    #    ABOVE the claims block because `claims.recall` owns the bare "what
    #    are people saying about <topic>" (what SHE noted) and this only takes
    #    the form with an explicit X marker (what X says NOW). Every rule here
    #    needs an X word (x, twitter, tweets, thread, replies) or a possessive
    #    handle, so no file, page, window or chrome verb can reach them:
    #    "search for invoice" stays a disk search; "search x for invoice" is X.
    #    Before this block "find tweets about titan wave" reached x.POST with
    #    the text "about titan wave" — a tweet queued for approval from a
    #    question. The red gate held it; the rule was still wrong.
    (re.compile(r"\bsearch\s+(?:on\s+)?(?:x|twitter)\s+for\s+(?P<q>.+?)\s*$|"
                r"\b(?:search|look)\s+for\s+(?P<q2>.+?)\s+on\s+(?:x|twitter)\s*$|"
                r"\b(?:find|show\s+me|any|are\s+there\s+any|get\s+me)\s+(?:the\s+)?"
                r"(?:tweets|posts\s+on\s+(?:x|twitter))\s+(?:about|on|mentioning)\s+"
                r"(?P<q3>.+?)(?:\s+on\s+(?:x|twitter))?\s*$|"
                r"\b(?:find|show\s+me|any)\s+posts\s+(?:about|mentioning)\s+(?P<q4>.+?)"
                r"\s+on\s+(?:x|twitter)\s*$", re.I),
     "x.search", lambda m: {"query": _clean(m["q"] or m["q2"] or m["q3"] or m["q4"] or "")},
     "Searching X."),
    (re.compile(r"\bwhat\s+(?:are|is)\s+(?:x|twitter|(?:people|everyone|everybody|folks)"
                r"\s+on\s+(?:x|twitter))\s+saying\s+about\s+(?P<q>.+?)\s*$|"
                r"\bwhat\s+(?:are|is)\s+(?:people|everyone|everybody|folks)\s+saying\s+about\s+"
                r"(?P<q2>.+?)\s+on\s+(?:x|twitter)\s*$", re.I),
     "x.search", lambda m: {"query": _clean(m["q"] or m["q2"] or "")},
     "Searching X."),
    # A thread is named by a status id, by the position she just read out
    # ("post two" -> the snapshot of the last read, x_tools._LAST_READ), or
    # not at all ("read the replies" -> the first post of the last read).
    (re.compile(r"\b(?:read|open|show\s+me|pull\s+up|get\s+me)\s+(?:me\s+)?(?:the\s+)?"
                r"(?:thread|replies|conversation)"
                r"(?:\s+(?:on|under|for|to|of|below)\s+(?:the\s+)?(?:post\s+|tweet\s+)?"
                rf"(?P<ref>{_THREAD_REF}))?(?:\s+(?:post|tweet|one))?\s*$|"
                r"\bwhat\s+are\s+the\s+replies(?:\s+(?:to|on|under)\s+(?:the\s+)?"
                rf"(?:post\s+|tweet\s+)?(?P<ref2>{_THREAD_REF}))?\s*$", re.I),
     "x.read_thread", lambda m: _thread_args(m["ref"] or m["ref2"]),
     "Reading the thread."),
    (re.compile(r"\bwhat(?:'s|\s+has|\s+have|\s+is|\s+did)\s+@?"
                r"(?P<h>(?!(?:x|twitter|people|everyone|everybody|the|my|that|this|it|he|she)\b)"
                r"[A-Za-z0-9_]{1,15})\s+(?:been\s+)?(?:posting|tweeting|posted|tweeted|saying(?!\s+about))"
                r"(?:\s+(?:lately|recently|today|this\s+week))?(?:\s+on\s+(?:x|twitter))?\s*$|"
                r"\b(?:read|show\s+me|check|open|pull\s+up|look\s+at)\s+(?:me\s+)?@?"
                r"(?P<h2>(?!(?:my|the|that|this|me|your|her|his|our|their)\b)[A-Za-z0-9_]{1,15})"
                r"(?:'s|s'|’s)\s+(?:x\s+|twitter\s+)?"
                r"(?:profile|posts|tweets|timeline|feed|latest(?:\s+(?:posts|tweets))?)\b"
                r"(?:\s+on\s+(?:x|twitter))?\s*$|"
                r"\b(?:read|show\s+me|check|open|pull\s+up)\s+(?:me\s+)?(?:the\s+)?(?:x\s+|twitter\s+)?"
                r"profile\s+(?:of|for)\s+@?(?P<h3>[A-Za-z0-9_]{1,15})\s*$|"
                r"^(?:read|show\s+me|check|open|pull\s+up)\s+(?:me\s+)?@(?P<h4>[A-Za-z0-9_]{1,15})\s*$",
                re.I),
     "x.read_user",
     lambda m: {"handle": (m["h"] or m["h2"] or m["h3"] or m["h4"] or "").lstrip("@")},
     "Reading their profile."),

    # ── CHROME PROFILES (batch 5). ABOVE the browser rules, because the
    #    domain-open rule below matches "open x.com ..." and would swallow
    #    "open x.com IN YOUR PROFILE" — sending him to a throwaway page instead
    #    of the profile he asked for.
    #
    #    ⚠ THE POSSESSIVE IS THE TIER SIGNAL, and it is how he already speaks.
    #    "open YOUR profile" is hers and green. "open MY <name> profile" is one
    #    of his thirty signed-in Chrome profiles: a different capability, amber,
    #    and it holds. Two rules, never one rule with a branch — the same reason
    #    the capabilities are split.
    (re.compile(r"\b(?:create|make|set\s+up)\b[^.]*\b(?:your|a|her)\b[^.]*"
                r"\b(?:chrome\s+)?profile\b", re.I),
     "system.chrome.create_profile", lambda m: {},
     "Making it."),
    (re.compile(r"\b(?:list|what|which|show)\b[^.]*\b(?:chrome\s+)?profiles?\b", re.I),
     "system.chrome.list_profiles", lambda m: {},
     "Looking."),
    (re.compile(r"\b(?:open|go\s+to|visit|bring\s+up)\s+(?P<url>[\w.-]+\.[a-z]{2,}\S*)\s+"
                r"in\s+(?:your|the|my)\s+(?:\w+\s+)?(?:browser|profile)\b", re.I),
     "system.chrome.open_url", lambda m: {"url": _clean(m["url"])},
     "Opening it."),
    # HIS — amber. Matched BEFORE hers so "open my work profile" cannot fall
    # through to the green rule.
    (re.compile(r"\bopen\s+my\s+(?P<name>[\w -]{2,30}?)\s*(?:chrome\s+)?profile\b", re.I),
     "system.chrome.open_personal_profile", lambda m: {"name": _clean(m["name"])},
     "Hold on."),
    (re.compile(r"\bopen\s+(?:your|her|the\s+tessa)\s*(?P<name>[\w -]{0,20}?)\s*"
                r"(?:chrome\s+)?profile\b", re.I),
     "system.chrome.open_profile",
     lambda m: {"name": _clean(m["name"]) or "default"},
     "Opening it."),
    (re.compile(r"\bam\s+i\s+(?:logged|signed)\s+in(?:to)?\s+(?:to\s+)?(?P<site>[\w.]{1,30})\b"
                r"|\bcheck\s+if\s+i(?:'m|\s+am)\s+(?:logged|signed)\s+in(?:to)?\s+"
                r"(?:to\s+)?(?P<site2>[\w.]{1,30})\b", re.I),
     "system.chrome.login_status",
     lambda m: {"site": _clean(m["site"] or m["site2"])},
     "Checking."),

    # ── BROWSER AND X SIT NEAR THE TOP, and every rule here is keyed to a
    #    distinctive noun — browser, page, site, timeline, X, tweet. That is
    #    what keeps them from colliding with the file verbs below, which are
    #    generic by nature: `fs.read`'s "read <thing>" would happily swallow
    #    "read me this page", and `win.close`'s "close <thing>" would swallow
    #    "close the browser". Specific noun first, generic verb second.

    # ── CLAIMS — what strangers said on X, recalled AS claims and judged
    #    only by him. Keyed to the noun `claim` or to "heard/saying about",
    #    so nothing here collides with a file, window or page verb. Above
    #    `context.forget` so a claim verb is never read as a page verb. There
    #    is deliberately no "forget that claim": `forget that` is the thread-
    #    clear phrase (conversation.py) and is matched before routing.
    #    core/brain/claims.py, core/tools/claims_tools.py.
    (re.compile(r"\b(?:confirm|verify)\s+(?:that|the|this|the\s+last)\s+claim"
                r"(?:\s+about\s+(?P<topic>.+?))?\s*$|"
                r"\b(?:that|the|this)\s+claim(?:\s+about\s+(?P<topic2>.+?))?\s+is\s+"
                r"(?:true|right|correct|real|legit)\s*$", re.I),
     "claims.confirm", lambda m: {"topic": _clean(m["topic"] or m["topic2"] or "")},
     "Marking it."),
    (re.compile(r"\b(?:reject|dismiss|scrap|drop)\s+(?:that|the|this|the\s+last)\s+claim"
                r"(?:\s+about\s+(?P<topic>.+?))?\s*$|"
                r"\b(?:that|the|this)\s+claim(?:\s+about\s+(?P<topic2>.+?))?\s+is\s+"
                r"(?:false|wrong|not\s+true|a\s+lie|rubbish|nonsense|fake)\s*$", re.I),
     "claims.reject", lambda m: {"topic": _clean(m["topic"] or m["topic2"] or "")},
     "Marking it."),
    (re.compile(r"\b(?:what\s+(?:have|did)\s+you\s+(?:hear|heard|see|seen|read)\s+about|"
                r"any\s+claims?\s+about|"
                r"what\s+(?:are|is)\s+(?:people|everyone|everybody)\s+saying\s+about|"
                r"what\s+(?:do|did)\s+people\s+say\s+about)\s+(?P<topic>.+?)\s*$|"
                r"\b(?:list|what\s+are)\s+(?:your|the|my)\s+(?:unverified\s+|noted\s+)?claims\s*$",
                re.I),
     "claims.recall", lambda m: {"topic": _clean(m["topic"] or "")},
     "Checking what I noted."),

    (re.compile(r"\b(?:forget|clear|drop)\b.*\b(?:page|site|article|what you read|"
                r"what you just read)\b", re.I),
     "context.forget", lambda m: {},
     "Forgetting it."),

    (re.compile(r"\bclose\b.*\b(?:the\s+)?browser\b|\bclose\s+chrome\b(?=.*\bbrowser\b)|"
                r"\byou\s+can\s+close\s+(?:the\s+)?browser\b", re.I),
     "browser.close", lambda m: {},
     "Closing it."),

    # ── THE MEDIA ROUND (2026-09-22): share a link, bookmark, save an image
    #    or a video. ABOVE the engagement block ("save that post" is a
    #    bookmark before any other rule sees `save`), ABOVE the repost rule
    #    ("share that post" is the LINK now — X's own Share button copies the
    #    link; a repost is "repost"/"retweet") and ABOVE the file rules
    #    ("remove that bookmark" reached fs.delete as a file called
    #    "bookmark"). Every rule here needs its own noun — link/url/permalink,
    #    bookmark(s), image/picture/photo, video/clip — or `share` with a
    #    post reference; a bare "save the file", "save this", "download it",
    #    "bookmark this page" and "share that file" stay exactly where they
    #    were. A destination ("save that image to the desktop") breaks the
    #    end anchor on purpose: the save folder is fixed, so a sentence that
    #    names another one must not be answered as if it were honoured.
    #    Bulk first, on purpose, for the tool to refuse out loud.
    (re.compile(rf"\b(?:un-?bookmark|unsave)\s+(?P<what>{_BULK}\b[^.]*)", re.I),
     "x.unbookmark", lambda m: {"post_id": _clean(m["what"])},
     "One at a time."),
    (re.compile(rf"\b(?:bookmark|save)\s+(?P<what>{_BULK}\b(?:[^.]*\b(?:post|tweet)s?\b[^.]*|\s+from\s+[^.]*))",
                re.I),
     "x.bookmark", lambda m: {"post_id": _clean(m["what"])},
     "One at a time."),
    (re.compile(rf"\b(?:un-?bookmark|unsave)\s+(?:the\s+|that\s+|this\s+)?(?:post\s+|tweet\s+)?"
                rf"(?P<ref>{_ENGAGE_REF})(?:\s+(?:one|post|tweet))?\s*[.!?]*\s*$|"
                r"\b(?:remove|take\s+off|delete|drop)\s+(?:the\s+|that\s+|this\s+|my\s+)?bookmark"
                r"(?:\s+(?:from|on|off|of)\s+(?:the\s+|that\s+|this\s+)?(?:post\s+|tweet\s+)?"
                rf"(?P<ref2>{_ENGAGE_REF})(?:\s+(?:one|post|tweet))?)?\s*[.!?]*\s*$|"
                r"\b(?:remove|take)\s+(?:the\s+|that\s+|this\s+)?(?:post\s+|tweet\s+)?"
                rf"(?P<ref3>{_ENGAGE_REF})(?:\s+(?:one|post|tweet))?\s+(?:out\s+of|from|off)\s+"
                r"(?:my\s+|the\s+)?bookmarks\s*[.!?]*\s*$", re.I),
     "x.unbookmark", lambda m: _post_args(m["ref"] or m["ref2"] or m["ref3"]),
     "Removing the bookmark."),
    (re.compile(rf"\bbookmark\s+(?:the\s+|that\s+|this\s+)?(?:post\s+|tweet\s+)?"
                rf"(?P<ref>{_ENGAGE_REF})(?:\s+(?:one|post|tweet))?(?:\s+for\s+later)?\s*[.!?]*\s*$|"
                r"\bsave\s+(?:the\s+|that\s+|this\s+)?(?:post|tweet)"
                rf"(?:\s+(?P<ref2>{_ENGAGE_REF}))?(?:\s+for\s+later)?\s*[.!?]*\s*$|"
                r"\badd\s+(?:the\s+|that\s+|this\s+)?(?:post\s+|tweet\s+)?"
                rf"(?P<ref3>{_ENGAGE_REF})(?:\s+(?:one|post|tweet))?\s+to\s+(?:my\s+|the\s+)?bookmarks\s*[.!?]*\s*$",
                re.I),
     "x.bookmark", lambda m: _post_args(m["ref"] or m["ref2"] or m["ref3"]),
     "Bookmarking it."),
    (re.compile(rf"\bshare\s+(?:the\s+|that\s+|this\s+)?(?:post\s+|tweet\s+)?"
                rf"(?P<ref>{_SHARE_REF})(?:\s+(?:one|post|tweet))?(?:\s+with\s+me)?\s*[.!?]*\s*$|"
                r"\b(?:give|get|send|show)\s+me\s+(?:the\s+|that\s+)?(?:link|url|permalink)\s+(?:to|for|of)\s+"
                r"(?:the\s+|that\s+|this\s+)?(?:post\s+|tweet\s+)?"
                rf"(?P<ref2>{_ENGAGE_REF})(?:\s+(?:one|post|tweet))?\s*[.!?]*\s*$|"
                r"\b(?:copy|share|get|give\s+me|send\s+me)\s+(?:the\s+|that\s+|this\s+)?(?:post|tweet)(?:'s|s'|’s)?\s+"
                r"(?:link|url|permalink)\s*[.!?]*\s*$|"
                r"\b(?:copy|share|get)\s+(?:the\s+|that\s+|this\s+)?(?:link|url|permalink)\s+(?:to|for|of)\s+"
                r"(?:the\s+|that\s+|this\s+)?(?:post\s+|tweet\s+)?"
                rf"(?P<ref3>{_ENGAGE_REF})(?:\s+(?:one|post|tweet))?\s*[.!?]*\s*$|"
                r"\bwhat(?:'s|\s+is)\s+(?:the\s+)?(?:link|url|permalink)\s+(?:to|for|of)\s+"
                r"(?:the\s+|that\s+|this\s+)?(?:post\s+|tweet\s+)?"
                rf"(?P<ref4>{_ENGAGE_REF})(?:\s+(?:one|post|tweet))?\s*[.!?]*\s*$|"
                r"^\s*(?:the\s+)?(?:link|url|permalink)\s+(?:to|for|of)\s+(?:the\s+|that\s+|this\s+)?(?:post\s+|tweet\s+)?"
                rf"(?P<ref5>{_ENGAGE_REF})(?:\s+(?:one|post|tweet))?\s*[.!?]*\s*$", re.I),
     "x.share_link", lambda m: _post_args(m["ref"] or m["ref2"] or m["ref3"] or m["ref4"] or m["ref5"]),
     "Here it is."),
    (re.compile(r"\b(?:save|download|grab|keep)\s+(?:me\s+)?(?:the\s+|that\s+|this\s+|those\s+|these\s+)?"
                r"(?:image|picture|photo|pic|images|pictures|photos|pics)"
                r"(?:\s+(?:on|from|in|of|off)\s+(?:the\s+|that\s+|this\s+)?(?:post\s+|tweet\s+)?"
                rf"(?P<ref>{_ENGAGE_REF})(?:\s+(?:one|post|tweet))?)?\s*[.!?]*\s*$", re.I),
     "x.save_image", lambda m: _post_args(m["ref"]),
     "Saving it."),
    (re.compile(r"\b(?:save|download|grab|keep)\s+(?:me\s+)?(?:the\s+|that\s+|this\s+)?"
                r"(?:video|clip|vid|movie|videos|clips)"
                r"(?:\s+(?:on|from|in|of|off)\s+(?:the\s+|that\s+|this\s+)?(?:post\s+|tweet\s+)?"
                rf"(?P<ref>{_ENGAGE_REF})(?:\s+(?:one|post|tweet))?)?\s*[.!?]*\s*$", re.I),
     "x.save_video", lambda m: _post_args(m["ref"]),
     "Saving it."),

    # ── THE ENGAGEMENT ROUND (X features round 2, 2026-09-12): like/unlike
    #    and follow/unfollow, ONE target each. The old like rule fired on any
    #    sentence containing the word `like` — "I'd like the weather" reached
    #    x.like as post 1 and held a public act — and read a spoken status id
    #    as a position. Both are fixed by requiring a POST REFERENCE (ordinal,
    #    demonstrative, or a 5+ digit id) with nothing after it. BULK first:
    #    "like everything from ada", "follow all my followers" are routed to
    #    the tool ON PURPOSE with the words as the target, so the handler's
    #    single-target shape check refuses them out loud and on the chain,
    #    instead of the router quietly picking post 1 or falling to UNROUTED.
    #    `unlike`/`unfollow` sit before `like`/`follow`; `follow` needs a
    #    single @name-shaped word and nothing else, so "follow the link" and
    #    "follow up with ada tomorrow" stay unrouted. ABOVE the notifications
    #    and timeline rules, whose bare-noun match would otherwise turn "like
    #    every post on my timeline" into a READ instead of a refusal.
    (re.compile(rf"\b(?:un-?like|unfavou?rite)\s+(?P<what>{_BULK}\b[^.]*)", re.I),
     "x.unlike", lambda m: {"post_id": _clean(m["what"])},
     "One at a time."),
    (re.compile(rf"\b(?:like|favou?rite)\s+(?P<what>{_BULK}\b[^.]*)", re.I),
     "x.like", lambda m: {"post_id": _clean(m["what"])},
     "One at a time."),
    (re.compile(rf"\bunfollow\s+(?P<what>(?:back\s+)?(?:{_BULK}|(?:all\s+)?(?:of\s+)?my\s+followers|"
                r"(?:the\s+)?(?:people|accounts)\s+who)\b[^.]*)", re.I),
     "x.unfollow", lambda m: {"handle": _clean(m["what"])},
     "One at a time."),
    (re.compile(rf"\bfollow\s+(?P<what>(?:back\s+)?(?:{_BULK}|(?:all\s+)?(?:of\s+)?my\s+followers|"
                r"(?:the\s+)?(?:people|accounts)\s+who)\b[^.]*)", re.I),
     "x.follow", lambda m: {"handle": _clean(m["what"])},
     "One at a time."),
    # LIKE BY AUTHOR (the sentences round, 2026-10-06): "like the newest post
    # from @premierleague", "like @x's latest post", "like their first 3 post"
    # (his U2). Needs an author or a possessive, so "like the first post"
    # (a post she read) stays with the ordinal rule below. The tool reads their
    # profile ONCE and holds on ONE status id; a count becomes a plan in
    # intents.py — one hold now, the rest named.
    (re.compile(_ASK_LEAD + r"(?:like|favou?rite)\s+(?:(?P<poss>their|his|her|its)\s+|"
                r"(?P<at>@[A-Za-z0-9_]{1,15})(?:'s|s'|’s)\s+|the\s+)?"
                r"(?:(?P<ord>second|third|fourth|fifth|2nd|3rd|4th|5th)\s+)?"
                r"(?:newest|latest|last|most\s+recent|recent|new|first|top)"
                r"(?:\s+(?P<n>\d|one|two|three|four|five))?\s+(?:post|tweet)s?"
                rf"(?:\s+(?:from|by|of|on)\s+(?P<who>@[A-Za-z0-9_]{{1,15}}|{_ACCOUNT_NAME}))?"
                rf"{_ON_X}{_ASK_TAIL}", re.I),
     "x.like", _like_by_author,
     "Finding it."),
    (re.compile(rf"\b(?:un-?like|unfavou?rite)\s+(?:the\s+|that\s+|this\s+)?(?:post\s+|tweet\s+)?"
                rf"(?P<ref>{_ENGAGE_REF})(?:\s+(?:one|post|tweet))?\s*[.!?]*\s*$|"
                r"\b(?:take|remove)\s+(?:my\s+|the\s+)?like\s+(?:off|from)\s+"
                r"(?:the\s+|that\s+|this\s+)?(?:post\s+|tweet\s+)?"
                rf"(?P<ref2>{_ENGAGE_REF})(?:\s+(?:one|post|tweet))?\s*[.!?]*\s*$", re.I),
     "x.unlike", lambda m: _post_args(m["ref"] or m["ref2"]),
     "Unliking it."),
    # A BARE "like this" is not a like (the sentences round, 2026-10-06): it is
    # "make it like this" as often as anything, and a public act needs a post
    # named — "like this one", "like this post", "like that", "like it" stay.
    (re.compile(rf"\b(?:like|favou?rite)\s+(?!this\s*[.!?]*\s*$)(?:the\s+|that\s+|this\s+)?(?:post\s+|tweet\s+)?"
                rf"(?P<ref>{_ENGAGE_REF})(?:\s+(?:one|post|tweet))?\s*[.!?]*\s*$", re.I),
     "x.like", lambda m: _post_args(m["ref"]),
     "Liking it."),
    (re.compile(r"^\s*(?:please\s+)?(?:(?:can|could|would)\s+you\s+(?:please\s+)?)?unfollow\s+@?"
                r"(?P<h>(?!(?:the|a|an|me|it|that|this|them|him|her|up|along|on|back|my|your|our|"
                r"their|his|its|x|twitter)\b)[A-Za-z0-9_]{1,15})"
                r"(?:\s+on\s+(?:x|twitter))?(?:\s+please)?\s*[.!?]*\s*$", re.I),
     "x.unfollow", lambda m: {"handle": m["h"]},
     "Unfollowing them."),
    (re.compile(r"^\s*(?:please\s+)?(?:(?:can|could|would)\s+you\s+(?:please\s+)?)?follow\s+@?"
                r"(?P<h>(?!(?:the|a|an|me|it|that|this|them|him|her|up|along|on|back|my|your|our|"
                r"their|his|its|x|twitter)\b)[A-Za-z0-9_]{1,15})"
                r"(?:\s+on\s+(?:x|twitter))?(?:\s+please)?\s*[.!?]*\s*$", re.I),
     "x.follow", lambda m: {"handle": m["h"]},
     "Following them."),
    # FOLLOW / UNFOLLOW BY NAME (the sentences round, 2026-10-06): "follow
    # premier league on X", "Follow Elon musk on X" (his U2, U3). A NAME is
    # more than one word, so it needs an explicit X marker ("on X", "for X",
    # "on twitter") — "follow up with ada tomorrow" and "follow the readme"
    # never reach X. The tool resolves the name through X's people search and
    # the hold names "@handle (Name, N followers)" before his yes. Unfollow first.
    (re.compile(_ASK_LEAD + rf"unfollow\s+(?P<name>{_ACCOUNT_NAME})\s+(?:on|for|in)\s+(?:x|twitter){_ASK_TAIL}",
                re.I),
     "x.unfollow", lambda m: {"handle": _clean(m["name"])},
     "Finding them."),
    (re.compile(_ASK_LEAD + rf"follow\s+(?P<name>{_ACCOUNT_NAME})\s+(?:on|for|in)\s+(?:x|twitter){_ASK_TAIL}",
                re.I),
     "x.follow", lambda m: {"handle": _clean(m["name"])},
     "Finding them."),

    # ── THE PUBLISHING ROUND 3 (2026-09-12): quote, thread, repost-by-id.
    #    ABOVE the timeline rule ("retweet every post on my timeline" must
    #    reach the repost tool to be REFUSED as bulk, not be read as a
    #    timeline read) and ABOVE x.reply / x.post: "quote that tweet with
    #    well said" reached x.post as the text "with well said", and "tweet
    #    a thread about X" reached x.post as "a thread about X" — a card each,
    #    for the wrong thing. Every rule here needs its own noun (quote,
    #    thread, repost/retweet) AND a post reference, a separator or a topic,
    #    so a quote he asks to be READ ("what's the quote for the naira") and
    #    a thread he asks to be read ("read the thread on post two", matched
    #    further up) never reach a red tool. Bulk first, on purpose: the tool
    #    refuses it by shape, out loud and on the chain.
    (re.compile(rf"\b(?:quote|quote[- ]?(?:post|tweet)|qt)\s+(?P<what>{_BULK}\b[^.]*)", re.I),
     "x.quote", lambda m: {"quoted_id": _clean(m["what"]), "text": ""},
     "One at a time."),
    (re.compile(rf"\b(?:quote|quote[- ]?(?:post|tweet)|qt)\s+(?:the\s+|that\s+|this\s+)?"
                rf"(?:post\s+|tweet\s+)?(?P<ref>{_ENGAGE_REF})(?:\s+(?:one|post|tweet))?\s*"
                r"(?:with|saying|and\s+say|:)\s*[:,]?\s*(?P<text>.+)$", re.I),
     "x.quote", lambda m: {**_post_args(m["ref"]), "text": _clean(m["text"] or "")},
     "Reading it back."),
    # A thread with its parts given: "post a thread: a; b; c", "tweet a thread
    # about X: a; b". The tool cuts it (`;`, `|`, newline, ` / `, `1/ 2/`).
    (re.compile(r"\b(?:post|tweet|send|publish|write|put\s+up)\s+(?:me\s+)?(?:a\s+|the\s+|this\s+)?"
                r"thread(?:\s+(?:about|on)\s+(?P<topic>[^:;|]+?))?\s*"
                r"(?::|\s+(?:saying|with|of)\s*:?)\s*(?P<parts>.+)$", re.I),
     "x.thread", lambda m: {"posts": _clean(m["parts"] or ""), "topic": _clean(m["topic"] or "")},
     "Reading it back."),
    # A thread drafted about a topic, in his voice: "tweet a thread about X",
    # "write a three post thread about X". No separator, or it is the rule above.
    (re.compile(r"\b(?:post|tweet|send|publish|write|put\s+up)\s+(?:me\s+)?(?:a\s+|the\s+)?"
                r"(?:(?P<n>\d{1,2}|two|three|four|five|six|seven|eight|nine|ten)[- ]?"
                r"(?:post|part|tweet)\s+)?thread\s+(?:about|on)\s+(?P<topic>[^:;|]+?)\s*[.!?]*\s*$",
                re.I),
     "x.thread", lambda m: {"topic": _clean(m["topic"] or ""), "n": _count(m["n"])},
     "Drafting it."),
    # A bare "post a thread" reaches the tool with nothing, so the refusal
    # says what to give her — it used to reach x.post as the text "a thread".
    (re.compile(r"\b(?:post|tweet|send|publish|write)\s+(?:me\s+)?(?:a\s+|the\s+)?thread\s*[.!?]*\s*$",
                re.I),
     "x.thread", lambda m: {"posts": "", "topic": ""},
     "Reading it back."),
    # Repost / unrepost, on x.like's shape: BULK first (refused by the tool),
    # unrepost before repost, ONE post reference and nothing after it.
    # `share` is the post's LINK now (x.share_link, the media round above),
    # never a repost — "share it" and "share that file" still reach nothing.
    (re.compile(rf"\b(?:un-?repost|un-?retweet)\s+(?P<what>{_BULK}\b[^.]*)", re.I),
     "x.unrepost", lambda m: {"post_id": _clean(m["what"])},
     "One at a time."),
    (re.compile(rf"\b(?:repost|retweet)\s+(?P<what>{_BULK}\b[^.]*)", re.I),
     "x.repost", lambda m: {"post_id": _clean(m["what"])},
     "One at a time."),
    (re.compile(rf"\b(?:un-?repost|un-?retweet|(?:undo|take\s+back)\s+(?:(?P<dem>that|this)\s+|the\s+|my\s+)?"
                rf"(?:repost|retweet))(?:\s+(?:of|on))?(?:\s+(?:the|that|this))?(?:\s+(?:post|tweet))?"
                rf"(?:\s+(?P<ref>{_ENGAGE_REF})(?:\s+(?:one|post|tweet))?)?\s*[.!?]*\s*$", re.I),
     "x.unrepost", lambda m: _post_args(m["ref"] or (m["dem"] and "that") or _need(m)),
     "Undoing it."),
    (re.compile(rf"\b(?:repost|retweet)\s+(?:the\s+|that\s+|this\s+)?(?:post\s+|tweet\s+)?"
                rf"(?P<ref>{_ENGAGE_REF})(?:\s+(?:one|post|tweet))?\s*[.!?]*\s*$", re.I),
     "x.repost", lambda m: _post_args(m["ref"]),
     "Reposting it."),
    # X BEFORE the generic browser rules: "read my timeline" is an X read, not
    # a page read, and "tweet that" must never reach a file verb.
    # THE SENTENCES ROUND (2026-10-06): his U1, "open X in chrome for me to
    # log in", reached x.login only through the model's second opinion (a
    # fuzzy "chrome" app match was doubted) — with Gemini down it would have
    # opened his everyday Chrome instead. The open-to-log-in shape is now
    # matched here: open X (in a browser) (for me) to / so I can log in.
    (re.compile(r"\b(?:log\s*(?:me\s*)?in(?:to)?\s+(?:to\s+)?(?:x|twitter)|"
                r"open\s+(?:x|twitter)\s+so\s+i\s+can\s+log|sign\s+me\s+in(?:to)?\s+(?:to\s+)?(?:x|twitter)|"
                r"open\s+(?:up\s+)?(?:x|twitter)(?:\.com)?(?:\s+(?:in|on|with)\s+(?:chrome|the\s+browser|my\s+browser|"
                r"your\s+browser|browser))?(?:\s+for\s+me)?\s+(?:to|so\s+(?:that\s+)?i\s+(?:can|could|fit)|"
                r"make\s+i|for\s+me\s+to)\s+(?:log|sign)\s*-?\s*in)\b", re.I),
     "x.login", lambda m: {},
     "Opening it."),
    (re.compile(r"\b(?:my\s+)?(?:x\s+)?notifications\b|\bwho\s+replied\s+to\s+me\b", re.I),
     "x.read_notifications", lambda m: {},
     "Checking."),
    (re.compile(r"\b(?:read\s+(?:me\s+)?(?:my\s+)?timeline|my\s+timeline|"
                rf"{_WHATS}\s+(?:on|happening\s+on)\s+(?:x|twitter))\b", re.I),
     "x.read_timeline", lambda m: {},
     "Reading it."),
    (re.compile(_ASK_LEAD + r"(?:reply|respond)\s+(?:with\s+|saying\s+)?"
                r"(?:'(?P<t1>[^']{1,280})'|\"(?P<t2>[^\"]{1,280})\"|“(?P<t3>[^”]{1,280})”|‘(?P<t4>[^’]{1,280})’)"
                r"\s+to\s+" + _REPLY_TARGET + _ON_X + _ASK_TAIL, re.I),
     "x.reply", _reply_by_author,
     "Finding it."),
    (re.compile(_ASK_LEAD + r"(?:reply|respond)\s+to\s+" + _REPLY_TARGET
                + r"\s+(?:saying|with)\s+(?P<t5>\S.{0,279}?)" + _ASK_TAIL, re.I),
     "x.reply", _reply_by_author,
     "Finding it."),
    # REPLY BEFORE POST, and POST vetoes `reply`. "reply to post two with
    # thanks" contains the word `post` and was matching `x.post` with the text
    # "two with thanks" — which would have queued a public tweet reading "two
    # with thanks" for approval. Both halves of the fix are needed: ordering
    # alone leaves the collision live for any phrasing reply misses.
    (re.compile(r"\breply\b\s+(?:to\s+)?(?:the\s+|that\s+|post\s+)?"
                r"(?P<idx>\d+|first|second|third|one|two|three)?\s*(?:with|saying)?\s*"
                r"(?P<text>.*)$", re.I),
     "x.reply", lambda m: {"index": _ordinal(m["idx"]), "text": _clean(m["text"] or "")},
     "Reading it back."),
    # PLURAL AND COMMA TOLERANT. His real transcript was
    # "Tessa, Tweets, Data Mbudinon AI Assist" — Whisper wrote the verb as a
    # plural noun and put a comma where he paused. The old rule required
    # `tweet` followed by whitespace, matched none of it, and the utterance
    # fell through to UNROUTED: he got "I caught that. Not yet." instead of
    # the approval gate. He has still never SEEN the gate work, and this rule
    # is why.
    # `tweet` IS UNAMBIGUOUS; `post` IS NOT. Split into two rules because one
    # rule covering both matched "post office opening times" and queued a
    # tweet reading "office opening times" for approval. Nothing would have
    # published — the red gate holds — but a false positive he has to refuse
    # is still a tool he stops trusting.
    #
    # So: any form of `tweet` is a tweet. `post` needs a companion signal —
    # "to x", "to twitter", or a demonstrative ("post this", "post that").
    # POST WITH THE WORDS IN QUOTES (the sentences round, 2026-10-06): "post
    # 'thanks @premierleague for the update'" is dictation — the quotes say
    # where the words start and stop — so it is x.post (red, the card), never
    # a profile read because an @name sits inside the words. Needs the quote
    # marks: "post office opening times" stays where it was.
    (re.compile(r"^\s*(?:tessa[,\s]+)?(?:please\s+)?(?:post|put\s+up|publish)\s*(?:this\s*)?(?:on\s+(?:x|twitter)\s*)?"
                r"[:,]?\s*[\"'“‘](?P<text>[^\"“”]{1,300}?)[\"'”’]\s*(?:(?:on|to)\s+(?:x|twitter))?\s*[.!?]*\s*$", re.I),
     "x.post", lambda m: {"text": _clean(m["text"] or "")},
     "Reading it back."),
    (re.compile(r"(?!.*\breply\b)\btweets?\b\s*(?:this|that|the following)?\s*"
                r"(?:to\s+(?:x|twitter))?\s*[:,]?\s*(?P<text>.+)$", re.I),
     "x.post", lambda m: {"text": _clean(m["text"] or "")},
     "Reading it back."),
    (re.compile(r"(?!.*\breply\b)\bposts?\b\s*"
                r"(?:(?:this|that|the following)\s*(?:to\s+(?:x|twitter))?|to\s+(?:x|twitter))"
                r"\s*[:,]?\s*(?P<text>.+)$", re.I),
     "x.post", lambda m: {"text": _clean(m["text"] or "")},
     "Reading it back."),

    # THE DOMAIN-OPEN RULE SITS ABOVE THE SEARCH RULES, and that ordering is a
    # bug fix rather than a preference. His "Zoi, OpenGoogle.com" repaired to
    # "Open Google.com" and then matched the SEARCH rule on the bare word
    # "google", so she searched the web for ".com" instead of opening the site
    # he named. "Open a named host" is more specific than "search for words" and
    # must be tried first.
    #
    # The URL rule in intents.py needs a scheme or a www, which Whisper rarely
    # produces from speech — this one takes a bare host.
    (re.compile(r"\b(?:open|go\s+to|bring\s+up|visit)\s+(?P<host>[\w-]+(?:\.[\w-]+)*\.(?:com|org|net|io|dev|co|ai|uk|ng|gov|edu)\S*)", re.I),
     "browser.open_url", lambda m: {"url": _clean(m["host"])},
     "Opening it."),

    # WEB SEARCH needs an explicit web marker. Bare "search for invoice" stays
    # with `fs.search` — he is far likelier to mean his own disk, and guessing
    # wrong sends a private filename to a search engine.
    # "GOOGLE" IS ALSO HALF AN APPLICATION NAME, and that collision is live:
    # "open Google Chrome" — the exact name on the shortcut — matched this rule
    # and ran a WEB SEARCH for "chrome" instead of opening his browser.
    #
    # The lookaheads exclude the product names only. "google the weather" and
    # "google chrome extensions for React" both still search, because the second
    # one has more after the product name than the bare noun.
    (re.compile(r"\b(?:google(?!\s+(?:chrome|drive|docs|meet|maps|photos|play)\b)|"
                r"search\s+(?:the\s+)?(?:web|online|internet)\s+for|"
                r"look\s+up|search\s+for\s+(?P<q2>.+?)\s+(?:online|on\s+the\s+web))\b\s*(?P<q>.*)$", re.I),
     "browser.search", lambda m: {"query": _clean(m["q"] or m["q2"] or "")},
     "Searching."),

    # ══ INPUT AND SCREEN ═════════════════════════════════════════════════════
    #
    # ⚠ PLACED ABOVE THE BROWSER BLOCK, AND NARROWED SO IT STEALS NOTHING.
    #
    # The browser rules below are the collision surface and they are wide:
    # `click|press|tap <anything>` is browser.click, and `screenshot` anywhere
    # in a sentence is browser.screenshot. Three of this round's phrases sit
    # right on top of them — "press enter", "click", "take a screenshot" — so
    # each rule here is anchored to a CLOSED SET (key names, bare click forms,
    # explicit screen words) rather than to a verb. "Press the login button"
    # and "screenshot the page" still reach the browser; the proof diffs all
    # 269 recorded phrases to show it.

    # ── KEYS. `press` + a name from the KEY TABLE, or a bare combination.
    #    Anchored with ^…$ and restricted to keys `winapi/inputs.py::VK`
    #    actually has, so "press the send button" cannot match and be sent to a
    #    keyboard that would refuse it anyway.
    (re.compile(rf"^(?:please\s+)?(?:press|hit|send|push)\s+(?:the\s+)?"
                rf"(?P<keys>{_KEYCOMBO})\s*(?:key)?$"
                rf"|^(?P<bare>(?:{_MODWORD})(?:\s*[+\-]\s*|\s+)(?:{_KEYNAME}))$", re.I),
     "system.input.key", lambda m: {"keys": _keys(m["keys"] or m["bare"])},
     "Pressing it."),

    # ── TYPE. Only the form with NO destination — "type X into the search box"
    #    is a browser act and stays one, so the negative lookahead hands it on.
    (re.compile(r"^(?:please\s+)?(?:type|write|enter)\s+(?P<text>\S.*?)\s*$", re.I),
     "system.input.type", lambda m: {"text": _typed(m["text"])},
     "Typing it."),

    # ── MOUSE. BARE click forms and the coordinate form only. `browser.click`
    #    needs a name after the verb, so "click" alone never reached it.
    (re.compile(r"^(?:please\s+)?(?:(?P<dbl>double[\s-]?)|(?P<rgt>right[\s-]?))?click"
                r"(?:\s+(?:at|on)\s+(?P<x>\d{1,5})\s*(?:,|\s)\s*(?P<y>\d{1,5}))?\s*$", re.I),
     "system.input.mouse",
     lambda m: {"action": ("double_click" if m["dbl"] else
                           "right_click" if m["rgt"] else "click"),
                **({"x": int(m["x"]), "y": int(m["y"])} if m["x"] else {})},
     "Clicking."),
    (re.compile(r"\bmove\s+(?:the\s+)?(?:mouse|cursor|pointer)\s+to\s+"
                r"(?P<x>\d{1,5})\s*(?:,|\s)\s*(?P<y>\d{1,5})\b", re.I),
     "system.input.mouse",
     lambda m: {"action": "move", "x": int(m["x"]), "y": int(m["y"])},
     "Moving it."),

    # ── SCROLL. Green, and the only input act that is.
    (re.compile(r"\bscroll\s+(?:to\s+the\s+)?(?P<dir>up|down|top|bottom)\b"
                r"(?:\s+a\s+(?P<amt>bit|little|lot))?", re.I),
     "system.input.scroll", lambda m: {"amount": _scroll_amount(m["dir"], m["amt"])},
     "Scrolling."),

    # ── SCREENSHOT. ⚠ A DELIBERATE RETARGET, and the one behaviour change this
    #    round makes to an existing phrase. A bare "take a screenshot" used to
    #    photograph the BROWSER PAGE — which is the wrong answer whenever a
    #    browser is not what he is looking at, and he has no way to tell which
    #    he got. Bare and screen-worded forms now mean the SCREEN; "screenshot
    #    the page/site/tab/browser" still means the browser, vetoed across by
    #    the same ValueError mechanism `_no_stranded_set` uses.
    (re.compile(r"\b(?:take|grab|capture|get)?\s*(?:a\s+|the\s+)?screen\s?shot\b"
                r"(?P<tail>.*)$", re.I),
     "system.screen.capture", lambda m: _shot_target(m),
     "Taking it."),

    # ── READ THE SCREEN. ⚠ Its output is FENCED as untrusted (screen.py).
    (re.compile(rf"{_WHATS}\s+on\s+(?:my|the)\s+screen\b"
                r"|\bread\s+(?:my|the)\s+screen\b"
                r"|\bwhat\s+does\s+(?:this|that)\s+window\s+say\b"
                r"|\bwhat\s+am\s+i\s+looking\s+at\b", re.I),
     "system.screen.read", lambda m: {},
     "Looking."),

    # ── RECORD. Amber; the hold names the duration.
    (re.compile(r"\b(?:start\s+)?record(?:ing)?\s+(?:my|the)\s+screen\b"
                r"(?:\s+for\s+(?P<secs>\d{1,3})\s*(?:seconds?|secs?|s)?)?"
                r"|\bscreen\s+record(?:ing)?\b(?:\s+for\s+(?P<s2>\d{1,3}))?", re.I),
     "system.screen.record",
     lambda m: {"seconds": int(m["secs"] or m["s2"] or 10)},
     "Recording."),

    # ── PIXEL.
    (re.compile(r"\b(?:what\s+)?colou?r\b[^.]*\bpixel\b[^.]*?(?P<x>\d{1,5})\s*(?:,|\s)\s*(?P<y>\d{1,5})"
                r"|\bpixel\s+(?:at\s+)?(?P<x2>\d{1,5})\s*(?:,|\s)\s*(?P<y2>\d{1,5})", re.I),
     "system.screen.pixel",
     lambda m: {"x": int(m["x"] or m["x2"]), "y": int(m["y"] or m["y2"])},
     "Looking."),

    # ── FIND AN IMAGE. Requires an explicit image FILE and the words "on
    #    screen" — without both, "find the report" is a file search and must
    #    stay one.
    (re.compile(r"\bfind\s+(?P<p1>\S+\.(?:png|bmp))\s+on\s+(?:my\s+|the\s+)?screen\b"
                r"|\bwhere\s+is\s+(?P<p2>\S+\.(?:png|bmp))\s+on\s+(?:my\s+|the\s+)?screen\b"
                r"|\blocate\s+(?P<p3>\S+\.(?:png|bmp))\s+on\s+(?:my\s+|the\s+)?screen\b", re.I),
     "system.screen.find",
     lambda m: {"path": (m["p1"] or m["p2"] or m["p3"])},
     "Looking for it."),

    (re.compile(r"\b(?:read|what\s+does)\b.*\b(?:this|that|the)\s+(?:page|site|article|website)\b", re.I),
     "browser.read_page", lambda m: {},
     "Reading it."),
    (re.compile(r"\b(?:take\s+a\s+)?screenshot\b|\bgrab\s+a\s+picture\s+of\b", re.I),
     "browser.screenshot", lambda m: {},
     "Taking it."),
    (re.compile(r"\b(?:click|press|tap)\s+(?:the\s+|on\s+)?(?P<name>.+?)(?:\s+button)?$", re.I),
     "browser.click", lambda m: {"name": _clean(m["name"])},
     "Clicking it."),
    (re.compile(r"\btype\s+(?P<text>.+?)\s+in(?:to)?\s+(?:the\s+)?(?P<field>.+?)(?:\s+(?:field|box))?$", re.I),
     "browser.type", lambda m: {"field": _clean(m["field"]), "text": _clean(m["text"])},
     "Typing it."),
    (re.compile(r"\bsubmit\b.*\bform\b|\bsend\s+(?:that\s+|the\s+)?form\b", re.I),
     "browser.submit", lambda m: {},
     "Reading it back."),

    # ── THE READ-ONLY OBSERVERS (batch 2) SIT ABOVE PROCESSES AND FILES.
    #
    #    Their position is forced by two generic rules further down that would
    #    otherwise swallow every one of them:
    #
    #      * `proc.list` matches "what's running", so "what's running at
    #        STARTUP" would have listed processes.
    #      * `fs.list` matches "list <thing>" and "what's in <thing>", so
    #        "list my services", "list scheduled tasks", "list startup items"
    #        and "what's in the event log" would each have gone looking for a
    #        FOLDER of that name and failed on a path that does not exist.
    #
    #    Each rule below is keyed to a distinctive noun — installed, startup,
    #    service, scheduled task, monitor, event log — so none of them can
    #    shadow the generic verbs, while the generic verbs would shadow all of
    #    them. Same reasoning as the system-query block, one layer earlier.
    #
    #    Everything these return is UNTRUSTED and fenced; see
    #    core/system/untrusted.py.

    (re.compile(r"\b(?:event\s*log|event\s+viewer)\b"
                r"|\brecent\s+(?:system|application|setup)\s+events\b"
                r"|\bcheck\s+the\s+(?:system|application|setup)\s+log\b", re.I),
     "system.eventlog.tail", lambda m: _eventlog_args(m),
     "Reading it."),

    # ── REGISTRY AND SERVICES (machine-control round, 2026-09-12) ────────────
    #
    #    RED, by EXACT target, FROZEN on the card. THEY SIT ABOVE
    #    `system.startup.list` ON PURPOSE: "disable the print spooler service
    #    at startup" carries the word `startup`, and that observer would
    #    otherwise answer a question he did not ask instead of raising the
    #    card. Every rule here REQUIRES the noun `service` — or, for the
    #    registry, a hive-prefixed key — so "stop chrome" stays `proc.kill`,
    #    "what runs at startup" stays the observer, and neither can be traded
    #    for the other by a mistranscription.
    #
    #    START AND STOP ARE SEPARATE RULES WITH SEPARATE TOOL NAMES. They are
    #    opposite acts on the same service: one rule with a captured verb would
    #    put the difference between "start Spooler" and "stop Spooler" inside
    #    an argument, where a frozen card cannot see it. The tool name IS the
    #    act, and the card names it.
    #
    #    Each control rule is followed by the SAME regex routed to the green
    #    observer: when the spoken name does not resolve to ONE service the
    #    builder raises and she reads the candidates back, instead of failing.

    # set registry value NAME under KEY to VALUE
    (re.compile(rf"{_LEAD}set\s+(?:the\s+)?registry\s+value\s+(?P<name>[\w.\-]{{1,64}})\s+"
                rf"(?:under|in|on)\s+{_REG_KEY}\s+to\s+{_REG_VALUE}\s*[.!?]*\s*$", re.I),
     "system.registry.set", lambda m: _registry_args(m["key"], m["name"], m["value"]),
     "Reading the key."),
    # set registry KEY\NAME to VALUE  — the last part of the path is the value
    (re.compile(rf"{_LEAD}set\s+(?:the\s+)?registry\s+(?:key\s+|value\s+|entry\s+|setting\s+)?"
                rf"{_REG_KEY}\s+to\s+{_REG_VALUE}\s*[.!?]*\s*$", re.I),
     "system.registry.set", lambda m: _registry_args(m["key"], None, m["value"]),
     "Reading the key."),

    # START — its own rule, its own tool name.
    (re.compile(rf"{_LEAD}start\s+(?:up\s+)?(?:the\s+)?{_SVC_NAME}\s+service"
                rf"(?:\s+(?:back\s+)?up)?{_SVC_TAIL}", re.I),
     "system.service.start", lambda m: _resolve_service(m["name"]),
     "Looking it up."),
    (re.compile(rf"{_LEAD}start\s+(?:up\s+)?(?:the\s+)?{_SVC_NAME}\s+service"
                rf"(?:\s+(?:back\s+)?up)?{_SVC_TAIL}", re.I),
     "system.services.list", lambda m: {"name": _service_query(m["name"])},
     "Looking."),

    # STOP — its own rule, its own tool name.
    (re.compile(rf"{_LEAD}stop\s+(?:the\s+)?{_SVC_NAME}\s+service{_SVC_TAIL}", re.I),
     "system.service.stop", lambda m: _resolve_service(m["name"]),
     "Looking it up."),
    (re.compile(rf"{_LEAD}stop\s+(?:the\s+)?{_SVC_NAME}\s+service{_SVC_TAIL}", re.I),
     "system.services.list", lambda m: {"name": _service_query(m["name"])},
     "Looking."),

    # SET STARTUP — how it starts, not whether it is running now.
    (re.compile(rf"{_LEAD}(?:set|make|change|put)\s+(?:the\s+)?{_SVC_NAME}\s+service\s+"
                rf"(?:to\s+|so\s+(?:it|that\s+it)\s+)?(?:start(?:s|ing)?\s+)?"
                rf"(?:up\s+)?(?:on\s+)?{_MODE_RE}"
                rf"(?:\s+at\s+(?:start\s?up|boot|log\s?in|log\s?on))?{_SVC_TAIL}", re.I),
     "system.service.set_startup", _service_startup_args,
     "Looking it up."),
    (re.compile(rf"{_LEAD}(?:disable|turn\s+off)\s+(?:the\s+)?{_SVC_NAME}\s+service"
                rf"(?:\s+at\s+(?:start\s?up|boot|log\s?in|log\s?on))?"
                rf"(?:\s+(?:entirely|completely|for\s+good))?{_SVC_TAIL}", re.I),
     "system.service.set_startup",
     lambda m: dict(_resolve_service(m["name"]), mode="disabled"),
     "Looking it up."),
    (re.compile(rf"{_LEAD}(?:set|make|change|put|disable|turn\s+off)\s+(?:the\s+)?{_SVC_NAME}"
                rf"\s+service\b[^.]*{_SVC_TAIL}", re.I),
     "system.services.list", lambda m: {"name": _service_query(m["name"])},
     "Looking."),

    # STARTUP BEFORE PROCESSES — "what's running at startup" is not a process list.
    (re.compile(r"\bstart\s?up\b|\bstarts?\s+with\s+windows\b"
                r"|\brun(?:s|ning)?\s+at\s+(?:start\s?up|login|log\s?on|boot)\b", re.I),
     "system.startup.list", lambda m: {},
     "Looking."),

    (re.compile(r"\b(?:is|are|do\s+i\s+have)\s+(?P<name>[\w.+\-' ]{2,40}?)\s+installed\b"
                r"|\b(?:what|which|list)\b[^.]*\b(?:programs?|software|applications?|apps)\b"
                r"[^.]*\binstalled\b"
                r"|\binstalled\s+(?:programs?|software|applications?|apps)\b"
                r"|\bwhat\s+have\s+i\s+got\s+installed\b", re.I),
     "system.software.list", lambda m: {"name": _clean(m["name"] or "")},
     "Looking."),

    # ── INSTALL / UNINSTALL (software-change round, 2026-09-12). RED, winget
    #    CATALOG ONLY, BY EXACT ID, FROZEN on the card — see
    #    core/system/abilities/software_change.py. Two rules per verb with the
    #    SAME regex: the first resolves the name to ONE catalog id (raises ->
    #    falls through), the second is the green search that reads the
    #    candidates back. ANCHORED to the clause start (`_LEAD`), so "pip
    #    install requests" / "npm install express" never reach them, and
    #    `install\s` cannot match "installed", so the observer above keeps "is
    #    chrome installed". "remove X" is an uninstall ONLY with an app noun
    #    after it; "remove the old folder" stays fs.delete further down.
    (re.compile(rf"{_LEAD}(?:download\s+and\s+)?install\s+(?:the\s+)?(?P<what>[\w.+\-' ]{{2,48}}?)"
                rf"(?:\s+(?:app|application|program|package))?(?:\s+for\s+me)?(?:\s+please)?\s*[.!?]*\s*$", re.I),
     "system.software.install", lambda m: _resolve_software(m["what"], "catalog"),
     "Looking it up."),
    (re.compile(rf"{_LEAD}(?:download\s+and\s+)?install\s+(?:the\s+)?(?P<what>[\w.+\-' ]{{2,48}}?)"
                rf"(?:\s+(?:app|application|program|package))?(?:\s+for\s+me)?(?:\s+please)?\s*[.!?]*\s*$", re.I),
     "system.software.search", lambda m: {"name": _software_query(m["what"]), "scope": "catalog"},
     "Looking."),
    (re.compile(rf"{_LEAD}(?:uninstall\s+(?:the\s+)?(?P<what>[\w.+\-' ]{{2,48}}?)"
                rf"(?:\s+(?:app|application|program|package|software))?"
                rf"|remove\s+(?:the\s+)?(?P<what2>[\w.+\-' ]{{2,48}}?)\s+(?:app|application|program|package|software))"
                rf"(?:\s+for\s+me)?(?:\s+please)?\s*[.!?]*\s*$", re.I),
     "system.software.uninstall", lambda m: _resolve_software(m["what"] or m["what2"], "installed"),
     "Looking it up."),
    (re.compile(rf"{_LEAD}(?:uninstall\s+(?:the\s+)?(?P<what>[\w.+\-' ]{{2,48}}?)"
                rf"(?:\s+(?:app|application|program|package|software))?"
                rf"|remove\s+(?:the\s+)?(?P<what2>[\w.+\-' ]{{2,48}}?)\s+(?:app|application|program|package|software))"
                rf"(?:\s+for\s+me)?(?:\s+please)?\s*[.!?]*\s*$", re.I),
     "system.software.search",
     lambda m: {"name": _software_query(m["what"] or m["what2"]), "scope": "installed"},
     "Looking."),
    # the catalog question on its own — green, read-only
    (re.compile(r"\b(?:search|look\s+up|look\s+for|find)\s+(?:for\s+)?(?P<what>[\w.+\-' ]{2,48}?)\s+(?:in|on)\s+"
                r"(?:the\s+)?(?:winget|catalog|catalogue|package\s+catalog)\b"
                r"|\bsearch\s+(?:the\s+)?(?:winget|catalog|catalogue)\s+for\s+(?P<what2>[\w.+\-' ]{2,48}?)\s*[.!?]*\s*$"
                r"|\bis\s+(?:there\s+)?(?:a\s+)?(?:winget\s+)?(?:package\s+for\s+)?(?P<what3>[\w.+\-' ]{2,48}?)\s+(?:in|on)\s+"
                r"(?:the\s+)?(?:winget|catalog|catalogue)\b"
                r"|\bwhat(?:'s|\s+is)\s+the\s+(?:winget\s+|package\s+)?id\s+(?:for|of)\s+(?P<what4>[\w.+\-' ]{2,48}?)\s*[.!?]*\s*$"
                r"|\bis\s+there\s+a\s+winget\s+package\s+for\s+(?P<what5>[\w.+\-' ]{2,48}?)\s*[.!?]*\s*$", re.I),
     "system.software.search",
     lambda m: {"name": _software_query(m["what"] or m["what2"] or m["what3"] or m["what4"] or m["what5"]),
                "scope": "catalog"},
     "Looking."),

    # A SERVICE, NOT A PROCESS. `proc.find` owns "is chrome running"; this rule
    # requires the word `service`, so the two cannot trade sentences.
    (re.compile(r"\bis\s+(?:the\s+)?(?P<name>[\w.\-' ]{2,40}?)\s+service\s+running\b"
                r"|\b(?:list|show|what|which|my)\b[^.]*\bservices?\b"
                r"|\bservices?\s+(?:that\s+are\s+)?running\b", re.I),
     "system.services.list", lambda m: _services_args(m),
     "Looking."),

    (re.compile(r"\bscheduled\s+tasks?\b|\btask\s+scheduler\b", re.I),
     "system.tasks.list", lambda m: {},
     "Looking."),

    # `\bscreens?\b` IS DELIBERATELY NOT MATCHED ON ITS OWN. "dim the screen",
    # "how bright is the screen" and "lock my screen" all contain it and all
    # belong elsewhere, so only "what screens" and "how many screens" reach here.
    (re.compile(r"\bmonitors?\b|\bresolution\b|\bdisplay\s+(?:info|settings|setup)\b"
                r"|\bwhat\s+screens?\b|\bhow\s+many\s+screens?\b", re.I),
     "system.display.info", lambda m: {},
     "Looking."),

    # ── POWER. ABOVE the process block on purpose: "stop the shutdown" and
    #    "cancel the 10 minute shutdown" carry a verb and a number that
    #    `proc.kill`'s rule would otherwise read as a pid.
    #
    #    ⚠ EVERY RULE HERE IS ANCHORED TO THE WHOLE CLAUSE (`_LEAD` ... `_TAIL`).
    #    "shut down" is the machine only when nothing follows it but the
    #    machine, "now", a delay or "please" — so "shut down chrome" and
    #    "shut the browser down" fall through untouched. "shut up", "stop"
    #    and "stop listening" match nothing here and reach STOP / SLEEP after
    #    the tool layer exactly as before.
    #
    #    ⚠ "go to sleep" IS NOT HERE AND MUST NOT BE. It is how he tells HER to
    #    stop listening; routing it to the machine once suspended his laptop
    #    mid-job. Suspending the machine names the machine (`sys.sleep`, below).
    #
    #    CANCEL FIRST: "don't shut down" must be read before "shut down".
    #    Green, and harmless when nothing is pending ("Nothing was scheduled").
    (re.compile(rf"\b(?:cancel|abort|stop|call\s+off|scrap|never\s*mind|forget|don'?t|do\s+not|undo)\b"
                rf"[^.]{{0,40}}?\b{_POWER_NOUN}\b(?:\s+(?:of\s+)?{_DET}{_MACHINE})?"
                rf"(?:\s+(?:now|please|yet|timer|countdown))?\s*[.!?]*\s*$"
                r"|^\s*abort(?:\s+(?:it|that|this))?\s*[.!?]*\s*$", re.I),
     "system.power.cancel", lambda m: {},
     "Cancelling."),
    (re.compile(r"\b(?:is\s+(?:a|any|anything|the)\s+(?:shutdown|restart|reboot|power\s+action)\s+"
                r"(?:pending|scheduled|coming|due)|is\s+anything\s+scheduled\s+to\s+(?:shut\s*down|restart|reboot)|"
                r"power\s+status|shutdown\s+status)\b", re.I),
     "system.power.status", lambda m: {},
     "Checking."),
    # THE FOUR REDS. The card is raised by the executor; the delay (if he
    # spoke one) is the only argument, and it is frozen on that card.
    (re.compile(rf"{_LEAD}{_SHUT}{_TAIL}", re.I),
     "system.power.shutdown", _delay_args,
     "Reading that back."),
    (re.compile(rf"{_LEAD}(?:restart|re-?boot|power\s+cycle){_TAIL}", re.I),
     "system.power.restart", _delay_args,
     "Reading that back."),
    (re.compile(rf"{_LEAD}(?:hibernate|(?:put|send)\s+{_DET}{_MACHINE}\s+(?:into|to)\s+hibernat(?:e|ion)){_TAIL}", re.I),
     "system.power.hibernate", _delay_args,
     "Reading that back."),
    (re.compile(rf"{_LEAD}(?:(?:log|sign)\s*-?\s*(?:me\s+)?(?:off|out)|logoff|logout)"
                rf"(?:\s+(?:of|from)\s+{_DET}{_MACHINE})?{_TAIL}", re.I),
     "system.power.logoff", _delay_args,
     "Reading that back."),

    # ── PRIORITY sits ABOVE the kill rule. "priority" is the distinctive noun
    #    (every rule here requires it), and it has to be read before `kill`'s
    #    verb list: "stop giving 4242 high priority" contains "stop" and a
    #    number, which is exactly the shape `proc.kill` matches. Three shapes:
    #    an explicit level ("set 4242 to high priority"), a verb ("raise the
    #    priority of 4242"), and an adjective after the number ("give 4242 a
    #    higher priority"). Realtime routes and is REFUSED by the tool's closed
    #    choice set — see core/system/abilities/process.py.
    (re.compile(r"\b(?:set|put|make|change|switch)\b(?=.*\bpriority\b).*?\b(?P<pid>\d{2,7})\b"
                r".*?\bto\s+(?P<level>real\s*-?\s*time|high|above\s+normal|normal|below\s+normal|low|idle)\b"
                r"|\b(?:set|put|make|change|switch)\b.*?\b(?P<pid2>\d{2,7})\b\s+(?:to\s+)?"
                r"(?P<level2>real\s*-?\s*time|high|above\s+normal|normal|below\s+normal|low|idle)\s+priority\b", re.I),
     "system.process.priority",
     lambda m: {"pid": int(m["pid"] or m["pid2"]), "level": _priority_level(m["level"] or m["level2"])},
     "Checking what that is."),
    (re.compile(r"\b(?P<verb>raise|boost|increase|bump|lower|reduce|drop|decrease)\b"
                r"(?=.*\bpriority\b).*?\b(?P<pid>\d{2,7})\b", re.I),
     "system.process.priority",
     lambda m: {"pid": int(m["pid"]), "level": _priority_level(m["verb"])},
     "Checking what that is."),
    (re.compile(r"\b(?P<pid>\d{2,7})\b.*?\b(?P<adj>higher|lower|more|less)\s+priority\b", re.I),
     "system.process.priority",
     lambda m: {"pid": int(m["pid"]), "level": _priority_level(m["adj"])},
     "Checking what that is."),

    # ── PROCESSES. `kill <pid>` sits above every other kill phrasing, and the
    #    port form is vetoed explicitly because `sys.kill_port` owns it.
    (re.compile(r"\b(?:kill|end|terminate|stop)\b(?!.*\bport\b).*?\b(?P<pid>\d{2,7})\b", re.I),
     "proc.kill", lambda m: {"pid": int(m["pid"])},
     "Checking what that is."),

    # ── DETAILS: one process, by number or by name. "how much memory is chrome
    #    using" used to fall through to `sys.memory` and answer about the whole
    #    MACHINE. Read-only. The name form aggregates every match and reads back
    #    the heaviest pid, so the next sentence can be "kill <that number>".
    (re.compile(r"\bhow\s+much\s+(?P<what>memory|ram|cpu|processor)\s+(?:is|does|do|are)\s+"
                r"(?:the\s+)?(?P<name>[\w.\-' ]{2,30}?|\d{2,7})\s+(?:using|use|taking|eating|hogging)\b", re.I),
     "system.process.details", lambda m: _details_args(m["name"]),
     "Looking."),
    (re.compile(r"\b(?:what(?:'s|\s+is)|details?\s+(?:of|on|for|about)|tell\s+me\s+about|describe"
                r"|info(?:rmation)?\s+(?:on|about|for))\s+(?:the\s+)?(?:process|pid)\s+(?P<pid>\d{2,7})\b", re.I),
     "system.process.details", lambda m: {"pid": int(m["pid"])},
     "Looking."),

    # ── KILL BY NAME IS NOT A KILL. "kill chrome" names an IMAGE, and one image
    #    is many processes (the 37-Code.exe incident). It routes to `proc.find`,
    #    which reads back the matching pids so he can name ONE; there is no tool
    #    it could reach that takes a name and ends anything. Below the pid rule,
    #    so any number in the sentence still wins. `stop <name>` is deliberately
    #    absent — "stop" belongs to the STOP and SLEEP intents — and `end <name>`
    #    vetoes the conversation words so "end the conversation" stays what it was.
    (re.compile(r"\b(?:kill|terminate)\b(?!.*\bport\b)\s+(?:all\s+of\s+the\s+|all\s+the\s+|all\s+|every\s+|the\s+)?"
                r"(?:process(?:es)?\s+)?(?P<name>[a-z][\w.\-]{1,30})(?:'s)?(?:\s+process(?:es)?)?\s*$"
                r"|\bend\s+(?:all\s+|every\s+|the\s+)?"
                r"(?!(?:conversation|call|session|chat|meeting|turn|day|it|that|this|me|now)\b)"
                r"(?P<name2>[a-z][\w.\-]{1,30})(?:\s+process(?:es)?)?\s*$", re.I),
     "proc.find", lambda m: _kill_name(m["name"] or m["name2"]),
     "Not by name, Emperor. Which one?"),
    (re.compile(rf"{_WHATS}\s+(?:eating|using|hogging|taking)\b.*\b(?P<what>cpu|memory|ram)\b", re.I),
     "proc.top", lambda m: {"by": "cpu" if m["what"].lower() == "cpu" else "memory"},
     "Looking."),
    (re.compile(r"\b(?:top|heaviest|biggest|worst)\b.*?\b(?:process|processes|by)\b\s*(?P<what>cpu|memory|ram)?", re.I),
     "proc.top", lambda m: {"by": "cpu" if (m["what"] or "").lower() == "cpu" else "memory"},
     "Looking."),
    (re.compile(r"\b(?:is|are)\s+(?P<name>[\w.\- ]{2,30}?)\s+running\b", re.I),
     "proc.find", lambda m: {"name": _clean(m["name"])},
     "Checking."),
    (re.compile(r"\bany\s+(?P<name>[\w.\-]{2,30})\s+process(?:es)?\b", re.I),
     "proc.find", lambda m: {"name": _clean(m["name"])},
     "Checking."),
    (re.compile(r"\bfind\s+(?:the\s+)?(?P<name>[\w.\-]{2,30})\s+process(?:es)?\b", re.I),
     "proc.find", lambda m: {"name": _clean(m["name"])},
     "Checking."),
    (re.compile(rf"{_WHATS}\s+running\b|\blist\s+(?:the\s+)?processes\b|"
                r"\bhow\s+many\s+processes\b", re.I),
     "proc.list", lambda m: {},
     "Looking."),

    # ── WINDOW LISTING sits here, beside the process listing, and ABOVE the
    #    file rules on purpose: "list my windows" would otherwise be read by
    #    `fs.list` as a folder called "windows" and fail on a path that does
    #    not exist. Two "list X" verbs, disambiguated by the noun.
    (re.compile(rf"\b(?:what\s+have\s+i\s+got\s+open|{_WHATS}\s+open|"
                r"list\s+(?:my\s+)?windows|"
                # "what windows are open" was UNROUTED — the most literal way
                # to ask the question the rule already answers.
                r"what\s+windows?\s+(?:are|do\s+i\s+have)\s+open)\b", re.I),
     "win.list", lambda m: {},
     "Looking."),

    # ── CLIPBOARD. Above the file rules because "copy" is shared, and the
    #    clipboard forms all name the clipboard explicitly.
    # COPY <TEXT> TO THE CLIPBOARD — ABOVE `fs.copy`, WHICH WAS EATING IT.
    #
    # "copy hello world to the clipboard" matched the FILE copy rule and tried
    # to copy a file called "hello world" into a folder called "the
    # clipboard". Measured. The clipboard forms all name the clipboard
    # explicitly, so requiring that word is enough to separate them, and
    # `fs.copy`'s "copy X to my desktop" is untouched.
    (re.compile(r"\b(?:copy|put|save|stick)\s+(?P<text>.+?)\s+(?:to|on|onto|into)\s+"
                r"(?:my\s+|the\s+)?clip\s?board\b", re.I),
     "clip.write", lambda m: {"text": _clean(m["text"])},
     "Copied."),

    (re.compile(r"\b(?:clear|wipe|empty)\b.*\bclip\s?board\b", re.I),
     "clip.clear", lambda m: {},
     "Clearing it."),
    (re.compile(rf"{_WHATS}\s+on\s+(?:my\s+|the\s+)?clip\s?board\b|"
                r"\bread\s+(?:my\s+|the\s+)?clip\s?board\b|"
                r"\bwhat\s+did\s+i\s+copy\b", re.I),
     "clip.read", lambda m: {},
     "Reading it."),

    # ── SYSTEM QUERIES SIT ABOVE THE FILE RULES, and this is a bug fix rather
    #    than a preference: `fs.list`'s "list <thing>" form matched "list wifi"
    #    and went looking for a folder called wifi. Every rule in this block
    #    names a specific system noun — wifi, ip, brightness, the connection —
    #    so none of them can shadow a file phrasing, while the generic file
    #    verbs would happily shadow all of them.
    # ── THE RADIO SWITCH GOES FIRST, ABOVE EVERY OTHER WIFI RULE.
    #
    #    `sys.wifi` below matches a BARE mention of wifi, so before this rule
    #    existed "turn wifi off" scanned for networks in range and read him a
    #    list — measured, not theorised. A switch and a scan are different
    #    acts and the switch is the specific one, so it is tried first.
    #
    #    A VERB IS REQUIRED. Matching a bare "wifi on" would swallow "is the
    #    wifi on", which is a QUESTION and belongs to the status rule below.
    #    So the radio noun alone is never enough: he has to say turn, switch,
    #    put, flip, enable or disable.
    #
    #    `system.radio.set` is AMBER. It therefore holds for his confirmation
    #    (core/system/abilities/radio.py — Wi-Fi off cuts her own connections),
    #    and this rule does not and cannot bypass that: the tier comes from
    #    permissions.yaml through the registry, never from here.
    (re.compile(r"\b(?:turn|switch|put|flip)\s+(?:the\s+|my\s+)?"
                r"(?P<r1>wi\s?-?fi|blue\s?tooth)\s+(?P<s1>on|off)\b"
                r"|\b(?:turn|switch|put|flip)\s+(?P<s2>on|off)\s+(?:the\s+|my\s+)?"
                r"(?P<r2>wi\s?-?fi|blue\s?tooth)\b"
                r"|\b(?P<v3>enable|disable)\s+(?:the\s+|my\s+)?(?P<r3>wi\s?-?fi|blue\s?tooth)\b", re.I),
     "system.radio.set",
     lambda m: {"radio": _radio(m["r1"] or m["r2"] or m["r3"]),
                "state": (m["s1"] or m["s2"]
                          or ("on" if (m["v3"] or "").lower() == "enable" else "off")).lower()},
     "Hold on."),

    # ── NETWORK STATUS — the question form, above the scan.
    #
    #    Retargeted from `sys.network` to `system.network.status`, which
    #    answers the same question and three more he actually asks: is the
    #    radio on, which network am I on, is Bluetooth on. `sys.network` is
    #    still a registered tool and still reachable by name; it is simply no
    #    longer what these sentences mean.
    #
    #    "what wifi am I on" and "is the wifi on" USED TO SCAN, for the same
    #    reason "turn wifi off" did. They are questions about the connection,
    #    not requests for a list of everything in range.
    (re.compile(r"\b(?:am\s+i\s+online|is\s+the\s+internet\b|network\s+status|"
                r"(?:have|got)\s+(?:i\s+)?(?:a\s+)?connection|am\s+i\s+connected|"
                rf"{_WHATS}\s+my\s+network\b|"
                r"(?:what|which)\s+(?:wi\s?-?fi|network)\s+am\s+i\s+(?:on|connected\s+to)|"
                r"(?:is|are)\s+(?:the\s+|my\s+)?(?:wi\s?-?fi|blue\s?tooth)\s+"
                r"(?:on|off|enabled|connected)|"
                r"what\s+am\s+i\s+connected\s+to)\b", re.I),
     "system.network.status", lambda m: {},
     "Checking."),

    # ── BRIGHTNESS. Retargeted to the capability, which reads the panel BACK
    #    after setting it and refuses to claim a level the hardware does not
    #    show. `_NUM` accepts the spoken form: "set brightness to eighty" used
    #    to fall through to the bare rule below and READ the brightness out.
    (re.compile(rf"\b(?:set|put|change|make)\s+(?:the\s+)?brightness\s+(?:to|at)\s+(?P<lvl2>{_NUM})\b"
                rf"|\bbrightness\b\D*?(?P<lvl>{_NUM})\b", re.I),
     "system.brightness.set", lambda m: {"level": _level(m["lvl2"] or m["lvl"])},
     "Setting it."),
    (re.compile(r"\b(?:dim|darken)\b.*\bscreen\b", re.I),
     "system.brightness.set", lambda m: {"level": 30},
     "Dimming it."),
    # A SET WHOSE LEVEL DID NOT PARSE IS NOT A GET.
    #
    # "set brightness to banana" reached this bare rule and she READ the
    # brightness back — an answer, to a question he did not ask, which reads
    # as though the command worked. `match()` treats ValueError as "this rule
    # did not fire", so vetoing here drops the clause to UNROUTED and she says
    # she did not catch it. That is the same shape as `_is_nameable` above,
    # and the same reason: a garbled phrase must not become a confident act.
    # `m.string` is the whole clause, so the veto sees the verb the pattern
    # itself does not capture.
    (re.compile(r"\bbrightness\b|\bhow\s+bright\b", re.I),
     "system.brightness.get", lambda m: _no_stranded_set(m),
     "Checking."),

    (re.compile(r"\b(?:my\s+)?ip(?:\s+address)?\b", re.I),
     "sys.ip", lambda m: {},
     "Checking."),
    # THE SCAN. Still `sys.wifi`, still "what networks are in range" — it is a
    # different question from every rule above and it keeps its own tool.
    (re.compile(r"\bwi\s?-?fi\b|\bwireless\s+networks?\b", re.I),
     "sys.wifi", lambda m: {},
     "Scanning."),
    # SUSPENDING THE MACHINE REQUIRES NAMING THE MACHINE.
    #
    # This pattern used to include a bare `go to sleep`, and that was a real
    # collision rather than a stylistic one: "go to sleep" is the most natural
    # way to tell HER to stop listening, and it resolved to sys.sleep — a GREEN
    # tool, so it executed with no confirmation and suspended his laptop.
    # He would have said four ordinary words and watched the machine go dark
    # mid-job.
    #
    # Addressed to Tessa, "go to sleep" means Tessa. Suspending the computer is a
    # different and more consequential act, so it now has to say so.
    (re.compile(rf"\b(?:(?:sleep|suspend)\s+{_DET}{_MACHINE}|"
                rf"(?:put|send)\s+{_DET}{_MACHINE}\s+(?:to|into)\s+sleep|"
                r"suspend)\b", re.I),
     "sys.sleep", lambda m: {},
     "Sleeping."),
    # LOCK — reuses `sys.lock` (green, LockWorkStation). "lock my screen"
    # used to route NOWHERE: intents.py only knew "lock the screen". Named
    # forms, "lock it", and the bare word; anchored, so "lock the vault"
    # stays whatever the vault makes of it.
    (re.compile(rf"{_LEAD}lock(?:\s+{_DET}(?:{_MACHINE}|screen|workstation|session)|\s+it|\s+up)?"
                r"(?:\s+now)?(?:\s+please)?\s*[.!?]*\s*$", re.I),
     "sys.lock", lambda m: {},
     "Locking."),

    # ── DEV AND FILES (batch 3) — SEARCH, CREATE, AND VS CODE.
    #
    #    THIS BLOCK SITS ABOVE THE MEDIA RULES, AND THAT POSITION IS A BUG FIX.
    #    `\b(?:pause|resume|play)\b` matched the word "resume" in "where is my
    #    resume", so asking where a file was PAUSED HIS MUSIC and never
    #    searched. Measured, not theorised. A search verb at the front of a
    #    clause is a search, and precedence is the honest fix — vetoing the
    #    word "resume" inside the media rule would leave the next collision
    #    ("play" in "playlist.txt") live.
    #
    #    Search and create are RETARGETED here from `fs.search` and
    #    `fs.create`, which stay registered and reachable by name. Same
    #    treatment `sys.brightness` and `sys.network` got: one path per action,
    #    and the capability version is the one his words reach.

    # VS CODE BEFORE EVERY OTHER `open`. "open C:\dev\tessa in vscode" used to
    # reach app.open_folder — it saw the path, opened EXPLORER, and dropped the
    # "in vscode" entirely. The rule requires the editor to be named, so a bare
    # "open vs code" (launch the editor) and "open my downloads" are untouched.
    (re.compile(r"\b(?:open|edit|load|launch)\s+(?P<what>.+?)\s+in\s+"
                r"(?:vs\s?code|visual\s+studio\s+code|code)\b", re.I),
     "system.code.open", lambda m: {"path": _code_path(m["what"])},
     "Opening it."),

    # THE PATTERN IS UNCHANGED FROM THE `fs.search` RULE IT REPLACES — only the
    # tool and the position moved. Changing both the matching and the target in
    # one step would make a regression impossible to attribute.
    (re.compile(r"\b(?:find|search for|look for|where is)\b\s+(?:a\s+|the\s+|my\s+|any\s+)?"
                r"(?:file|folder)?\s*(?:called|named)?\s*(?P<name>.+)$", re.I),
     "system.files.search", lambda m: {"name": _clean(m["name"])},
     "Searching."),

    # FILE BEFORE FOLDER, distinguished by the noun he says. Both refuse to
    # overwrite; that is enforced in the capability, not here.
    (re.compile(r"\b(?:make|create|new)\b[^.]*\bfile\b[^.]*?\b(?:called|named)\s+(?P<name>.+)$", re.I),
     "system.files.create_file", lambda m: {"path": _create_path(m["name"])},
     "Making it."),
    (re.compile(r"\b(?:make|create|new)\b.*\b(?:folder|directory)\b.*?\b(?:called|named)\s+(?P<name>.+)$", re.I),
     "system.files.create_folder", lambda m: {"path": _create_path(m["name"])},
     "Making it."),

    # ── VOLUME AND MEDIA. These existed as coarse keyword rules further down
    #    intents.py and "turn it up" — the single most natural way to say it —
    #    matched none of them. Routed here instead so they go through the
    #    registry and pick up the tier and the audit entry with everything else.
    #
    #    "stop" is deliberately absent from the media verbs. It is the STOP
    #    intent, it halts her speech, and it is the one word he uses when
    #    something has gone wrong. Overloading it onto the media keys would
    #    make his interrupt sometimes pause Spotify instead.
    # ── AN ABSOLUTE LEVEL GOES TO THE CAPABILITY, A RELATIVE STEP TO THE KEYS.
    #
    #    These are two different acts and this file now says so. "set volume to
    #    forty" names a number, so it goes to `system.volume.set`, which writes
    #    the endpoint scalar and reads it back — exact, and verifiable.
    #    "turn it up" names no number, so it stays on `sys.volume`, which taps
    #    the media key three times. That is not a legacy leftover: there is no
    #    absolute level in the sentence, and inventing one at parse time (read
    #    the device, add ten) would put device I/O in the router and act on a
    #    level that may have changed by the time the tool ran.
    #
    #    Neither rule can match the other's sentence: this one requires a
    #    number, and `up`/`down`/`louder` are not numbers.
    (re.compile(rf"\b(?:set|put|change|make)\s+(?:the\s+)?(?:volume|sound)\s+(?:to|at)\s+(?P<lvl>{_NUM})\b"
                rf"|\b(?:volume|sound)\s+(?:to\s+|at\s+)?(?P<lvl2>{_NUM})\s*(?:percent|%)?\b", re.I),
     "system.volume.set", lambda m: {"level": _level(m["lvl"] or m["lvl2"])},
     "Setting it."),
    (re.compile(rf"{_WHATS}\s+the\s+(?:volume|sound)(?:\s+level)?\b|\bhow\s+loud\b|"
                r"\bvolume\s+level\b", re.I),
     "system.volume.get", lambda m: {},
     "Checking."),

    (re.compile(r"\b(?:turn\s+(?:it|the\s+(?:volume|sound))\s+up|volume\s+up|louder|"
                r"crank\s+it|turn\s+it\s+up)\b", re.I),
     "sys.volume", lambda m: {"direction": "up"},
     "Up."),
    (re.compile(r"\b(?:turn\s+(?:it|the\s+(?:volume|sound))\s+down|volume\s+down|quieter|"
                r"turn\s+it\s+down)\b", re.I),
     "sys.volume", lambda m: {"direction": "down"},
     "Down."),
    # UNMUTE BEFORE MUTE, AND THEY NOW MEAN DIFFERENT THINGS.
    #
    # Both used to send `direction: mute` — the TOGGLE key — so "unmute"
    # muted a machine that was already unmuted, and she said "Muted." either
    # way. `system.volume.mute` takes an explicit boolean and reads the
    # endpoint back, so the word he said is the state he gets.
    (re.compile(r"\bunmute\b|\bsound\s+back\s+on\b|\bun\s?-?mute\b", re.I),
     "system.volume.mute", lambda m: {"muted": False},
     "Sound back."),
    (re.compile(r"\b(?:mute|silence\s+it|sound\s+off)\b", re.I),
     "system.volume.mute", lambda m: {"muted": True},
     "Muted."),
    (re.compile(r"\b(?:next\s+(?:track|song|one)|skip\s+(?:this|it|ahead)?)\b", re.I),
     "sys.media", lambda m: {"action": "next"},
     "Skipped."),
    (re.compile(r"\b(?:previous\s+(?:track|song)|go\s+back\s+a\s+(?:track|song))\b", re.I),
     "sys.media", lambda m: {"action": "previous"},
     "Back one."),
    (re.compile(r"\b(?:pause|resume|play)\b(?!\s+(?:me|it\s+again))", re.I),
     "sys.media", lambda m: {"action": "playpause"},
     "Done."),

    # ── FILES, destructive first.
    (re.compile(r"\b(?:delete|bin|trash|get rid of|remove)\s+(?:the\s+|that\s+|my\s+)?(?P<what>.+)$", re.I),
     "fs.delete", lambda m: {"path": _need_target(m["what"])},
     "Hold on."),

    (re.compile(r"\b(?:rename)\s+(?:the\s+|that\s+|my\s+)?(?P<what>.+?)\s+to\s+(?P<to>.+)$", re.I),
     "fs.rename", lambda m: {"path": _need_target(m["what"]), "to": _clean(m["to"])},
     "Renaming it."),
    (re.compile(r"\b(?:move)\s+(?:the\s+|that\s+|my\s+)?(?P<what>.+?)\s+(?:to|into)\s+(?P<to>.+)$", re.I),
     "fs.move", lambda m: {"path": _need_target(m["what"]), "to": _need_target(m["to"])},
     "Moving it."),
    (re.compile(r"\b(?:copy|duplicate)\s+(?:the\s+|that\s+|my\s+)?(?P<what>.+?)\s+(?:to|into)\s+(?P<to>.+)$", re.I),
     "fs.copy", lambda m: {"path": _need_target(m["what"]), "to": _need_target(m["to"])},
     "Copying it."),
    # `fs.create`'s rule MOVED to the dev/files block above and retargeted to
    # `system.files.create_folder`, which parses "<name> in <folder>" instead
    # of hardcoding Documents and calling the whole phrase the folder name.
    # `fs.create` stays registered and reachable by name.

    # VETOED ON `disk` AND `drive`. "How much space is on my disk" is a
    # question about the volume and `sys.disk` owns it; without this veto the
    # folder rule matched first and tried to size a folder called "on my disk".
    (re.compile(r"\b(?:how\s+big|how\s+much\s+space|size)\b(?!.*\b(?:disk|drive|c\s+drive)\b)"
                r".*?\b(?:is|of|does)?\s*(?:my\s+|the\s+)?"
                r"(?P<what>[\w:\\ .\-]+?)(?:\s+folder)?(?:\s+us(?:e|ing))?\s*$", re.I),
     "fs.usage", lambda m: {"path": _need_target(m["what"])},
     "Adding it up."),

    # `fs.search`'s rule MOVED to the dev/files block above, retargeted to
    # `system.files.search` and placed above the media rules. It answers from a
    # cached filename index in 1-85 ms instead of walking the disk for up to
    # 42 seconds. `fs.search` stays registered and reachable by name.

    (re.compile(r"(?!.*\b(?:windows|processes)\b)"
                rf"(?:{_WHATS}\s+in\s+(?:my\s+|the\s+)?(?P<what>.+)$|"
                r"\blist\s+(?:my\s+|the\s+)?(?P<what2>.+)$|"
                r"\bwhat\s+have\s+i\s+got\s+in\s+(?:my\s+|the\s+)?(?P<what3>.+)$)", re.I),
     "fs.list", lambda m: {"path": _need_target(m["what"] or m["what2"] or m["what3"])},
     "Looking."),

    (re.compile(r"\bread\s+(?:me\s+)?(?:the\s+|that\s+|my\s+)?(?P<what>.+)$", re.I),
     "fs.read", lambda m: {"path": _need_target(m["what"])},
     "Reading it."),

    # THE TARGET IS CAPTURED NOW. The old pattern's first alternative had no
    # group at all, so "reveal plan.md in explorer" reached `fs.reveal` with
    # path="" and she answered "no path came through" about a file he had just
    # named. `open <folder> in explorer` is deliberately NOT matched here and
    # still reaches `app.open_folder`, which OPENS the folder — reveal SELECTS
    # a thing in its parent, and for a folder that is the wrong gesture.
    (re.compile(r"\b(?:reveal|show\s+me)\s+(?P<what>.+?)\s+in\s+(?:the\s+)?explorer\b"
                r"|\breveal\s+(?P<what2>.+)$", re.I),
     "fs.reveal", lambda m: {"path": _need_target(m["what"] or m["what2"] or "")},
     "Showing you."),

    # ── WINDOWS. `close` and `minimise` name a WINDOW, never a process.
    #    `go to` is deliberately NOT a focus verb: "go to my downloads" is a
    #    folder in his vocabulary and always has been, and stealing it here
    #    would have broken a phrase that already worked.
    # ── RESTORE AND SNAP — the two window acts that did not exist.
    #
    #    Both resolve their target through `core.tools.winman._find_one`, so
    #    "this window" means the one in front. Neither closes anything: there
    #    is no WM_CLOSE and no PID in either path. `win.close` (green, the app
    #    decides) and `proc.kill` (amber, PID only) stay exactly where they are.
    (re.compile(r"\bun-?minimi[sz]e\s+(?P<name>.+?)(?:\s+window)?$"
                r"|\brestore\s+(?P<n2>.+?)(?:\s+window)?$"
                r"|\bput\s+(?P<n3>.+?)\s+back(?:\s+up)?$", re.I),
     "system.window.restore",
     lambda m: {"name": _clean(m["name"] or m["n2"] or m["n3"])},
     "Bringing it back."),
    (re.compile(r"\bsnap\s+(?P<name>.+?)\s+(?:to\s+the\s+|to\s+)?(?P<side>left|right)"
                r"(?:\s+half|\s+hand\s+side)?$", re.I),
     "system.window.snap",
     lambda m: {"name": _clean(re.sub(r"\s+window$", "", m["name"], flags=re.I)),
                "side": m["side"].lower()},
     "Snapping it."),

    (re.compile(r"\bminimi[sz]e\s+(?P<name>.+)$|\bhide\s+(?P<name2>.+)$", re.I),
     "win.minimise", lambda m: {"name": _clean(m["name"] or m["name2"])},
     "Out of the way."),
    (re.compile(r"\bmaximi[sz]e\s+(?P<name>.+)$|\b(?:full\s?screen)\s+(?P<name2>.+)$", re.I),
     "win.maximise", lambda m: {"name": _clean(m["name"] or m["name2"])},
     "Full screen."),
    (re.compile(r"\bclose\s+(?:the\s+|my\s+)?(?P<name>.+?)(?:\s+window)?$", re.I),
     "win.close", lambda m: {"name": _clean(m["name"])},
     "Closing it."),
    (re.compile(r"\b(?:bring|switch to|focus(?:\s+on)?)\s+(?:the\s+|my\s+)?"
                r"(?P<name>.+?)(?:\s+forward|\s+window)?$", re.I),
     "win.focus", lambda m: {"name": _clean(m["name"])},
     "Bringing it up."),

]


#: Words that can never be the NAME of a thing on his disk.
#:
#: Pronouns and deictics. He says them constantly — "read me that", "open it" —
#: and they refer to something in the conversation, never to a file called
#: "that".
_NOT_A_NAME = {
    "me", "it", "that", "this", "these", "those", "them", "they", "you",
    "us", "him", "her", "there", "here", "now", "then", "one", "thing",
    "stuff", "something", "anything", "everything", "please", "again",
    "yes", "no", "ok", "okay", "up", "down", "back",
}

#: (tool, argument) pairs whose value must NAME AN EXISTING THING.
#:
#: Deliberately NOT including `fs.search.name`, and that exclusion is the whole
#: reason this is a list rather than a blanket rule: "find me a file called
#: invoice" passes a SEARCH TERM, and a search term is allowed to name something
#: that does not exist — that is what searching is.
_MUST_NAME_A_THING = {
    ("fs.list", "path"), ("fs.usage", "path"), ("fs.read", "path"),
    ("fs.delete", "path"), ("fs.move", "path"), ("fs.open", "path"),
    ("app.open_folder", "path"),
    # ADDED with the reveal capture above: now that "show me X in explorer"
    # takes a target, "show me THAT in explorer" would hand `fs.reveal` the
    # word "that" and she would answer "that is not there". A path made
    # entirely of pronouns is a mistranscribed sentence, not a name.
    ("fs.reveal", "path"),
}


def _is_nameable(value: str) -> bool:
    """
    Could this string plausibly name something on his machine?

    THE BUG THIS EXISTS FOR: Whisper returned "Stop List Me." for "stop
    listening", the `list <thing>` rule matched, and `fs.list` fired with
    `path="Me"`. She answered "That failed, sir. Me is not there." — a garbled
    phrase turned into an action against a nonsense argument, and the error
    message made it sound like his fault.

    DELIBERATELY NARROW. The test is not "does this exist" — he is allowed to
    ask about a folder that turns out to be missing, and the tool saying so is
    more useful than a refusal. The test is whether every word is a PRONOUN,
    because a path made entirely of pronouns is not a mistranscribed name, it is
    a mistranscribed sentence.

    Making it stricter would break the thing that makes her usable: the router
    meets him halfway, and a rule that demanded every argument resolve would
    turn "what's in my invoices folder" into a refusal instead of an answer.
    """
    v = " ".join(str(value or "").split()).strip(" .,?!\"'").lower()
    if not v:
        return False
    # A path shape is self-evidently a name, whatever words are in it.
    if any(ch in v for ch in ("\\", "/", ":")):
        return True
    words = [w for w in re.split(r"[^\w]+", v) if w]
    if not words:
        return False
    return not all(w in _NOT_A_NAME for w in words)


#: A word-initial @name inside an argument (not "ada@example.com").
_HANDLE_IN_ARG = re.compile(r"(?<![\w.@])@[A-Za-z0-9_]{1,15}\b")


def _is_file_tool(name: str) -> bool:
    """The tools whose target is a path on his disk."""
    return (name.startswith(("fs.", "system.files.")) or name in ("app.open_folder", "system.code.open"))


def match(clause: str) -> ToolCall | None:
    """First rule that fires. Returns None so the caller can fall through."""
    c = (clause or "").strip()
    if not c:
        return None
    for pattern, name, build, opener in _RULES:
        m = pattern.search(c)
        if not m:
            continue
        try:
            args = build(m)
        except (ValueError, TypeError, KeyError):
            continue

        # ── THE NEAR-MISS BOUNDARY ──────────────────────────────────────────
        #
        # `continue`, not `return None`: a later rule may still match this
        # clause legitimately. Falling through to UNROUTED is what produces the
        # honest "I did not catch that" rather than an action on a nonsense
        # argument.
        if any((name, k) in _MUST_NAME_A_THING and not _is_nameable(v)
               for k, v in args.items()):
            continue
        # AN @HANDLE IS NEVER A FILE (the sentences round, 2026-10-06). His
        # "read @mcityXtra_ bio" became fs.read of a path called "@mcityXtra_
        # bio". A file tool whose target holds a word-initial @name skips this
        # rule; the profile rules above own those sentences, and anything they
        # do not match falls through rather than reaching a file.
        if _is_file_tool(name) and any(isinstance(v, str) and _HANDLE_IN_ARG.search(v)
                                       for v in args.values()):
            continue

        from core.tools import REGISTRY

        spec = REGISTRY.get(name)
        tier = spec.tier if spec else "green"
        return ToolCall(name=name, args=args, tier=tier, speech=opener)
    return None
