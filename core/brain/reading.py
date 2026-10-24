from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .tools_local import _FOLDER_ALIASES, _KNOWN_FOLDERS, folder_stem

EMAIL_LINE = "I cannot read your email yet, Emperor. There is no mail connection on this machine."
EMAIL_REPLY_LINE = "I cannot read your email yet, Emperor, so I cannot draft a reply to it."
REFERENT_TTL_S = 20 * 60
HISTORY_LINES = 6

_HER_NAMES = ("tessa", "zoi", "zoe")
_DETS = r"(?:the|my|your|our|this|that|his|her|a|an)"
_FOLDER_NOUN = r"(?:folder|directory|dir|repo|repository|project)"
_PREPS = r"on|in|inside|within|from|into|onto|to|under|at|off|out\s+of"
_UI_PLACES = {"taskbar": "the taskbar", "task bar": "the taskbar", "tray": "the tray", "system tray": "the tray",
              "notification area": "the tray", "start menu": "the start menu", "screen": "the screen",
              "background": "the background", "file explorer": "explorer", "explorer": "explorer"}
_TERMINALS = re.compile(r"\s*,?\s+(?:in|inside|on|from|using|with)\s+(?:the\s+|a\s+|my\s+)?"
                        r"(?:terminal|powershell|power\s+shell|cmd|command\s+prompt|console|shell)\s*$", re.I)
_TAIL = re.compile(r"(?:[\s,]+(?:please|pls|abeg|for\s+me|thanks|thank\s+you|"
                   r"(?<!\bthe )(?<!\bmy )(?<!\byour )(?<!\bcalled )(?<!\bnamed )(?:tessa|zoi|zoe)))+\s*[.!?]*\s*$", re.I)
_HEAD = re.compile(r"^(?:tessa|zoi|zoe)\b(?!\s+(?:folder|directory|dir|repo|repository|project|console|os|orb|"
                   r"core)\b)[\s,]*", re.I)
_PATH_LIT = re.compile(r"([a-zA-Z]:[\\/][^\s\"']*|\\\\[^\s\"']+)")
_DRIVE = re.compile(r"^(?:the\s+)?(?:(?P<a>[a-z])\s*(?::|\s)\s*drive|drive\s+(?P<b>[a-z])|(?P<c>[a-z]):)$", re.I)
_REFERENT = re.compile(r"^(?:it|that|this|them|those|these|one|thing|file|folder|directory|document|doc|window|app|"
                       r"program|that\s+one|this\s+one|the\s+(?:same\s+)?(?:one|thing)|"
                       r"(?:the|that|this|same)\s+(?:file|document|doc|folder|directory|window|app|program|"
                       r"picture|photo|video|one|thing))$", re.I)
_BULK = re.compile(r"^(?:everything|all|all\s+of\s+(?:it|them)|the\s+(?:whole\s+)?lot|its\s+contents|the\s+contents|"
                   r"all\s+(?:the\s+|my\s+)?(?:files|things|stuff|contents)|(?:the\s+)?(?:whole|entire)\s+(?:thing|lot))$",
                   re.I)
_FILEISH = re.compile(r"\.[A-Za-z0-9]{1,6}$")
_FILE_TOKEN = re.compile(r"(?<![\w\\/.-])([\w()-]+(?:\.[\w()-]+)*\.[A-Za-z0-9]{1,6})\b")
_EMAIL = re.compile(r"\b(?:inbox|mail\s*box)\b|"
                    r"\b(?:read|check|any|new|unread|got|have|show|open|list|what(?:'s|\s+is)\s+in)\b[^.]*"
                    r"\b(?:e-?mails?|mail|gmail|outlook)\b|"
                    r"\b(?:e-?mails?|gmail|outlook)\s+(?:inbox|today|from)\b", re.I)
