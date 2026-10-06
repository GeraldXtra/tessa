"""
core/system/abilities/software_change.py — INSTALL and UNINSTALL software.
RED, winget CATALOG ONLY, by EXACT PACKAGE ID, the id FROZEN on the card —
plus the green search that turns a spoken name into that id.

    system.software.search      green  system.inventory     winget search / winget list — read-only,
                                                            fenced; the name -> id step
    system.software.install     red    software.install     winget install   --exact --id <ID> --source winget
    system.software.uninstall   red    software.uninstall   winget uninstall --exact --id <ID> --source winget

────────────────────────────────────────────────────────────────────────────────
⚠⚠ WHY THIS IS THE SECOND MASTER KEY, AFTER shell.execute

Installing software RUNS AN INSTALLER: arbitrary code, usually elevated. It is
how malware arrives ("install this"). A poisoned tweet that could reach an
install capability is "download and run this at 3am". So the safeguard is
STRUCTURAL, not hopeful:

  1. THERE IS NO RAW-INSTALLER PATH. The only mechanism in this module is
     `winget install --exact --id <ID> --source winget` — a constant argument
     vector with ONE slot, and that slot admits only a catalog package id
     (`ID_RE`: dotted Publisher.Name segments; no path separators, no colon, no
     space, no leading dash, at least one dot). A file path, an .exe, a URL, a
     `--manifest`, a `--source` of his own — none of them can be expressed
     here. `winget` then resolves the id against Microsoft's curated catalog
     and nothing else; an id the catalog does not know installs nothing.
  2. THE ID IS FROZEN ON THE CARD (`frozen=("id",)`). An approved "install
     VideoLAN.VLC" cannot come back from the surface as "install Evil.Thing":
     `resolve_edit` refuses the edit and keeps the request. `name` is display
     only and is not what the argv carries.
  3. RED, CARD-ONLY, TWICE. The framework (core/system/capability.py) will not
     call `run` without the real `_approved_by_surface` from
     `Executor.execute_approved`; `_change()` refuses again without it. A
     model's args carrying the flag are stripped and logged before dispatch; a
     fenced context (a tweet in the fence) refuses the red action before a card
     is ever raised.
  4. THE PROTECT-LIST. `uninstall` refuses — before the card (`describe`) AND
     after approval (`run`) — anything Tessa runs on and anything Windows
     needs: see `protection()`.
  5. ONE SEAM. `_run_winget` is the only place winget is invoked; a proof swaps
     it for a recorder and nothing below it ever runs.

THE NAME -> ID STEP IS READ-ONLY AND ITS OUTPUT IS UNTRUSTED. A package name is
whatever a manifest author wrote; the search returns it fenced (external
content), exactly like the installed-software observer in software.py. The
voice route "install vlc" resolves the name with `resolve()` BEFORE the card
is raised, so what he approves is the exact id, never a name that would be
looked up again later against a catalog that may have changed.

winget IS ADDRESSED BY ITS APP-EXECUTION ALIAS under %LOCALAPPDATA%, never bare
on PATH first — this daemon's shell does not have WindowsApps on PATH, and a
bare name is a planting risk. Every subprocess is `shell=False`, fixed argv.
No new Python dependency: winget is a Windows CLI.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, Callable

from core.system.capability import Capability, Param
from core.system.untrusted import fenced, head

#: The one catalog. Pinned on every argv so the msstore source (and any source
#: he adds by hand) is never consulted by this module.
CATALOG = "winget"

#: A winget package identifier: 2-8 dotted segments of [A-Za-z0-9_+-], each
#: starting alphanumeric, 32 chars max per segment (the manifest schema's
#: bound). THIS IS THE SHAPE GUARD: no "\\", no "/", no ":", no space, no
#: leading "-", at least one ".". A path, a URL and an option flag all fail it.
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_+\-]{0,31}(?:\.[A-Za-z0-9][A-Za-z0-9_+\-]{0,31}){1,7}$")

#: A spoken search term: starts alphanumeric (so it can never be read as an
#: option), a bounded set of characters, 64 max.
QUERY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._+\-']{0,63}$")

#: Courtesy check on top of the mechanism, for a clearer refusal: a TWO-segment
#: id whose second segment is an installer extension is "setup.exe", not a
#: package. Three-segment ids like `Zoom.Zoom.EXE` are real catalog ids and
#: pass. The argv would have refused "setup.exe" anyway (no such catalog id).
_FILE_LIKE = ("exe", "msi", "msix", "msixbundle", "appx", "appxbundle", "ps1", "bat", "cmd",
              "com", "scr", "vbs", "js", "jar", "zip", "7z", "rar", "iso", "dll", "lnk", "url")

SEARCH_TIMEOUT_S = 60
INSTALL_TIMEOUT_S = 900       # a real installer can take minutes; the card already said yes
UNINSTALL_TIMEOUT_S = 600

#: winget's HRESULT-shaped exit codes, in plain words (the ones he will meet).
_EXIT_WORDS = {
    0: "done",
    -1978335215: "the catalog has no package with that id",              # 0x8A150011 NO_APPLICATIONS_FOUND
    -1978335212: "more than one package matched — the id was not exact",  # 0x8A150014 MULTIPLE_APPLICATIONS_FOUND
    -1978335189: "the installer was cancelled",                           # 0x8A15002B (install cancelled)
    -1978334967: "the package is already installed",                      # 0x8A150109 (installed, up to date)
    -1978335135: "no installer in the catalog fits this machine",         # 0x8A150061 NO_APPLICABLE_INSTALLER
    -1978335216: "winget's source could not be opened",                   # 0x8A150010 SOURCE_OPEN_FAILED
    -1978335167: "the installer needs elevation and did not get it",      # 0x8A150041 (elevation required)
    -1978335226: "winget needs the source agreements accepted",           # 0x8A150006 (agreements)
}


class Refused(Exception):
    """Internal: a target this module will not act on. Converted to ToolError."""


# ─────────────────────────────────────────────────────────────────────────────
# the protect-list — what uninstall refuses, before the card and after it
# ─────────────────────────────────────────────────────────────────────────────

def _running_python_id() -> str:
    """The catalog id of the interpreter THIS daemon runs on (Python.Python.3.12 here)."""
    return f"Python.Python.{sys.version_info.major}.{sys.version_info.minor}"


#: TESSA'S OWN RUNTIME — verified against this machine on 2026-09-12:
#:   * the daemon is `Python.Python.3.12`'s python.exe (runtime.json pid -> exe);
#:     `Python.Launcher` is py.exe, which the owner's shortcuts use;
#:   * `Google.Chrome`: core/tools/browser.py drives HIS installed Chrome
#:     (`channel="chrome"`) — no Chromium download, metered data;
#:   * `Git.Git`: CLAUDE.md, "Gerald owns version control";
#:   * `OpenJS.NodeJS` / `.LTS`: package.json engines node>=22 — the Console and
#:     the Orb are Electron apps built and run with it;
#:   * `Microsoft.AppInstaller`: winget ITSELF — removing it removes this
#:     capability's own mechanism (and Store app installs);
#:   * `Microsoft.WindowsTerminal`: the daemon's process ancestry is
#:     powershell.exe <- WindowsTerminal.exe; she runs inside it;
#:   * `Microsoft.OneDrive`: the owner's 60+ project folders live in the
#:     OneDrive tree (permissions.yaml protected_paths); uninstalling the
#:     client orphans 17,340 placeholders.
PROTECTED_RUNTIME: frozenset[str] = frozenset(s.lower() for s in (
    _running_python_id(), "Python.Launcher", "Google.Chrome", "Git.Git",
    "OpenJS.NodeJS", "OpenJS.NodeJS.LTS", "Microsoft.AppInstaller",
    "Microsoft.WindowsTerminal", "Microsoft.OneDrive",
))

#: WINDOWS / SYSTEM COMPONENTS by id prefix (case-insensitive). Runtimes that
#: other programs — and Windows features — load: uninstalling one breaks
#: things that never appear in a card. Drivers by publisher: on this laptop
#: (HD 620, legacy driver) a removed graphics/audio/network driver may not
#: reinstall from the catalog at all.
PROTECTED_PREFIXES: tuple[str, ...] = tuple(s.lower() for s in (
    "Python.Python.",               # ANY CPython — she may be moved to another minor
    "Microsoft.VCRedist.", "Microsoft.VCLibs.", "Microsoft.DotNet.",
    "Microsoft.WindowsAppRuntime.", "Microsoft.UI.Xaml.", "Microsoft.Edge",
    "Microsoft.DirectX", "Microsoft.PowerShell", "Microsoft.WindowsTerminal",
    "Microsoft.AppInstaller",
    "Intel.", "NVIDIA.", "Nvidia.", "AMD.", "Realtek.",
))


def protection(package_id: str) -> tuple[str, str] | None:
    """
    (reason, alternative) when `package_id` must not be uninstalled, else None.
    Case-insensitive: winget ids are matched exactly by the argv, but a
    protect-list that a change of case could slip past protects nothing.
    """
    low = (package_id or "").strip().lower()
    if not low:
        return None
    if low in PROTECTED_RUNTIME:
        return (f"{package_id} is part of what I run on",
                "Uninstalling it would take me — or the surfaces — down with it. Not that one.")
    for prefix in PROTECTED_PREFIXES:
        if low.startswith(prefix):
            return (f"{package_id} is a Windows or system component",
                    "Other programs and Windows itself load it. I will not uninstall those.")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# the id and query shape guards
# ─────────────────────────────────────────────────────────────────────────────

def validate_id(raw: Any) -> str:
    """
    The ONE gate every id passes before it can reach an argv. Raises
    `Refused` naming what was wrong, in his terms.
    """
    s = str(raw if raw is not None else "").strip()
    if not s:
        raise Refused("no package id was given",
                      "Say the program's name and I will look up its catalog id first.")
    if "://" in s or s[:1] in ("-", "/") or "\\" in s or "/" in s or ":" in s:
        kind = "a web address" if "://" in s else ("an option" if s[:1] in ("-", "/") else "a file path")
        raise Refused(f"{s[:60]!r} is {kind}, not a catalog package id",
                      "I only install named packages from the winget catalog, by their exact id. "
                      "Never a file, never a link.")
    if not ID_RE.match(s):
        raise Refused(f"{s[:60]!r} is not a catalog package id",
                      "A winget id looks like Publisher.Name — VideoLAN.VLC. "
                      "Say the program's name and I will look it up.")
    parts = s.split(".")
    if len(parts) == 2 and parts[1].lower() in _FILE_LIKE:
        raise Refused(f"{s!r} looks like a file, not a catalog package",
                      "I only install named packages from the winget catalog. Never a file.")
    return s


def _clean_query(raw: Any) -> str:
    s = " ".join(str(raw if raw is not None else "").split()).strip(" .,?!\"'")
    if not s:
        raise Refused("no program name was given", "Tell me the program and I will look it up.")
    if not QUERY_RE.match(s):
        raise Refused(f"{s[:60]!r} is not a name I can search the catalog for",
                      "Letters, digits, dots, dashes and spaces — the program's name.")
    return s


# ─────────────────────────────────────────────────────────────────────────────
# winget: where it is, the fixed argument vectors, THE ONE SEAM
# ─────────────────────────────────────────────────────────────────────────────

def winget_path() -> str:
    """
    The app-execution alias under %LOCALAPPDATA%\\Microsoft\\WindowsApps first
    (that is where App Installer puts it, and this daemon's shell does not have
    it on PATH), then PATH. Raises `Refused` when winget is not on the machine.
    """
    local = os.environ.get("LOCALAPPDATA") or ""
    alias = os.path.join(local, "Microsoft", "WindowsApps", "winget.exe") if local else ""
    if alias and os.path.isfile(alias):
        return alias
    found = shutil.which("winget")
    if found:
        return found
    raise Refused("winget is not installed on this machine",
                  "Install App Installer from the Microsoft Store and I can do this.")


_COMMON: tuple[str, ...] = ("--source", CATALOG, "--accept-source-agreements", "--disable-interactivity")


def argv_search(query: str) -> list[str]:
    return [winget_path(), "search", "--query", query, *_COMMON]


def argv_list(query: str) -> list[str]:
    return [winget_path(), "list", "--query", query, *_COMMON]


def argv_install(package_id: str) -> list[str]:
    # `--exact --id`: the catalog entry whose id IS this string, no fuzzy match.
    # `--accept-package-agreements`: the card was his acceptance; without it a
    # licensed package stops at a prompt `--disable-interactivity` cannot answer.
    return [winget_path(), "install", "--exact", "--id", package_id, *_COMMON,
            "--accept-package-agreements"]


def argv_uninstall(package_id: str) -> list[str]:
    return [winget_path(), "uninstall", "--exact", "--id", package_id, *_COMMON]


#: Verbs this module may ever hand to winget. `_run_winget` refuses anything
#: else, so a later edit cannot quietly add `--manifest` or `import`.
_VERBS = frozenset({"search", "list", "install", "uninstall"})
_NEVER_FLAGS = frozenset({"--manifest", "-m", "--location", "-l", "--override", "--custom",
                          "--header", "--force", "--ignore-security-hash", "--dependency-source",
                          "--allow-reboot", "--skip-dependencies", "import", "export", "configure",
                          "settings", "source", "download", "repair", "hash", "validate", "pin"})


def _run_winget(argv: list[str], timeout: int) -> subprocess.CompletedProcess:
    """
    THE ONE SEAM. Every winget invocation in this module comes through here.
    `shell=False`, no window, output captured as bytes and decoded UTF-8 (winget
    writes UTF-8 to a pipe). A proof replaces this function with a recorder and
    nothing below it ever runs.
    """
    if len(argv) < 2 or argv[1] not in _VERBS or any(a in _NEVER_FLAGS for a in argv[1:]):
        raise Refused("that winget command is not one I run", "search, list, install, uninstall only.")
    cp = subprocess.run(argv, shell=False, capture_output=True, timeout=timeout,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    out = (cp.stdout or b"").decode("utf-8", errors="replace")
    err = (cp.stderr or b"").decode("utf-8", errors="replace")
    return subprocess.CompletedProcess(argv, cp.returncode, out, err)


# ─────────────────────────────────────────────────────────────────────────────
# reading winget's table — the name -> id step
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Candidate:
    name: str
    id: str
    version: str
    match: str = ""


def parse_table(text: str) -> tuple[list[Candidate], int]:
    """
    winget prints a fixed-width table: a header line naming the columns, a
    dashed rule, then rows. Column starts come from the header's own token
    positions, so a wider Name column moves nothing. Returns (rows, dropped):
    rows whose Id fails `ID_RE` (an `ARP\\...` or `MSIX\\...` entry, or anything
    odd) are DROPPED and counted — they are not catalog packages.
    """
    lines = [ln.split("\r")[-1] for ln in (text or "").splitlines()]     # spinner fragments
    header = -1
    for i, ln in enumerate(lines):
        if ln.startswith("Name") and re.search(r"\bId\b", ln) and i + 1 < len(lines) \
                and lines[i + 1].strip().startswith("---"):
            header = i
            break
    if header < 0:
        return [], 0
    cols = [(m.group(0), m.start()) for m in re.finditer(r"\S+", lines[header])]
    names = [c[0] for c in cols]
    starts = [c[1] for c in cols] + [None]
    if "Id" not in names:
        return [], 0
    rows: list[Candidate] = []
    dropped = 0
    for ln in lines[header + 2:]:
        if not ln.strip():
            continue
        cells = {}
        for j, (col, _) in enumerate(cols):
            end = starts[j + 1]
            cells[col] = (ln[starts[j]:end] if end is not None else ln[starts[j]:]).strip()
        pid = cells.get("Id", "")
        if not ID_RE.match(pid):
            dropped += 1
            continue
        rows.append(Candidate(name=cells.get("Name", ""), id=pid,
                              version=cells.get("Version", "") or cells.get("Installed", ""),
                              match=cells.get("Match", "")))
    return rows, dropped


@dataclass(frozen=True)
class Resolution:
    id: str                       # "" when unresolved
    name: str
    why: str
    candidates: tuple[Candidate, ...] = field(default_factory=tuple)
    dropped: int = 0


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def pick(query: str, rows: list[Candidate]) -> tuple[Candidate | None, str]:
    """
    Which catalog entry did he mean? In order: the id he said; the catalog's
    own MONIKER for the query (the curated short name — "vlc" is
    VideoLAN.VLC's); an exact name; a name equal once punctuation and case are
    ignored ("notepad++", "7 zip"); the only hit. Otherwise nothing — the
    candidates are read back and he picks.
    """
    q = query.strip()
    ql, qn = q.lower(), _norm(q)
    for r in rows:
        if r.id.lower() == ql:
            return r, "exact id"
    for r in rows:
        if r.match.lower() == f"moniker: {ql}":
            return r, "catalog moniker"
    for r in rows:
        if r.name.lower() == ql:
            return r, "exact name"
    same = [r for r in rows if _norm(r.name) == qn]
    if len(same) == 1:
        return same[0], "name"
    if len(rows) == 1:
        return rows[0], "the only match"
    return None, ("no match" if not rows else f"{len(rows)} candidates")


def resolve(name: str, scope: str = "catalog") -> Resolution:
    """
    Name -> exact id, read-only. `scope="catalog"` searches what CAN be
    installed; `scope="installed"` lists what IS installed (for uninstall).
    Never raises for a missing match — `id` is "" and `why` says so — but a
    bad query or a missing winget is a `Refused`.
    """
    q = _clean_query(name)
    argv = argv_search(q) if scope == "catalog" else argv_list(q)
    try:
        cp = _run_winget(argv, SEARCH_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise Refused(f"winget did not answer within {SEARCH_TIMEOUT_S} seconds",
                      "The catalog is online; check the connection and ask again.") from None
    except OSError as exc:
        raise Refused(f"winget could not be started: {exc}", "Is App Installer healthy?") from None
    rows, dropped = parse_table(cp.stdout)
    best, why = pick(q, rows)
    if best is None:
        return Resolution(id="", name=q, why=why, candidates=tuple(rows[:20]), dropped=dropped)
    return Resolution(id=best.id, name=best.name or best.id, why=why,
                      candidates=tuple(rows[:20]), dropped=dropped)


# ─────────────────────────────────────────────────────────────────────────────
# GREEN: the search tool
# ─────────────────────────────────────────────────────────────────────────────

def search(name: str, scope: str = "catalog") -> dict[str, Any]:
    from core.tools.base import ToolError

    try:
        res = resolve(name, scope)
    except Refused as r:
        raise ToolError(*r.args) from None
    where = "the winget catalog" if scope == "catalog" else "what is installed here"
    cands = list(res.candidates)
    if res.id:
        top = next((c for c in cands if c.id == res.id), None)
        ver = f", version {top.version}" if top and top.version else ""
        others = f" And {len(cands) - 1} more." if len(cands) > 1 else ""
        if scope == "catalog":
            summary = (f"{res.name}{ver}, Emperor — id {res.id}.{others} "
                       f"Say install {res.id} and I will put it on the card.")
        else:
            summary = (f"{res.name}{ver} is installed, Emperor — id {res.id}.{others} "
                       f"Say uninstall {res.id} and I will put it on the card.")
    elif cands:
        summary = (f"{len(cands)} candidates for {name} in {where}, Emperor: "
                   f"{head([f'{c.name} ({c.id})' for c in cands], 4)}. Which one?")
    else:
        summary = f"Nothing matching {name} in {where}, Emperor."
    return {
        "n": len(cands), "asked": name, "scope": scope, "found": bool(cands),
        "resolved": res.id, "resolved_name": res.name if res.id else "", "why": res.why,
        "candidates": [c.__dict__ for c in cands], "dropped": res.dropped,
        "head": head([c.id for c in cands]),
        "summary": summary,
        **fenced(f"the winget {scope} search for {name}",
                 [f"{c.name} | {c.id} | {c.version}" for c in cands]),
    }


# ─────────────────────────────────────────────────────────────────────────────
# RED: install / uninstall — the same shape, distinct NAMES
# ─────────────────────────────────────────────────────────────────────────────

#: `Executor._log`, bound at construction like power's. Writes the RAN /
#: RAN-FAILED line with the exact argv and exit code under the request's TRUE
#: actor. None until bound; the action is unaffected, only the line is lost.
_REPORT: Callable[..., None] | None = None


def bind_reporter(report: Callable[..., None] | None) -> None:
    global _REPORT
    _REPORT = report


def _report(verb: str, tool: str, summary: str, actor: str) -> None:
    if _REPORT is None:
        return
    try:
        _REPORT(verb, tool, summary, "red", actor=actor)
    except Exception:  # noqa: BLE001
        pass


def _tail(s: str, n: int = 600) -> str:
    s = " ".join((s or "").split())
    return s[-n:] if len(s) > n else s


def _change(action: str, raw_id: Any, name: str, *, approved: bool,
            request_id: str, actor: str) -> dict[str, Any]:
    """
    The one body behind both red runs. Order: id shape -> protect-list ->
    approval flag -> the seam. Each refusal is a ToolError the executor audits
    as APPROVED-BUT-FAILED (or, via `describe`, refuses before the card).
    """
    from core.tools.base import ToolError

    tool = f"system.software.{action}"
    try:
        package_id = validate_id(raw_id)
        if action == "uninstall":
            hit = protection(package_id)
            if hit is not None:
                raise Refused(*hit)
        if not approved:
            raise Refused(f"{package_id} cannot be {'installed' if action == 'install' else 'uninstalled'} "
                          f"without your approval on the card",
                          "Approve it there and I will run winget.")
        argv = argv_install(package_id) if action == "install" else argv_uninstall(package_id)
    except Refused as r:
        raise ToolError(*r.args) from None

    shown = name.strip() or package_id
    timeout = INSTALL_TIMEOUT_S if action == "install" else UNINSTALL_TIMEOUT_S
    _report("RUNNING", tool, f"requestId={request_id} winget {' '.join(argv[1:])}", actor)
    try:
        cp = _run_winget(argv, timeout)
    except subprocess.TimeoutExpired:
        _report("RAN-FAILED", tool, f"requestId={request_id} {package_id}: timed out after {timeout}s", actor)
        raise ToolError(f"winget did not finish {action}ing {shown} within {timeout // 60} minutes",
                        "The installer may still be running on screen. Check it before asking again.") from None
    except Refused as r:
        raise ToolError(*r.args) from None
    except OSError as exc:
        _report("RAN-FAILED", tool, f"requestId={request_id} {package_id}: {type(exc).__name__}: {exc}"[:200], actor)
        raise ToolError(f"winget could not be started: {exc}", "Is App Installer healthy?") from None

    rc = int(cp.returncode)
    tail = _tail(cp.stdout) or _tail(cp.stderr)
    words = _EXIT_WORDS.get(rc, f"exit code {rc}")
    if rc != 0:
        _report("RAN-FAILED", tool, f"requestId={request_id} {package_id}: winget exit {rc} ({words}) — {tail[:160]}", actor)
        raise ToolError(f"winget could not {action} {shown} ({package_id}): {words}",
                        "Nothing was changed. Say it again once that is sorted, or pick another package.")
    _report("RAN", tool, f"requestId={request_id} {package_id}: winget exit 0 — {tail[:160]}", actor)
    verb = "Installed" if action == "install" else "Uninstalled"
    return {
        "action": action, "id": package_id, "name": shown, "exit": rc, "ok": True,
        "argv": argv[1:], "output": tail,
        "verdict": f"{verb} {shown} ({package_id}), Emperor.",
        # winget's output is program text about a package somebody else named:
        # data, fenced, like every other tool's output (CONTRACT 6.1).
        **fenced(f"winget output for {package_id}", [tail]),
    }


def install(id: str, name: str = "", provenance: str = "schedule",
            _approved_by_surface: bool = False, _request_id: str = "") -> dict[str, Any]:
    """RED. `winget install --exact --id <id> --source winget`. Only from the card."""
    return _change("install", id, name, approved=bool(_approved_by_surface),
                   request_id=_request_id, actor=provenance)


def uninstall(id: str, name: str = "", provenance: str = "schedule",
              _approved_by_surface: bool = False, _request_id: str = "") -> dict[str, Any]:
    """RED. `winget uninstall --exact --id <id> --source winget`. Protect-list, then the card."""
    return _change("uninstall", id, name, approved=bool(_approved_by_surface),
                   request_id=_request_id, actor=provenance)


def _describe(action: str) -> Callable[[dict[str, Any]], str]:
    """
    What the card SAYS, and what it refuses before it is raised. Read-only —
    no winget call here: the id shape and the protect-list are decided from
    the args alone, and a ToolError from here is a refusal BEFORE the card.
    """
    def describe(args: dict[str, Any]) -> str:
        from core.tools.base import ToolError

        try:
            package_id = validate_id(args.get("id"))
            if action == "uninstall":
                hit = protection(package_id)
                if hit is not None:
                    raise Refused(*hit)
        except Refused as r:
            raise ToolError(*r.args) from None
        shown = str(args.get("name") or "").strip()
        label = f"{shown} ({package_id})" if shown and shown.lower() != package_id.lower() else package_id
        return f"{action} {label} from the {CATALOG} catalog" if action == "install" else f"{action} {label}"

    return describe


_ID_PARAM = Param("id", str, doc="The exact winget package id, like VideoLAN.VLC. "
                                 "Say the program's name and I will look it up first.")
_NAME_PARAM = Param("name", str, default="", doc="What the catalog calls it — shown on the card, never executed.")

CAPABILITIES = [
    Capability(
        name="system.software.search", capability="system.inventory", tier="green",
        run=search,
        params=(Param("name", str, doc="A program name to look up."),
                Param("scope", str, default="catalog", choices=("catalog", "installed"),
                      doc="catalog (what can be installed) or installed (what is here).")),
        phrasings=("search winget for vlc", "is vlc in the catalog", "what's the winget id for vlc",
                   "look up vlc in winget"),
        success="{summary}",
        audit="search winget {scope} for {name}",
        note="Read-only name -> id step for install/uninstall: winget search (catalog) or winget list "
             "(installed), --source winget, fixed argv, shell=False. Package names are untrusted and "
             "fenced. Rows whose id is not catalog-shaped (ARP\\, MSIX\\) are dropped and counted.",
    ),
    Capability(
        name="system.software.install", capability="software.install", tier="red",
        run=install,
        params=(_ID_PARAM, _NAME_PARAM),
        # ⚠⚠ THE ID IS FROZEN. The card may not retarget an approved install.
        frozen=("id",),
        describe=_describe("install"),
        phrasings=("install vlc", "download and install 7zip", "install notepad++"),
        success="{verdict}",
        failure="I did not install it, sir. {reason} {alternative}",
        audit="INSTALL {id} ({name}) from the winget catalog",
        hold="install {name} ({id}) from the winget catalog",
        note="RED, card-only. Mechanism: winget install --exact --id <ID> --source winget "
             "--accept-package-agreements --accept-source-agreements --disable-interactivity — fixed "
             "argv, shell=False; the id slot admits only a catalog-shaped id (ID_RE), so a file path, "
             "an .exe, a URL, --manifest or another --source cannot be expressed. Refuses without the "
             "real _approved_by_surface (threaded, never fabricated).",
    ),
    Capability(
        name="system.software.uninstall", capability="software.uninstall", tier="red",
        run=uninstall,
        params=(_ID_PARAM, _NAME_PARAM),
        frozen=("id",),
        describe=_describe("uninstall"),
        phrasings=("uninstall vlc", "remove the vlc app", "uninstall 7-zip"),
        success="{verdict}",
        failure="I did not uninstall it, sir. {reason} {alternative}",
        audit="UNINSTALL {id} ({name})",
        hold="uninstall {name} ({id})",
        note="RED, card-only. Mechanism: winget uninstall --exact --id <ID> --source winget — fixed argv, "
             "shell=False, catalog-shaped ids only (no ARP\\ or MSIX\\ entries). The protect-list "
             "(protection(): the daemon's Python and launcher, Chrome, Git, Node, App Installer, "
             "Windows Terminal, OneDrive; VC/.NET/AppRuntime/Xaml/Edge/DirectX/PowerShell prefixes; "
             "Intel/NVIDIA/AMD/Realtek drivers) is refused BEFORE the card (describe) and AFTER "
             "approval (run).",
    ),
]
