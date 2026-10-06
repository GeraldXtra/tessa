"""
core/system/abilities/create.py — make a new folder, or a new file.

AMBER under the existing `fs.create` key: "Additive, but it writes to his
disk." No new permission line — that key already classifies making a thing,
and a file and a folder are the same act at the same tier.

⚠⚠ CREATE IS NOT A CLOBBER, AND THAT IS THE WHOLE REASON THIS IS SAFE AT AMBER

Both capabilities REFUSE if the target already exists. They can only ever add
something that was not there, which is why they can hold for a spoken "yes"
instead of needing the approval card. Overwriting an existing file destroys
its contents, is not reversible from the Recycle Bin, and is therefore a
different act for a different batch at a different tier. There is no `force`,
no `overwrite`, and no parameter that could become one.

`core.tools.files.make_folder` already refuses a clobber and this keeps that
behaviour exactly; what it adds is the file case, which did not exist
anywhere, plus typed parameters and the protected-path rule below.

⚠ PROTECTED PATHS. Both declare `mutating=True` and `target="path"`, so the
guard's protected-path rule sees the real destination: creating under
`C:\\dev\\tessa`, `C:\\Windows`, `Program Files` or his OneDrive confirms
REGARDLESS of tier or actor (permissions.yaml, and CONTRACT §6.4). They are
amber and hold anyway; the rule adds the reason he hears.

⚠ THE PATH IS UNTRUSTED INPUT. It arrives from a phrase or a model, so it is
expanded, resolved and checked here, and the parent must already exist — she
will not silently build four levels of directory he did not ask for.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from core.system.capability import Capability, Param

#: A file created with text may not be larger than this. A "create a file"
#: turn is a note or a stub, not a data dump, and an unbounded write from a
#: spoken argument is a way to fill a disk with 14.5 GB free.
MAX_TEXT_BYTES = 64 * 1024


def _target(raw: str) -> Path:
    """Expand, resolve, and refuse anything that is not a usable path."""
    from core.tools.base import ToolError

    text = str(raw or "").strip().strip('"').strip("'")
    if not text:
        raise ToolError("no path came through", "Tell me the name and where to put it.")
    try:
        path = Path(os.path.expandvars(text)).expanduser()
    except (OSError, ValueError):
        raise ToolError(f"{text!r} is not a usable path", "Try a simpler name.") from None
    if not path.is_absolute():
        path = Path.home() / "Documents" / path
    # `\\` at the front is a UNC share. Creating on somebody else's machine is
    # not what "make a folder" means, and it is not this batch's act.
    if str(path).startswith("\\\\"):
        raise ToolError("that is a network share", "I only make things on this machine.")
    return path


def _guard_parent(path: Path) -> None:
    from core.tools.base import ToolError

    parent = path.parent
    if not parent.exists():
        raise ToolError(f"{parent} does not exist",
                        "Make that folder first, or give me a path that is already there.")
    if not parent.is_dir():
        raise ToolError(f"{parent} is a file, not a folder", "Give me a folder to put it in.")


def create_folder(path: str) -> dict[str, Any]:
    from core.tools.base import ToolError

    target = _target(path)
    if target.exists():
        # THE NO-CLOBBER REFUSAL. Naming which kind of thing is already there
        # is the difference between a refusal he can act on and one he argues
        # with.
        what = "folder" if target.is_dir() else "file"
        raise ToolError(f"there is already a {what} called {target.name} in {target.parent}",
                        "Give it another name, or tell me to open the one that is there.")
    _guard_parent(target)
    try:
        target.mkdir()
    except OSError as exc:
        raise ToolError(f"Windows would not make it ({exc.strerror or exc})",
                        "Check the name has no illegal characters.") from None
    return {"path": str(target), "name": target.name, "where": str(target.parent)}


def create_file(path: str, text: str = "") -> dict[str, Any]:
    from core.tools.base import ToolError

    target = _target(path)
    if target.exists():
        what = "folder" if target.is_dir() else "file"
        raise ToolError(f"there is already a {what} called {target.name} in {target.parent}",
                        "Give it another name. I will not write over something that is there.")
    _guard_parent(target)
    body = str(text or "")
    if len(body.encode("utf-8")) > MAX_TEXT_BYTES:
        raise ToolError(f"that is more than {MAX_TEXT_BYTES // 1024} kilobytes of text",
                        "Give me something shorter, or make it empty and edit it.")
    try:
        # `x` — EXCLUSIVE CREATE. Not `w`. Between the exists() check above and
        # this line something could appear, and `w` would truncate it. `x`
        # makes the filesystem itself refuse, so the no-clobber promise does
        # not depend on winning a race.
        with target.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(body)
    except FileExistsError:
        raise ToolError(f"{target.name} appeared while I was writing it",
                        "Try again, or give it another name.") from None
    except OSError as exc:
        raise ToolError(f"Windows would not make it ({exc.strerror or exc})",
                        "Check the name has no illegal characters.") from None
    return {"path": str(target), "name": target.name, "where": str(target.parent),
            "chars": len(body), "empty": not body}


CAPABILITIES = [
    Capability(
        name="system.files.create_folder", capability="fs.create", tier="amber",
        run=create_folder,
        params=(Param("path", str, doc="Where to make it, name included."),),
        mutating=True, target="path",
        phrasings=("make a folder called drafts", "create a folder called notes"),
        success="Made it, Emperor. {name}, in {where}.",
        audit="mkdir {path}",
        hold="make the folder {path}",
        note="Refuses if anything of that name is already there — create is never a clobber. "
             "The parent must already exist. mutating+target, so the protected-path rule sees "
             "the real destination.",
    ),
    Capability(
        name="system.files.create_file", capability="fs.create", tier="amber",
        run=create_file,
        params=(Param("path", str, doc="Where to make it, name included."),
                Param("text", str, default="", doc="What to put in it, if anything.")),
        mutating=True, target="path",
        phrasings=("create a file called notes.txt", "make a new file called todo.md"),
        success="Made it, Emperor. {name}, in {where}.",
        audit="create file {path}",
        hold="create the file {path}",
        note="Opened with mode 'x' — EXCLUSIVE create, so the no-clobber promise is enforced "
             "by the filesystem and not by a check that could lose a race. There is no "
             "overwrite parameter and none may be added; overwriting is a different act.",
    ),
]