_X_WORDS = re.compile(r"\b(?:x|twitter|tweets?|dms?|d\.m\.s?|direct\s+messages?)\b", re.I)
_HER_SLEEP = re.compile(
    r"\b(?:(?:go|head|get|run)\s+(?:on\s+)?(?:back\s+)?to\s+(?:bed|sleep)|back\s+to\s+sleep|get\s+some\s+(?:sleep|rest)|"
    r"you\s+(?:can|may|could|should|must|might\s+as\s+well)\s+(?:go\s+)?(?:back\s+)?(?:to\s+)?sleep|"
    r"time\s+(?:for\s+you\s+)?to\s+sleep|go\s+(?:and\s+)?sleep|sleep\s+now|"
    r"(?:tessa|zoi|zoe)[\s,]+(?:go\s+)?sleep|sleep[\s,]+(?:tessa|zoi|zoe))\b", re.I)
_MACHINE = re.compile(r"\b(?:laptop|computer|pc|machine|system|windows|screen|monitor|display|desktop|box|"
                      r"suspend|hibernat\w*|sleep\s+mode|standby)\b", re.I)
_HOURS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
          "ten": 10, "eleven": 11, "twelve": 12, "noon": 12, "midday": 12, "midnight": 0}
_MINUTE_WORDS = {"o'clock": 0, "oclock": 0, "fifteen": 15, "thirty": 30, "forty five": 45, "forty-five": 45}
_DAY = r"today|tonight|tomorrow(?:\s+(?:morning|afternoon|evening|night))?|this\s+(?:morning|afternoon|evening)"
_HOUR = r"\d{1,2}|" + "|".join(_HOURS)
_WHEN = re.compile(
    rf"(?:(?P<day1>{_DAY})\s+)?(?:(?:for|at|by|around)\s+)?(?P<h>{_HOUR})"
    rf"(?:(?::|\.)(?P<m>\d{{2}})|\s+(?P<mw>o'?clock|fifteen|thirty|forty[- ]five))?\s*"
    rf"(?P<ap>a\.?\s?m\.?|p\.?\s?m\.?)?(?:\s+(?P<day2>{_DAY}))?\s*$", re.I)
_REL = re.compile(r"^(?:in\s+)?(?P<n>\d{1,3}|an?|one|two|three|four|five|ten|fifteen|twenty|thirty|forty\s+five|"
                  r"half\s+an?)\s+(?P<u>minutes?|mins?|hours?|hrs?)$", re.I)
_REL_N = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "ten": 10, "fifteen": 15,
          "twenty": 20, "thirty": 30, "forty five": 45}


class Ask(Exception):
    def __init__(self, line: str) -> None:
        super().__init__(line)
        self.line = line


@dataclass(frozen=True)
class Referent:
    kind: str
    value: str
    at: float


_CTX = threading.local()


class context:
    def __init__(self, refs: list[Referent]) -> None:
        self.refs = list(refs)
        self.prev: list[Referent] | None = None

    def __enter__(self) -> "context":
        self.prev = getattr(_CTX, "refs", None)
        _CTX.refs = self.refs
        return self

    def __exit__(self, *exc: Any) -> None:
        _CTX.refs = self.prev


def referents() -> list[Referent]:
    return list(getattr(_CTX, "refs", None) or [])


def clean(text: Any) -> str:
    return " ".join(str(text or "").split()).strip(" .,?!\"'")


def unaddressed(phrase: Any) -> str:
    t = clean(phrase)
    prev = None
    while prev != t:
        prev = t
        t = clean(_TAIL.sub("", t))
        t = clean(_HEAD.sub("", t))
    return t


def nameable(value: str) -> bool:
    from .phrasings import _is_nameable
    return _is_nameable(value)


def folder_of(phrase: Any) -> Path | None:
    t = unaddressed(phrase).lower()
    if not t:
        return None
    if t in _FOLDER_ALIASES:
        return _KNOWN_FOLDERS[_FOLDER_ALIASES[t]]
    t = re.sub(rf"^{_DETS}\s+", "", t)
    t = re.sub(r"^(?:whole|entire|own)\s+", "", t)
    if t in _FOLDER_ALIASES:
        return _KNOWN_FOLDERS[_FOLDER_ALIASES[t]]
    noun = re.search(rf"\s+{_FOLDER_NOUN}$", t)
    core = t[:noun.start()].strip() if noun else t
    if core in _FOLDER_ALIASES and (noun or core not in _HER_NAMES):
        return _KNOWN_FOLDERS[_FOLDER_ALIASES[core]]
    for name, path in _KNOWN_FOLDERS.items():
        if core in (name, folder_stem(name)):
            if name in _HER_NAMES and not noun:
                return None
            return path
    return None


