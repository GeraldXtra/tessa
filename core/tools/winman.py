"""
core/tools/winman.py — open windows: list, focus, minimise, maximise, close.

BY WINDOW HANDLE, ALWAYS. Every function here resolves a spoken name to exactly
one HWND and then operates on that handle. Nothing broadcasts, nothing iterates
an image name, nothing closes "all Chrome windows" because he said "chrome".
That is the same discipline `procs.py` applies to PIDs, and for the same
reason: this project has already destroyed the owner's work once by treating a
name as a target.

NO pywin32. `win32gui` is not installed and is not being installed — every call
below is `ctypes` into `user32`, which is already on the machine.

CLOSE IS `WM_CLOSE`, NEVER A KILL. WM_CLOSE is what clicking the X does: the
application gets to run its shutdown, flush its buffers, and put up its own
"save changes?" prompt. A window that refuses to close stays open and she says
so. Killing the process behind an unsaved document is not "closing a window",
and `procs.kill` is a separate, AMBER, PID-only tool for when he means it.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Any

from .base import ToolError

user32 = ctypes.windll.user32

SW_MAXIMIZE = 3
SW_MINIMIZE = 6
SW_RESTORE = 9
WM_CLOSE = 0x0010

_ENUM_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def _title(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _pid_of(hwnd: int) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def enumerate_windows() -> list[dict[str, Any]]:
    """
    Every VISIBLE, TITLED, top-level window.

    Both filters matter. Windows carries a long tail of invisible message-only
    and tool windows — reading him a list of forty entries, most of them blank,
    is not an answer to "what have I got open".
    """
    out: list[dict[str, Any]] = []

    def cb(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        t = _title(hwnd)
        if not t:
            return True
        out.append({"hwnd": int(hwnd), "title": t, "pid": _pid_of(hwnd)})
        return True

    user32.EnumWindows(_ENUM_PROC(cb), 0)
    return out


def _find_one(name: str) -> dict[str, Any]:
    """
    Resolve a spoken name to ONE window, or ask which.

    Substring, case-insensitive, then narrowed: an exact title match wins
    outright, because "chrome" matching six tabs and one settings window should
    not become a question when one of them is literally called Chrome.
    """
    needle = str(name or "").strip().lower()
    if not needle:
        raise ToolError("no window name came through", "Say part of the title.")
    wins = enumerate_windows()
    hits = [w for w in wins if needle in w["title"].lower()]
    if not hits:
        raise ToolError(f"nothing open matches {name!r}",
                        "Say part of the title as it appears in the task bar.")
    exact = [w for w in hits if w["title"].lower() == needle]
    if exact:
        return exact[0]
    if len(hits) > 1:
        # She ASKS rather than guessing (spec §Q). Picking the first of six is
        # how she minimises the wrong window and he loses what he was reading.
        listed = "; ".join(w["title"][:40] for w in hits[:3])
        raise ToolError(f"{len(hits)} windows match {name!r}",
                        f"Which one — {listed}?")
    return hits[0]


def list_windows() -> dict[str, Any]:
    wins = enumerate_windows()
    head = ", ".join(w["title"][:32] for w in wins[:4])
    return {"n": len(wins), "head": head or "nothing", "windows": wins}


def focus(name: str) -> dict[str, Any]:
    """
    Bring a window forward.

    THE FOREGROUND LOCK IS REAL AND IS HANDLED, NOT IGNORED. Windows refuses
    `SetForegroundWindow` from a process that does not already own the
    foreground — it flashes the task bar button instead and returns 0. The
    supported way through is to attach this thread's input queue to the thread
    that currently owns the foreground for the duration of the call, which is
    what Explorer and every launcher does. It is detached immediately after; a
    permanently attached input queue makes both threads share focus state and
    is a real way to hang a UI.

    If it still fails she says so rather than reporting success at a task bar
    that merely blinked.
    """
    w = _find_one(name)
    hwnd = w["hwnd"]
    user32.ShowWindow(hwnd, SW_RESTORE)

    fg = user32.GetForegroundWindow()
    this_thread = ctypes.windll.kernel32.GetCurrentThreadId()
    fg_thread = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    attached = False
    if fg_thread and fg_thread != this_thread:
        attached = bool(user32.AttachThreadInput(fg_thread, this_thread, True))
    try:
        ok = bool(user32.SetForegroundWindow(hwnd))
    finally:
        if attached:
            user32.AttachThreadInput(fg_thread, this_thread, False)

    if not ok and user32.GetForegroundWindow() != hwnd:
        raise ToolError(f"Windows would not bring {w['title'][:40]!r} forward",
                        "Its task bar button should be flashing. Click it and I will leave it alone.")
    return {"title": w["title"], "hwnd": hwnd, "pid": w["pid"]}


def minimise(name: str) -> dict[str, Any]:
    w = _find_one(name)
    user32.ShowWindow(w["hwnd"], SW_MINIMIZE)
    return {"title": w["title"], "hwnd": w["hwnd"]}


def maximise(name: str) -> dict[str, Any]:
    w = _find_one(name)
    user32.ShowWindow(w["hwnd"], SW_MAXIMIZE)
    return {"title": w["title"], "hwnd": w["hwnd"]}


def close(name: str) -> dict[str, Any]:
    """
    WM_CLOSE to ONE window, then verify it actually went.

    Posting the message and reporting success is what most automation does and
    it is a small lie: an application with unsaved work answers WM_CLOSE with a
    dialog and stays open. She waits briefly, checks, and tells him which
    happened.
    """
    import time

    w = _find_one(name)
    hwnd = w["hwnd"]
    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    for _ in range(12):                     # up to ~600 ms
        time.sleep(0.05)
        if not user32.IsWindow(hwnd):
            return {"title": w["title"], "closed": True, "verdict": "Closed, Emperor."}
    return {"title": w["title"], "closed": False,
            "verdict": "It did not close, Emperor. It is asking you something — "
                       "probably whether to save."}
