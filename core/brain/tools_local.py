"""
core/brain/tools_local.py — the local tool surface. Free, offline, 0 ms.

CLAUDE.md INVARIANT 4 IS THE WHOLE DESIGN HERE: the model never receives a raw
command string to execute. Every entry below is a tool NAME plus STRUCTURED
ARGUMENTS, and Python owns execution. Nothing in this file interpolates a
model-produced string into a shell.

These stay local FOREVER. Paying Opus to open a folder would be absurd — it is
also slower, costs money, and fails when the connection does.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

Tier = str  # "green" | "amber" | "red"


#: Who or what BUILT a call. A strict subset of the protocol's PROVENANCE enum
#: (CONTRACT §6.2) — `program` and `external` can never build a tool call, only
#: be read by one.
#:
#:   human     the router parsed the owner's speech or typing
#:   agent     the model picked it (the tool-picking loop, when it exists)
#:   schedule  an unattended trigger (the scheduler, when it exists)
Origin = str


@dataclass(frozen=True)
class ToolCall:
    name: str
    args: dict[str, Any] = field(default_factory=dict)
    tier: Tier = "green"
    #: What Tessa says while doing it. Short first sentence — Piper streams per
    #: sentence and the opener is the whole 400 ms budget.
    speech: str = ""
    #: ── WHO BUILT THIS CALL. THE APPROVAL CARD'S PROVENANCE COMES FROM HERE. ──
    #:
    #: It used to come from `args["provenance"]`, defaulting to "human". That
    #: was harmless while every call was router-built from his own speech, and
    #: it is a hole the moment a model builds one: nothing stops a model setting
    #: `args["provenance"] = "human"` on its own call, and the card — the
    #: security boundary for every red action — would then say the owner asked
    #: for it. A forgeable provenance guts the card.
    #:
    #: So provenance is a FIELD ON THE CALL, set by whatever constructed it and
    #: never readable from `args`. The executor strips `provenance`/`origin`
    #: keys out of `args` before dispatch (core/brain/executor.py), mirroring how
    #: `_approved_by_surface` is made unforgeable.
    #:
    #: THE DEFAULT IS "schedule", NOT "human", AND THAT IS THE POINT. It was
    #: "human" so existing construction sites kept their meaning — which meant
    #: any builder that forgot the field (a future model loop, a scheduler, a
    #: test) was recorded as the OWNER on the card and on the chain. A forgotten
    #: origin now fails SAFE: "schedule" is the most restrictive actor in
    #: guard.py. Every real human call is stamped "human" explicitly at the
    #: router's one choke point (core/brain/router.py) and at the typed and
    #: voice web.search sites; nothing human relies on the default. A future
    #: model loop sets "agent". Anything unrecognised is treated as "schedule".
    origin: Origin = "schedule"
    #: ── THE HOLD'S "YES" TRAVELS HERE, NOT IN `args`. ─────────────────────────
    #:
    #: `confirmed` used to be an ARGS key: `answer_confirmation` set
    #: `args["confirmed"] = True` and the hold gate read it back with
    #: `args.get("confirmed")`. Nothing stripped it, so a model-built call
    #: carrying `confirmed: true` would have skipped the hold outright — the
    #: same hole as `provenance`, and a bypass rather than a mislabel. The
    #: executor now strips that key with the others, and the ONLY things that
    #: satisfy a hold are this field — set by `answer_confirmation` once the
    #: ledger has accepted his "yes" — and the ledger's own repeat match. A
    #: model's args cannot reach either. Defaults False: a call is unconfirmed
    #: unless the code that owns the ledger says otherwise.
    confirmed: bool = False


# ── the Start Menu index ─────────────────────────────────────────────────────

_START_DIRS = [
    Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "Microsoft/Windows/Start Menu/Programs",
    Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
]

_KNOWN_FOLDERS = {
    "downloads": Path.home() / "Downloads",
    "documents": Path.home() / "Documents",
    "desktop": Path.home() / "Desktop",
    "pictures": Path.home() / "Pictures",
    "videos": Path.home() / "Videos",
    "music": Path.home() / "Music",
    "home": Path.home(),
    # DERIVED, NOT HARD-CODED. This was `C:\dev\zoey` and the rename turned it
    # into `C:\dev\tessa` — a path that does not exist until Gerald renames the
    # folder, so "open tessa" would have failed silently in between.
    #
    # It was also a CLAUDE.md violation the whole time: "No hard-coded paths to
    # the owner's machine outside config and tests." Deriving it from this
    # file's own location fixes both, and keeps working wherever he puts the
    # repo — including after the folder rename, with no further edit.
    "tessa": Path(__file__).resolve().parents[2],
    "dev": Path(__file__).resolve().parents[3],
}

#: SPOKEN NAMES FOR THE SAME FOLDER. He does not say "pictures" — he says
#: photos, or pics, or images, and they are one place on disk.
#:
#: This is the same class of failure as the dropped plural that made "open my
#: download" fail after Whisper transcribed it without the s: the folder was
#: right there and the matcher was too literal to see it. Coverage of exact
#: words loses; resolving the word he used to the place he meant wins.
_FOLDER_ALIASES = {
    "docs": "documents", "doc": "documents", "my docs": "documents",
    "dl": "downloads", "downloaded": "downloads",
    "pics": "pictures", "photos": "pictures", "photo": "pictures", "images": "pictures",
    "movies": "videos", "vids": "videos", "films": "videos",
    "songs": "music", "tunes": "music",
    "my pc": "home", "user folder": "home", "home folder": "home", "my folder": "home",
    "repo": "tessa", "the repo": "tessa", "tessa os": "tessa", "tessa repo": "tessa",
    "projects": "dev", "project folder": "dev",
}


def folder_stem(name: str) -> str:
    """"downloads" -> "download". ONE definition, used everywhere it is needed."""
    return name[:-1] if name.endswith("s") else name


def folder_for(text: str) -> Path | None:
    """
    Resolve spoken words to a known folder: canonical name, singular or plural,
    or any alias above. Returns None rather than guessing.

    Word-boundary matched, never substring: "documentation" must not resolve to
    Documents, and "developer" must not resolve to C:\\dev.
    """
    c = (text or "").lower().strip(" .,?!\"'")
    if not c:
        return None
    for alias, canonical in _FOLDER_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", c):
            return _KNOWN_FOLDERS[canonical]
    for name, path in _KNOWN_FOLDERS.items():
        if re.search(rf"\b{re.escape(folder_stem(name))}s?\b", c):
            return path
    return None


_BROWSERS = {
    "chrome": "chrome", "google chrome": "chrome",
    "edge": "msedge", "microsoft edge": "msedge",
    "firefox": "firefox", "brave": "brave",
}


def index_start_menu() -> dict[str, Path]:
    """
    Every .lnk on the Start Menu, keyed by lowercase stem.

    Built once and cached by the caller. Scanning is metadata-only — names and
    paths, never contents — which is also CLAUDE.md invariant 5's rule about
    reparse points, and there is no reason to open a shortcut to know its name.
    """
    out: dict[str, Path] = {}
    for root in _START_DIRS:
        if not root.exists():
            continue
        for p in root.rglob("*.lnk"):
            out.setdefault(p.stem.lower(), p)
    return out


# ── fuzzy matching ───────────────────────────────────────────────────────────
#
# THREE TIERS, cheapest first, and the tier that matched is reported so an
# ambiguous answer can be explained rather than just delivered:
#
#   1. exact          "notepad" -> Notepad
#   2. substring      "code" -> Visual Studio Code        (containment both ways)
#   3. ratio          "vscode" -> Visual Studio Code      (SequenceMatcher)
#
# The point is that "open my downloads", "open downloads folder" and "downloads"
# are ONE intent. Coverage is easy; collapsing phrasings is the hard part and it
# is what makes her feel like she understands rather than like she has a manual.

AMBIGUITY_MARGIN = 0.08
MIN_RATIO = 0.62


def fuzzy_match(query: str, candidates: dict[str, Path]) -> tuple[list[str], str]:
    """Returns (best_keys, how). More than one key means genuine ambiguity."""
    q = query.lower().strip()
    if not q:
        return [], "empty"
    if q in candidates:
        return [q], "exact"

    subs = [k for k in candidates if q in k or k in q]
    if len(subs) == 1:
        return subs, "substring"
    pool = subs or list(candidates)

    scored = sorted(((SequenceMatcher(None, q, k).ratio(), k) for k in pool), reverse=True)
    if not scored or scored[0][0] < MIN_RATIO:
        return ([], "no-match") if not subs else (subs[:4], "substring-ambiguous")
    top = scored[0][0]
    tied = [k for score, k in scored if top - score <= AMBIGUITY_MARGIN]
    return (tied[:4], "ratio-ambiguous" if len(tied) > 1 else "ratio")


# ── executors. Python owns every one of these. ───────────────────────────────


def open_path(path: Path) -> None:
    os.startfile(str(path))  # noqa: S606 - a Path we constructed, never model text


def open_in_vscode(path: Path) -> tuple[bool, str]:
    exe = shutil.which("code") or shutil.which("code.cmd")
    if exe is None:
        return False, "VS Code is not on PATH"
    subprocess.Popen([exe, str(path)], shell=False)
    return True, str(path)


def open_url(url: str, browser: str | None = None) -> tuple[bool, str]:
    """
    URL is validated as http(s) BEFORE anything is launched.

    An unvalidated scheme here would be a launcher for `file://`, or worse for a
    registered handler — from a string that may have originated in speech.
    """
    if not re.match(r"^https?://[^\s]+$", url):
        return False, f"not an http(s) URL: {url!r}"
    exe = _BROWSERS.get((browser or "").lower())
    resolved = shutil.which(exe) if exe else None
    if resolved:
        subprocess.Popen([resolved, url], shell=False)
        return True, f"{browser}: {url}"
    os.startfile(url)  # noqa: S606 - validated http(s) above
    return True, f"default browser: {url}"


def listening_on_port(port: int) -> list[dict[str, Any]]:
    """Who holds a port. `netstat -ano` then resolve each PID via tasklist."""
    out: list[dict[str, Any]] = []
    try:
        res = subprocess.run(["netstat", "-ano", "-p", "TCP"],
                             capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return out
    for line in res.stdout.splitlines():
        if f":{port} " not in line and not line.strip().endswith(str(port)):
            if f":{port}" not in line:
                continue
        parts = line.split()
        if len(parts) >= 5 and parts[0] == "TCP" and parts[3] == "LISTENING":
            if not parts[1].endswith(f":{port}"):
                continue
            pid = int(parts[4])
            out.append({"pid": pid, "local": parts[1], "name": _pid_name(pid)})
    return out


def _pid_name(pid: int) -> str:
    try:
        res = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, timeout=15)
        m = re.match(r'^"([^"]+)"', res.stdout.strip())
        return m.group(1) if m else "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def tool_version(tool: str) -> tuple[bool, str]:
    exe = shutil.which(tool) or shutil.which(f"{tool}.cmd")
    if exe is None:
        return False, f"{tool} is not on PATH"
    try:
        res = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=20)
        return True, (res.stdout or res.stderr).strip().splitlines()[0]
    except (OSError, subprocess.SubprocessError, IndexError) as exc:
        return False, f"{tool} did not answer --version ({exc})"
