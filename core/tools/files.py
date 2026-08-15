"""
core/tools/files.py — the file hands.

THE TWO INVARIANTS THIS FILE EXISTS TO HOLD

  * CLAUDE.md #6 — DELETION IS RECYCLE BIN ONLY. Never `os.remove`, never
    `shutil.rmtree`, no flag, no tier, no approval. `delete()` below goes
    through the Windows shell's own `SHFileOperationW` with `FOF_ALLOWUNDO`,
    which is the same call Explorer makes when you press Delete. If that call
    fails, the file stays. There is no fallback path to a hard delete, because a
    fallback is how "Recycle Bin only" becomes "Recycle Bin usually".

  * CLAUDE.md #5 — INDEXING IS METADATA-ONLY, AND NEVER READ A REPARSE POINT.
    His OneDrive tree holds 17,340 dehydrated placeholders and opening one
    downloads it over a metered connection. `search()` reads names, sizes and
    attributes and never opens a file. `read_text()` refuses a reparse point
    outright rather than paying for it and telling him afterwards.

NO NEW DEPENDENCIES. `send2trash` is not installed and is not being installed —
the Recycle Bin is a `ctypes` call into `shell32`, about forty lines, on a
metered link where a wheel is a real cost.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
from ctypes import wintypes
from pathlib import Path
from typing import Any, Iterator

from .base import ToolError, ToolHold

# ── the Recycle Bin, via the shell's own file operation ──────────────────────

FO_DELETE = 0x0003
FOF_SILENT = 0x0004            # no progress dialog
FOF_NOCONFIRMATION = 0x0010    # she already confirmed with him, in her voice
FOF_ALLOWUNDO = 0x0040         # <- THE WHOLE POINT: this is what "recycle" is
FOF_NOERRORUI = 0x0400         # errors come back as a code, not a message box


class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", wintypes.UINT),
        ("pFrom", wintypes.LPCWSTR),
        ("pTo", wintypes.LPCWSTR),
        ("fFlags", ctypes.c_uint16),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", wintypes.LPCWSTR),
    ]


def _recycle(paths: list[Path]) -> None:
    """
    Send to the Recycle Bin. Raises rather than falling back.

    `pFrom` is a DOUBLE-NULL-TERMINATED list — one NUL between entries and one
    closing the list. Getting that wrong does not error, it silently truncates
    the list to the first entry, which on a multi-file delete would leave him
    believing more was recycled than actually was.
    """
    joined = "\0".join(str(p) for p in paths) + "\0\0"
    op = _SHFILEOPSTRUCTW(
        hwnd=None,
        wFunc=FO_DELETE,
        pFrom=joined,
        pTo=None,
        fFlags=FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI,
        fAnyOperationsAborted=False,
        hNameMappings=None,
        lpszProgressTitle=None,
    )
    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if rc != 0:
        raise ToolError(f"Windows refused the delete, code {rc}",
                        "The files are untouched. Check they are not open somewhere.")
    if op.fAnyOperationsAborted:
        raise ToolError("the delete was aborted part way",
                        "Some may have gone to the Recycle Bin. Have a look.")


# ── path helpers ─────────────────────────────────────────────────────────────

#: Attribute bit for a reparse point. Set on every OneDrive placeholder.
FILE_ATTRIBUTE_REPARSE_POINT = 0x400

#: read_text cap. Whisper-sized commands do not ask for a 40 MB log, and an
#: unbounded read on 8 GB of RAM is how the daemon dies holding his microphone.
MAX_READ_BYTES = 2_000_000


def _plural(n: int, noun: str) -> str:
    """
    "1 item", not "1 items".

    A small thing that matters more in speech than on screen: he HEARS "one
    items", and every one of those is a reminder that he is talking to a
    template. She is supposed to sound like a person.
    """
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _size_words(b: int) -> str:
    """
    Say the size the way a person would.

    1240 bytes reported as "1 kilobytes" is both ungrammatical and useless — it
    rounds a real file down to something that sounds empty, on a confirmation
    where the size is the whole point of asking.
    """
    if b < 1_000:
        return _plural(b, "byte")
    if b < 1_000_000:
        return f"{b / 1e3:.1f} kilobytes"
    if b < 1_000_000_000:
        return f"{b / 1e6:.1f} megabytes"
    return f"{b / 1e9:.2f} gigabytes"


def _resolve(raw: Any, *, must_exist: bool = True) -> Path:
    if raw is None or str(raw).strip() == "":
        raise ToolError("no path came through", "Name the folder and I will try again.")
    p = Path(os.path.expandvars(str(raw))).expanduser()
    if must_exist and not p.exists():
        raise ToolError(f"{p} is not there", "Give me another path and I will open it.")
    return p


def is_reparse_point(p: Path) -> bool:
    try:
        return bool(p.stat(follow_symlinks=False).st_file_attributes  # type: ignore[attr-defined]
                    & FILE_ATTRIBUTE_REPARSE_POINT)
    except (OSError, AttributeError):
        return False


def _walk(root: Path, *, max_entries: int) -> Iterator[Path]:
    """
    Metadata-only walk. Never opens a file, never follows into a reparse point.

    Not following reparse points is not tidiness — descending into a OneDrive
    junction and stat-ing everything under it is how a name search turns into a
    metered download somebody has to explain later.
    """
    seen = 0
    stack = [root]
    while stack and seen < max_entries:
        cur = stack.pop()
        try:
            entries = list(os.scandir(cur))
        except (PermissionError, OSError):
            continue
        for e in entries:
            if seen >= max_entries:
                break
            p = Path(e.path)
            yield p
            seen += 1
            try:
                if e.is_dir(follow_symlinks=False) and not (
                        e.stat(follow_symlinks=False).st_file_attributes
                        & FILE_ATTRIBUTE_REPARSE_POINT):
                    stack.append(p)
            except (OSError, AttributeError):
                continue


# ── GREEN ────────────────────────────────────────────────────────────────────

def list_dir(path: str, limit: int = 200) -> dict[str, Any]:
    p = _resolve(path)
    if not p.is_dir():
        raise ToolError(f"{p.name} is a file, not a folder", "Ask me to open it instead.")
    dirs, files = [], []
    for e in sorted(os.scandir(p), key=lambda x: x.name.lower()):
        (dirs if e.is_dir(follow_symlinks=False) else files).append(e.name)
    total = len(dirs) + len(files)
    head = ", ".join((dirs + files)[:5])
    return {"path": str(p), "name": p.name or str(p), "n": total,
            "things": _plural(total, "item"),
            "dirs": len(dirs), "files": len(files),
            "names": (dirs + files)[:limit], "head": head or "nothing"}


def search(name: str, root: str | None = None, limit: int = 40) -> dict[str, Any]:
    """
    Find by NAME. Substring, case-insensitive — he says "the invoice one", not a
    glob, and a matcher that needs `*invoice*` is a matcher he will stop using.
    """
    needle = str(name or "").strip().lower()
    if not needle:
        raise ToolError("no name came through", "Tell me part of the name.")
    base = _resolve(root) if root else Path.home()
    hits = [p for p in _walk(base, max_entries=60_000) if needle in p.name.lower()]
    hits = hits[:limit]
    return {"n": len(hits), "things": _plural(len(hits), "match"),
            "needle": name, "root": str(base),
            "paths": [str(h) for h in hits],
            "first": hits[0].name if hits else "",
            "where": str(hits[0].parent) if hits else ""}


def read_text(path: str, max_bytes: int = MAX_READ_BYTES) -> dict[str, Any]:
    p = _resolve(path)
    if is_reparse_point(p):
        # INVARIANT 5. This is the hydration firewall doing its job: refusing
        # BEFORE the read, not warning after it, because after it the metered
        # bytes are already spent.
        raise ToolError(f"{p.name} is a cloud placeholder, not a local file",
                        "Opening it would download it on your metered link. Say hydrate and I will ask first.")
    size = p.stat().st_size
    if size > max_bytes:
        raise ToolError(f"{p.name} is {size / 1e6:.1f} megabytes",
                        f"That is past my {max_bytes / 1e6:.0f} megabyte read limit. Open it instead?")
    text = p.read_text(encoding="utf-8", errors="replace")
    n_lines = text.count("\n") + 1
    return {"path": str(p), "name": p.name, "chars": len(text),
            "lines": n_lines, "things": _plural(n_lines, "line"),
            # UNTRUSTED, and named for it. A file on disk is not his speech — a
            # README, a downloaded PDF's text layer, a config someone sent him
            # can carry an injection exactly as a web page can. The key name is
            # the reminder, so a caller cannot pass this to a model by reflex.
            "external_text": text}


def open_path(path: str) -> dict[str, Any]:
    p = _resolve(path)
    os.startfile(str(p))  # noqa: S606 — a resolved Path, never a string from a model
    return {"path": str(p), "name": p.name}


def reveal(path: str) -> dict[str, Any]:
    """Explorer, with the item selected. `/select,` needs the comma and no space."""
    p = _resolve(path)
    subprocess.Popen(["explorer.exe", f"/select,{p}"], shell=False)
    return {"path": str(p), "name": p.name}


def disk_usage(path: str) -> dict[str, Any]:
    p = _resolve(path)
    total = 0
    n = 0
    for entry in _walk(p, max_entries=200_000):
        try:
            st = entry.stat(follow_symlinks=False)
            if not st.st_file_attributes & FILE_ATTRIBUTE_REPARSE_POINT:
                total += st.st_size
                n += 1
        except (OSError, AttributeError):
            continue
    return {"path": str(p), "name": p.name or str(p), "bytes": total, "n": n,
            "things": _plural(n, "file"), "size": _size_words(total),
            "mb": total / 1e6, "gb": total / 1e9}


# ── AMBER ────────────────────────────────────────────────────────────────────

def make_folder(path: str) -> dict[str, Any]:
    p = _resolve(path, must_exist=False)
    if p.exists():
        raise ToolError(f"{p.name} already exists", "Give it another name.")
    p.mkdir(parents=True)
    return {"path": str(p), "name": p.name}


def rename(path: str, to: str) -> dict[str, Any]:
    p = _resolve(path)
    new_name = str(to).strip()
    if not new_name or any(c in new_name for c in '\\/:*?"<>|'):
        raise ToolError(f"{new_name!r} is not a usable file name", "Try one without slashes or colons.")
    target = p.with_name(new_name)
    if target.exists():
        raise ToolError(f"{new_name} already exists there", "Pick another name.")
    p.rename(target)
    return {"path": str(target), "was": p.name, "name": new_name}


def move(path: str, to: str) -> dict[str, Any]:
    import shutil

    src = _resolve(path)
    dst = _resolve(to, must_exist=False)
    if dst.is_dir():
        dst = dst / src.name
    if dst.exists():
        raise ToolError(f"{dst.name} is already there", "Move it somewhere else or rename it first.")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return {"path": str(dst), "name": src.name, "to": str(dst.parent)}


def copy(path: str, to: str) -> dict[str, Any]:
    import shutil

    src = _resolve(path)
    dst = _resolve(to, must_exist=False)
    if dst.is_dir():
        dst = dst / src.name
    if dst.exists():
        raise ToolError(f"{dst.name} is already there", "Pick another destination.")
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(str(src), str(dst))
    else:
        shutil.copy2(str(src), str(dst))
    return {"path": str(dst), "name": src.name, "to": str(dst.parent)}


# ── RED ──────────────────────────────────────────────────────────────────────

def delete(path: str, confirmed: bool = False) -> dict[str, Any]:
    """
    Recycle Bin, and it HOLDS on the first ask.

    The hold counts what is about to go and says the number out loud. "Delete
    the folder" and "delete 4,102 files" are the same sentence to him and very
    different events, and the only moment that difference is cheap is before it
    happens.
    """
    p = _resolve(path)
    if p.is_dir():
        n = sum(1 for _ in _walk(p, max_entries=200_000))
        what = f"{p} holds {_plural(n, 'item')}"
    else:
        n = 1
        what = f"{p.name} is {_size_words(p.stat().st_size)}"

    if not confirmed:
        raise ToolHold(f"{what}. It goes to the Recycle Bin, not gone for good")

    _recycle([p])
    return {"path": str(p), "name": p.name, "n": n, "things": _plural(n, "item")}
