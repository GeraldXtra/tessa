"""
core/tests/test_identity.py — spec §7.5, constructed rather than reasoned about.

An assertion that has never been seen to fire is an assertion you are trusting,
not one you have tested. So this forces both trip conditions with a real
subprocess and a redirected environment, and confirms the daemon REFUSES TO
START rather than logging a warning and carrying on.

    python core/tests/test_identity.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.security.identity import (  # noqa: E402
    SERVICE_SIDS,
    IdentityError,
    appdata_is_redirected,
    assert_not_service_account,
    current_sid,
)

passed = failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok    {name}" + (f"  {detail}" if detail else ""))
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


print("\nspec §7.5 — the daemon must NOT be a service account\n")

# ── the happy path, on this machine ──────────────────────────────────────────
sid = current_sid()
print(f"  detected SID     : {sid}")
redirected, detail = appdata_is_redirected()
print(f"  APPDATA          : {detail}")
print(f"  redirected       : {redirected}\n")

check("a SID was actually resolved (not a silent None)", sid is not None, str(sid))
check("this SID is not a service account", sid not in SERVICE_SIDS, str(sid))
try:
    ev = assert_not_service_account()
    check("assert passes on this machine", True, f"user={ev['user']}")
except IdentityError as exc:
    check("assert passes on this machine", False, str(exc))

# ── TRIP 1: redirected APPDATA, in-process ───────────────────────────────────
print("\n  forcing trip 1: APPDATA redirected into a service profile")
saved = os.environ.get("APPDATA")
os.environ["APPDATA"] = r"C:\Windows\System32\config\systemprofile\AppData\Roaming"
try:
    assert_not_service_account()
    check("redirected APPDATA raises IdentityError", False, "it did NOT raise")
except IdentityError as exc:
    check("redirected APPDATA raises IdentityError", True, str(exc)[:96] + "…")
finally:
    if saved is None:
        os.environ.pop("APPDATA", None)
    else:
        os.environ["APPDATA"] = saved

# ── TRIP 2: both APPDATA vars unset ──────────────────────────────────────────
print("\n  forcing trip 2: APPDATA and LOCALAPPDATA both unset")
sa, sl = os.environ.pop("APPDATA", None), os.environ.pop("LOCALAPPDATA", None)
try:
    assert_not_service_account()
    check("unset APPDATA raises IdentityError", False, "it did NOT raise")
except IdentityError as exc:
    check("unset APPDATA raises IdentityError", True, str(exc)[:80] + "…")
finally:
    if sa is not None:
        os.environ["APPDATA"] = sa
    if sl is not None:
        os.environ["LOCALAPPDATA"] = sl

# ── TRIP 3: THE REAL ONE — does the DAEMON refuse to start? ──────────────────
#
# The two above prove the function raises. This proves the daemon acts on it:
# a real `python core/server.py` in a child process with a redirected APPDATA,
# checked for "REFUSING TO START" and for the ABSENCE of "listening on".
print("\n  forcing trip 3: a REAL daemon launch with APPDATA redirected")
env = dict(os.environ)
env["APPDATA"] = r"C:\Windows\System32\config\systemprofile\AppData\Roaming"
env["PYTHONUNBUFFERED"] = "1"
proc = subprocess.run(
    [sys.executable, "core/server.py", "--dev"],
    cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=60,
)
out = (proc.stdout or "") + (proc.stderr or "")
for line in out.strip().splitlines()[:6]:
    print(f"      | {line}")

check("daemon logged REFUSING TO START", "REFUSING TO START" in out)
check("daemon NEVER reached 'listening on'", "listening on" not in out,
      "a warn-and-continue here would be the landmine")
check("daemon did NOT write a runtime file for the bad identity",
      "runtime file:" not in out)
check("daemon exited rather than serving", proc.returncode == 0,
      f"exit code {proc.returncode} (clean refusal, not a crash)")

print(f"\n{passed} passed, {failed} failed\n")
sys.exit(0 if failed == 0 else 1)
