"""
core/system/winapi/inputs.py — synthetic keyboard and mouse, over stdlib ctypes.

NO `pyautogui`, AND THAT IS A DECISION RATHER THAN AN OMISSION

pyautogui is the obvious library and it is NOT INSTALLED here. Installing it
pulls pyscreeze, pytweening, pymsgbox, mouseinfo and Pillow on a METERED
connection, to wrap the two Win32 calls this file makes directly in ~150 lines:
`SendInput` and `SetCursorPos`. The same reasoning `gemini.py` gives for using
httpx over the Google SDK, and `local.py` for CTranslate2 over llama-cpp.

`SendInput`, NOT `keybd_event`/`mouse_event`. The latter pair are documented as
superseded, they cannot be delivered atomically as a block, and a hotkey sent as
separate calls can interleave with real input from his hands — which for a
combination like Ctrl+A means the Ctrl can be seen as released before the A
lands. SendInput submits the whole sequence in one serialised call.

TYPING IS UNICODE, NOT VIRTUAL KEYS. `KEYEVENTF_UNICODE` sends the CHARACTER,
so it does not depend on the active keyboard layout and cannot be turned into a
different character by a layout switch mid-sentence. It also means typing "£"
or an emoji works without a mapping table that would be wrong on his machine.

────────────────────────────────────────────────────────────────────────────────
⚠⚠ THE GUARDRAILS LIVE HERE, AT THE MECHANISM, NOT ONLY AT THE CAPABILITY

`shell.execute` is deliberately NOT in this batch: running an arbitrary command
is the master key and gets its own round. A keyboard is a way around that, and
the way around is short — type `powershell -c ...` into an open terminal, or
press Win+R and type it into the run box. Either would be the isolated
capability, reached through a different door.

So the refusals are enforced in `send_keys`/`send_text` themselves, where every
caller must pass through them, rather than in one capability's handler where a
second capability added later would simply miss them.
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from typing import Any

user32 = ctypes.windll.user32
user32.SetProcessDPIAware()

# ── SendInput plumbing ───────────────────────────────────────────────────────

ULONG_PTR = ctypes.c_uint64 if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x0002, 0x0004

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x0008, 0x0010
MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP = 0x0020, 0x0040
MOUSEEVENTF_WHEEL, MOUSEEVENTF_HWHEEL = 0x0800, 0x1000
MOUSEEVENTF_ABSOLUTE = 0x8000
WHEEL_DELTA = 120


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class _INPUTunion(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTunion)]


# ARGTYPES DECLARED, ALWAYS. `core/tools/clip.py` shipped for weeks with a
# HANDLE-returning call left undeclared, so ctypes truncated a 64-bit handle to
# 32 bits and clipboard read AND write had never once worked on this machine.
# The cost of declaring these is three lines; the cost of not is silent nonsense.
user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
user32.SetCursorPos.argtypes = (ctypes.c_int, ctypes.c_int)
user32.GetSystemMetrics.argtypes = (ctypes.c_int,)


class InputRefused(RuntimeError):
    """A guardrail said no. Carries the reason she reads out."""


def _send(*inputs: INPUT) -> int:
    n = len(inputs)
    arr = (INPUT * n)(*inputs)
    sent = user32.SendInput(n, arr, ctypes.sizeof(INPUT))
    if sent != n:
        raise InputRefused(
            f"Windows accepted {sent} of {n} keystrokes "
            f"(error {ctypes.get_last_error() if hasattr(ctypes, 'get_last_error') else '?'})")
    return sent


# ── who has the keyboard right now ───────────────────────────────────────────

def foreground() -> dict[str, Any]:
    """(hwnd, title, class) of the window that will receive the keystrokes."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return {"hwnd": 0, "title": "", "class": ""}
    cls = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, cls, 256)
    n = user32.GetWindowTextLengthW(hwnd)
    title = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, title, n + 1)
    return {"hwnd": int(hwnd), "title": title.value, "class": cls.value}


