"""
core/brain/intents.py — the local intent surface: parse an utterance into
tool NAME + structured ARGS, or say it missed.

WHAT THIS IS FOR

Everything here is free, offline, and instant. Opening a folder must not cost
₦0.05 and must not stop working when the connection does. The model is for
judgement — summarising, teaching, mathematics, reasoning. It is not for
`os.startfile`.

WHAT IT REFUSES TO DO

Guess. Spec §Q says she asks rather than guesses, so when two applications match
equally well she names them and asks which. A launcher that opens the wrong
thing confidently is worse than one that asks, because the wrong thing has
already happened by the time he notices.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import phrasings
from .tools_local import (
    _BROWSERS,
    _KNOWN_FOLDERS,
    ToolCall,
    folder_for,
    folder_stem,
    fuzzy_match,
    index_start_menu,
)


@dataclass
class Parse:
    """One utterance may contain several calls — see `split_clauses`."""
    calls: list[ToolCall] = field(default_factory=list)
    #: Set when she must ask instead of act (spec §Q).
    question: str | None = None
    unrouted_text: str | None = None
    #: WHY THE FAST PATH IS NOT SURE OF ITS OWN ANSWER (intent round,
    #: 2026-09-22): "ambiguous app: Xagent, Xftp, Xshell" or "fuzzy app:
    #: feedback hub". Empty when it is sure. The router carries it on
    #: `Routed.doubt`, and the brain is asked to confirm or correct before
    #: anything runs — see core/brain/intent_model.py. Nothing reads it to
    #: decide whether a tool is ALLOWED; it only decides whether the parse
    #: is TRUSTED.
    doubt: str = ""
    plan: list["PlanStep"] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.calls) and self.question is None


# ── greeting ─────────────────────────────────────────────────────────────────
#
# HIS BOUNDARIES AND HIS WORDS. Local, always — a greeting is not worth a round
# trip and must never be one. Goodnight is NOT here on purpose: it fires only
# when he says he is going to sleep, never on a clock, because a machine that
# tells you to go to bed on a timer is a machine you turn off.

_MORNING = ["Good morning, Emperor.", "Morning, Emperor.", "Good morning, Emperor. Ready when you are."]
_AFTERNOON = ["Good afternoon, Emperor.", "Afternoon, Emperor.", "Good afternoon, Emperor. What are we on?"]
_EVENING = ["Good evening, Emperor.", "Evening, Emperor.", "Good evening, Emperor. Still going?"]


def greeting(now: datetime | None = None, variant: int = 0, late_fact: str | None = None) -> str:
    """
    `late_fact` is passed in ONLY when it is true. She never invents a reason to
    have noticed something — an assistant that fabricates a small observation
    has taught you not to trust the large ones.
    """
    now = now or datetime.now()
    h = now.hour
    if 0 <= h < 5:
        base = f"It is {now.strftime('%I:%M %p').lstrip('0')}, Emperor."
        return f"{base} {late_fact}" if late_fact else f"{base} You are up late."
    if h < 12:
        pool = _MORNING
    elif h < 16:
        pool = _AFTERNOON
    else:
        pool = _EVENING
    out = pool[variant % len(pool)]
    return f"{out} {late_fact}" if late_fact else out


# ── clause splitting, so one utterance can carry two jobs ────────────────────

# "download and install vlc" is ONE job (software-change round, 2026-09-12):
# before the two lookbehinds it split into "download" — which routed to the
# Downloads FOLDER — and "install vlc". Both lookbehinds are needed: the first
# blocks a match that starts on the space after "download", the second one
# that starts on the "and" itself.
_CONNECTORS = re.compile(
    r"(?<!download)(?<!download\s)\s*(?:,\s*)?\b(?:and then|then|and also|and|also)\b\s+", re.I)


#: Self-correction mid-sentence. Speech is not typing — he changes his mind
#: halfway through and there is no backspace. "open Chrome... actually open VS
#: Code" is ONE instruction whose operative half is the second one, and treating
#: it as two would open Chrome he did not want.
_CORRECTIONS = re.compile(
    r"\b(?:actually|no wait|wait no|scratch that|i mean|rather|instead|sorry)\b", re.I)

#: A PRIVATE MESSAGE IS ONE JOB (X direct messages, round 4, 2026-09-12). The
#: words after "saying" ARE the message: "dm ada saying see you at six and
#: bring the papers" is one message, not a DM plus a window switch; and "dm
#: @ada and @bob saying hi" must reach the DM rule WHOLE, so it is refused as
#: bulk, rather than arrive as "dm @ada" and be sent to the first name with
#: nothing. Anchored to the verbs the DM rules own (core/brain/phrasings.py);
#: "send the file to bob and open notepad" is not matched and splits as before.
_ONE_JOB = re.compile(
    r"^\s*(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?)?"
    r"(?:dm|d\.m\.|direct[- ]message|private[- ]message|"
    r"message\s+@?[A-Za-z0-9_]{1,15}\s*(?:on\s+(?:x|twitter)\s*)?(?:saying|with|that\s+says|:|,)|"
    r"send\s+(?:@?[A-Za-z0-9_]{1,15}\s+)?(?:a\s+)?(?:dm|d\.m\.|direct\s+message|private\s+message|message)\b|"
    r"(?:reply|respond|answer)\s+(?:to\s+)?@?[A-Za-z0-9_]{1,15}(?:'s|s'|’s)\s+"
    r"(?:dm|d\.m\.|direct\s+message|private\s+message|message))(?:\b|(?<=[:,]))", re.I)

#: A message TO HIM is not a private message he is sending: "dm me the link",
#: "send me a message when it's done", "message us" are requests, and stay on
#: the ordinary path.
_TO_SELF = re.compile(
    r"^\s*(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?)?(?:please\s+)?"
    r"(?:send|dm|d\.m\.|direct[- ]message|private[- ]message|message)\s+(?:me|us)\b", re.I)


#: What she says to a private-message SHAPE the DM rules could not place. A
#: dictated DM never goes to the brain — his private words to a cloud model,
#: and into the thread on disk — so she names the shape she needs instead.
#: Shared by the voice loop and the typed path (intent round, 2026-09-22): the
#: sentence lived only in core/voice/loop.py, and the typed path had no guard.
PRIVATE_UNROUTED = ("That sounded like a private message, Emperor, and I could not tell "
                    "who it was for. Say DM, one @name, then saying, and the words.")


def is_private_utterance(text: str) -> bool:
    """
    True when an utterance has the SHAPE of a private message he is sending
    (X direct messages, round 4, 2026-09-12): it starts with a DM verb the DM
    rules own (`_ONE_JOB`) and is not a message to himself. The voice loop
    and the daemon use this for what they LOG about a turn, never for what
    they do — a dictated private message must not land in the daemon log,
    on the chain or in a cloud model's context because the router happened
    to miss it ("direct message ada bob saying …"). Routing is unchanged.
    """
    # The spoken lead comes off first, as it does before routing: "Tessa,
    # please dm ada saying hi" is the same shape as "dm ada saying hi".
    t = strip_lead(str(text or ""))
    return bool(_ONE_JOB.match(t)) and not bool(_TO_SELF.match(t))


def split_clauses(text: str) -> list[str]:
    """
    "open my Tessa console and check my node version" -> two clauses.

    Deliberately naive, and bounded to 3: a real conjunction parser would start
    splitting "node and npm" into two jobs. Anything it gets wrong falls through
    to UNROUTED, which is visible, rather than into a wrong action, which is not.

    A self-correction wins outright — everything before it is discarded, because
    that is what he meant by saying it.
    """
    if _ONE_JOB.match(text):
        # A self-correction inside a private message stays inside it ("dm
        # ada saying sorry I'm late" is the message) unless the correction
        # itself starts a new one ("... actually dm bob saying hi").
        if _CORRECTIONS.search(text):
            tail = _CORRECTIONS.split(text)[-1].strip(" ,.")
            if tail and _ONE_JOB.match(tail):
                return [tail]
        return [text.strip()]
    if _CORRECTIONS.search(text):
        tail = _CORRECTIONS.split(text)[-1].strip(" ,.")
        if tail:
            text = tail
    parts = [p.strip(" ,.") for p in _CONNECTORS.split(text) if p and p.strip(" ,.")]
    return parts[:3] if len(parts) > 1 else [text.strip()]


_LEADS = re.compile(
    r"^(?:hello|hey|hi|ok|okay|please|tessa|hello tessa|hey tessa|ok tessa)\b[\s,]*", re.I)


def strip_lead(text: str) -> str:
    prev = None
    t = text.strip()
    while prev != t:
        prev = t
        t = _LEADS.sub("", t).strip()
    return t


# ── one sentence, one plan (the sentences round, 2026-10-06) ─────────────────

#: A CLAUSE THAT ONLY ASKS TO BE TOLD THE RESULT of the one before it — "and
#: tell me what's in the bio", "and read it to me", "and let me know". His U4
#: ("read @mcityXtra_ bio and tell me what's in the bio") split into two
#: commands and she answered twice. The action's own answer IS the report, so
#: a tail like this is dropped — never the first clause, and only the shapes
#: that point BACK at it: "and tell me what's in my downloads" names its own
#: target and stays a command.
_REPORT_TAIL = re.compile(
    r"^(?:(?:and|then|also)\s+)*(?:"
    r"(?:tell|show|give)\s+me\s+(?:what\s+(?:it|they|that|she|he)\s+says?|"
    r"what\s+you\s+(?:find|see|get|got|found)|"
    r"what(?:'s|\s+is)\s+(?:in|on)\s+(?:it|there|them|the\s+(?:bio|profile|page|post|tweet))|"
    r"about\s+(?:it|them|that)|the\s+(?:result|answer|bio|details|gist)|"
    r"if\s+it\b|whether\s+it\b)|"
    r"read\s+(?:it|that|them|this)\s+(?:out\s+)?(?:to\s+me|aloud|out|back)|"
    r"let\s+me\s+know|say\s+what\s+(?:it|they)\s+says?|report\s+back)\b", re.I)

#: The X tools whose every call HOLDS or raises a card. One hold at a time is
#: the ledger's rule (core/brain/confirm.py), so a sentence with two of these
#: runs the first and NAMES the rest.
_X_WRITES = frozenset({
    "x.follow", "x.unfollow", "x.like", "x.unlike", "x.repost", "x.unrepost",
    "x.bookmark", "x.unbookmark", "x.post", "x.reply", "x.quote", "x.thread", "x.send_dm",
})
MAX_PLAN_WRITES = 5


@dataclass
class PlanStep:
    call: ToolCall
    clause: str
    count: int = 1


def _plan(out: "Parse", clauses: list[str]) -> None:
    """
    Run only up to the FIRST X write; name the rest in one line (`preface`)
    and hand him the words for the next step (`after`). A like with a count
    ("their first 3 post") is the same plan on its own: one hold now, the
    next one named. Every other sentence is left exactly as it was parsed.
    """
    counts = [1] * len(out.calls)
    for i, c in enumerate(out.calls):
        if c.name == "x.like" and "count" in (c.args or {}):
            counts[i] = max(1, int(c.args.pop("count") or 0))
    writes = [i for i, c in enumerate(out.calls) if c.name in _X_WRITES]
    total = sum(counts[i] for i in writes)
    by_author = any(c.name == "x.reply" and (c.args or {}).get("author") for c in out.calls)
    if len(writes) < 2 and total < 2 and not by_author:
        return
    if total > MAX_PLAN_WRITES:
        out.calls = []
        out.question = (f"That is {total} actions on X in one sentence, Emperor. I take at most "
                        f"{MAX_PLAN_WRITES} per sentence, each with its own yes, so I did nothing. "
                        f"Ask for {MAX_PLAN_WRITES} or fewer.")
        return
    first = writes[0]
    out.plan = [PlanStep(c, clauses[i], counts[i]) for i, c in enumerate(out.calls) if i >= first]
    out.calls = out.calls[:first]


#: "open VS Code and Spotify": a later clause with no verb of its own borrows
#: the first clause's launch verb. Only these three — a launch is green and
#: reversible; "delete x and y" must never grow a second delete this way.
_LAUNCH_VERB = re.compile(r"^\s*(open|launch|start)\b", re.I)
_ANY_VERB = re.compile(
    r"^\s*(?:open|launch|start|close|read|show|play|pause|stop|kill|delete|remove|move|copy|rename|make|"
    r"create|find|search|like|unlike|follow|unfollow|post|tweet|reply|send|dm|message|set|turn|tell|give|"
    r"what|who|how|check|save|download|share|bookmark|repost|retweet|run|type|click|lock|go|take|switch|"
    r"bring|focus|minimi[sz]e|maximi[sz]e|restore|snap|mute|unmute|install|uninstall|shut|restart|sleep|"
    r"hibernate|log|sign|list|reveal|screenshot|record)\b", re.I)


# ── the parser ───────────────────────────────────────────────────────────────

_VERSION_TOOLS = {"node": "node", "npm": "npm", "python": "python", "git": "git"}


def _is_bare_folder(c: str) -> bool:
    """The whole utterance IS a folder name — "downloads", or "photos", alone.

    Deliberately an equality test and not `folder_for(c) is not None`: the
    latter would open C:\\dev on "what's the dev version" and his home folder on
    "I am working from home". A bare noun is a command; a noun in a sentence
    needs a verb in front of it.

    `folder_stem` is imported rather than redefined. It was briefly declared in
    two files, each carrying a comment claiming to be the only one — which is
    how the plural handling drifted apart the first time.
    """
    from .tools_local import _FOLDER_ALIASES

    if c in _FOLDER_ALIASES:
        return True
    return any(c == name or c == folder_stem(name) for name in _KNOWN_FOLDERS)


#: A path he SAID, or a drive letter. Item 1g.
#:
#: Three shapes, because he says all three: a full path with a drive, a UNC
#: share, and a bare drive letter ("open D drive", "open the D drive").
_PATH_RE = re.compile(r"([a-zA-Z]:[\\/][^\s\"']*|\\\\[^\s\"']+)")
_DRIVE_RE = re.compile(r"\b(?:drive\s+([a-zA-Z])|([a-zA-Z])\s*(?::|\s)\s*drive)\b", re.I)


def _explicit_path(text: str) -> Path | None:
    """
    A literal path or drive in what he said, or None.

    Returned WITHOUT checking existence, so the caller can tell him the path is
    missing rather than falling through to a fuzzy application match — which
    would be the worst outcome: he names a folder that is not there and she
    opens an unrelated program whose name happens to be close.
    """
    m = _PATH_RE.search(text or "")
    if m:
        return Path(m.group(1).rstrip(" .,!?"))
    m = _DRIVE_RE.search(text or "")
    if m:
        letter = (m.group(1) or m.group(2) or "").upper()
        if letter:
            return Path(f"{letter}:\\")
    return None


class IntentParser:
    def __init__(self) -> None:
        # ONE SHARED INDEX for the process, built lazily and cached to disk.
        # See core/brain/appindex.py for what it covers and what it costs.
        from .appindex import get_index
        self._index = get_index()
        #: Set per clause by `_parse_one`, read by `parse` — see `Parse.doubt`.
        self.doubt = ""

    @property
    def apps(self) -> dict[str, Path]:
        """Back-compat view for anything still expecting the old dict."""
        return {e.key: Path(e.launch) for e in self._index.entries}

    def parse(self, utterance: str) -> Parse:
        out = Parse()
        # A TRAILING "and tell me what's in it" REPORTS the action before it;
        # it is not a second command (see `_REPORT_TAIL`). Never the first.
        clauses = [c for i, c in enumerate(split_clauses(strip_lead(utterance)))
                   if i == 0 or not _REPORT_TAIL.match(c)]
        parsed: list[str] = []
        lead = _LAUNCH_VERB.match(clauses[0]) if clauses else None
        for i, clause in enumerate(clauses):
            self.doubt = ""
            call, question = self._parse_one(clause)
            if (call is None and question is None and i > 0 and lead is not None
                    and not _ANY_VERB.match(clause)):
                # "open VS Code and Spotify" — the second clause has no verb of
                # its own; it borrows the first one's open/launch/start. It was
                # dropped silently before (one launch, not two).
                call, question = self._parse_one(f"{lead.group(1)} {clause}")
            if self.doubt and not out.doubt:
                out.doubt = self.doubt
            if question:
                out.question = question
                return out
            if call is None:
                out.unrouted_text = clause
                break
            out.calls.append(call)
            parsed.append(clause)
        _plan(out, parsed)
        return out

    def _parse_one(self, clause: str) -> tuple[ToolCall | None, str | None]:
        c = clause.lower().strip(" .?!")

        # ── versions ────────────────────────────────────────────────────────
        m = re.search(r"\b(node|npm|python|git)\b.*\bversion\b|\bversion of\s+(node|npm|python|git)\b", c)
        if m:
            tool = m.group(1) or m.group(2)
            return ToolCall("sys.tool_version", {"tool": _VERSION_TOOLS[tool]},
                            speech=f"Checking {tool}."), None

        # ── ports ───────────────────────────────────────────────────────────
        m = re.search(r"\bport\s+(\d{2,5})\b", c)
        if m:
            port = int(m.group(1))
            if re.search(r"\b(kill|stop|end|terminate|free)\b", c):
                return ToolCall("sys.kill_port", {"port": port}, tier="amber",
                                speech=f"That will kill whatever holds port {port}."), None
            return ToolCall("sys.port_owner", {"port": port},
                            speech=f"Checking port {port}."), None

        # ── THE WINDOWS TOOL SURFACE ────────────────────────────────────────
        #
        # Placed AFTER versions and ports and BEFORE the coarse machine-state
        # keywords, and the position is the whole design. The two rules above
        # are extremely specific (a named tool plus the word "version"; the
        # word "port" plus a number) and must not be shadowed. Everything
        # below is broad keyword matching that WOULD shadow the new surface —
        # `\b(disk|storage|space|drive)\b` alone would swallow "how much space
        # is my downloads folder using" and answer about the C: volume.
        call = phrasings.match(clause)
        if call is not None:
            return call, None

        # ── AN EXPLICIT PATH OR DRIVE HE ASKED TO OPEN ──────────────────────
        #
        # BEFORE the machine-state keywords, and that position is the fix. The
        # `\b(disk|storage|space|drive)\b` rule below is deliberately broad, and
        # it swallowed "open the D drive" and answered with free space on C:.
        # He asked her to OPEN something; an open verb plus a drive letter is
        # not a question about storage.
        #
        # Guarded on the verb so "how much space is on my D drive" still reaches
        # sys.disk, which is what that broad rule is for.
        if re.search(r"\b(open|show|go to|take me to|browse)\b", c):
            _p = _explicit_path(clause)
            if _p is not None:
                if _p.exists():
                    return ToolCall("app.open_folder", {"path": str(_p)},
                                    speech="Opening it."), None
                return None, (f"There is nothing at {_p}, Emperor. "
                              f"Check the path and say it again.")

        # ── machine state ───────────────────────────────────────────────────
        #
        # "drive" is also half a product name. "open google drive" and "open
        # onedrive" were reporting free space on C:, the same collision that
        # made "open Google Chrome" run a web search. The negative lookbehind
        # excludes the products; every real storage question still lands here.
        if re.search(r"\b(disk|storage|space|(?<!google )(?<!one )(?<!sky )drive)\b", c):
            return ToolCall("sys.disk", speech="Checking disk."), None
        if re.search(r"\b(ram|memory)\b", c):
            return ToolCall("sys.memory", speech="Checking memory."), None
        if re.search(r"\bbattery\b", c):
            return ToolCall("sys.battery", speech="Checking battery."), None
        if re.search(r"\buptime\b", c):
            return ToolCall("sys.uptime", speech="Checking uptime."), None
        if re.search(r"\b(top|heaviest|biggest)\b.*\bprocess", c):
            return ToolCall("sys.top_processes", {"n": 5}, speech="Looking."), None
        if re.search(r"\bprocess(es)?\b|\bwhat.s running\b", c):
            return ToolCall("sys.process_list", speech="Looking."), None

        # ── media and machine control ───────────────────────────────────────
        if re.search(r"\b(volume|sound)\b.*\bup\b|\blouder\b", c):
            return ToolCall("sys.volume", {"direction": "up"}, speech="Up."), None
        if re.search(r"\b(volume|sound)\b.*\bdown\b|\bquieter\b", c):
            return ToolCall("sys.volume", {"direction": "down"}, speech="Down."), None
        if re.search(r"\bmute\b", c):
            return ToolCall("sys.volume", {"direction": "mute"}, speech="Muted."), None
        if re.search(r"\b(play|pause)\b", c):
            return ToolCall("sys.media", {"action": "playpause"}, speech="Done."), None
        if re.search(r"\bnext (track|song)\b|\bskip\b", c):
            return ToolCall("sys.media", {"action": "next"}, speech="Skipped."), None
        if re.search(r"\block (the )?(machine|screen|pc|computer)\b", c):
            return ToolCall("sys.lock", speech="Locking."), None

        # ── URLs ────────────────────────────────────────────────────────────
        m = re.search(r"\b(https?://\S+|(?:www\.)[\w.-]+\.\w{2,})", clause)
        if m:
            url = m.group(1)
            url = url if url.startswith("http") else f"https://{url}"
            browser = next((b for b in _BROWSERS if b in c), None)
            return ToolCall("app.open_url", {"url": url, "browser": browser},
                            speech="Opening it."), None

        # ── VS Code ─────────────────────────────────────────────────────────
        #
        # With a folder, open that folder IN VS Code. Without one, open the
        # editor itself — "open VS Code" is a complete instruction and it
        # previously fell through to a fuzzy app match that scored 0.56 against
        # "Visual Studio Code" and lost, so she said she could not do it.
        if re.search(r"\b(vs ?code|visual studio code|vscode|in code)\b", c):
            target = self._folder_from(c)
            if target is not None:
                return ToolCall("app.open_vscode", {"path": str(target)},
                                speech="Opening in VS Code."), None
            return ToolCall("app.open_vscode", {"path": ""},
                            speech="Opening VS Code."), None

        # ── folders ─────────────────────────────────────────────────────────
        if re.search(r"\b(open|show|go to|take me to)\b", c) or _is_bare_folder(c):
            target = self._folder_from(c)
            if target is not None:
                return ToolCall("app.open_folder", {"path": str(target)},
                                speech="Opening it."), None

            # ── an EXPLICIT PATH or DRIVE, before the app index ─────────────
            #
            # Item 1g: "everything on my system" is not only applications. A
            # literal path he names must open as itself and never be fuzzy
            # matched against an application — "open C:\\dev\\tessa" hunting the
            # Start Menu for something called "dev tessa" would be absurd.
            explicit = _explicit_path(clause)
            if explicit is not None:
                if explicit.exists():
                    return ToolCall("app.open_folder", {"path": str(explicit)},
                                    speech="Opening it."), None
                # AN HONEST MISS, NAMED. Silence here is the failure mode this
                # codebase keeps designing against, and a wrong guess is worse:
                # she must not open something else because the thing he asked
                # for is absent.
                return None, (f"There is nothing at {explicit}, Emperor. "
                              f"Check the path and say it again.")

            # ── applications ────────────────────────────────────────────────
            #
            # `appindex.resolve` does its own query normalisation — including
            # dropping category nouns like "browser", which is the whole of the
            # Chrome fix — so nothing is stripped here first.
            entries, how = self._index.resolve(clause)
            if len(entries) == 1:
                e = entries[0]
                if how.startswith("fuzzy"):
                    # A RATIO IS A GUESS, NOT EVIDENCE (appindex._score). "show
                    # my feed" fuzzy-matched Feedback Hub at 0.75. The launch is
                    # still parsed — the brain confirms or corrects it first.
                    self.doubt = f"fuzzy app: {e.name}"
                return ToolCall("app.open", {"app": e.key, "match": how},
                                speech=f"Opening {e.name}."), None
            if len(entries) > 1:
                names = ", ".join(e.name for e in entries[:3])
                # "open X" scored Xagent, Xftp and Xshell as equals because
                # "x" is a prefix of all three. The question below is what she
                # asks when there is no brain to ask; with one, the sentence
                # decides — see intent_model.second_opinion.
                self.doubt = f"ambiguous app: {names}"
                return None, (
                    f"I found more than one. Did you mean {names}?"
                )
        return None, None

    @staticmethod
    def _folder_from(c: str) -> Path | None:
        # OPTIONAL TRAILING 's' AND SPOKEN ALIASES, both from `folder_for` so
        # there is ONE resolver rather than two that drift. Whisper dropped the
        # plural on a real turn — "Open my download." for "open my downloads" —
        # and the folder then failed to match, so a command that had worked a
        # minute earlier became UNROUTED. Aliases are the same failure one step
        # out: he says photos, the folder is called Pictures.
        hit = folder_for(c)
        if hit is not None:
            return hit
        m = re.search(r"([a-z]:\\[^\s\"']+)", c, re.I)
        if m:
            p = Path(m.group(1))
            return p if p.exists() else None
        return None
