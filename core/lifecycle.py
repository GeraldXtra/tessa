"""
core/lifecycle.py — how the daemon starts, stays single, survives its console and stops.

Every daemon from 23 Sep to 5 Oct died the same way: its console window was
closed, Intel's OpenMP runtime (ctranslate2\\libiomp5md.dll, loaded with
Whisper) answered CTRL_CLOSE with `forrtl: error (200): program aborting due to
window-CLOSE event`, and the process ended without its clean shutdown —
runtime.json left behind, no `daemon.stop` row. Separately, a second daemon's
clean exit deleted the first one's runtime.json (28 Sep). This module is the
answer to both, kept out of server.py so the server stays about the protocol.

NOTHING HERE RUNS AT IMPORT. server.py's `if __name__ == "__main__":` path
calls `take_guard`, `after_guard` and `finish`; main() binds `STOPPER` and runs
the two background tasks. A multiprocessing child re-imports the main module as
`__mp_main__` and must never take the guard or install a handler — so neither
happens at import time, here or in server.py.

    take_guard          one daemon per data dir AND per runtime dir (named mutexes)
    refuse_second_launch  the one line a refused launch writes, then exit 3
    after_guard         logs, stale stop requests, quiet children, handlers, startup line
    STOPPER             the one way to ask for a clean stop, from any thread
    watch_stop_requests the tcli stop request (proof of the token, never the token)
    log_cap_task        keeps data\\logs bounded while the daemon runs
    finish              release the guard, write the exit line, end the process
"""

from __future__ import annotations

import asyncio
import ctypes
import ctypes.wintypes as wt
import hashlib
import hmac
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

#: A refused second launch exits with this. 3 is what scripts/autostart.py
#: --check used for the same fact, so the meaning does not move.
EXIT_ALREADY_RUNNING = 3
EXIT_CRASHED = 1

#: How long a console/end-session handler waits for the clean shutdown before
#: returning. Windows ends the process 5 s after CTRL_CLOSE (and after an
#: unanswered CTRL_LOGOFF/CTRL_SHUTDOWN or WM_ENDSESSION), so the design limit
#: is 4.5 s: the clean path normally finishes in well under one second (no
#: client, no browser), and the last half-second is margin, not budget.
SHUTDOWN_LIMIT_S = 4.5

#: data\logs housekeeping. Daily files run 4-8 KB on this machine; the caps
#: exist so a fault that logs in a loop cannot fill C:.
LOG_FILE_CAP_BYTES = 5 * 1024 * 1024
LOG_DIR_CAP_BYTES = 20 * 1024 * 1024
LOG_KEEP_FILES = 60

#: The stop request tcli writes beside runtime.json. It carries an HMAC of this
#: launch's pid keyed with the token — proof of knowing the token without the
#: token itself ever being written anywhere (CONTRACT §2.3).
STOP_REQUEST_NAME = "stop-request.json"
STOP_PROOF_VERSION = "tessa-stop-v1"

_k32 = ctypes.WinDLL("kernel32", use_last_error=True) if sys.platform == "win32" else None
_u32 = ctypes.WinDLL("user32", use_last_error=True) if sys.platform == "win32" else None
if _k32 is not None:
    _k32.CreateMutexW.restype = wt.HANDLE
    _k32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
    _k32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
    _k32.WaitForSingleObject.restype = wt.DWORD
    _k32.ReleaseMutex.argtypes = [wt.HANDLE]
    _k32.CloseHandle.argtypes = [wt.HANDLE]
    _k32.GetCurrentProcess.restype = wt.HANDLE
    _k32.OpenProcess.restype = wt.HANDLE
    _k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    _k32.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
    _k32.IsProcessInJob.argtypes = [wt.HANDLE, wt.HANDLE, ctypes.POINTER(wt.BOOL)]
    _k32.GetConsoleWindow.restype = wt.HWND
    _k32.GetConsoleProcessList.argtypes = [ctypes.POINTER(wt.DWORD), wt.DWORD]
    _k32.SetStdHandle.argtypes = [wt.DWORD, wt.HANDLE]
    _k32.GetFinalPathNameByHandleW.argtypes = [wt.HANDLE, wt.LPWSTR, wt.DWORD, wt.DWORD]
    _k32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
    _k32.GetModuleHandleW.restype = wt.HMODULE
    _k32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
