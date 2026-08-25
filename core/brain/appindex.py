"""
core/brain/appindex.py — everything on his machine she can open.

────────────────────────────────────────────────────────────────────────────────
THE DIAGNOSIS IN THE BRIEF WAS WRONG, AND SAYING SO MATTERS

He was told the indexer scans only the per-user Start Menu and misses the
machine-wide one. It does not: `tools_local._START_DIRS` has listed BOTH roots
all along, and `Google Chrome.lnk` was in the index the whole time. Measured:

    C:\\ProgramData\\...\\Start Menu\\Programs      161 shortcuts
    %APPDATA%\\...\\Start Menu\\Programs             67 shortcuts
    indexed keys                                    219
    keys containing "chrome"                        'google chrome'  ✓ present

And `fuzzy_match("chrome", ...)` returns `['google chrome']` correctly.

THE ACTUAL FAULT is one word. His sentence was "Open my Chrome BROWSER", and
`intents.py` stripped `open|launch|start|run|show|my|the|app|application|please`
— but not "browser". So the query reaching the matcher was `"chrome browser"`,
no key contained it, and `SequenceMatcher` fell back to ratio scoring where
things literally ENDING in "browser" beat `google chrome`:

    'chrome'          -> substring        ['google chrome']            correct
    'chrome browser'  -> ratio-ambiguous  ['opera browser',
                                           'samsung browser',
                                           'epic privacy browser']     his bug

Fixing the root he was told about would have changed nothing and looked like a
fix. Two things are fixed instead: generic category nouns come off the query,
and ranking stops letting a fuzzy ratio outvote a whole-word match.

────────────────────────────────────────────────────────────────────────────────
SOURCES — MEASURED BEFORE BEING CHOSEN

    source                            cost      new keys   built?
    Start Menu, both roots            20.5 ms   219 base   YES
    Desktop, his + public              0.7 ms   +0         YES (free; +0 today
                                                            is not +0 tomorrow)
    App Paths, HKLM+HKCU, 32+64        5.3 ms   +51        YES
    Get-StartApps (UWP + Win32)      1746.7 ms  +76        YES, in background
    .lnk target resolution           5303.0 ms  —          YES, in background
    Program Files, depth 2            557.5 ms  +283       NO — see below

PROGRAM FILES IS DECLINED, deliberately. Its 283 "new" keys are overwhelmingly
uninstallers, crash handlers, updaters and helper binaries — `unins000`,
`crashpad_handler`, `vcredist` — and every genuine application in it is already
reachable through App Paths or the Start Menu. Indexing it would make her
offer him `setup.exe` when he asks for something, which is worse than a miss.

GET-StartApps IS THE ONE THAT PAYS FOR ITSELF. It is the only source that has
Calculator, Settings, WhatsApp, Mail and Windows Terminal at all — they are UWP
packages with no `.lnk` anywhere on disk — and it returns the AppUserModelID
needed to launch them.

────────────────────────────────────────────────────────────────────────────────
COST: HE MUST NOT PAY FOR THIS AT STARTUP

Two tiers.

  FAST (~26 ms)  Start Menu + Desktop + App Paths. Built on first use, inline.
                 She is usable immediately with this alone.
  SLOW (~7 s)    Get-StartApps + resolving 228 shortcut targets. Built on a
                 BACKGROUND THREAD and merged in when it lands, then cached to
                 `data/appindex.json` so the next launch pays nothing.

A lookup never blocks on the slow tier. If it has not landed yet she answers
from the fast tier, which contains every Win32 app on the machine.

────────────────────────────────────────────────────────────────────────────────
NEW SOFTWARE — the honest answer

`refresh_if_stale()` rebuilds when the cache is older than `MAX_AGE_S` (1 hour),
and `resolve()` triggers ONE rebuild-on-miss: if he asks for something that is
not in the index, the index is rebuilt once and the query retried before she
says she cannot find it. So an app installed five minutes ago is found the first
time he asks for it, at the cost of one rebuild.

There is no filesystem watcher. A watcher on two Start Menu trees plus the
registry is more moving parts than a rebuild-on-miss buys, on a 2-core machine.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import winreg
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CACHE_PATH = REPO_ROOT / "data" / "appindex.json"

#: Rebuild the cache when it is older than this.
MAX_AGE_S = 3600.0

#: How long a MISS may wait for the slow tier before giving up.
#:
#: Only ever paid on a miss, and only until the first slow build lands — after
#: that it is cached on disk and a cold start reloads it in ~16 ms. Generous
#: because the alternative is telling him she cannot find something she can.
SLOW_WAIT_S = 12.0

_APP_PATHS_KEY = r"Software\Microsoft\Windows\CurrentVersion\App Paths"

#: Words that describe a CATEGORY rather than name an application.
#:
#: THIS LIST IS THE CHROME FIX. "Open my Chrome browser" is not a request for
#: something called "chrome browser"; it is a request for Chrome, and the noun
#: is how he tells her what KIND of thing Chrome is. Left in the query it
#: matched every application whose name ends in the category word.
_CATEGORY_NOUNS = {
    "browser", "app", "apps", "application", "applications", "program",
    "programs", "software", "window", "tool", "client", "editor",
}

#: Words that can never name an application. Kept in step with the identical
#: list in `phrasings.py`, which guards the file rules — the two failures are
#: the same failure and they arrived a day apart.
_NOT_A_NAME = {
    "me", "it", "this", "that", "these", "those", "them", "they", "you",
    "us", "him", "her", "there", "here", "now", "then", "one", "thing",
    "stuff", "something", "anything", "everything", "again", "yes", "no",
    "back", "something",
}

#: Stripped from the front or anywhere — he says these around the name.
_COMMAND_WORDS = {
    "open", "launch", "start", "run", "show", "bring", "up", "me", "my",
    "the", "a", "an", "please", "for", "get", "go", "to", "into", "on",
}

#: Shell things with no .lnk and no package — reachable only by URI or verb.
#: Small, hand-kept, and zero cost. Each one is something he plausibly says.
_SHELL_APPS: dict[str, tuple[str, str]] = {
    "settings": ("ms-settings:", "Settings"),
    "windows settings": ("ms-settings:", "Settings"),
    "control panel": ("control.exe", "Control Panel"),
    "task manager": ("taskmgr.exe", "Task Manager"),
    "file explorer": ("explorer.exe", "File Explorer"),
    "explorer": ("explorer.exe", "File Explorer"),
    "registry editor": ("regedit.exe", "Registry Editor"),
    "device manager": ("devmgmt.msc", "Device Manager"),
    "disk management": ("diskmgmt.msc", "Disk Management"),
    "services": ("services.msc", "Services"),
    "command prompt": ("cmd.exe", "Command Prompt"),
    "powershell": ("powershell.exe", "PowerShell"),
    "notepad": ("notepad.exe", "Notepad"),
    "calculator": ("calc.exe", "Calculator"),
    "paint": ("mspaint.exe", "Paint"),
    "bluetooth settings": ("ms-settings:bluetooth", "Bluetooth Settings"),
    "wifi settings": ("ms-settings:network-wifi", "Wi-Fi Settings"),
    "sound settings": ("ms-settings:sound", "Sound Settings"),
    "display settings": ("ms-settings:display", "Display Settings"),
}


@dataclass(frozen=True)
class AppEntry:
    """One openable thing, and everything needed to rank and launch it."""
    key: str            # lowercase, what we match against
    name: str           # display name, what she says
    launch: str         # path, AppUserModelID, or shell URI
    kind: str           # "lnk" | "exe" | "uwp" | "shell"
    source: str         # provenance, for the report and for debugging
    target: str = ""    # resolved binary for a .lnk; "" when unknown
    alive: bool = True  # False once resolved and found missing


def _identity(e: AppEntry) -> str:
    """
    What this entry ACTUALLY starts, for collapsing duplicates across sources.

    A shortcut, an App Paths registration and a Get-StartApps row can all name
    the same binary; only the basename is common to all three. UWP packages and
    shell verbs have no binary, so they fall back to the display name.
    """
    ref = e.target or e.launch
    if ref and ref.lower().endswith(".exe"):
        return Path(ref).name.lower()
    # No binary to point at: a UWP package, a shell verb, or a shortcut whose
    # target is a folder or a URL. Fall back to the NAME rather than the path —
    # "File Explorer" reached as a .lnk and as `explorer.exe` is one thing to
    # him, and keying on the two different paths made her offer it three times.
    return f"name:{e.key}"


def _norm_query(text: str) -> str:
    """
    Strip command words and category nouns, keep the name.

    "open my chrome browser" -> "chrome"
    "open vs code"           -> "vs code"

    A token is only dropped if something survives. "open my browser" keeps
    "browser", because there the category word IS the whole request and she
    should try to match it rather than search for an empty string.
    """
    words = [w for w in "".join(
        ch if (ch.isalnum() or ch in " .+-_") else " " for ch in (text or "").lower()
    ).split() if w]
    kept = [w for w in words if w not in _COMMAND_WORDS and w not in _CATEGORY_NOUNS]
    if not kept:
        kept = [w for w in words if w not in _COMMAND_WORDS]
    return " ".join(kept).strip()


# ── the fast sources ─────────────────────────────────────────────────────────

def _start_menu() -> list[AppEntry]:
    roots = [
        (Path(os.environ.get("ProgramData", r"C:\ProgramData"))
         / "Microsoft/Windows/Start Menu/Programs", "start-menu-machine"),
        (Path(os.environ.get("APPDATA", ""))
         / "Microsoft/Windows/Start Menu/Programs", "start-menu-user"),
    ]
    out: list[AppEntry] = []
    for root, src in roots:
        if not root.exists():
            continue
        for p in root.rglob("*.lnk"):
            out.append(AppEntry(key=p.stem.lower(), name=p.stem,
                                launch=str(p), kind="lnk", source=src))
    return out


def _desktops() -> list[AppEntry]:
    roots = [(Path.home() / "Desktop", "desktop-user"),
             (Path(os.environ.get("PUBLIC", r"C:\Users\Public")) / "Desktop",
              "desktop-public")]
    out: list[AppEntry] = []
    for root, src in roots:
        if not root.exists():
            continue
        for p in root.glob("*.lnk"):
            out.append(AppEntry(key=p.stem.lower(), name=p.stem,
                                launch=str(p), kind="lnk", source=src))
    return out


def _app_paths() -> list[AppEntry]:
    """
    HKLM + HKCU `App Paths`, both registry views.

    THIS IS HOW WINDOWS ITSELF RESOLVES "chrome" typed into Run, which makes it
    the most authoritative source for "what does this NAME mean" — and it
    catches installs that never created a shortcut.
    """
    out: list[AppEntry] = []
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                root = winreg.OpenKey(hive, _APP_PATHS_KEY, 0, winreg.KEY_READ | view)
            except OSError:
                continue
            try:
                i = 0
                while True:
                    try:
                        sub_name = winreg.EnumKey(root, i)
                    except OSError:
                        break
                    i += 1
                    if not sub_name.lower().endswith(".exe"):
                        continue          # .dll entries are not launchable
                    try:
                        sub = winreg.OpenKey(root, sub_name, 0, winreg.KEY_READ | view)
                        val, _ = winreg.QueryValueEx(sub, "")
                    except OSError:
                        continue
                    if not val:
                        continue
                    path = os.path.expandvars(str(val).strip('"'))
                    stem = sub_name[:-4]
                    out.append(AppEntry(
                        key=stem.lower(), name=stem, launch=path, kind="exe",
                        source="app-paths", target=path,
                        alive=Path(path).exists(),
                    ))
            finally:
                root.Close()
    return out


def _shell_apps() -> list[AppEntry]:
    return [AppEntry(key=k, name=name, launch=cmd, kind="shell",
                     source="shell-builtin")
            for k, (cmd, name) in _SHELL_APPS.items()]


# ── the slow sources, for the background thread ──────────────────────────────

def _start_apps() -> list[AppEntry]:
    """
    `Get-StartApps` — the ONLY source that has UWP packages.

    Calculator, Settings, WhatsApp, Mail and Windows Terminal have no `.lnk`
    anywhere on disk. This returns their AppUserModelID, which is the only way
    to launch them.
    """
    ps = "Get-StartApps | ForEach-Object { \"$($_.Name)`t$($_.AppID)\" }"
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=90,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    out: list[AppEntry] = []
    for line in r.stdout.splitlines():
        if "\t" not in line:
            continue
        name, app_id = line.split("\t", 1)
        name, app_id = name.strip(), app_id.strip()
        if not name or not app_id:
            continue
        # ONLY TRUE UWP PACKAGES ARE KEPT.
        #
        # An AppID containing "!" is a package AUMID — a thing with no .lnk
        # anywhere, reachable only through shell:AppsFolder. Everything else
        # Get-StartApps returns is a Win32 entry the Start Menu scan ALREADY
        # has, under an AppID that is not a path and so cannot be matched
        # against the shortcut it duplicates.
        #
        # Keeping them produced exactly that failure: she offered "Discord or
        # Discord", "Figma or Figma", "Git Bash, Git Bash, Git Bash" — an
        # ambiguity that existed only in the index. Dropping them costs nothing
        # because the same programs are already indexed with real paths.
        if "!" not in app_id:
            continue
        out.append(AppEntry(key=name.lower(), name=name, launch=app_id,
                            kind="uwp", source="start-apps"))
    return out


def _resolve_lnk_targets(paths: list[str]) -> dict[str, str]:
    """
    Resolve every shortcut target in ONE PowerShell call.

    Item 1d: a shortcut pointing at a missing binary must not be offered. On
    this machine that is not hypothetical — five dead shortcuts were found,
    four of them a Python 3.14 that has been uninstalled.

    ONE call, not 228. Per-file would be minutes; batched it is ~5.3 s, which is
    why it lives on the background thread and gets cached.

    pywin32 would do this in-process and much faster, but it is not installed
    and CLAUDE.md forbids adding a dependency casually on a metered connection.
    """
    if not paths:
        return {}
    ps = (
        "$sh = New-Object -ComObject WScript.Shell\n"
        "$input | ForEach-Object {\n"
        "  try { $t = $sh.CreateShortcut($_).TargetPath } catch { $t = '' }\n"
        "  \"$_`t$t\"\n"
        "}\n"
    )
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            input="\n".join(paths), capture_output=True, text=True, timeout=180,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    out: dict[str, str] = {}
    for line in r.stdout.splitlines():
        if "\t" in line:
            src, tgt = line.split("\t", 1)
            out[src.strip()] = tgt.strip()
    return out


# ── the index ────────────────────────────────────────────────────────────────

class AppIndex:
    """
    Two-tier index with a disk cache.

    Thread-safe because the slow tier is built off-thread while lookups keep
    being served from the fast tier.
    """

    def __init__(self, cache_path: Path | None = None) -> None:
        self.cache_path = cache_path or CACHE_PATH
        # THE TWO TIERS ARE HELD SEPARATELY, and that is not tidiness.
        #
        # They were one list, and `build_fast()` assigned over it wholesale.
        # So the first rebuild-on-miss silently DELETED every Get-StartApps
        # entry — measured: "open spotify" missed, triggered a rebuild, and the
        # next query "open whatsapp" then failed too, because WhatsApp is a UWP
        # package that only the slow tier knows about. A refresh that destroys
        # the index it is refreshing is worse than no refresh.
        self._fast_entries: list[AppEntry] = []
        self._slow_entries: list[AppEntry] = []
        self._lock = threading.Lock()
        self._fast_built = False
        self._slow_built = False
        self._building = False
        self.built_at: float = 0.0
        self.timings: dict[str, float] = {}
        self._miss_rebuilds = 0

    # ── building ─────────────────────────────────────────────────────────────

    def build_fast(self) -> None:
        """Start Menu + Desktop + App Paths + shell built-ins. ~26 ms."""
        t0 = time.perf_counter()
        entries = _start_menu() + _desktops() + _app_paths() + _shell_apps()
        with self._lock:
            # Only the fast tier is replaced. The slow tier survives a refresh.
            self._fast_entries = entries
            self._fast_built = True
            self.built_at = time.time()
        self.timings["fast_ms"] = (time.perf_counter() - t0) * 1000

    def build_slow(self) -> None:
        """Get-StartApps + .lnk target resolution. ~7 s. Off-thread."""
        t0 = time.perf_counter()
        extra = _start_apps()
        t_apps = time.perf_counter()

        with self._lock:
            fast = list(self._fast_entries)
        lnks = [e.launch for e in fast if e.kind == "lnk"]
        lnks += [e.launch for e in extra if e.kind == "lnk"]
        targets = _resolve_lnk_targets(sorted(set(lnks)))

        def applied(e: AppEntry) -> AppEntry:
            if e.kind != "lnk":
                return e
            tgt = targets.get(e.launch, "")
            if not tgt:
                # No target: a folder shortcut, a URL, or a shell verb. Keep it
                # — `explorer.exe` opens it fine — but it is NOT proof of life.
                return e
            return AppEntry(key=e.key, name=e.name, launch=e.launch, kind=e.kind,
                            source=e.source, target=tgt, alive=Path(tgt).exists())

        with self._lock:
            # Resolved targets belong to the FAST entries, so they are written
            # back there; the slow tier holds only what Get-StartApps added.
            self._fast_entries = [applied(e) for e in self._fast_entries]
            self._slow_entries = [applied(e) for e in extra]
            self._slow_built = True
            self.built_at = time.time()
        self.timings["startapps_ms"] = (t_apps - t0) * 1000
        self.timings["lnk_resolve_ms"] = (time.perf_counter() - t_apps) * 1000
        self._save()

    def build_async(self) -> threading.Thread | None:
        """Fast tier now, slow tier on a thread. Returns the thread."""
        if not self._fast_built:
            if not self._load():
                self.build_fast()
            elif self._stale():
                self.build_fast()
        if self._slow_built or self._building:
            return None
        self._building = True

        def run() -> None:
            try:
                self.build_slow()
            except Exception:  # noqa: BLE001
                pass          # a failed slow tier must never break the fast one
            finally:
                self._building = False

        t = threading.Thread(target=run, name="tessa-appindex", daemon=True)
        t.start()
        return t

    def ensure(self) -> None:
        if not self._fast_built:
            if not self._load():
                self.build_fast()

    def _stale(self) -> bool:
        return (time.time() - self.built_at) > MAX_AGE_S

    def refresh_if_stale(self) -> bool:
        if self._stale():
            self.build_fast()
            self.build_async()
            return True
        return False

    # ── cache ────────────────────────────────────────────────────────────────

    def _save(self) -> None:
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                payload = {
                    "built_at": self.built_at,
                    "fast": [asdict(e) for e in self._fast_entries],
                    "slow": [asdict(e) for e in self._slow_entries],
                }
            self.cache_path.write_text(json.dumps(payload), encoding="utf-8")
        except OSError:
            pass          # a cache that cannot be written is not a failure

    def _load(self) -> bool:
        try:
            raw = json.loads(self.cache_path.read_text(encoding="utf-8"))
            fast = [AppEntry(**e) for e in raw.get("fast", [])]
            slow = [AppEntry(**e) for e in raw.get("slow", [])]
            if not fast and not slow:
                return False
            with self._lock:
                self._fast_entries = fast
                self._slow_entries = slow
                self.built_at = float(raw.get("built_at", 0.0))
                self._fast_built = True
                self._slow_built = bool(slow)
            return not self._stale()
        except (OSError, ValueError, KeyError, TypeError):
            return False

    # ── ranking ──────────────────────────────────────────────────────────────

    @property
    def entries(self) -> list[AppEntry]:
        with self._lock:
            return self._fast_entries + self._slow_entries

    def _score(self, q: str, e: AppEntry) -> float:
        """
        Rank by what he MEANT, not by string distance.

        Item 1e in order of authority:
          exact key            > everything
          whole word in name   > substring        <- the Chrome fix
          real executable      > a shortcut       <- App Paths beats a .lnk
          anything alive       > a dead shortcut  <- item 1d
        """
        key = e.key
        if not q:
            return 0.0

        qwords = q.split()
        kwords = key.replace("-", " ").replace("_", " ").split()

        score = 0.0
        if key == q:
            score = 1000.0
        elif q in kwords:
            # A WHOLE WORD. "chrome" is a word in "google chrome" and is not a
            # word in "opera browser" — which is the entire bug this fixes.
            score = 800.0
            if kwords and kwords[0] == q:
                score += 40.0          # "chrome ..." beats "... chrome"
        elif all(w in kwords for w in qwords):
            score = 760.0
        elif key.startswith(q):
            score = 600.0
        elif q in key:
            score = 420.0
        elif key in q:
            score = 380.0
        else:
            ratio = SequenceMatcher(None, q, key).ratio()
            if ratio < 0.72:
                return 0.0
            # DELIBERATELY CAPPED BELOW EVERY STRUCTURAL MATCH. A ratio is a
            # guess; a whole-word hit is evidence. Letting a 0.9 ratio outrank
            # an 800 word match is exactly how "chrome browser" returned Opera.
            score = 200.0 + ratio * 100.0

        # A shortcut whose binary is gone must never be offered (item 1d).
        if not e.alive:
            return 0.0
        # A real binary beats a shortcut to one.
        if e.kind in ("exe", "shell"):
            score += 25.0
        # Fewer extra words means a closer name: "chrome" over "chrome canary".
        score -= max(0, len(kwords) - len(qwords)) * 1.5
        return score

    def resolve(self, query: str, *, allow_rebuild: bool = True
                ) -> tuple[list[AppEntry], str]:
        """
        (best_entries, how). More than one entry means genuine ambiguity.

        REBUILD-ON-MISS: a query that finds nothing rebuilds the index once and
        retries, so an application installed after the daemon started is found
        the first time he asks for it rather than after a restart.
        """
        self.ensure()
        q = _norm_query(query)
        if not q:
            return [], "empty"

        # THE SAME NEAR-MISS BOUNDARY THE FILE RULES HAVE (see phrasings.py).
        #
        # "open this" fuzzy-matched an app called "Tips" at ratio 0.75 and
        # launched it. A query made entirely of pronouns is a mistranscribed
        # sentence, not a name, and the honest answer is a miss. Same failure as
        # `fs.list path="Me"`, one module over.
        if all(w in _NOT_A_NAME for w in q.split()):
            return [], "not-a-name"

        best, how = self._rank(q)
        if best:
            return best, how

        # ── A MISS IS ALLOWED TO WAIT FOR THE SLOW TIER ─────────────────────
        #
        # The two tiers exist so he never waits at STARTUP. A miss is different:
        # he has already asked, she is already thinking, and the alternative to
        # waiting is telling him she cannot find an application that is sitting
        # in the index eight seconds later.
        #
        # This is the cold-start case and it is real: on a fresh process with no
        # cache, "open whatsapp" resolved exactly against the full index and
        # came back UNROUTED through the daemon, because WhatsApp is a UWP
        # package that only the slow tier knows about and the slow tier had not
        # landed yet.
        if allow_rebuild and not self._slow_built:
            t = self.build_async()
            if t is not None:
                t.join(timeout=SLOW_WAIT_S)
            best, how = self._rank(q)
            if best:
                return best, how + "-after-slow"

        if allow_rebuild and self._miss_rebuilds < 8:
            self._miss_rebuilds += 1
            self.build_fast()
            self.build_async()
            best, how = self._rank(q)
            if best:
                return best, how + "-after-rebuild"
        return [], "no-match"

    def _rank(self, q: str) -> tuple[list[AppEntry], str]:
        scored: list[tuple[float, AppEntry]] = []
        for e in self.entries:
            s = self._score(q, e)
            if s > 0:
                scored.append((s, e))
        if not scored:
            return [], "no-match"
        scored.sort(key=lambda t: -t[0])
        top = scored[0][0]

        # DEDUPE BY WHAT IT ACTUALLY STARTS, not by the path we found it at.
        #
        # `chrome` (App Paths), `Google Chrome` (Start Menu .lnk) and
        # `Google Chrome` (Get-StartApps) are ONE program reached three ways.
        # Keying on the full path made them three different identities, so she
        # offered "Notepad, Notepad or Notepad" as a genuine choice — an
        # ambiguity that exists only in the index, never in his machine.
        #
        # The identity is the BASENAME of the resolved binary: all three
        # Notepads collapse to `notepad.exe`. Falling back to the normalised
        # display name covers UWP packages and shell verbs, which have no
        # binary to point at.
        # TWO IDENTITIES, BOTH REQUIRED.
        #
        # By BINARY, so `chrome` (App Paths) and `Google Chrome` (Start Menu)
        # collapse even though their names differ.
        #
        # By NAME, so `Calculator` the shell verb and `Calculator` the UWP
        # package collapse even though their launch mechanisms differ and
        # neither resolves to a comparable path. Two things with the same name
        # are one thing to him; which mechanism starts it is our problem, not
        # a question to put to him.
        #
        # Ties already favour a real binary — `_score` adds 25 for exe/shell —
        # so the survivor is the most directly launchable of the duplicates.
        seen_bin: set[str] = set()
        seen_name: set[str] = set()
        winners: list[AppEntry] = []
        for s, e in scored:
            if top - s > 30.0:
                break
            ident = _identity(e)
            if ident in seen_bin or e.key in seen_name:
                continue
            seen_bin.add(ident)
            seen_name.add(e.key)
            winners.append(e)

        if len(winners) == 1:
            return winners[:1], "exact" if top >= 1000 else (
                "word" if top >= 700 else "fuzzy")
        return winners[:4], "ambiguous"


#: One index for the process. Built once, shared.
_INDEX: AppIndex | None = None
_INDEX_LOCK = threading.Lock()


def get_index() -> AppIndex:
    global _INDEX
    with _INDEX_LOCK:
        if _INDEX is None:
            _INDEX = AppIndex()
            _INDEX.ensure()
    return _INDEX


def warm(background: bool = True) -> AppIndex:
    """Called at daemon start so he never waits for the slow tier."""
    idx = get_index()
    if background:
        idx.build_async()
    else:
        idx.build_fast()
        idx.build_slow()
    return idx


def launch(entry: AppEntry) -> tuple[bool, str]:
    """
    Start it. Python owns execution; the model never supplies a command string
    (CLAUDE.md invariant 4) — this takes an AppEntry we built, not text.
    """
    try:
        if entry.kind == "uwp":
            # UWP packages launch only through the shell's AppsFolder.
            subprocess.Popen(
                ["explorer.exe", f"shell:AppsFolder\\{entry.launch}"],
                close_fds=True,
            )
            return True, entry.name
        if entry.kind == "shell":
            if entry.launch.startswith("ms-settings:"):
                os.startfile(entry.launch)  # noqa: S606
            else:
                subprocess.Popen([entry.launch], close_fds=True)
            return True, entry.name
        os.startfile(entry.launch)  # noqa: S606 - a path we indexed, never model text
        return True, entry.name
    except OSError as exc:
        return False, f"{type(exc).__name__}: {exc}"
