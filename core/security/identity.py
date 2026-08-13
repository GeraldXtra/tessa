"""
core/security/identity.py — spec §7.5: assert the daemon is NOT LocalSystem.

WHY THIS EXISTS

Running as LocalSystem is not merely "wrong user". Session 0 isolation silently
redirects the per-user environment: APPDATA becomes
`C:\\Windows\\System32\\config\\systemprofile\\AppData\\Roaming`, so `npm install -g`
lands somewhere the owner's PATH will never look, `pip install --user` writes to
a profile no human logs into, and `%LOCALAPPDATA%\\Zoey\\runtime.json` — the file
both surfaces discover the daemon through — is written to a different path
entirely. Every one of those failures presents as something else: "npm installed
but the command is not found", "the Console cannot see the daemon". The cause is
three layers away from the symptom.

It also breaks the security model rather than merely inconveniencing it.
CONTRACT §2.1 requires the token file to carry a user-only ACL. As LocalSystem
that ACL is granted to a service account, and "only the owner can read it" stops
being true in the sense that matters.

So this follows the same rule as `runtime.py`'s ACL readback: do not TRY and
hope, CHECK and refuse. A daemon that cannot prove who it is does not start.
"""

from __future__ import annotations

import getpass
import os
import subprocess
from pathlib import Path

#: SIDs that mean "not a human user". S-1-5-18 is LocalSystem; 19 and 20 are
#: LocalService and NetworkService, which have the same APPDATA redirection
#: problem and are equally wrong for this daemon.
SERVICE_SIDS = {
    "S-1-5-18": "LocalSystem",
    "S-1-5-19": "LocalService",
    "S-1-5-20": "NetworkService",
}


class IdentityError(RuntimeError):
    """The daemon is running as an identity it must never run as."""


def current_sid() -> str | None:
    """
    This process's user SID, via `whoami /user`.

    whoami is in-box on every supported Windows and needs no dependency —
    pywin32 would be a ~10 MB download on a metered connection to answer one
    question asked once per launch. Returns None if it cannot be determined,
    which callers treat as "cannot verify", not as "verified safe".
    """
    try:
        result = subprocess.run(
            ["whoami", "/user", "/fo", "csv", "/nh"],
            capture_output=True, text=True, check=False, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or not result.stdout:
        return None
    # "DOMAIN\user","S-1-5-21-..."
    parts = [p.strip().strip('"') for p in result.stdout.strip().split(",")]
    for p in parts:
        if p.upper().startswith("S-1-"):
            return p
    return None


def appdata_is_redirected() -> tuple[bool, str]:
    """
    Second, INDEPENDENT signal: has APPDATA been redirected to a service profile?

    Deliberately not derived from the SID. The SID says who we are; this says
    what actually broke. Either one alone can be wrong — a SID lookup can fail,
    and APPDATA can be redirected by something other than Session 0 — so both
    are reported and either one trips the assertion.
    """
    appdata = os.environ.get("APPDATA", "")
    local = os.environ.get("LOCALAPPDATA", "")
    marker = "systemprofile"
    for name, value in (("APPDATA", appdata), ("LOCALAPPDATA", local)):
        if value and marker in value.lower():
            return True, f"{name}={value}"
    if not appdata and not local:
        return True, "APPDATA and LOCALAPPDATA are BOTH unset"
    return False, f"APPDATA={appdata}"


def assert_not_service_account() -> dict[str, str]:
    """
    Spec §7.5. Raises IdentityError if the daemon is a service account.

    Returns the evidence on success so the caller can log what it verified
    rather than logging that it verified something.
    """
    if os.name != "nt":
        return {"platform": os.name, "checked": "skipped — not Windows"}

    sid = current_sid()
    redirected, appdata_detail = appdata_is_redirected()

    problems: list[str] = []
    if sid is not None and sid in SERVICE_SIDS:
        problems.append(f"running as {SERVICE_SIDS[sid]} ({sid})")
    if redirected:
        problems.append(f"APPDATA points into a service profile: {appdata_detail}")

    if problems:
        raise IdentityError(
            "spec §7.5: the daemon must NOT run as a service account. "
            + "; ".join(problems)
            + ". Session 0 isolation redirects APPDATA, which silently breaks "
            "`npm install -g`, `pip install --user`, and the runtime file both "
            "surfaces discover the daemon through. Start it as the owner."
        )

    return {
        "sid": sid or "unknown",
        "user": getpass.getuser(),
        "appdata": appdata_detail,
        "runtimeParent": str(Path(os.environ.get("LOCALAPPDATA", "")) / "Zoey"),
    }
