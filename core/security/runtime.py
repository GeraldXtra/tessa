"""
core/security/runtime.py — the per-launch auth token and its discovery file.

CONTRACT §1 and §2.1.

The whole security model of the local WebSocket rests on one assumption: that
`%LOCALAPPDATA%\\Tessa\\runtime.json` is readable ONLY by the owner. If that ACL
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

RUNTIME_DIRNAME = "Tessa"
#: What the directory was called before the rename. Used ONLY by `migrate_local_appdata`.
LEGACY_DIRNAME = "Zoey"
RUNTIME_FILENAME = "runtime.json"
PROTOCOL_VERSION = 1
TOKEN_BYTES = 32  # -> 64 hex chars, per CONTRACT §2.1


def local_appdata_root() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        # Non-Windows dev fallback; the daemon targets Windows.
        base = str(Path.home() / ".local" / "share")
    return Path(base) / RUNTIME_DIRNAME


def runtime_path() -> Path:
    return local_appdata_root() / RUNTIME_FILENAME


def migrate_local_appdata() -> str | None:
    """
    Move `%LOCALAPPDATA%\\Zoey` to `%LOCALAPPDATA%\\Tessa`, once.

    Returns a one-line description if anything moved, else None.

    WHY THIS EXISTS AND WHY IT MOVES RATHER THAN RECREATES.

    The repo's own `data/` is referenced by RELATIVE paths, so it travels with
    the folder when the repo is renamed and needs nothing. This directory does
    not: it sits under the user profile and no rename of the repo touches it.

    It holds `browser-profiles/`, and THAT is his logged-in X session. Pointing
    the code at a new empty directory would not lose the folder — it would
    simply stop using it, and he would find himself logged out of X with his
    profile still sitting in a directory nothing reads any more. `os.replace` on
    the DIRECTORY preserves every byte inside it, cookies and all; creating a
    fresh profile would not.

    BOTH PRESENT — AND "PRESENT" IS NOT THE SAME AS "IN USE".

    My first version refused whenever the new directory existed, reasoning that
    it meant a newer daemon had already run. That reasoning is wrong and I
    caught it by looking: `%LOCALAPPDATA%\\Tessa` already existed on this machine
    and was COMPLETELY EMPTY. Refusing there would have stranded a 38 MB browser
    profile — his logged-in X session — in the old directory while the daemon
    read an empty new one. He would have been logged out of X with his profile
    still on disk in a place nothing reads.

    So the test is CONTENT, not existence:

      new absent            -> rename the whole directory (atomic, cheapest)
      new present but EMPTY -> move each entry across, then drop the empty shell
      new present with data -> a real daemon has run; prefer it, leave the old
                               alone and name it in the log

    The last case still refuses to MERGE, and that part of the original
    reasoning stands: interleaving two Chrome profile directories risks a
    half-merged profile, which is a corrupted login rather than a missing one —
    worse than either input on its own.
    """
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        return None
    old = Path(base) / LEGACY_DIRNAME
    new = Path(base) / RUNTIME_DIRNAME
    if not old.is_dir():
        return None

    if not new.exists():
        try:
            os.replace(old, new)
        except OSError as exc:
            return f"could not move {old} to {new}: {exc}"
        return (f"moved {old} -> {new} "
                f"(browser profiles, window and theme state intact)")

    # ── PER ENTRY, NOT ALL-OR-NOTHING ───────────────────────────────────────
    #
    # The check used to be "does the new directory have ANY contents", and that
    # was too coarse — it cost him something real on this very machine. Another
    # process created `Tessa\\browser-profiles` (a fresh, empty-of-login Chrome
    # profile) minutes before this ran. The whole migration then refused, so his
    # THEME and WINDOW STATE — for which the new directory held nothing at all
    # and there was no conflict of any kind — stayed stranded in the old one
    # alongside the 38 MB profile that actually has his X session in it.
    #
    # So each entry is judged on its own: move what has no counterpart, never
    # overwrite what does. That still refuses to merge a Chrome profile — the
    # original reasoning stands, an interleaved profile is a corrupted login
    # rather than a missing one — while rescuing everything that was never in
    # conflict.
    moved, kept = [], []
    for entry in list(old.iterdir()):
        target = new / entry.name
        if target.exists():
            kept.append(entry.name)
            continue
        try:
            os.replace(entry, target)
            moved.append(entry.name)
        except OSError as exc:
            return (f"partially migrated: moved {moved}, then failed on "
                    f"{entry.name}: {exc}")
    try:
        old.rmdir()
    except OSError:
        pass          # leftovers are expected when something was kept back

    if moved and kept:
        return (f"moved {', '.join(moved)} into {new}; LEFT BEHIND in {old} "
                f"because {new.name} already has its own: {', '.join(kept)} "
                f"- compare them yourself and delete the one you do not want")
    if moved:
        return f"moved {len(moved)} entr(ies) into {new}: {', '.join(moved)}"
    return (f"nothing to move - {new} already has its own copy of: "
            f"{', '.join(kept)}; {old} is untouched")


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