if _u32 is not None:
    _u32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
    _u32.IsWindowVisible.argtypes = [wt.HWND]
    _u32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]


def log(msg: str) -> None:
    """Same shape as server.log, so the daemon log reads as one voice."""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# ── THE GUARD ─────────────────────────────────────────────────────────────────
#
# DECIDED BY OWNERSHIP, NEVER BY EXISTENCE. CreateMutexW then a zero-timeout
# wait: WAIT_OBJECT_0 or WAIT_ABANDONED means THIS process now owns it;
# WAIT_TIMEOUT means a live daemon does. ERROR_ALREADY_EXISTS alone would let
# any process that merely holds a handle block every future start.
#
# A mutex belongs to the THREAD that took it, so it is taken on the main thread
# (which lives as long as the process) and the handle stays open until exit.
# A daemon that dies without releasing it leaves it ABANDONED, and the next
# daemon takes it — and says so in its startup line, which is how "the last one
# did not stop cleanly" becomes visible.
#
# ONE GUARD PER SHARED RESOURCE: the data dir (audit chain, memory) and the
# runtime dir (runtime.json). Names come from a hash of the normalised absolute
# path, so an isolated test daemon never blocks the real one, nor the reverse.
# Nothing but the daemon opens these names — tcli decides liveness by ping.

WAIT_OBJECT_0, WAIT_ABANDONED, WAIT_TIMEOUT = 0x0, 0x80, 0x102


def _norm(path: Path) -> str:
    return os.path.normcase(os.path.abspath(str(Path(path).resolve())))


def guard_name(kind: str, path: Path) -> str:
    # "Local\" is the per-session namespace prefix; the rest has no backslash.
    return f"Local\\TessaCore.{kind}.{hashlib.sha256(_norm(path).encode('utf-8')).hexdigest()[:32]}"


class Guard:
    def __init__(self) -> None:
        self.handles: list[int] = []
        self.names: list[str] = []
        self.abandoned: list[str] = []
        self.thread_id = threading.get_ident()

    def release(self) -> None:
        """Main thread only (the owner). Called once, at the very end."""
        if _k32 is None:
            return
        for h in self.handles:
            _k32.ReleaseMutex(h)
            _k32.CloseHandle(h)
        self.handles.clear()


def take_guard(data_dir: Path, runtime_dir: Path) -> tuple[Guard | None, str]:
    """(guard, "") when this process owns both; (None, "data"|"runtime") when a live daemon owns one."""
    g = Guard()
    if _k32 is None:
        return g, ""
    Path(runtime_dir).mkdir(parents=True, exist_ok=True)
    for kind, path in (("data", Path(data_dir)), ("runtime", Path(runtime_dir))):
        name = guard_name(kind, path)
        h = _k32.CreateMutexW(None, False, name)
        if not h:
            err = ctypes.get_last_error()
            g.release()
            raise OSError(f"CreateMutexW({name}) failed (error {err})")
        r = _k32.WaitForSingleObject(h, 0)
        if r in (WAIT_OBJECT_0, WAIT_ABANDONED):
            g.handles.append(h)
            g.names.append(name)
            if r == WAIT_ABANDONED:
                g.abandoned.append(kind)
            continue
        _k32.CloseHandle(h)
        g.release()
        if r == WAIT_TIMEOUT:
            return None, kind
        raise OSError(f"WaitForSingleObject({name}) returned {r:#x} (error {ctypes.get_last_error()})")
    return g, ""