def drive_of(phrase: Any) -> Path | None:
    m = _DRIVE.match(unaddressed(phrase))
    if not m:
        return None
    letter = (m.group("a") or m.group("b") or m.group("c") or "").upper()
    return Path(f"{letter}:\\") if letter else None


def literal_path(phrase: Any) -> str:
    m = _PATH_LIT.search(str(phrase or ""))
    return m.group(1).rstrip(" .,!?") if m else ""


def place_of(phrase: Any) -> Path | None:
    lit = literal_path(phrase)
    if lit and clean(phrase).lower().replace("/", "\\").startswith(lit.lower().replace("/", "\\")):
        return Path(lit)
    return folder_of(phrase) or drive_of(phrase)


def ui_place(phrase: Any) -> str:
    t = re.sub(rf"^{_DETS}\s+", "", unaddressed(phrase).lower())
    return _UI_PLACES.get(t, "")


def split_place(phrase: Any, *, ui: bool = True) -> tuple[str, Path | None, str]:
    t = unaddressed(phrase)
    for m in reversed(list(re.finditer(rf"(?:^|[\s,]+)({_PREPS})\s+", t, re.I))):
        place = t[m.end():]
        rest = unaddressed(t[:m.start()])
        where = place_of(place)
        if where is not None:
            return rest, where, m.group(1).lower()
        if ui and ui_place(place):
            return rest, None, "ui"
    return t, None, ""


def bare_name(phrase: Any) -> str:
    t = unaddressed(phrase)
    t = re.sub(rf"^{_DETS}\s+", "", t, flags=re.I)
    t = re.sub(r"^(?:(?:file|folder|document|picture|photo|video)\s+)?(?:called|named)\s+", "", t, flags=re.I)
    t = re.sub(r"^(?:(?:the|a|an)\s+)?(?:file|folder|document)\s+(?=\S+\.[A-Za-z0-9]{1,6}$)", "", t, flags=re.I)
    m = re.match(r"^(?P<n>.+?)\s+(?:file|document|folder|directory)$", t, re.I)
    if m and (_FILEISH.search(m.group("n")) or m.group("n").lower() not in ("the", "my", "a")):
        t = m.group("n")
    return clean(t)


def is_referent(phrase: Any) -> bool:
    return bool(_REFERENT.match(unaddressed(phrase)))


def _wanted_kinds(phrase: str) -> tuple[str, ...]:
    t = phrase.lower()
    if re.search(r"\b(?:file|document|doc|picture|photo|video)\b", t):
        return ("file",)
    if re.search(r"\b(?:folder|directory)\b", t):
        return ("folder",)
    if re.search(r"\b(?:window|app|program)\b", t):
        return ("window",)
    return ("file", "folder")


def _question_for(kinds: tuple[str, ...]) -> str:
    if kinds == ("folder",):
        return "Which folder, Emperor?"
    if kinds == ("window",):
        return "Which window, Emperor?"
    if kinds == ("file",):
        return "Which file, Emperor?"
    return "Which file or folder, Emperor?"


def referent_value(phrase: Any, kinds: tuple[str, ...] | None = None) -> str:
    t = unaddressed(phrase)
    want = kinds or _wanted_kinds(t)
    refs = referents()
    if refs and refs[0].kind == "email":
        raise Ask(EMAIL_LINE)
    for r in refs:
        if r.kind in want and r.value:
            return r.value
    raise Ask(_question_for(want))


def _index_hits(name: str) -> list[str]:
    from core.system.winapi import fileindex
    try:
        hits = fileindex.get_index().find(name, limit=1_000_000)
    except Exception:
        return []
    low = name.lower()
    return [h for h in hits if Path(h).name.lower() == low]


def _where_words(paths: list[str]) -> str:
    names = []
    for p in paths[:3]:
        parent = Path(p).parent
        names.append(parent.name or str(parent))
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " or " + names[-1]