#: ⚠⚠ WINDOW CLASSES THAT ARE A COMMAND LINE.
#:
#: Typing into one of these IS `shell.execute` — the capability this batch
#: deliberately does not build — reached through the keyboard instead of through
#: the tool registry, with none of that round's scrutiny and no red card. So the
#: keyboard refuses them by window CLASS, which the window cannot lie about the
#: way it can lie about its title.
#:
#: Measured on this machine: cmd.exe and PowerShell 5.1 are `ConsoleWindowClass`;
#: Windows Terminal is `CASCADIA_HOSTING_WINDOW_CLASS`; VS Code's integrated
#: terminal is inside `Chrome_WidgetWin_1`, which is NOT listed — see below.
TERMINAL_CLASSES = {
    "consolewindowclass",              # cmd.exe, powershell.exe, conhost
    "cascadia_hosting_window_class",   # Windows Terminal
    "mintty",                          # Git Bash
    "putty",
    "vncviewer",
}

#: Credential surfaces. Never typed into, for the same reason the Chrome-profile
#: round refuses to type a password: the owner types his own credentials.
CREDENTIAL_CLASSES = {
    "credential dialog xaml host",
    "#32770" ,                         # the classic Win32 dialog, incl. UAC consent
}

#: ⚠ THE LIMIT OF CLASS-BASED REFUSAL, STATED RATHER THAN GLOSSED.
#:
#: An Electron app draws its own text, so VS Code's integrated terminal is a
#: `Chrome_WidgetWin_1` and is INDISTINGUISHABLE, from outside, from VS Code's
#: editor pane. Listing that class would refuse all typing into VS Code, which
#: is one of the two places he actually wants dictation. So this refusal stops
#: the real terminals and does NOT stop an embedded one. It is a bar across the
#: obvious door, not a proof of impossibility — the report says so plainly.
_SUSPICIOUS_TITLE_WORDS = ("powershell", "command prompt", "cmd.exe", "terminal",
                           "bash", "wsl", "administrator:")


def keyboard_target() -> dict[str, Any]:
    """
    Where a keystroke would land, and whether that is allowed.

    Returns the foreground window plus `refuse` (a reason, or empty).
    """
    fg = foreground()
    cls = (fg.get("class") or "").strip().lower()
    title = (fg.get("title") or "").strip().lower()
    if cls in TERMINAL_CLASSES:
        fg["refuse"] = (f"{fg['title'] or 'that window'} is a terminal, and typing into a "
                        f"terminal is running a command")
        return fg
    if cls in CREDENTIAL_CLASSES and any(w in title for w in ("credential", "sign in", "password")):
        fg["refuse"] = "that looks like a credential prompt, and I never type those"
        return fg
    if any(w in title for w in _SUSPICIOUS_TITLE_WORDS):
        fg["refuse"] = (f"{fg['title']} looks like a command line, and typing into one "
                        f"is running a command")
        return fg
    fg["refuse"] = ""
    return fg


# ── keys ─────────────────────────────────────────────────────────────────────

#: The keys she can name. A CLOSED SET, deliberately: an open mapping from
#: spoken words to virtual-key codes is a way to reach keys nobody reviewed.
VK = {
    "enter": 0x0D, "return": 0x0D, "tab": 0x09, "escape": 0x1B, "esc": 0x1B,
    "space": 0x20, "backspace": 0x08, "delete": 0x2E, "insert": 0x2D,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "ctrl": 0x11, "control": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B,
    "capslock": 0x14,
    **{str(i): 0x30 + i for i in range(10)},
    **{c: 0x41 + i for i, c in enumerate("abcdefghijklmnopqrstuvwxyz")},
    **{f"f{i}": 0x6F + i for i in range(1, 13)},
}

MODIFIERS = {"ctrl", "control", "alt", "shift", "win"}

