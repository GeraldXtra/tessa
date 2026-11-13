from __future__ import annotations

import ctypes
import os
import uuid
from ctypes import wintypes
from pathlib import Path
from typing import Any

from core.security import gate

KNOWN = {
    "Documents": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
    "Desktop": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641",
    "Downloads": "374DE290-123F-4565-9164-39C4925E467B",
    "Pictures": "33E28130-4E1E-4676-835A-98395C3BC3BB",
    "Videos": "18989B1D-99B5-455B-841C-AB7C74E4DDFC",
    "Music": "4BD8D571-6D19-48D3-BE97-422220080E43",
}
FREE, CARD, REFUSE = "free", "card", "refuse"
WRITERS = ("human", "agent")
CARD_NAMES = frozenset({"desktop.ini", "autorun.inf", "folder.htt"})
KF_FLAG_DONT_VERIFY = 0x00004000
NOTEPAD = {"process": "notepad.exe", "window": "notepad", "focus": ("edit", "richeditd2dpt"), "label": "Notepad's text area"}
TYPING = [NOTEPAD]
WRITE_CARD = "fs.write_checked"
TYPE_CARD = "input.type_checked"
_GUARD: dict[str, Any] = {}


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8)]


def _known_path(guid: str) -> Path | None:
    u = uuid.UUID(guid)
    g = _GUID(u.fields[0], u.fields[1], u.fields[2], (ctypes.c_ubyte * 8)(*u.bytes[8:]))
    ptr = ctypes.c_wchar_p()
    try:
        fn = ctypes.windll.shell32.SHGetKnownFolderPath
        fn.argtypes = [ctypes.POINTER(_GUID), wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(ctypes.c_wchar_p)]
        fn.restype = ctypes.c_long
        if fn(ctypes.byref(g), KF_FLAG_DONT_VERIFY, None, ctypes.byref(ptr)) == 0 and ptr.value:
            return Path(ptr.value)
    except Exception:
        return None
    finally:
        try:
            ctypes.windll.ole32.CoTaskMemFree(ptr)
        except Exception:
            pass
    return None


def _temp_path() -> Path | None:
    try:
        buf = ctypes.create_unicode_buffer(32768)
        n = ctypes.windll.kernel32.GetTempPathW(32768, buf)
        if n:
            return Path(buf.value)
    except Exception:
        pass
    t = os.environ.get("TEMP") or os.environ.get("TMP")
    return Path(t) if t else None


_known = _known_path
_temp = _temp_path


def _usable(root: Path) -> bool:
    try:
        if not root.is_absolute() or root.parent == root:
            return False
    except (OSError, ValueError):
        return False
    if gate.norm(root) == gate.norm(Path.home()) or gate.in_admin_root(root):
        return False
    return not gate.protected_reason(root)


def folders() -> dict[str, Path]:
    out: dict[str, Path] = {}
    for name, guid in KNOWN.items():
        p = _known(guid)
        if p is not None and _usable(p):
            out[name] = p
    t = _temp()
    if t is not None and _usable(t):
        out["temp"] = t
    return out


def known(name: str) -> Path:
    p = folders().get(name)
    return p if p is not None else Path.home() / name


def _guard() -> Any:
    g = _GUARD.get("g")
    if g is None:
        from core.security.guard import Guard

        g = Guard(Path(__file__).resolve().parents[1] / "config" / "permissions.yaml")
        _GUARD["g"] = g
    return g


def _full(p: Path) -> Path:
    try:
        return p.resolve()
    except (OSError, RuntimeError, ValueError):
        return Path(os.path.normpath(str(p)))


def place(path: Any, actor: str, *, folder: bool = False) -> tuple[str, str]:
    raw = str(path or "").strip()
    if not raw:
        return REFUSE, "no path came through"
    p = Path(os.path.expandvars(raw)).expanduser()
    if not p.is_absolute():
        return CARD, f"{p} is not a full path"
    why = gate.protected_reason(p)
    if why:
        return REFUSE, f"{p} is in {why}, and my file tools never write there"
    full = _full(p)
    runs = not folder and (p.suffix.lower() in gate.RUNNABLE_EXTS or p.name.lower() in CARD_NAMES)
    for name, root in folders().items():
        if gate.under(full, _full(root)):
            if runs:
                return CARD, f"{p.name} is a kind of file Windows runs or acts on when it shows the folder"
            return FREE, "the temp folder" if name == "temp" else f"your {name} folder"
    if actor == "human":
        if _guard().is_protected(str(full)):
            return CARD, f"{p.parent} is inside a protected folder"
        if runs:
            return CARD, f"{p.name} is a kind of file Windows runs or acts on when it shows the folder"
        return FREE, "a folder you named yourself"
    return CARD, f"{p.parent} is not one of the folders I write to without asking"


def worst(*verdicts: tuple[str, str]) -> tuple[str, str]:
    for want in (REFUSE, CARD):
        for v in verdicts:
            if v[0] == want:
                return v
    return verdicts[0] if verdicts else (FREE, "")


def check_write(op: str, dest: Any, actor: str, *, source: Any = None, text: str | None = None,
                folder: bool = False) -> str:
    from core.tools.base import ToolError, ToolRedirect

    verdicts = [place(dest, actor, folder=folder)]
    if source not in (None, "") and op != "copy":
        verdicts.append(place(source, actor, folder=True))
    if actor not in WRITERS:
        verdicts.append((CARD, f"a {actor} request never writes without your card"))
    v, why = worst(*verdicts)
    if v == REFUSE:
        raise ToolError(why, "Do that one yourself, or name another place.")
    if v == CARD:
        args: dict[str, Any] = {"op": op, "path": str(dest)}
        if source not in (None, ""):
            args["source"] = str(source)
        if text is not None:
            args["text"] = str(text)
        raise ToolRedirect(WRITE_CARD, args, why)
    return why


def typing_allowed(t: dict[str, Any]) -> str:
    proc = str(t.get("process") or "").lower()
    cls = str(t.get("class") or "").lower()
    focus = str(t.get("focus_class") or "").lower()
    path = str(t.get("process_path") or "")
    for a in TYPING:
        if proc == a["process"] and cls == a["window"] and focus in a["focus"] and path and gate.in_admin_root(path):
            return a["label"]
    return ""


def same_target(a: dict[str, Any], b: dict[str, Any]) -> bool:
    keys = ("hwnd", "pid", "class", "focus_hwnd", "focus_class")
    if any(str(a.get(k) or "") != str(b.get(k) or "") for k in keys):
        return False
    return gate.norm(a.get("process_path") or "") == gate.norm(b.get("process_path") or "")