def find_one(name: str) -> str:
    hits = list(dict.fromkeys(_index_hits(name)))
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        more = f" I found {len(hits)} of them." if len(hits) > 3 else ""
        raise Ask(f"Which {name}, Emperor? The one in {_where_words(hits)}?{more}")
    raise Ask(f"I cannot find {name}, Emperor. Which folder is it in?")


def path_target(phrase: Any) -> str:
    t = unaddressed(phrase)
    if not t:
        raise ValueError("no target")
    lit = literal_path(t)
    if lit and (clean(t).lower().replace("/", "\\") == lit.lower().replace("/", "\\")
                or re.match(r"^(?:the\s+)?(?:file|folder)\s+", t, re.I)):
        return lit
    if is_referent(t):
        return referent_value(t)
    rest, where, _prep = split_place(t, ui=False)
    if where is not None:
        name = bare_name(rest)
        if not name or _BULK.match(name) or folder_of(name) == where:
            return str(where)
        if is_referent(name):
            return referent_value(name)
        if not nameable(name):
            raise ValueError("not a name")
        return str(where / name)
    if lit:
        return lit
    whole = folder_of(t) or drive_of(t)
    if whole is not None:
        return str(whole)
    name = bare_name(t)
    if is_referent(name):
        return referent_value(name)
    if not nameable(name):
        raise ValueError("not a name")
    whole = folder_of(name)
    if whole is not None:
        return str(whole)
    if not _FILEISH.search(name):
        raise ValueError("a bare word with no place and no extension is not a file I can name")
    return find_one(name)


def dest_target(phrase: Any, source: str = "") -> str:
    t = unaddressed(phrase)
    if not t:
        raise Ask("Where to, Emperor?")
    lit = literal_path(t)
    if lit:
        return lit
    where = folder_of(t) or drive_of(t)
    if where is not None:
        return str(where)
    rest, where, _prep = split_place(t, ui=False)
    if where is not None:
        name = bare_name(rest)
        return str(where / name) if name and not _BULK.match(name) else str(where)
    name = bare_name(t)
    if source and name and nameable(name):
        beside = Path(source).parent / name
        if beside.is_dir():
            return str(beside)
    raise Ask(f"Where is {name or t}, Emperor? Name the folder.")


def new_path(phrase: Any) -> str:
    t = unaddressed(phrase)
    rest, where, _prep = split_place(t, ui=False)
    name = bare_name(rest if where is not None else t)
    if not name:
        raise ValueError("no name to create")
    if any(sep in name for sep in ("\\", "/", ":")):
        return name
    if any(ch in name for ch in '<>:"|?*'):
        raise ValueError(f"not a usable name: {name!r}")
    if where is None:
        m = re.search(rf"\s+(?:{_PREPS})\s+(?P<p>.+)$", t, re.I)
        if m and not folder_of(m.group("p")):
            raise Ask(f"Where should I make {bare_name(t[:m.start()]) or 'it'}, Emperor? Name the folder.")
        where = Path.home() / "Documents"
    return str(where / name)


_FOREGROUND = re.compile(r"^(?:this|this\s+(?:window|one)|the\s+(?:current|active|front)\s+window|"
                         r"the\s+window\s+in\s+front)$", re.I)


def window_name(phrase: Any) -> str:
    t = unaddressed(phrase)
    if _FOREGROUND.match(t):
        return "this window"
    if is_referent(t):
        return referent_value(t, ("window",))
    rest, _where, prep = split_place(t)
    if prep and rest:
        t = rest
    t = re.sub(r"\s+(?:back\s+up|back|up|forward|again)$", "", t, flags=re.I)
    t = re.sub(r"\s+window$", "", t, flags=re.I)
    t = clean(re.sub(rf"^{_DETS}\s+", "", t, flags=re.I))
    if is_referent(t):
        return referent_value(t, ("window",))
    if not t or not nameable(t):
        raise ValueError("not a window")
    return t


def command_and_cwd(raw: Any) -> tuple[str, str]:
    cmd = clean(raw)
    cmd = clean(_TERMINALS.sub("", cmd))
    for m in reversed(list(re.finditer(r"\s+(?:in|inside|within|from|at)\s+", cmd, re.I))):
        where = place_of(cmd[m.end():])
        if where is not None:
            return clean(cmd[:m.start()]), str(where)
    return cmd, ""


