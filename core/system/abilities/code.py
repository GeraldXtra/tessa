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
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from core.security import gate
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
    if not str(path or "").strip():
        return [launcher]
    target = Path(os.path.expandvars(str(path or "").strip().strip('"'))).expanduser()
    if not target.exists():
        raise ToolError(f"{target} is not there", "Give me a path that exists.")
    # `--` FIRST. A file called `-r` or `--wait` would otherwise be read by VS
    # Code as a switch rather than as something to open.
    return [launcher, "--", str(target.resolve())]


def checked_argv(path: str) -> list[str]:
    from core.tools.base import ToolError

    argv = argv_for(path)
    if len(argv) > 1:
        target = Path(argv[-1])
        if Path(argv[0]).suffix.lower() in (".cmd", ".bat") and re.search(r'[%^&|<>!"]', str(target)):
            raise ToolError("that path has characters the VS Code launcher would read as commands",
                            "Rename the folder, or open it from VS Code yourself.")
        tasks = gate.vscode_tasks_on_open(target)
        if tasks:
            raise ToolError(f"{tasks} sets a task to run as soon as the folder opens",
                            "Open that folder in VS Code yourself, after reading the task.")
    return argv


def open_in_code(path: str) -> dict[str, Any]:
    from core.tools.base import ToolError, ToolRedirect

    argv = checked_argv(path)
    target = Path(argv[-1]) if len(argv) > 1 else Path(argv[0])
    entry = SimpleNamespace(kind="exe", name="Visual Studio Code", launch=argv[0])
    if gate.app_needs_card(entry) is not None:
        raise ToolRedirect("app.launch_checked", {"app": "Visual Studio Code", "launch": argv[0],
                                                  "open": argv[-1] if len(argv) > 1 else ""},
                           "VS Code is not in the program folders, so it opens only from the card")
    try:
        gate.start(gate.Start(kind=gate.APP, why="open in VS Code", entry=entry, argv=argv))
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
