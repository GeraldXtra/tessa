from __future__ import annotations

import ctypes
import hashlib
import json
import logging
import os
import re
import sqlite3
import struct
import subprocess
import threading
import webbrowser
from contextlib import contextmanager
from ctypes import wintypes
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator

ROOT = Path(__file__).resolve().parents[2]
STORE_PATH = ROOT / "data" / "gate.db"
WRITTEN = ROOT / "data" / "code" / "written.jsonl"
LOG = logging.getLogger("tessa.gate")
SCHEMA = 1

OWN, APP, PAGE, ITEM, CODE = "own", "app", "page", "item", "code"
KINDS = (OWN, APP, PAGE, ITEM, CODE)

SOURCE_EXTS = frozenset({
    ".py", ".pyw", ".pyz", ".js", ".mjs", ".cjs", ".ts", ".mts", ".cts", ".jsx", ".tsx", ".java", ".kt", ".kts",
    ".groovy", ".scala", ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh", ".cs", ".csx", ".fs", ".fsx", ".vb",
    ".f", ".f90", ".f95", ".for", ".rs", ".go", ".ps1", ".psm1", ".psd1", ".ps1xml", ".bat", ".cmd", ".sh",
    ".bash", ".zsh", ".pl", ".pm", ".rb", ".lua", ".php", ".r", ".jl", ".dart", ".swift", ".tcl", ".awk",
    ".ipynb", ".pth",
})
PROGRAM_EXTS = frozenset({
    ".exe", ".com", ".scr", ".pif", ".dll", ".pyd", ".so", ".node", ".jar", ".class", ".msi", ".msp", ".mst",
    ".msix", ".msixbundle", ".appx", ".appxbundle", ".appinstaller", ".application", ".appref-ms", ".cpl",
    ".msc", ".hta", ".vbs", ".vbe", ".jse", ".wsf", ".wsh", ".wsc", ".sct", ".reg", ".inf", ".lnk", ".url",
    ".scf", ".chm", ".gadget", ".settingcontent-ms", ".library-ms", ".search-ms", ".searchconnector-ms",
    ".diagcab", ".msh", ".mshxml", ".psc1", ".xbap", ".xll", ".docm", ".dotm", ".xlsm", ".xltm", ".xlam",
    ".pptm", ".potm", ".ppam", ".ppsm", ".sldm", ".accdb", ".mdb", ".iqy", ".slk", ".website", ".theme",
    ".themepack", ".deskthemepack", ".rdp", ".mof",
})
RUNNABLE_EXTS = SOURCE_EXTS | PROGRAM_EXTS
DISPLAY_EXTS = frozenset({
    ".txt", ".md", ".markdown", ".log", ".csv", ".tsv", ".json", ".xml", ".yaml", ".yml", ".toml", ".ini",
    ".pdf", ".epub", ".rtf", ".odt", ".ods", ".odp", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".ico", ".heic", ".svg", ".psd",
    ".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".wma", ".opus", ".mid", ".midi",
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".webm", ".m4v", ".mpg", ".mpeg", ".3gp",
    ".html", ".htm", ".zip", ".7z", ".rar", ".tar", ".gz",
})
DEPENDENCY_DIRS = frozenset({
    "node_modules", ".git", "__pycache__", ".venv", "venv", "env", ".env", "site-packages", ".tox", ".mypy_cache",
    ".pytest_cache", "target", "build", "dist", "bin", "obj", ".gradle", ".idea", ".next", "bower_components",
    "vendor", "runs",
})
HOSTS = frozenset({
    "cmd.exe", "powershell.exe", "powershell_ise.exe", "pwsh.exe", "wscript.exe", "cscript.exe", "mshta.exe",
    "rundll32.exe", "regsvr32.exe", "msiexec.exe", "explorer.exe", "conhost.exe", "openconsole.exe",
    "windowsterminal.exe", "wt.exe", "bash.exe", "wsl.exe", "wslhost.exe", "sh.exe", "python.exe",
    "pythonw.exe", "py.exe", "pyw.exe", "node.exe", "java.exe", "javaw.exe", "perl.exe", "ruby.exe", "php.exe",
    "msbuild.exe", "installutil.exe", "regasm.exe", "regsvcs.exe", "cmstp.exe", "forfiles.exe", "pcalua.exe",
    "schtasks.exe", "at.exe", "wmic.exe", "certutil.exe", "bitsadmin.exe", "hh.exe", "control.exe",
    "mmc.exe", "reg.exe", "regedit.exe", "dotnet.exe", "code.exe", "chrome.exe", "msedge.exe", "firefox.exe",
})
MAX_FILES = 200
MAX_VIEW_BYTES = 64 * 1024
MAX_VIEW_TOTAL = 512 * 1024
MAX_HASH_BYTES = 512 * 1024 * 1024
MAX_DEP_COUNT = 20000
MAX_WALK_ENTRIES = 10000
MAX_CARD_BYTES = 768 * 1024
LEDGER_TREE_FILES = 2000
CLOUD_BITS = 0x00400000 | 0x00040000 | 0x00001000 | 0x00000400
_SYSTEM_TOOLS = {"powershell": ("WindowsPowerShell", "v1.0", "powershell.exe")}