def pid_alive(pid: int) -> bool:
    if _k32 is None or pid <= 0:
        return False
    h = _k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    try:
        code = wt.DWORD()
        return bool(_k32.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == 259
    finally:
        _k32.CloseHandle(h)


def _stdout_path() -> Path | None:
    """The file behind fd 1, or None when it is a console, a pipe or absent."""
    try:
        import msvcrt

        h = msvcrt.get_osfhandle(sys.stdout.fileno())
    except (AttributeError, OSError, ValueError):
        return None
    buf = ctypes.create_unicode_buffer(1024)
    n = _k32.GetFinalPathNameByHandleW(h, buf, 1024, 0)
    if not n or n >= 1024:
        return None
    p = buf.value
    return Path(p[4:] if p.startswith("\\\\?\\") else p)


def daily_log(log_dir: Path) -> Path:
    # Same name the launcher builds from %DATE%: daemon-YYYY-MM-DD.log
    return Path(log_dir) / f"daemon-{datetime.now().strftime('%Y-%m-%d')}.log"


def refuse_second_launch(runtime_file: Path, log_dir: Path, held: str) -> None:
    """
    A live daemon owns the guard. Write ONE line, never to the audit chain, and
    exit 3 — no port, no audit, no runtime.json, no log rotation. The line goes
    to this launch's own log (its redirected stdout, else an APPEND to the daily
    log, which never truncates or locks the running daemon's file) and, when
    this launch has a console, to that console as well.
    """
    pid = None
    try:
        pid = int(json.loads(Path(runtime_file).read_text(encoding="utf-8")).get("pid", 0)) or None
    except (OSError, ValueError, TypeError):
        pid = None
    if pid is not None and not pid_alive(pid):
        pid = None   # a stale file names a dead daemon, not the one holding the guard
    who = f" (pid {pid})" if pid else ""
    line = (f"[{datetime.now().strftime('%H:%M:%S')}] Tessa is already running{who}. "
            f"Use tcli daemon restart.")
    out = _stdout_path()
    try:
        if out is not None:
            sys.stdout.write(line + "\n")
            sys.stdout.flush()
        else:
            Path(log_dir).mkdir(parents=True, exist_ok=True)
            with open(daily_log(log_dir), "a", encoding="utf-8") as f:
                f.write(line + "\n")
            if sys.stdout is not None:
                sys.stdout.write(line + "\n")
                sys.stdout.flush()
        if out is not None and _k32 is not None and _k32.GetConsoleWindow():
            with open("CONOUT$", "w", encoding="utf-8") as con:
                con.write(line + "\n")
    except OSError:
        pass
    os._exit(EXIT_ALREADY_RUNNING)


# ── STOPPING: ONE DOOR, ANY THREAD ────────────────────────────────────────────


class Stopper:
    """
    Console handler, end-session window, stop request and SIGINT all come here.
    The first reason wins. Thread-safe: the asyncio Event is only ever set on
    the loop's own thread, via call_soon_threadsafe. A request that arrives
    before main() binds the loop is remembered and honoured at bind time.
    """

    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self.event: asyncio.Event | None = None
        self.reason: str | None = None
        self.restarting = False
        self.requested_at: float | None = None
        self.done = threading.Event()
        self._lock = threading.Lock()

    def bind(self, loop: asyncio.AbstractEventLoop, event: asyncio.Event) -> None:
        with self._lock:
            self.loop, self.event = loop, event
            pending = self.reason is not None
        if pending:
            event.set()

    def request(self, reason: str, restarting: bool = False) -> bool:
        with self._lock:
            first = self.reason is None
            if first:
                self.reason, self.restarting = reason, bool(restarting)
                self.requested_at = time.monotonic()
            loop, event = self.loop, self.event
        if loop is not None and event is not None:
            try:
                loop.call_soon_threadsafe(event.set)
            except RuntimeError:
                pass   # loop already closed: shutdown is under way
        return first


STOPPER = Stopper()

# ── THE CONSOLE HANDLER ───────────────────────────────────────────────────────
#
# The callback object is held at module level for the life of the process: a
# garbage-collected ctypes callback crashes the process the moment Windows calls
# it. It hands the stop to the loop thread-safely and then BLOCKS until the
# clean shutdown has finished (or the limit nears) — returning early would let
# Windows end the process half way through it.

_CTRL_NAMES = {0: "CTRL_C", 1: "CTRL_BREAK", 2: "CTRL_CLOSE", 5: "CTRL_LOGOFF", 6: "CTRL_SHUTDOWN"}
_HANDLER_TYPE = ctypes.WINFUNCTYPE(wt.BOOL, wt.DWORD)
_HANDLER_REF: Any = None


def _on_console_ctrl(ctrl: int) -> bool:
    name = _CTRL_NAMES.get(ctrl, f"console event {ctrl}")
    STOPPER.request(f"console {name}")
    STOPPER.done.wait(SHUTDOWN_LIMIT_S)
    return True


def install_console_handler() -> bool:
    global _HANDLER_REF
    if _k32 is None or _HANDLER_REF is not None:
        return False
    _HANDLER_REF = _HANDLER_TYPE(_on_console_ctrl)
    # A process started with CREATE_NEW_PROCESS_GROUP begins with CTRL_C ignored;
    # undo that so CTRL_C reaches the handler like the other four.
    _k32.SetConsoleCtrlHandler(None, False)
    return bool(_k32.SetConsoleCtrlHandler(_HANDLER_REF, True))


# ── SIGN-OUT AND SHUTDOWN ─────────────────────────────────────────────────────
#
# A process that has loaded user32 (this one has, through ctypes) is treated by
# Windows as a GUI application: at sign-out and shutdown it is NOT sent
# CTRL_LOGOFF/CTRL_SHUTDOWN, it is sent WM_QUERYENDSESSION/WM_ENDSESSION — and
# only if it owns a top-level window. Without one it is simply terminated, with
# no daemon.stop row and runtime.json left behind. So the daemon owns one
# hidden, never-shown top-level window whose only job is to hear the session
# end. It ignores WM_CLOSE, so closing it does nothing.

_WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
_WNDPROC_REF: Any = None
END_SESSION_CLASS = "TessaCoreEndSession"
_ENDSESSION_LOGOFF = 0x80000000


class _WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wt.UINT), ("lpfnWndProc", _WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH), ("lpszMenuName", wt.LPCWSTR),
                ("lpszClassName", wt.LPCWSTR)]