def is_email_request(text: Any) -> bool:
    t = str(text or "")
    return bool(_EMAIL.search(t)) and not _X_WORDS.search(t)


def her_sleep(text: Any) -> bool:
    t = str(text or "")
    return bool(_HER_SLEEP.search(t)) and not _MACHINE.search(t)


def when(phrase: Any, now: datetime | None = None) -> str:
    t = re.sub(r"^(?:for|at|by|around|on)\s+", "", clean(phrase).lower())
    t = re.sub(r"\s+(?:please|for\s+me)$", "", t)
    if not t:
        raise ValueError("no time")
    rel = _REL.match(t)
    if rel:
        raw = rel.group("n").lower()
        n = 30 if raw.startswith("half") else (int(raw) if raw.isdigit() else _REL_N.get(raw, 0))
        mult = 60 if rel.group("u").lower().startswith("h") else 1
        if n <= 0:
            raise ValueError("no time")
        return f"+{n * mult}m"
    m = _WHEN.match(t)
    if not m:
        raise ValueError("no time")
    hraw = m.group("h").lower()
    h = int(hraw) if hraw.isdigit() else _HOURS[hraw]
    mm = int(m.group("m")) if m.group("m") else _MINUTE_WORDS.get((m.group("mw") or "").lower().replace("-", " "), 0)
    ap = (m.group("ap") or "").replace(".", "").replace(" ", "").lower()
    day = (m.group("day1") or m.group("day2") or "").lower()
    if h > 23 or mm > 59:
        raise ValueError("not a time")
    if ap:
        if h > 12:
            raise ValueError("not a time")
        h = (h % 12) + (12 if ap == "pm" else 0)
    elif hraw in ("noon", "midday", "midnight") or h == 0 or h >= 13:
        pass
    elif re.search(r"tonight|evening|night|afternoon", day):
        h = h + 12 if h < 12 else h
    elif "morning" in day:
        pass
    else:
        raise Ask(f"{hraw} in the morning or in the evening, Emperor?")
    now = now or datetime.now()
    if day.startswith("tomorrow"):
        at = (now + timedelta(days=1)).replace(hour=h, minute=mm, second=0, microsecond=0)
        return str(int(at.timestamp()))
    return f"{h:02d}:{mm:02d}"


def history_referents(lines: list[str] | None) -> list[Referent]:
    out: list[Referent] = []
    for line in reversed(list(lines or [])[-HISTORY_LINES:]):
        t = unaddressed(line)
        if is_email_request(t):
            out.append(Referent("email", "", 0.0))
            continue
        tok = _FILE_TOKEN.search(t)
        if tok:
            rest, where, _p = split_place(t[tok.start():], ui=False)
            name = bare_name(rest)
            if where is not None and _FILEISH.search(name):
                out.append(Referent("file", str(where / name), 0.0))
                continue
        m = re.search(r"\b(?:open|show(?:\s+me)?|go\s+to|list|read|reveal)\s+(?P<o>.+)$", t, re.I)
        if m:
            whole = folder_of(m.group("o"))
            if whole is not None:
                out.append(Referent("folder", str(whole), 0.0))
    return out


_PATH_TOOLS = ("fs.", "system.files.", "app.open_folder", "system.code.open")
_WINDOW_TOOLS = ("win.", "system.window.")


def referents_of(name: str, args: dict[str, Any], at: float | None = None) -> list[Referent]:
    now = time.time() if at is None else at
    out: list[Referent] = []
    if name.startswith(_PATH_TOOLS):
        p = args.get("path")
        if isinstance(p, str) and p and os.path.isabs(p):
            kind = "file" if _FILEISH.search(p) else "folder"
            out.append(Referent(kind, p, now))
            if name in ("app.open_folder", "fs.open") and kind == "folder":
                out.append(Referent("window", Path(p).name or p, now))
    if name.startswith(_WINDOW_TOOLS) and isinstance(args.get("name"), str) and args.get("name"):
        out.append(Referent("window", args["name"], now))
    if name in ("app.open", "system.app.launch"):
        app = args.get("app") or args.get("name")
        if isinstance(app, str) and app:
            out.append(Referent("window", app, now))
    return out