#: ⚠⚠ COMBINATIONS THAT ARE REFUSED OUTRIGHT, AND WHY EACH ONE.
#:
#: These are not "risky" — each has a specific consequence this batch has
#: deliberately not built, or one that destroys work with no undo. A hold would
#: not make them safe: he cannot see, from "shall I press Win+R", what would be
#: typed into the box a moment later.
FORBIDDEN_COMBOS: dict[frozenset[str], str] = {
    frozenset({"win", "r"}): "Win+R opens the run box, and that is running a command",
    frozenset({"win", "x"}): "Win+X opens the admin menu, which reaches a terminal",
    frozenset({"ctrl", "shift", "escape"}): "that opens Task Manager, which can kill anything",
    frozenset({"ctrl", "alt", "delete"}): "Windows reserves that one; I cannot send it anyway",
    frozenset({"alt", "f4"}): "Alt+F4 closes a window without asking about unsaved work",
    frozenset({"win", "l"}): "that locks the screen — say lock my screen and I will do it properly",
    frozenset({"win", "d"}): "",          # allowed: show desktop is harmless and reversible
}
#: Entries whose reason is empty are ALLOWED; they are listed so the table
#: reads as a considered set rather than an arbitrary blacklist.
FORBIDDEN_COMBOS = {k: v for k, v in FORBIDDEN_COMBOS.items() if v}

#: Single keys that destroy work when the focus is not what she thinks it is.
#: Refused only as a bare press; harmless inside a combination she allows.
CAUTION_KEYS = {"delete", "backspace"}


def _key_event(vk: int, up: bool) -> INPUT:
    return INPUT(type=INPUT_KEYBOARD,
                 u=_INPUTunion(ki=KEYBDINPUT(wVk=vk, wScan=0,
                                             dwFlags=KEYEVENTF_KEYUP if up else 0,
                                             time=0, dwExtraInfo=0)))


def _char_event(ch: str, up: bool) -> INPUT:
    return INPUT(type=INPUT_KEYBOARD,
                 u=_INPUTunion(ki=KEYBDINPUT(
                     wVk=0, wScan=ord(ch),
                     dwFlags=KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0),
                     time=0, dwExtraInfo=0)))


def check_combo(keys: list[str]) -> str:
    """The refusal reason for this combination, or "" if it may be sent."""
    named = [k.strip().lower() for k in keys if k.strip()]
    unknown = [k for k in named if k not in VK]
    if unknown:
        return f"I do not know the key {unknown[0]!r}"
    reason = FORBIDDEN_COMBOS.get(frozenset(named))
    if reason:
        return reason
    # Win + anything unlisted is refused rather than allowed: the Windows key is
    # a launcher, and enumerating every shell shortcut it owns is not something
    # to get right by guessing.
    if "win" in named and frozenset(named) != frozenset({"win", "d"}) and len(named) > 1:
        return "I only use the Windows key for show desktop"
    if len(named) == 1 and named[0] in CAUTION_KEYS:
        return f"a bare {named[0]} deletes whatever is selected, and I cannot see what that is"
    return ""


def send_keys(keys: list[str]) -> dict[str, Any]:
    """
    Press a key or a combination, modifiers held then released in reverse.

    ⚠ Both guardrails run HERE so no caller can skip them.
    """
    reason = check_combo(keys)
    if reason:
        raise InputRefused(reason)
    target = keyboard_target()
    if target["refuse"]:
        raise InputRefused(target["refuse"])
    named = [k.strip().lower() for k in keys if k.strip()]
    mods = [k for k in named if k in MODIFIERS]
    rest = [k for k in named if k not in MODIFIERS]
    seq = [_key_event(VK[k], False) for k in mods]
    seq += [_key_event(VK[k], False) for k in rest]
    seq += [_key_event(VK[k], True) for k in reversed(rest)]
    seq += [_key_event(VK[k], True) for k in reversed(mods)]
    _send(*seq)
    return {"keys": "+".join(named), "window": target["title"], "class": target["class"]}