def _end_session_thread(ready: threading.Event) -> None:
    global _WNDPROC_REF
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    u32.DefWindowProcW.restype = ctypes.c_ssize_t
    u32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    u32.CreateWindowExW.restype = wt.HWND
    u32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU,
                                    wt.HINSTANCE, ctypes.c_void_p]
    u32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]

    def wndproc(hwnd, msg, wparam, lparam):
        if msg == 0x0011:            # WM_QUERYENDSESSION: never block sign-out
            return 1
        if msg == 0x0016:            # WM_ENDSESSION
            if wparam:
                why = "sign-out" if (lparam & _ENDSESSION_LOGOFF) else "Windows shutdown"
                STOPPER.request(f"session end ({why})")
                STOPPER.done.wait(SHUTDOWN_LIMIT_S)
            return 0
        if msg == 0x0010:            # WM_CLOSE: nobody closes this window
            return 0
        return u32.DefWindowProcW(hwnd, msg, wparam, lparam)

    _WNDPROC_REF = _WNDPROC(wndproc)
    hinst = _k32.GetModuleHandleW(None)
    wc = _WNDCLASSW(lpfnWndProc=_WNDPROC_REF, hInstance=hinst, lpszClassName=END_SESSION_CLASS)
    u32.RegisterClassW(ctypes.byref(wc))
    hwnd = u32.CreateWindowExW(0, END_SESSION_CLASS, "Tessa Core (session end)", 0,
                               0, 0, 0, 0, None, None, hinst, None)
    ready.set()
    if not hwnd:
        return
    msg = wt.MSG()
    while u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        u32.TranslateMessage(ctypes.byref(msg))
        u32.DispatchMessageW(ctypes.byref(msg))


def start_end_session_watch() -> None:
    if _k32 is None:
        return
    ready = threading.Event()
    threading.Thread(target=_end_session_thread, args=(ready,), name="tessa-session-end",
                     daemon=True).start()
    ready.wait(2.0)


# ── CHILDREN WITHOUT A CONSOLE WINDOW ─────────────────────────────────────────
#
# Started by pythonw.exe the daemon has no console, so every console program it
# runs to READ its output (powershell, whoami, icacls, tasklist, netsh, sc,
# schtasks, wevtutil, cmd /c ...) would get a brand-new console window of its
# own — and with Windows Terminal as the default terminal, a terminal window.
# One rule, applied here rather than at twenty call sites: a child whose stdio
# is redirected, or a .cmd/.bat launcher, gets CREATE_NO_WINDOW. A child the
# owner asked to SEE (an app, "open command prompt") has no redirected stdio
# and is left exactly as it was. A daemon that has a console of its own does
# not need this — its children share that console — so it is only installed
# when there is no console window.

CREATE_NEW_CONSOLE = 0x00000010
DETACHED_PROCESS = 0x00000008
CREATE_NO_WINDOW = 0x08000000
_QUIET_INSTALLED = False


