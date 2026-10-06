"""
core/system/abilities/code.py — open a folder, a file or a project in VS Code.

GREEN under the existing `fs.open` key ("hand a path to its registered
application"). No new permission line.

WHAT THIS MIGRATES. `app.open_vscode` has been one of the last hand-written
branches in `Executor._dispatch`: no registry entry, no `ToolSpec`, no audit
template, and a tier that came from the `_LEGACY_TIERS` map rather than from
the registry. This is that behaviour as a real capability — typed argument,
tier read from permissions.yaml through the guard, one audit line — using the
same `code` CLI the branch used.

THE LAUNCHER IS RESOLVED, NOT ASSUMED. Measured on this machine:

    shutil.which("code")     C:\\Users\\...\\Microsoft VS Code\\bin\\code.CMD
    Code.exe                 C:\\Users\\...\\Microsoft VS Code\\Code.exe

`code` is on PATH here, and the fallback is the installed `Code.exe`, which is
searched in the three standard locations. If neither is there she says VS Code
is not installed and offers Explorer instead, rather than failing silently or
guessing a path that does not exist.

⚠ NO COMMAND STRING, EVER. The launcher is a resolved path this module found,
the argument is a resolved `Path`, and they are passed as a fixed argv with
`shell=False`. `code` is invoked with `--` before the target so a filename
beginning with a dash cannot become a VS Code switch. CLAUDE.md invariant 4,
held at the last mile.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from core.system.capability import Capability, Param


def _launcher() -> tuple[str, str] | None:
    """(path to launcher, how it was found), or None when VS Code is absent."""
    for name in ("code", "code.cmd"):
        found = shutil.which(name)
        if found:
            return found, "PATH"
    for guess in (Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Microsoft VS Code/bin/code.cmd",
                  Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Microsoft VS Code/Code.exe",
                  Path(os.environ.get("ProgramFiles", "")) / "Microsoft VS Code/Code.exe",
                  Path(os.environ.get("ProgramFiles(x86)", "")) / "Microsoft VS Code/Code.exe"):
        try:
            if guess.is_file():
                return str(guess), "installed location"
        except OSError:
            continue
    return None


def argv_for(path: str) -> list[str]:
    """
    The exact argv that would be run. Separated from `open_in_code` so the
    proof can assert the command WITHOUT launching a window.
    """
    from core.tools.base import ToolError

    found = _launcher()
    if found is None:
        raise ToolError("VS Code is not installed on this machine, or not on PATH",
                        "I can open it in Explorer instead.")
    launcher, _how = found
    target = Path(os.path.expandvars(str(path or "").strip().strip('"'))).expanduser()
    if not str(target).strip():
        raise ToolError("no path came through", "Name the folder or file.")
    if not target.exists():
        raise ToolError(f"{target} is not there", "Give me a path that exists.")
    # `--` FIRST. A file called `-r` or `--wait` would otherwise be read by VS
    # Code as a switch rather than as something to open.
    return [launcher, "--", str(target.resolve())]


def open_in_code(path: str) -> dict[str, Any]:
    from core.tools.base import ToolError

    argv = argv_for(path)
    target = Path(argv[-1])
    try:
        subprocess.Popen(argv, shell=False, close_fds=True)
    except OSError as exc:
        raise ToolError(f"VS Code would not start ({exc.strerror or exc})",
                        "I can open it in Explorer instead.") from None
    return {"path": str(target), "name": target.name or str(target),
            "kind": "folder" if target.is_dir() else "file"}


CAPABILITIES = [
    Capability(
        name="system.code.open", capability="fs.open", tier="green",
        run=open_in_code,
        params=(Param("path", str, doc="The folder, file or project to open."),),
        phrasings=("open this in vs code", "open the tessa project in code",
                   "open plan.md in vscode"),
        success="Opening {name} in VS Code, Emperor.",
        audit="open {path} in vscode",
        note="Migrates the legacy app.open_vscode branch onto the registry: it had no "
             "ToolSpec, no audit template and took its tier from the _LEGACY_TIERS map. "
             "Launcher resolved via PATH then the installed location, never assumed. "
             "Fixed argv with `--`, shell=False.",
    ),
]
