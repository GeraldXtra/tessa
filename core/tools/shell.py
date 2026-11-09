"""
core/tools/shell.py — RED. Run a command the OWNER dictated.

WHY THIS EXISTS AT ALL, GIVEN INVARIANT 4

CLAUDE.md #4 says the model never receives a raw command string to execute. It
does not say the OWNER may not run a command on his own machine — he does that
in a terminal all day. The invariant is about who authors the string, and this
file enforces that distinction as code rather than as a convention:

    execute(command, provenance="human")   -> allowed, after confirmation
    execute(command, provenance="model")   -> REFUSED, unconditionally
    execute(command, provenance="external")-> REFUSED, unconditionally

The refusal is not a tier and not a prompt. There is no argument, no approval
and no confirmation that reaches past it, because "the model asked me to run
this and I checked with you first" is exactly the shape a successful injection
takes: the model is persuaded by a page, it proposes a command, the owner is
half-listening, and he says yes.

FOUR MORE THINGS THIS DOES

  * It HOLDS and reads the command back verbatim before running anything. His
    own microphone has produced "Alicoy" and a sentence he never said from ordinary
    sentences; a mistranscribed command is the normal case, not the edge case.
  * It refuses while untrusted content is in context — the caller passes the
    `SessionContext` gate, same as every other red tool.
  * Output is `program` provenance. It comes back as DATA and is never fed to
    the model as instruction.
  * It is bounded: a timeout, and captured output truncated. An unbounded
    command holds the daemon that owns his microphone.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

from core.security import gate

from .base import ToolError, ToolHold

#: Long enough for `npm install`, short enough that a hung command does not own
#: the daemon for the rest of the evening.
DEFAULT_TIMEOUT_S = 120.0

#: What comes back is DATA. Truncated so a `dir /s` cannot blow out her context
#: or a WebSocket frame (CONTRACT §1 caps a frame at 1 MiB).
MAX_OUTPUT_CHARS = 8_000


MAX_NAMED = 40
PACKAGE_FILES = {
    "npm": ("package.json",), "npx": ("package.json",), "yarn": ("package.json",), "pnpm": ("package.json",),
    "pip": ("setup.py", "pyproject.toml", "setup.cfg"), "pip3": ("setup.py", "pyproject.toml", "setup.cfg"),
    "python": ("setup.py", "pyproject.toml", "setup.cfg"), "py": ("setup.py", "pyproject.toml", "setup.cfg"),
    "uv": ("pyproject.toml", "setup.py"), "poetry": ("pyproject.toml",), "make": ("Makefile", "makefile", "GNUmakefile"),
    "nmake": ("Makefile", "makefile"), "mingw32-make": ("Makefile", "makefile"), "cargo": ("Cargo.toml", "build.rs"),
    "gradle": ("build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"),
    "gradlew": ("gradlew.bat", "build.gradle", "build.gradle.kts"), "mvn": ("pom.xml",), "ant": ("build.xml",),
    "rake": ("Rakefile",), "bundle": ("Gemfile",), "composer": ("composer.json",), "dotnet": (),
}


def _tokens(cmd: str) -> list[str]:
    return [t.strip("\"'") for t in re.findall(r'"[^"]*"|\'[^\']*\'|[^\s&|<>()]+', cmd)]


def _stem(tok: str) -> str:
    stem = Path(tok).name.lower()
    return stem[:-4] if stem.endswith((".exe", ".cmd", ".bat")) else stem


def _repo_hooks(base: Path) -> tuple[list[Path], list[dict[str, Any]]]:
    for d in (base, *base.parents):
        dot = d / ".git"
        if dot.is_dir():
            try:
                hooks = sorted(p for p in (dot / "hooks").iterdir() if p.is_file() and p.suffix.lower() != ".sample")
            except OSError:
                hooks = []
            return hooks, [{"folder": "git's own settings", "why": "settings such as core.hooksPath, aliases and "
                            "filters can make git start a program, and I do not read them for your own commands",
                            "files": None}]
        if dot.exists():
            return [], [{"folder": str(dot), "why": "a git link to a repository kept elsewhere, which I did not follow",
                         "files": None}]
    return [], []


def command_files(cmd: str, cwd: str) -> tuple[list[Path], list[dict[str, Any]]]:
    base = Path(cwd)
    found = named_files(cmd, cwd)
    stems = {_stem(t) for t in _tokens(cmd) if t}
    roots = [base] if stems & set(PACKAGE_FILES) else []
    roots += [p.parent for p in found if p.suffix.lower() in gate.RUNNABLE_EXTS and not gate.in_admin_root(p)]
    unchecked: list[dict[str, Any]] = []
    walked: list[Path] = []
    for r in sorted(roots, key=lambda x: len(gate.norm(x))):
        if any(gate.under(r, w) for w in walked):
            continue
        walked.append(r)
        paths, unc = gate.walk_runnable(r)
        found += [p for p in paths if not any(gate.norm(p) == gate.norm(q) for q in found)]
        unchecked += [{**u, "folder": gate.rel_to(Path(r, u["folder"]), base)} for u in unc]
    if "git" in stems:
        hooks, notes = _repo_hooks(base)
        found += [p for p in hooks if not any(gate.norm(p) == gate.norm(q) for q in found)]
        unchecked += notes
    if len(found) > gate.MAX_FILES:
        raise ToolError(f"that command runs more than {gate.MAX_FILES} files, too many to show you",
                        "Run it from your own terminal.")
    return found, unchecked


def named_files(cmd: str, cwd: str) -> list[Path]:
    base = Path(cwd)
    exts = [e.lower() for e in os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";") if e]
    found: list[Path] = []

    def add(p: Path) -> None:
        if not any(gate.norm(p) == gate.norm(q) for q in found):
            found.append(p)

    for tok in _tokens(cmd):
        if not tok:
            continue
        for cand in {tok, tok.split("=", 1)[-1]}:
            try:
                p = Path(os.path.expandvars(cand)).expanduser()
                if not p.is_absolute():
                    p = base / p
                if p.is_file() and p.suffix.lower() in gate.RUNNABLE_EXTS:
                    add(p)
                elif not p.suffix and not cand.startswith(("-", "/")):
                    for ext in exts:
                        q = p.with_name(p.name + ext)
                        if q.is_file():
                            add(q)
            except (OSError, ValueError):
                continue
        for name in PACKAGE_FILES.get(_stem(tok), ()):
            f = base / name
            if f.is_file():
                add(f)
    if len(found) > MAX_NAMED:
        raise ToolError(f"that command names more than {MAX_NAMED} files that can run, too many to show you",
                        "Run it from your own terminal.")
    return found


def resolve_command(args: dict[str, Any]) -> dict[str, Any]:
    out = dict(args)
    cmd = str(out.get("command") or "").strip()
    cwd = str(out.get("cwd") or "") or os.getcwd()
    out["cwd"] = cwd
    paths, unchecked = command_files(cmd, cwd)
    pieces = gate.file_pieces(paths, Path(cwd))
    out["files"] = [p.card() for p in pieces]
    out["views"] = {p.rel: p.text for p in pieces if p.text is not None}
    out["unchecked"] = unchecked
    return gate.fit_card(out)


def execute(command: str, *, provenance: str = "human", confirmed: bool = False,
            cwd: str | None = None, timeout_s: float = DEFAULT_TIMEOUT_S, files: Any = (), views: Any = None,
            unchecked: Any = (), _request_id: str = "") -> dict[str, Any]:
    if provenance != "human":
        raise ToolError(
            f"that command came from {provenance}, not from you",
            "I only run commands you dictate yourself. Say it to me directly.")

    cmd = str(command or "").strip()
    if not cmd:
        raise ToolError("no command came through", "Say the command and I will read it back.")

    if not confirmed:
        # Read it back EXACTLY. Not a summary, not "a git command" — the
        # characters, so a mistranscription is audible before it is executed.
        raise ToolHold(f'I heard: "{cmd}". That runs as you, on this machine')

    # `cmd.exe /c` because he dictates shell syntax — pipes, redirects, chained
    # commands are the point of the tool. The string is HIS, verified out loud,
    # and it is the single place in this codebase where a string becomes a
    # process. Everything else takes NAME + ARGS.
    where = str(cwd or "") or os.getcwd()
    rows = [dict(r) for r in (files or [])]
    now = {gate.norm(p) for p in command_files(cmd, where)[0]}
    if now != {gate.norm(r["path"]) for r in rows}:
        raise ToolError("the files this command runs are not the ones on the card any more, so nothing ran",
                        "Ask me again and read the new card.")
    with gate.verified(rows, entry=cmd, root=where, kind="shell", approved_by=_request_id or "card", walk=False):
        proc = gate.start(gate.Start(kind=gate.CODE, why="shell.execute", mode="run",
                                     argv=[gate.system_program("cmd.exe"), "/c", cmd],
                                     options=dict(capture_output=True, text=True, timeout=timeout_s, cwd=cwd,
                                                  shell=False)))
    out = (proc.stdout or "") + (proc.stderr or "")
    truncated = len(out) > MAX_OUTPUT_CHARS
    return {
        "command": cmd,
        "code": proc.returncode,
        "ok": proc.returncode == 0,
        "lines": out.count("\n"),
        "truncated": truncated,
        # Named for its trust level, like clip.read's — this is `program`
        # provenance and must go through the fence before any model sees it.
        "external_text": out[:MAX_OUTPUT_CHARS],
    }
