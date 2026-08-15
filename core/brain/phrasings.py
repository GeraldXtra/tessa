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


# ── the table ────────────────────────────────────────────────────────────────
#
# (pattern, tool name, args builder, spoken opener)
#
# The opener is what she says WHILE the tool runs. Short, because Piper streams
# per sentence and the first one is the whole 400 ms budget.

_RULES: list[tuple[re.Pattern[str], str, Builder, str]] = [

    # ── SHELL. Anchored, so only a sentence that STARTS with the verb is a
    #    command. "what's running" and "run of the mill" cannot reach it.
    (re.compile(r"^(?:run|execute)\s+(?P<cmd>\S.*)$", re.I),
     "shell.execute", lambda m: {"command": _clean(m["cmd"]), "provenance": "human"},
     "Reading that back."),

    # ── BROWSER AND X SIT NEAR THE TOP, and every rule here is keyed to a
    #    distinctive noun — browser, page, site, timeline, X, tweet. That is
    #    what keeps them from colliding with the file verbs below, which are
    #    generic by nature: `fs.read`'s "read <thing>" would happily swallow
    #    "read me this page", and `win.close`'s "close <thing>" would swallow
    #    "close the browser". Specific noun first, generic verb second.

    (re.compile(r"\b(?:forget|clear|drop)\b.*\b(?:page|site|article|what you read|"
                r"what you just read)\b", re.I),
     "context.forget", lambda m: {},
     "Forgetting it."),

    (re.compile(r"\bclose\b.*\b(?:the\s+)?browser\b|\bclose\s+chrome\b(?=.*\bbrowser\b)|"
                r"\byou\s+can\s+close\s+(?:the\s+)?browser\b", re.I),
     "browser.close", lambda m: {},
     "Closing it."),

    # X BEFORE the generic browser rules: "read my timeline" is an X read, not
    # a page read, and "tweet that" must never reach a file verb.
    (re.compile(r"\b(?:log\s*(?:me\s*)?in(?:to)?\s+(?:to\s+)?(?:x|twitter)|"
                r"open\s+(?:x|twitter)\s+so\s+i\s+can\s+log|sign\s+me\s+in(?:to)?\s+(?:to\s+)?(?:x|twitter))\b", re.I),
     "x.login", lambda m: {},
     "Opening it."),
    (re.compile(r"\b(?:my\s+)?(?:x\s+)?notifications\b|\bwho\s+replied\s+to\s+me\b", re.I),
     "x.read_notifications", lambda m: {},
     "Checking."),
    (re.compile(r"\b(?:read\s+(?:me\s+)?(?:my\s+)?timeline|my\s+timeline|"
                rf"{_WHATS}\s+(?:on|happening\s+on)\s+(?:x|twitter))\b", re.I),
     "x.read_timeline", lambda m: {},
     "Reading it."),
    (re.compile(r"\b(?:like|favourite|favorite)\b\s+(?:the\s+|that\s+|post\s+)?"
                r"(?P<idx>\d+|first|second|third|one|two|three)?\b(?!.*\bfolder\b)", re.I),
     "x.like", lambda m: {"index": _ordinal(m["idx"])},
     "Liking it."),
    (re.compile(r"\b(?:repost|retweet|share)\b\s+(?:the\s+|that\s+|post\s+)?"
                r"(?P<idx>\d+|first|second|third|one|two|three)?\b", re.I),
     "x.repost", lambda m: {"index": _ordinal(m["idx"])},
     "Reposting it."),
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
    # "Zoey, Tweets, Data Mbudinon AI Assist" — Whisper wrote the verb as a
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
    (re.compile(r"\b(?:google|search\s+(?:the\s+)?(?:web|online|internet)\s+for|"
                r"look\s+up|search\s+for\s+(?P<q2>.+?)\s+(?:online|on\s+the\s+web))\b\s*(?P<q>.*)$", re.I),
     "browser.search", lambda m: {"query": _clean(m["q"] or m["q2"] or "")},
     "Searching."),

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

    # ── PROCESSES. `kill <pid>` sits above every other kill phrasing, and the
    #    port form is vetoed explicitly because `sys.kill_port` owns it.
    (re.compile(r"\b(?:kill|end|terminate|stop)\b(?!.*\bport\b).*?\b(?P<pid>\d{2,7})\b", re.I),
     "proc.kill", lambda m: {"pid": int(m["pid"])},
     "Checking what that is."),
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
                r"list\s+(?:my\s+)?windows)\b", re.I),
     "win.list", lambda m: {},
     "Looking."),

    # ── CLIPBOARD. Above the file rules because "copy" is shared, and the
    #    clipboard forms all name the clipboard explicitly.
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
    (re.compile(r"\bbrightness\b.*?(?P<level>\d{1,3})\b|\bset\s+brightness\s+to\s+(?P<level2>\d{1,3})\b", re.I),
     "sys.brightness", lambda m: {"level": int(m["level"] or m["level2"])},
     "Setting it."),
    (re.compile(r"\b(?:dim|darken)\b.*\bscreen\b", re.I),
     "sys.brightness", lambda m: {"level": 30},
     "Dimming it."),
    (re.compile(r"\bbrightness\b", re.I),
     "sys.brightness", lambda m: {},
     "Checking."),
    (re.compile(r"\b(?:am\s+i\s+online|is\s+the\s+internet\b|network\s+status|"
                r"(?:have|got)\s+(?:i\s+)?(?:a\s+)?connection|am\s+i\s+connected)\b", re.I),
     "sys.network", lambda m: {},
     "Checking."),
    (re.compile(r"\b(?:my\s+)?ip(?:\s+address)?\b", re.I),
     "sys.ip", lambda m: {},
     "Checking."),
    (re.compile(r"\bwi\s?-?fi\b|\bwireless\s+networks?\b", re.I),
     "sys.wifi", lambda m: {},
     "Scanning."),
    (re.compile(r"\b(?:go\s+to\s+sleep|sleep\s+the\s+(?:machine|laptop|pc|computer)|suspend)\b", re.I),
     "sys.sleep", lambda m: {},
     "Sleeping."),

    # ── VOLUME AND MEDIA. These existed as coarse keyword rules further down
    #    intents.py and "turn it up" — the single most natural way to say it —
    #    matched none of them. Routed here instead so they go through the
    #    registry and pick up the tier and the audit entry with everything else.
    #
    #    "stop" is deliberately absent from the media verbs. It is the STOP
    #    intent, it halts her speech, and it is the one word he uses when
    #    something has gone wrong. Overloading it onto the media keys would
    #    make his interrupt sometimes pause Spotify instead.
    (re.compile(r"\b(?:turn\s+(?:it|the\s+(?:volume|sound))\s+up|volume\s+up|louder|"
                r"crank\s+it|turn\s+it\s+up)\b", re.I),
     "sys.volume", lambda m: {"direction": "up"},
     "Up."),
    (re.compile(r"\b(?:turn\s+(?:it|the\s+(?:volume|sound))\s+down|volume\s+down|quieter|"
                r"turn\s+it\s+down)\b", re.I),
     "sys.volume", lambda m: {"direction": "down"},
     "Down."),
    (re.compile(r"\b(?:mute|unmute|silence\s+it)\b", re.I),
     "sys.volume", lambda m: {"direction": "mute"},
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
    (re.compile(r"\b(?:make|create|new)\b.*\bfolder\b.*?\b(?:called|named)\s+(?P<name>.+)$", re.I),
     "fs.create", lambda m: {"path": str(Path.home() / "Documents" / _clean(m["name"]))},
     "Making it."),

    # VETOED ON `disk` AND `drive`. "How much space is on my disk" is a
    # question about the volume and `sys.disk` owns it; without this veto the
    # folder rule matched first and tried to size a folder called "on my disk".
    (re.compile(r"\b(?:how\s+big|how\s+much\s+space|size)\b(?!.*\b(?:disk|drive|c\s+drive)\b)"
                r".*?\b(?:is|of|does)?\s*(?:my\s+|the\s+)?"
                r"(?P<what>[\w:\\ .\-]+?)(?:\s+folder)?(?:\s+us(?:e|ing))?\s*$", re.I),
     "fs.usage", lambda m: {"path": _need_target(m["what"])},
     "Adding it up."),

    (re.compile(r"\b(?:find|search for|look for|where is)\b\s+(?:a\s+|the\s+|my\s+|any\s+)?"
                r"(?:file|folder)?\s*(?:called|named)?\s*(?P<name>.+)$", re.I),
     "fs.search", lambda m: {"name": _clean(m["name"])},
     "Searching."),

    (re.compile(r"(?!.*\b(?:windows|processes)\b)"
                rf"(?:{_WHATS}\s+in\s+(?:my\s+|the\s+)?(?P<what>.+)$|"
                r"\blist\s+(?:my\s+|the\s+)?(?P<what2>.+)$|"
                r"\bwhat\s+have\s+i\s+got\s+in\s+(?:my\s+|the\s+)?(?P<what3>.+)$)", re.I),
     "fs.list", lambda m: {"path": _need_target(m["what"] or m["what2"] or m["what3"])},
     "Looking."),

    (re.compile(r"\bread\s+(?:me\s+)?(?:the\s+|that\s+|my\s+)?(?P<what>.+)$", re.I),
     "fs.read", lambda m: {"path": _need_target(m["what"])},
     "Reading it."),

    (re.compile(r"\b(?:reveal|show me)\b.*\bin\s+explorer\b|\breveal\s+(?P<what>.+)$", re.I),
     "fs.reveal", lambda m: {"path": _need_target(m["what"] or "")},
     "Showing you."),

    # ── WINDOWS. `close` and `minimise` name a WINDOW, never a process.
    #    `go to` is deliberately NOT a focus verb: "go to my downloads" is a
    #    folder in his vocabulary and always has been, and stealing it here
    #    would have broken a phrase that already worked.
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
        from core.tools import REGISTRY

        spec = REGISTRY.get(name)
        tier = spec.tier if spec else "green"
        return ToolCall(name=name, args=args, tier=tier, speech=opener)
    return None