#: Typing is chunked so a long passage cannot monopolise the input queue on a
#: two-core machine, and so his own keyboard stays responsive underneath it.
TYPE_CHUNK = 20
TYPE_PAUSE_S = 0.01
MAX_TEXT = 4000


def send_text(text: str) -> dict[str, Any]:
    """Type `text` into whatever has focus, as characters rather than keys."""
    body = str(text or "")
    if not body:
        raise InputRefused("there is nothing to type")
    if len(body) > MAX_TEXT:
        raise InputRefused(f"that is {len(body)} characters; I type at most {MAX_TEXT} at once")
    target = keyboard_target()
    if target["refuse"]:
        raise InputRefused(target["refuse"])
    sent = 0
    for i in range(0, len(body), TYPE_CHUNK):
        chunk = body[i:i + TYPE_CHUNK]
        seq: list[INPUT] = []
        for ch in chunk:
            if ch == "\n":
                seq += [_key_event(VK["enter"], False), _key_event(VK["enter"], True)]
            else:
                seq += [_char_event(ch, False), _char_event(ch, True)]
        _send(*seq)
        sent += len(chunk)
        time.sleep(TYPE_PAUSE_S)
    return {"chars": sent, "window": target["title"], "class": target["class"]}


# ── mouse ────────────────────────────────────────────────────────────────────

SM_CXSCREEN, SM_CYSCREEN = 0, 1


def screen_size() -> tuple[int, int]:
    return int(user32.GetSystemMetrics(SM_CXSCREEN)), int(user32.GetSystemMetrics(SM_CYSCREEN))


def cursor_pos() -> tuple[int, int]:
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return int(pt.x), int(pt.y)


def move_to(x: int, y: int) -> dict[str, Any]:
    w, h = screen_size()
    if not (0 <= x < w and 0 <= y < h):
        raise InputRefused(f"({x}, {y}) is off the screen; it is {w} by {h}")
    user32.SetCursorPos(int(x), int(y))
    return {"x": int(x), "y": int(y)}


_BUTTONS = {
    "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
}


def _mouse_event(flags: int, data: int = 0, dx: int = 0, dy: int = 0) -> INPUT:
    return INPUT(type=INPUT_MOUSE,
                 u=_INPUTunion(mi=MOUSEINPUT(dx=dx, dy=dy, mouseData=data,
                                             dwFlags=flags, time=0, dwExtraInfo=0)))


def click(button: str = "left", double: bool = False) -> dict[str, Any]:
    down, up = _BUTTONS[button]
    seq = [_mouse_event(down), _mouse_event(up)]
    if double:
        seq += [_mouse_event(down), _mouse_event(up)]
    _send(*seq)
    x, y = cursor_pos()
    return {"button": button, "double": double, "x": x, "y": y}


def drag(x1: int, y1: int, x2: int, y2: int, button: str = "left") -> dict[str, Any]:
    down, up = _BUTTONS[button]
    move_to(x1, y1)
    _send(_mouse_event(down))
    # A few interpolated moves: a single jump is not a drag to most applications,
    # which track motion between press and release rather than only the endpoints.
    for i in range(1, 9):
        move_to(int(x1 + (x2 - x1) * i / 8), int(y1 + (y2 - y1) * i / 8))
        time.sleep(0.01)
    _send(_mouse_event(up))
    return {"from": [x1, y1], "to": [x2, y2], "button": button}


def scroll(amount: int, horizontal: bool = False) -> dict[str, Any]:
    """`amount` in notches; positive is up (or right)."""
    flag = MOUSEEVENTF_HWHEEL if horizontal else MOUSEEVENTF_WHEEL
    _send(_mouse_event(flag, data=int(amount) * WHEEL_DELTA))
    return {"notches": int(amount), "axis": "horizontal" if horizontal else "vertical"}
