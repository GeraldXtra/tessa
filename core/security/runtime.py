"""
core/security/runtime.py — the per-launch auth token and its discovery file.

CONTRACT §1 and §2.1.

The whole security model of the local WebSocket rests on one assumption: that
`%LOCALAPPDATA%\\Zoey\\runtime.json` is readable ONLY by the owner. If that ACL
is wrong, the token is public and the Origin check is the only thing left
standing. So this module does not "try" to set the ACL — it sets it, reads it
back, and refuses to start if it did not take.
"""

from __future__ import annotations

import getpass
import json
import os
import secrets
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RUNTIME_DIRNAME = "Zoey"
RUNTIME_FILENAME = "runtime.json"
PROTOCOL_VERSION = 1
TOKEN_BYTES = 32  # -> 64 hex chars, per CONTRACT §2.1


def runtime_path() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        # Non-Windows dev fallback; the daemon targets Windows.
        base = str(Path.home() / ".local" / "share")
    return Path(base) / RUNTIME_DIRNAME / RUNTIME_FILENAME


def new_token() -> str:
    """32 cryptographically random bytes, hex-encoded. Regenerated every launch."""
    return secrets.token_hex(TOKEN_BYTES)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _lock_down_acl(path: Path) -> None:
    """
    Restrict `path` to the current user and SYSTEM only.

    Uses icacls because it needs no third-party dependency (pywin32 is a large
    install on a metered connection):
      /inheritance:r   drop inherited ACEs — otherwise Users/Authenticated Users
                       may still be able to read it
      /grant:r         replace, not append, any existing grant for that principal
    """
    if sys.platform != "win32":
        path.chmod(0o600)
        return

    user = os.environ.get("USERNAME") or getpass.getuser()
    result = subprocess.run(
        ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F", "/grant:r", "SYSTEM:F"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to restrict ACL on {path}: {result.stderr.strip() or result.stdout.strip()}"
        )


def _acl_is_locked_down(path: Path) -> tuple[bool, str]:
    """
    Read the ACL back and confirm no broad principal can read the token.

    Verifying rather than assuming: an icacls exit code of 0 does not by itself
    prove the resulting ACL is what we wanted.
    """
    if sys.platform != "win32":
        return True, ""

    result = subprocess.run(
        ["icacls", str(path)], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        return False, f"could not read ACL: {result.stderr.strip()}"

    text = result.stdout
    forbidden = [
        "Everyone",
        "BUILTIN\\Users",
        "Authenticated Users",
        "AUTORITE NT\\Utilisateurs authentifi",  # localised variants
        "NT AUTHORITY\\Authenticated Users",
    ]
    for principal in forbidden:
        if principal in text:
            return False, f"{principal} still has access to {path.name}"
    return True, ""


def preflight_runtime_file() -> Path:
    """
    Create and lock down the file WITHOUT writing anything into it.

    Split out of `write_runtime_file` so the daemon can pay the ACL check early
    and the *advertisement* late.

    THE PROBLEM THIS SOLVES. `runtime.json` used to be written before the voice
    model loaded, and a `--voice` daemon spends 3-17 s in `WhisperModel(...)`
    before `serve()` binds a socket. For that whole window the file said "there
    is a daemon on this port, here is its token" and a surface that believed it
    got ECONNREFUSED — or, worse, sampled the process and recorded a 65 MB
    daemon that was about to become a 314 MB one. That is the 5x memMB
    discrepancy Session 2 reported, and it was a real ordering bug rather than a
    measurement artefact.

    THE ACL CHECK CANNOT MOVE WITH IT. If the token file is world-readable the
    daemon must refuse to start, and discovering that after a 17 s model load is
    17 s of wasted work on a 2-core machine. So the check stays at the front and
    only the secret moves to the back.

    `touch(exist_ok=True)` deliberately does not truncate. A previous daemon's
    entry stays intact and valid until this one is genuinely listening, instead
    of being clobbered 17 s early and leaving BOTH undiscoverable.
    """
    path = runtime_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    # CLEAR A DEAD DAEMON'S ENTRY, AND ONLY A DEAD ONE.
    #
    # Deferring the write has one cost: a stale file from a force-killed daemon
    # now survives ~20 s longer, because this daemon no longer overwrites it
    # early. `read_runtime_file` already refuses a dead pid per CONTRACT §1, so
    # a conforming surface is safe either way — but leaving a corpse readable
    # for the whole boot is worse than removing it, and this is the moment we
    # know it is a corpse.
    #
    # A LIVE pid is left strictly alone. Another daemon may legitimately own
    # this file (two are running on this machine right now), and clobbering a
    # working daemon's entry to announce one that is 20 s from ready would make
    # BOTH undiscoverable.
    try:
        if path.exists() and path.stat().st_size > 0:
            stale = json.loads(path.read_text(encoding="utf-8"))
            owner = int(stale.get("pid", 0))
            if owner and owner != os.getpid() and not pid_is_alive(owner):
                path.unlink(missing_ok=True)
    except (json.JSONDecodeError, OSError, ValueError, TypeError):
        # An unreadable file is not evidence of a live daemon; leave it to be
        # overwritten rather than guessing at its contents.
        pass

    # 1. create empty, 2. restrict, 3. verify — the secret comes later.
    path.touch(mode=0o600, exist_ok=True)
    _lock_down_acl(path)
    ok, why = _acl_is_locked_down(path)
    if not ok:
        raise RuntimeError(
            f"Refusing to start: {why}. The auth token would be readable by other users."
        )
    return path


def write_runtime_file(port: int, token: str, pid: int | None = None) -> Path:
    """
    Write runtime.json and lock it down. Raises if the ACL cannot be verified.

    Order matters: the file is created empty and locked down BEFORE the token is
    written into it, so the secret is never briefly present in a
    world-readable file.

    CALL THIS ONLY ONCE THE LISTENER IS BOUND. Writing it is the act of
    advertising, and a daemon must not advertise a port it is not yet serving.
    See `preflight_runtime_file`.
    """
    path = preflight_runtime_file()

    payload: dict[str, Any] = {
        "protocolVersion": PROTOCOL_VERSION,
        "port": port,
        "token": token,
        "pid": pid if pid is not None else os.getpid(),
        "startedAt": _now(),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # Writing can recreate the file on some filesystems; re-verify.
    ok, why = _acl_is_locked_down(path)
    if not ok:
        path.unlink(missing_ok=True)
        raise RuntimeError(f"Refusing to start: ACL not retained after write ({why}).")

    return path


def remove_runtime_file() -> None:
    """Delete on clean shutdown (CONTRACT §1)."""
    runtime_path().unlink(missing_ok=True)


def pid_is_alive(pid: int) -> bool:
    if sys.platform == "win32":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            check=False,
        )
        return str(pid) in result.stdout
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


def read_runtime_file() -> dict[str, Any] | None:
    """
    Read runtime.json, or None if absent/stale.

    CONTRACT §1: a stale file whose pid is dead MUST be ignored, not trusted —
    otherwise a surface would sit there retrying against a port that a
    completely unrelated process may now own.
    """
    path = runtime_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    pid = data.get("pid")
    if not isinstance(pid, int) or not pid_is_alive(pid):
        return None
    return data
