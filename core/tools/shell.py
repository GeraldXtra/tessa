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
    own microphone has produced "Alicoy" and "The game is over" from ordinary
    sentences; a mistranscribed command is the normal case, not the edge case.
  * It refuses while untrusted content is in context — the caller passes the
    `SessionContext` gate, same as every other red tool.
  * Output is `program` provenance. It comes back as DATA and is never fed to
    the model as instruction.
  * It is bounded: a timeout, and captured output truncated. An unbounded
    command holds the daemon that owns his microphone.
"""

from __future__ import annotations

import subprocess
from typing import Any

from .base import ToolError, ToolHold

#: Long enough for `npm install`, short enough that a hung command does not own
#: the daemon for the rest of the evening.
DEFAULT_TIMEOUT_S = 120.0

#: What comes back is DATA. Truncated so a `dir /s` cannot blow out her context
#: or a WebSocket frame (CONTRACT §1 caps a frame at 1 MiB).
MAX_OUTPUT_CHARS = 8_000


def execute(command: str, *, provenance: str = "human", confirmed: bool = False,
            cwd: str | None = None, timeout_s: float = DEFAULT_TIMEOUT_S) -> dict[str, Any]:
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
    proc = subprocess.run(
        ["cmd.exe", "/c", cmd],
        capture_output=True, text=True, timeout=timeout_s,
        cwd=cwd, shell=False,
    )
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
