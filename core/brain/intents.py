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

from .tools_local import (
    _BROWSERS,
    _KNOWN_FOLDERS,
    ToolCall,
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

_CONNECTORS = re.compile(r"\s*(?:,\s*)?\b(?:and then|then|and also|and|also)\b\s+", re.I)


#: Self-correction mid-sentence. Speech is not typing — he changes his mind
#: halfway through and there is no backspace. "open Chrome... actually open VS
#: Code" is ONE instruction whose operative half is the second one, and treating
#: it as two would open Chrome he did not want.
_CORRECTIONS = re.compile(
    r"\b(?:actually|no wait|wait no|scratch that|i mean|rather|instead|sorry)\b", re.I)


def split_clauses(text: str) -> list[str]:
    """
    "open my Zoey console and check my node version" -> two clauses.

    Deliberately naive, and bounded to 3: a real conjunction parser would start
    splitting "node and npm" into two jobs. Anything it gets wrong falls through
    to UNROUTED, which is visible, rather than into a wrong action, which is not.

    A self-correction wins outright — everything before it is discarded, because
    that is what he meant by saying it.
    """
    if _CORRECTIONS.search(text):
        tail = _CORRECTIONS.split(text)[-1].strip(" ,.")
        if tail:
            text = tail
    parts = [p.strip(" ,.") for p in _CONNECTORS.split(text) if p and p.strip(" ,.")]
    return parts[:3] if len(parts) > 1 else [text.strip()]


_LEADS = re.compile(
    r"^(?:hello|hey|hi|ok|okay|please|zoey|hello zoey|hey zoey|ok zoey)\b[\s,]*", re.I)


def strip_lead(text: str) -> str:
    prev = None
    t = text.strip()
    while prev != t:
        prev = t
        t = _LEADS.sub("", t).strip()
    return t


# ── the parser ───────────────────────────────────────────────────────────────

_VERSION_TOOLS = {"node": "node", "npm": "npm", "python": "python", "git": "git"}


class IntentParser:
    def __init__(self) -> None:
        self._apps: dict[str, Path] | None = None

    @property
    def apps(self) -> dict[str, Path]:
        if self._apps is None:
            self._apps = index_start_menu()
        return self._apps

    def parse(self, utterance: str) -> Parse:
        out = Parse()
        for clause in split_clauses(strip_lead(utterance)):
            call, question = self._parse_one(clause)
            if question:
                out.question = question
                return out
            if call is None:
                out.unrouted_text = clause
                return out
            out.calls.append(call)
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

        # ── machine state ───────────────────────────────────────────────────
        if re.search(r"\b(disk|storage|space|drive)\b", c):
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
        if re.search(r"\b(open|show|go to|take me to)\b", c) or c in _KNOWN_FOLDERS:
            target = self._folder_from(c)
            if target is not None:
                return ToolCall("app.open_folder", {"path": str(target)},
                                speech="Opening it."), None

            # ── applications, fuzzy ─────────────────────────────────────────
            q = re.sub(r"\b(open|launch|start|run|show|my|the|app|application|please)\b", " ", c)
            q = " ".join(q.split())
            if q:
                keys, how = fuzzy_match(q, self.apps)
                if len(keys) == 1:
                    return ToolCall("app.open", {"app": keys[0], "match": how},
                                    speech=f"Opening {keys[0]}."), None
                if len(keys) > 1:
                    names = ", ".join(k.title() for k in keys[:3])
                    return None, (
                        f"I found more than one. Did you mean {names}?"
                    )
        return None, None

    @staticmethod
    def _folder_from(c: str) -> Path | None:
        for name, path in _KNOWN_FOLDERS.items():
            if re.search(rf"\b{re.escape(name)}\b", c):
                return path
        m = re.search(r"([a-z]:\\[^\s\"']+)", c, re.I)
        if m:
            p = Path(m.group(1))
            return p if p.exists() else None
        return None