def _refuse(reason: str, alternative: str = "Nothing was started.") -> Any:
    from core.tools.base import ToolError

    raise ToolError(reason, alternative)


def norm(path: Any) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def under(path: Any, root: Any) -> bool:
    a, b = norm(path), norm(root).rstrip("\\/")
    return a == b or a.startswith(b + os.sep)


def sha256_file(path: Any) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _attrs(path: Any) -> int:
    try:
        a = ctypes.windll.kernel32.GetFileAttributesW(str(path))
    except Exception:
        return 0
    return 0 if a == 0xFFFFFFFF else int(a)


def code_folder() -> Path:
    try:
        from core.tools import coding

        return Path(coding.code_folder())
    except Exception:
        return Path.home() / "Documents" / "Tessa Code"


def admin_roots() -> list[Path]:
    out: list[Path] = []
    for var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "SystemRoot"):
        v = os.environ.get(var)
        if v and Path(v) not in out:
            out.append(Path(v))
    return out


def machine_start_menu() -> Path:
    return Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "Microsoft" / "Windows" / "Start Menu"


def user_start_menu() -> Path:
    return Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))) / "Microsoft" / "Windows" / "Start Menu"


def _elevated_now() -> bool:
    try:
        advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
        advapi.OpenProcessToken.restype = wintypes.BOOL
        advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                               ctypes.POINTER(wintypes.DWORD)]
        advapi.GetTokenInformation.restype = wintypes.BOOL
        token = wintypes.HANDLE()
        if not advapi.OpenProcessToken(k32.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
            return True
        try:
            value = wintypes.DWORD()
            size = wintypes.DWORD()
            if not advapi.GetTokenInformation(token, 20, ctypes.byref(value), 4, ctypes.byref(size)):
                return True
            return bool(value.value)
        finally:
            k32.CloseHandle(token)
    except Exception:
        return True


_ELEVATED: dict[str, bool] = {}


def elevated() -> bool:
    if "v" not in _ELEVATED:
        _ELEVATED["v"] = _elevated_now()
    return _ELEVATED["v"]


class Store:
    def __init__(self, path: Any = None, written: Any = None) -> None:
        self.path = Path(path) if path is not None else STORE_PATH
        self.written = Path(written) if written is not None else WRITTEN
        self._lock = threading.RLock()
        self._db: sqlite3.Connection | None = None
        self.too_new = False
        self.version_found: int | None = None

    def _conn(self) -> sqlite3.Connection:
        if self._db is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(str(self.path), check_same_thread=False, timeout=10.0)
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=NORMAL")
            self._migrate(db)
            self._db = db
        return self._db

    def _migrate(self, db: sqlite3.Connection) -> None:
        found = int(db.execute("PRAGMA user_version").fetchone()[0])
        self.version_found = found
        if found > SCHEMA:
            self.too_new = True
            LOG.warning("gate store %s is schema %s, newer than %s: nothing counts as approved", self.path, found, SCHEMA)
            return
        with db:
            db.execute("CREATE TABLE IF NOT EXISTS gate_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS gate_approved (set_sha TEXT PRIMARY KEY, kind TEXT NOT NULL, "
                       "entry TEXT NOT NULL, files TEXT NOT NULL, argv TEXT NOT NULL, request_id TEXT NOT NULL, "
                       "at TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS gate_ledger (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                       "path TEXT NOT NULL, norm TEXT NOT NULL, sha256 TEXT NOT NULL, op TEXT NOT NULL, "
                       "tool TEXT NOT NULL, at TEXT NOT NULL)")
            db.execute("CREATE INDEX IF NOT EXISTS gate_ledger_norm ON gate_ledger(norm)")
            db.execute("CREATE INDEX IF NOT EXISTS gate_ledger_sha ON gate_ledger(sha256)")
            if found < 1:
                db.execute("PRAGMA user_version = 1")
        self._import_written(db)

    def _import_written(self, db: sqlite3.Connection) -> None:
        if db.execute("SELECT value FROM gate_meta WHERE key = 'written_imported'").fetchone() is not None:
            return
        rows = []
        try:
            text = self.written.read_text(encoding="utf-8")
        except OSError:
            text = ""
        for line in text.splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            p = str(r.get("path") or "")
            if p:
                rows.append((p, norm(p), str(r.get("sha256") or ""), "write", "code.write", str(r.get("at") or _now())))
        with db:
            db.executemany("INSERT INTO gate_ledger (path, norm, sha256, op, tool, at) VALUES (?, ?, ?, ?, ?, ?)", rows)
            db.execute("INSERT INTO gate_meta (key, value) VALUES ('written_imported', ?)", (str(len(rows)),))

    def tables(self) -> list[str]:
        with self._lock:
            return [r[0] for r in self._conn().execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]

    def is_approved(self, set_sha: str) -> bool:
        with self._lock:
            db = self._conn()
            if self.too_new:
                return False
            return db.execute("SELECT 1 FROM gate_approved WHERE set_sha = ?", (set_sha,)).fetchone() is not None

    def approve(self, set_sha: str, kind: str, entry: str, files: list[dict[str, Any]], argv: list[str],
                request_id: str) -> None:
        with self._lock:
            db = self._conn()
            if self.too_new:
                return
            with db:
                db.execute("INSERT OR REPLACE INTO gate_approved (set_sha, kind, entry, files, argv, request_id, at) "
                           "VALUES (?, ?, ?, ?, ?, ?, ?)",
                           (set_sha, kind, entry, json.dumps([[f["rel"], f["sha256"]] for f in files]),
                            json.dumps(list(argv)), request_id, _now()))

    def record(self, path: Any, op: str, tool: str, sha: str | None = None) -> None:
        p = Path(str(path))
        rows: list[tuple[str, str, str, str, str, str]] = []
        if p.is_dir():
            rows.append((str(p), norm(p), "", op + "-tree", tool, _now()))
            n = 0
            for dp, dns, fns in os.walk(p):
                for fn in fns:
                    if n >= LEDGER_TREE_FILES:
                        break
                    f = Path(dp) / fn
                    rows.append((str(f), norm(f), _safe_sha(f), op, tool, _now()))
                    n += 1
        else:
            rows.append((str(p), norm(p), sha if sha is not None else _safe_sha(p), op, tool, _now()))
        with self._lock:
            db = self._conn()
            if self.too_new:
                return
            with db:
                db.executemany("INSERT INTO gate_ledger (path, norm, sha256, op, tool, at) VALUES (?, ?, ?, ?, ?, ?)",
                               rows)

    def hers(self, path: Any, sha: str = "") -> bool:
        if under(path, code_folder()):
            return True
        n = norm(path)
        with self._lock:
            db = self._conn()
            if db.execute("SELECT 1 FROM gate_ledger WHERE norm = ? LIMIT 1", (n,)).fetchone() is not None:
                return True
            if sha and db.execute("SELECT 1 FROM gate_ledger WHERE sha256 = ? LIMIT 1", (sha,)).fetchone() is not None:
                return True
            for (root,) in db.execute("SELECT norm FROM gate_ledger WHERE op LIKE '%-tree'"):
                if n == root or n.startswith(root.rstrip("\\/") + os.sep):
                    return True
        return False

    def ledger_rows(self) -> list[tuple[str, str, str, str]]:
        with self._lock:
            return list(self._conn().execute("SELECT path, sha256, op, tool FROM gate_ledger ORDER BY id"))


def _safe_sha(p: Path) -> str:
    try:
        if p.is_file() and not _attrs(p) & CLOUD_BITS and p.stat().st_size <= MAX_HASH_BYTES:
            return sha256_file(p)
    except OSError:
        pass
    return ""


_STORE: dict[str, Any] = {}


def store_paths() -> tuple[Path, Path]:
    try:
        from core.tools import coding

        data = Path(coding.DATA)
    except Exception:
        data = WRITTEN.parent
    return data.parent / STORE_PATH.name, data / WRITTEN.name


def store() -> Store:
    s = _STORE.get("s")
    if s is not None and _STORE.get("pinned"):
        return s
    path, written = store_paths()
    if s is None or norm(s.path) != norm(path):
        s = Store(path, written)
        _STORE["s"] = s
    return s


def use_store(s: Store) -> None:
    _STORE["s"] = s
    _STORE["pinned"] = True


def record(path: Any, op: str, tool: str, sha: str | None = None) -> None:
    try:
        store().record(path, op, tool, sha)
    except Exception as exc:
        LOG.warning("gate ledger could not record %s %s: %s", op, path, exc)


def protected_locations() -> list[tuple[Path, str]]:
    home = Path.home()
    appdata = Path(os.environ.get("APPDATA", str(home / "AppData" / "Roaming")))
    local = Path(os.environ.get("LOCALAPPDATA", str(home / "AppData" / "Local")))
    progdata = Path(os.environ.get("ProgramData", r"C:\ProgramData"))
    docs = home / "Documents"
    try:
        from core.tools import coding

        docs = Path(coding.documents())
    except Exception:
        pass
    out = [
        (ROOT, "my own code, settings and stores"),
        (STORE_PATH.parent, "my own stores"),
        (local / "Tessa", "my own stores"),
        (user_start_menu(), "the Start menu, which my app index reads"),
        (machine_start_menu(), "the Start menu, which my app index reads"),
        (appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup", "a folder Windows starts programs from"),
        (progdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "StartUp", "a folder Windows starts programs from"),
        (docs / "WindowsPowerShell", "PowerShell's profile folder, which runs at every start"),
        (docs / "PowerShell", "PowerShell's profile folder, which runs at every start"),
        (appdata / "Python", "Python's per-user packages, which run at every start"),
        (home / ".gitconfig", "git's settings"),
        (home / ".config" / "git", "git's settings"),
        (home / ".ssh", "ssh's settings"),
        (home / ".bashrc", "a shell start file"),
        (home / ".bash_profile", "a shell start file"),
        (home / ".profile", "a shell start file"),
        (home / ".npmrc", "npm's settings"),
    ]
    return out


def protected_reason(path: Any) -> str:
    try:
        p = Path(os.path.expandvars(str(path))).expanduser()
        full = p.resolve() if p.is_absolute() else p
    except (OSError, RuntimeError, ValueError):
        full = Path(str(path))
    for root, why in protected_locations():
        if under(full, root) or under(path, root):
            return why
    parts = [x.lower() for x in Path(str(full)).parts]
    if ".git" in parts[:-1] or (parts and parts[-1] == ".git"):
        return "a git repository's own folder, where its hooks and settings live"
    return ""


def refuse_protected(*paths: Any) -> None:
    for p in paths:
        if p in (None, ""):
            continue
        why = protected_reason(p)
        if why:
            _refuse(f"{p} is in {why}, and my file tools never write there",
                    "Do that one yourself, or name another place.")


@dataclass(frozen=True)
class Piece:
    rel: str
    path: str
    sha256: str
    bytes: int
    hers: bool
    text: str | None

    def card(self) -> dict[str, Any]:
        row: dict[str, Any] = {"file": self.rel, "path": self.path, "sha256": self.sha256, "bytes": self.bytes,
                               "hers": self.hers}
        if self.text is not None:
            row["lines"] = len(self.text.splitlines())
        else:
            row["view"] = f"not text, or too long for the card: open {self.path} to read it, and check its sha256"
        return row


@dataclass
class FileSet:
    entry: str
    root: str
    pieces: list[Piece]
    unchecked: list[dict[str, Any]]
    argv: list[str]
    kind: str = CODE

    @property
    def set_sha(self) -> str:
        return set_sha(self.kind, self.entry, [(p.rel, p.sha256) for p in self.pieces], self.argv)

    def card_files(self) -> list[dict[str, Any]]:
        return [p.card() for p in self.pieces]

    def views(self) -> dict[str, str]:
        return {p.rel: p.text for p in self.pieces if p.text is not None}


def set_sha(kind: str, entry: str, pairs: list[tuple[str, str]], argv: list[str]) -> str:
    body = json.dumps({"kind": kind, "entry": entry, "files": sorted([list(x) for x in pairs]), "argv": list(argv)},
                      sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except (ValueError, OSError):
        return str(path)


def rel_to(path: Any, root: Any) -> str:
    return _rel(Path(str(path)), Path(str(root)))


def _text_of(raw: bytes) -> str | None:
    if len(raw) > MAX_VIEW_BYTES or b"\x00" in raw:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def program_root(entry: Path) -> Path:
    return entry.parent


def walk_runnable(root: Path, entry: Path | None = None) -> tuple[list[Path], list[dict[str, Any]]]:
    found: list[Path] = []
    unchecked: list[dict[str, Any]] = []
    stack = [root]
    seen = 0
    while stack:
        d = stack.pop()
        try:
            items = sorted(os.scandir(d), key=lambda e: e.name.lower())
        except OSError as exc:
            _refuse(f"I could not read the folder {d} ({exc.strerror or exc})", "Nothing ran.")
        seen += len(items)
        if seen > MAX_WALK_ENTRIES:
            _refuse(f"the program's folder {root} holds more than {MAX_WALK_ENTRIES} files and folders, too many to "
                    f"check before it runs", "Move the program into a folder of its own and ask again.")
        for e in items:
            try:
                attrs = e.stat(follow_symlinks=False).st_file_attributes
            except (OSError, AttributeError):
                attrs = 0
            if e.is_dir(follow_symlinks=False):
                if attrs & 0x400:
                    unchecked.append({"folder": _rel(Path(e.path), root), "why": "a link to another place",
                                      "files": None})
                    continue
                if e.name.lower() in DEPENDENCY_DIRS:
                    unchecked.append({"folder": _rel(Path(e.path), root), "why": "a dependency or build folder",
                                      "files": _count_files(Path(e.path))})
                    continue
                stack.append(Path(e.path))
                continue
            p = Path(e.path)
            if Path(e.name).suffix.lower() in RUNNABLE_EXTS or (entry is not None and norm(p) == norm(entry)):
                if attrs & 0x400 or attrs & CLOUD_BITS:
                    _refuse(f"{p.name} is a cloud placeholder or a link, so I cannot check it without downloading it",
                            "Make the folder available offline first.")
                found.append(p)
                if len(found) > MAX_FILES:
                    _refuse(f"the program's folder {root} holds more than {MAX_FILES} files that can run, too many "
                            f"to show you in full", "Move the program into a folder of its own and ask again.")
    if entry is not None and not any(norm(p) == norm(entry) for p in found):
        found.append(entry)
    found.sort(key=lambda p: _rel(p, root).lower())
    return found, unchecked


def _count_files(d: Path) -> int:
    n = 0
    for _dp, _dns, fns in os.walk(d):
        n += len(fns)
        if n >= MAX_DEP_COUNT:
            return n
    return n


def program_set(entry: Any, argv: list[str] | None = None, *, root: Any = None, kind: str = CODE,
                extra: list[Path] | None = None) -> FileSet:
    e = Path(str(entry))
    r = Path(str(root)) if root is not None else program_root(e)
    paths, unchecked = walk_runnable(r, e)
    for x in extra or []:
        if not any(norm(x) == norm(p) for p in paths):
            paths.append(x)
    return FileSet(entry=_rel(e, r), root=str(r), pieces=file_pieces(paths, r), unchecked=unchecked,
                   argv=list(argv or []), kind=kind)


def file_pieces(paths: list[Path], r: Path) -> list[Piece]:
    s = store()
    pieces: list[Piece] = []
    shown = 0
    for p in paths:
        size = p.stat().st_size
        if size > MAX_HASH_BYTES:
            _refuse(f"{p.name} is larger than {MAX_HASH_BYTES // (1024 * 1024)} megabytes", "Nothing ran.")
        text = None
        if size <= MAX_VIEW_BYTES:
            raw = p.read_bytes()
            sha = hashlib.sha256(raw).hexdigest()
            text = _text_of(raw)
            size = len(raw)
        else:
            sha = sha256_file(p)
        if text is not None and shown + size > MAX_VIEW_TOTAL:
            text = None
        if text is not None:
            shown += size
        pieces.append(Piece(rel=_rel(p, r), path=str(p), sha256=sha, bytes=size, hers=s.hers(p, sha), text=text))
    return pieces


def json_bytes(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))


def fit_card(card: dict[str, Any]) -> dict[str, Any]:
    views = dict(card.get("views") or {})
    base = json_bytes({k: v for k, v in card.items() if k != "views"})
    cost = {rel: json_bytes([rel, text]) for rel, text in views.items()}
    total = base + sum(cost.values())
    rows = {str(r.get("file")): r for r in card.get("files") or [] if isinstance(r, dict)}
    for rel in sorted(cost, key=lambda k: -cost[k]):
        if total <= MAX_CARD_BYTES:
            break
        total -= cost[rel]
        views.pop(rel)
        row = rows.get(rel)
        if row is not None:
            row["view"] = f"too long to show with the rest of the card: open {row.get('path')} to read it, and check its sha256"
    if base > MAX_CARD_BYTES:
        _refuse("the card for this would be too big to send to your screen",
                "Read the files yourself, then run it from your own terminal.")
    card["views"] = views
    return card


_GENERIC_READ = 0x80000000
_FILE_SHARE_READ = 0x00000001
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_NORMAL = 0x80


def _open_locked(path: str) -> Any:
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                wintypes.DWORD, wintypes.HANDLE]
    k32.CreateFileW.restype = wintypes.HANDLE
    h = k32.CreateFileW(str(path), _GENERIC_READ, _FILE_SHARE_READ, None, _OPEN_EXISTING, _FILE_ATTRIBUTE_NORMAL, None)
    if h is None or h == ctypes.c_void_p(-1).value:
        err = ctypes.get_last_error()
        raise OSError(err, f"could not hold {path} still (Windows error {err})")
    import msvcrt

    fd = msvcrt.open_osfhandle(int(h), os.O_RDONLY | getattr(os, "O_BINARY", 0))
    return os.fdopen(fd, "rb")


@dataclass
class Held:
    kind: str
    entry: str
    root: str
    paths: list[str]
    approved_by: str
    built: set[str] = field(default_factory=set)
    active: bool = True
    thread: int = 0

    def allow_built(self, path: Any) -> None:
        self.built.add(norm(path))


_CURRENT = threading.local()


def current() -> Held | None:
    h = getattr(_CURRENT, "held", None)
    if h is not None and h.active and h.thread == threading.get_ident():
        return h
    return None


@contextmanager
def verified(files: list[dict[str, Any]], *, entry: str, root: str, kind: str = CODE, approved_by: str,
             argv: list[str] | None = None, walk: bool = True) -> Iterator[Held]:
    want = {str(f["rel"] if "rel" in f else f["file"]): str(f["sha256"]) for f in files}
    paths = {str(f["rel"] if "rel" in f else f["file"]): str(f["path"]) for f in files}
    if walk:
        r = Path(root)
        e = Path(r, entry) if not Path(entry).is_absolute() else Path(entry)
        now_paths, _unchecked = walk_runnable(r, e)
        now = {_rel(p, r) for p in now_paths}
        if now != set(want):
            added = sorted(now - set(want))
            gone = sorted(set(want) - now)
            what = []
            if added:
                what.append("new since the card: " + ", ".join(added[:5]))
            if gone:
                what.append("gone since the card: " + ", ".join(gone[:5]))
            _refuse("the program's files changed after you approved it (" + "; ".join(what) + "), so nothing ran",
                    "Ask me again and read the new card.")
    handles = []
    try:
        for rel, sha in want.items():
            try:
                fh = _open_locked(paths[rel])
            except OSError as exc:
                _refuse(f"I could not hold {rel} still to check it ({exc}), so nothing ran", "Close whatever has it open and ask again.")
            handles.append(fh)
            h = hashlib.sha256()
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
            if h.hexdigest() != sha:
                _refuse(f"{rel} changed after you approved it, so nothing ran", "Ask me again and read the new card.")
        held = Held(kind=kind, entry=entry, root=root, paths=[norm(p) for p in paths.values()],
                    approved_by=approved_by, thread=threading.get_ident())
        prev = getattr(_CURRENT, "held", None)
        _CURRENT.held = held
        try:
            yield held
        finally:
            held.active = False
            _CURRENT.held = prev
    finally:
        for fh in handles:
            try:
                fh.close()
            except OSError:
                pass


def _resolve_own(argv: list[str]) -> list[str]:
    if not argv:
        _refuse("an empty program line", "Nothing was started.")
    first = str(argv[0])
    if os.path.dirname(first):
        return [first, *argv[1:]]
    sysroot = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    base = first.lower()
    stem = base[:-4] if base.endswith(".exe") else base
    if stem in _SYSTEM_TOOLS:
        cand = sysroot / "System32" / Path(*_SYSTEM_TOOLS[stem])
        if cand.is_file():
            return [str(cand), *argv[1:]]
    for cand in (sysroot / "System32" / (stem + ".exe"), sysroot / (stem + ".exe")):
        if cand.is_file():
            return [str(cand), *argv[1:]]
    return [first, *argv[1:]]


@dataclass
class Start:
    kind: str
    why: str
    argv: list[str] | None = None
    mode: str = "run"
    options: dict[str, Any] = field(default_factory=dict)
    path: str = ""
    url: str = ""
    entry: Any = None
    launcher: Any = None


SEEN: list[dict[str, Any]] = []


def _seen(kind: str, what: str, why: str) -> None:
    SEEN.append({"kind": kind, "what": what, "why": why})
    del SEEN[:-200]
    LOG.info("gate %s: %s (%s)", kind, what, why)


def start(req: Start) -> Any:
    if req.kind not in KINDS:
        _refuse(f"the gate does not know the kind {req.kind!r}", "Nothing was started.")
    opts = dict(req.options)
    if opts.get("shell"):
        _refuse("a shell line, which the gate never starts", "Nothing was started.")
    if req.kind == OWN:
        if req.mode == "browser":
            _seen(OWN, "her browser", req.why)
            return req.launcher.launch_persistent_context(**opts)
        argv = _resolve_own([str(a) for a in (req.argv or [])])
        _seen(OWN, Path(argv[0]).name, req.why)
        return _spawn(argv, req.mode, opts)
    if req.kind == PAGE:
        url = str(req.url or "")
        if not re.match(r"^https?://[^\s]+$", url):
            _refuse(f"{url!r} is not a web address", "Give me a full web address.")
        if req.argv:
            argv = [str(a) for a in req.argv]
            if Path(argv[0]).name.lower() not in ("chrome.exe", "msedge.exe", "firefox.exe", "brave.exe") or argv[1:] != [url]:
                _refuse("a page opens only in a browser, with the address alone", "Nothing was started.")
            _seen(PAGE, url, req.why)
            return _spawn(argv, "popen", opts)
        _seen(PAGE, url, req.why)
        if req.mode == "default":
            return os.startfile(url)
        return webbrowser.open(url)
    if req.kind == ITEM:
        p = Path(req.path)
        if req.mode == "reveal":
            _seen(ITEM, "select " + str(p), req.why)
            return _spawn([_resolve_own(["explorer.exe"])[0], f"/select,{p}"], "popen", {})
        if item_kind(p) != "display":
            _refuse(f"{p.name} is something Windows would run, not show", "Nothing was opened.")
        _seen(ITEM, str(p), req.why)
        return os.startfile(str(p))
    if req.kind == APP:
        verdict = app_verdict(req.entry)
        if not verdict["open"] and not _approved_app(verdict):
            _refuse(f"{verdict['name']} needs your card first: {verdict['why']}", "Approve the card and I will open it.")
        _seen(APP, verdict["name"], req.why)
        if req.argv:
            argv = [str(a) for a in req.argv]
            if norm(argv[0]) != norm(getattr(req.entry, "launch", "")):
                _refuse("that is not the program that was checked", "Nothing was started.")
            return _spawn(argv, "popen", {"close_fds": True, **opts})
        return _launch_app(req.entry, opts)
    held = current()
    argv = [str(a) for a in (req.argv or [])]
    if held is None:
        _refuse("code runs only after your card, or from a program you approved before", "Nothing ran.")
    if not argv:
        _refuse("an empty program line", "Nothing ran.")
    if not (norm(argv[0]) in held.built or any(norm(a) in held.paths for a in argv[1:]) or held.kind == "shell"):
        _refuse("that is not the program you approved", "Nothing ran.")
    _seen(CODE, Path(argv[0]).name + " " + held.entry, req.why)
    return _spawn(argv, req.mode, opts)


def _spawn(argv: list[str], mode: str, opts: dict[str, Any]) -> Any:
    if mode == "popen":
        return subprocess.Popen(argv, **opts)
    return subprocess.run(argv, **opts)


def run(argv: list[str], *, why: str, **options: Any) -> Any:
    return start(Start(kind=OWN, why=why, argv=list(argv), mode="run", options=options))


def popen(argv: list[str], *, why: str, **options: Any) -> Any:
    return start(Start(kind=OWN, why=why, argv=list(argv), mode="popen", options=options))


def item_kind(p: Path) -> str:
    if p.is_dir():
        return "display"
    ext = p.suffix.lower()
    if ext in SOURCE_EXTS:
        return "source"
    if ext in DISPLAY_EXTS:
        return "display"
    return "program"


@dataclass(frozen=True)
class Shortcut:
    target: str
    arguments: str
    workdir: str
    advertised: bool


def read_shortcut(path: Any) -> Shortcut:
    raw = Path(str(path)).read_bytes()
    if len(raw) < 0x4C or struct.unpack_from("<I", raw, 0)[0] != 0x4C:
        raise ValueError("not a shortcut file")
    flags = struct.unpack_from("<I", raw, 0x14)[0]
    off = 0x4C
    if flags & 0x01:
        off += 2 + struct.unpack_from("<H", raw, off)[0]
    target = ""
    if flags & 0x02:
        start_li = off
        li_size, li_header, li_flags = struct.unpack_from("<III", raw, off)
        if li_flags & 0x01:
            base_off = struct.unpack_from("<I", raw, off + 16)[0]
            suffix_off = struct.unpack_from("<I", raw, off + 24)[0]
            if li_header >= 0x24:
                ubase = struct.unpack_from("<I", raw, off + 28)[0]
                usuffix = struct.unpack_from("<I", raw, off + 32)[0]
                target = _wstr(raw, start_li + ubase) + (_wstr(raw, start_li + usuffix) if usuffix else "")
            if not target:
                target = _astr(raw, start_li + base_off) + _astr(raw, start_li + suffix_off)
        off = start_li + li_size
    unicode = bool(flags & 0x80)
    strings = {}
    for bit, name in ((0x04, "name"), (0x08, "relative"), (0x10, "workdir"), (0x20, "arguments"), (0x40, "icon")):
        if flags & bit:
            n = struct.unpack_from("<H", raw, off)[0]
            off += 2
            size = n * 2 if unicode else n
            chunk = raw[off:off + size]
            strings[name] = chunk.decode("utf-16-le" if unicode else "mbcs", errors="replace")
            off += size
    advertised = False
    env_target = ""
    while off + 8 <= len(raw):
        size, sig = struct.unpack_from("<II", raw, off)
        if size < 4:
            break
        if sig == 0xA0000006:
            advertised = True
        if sig == 0xA0000001 and size >= 788:
            env_target = raw[off + 268:off + 788].decode("utf-16-le", errors="replace").split("\x00", 1)[0]
        off += size
    if not target and env_target:
        target = os.path.expandvars(env_target)
    if not target and strings.get("relative"):
        target = str((Path(str(path)).parent / strings["relative"]).resolve())
    return Shortcut(target=target, arguments=strings.get("arguments", ""), workdir=strings.get("workdir", ""),
                    advertised=advertised)


def _wstr(raw: bytes, at: int) -> str:
    end = at
    while end + 1 < len(raw) and raw[end:end + 2] != b"\x00\x00":
        end += 2
    return raw[at:end].decode("utf-16-le", errors="replace")


def _astr(raw: bytes, at: int) -> str:
    end = raw.find(b"\x00", at)
    return raw[at:end if end >= 0 else len(raw)].decode("mbcs", errors="replace")


def _in_admin_root(path: Any) -> bool:
    return any(under(path, r) for r in admin_roots())


def in_admin_root(path: Any) -> bool:
    return _in_admin_root(path)


def app_verdict(entry: Any) -> dict[str, Any]:
    kind = str(getattr(entry, "kind", ""))
    name = str(getattr(entry, "name", "") or getattr(entry, "launch", ""))
    launch = str(getattr(entry, "launch", ""))
    out: dict[str, Any] = {"name": name, "kind": kind, "launch": launch, "open": False, "why": "", "target": "",
                           "arguments": "", "workdir": "", "files": []}
    if kind == "uwp":
        out.update(open=True, why="an installed Store app")
        return out
    if kind == "shell":
        out.update(open=True, why="one of Windows' own tools")
        return out
    if elevated():
        out["why"] = "I am running as administrator, so the program folders do not prove anything is yours"
    if kind == "exe":
        target, args, workdir = launch, "", ""
    elif kind == "lnk":
        try:
            sc = read_shortcut(launch)
        except (OSError, ValueError, struct.error) as exc:
            out["why"] = f"I could not read the shortcut ({exc})"
            return out
        target, args, workdir = sc.target, sc.arguments, sc.workdir
        if sc.advertised and not target:
            out["why"] = out["why"] or "the shortcut does not name its program"
    else:
        out["why"] = f"I do not know how to check a {kind or 'nameless'} entry"
        return out
    out.update(target=target, arguments=args, workdir=workdir)
    files: list[Path] = []
    if kind == "lnk":
        files.append(Path(launch))
    if target and Path(target).is_file():
        files.append(Path(target))
    s = store()
    rows = []
    for f in files:
        try:
            sha = sha256_file(f)
        except OSError as exc:
            out["why"] = f"I could not read {f} ({exc.strerror or exc})"
            return out
        rows.append({"file": f.name, "rel": str(f), "path": str(f), "sha256": sha, "hers": s.hers(f, sha),
                     "bytes": f.stat().st_size})
    out["files"] = rows
    if out["why"]:
        return out
    if not target or not Path(target).is_file():
        out["why"] = "the program it starts is not there"
        return out
    if Path(target).suffix.lower() != ".exe":
        out["why"] = f"it starts {Path(target).name}, which is not a program file"
        return out
    if not _in_admin_root(target):
        out["why"] = f"its program is in {Path(target).parent}, a folder that programs running as you can change"
        return out
    if Path(target).name.lower() in HOSTS and args.strip():
        out["why"] = f"it hands {Path(target).name} a command line"
        return out
    if kind == "lnk" and args.strip() and not under(launch, machine_start_menu()):
        out["why"] = "the shortcut adds its own arguments, and it is in a folder you and I can change"
        return out
    if any(r["hers"] for r in rows):
        out["why"] = "I wrote or changed one of its files"
        return out
    out.update(open=True, why="an installed program")
    return out


def app_set_sha(verdict: dict[str, Any]) -> str:
    return set_sha(APP, str(verdict.get("launch") or ""), [(r["rel"], r["sha256"]) for r in verdict.get("files") or []],
                   [str(verdict.get("target") or ""), str(verdict.get("arguments") or ""), str(verdict.get("workdir") or "")])


_APP_PASS = threading.local()


def _approved_app(verdict: dict[str, Any]) -> bool:
    want = getattr(_APP_PASS, "sha", None)
    sha = app_set_sha(verdict)
    if want is not None and want == sha:
        return True
    return bool(verdict.get("files")) and store().is_approved(sha)


def app_needs_card(entry: Any) -> dict[str, Any] | None:
    verdict = app_verdict(entry)
    if verdict["open"] or _approved_app(verdict):
        return None
    return verdict


def system_program(name: str) -> str:
    return _resolve_own([name])[0]


@contextmanager
def app_pass(sha: str) -> Iterator[None]:
    _APP_PASS.sha = sha
    try:
        yield
    finally:
        _APP_PASS.sha = None


def _launch_app(entry: Any, opts: dict[str, Any]) -> Any:
    kind = str(getattr(entry, "kind", ""))
    launch = str(getattr(entry, "launch", ""))
    if kind == "uwp":
        return _spawn([_resolve_own(["explorer.exe"])[0], f"shell:AppsFolder\\{launch}"], "popen", {"close_fds": True})
    if kind == "shell":
        if launch.startswith("ms-settings:"):
            return os.startfile(launch)
        return _spawn(_resolve_own([launch]), "popen", {"close_fds": True})
    return os.startfile(launch)


def vscode_tasks_on_open(target: Path) -> str:
    candidates: list[Path] = []
    if target.is_dir():
        candidates.append(target / ".vscode" / "tasks.json")
    elif target.suffix.lower() == ".code-workspace":
        candidates.append(target)
    for c in candidates:
        try:
            text = c.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if re.search(r"folderOpen", text, re.I):
            return str(c)
    return ""
