"""
core/system/abilities/window.py — restore a window, and snap it to half the screen.

GREEN under the existing `window.control` key. No new permission line.

⚠ WHAT THIS DELIBERATELY DOES **NOT** DO, AND WHY

It does not re-implement focus, minimise, maximise, close or list. Those are
already REGISTRY tools — `win.focus`, `win.minimise`, `win.maximise`,
`win.close`, `win.list` — with tiers read from permissions.yaml, audit
templates, and handlers in `core/tools/winman.py`. They are NOT legacy
branches: `Executor._LEGACY_TIERS` contains no window entry, which was checked
before this file was written. Wrapping them here would create a second path to
each act, which is the exact thing every batch so far has been removing.

So this module adds only the two things that did not exist anywhere:

    system.window.restore   un-minimise a window WITHOUT stealing focus
    system.window.snap      put a window on the left or right half

`win.focus` already calls `SW_RESTORE` on the way to raising a window, which
is the right behaviour for "switch to Chrome" and the wrong one for "put that
back" — restoring something you want to keep an eye on should not yank the
foreground away from what you are typing into.

⚠ SNAP IS ARITHMETIC ON THE WORK AREA, NOT A KEYSTROKE. The obvious
implementation is to synthesise Win+Left, which requires the target window to
already be focused, steals the foreground, and on this machine opens the Snap
Assist chooser over the other half. `SetWindowPos` against the monitor's work
area is exact, needs no focus, disturbs nothing else, and respects the task
bar because the work area already excludes it.

⚠ NOTHING HERE CLOSES OR KILLS ANYTHING. There is no WM_CLOSE, no
TerminateProcess, no PID in this module. Closing a window is `win.close`
(green, WM_CLOSE, the application decides whether to prompt); killing a
process is `proc.kill` (amber, PID only) and is a different batch.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Any

from core.system.capability import Capability, Param

user32 = ctypes.windll.user32

SW_RESTORE = 9
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
MONITOR_DEFAULTTONEAREST = 0x0002


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", _RECT),
                ("rcWork", _RECT), ("dwFlags", wintypes.DWORD)]


def _work_area(hwnd: int) -> tuple[int, int, int, int]:
    """
    The usable rectangle of the monitor this window is on: the screen minus
    the task bar. Taken from the window's OWN monitor, so a snap on a second
    display does not fling it back to the primary one.
    """
    monitor = user32.MonitorFromWindow(wintypes.HWND(hwnd), MONITOR_DEFAULTTONEAREST)
    info = _MONITORINFO()
    info.cbSize = ctypes.sizeof(_MONITORINFO)
    if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        raise OSError("GetMonitorInfoW failed")
    r = info.rcWork
    return int(r.left), int(r.top), int(r.right - r.left), int(r.bottom - r.top)


def restore(name: str) -> dict[str, Any]:
    """Un-minimise, leaving the foreground where it is."""
    from core.tools import winman
    from core.tools.base import ToolError

    window = winman._find_one(name)          # reused, not reimplemented
    hwnd = window["hwnd"]
    if not user32.IsWindow(wintypes.HWND(hwnd)):
        raise ToolError(f"{window['title'][:40]!r} is not open any more",
                        "Say part of the title of something that is.")
    was_minimised = bool(user32.IsIconic(wintypes.HWND(hwnd)))
    user32.ShowWindow(wintypes.HWND(hwnd), SW_RESTORE)
    return {"title": window["title"], "hwnd": hwnd,
            "was_minimised": was_minimised,
            "state": "back up" if was_minimised else "already up"}


def snap(name: str, side: str) -> dict[str, Any]:
    """Put a window on the left or right half of its own monitor."""
    from core.tools import winman
    from core.tools.base import ToolError

    window = winman._find_one(name)
    hwnd = window["hwnd"]
    if not user32.IsWindow(wintypes.HWND(hwnd)):
        raise ToolError(f"{window['title'][:40]!r} is not open any more",
                        "Say part of the title of something that is.")
    try:
        left, top, width, height = _work_area(hwnd)
    except OSError:
        raise ToolError("Windows would not describe the screen",
                        "Try maximising it instead.") from None

    # A maximised or minimised window ignores SetWindowPos until it is
    # restored, so put it back to a normal state first.
    user32.ShowWindow(wintypes.HWND(hwnd), SW_RESTORE)
    half = width // 2
    x = left if side == "left" else left + half
    ok = user32.SetWindowPos(wintypes.HWND(hwnd), None, x, top, half, height,
                             SWP_NOZORDER | SWP_NOACTIVATE | SWP_SHOWWINDOW)
    if not ok:
        raise ToolError(f"Windows would not move {window['title'][:40]!r}",
                        "Some windows refuse to be resized.")
    return {"title": window["title"], "hwnd": hwnd, "side": side,
            "x": x, "y": top, "width": half, "height": height}


CAPABILITIES = [
    Capability(
        name="system.window.restore", capability="window.control", tier="green",
        run=restore,
        params=(Param("name", str, doc="Part of the title, or 'this window'."),),
        phrasings=("restore chrome", "put chrome back", "unminimise chrome"),
        success="{title} is {state}, Emperor.",
        audit="restore window {name}",
        note="SW_RESTORE only — deliberately does NOT steal the foreground, which is what "
             "separates it from win.focus. Resolves the target through core.tools.winman."
             "_find_one, so 'this window' means the one in front.",
    ),
    Capability(
        name="system.window.snap", capability="window.control", tier="green",
        run=snap,
        params=(Param("name", str, doc="Part of the title, or 'this window'."),
                Param("side", str, choices=("left", "right"), doc="Left or right half?")),
        phrasings=("snap chrome left", "snap this window right",
                   "put chrome on the left half"),
        success="{title} is on the {side}, Emperor.",
        audit="snap window {name} {side}",
        note="SetWindowPos against the monitor's WORK AREA, so the task bar is respected and "
             "a second display keeps its own window. Not a synthesised Win+Left, which needs "
             "focus and opens Snap Assist over the other half.",
    ),
]