def _wants_no_window(args: Any, kw: dict) -> bool:
    if any(kw.get(k) is not None for k in ("stdin", "stdout", "stderr")):
        return True
    first = args[0] if isinstance(args, (list, tuple)) and args else args
    return str(first).lower().rstrip('"').endswith((".cmd", ".bat"))


def quiet_console_children() -> bool:
    global _QUIET_INSTALLED
    if _k32 is None or _QUIET_INSTALLED or _k32.GetConsoleWindow():
        return False
    original = subprocess.Popen.__init__

    def __init__(self, args, *a, **kw):  # noqa: N807
        if len(a) < 13:   # creationflags not passed positionally
            flags = kw.get("creationflags", 0) or 0
            if (not flags & (CREATE_NEW_CONSOLE | DETACHED_PROCESS | CREATE_NO_WINDOW)
                    and _wants_no_window(args, kw)):
                kw["creationflags"] = flags | CREATE_NO_WINDOW
        original(self, args, *a, **kw)

    subprocess.Popen.__init__ = __init__
    _QUIET_INSTALLED = True
    return True


# ── WHERE AM I: THE STARTUP LINE ──────────────────────────────────────────────


def _exe_of(pid: int) -> str:
    h = _k32.OpenProcess(0x1000, False, pid)
    if not h:
        return "?"
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = wt.DWORD(1024)
        return buf.value.rsplit("\\", 1)[-1] if _k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)) else "?"
    finally:
        _k32.CloseHandle(h)


def console_host() -> str:
    """none | conhost.exe (window visible/hidden/none) | OpenConsole.exe (Windows Terminal)."""
    if _k32 is None:
        return "n/a"
    pids = (wt.DWORD * 64)()
    if _k32.GetConsoleProcessList(pids, 64) == 0:
        return "none"
    hwnd = _k32.GetConsoleWindow()
    if not hwnd:
        return "windowless console (conhost.exe, no window)"
    cls = ctypes.create_unicode_buffer(128)
    _u32.GetClassNameW(hwnd, cls, 128)
    owner = wt.DWORD()
    _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
    vis = "visible" if _u32.IsWindowVisible(hwnd) else "hidden"
    if cls.value == "ConsoleWindowClass":
        return f"conhost.exe (classic window, {vis})"
    if cls.value == "PseudoConsoleWindow":
        return f"{_exe_of(owner.value)} pseudo-console (Windows Terminal / ConPTY, {vis})"
    return f"{cls.value} owned by {_exe_of(owner.value)} ({vis})"


def job_state() -> str:
    if _k32 is None:
        return "n/a"
    r = wt.BOOL()
    if not _k32.IsProcessInJob(_k32.GetCurrentProcess(), None, ctypes.byref(r)):
        return "unknown"
    if not r.value:
        return "not in a job"

    class _Basic(ctypes.Structure):
        _fields_ = [("a", ctypes.c_int64), ("b", ctypes.c_int64), ("LimitFlags", wt.DWORD),
                    ("c", ctypes.c_size_t), ("d", ctypes.c_size_t), ("e", wt.DWORD),
                    ("f", ctypes.c_size_t), ("g", wt.DWORD), ("h", wt.DWORD)]

    info = _Basic()
    if not _k32.QueryInformationJobObject(None, 2, ctypes.byref(info), ctypes.sizeof(info), None):
        return "in a job (limits unreadable)"
    f = info.LimitFlags
    names = [n for b, n in ((0x2000, "KILL_ON_JOB_CLOSE"), (0x800, "BREAKAWAY_OK"),
                            (0x1000, "SILENT_BREAKAWAY_OK")) if f & b]
    return f"IN A JOB flags=0x{f:04x} {names or ['no kill-on-close']}"


def startup_line(*, flags: list[str], data_dir: Path, runtime_file: Path,
                 for_disable_inherited: str | None, guard: Guard | None) -> str:
    if guard is None:
        g = "none"
    elif guard.abandoned:
        g = f"taken - ABANDONED ({', '.join(guard.abandoned)}): the previous daemon did not stop cleanly"
    else:
        g = "taken"
    return ("startup: " + " | ".join([
        f"pid {os.getpid()} (parent {os.getppid()})",
        f"exe {sys.executable}",
        f"prefix {sys.prefix}",
        f"cwd {os.getcwd()}",
        f"data {Path(data_dir).resolve()}",
        f"runtime {runtime_file}",
        f"flags {' '.join(flags) or '(none)'}",
        f"console {console_host()}",
        f"job {job_state()}",
        f"FOR_DISABLE_CONSOLE_CTRL_HANDLER=1 set in code (inherited: {for_disable_inherited or 'none'})",
        f"guard {g}",
    ]))


# ── data\logs: BOUNDED ────────────────────────────────────────────────────────


def _switch_stdio(path: Path) -> None:
    """Point fd 1/2 (Python AND native writers, e.g. a DLL's stderr) at `path`."""
    import msvcrt

    sys.stdout.flush()
    sys.stderr.flush()
    fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_BINARY)
    os.dup2(fd, 1)
    os.dup2(fd, 2)
    os.close(fd)
    _k32.SetStdHandle(wt.DWORD(-11 & 0xFFFFFFFF), msvcrt.get_osfhandle(1))   # STD_OUTPUT_HANDLE
    _k32.SetStdHandle(wt.DWORD(-12 & 0xFFFFFFFF), msvcrt.get_osfhandle(2))   # STD_ERROR_HANDLE


def manage_logs(log_dir: Path, *, file_cap: int = LOG_FILE_CAP_BYTES,
                dir_cap: int = LOG_DIR_CAP_BYTES, keep: int = LOG_KEEP_FILES) -> list[str]:
    """
    Roll this daemon's own log past `file_cap`, then prune the oldest daemon-*.log
    files until the folder fits `dir_cap` and `keep`. Only ever called once the
    guard is owned, so a refused launch can never rotate the running daemon's log.
    The file this process is writing is never deleted.
    """
    notes: list[str] = []
    log_dir = Path(log_dir)
    if _k32 is None or not log_dir.is_dir():
        return notes
    mine = _stdout_path()
    if mine is not None and _norm(mine.parent) == _norm(log_dir):
        try:
            size = mine.stat().st_size
        except OSError:
            size = 0
        if size > file_cap:
            nxt = log_dir / f"daemon-{datetime.now().strftime('%Y-%m-%d-%H%M%S')}.log"
            _switch_stdio(nxt)
            notes.append(f"log: {mine.name} reached {size} B (cap {file_cap}) - continuing in {nxt.name}")
            mine = nxt
    files = sorted(log_dir.glob("daemon-*.log"), key=lambda p: p.stat().st_mtime)
    total = sum(p.stat().st_size for p in files)
    for p in list(files):
        if total <= dir_cap and len(files) <= keep:
            break
        if mine is not None and _norm(p) == _norm(mine):
            continue
        try:
            sz = p.stat().st_size
            p.unlink()
            total -= sz
            files.remove(p)
            notes.append(f"log: pruned {p.name} ({sz} B)")
        except OSError:
            continue
    return notes


async def log_cap_task(log_dir: Path, interval_s: float = 60.0) -> None:
    while True:
        await asyncio.sleep(interval_s)
        try:
            for n in manage_logs(log_dir):
                log(n)
        except Exception as exc:  # noqa: BLE001 - housekeeping must never kill the daemon
            log(f"!! log housekeeping failed: {type(exc).__name__}: {exc}")


# ── THE STOP REQUEST (tcli daemon stop) ───────────────────────────────────────
#
# A local file beside runtime.json, so no network, no protocol change and no
# reach from a web page (a page cannot write into %LOCALAPPDATA%). It proves
# knowledge of THIS launch's token with an HMAC over this launch's pid; the
# token itself is never written. A request for another pid, or with a proof
# that does not match this token, is ignored. Leftovers are deleted at startup,
# so no stale request can stop her at every sign-in.


def stop_request_path(runtime_dir: Path) -> Path:
    return Path(runtime_dir) / STOP_REQUEST_NAME


def stop_proof(token: str, pid: int, nonce: str, restarting: bool) -> str:
    msg = f"{STOP_PROOF_VERSION}|{pid}|{nonce}|{1 if restarting else 0}".encode("utf-8")
    return hmac.new(token.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def judge_stop_request(raw: str, token: str, pid: int) -> tuple[bool, dict[str, Any]]:
    try:
        req = json.loads(raw)
        rpid = int(req.get("pid", 0))
        nonce = str(req.get("nonce", ""))
        restarting = bool(req.get("restarting", False))
        proof = str(req.get("proof", ""))
        by = str(req.get("by", "unknown"))[:40]
    except (ValueError, TypeError, AttributeError):
        return False, {"why": "unreadable request"}
    if req.get("v") != STOP_PROOF_VERSION:
        return False, {"why": f"unknown request version {req.get('v')!r}", "pid": rpid}
    if rpid != pid:
        return False, {"why": f"for another daemon (pid {rpid}, this is {pid})", "pid": rpid}
    if not 16 <= len(nonce) <= 128:
        return False, {"why": "malformed nonce", "pid": rpid}
    if not hmac.compare_digest(stop_proof(token, pid, nonce, restarting), proof):
        return False, {"why": "proof does not match this launch's token", "pid": rpid}
    return True, {"restarting": restarting, "by": by, "pid": rpid}


def clear_stale_stop_requests(runtime_dir: Path) -> str | None:
    p = stop_request_path(runtime_dir)
    if not p.exists():
        return None
    try:
        who = json.loads(p.read_text(encoding="utf-8")).get("pid")
    except (OSError, ValueError, AttributeError):
        who = "unreadable"
    try:
        p.unlink()
    except OSError as exc:
        return f"stop request: a leftover request could NOT be removed ({exc})"
    return f"stop request: removed a leftover request (for pid {who}) - it can never stop this daemon"


async def watch_stop_requests(runtime_dir: Path, token: str, audit: Any,
                              interval_s: float = 0.25) -> None:
    path = stop_request_path(runtime_dir)
    pid = os.getpid()
    while True:
        await asyncio.sleep(interval_s)
        if not path.exists():
            continue
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            continue   # being written; next tick
        ok, info = judge_stop_request(raw, token, pid)
        try:
            path.unlink()
        except OSError:
            pass
        if not ok:
            audit.append(actor="system", tool="daemon.stop.ignored", tier="none",
                         summary=f"Ignored a stop request: {info['why']}", detail=info)
            log(f"stop request IGNORED: {info['why']}")
            continue
        verb = "restart" if info["restarting"] else "stop"
        audit.append(actor="system", tool="daemon.stop.requested", tier="none",
                     summary=(f"{verb.capitalize()} requested by {info['by']} - proof of this "
                              f"launch's token verified"),
                     detail=info)
        log(f"stop request ACCEPTED from {info['by']} ({verb})")
        STOPPER.request(f"{info['by']} {verb}", restarting=info["restarting"])
        return


# ── THE MAIN PATH: AFTER THE GUARD, AND AT THE END ────────────────────────────


def after_guard(*, guard: Guard | None, data_dir: Path, runtime_dir: Path, runtime_file: Path,
                flags: list[str], for_disable_inherited: str | None) -> None:
    """Everything a daemon does once it owns the guard and before main() runs."""
    try:
        notes = manage_logs(Path(data_dir) / "logs")
    except Exception as exc:  # noqa: BLE001 - housekeeping must never cost a start
        notes = [f"!! log housekeeping failed: {type(exc).__name__}: {exc}"]
    quiet = quiet_console_children()
    stale = clear_stale_stop_requests(runtime_dir)
    install_console_handler()
    start_end_session_watch()
    log(startup_line(flags=flags, data_dir=data_dir, runtime_file=runtime_file,
                     for_disable_inherited=for_disable_inherited, guard=guard))
    for n in notes:
        log(n)
    if quiet:
        log("children: no console of my own - console helpers I run get CREATE_NO_WINDOW")
    if stale:
        log(stale)


def finish(guard: Guard | None, code: int, why: str) -> None:
    """Release the guard (owner thread), write the exit line, end the process now."""
    if guard is not None:
        guard.release()
    try:
        log(f"exit: {why} (code {code})")
        sys.stdout.flush()
        sys.stderr.flush()
    except (OSError, ValueError):
        pass
    STOPPER.done.set()
    # os._exit, not sys.exit: everything that matters is already on disk, and a
    # stray non-daemon thread (an audio stream, a browser driver) must not keep
    # a stopped daemon alive — tcli restart waits for this pid to be gone.
    os._exit(code)
