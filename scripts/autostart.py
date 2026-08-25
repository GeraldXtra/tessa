"""
scripts/autostart.py — she is running when he logs in.

    python scripts/autostart.py --status
    python scripts/autostart.py --install            (no microphone)
    python scripts/autostart.py --install --voice     (microphone live at login)
    python scripts/autostart.py --remove

────────────────────────────────────────────────────────────────────────────────
THE GAP THIS CLOSES

"When I boot my system and I say Hey Tessa" needs the daemon to be running after
a restart. It is not. He starts it by hand in a PowerShell window every time,
and if that window closes she is gone. That is the distance between "an
always-on personal agent" and what exists.

────────────────────────────────────────────────────────────────────────────────
FOUR OPTIONS. THE STARTUP FOLDER WINS.

  1. STARTUP FOLDER SHORTCUT            <- chosen
     %APPDATA%\\...\\Start Menu\\Programs\\Startup\\Tessa.lnk
     + Runs as HIM, in HIS session, after the shell is up.
     + Removable by deleting one file, which he can do in Explorer.
     + Appears in Task Manager's Startup tab with an on/off switch he already
       knows how to use. Nothing else on this list is discoverable that way.
     + No admin rights, no registry, no service.
     - Runs a few seconds after logon rather than before it. Irrelevant here.

  2. REGISTRY Run KEY (HKCU\\...\\CurrentVersion\\Run)
     Identical effect, strictly worse properties: invisible unless he opens
     regedit, and it is the single most common malware persistence location, so
     it argues with antivirus heuristics for no benefit.

  3. SCHEDULED TASK AT LOGON
     More capable — delays, restart-on-failure, run-whether-logged-on-or-not.
     That last capability is a reason to avoid it, not to want it: a task that
     runs without a logged-on session has no audio device and no desktop, so the
     microphone and speakers do not work, and it would look like a daemon that
     starts and does nothing. More surface, harder to remove, no gain.

  4. WINDOWS SERVICE — WRONG, AND I WANT TO SAY WHY RATHER THAN SKIP IT.
     Two independent reasons, either one fatal.

     POLICY: spec §7.5 requires the daemon NOT to run as a service account, and
     `core/security/identity.py` already refuses to start when APPDATA resolves
     into a service profile. Installing a service would build a thing that this
     codebase deliberately refuses to be.

     PHYSICS: a service runs in session 0, which has no audio endpoint. There is
     no microphone and no speaker in session 0. A voice assistant as a Windows
     service cannot hear or speak — the whole feature would be dead on arrival,
     and it would fail silently rather than loudly.

     Phase 3 in the spec still says "Windows service". That should be revisited
     for exactly this reason; it is in `docs-reconciliation.md`.

────────────────────────────────────────────────────────────────────────────────
THE MICROPHONE IS OPT-IN. HE TYPES --voice.

`--install` alone starts a daemon with NO microphone. `--install --voice` starts
one that opens the microphone at login and holds it open.

That is a real change in what his machine does when he turns it on, and
`voice.stream.open` is red tier precisely because always-live capture is a
privacy fact rather than a convenience. So it is never the default — but typing
`--voice` once IS the opt-in, and it is what he actually wants, so it is not
hidden behind anything either.

────────────────────────────────────────────────────────────────────────────────
IT CANNOT FAIL WHERE NOBODY IS LOOKING

Started at login the daemon has no console he is watching. A daemon that refuses
to start over a broken ACL, a chain fault or a missing model must not disappear
quietly.

  * stdout and stderr are appended to `data/logs/daemon-YYYY-MM-DD.log`.
  * The window is MINIMISED, not hidden. It exists in his taskbar, so "is she
    running" is answerable by looking, exactly as it is today.
  * `--status` prints whether the shortcut is installed, whether a daemon is
    live, and the last lines of today's log.

A hidden `pythonw` was the obvious choice and is rejected on purpose: it makes a
failed start indistinguishable from a working one until he speaks to her and
nothing happens.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STARTUP_DIR = (Path(os.environ.get("APPDATA", ""))
               / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup")
SHORTCUT = STARTUP_DIR / "Tessa.lnk"
LOG_DIR = REPO / "data" / "logs"
LAUNCHER = REPO / "scripts" / "start-tessa.cmd"


def _write_launcher(voice: bool, stt_model: str) -> Path:
    """
    A .cmd wrapper, so the shortcut points at something readable.

    The alternative is baking a long argument string into the .lnk, where he
    cannot see it and cannot change it without this script. A batch file in the
    repo is inspectable and editable with Notepad.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    args = "--dev"
    if voice:
        args += f" --voice --stt-model {stt_model}"
    body = f"""@echo off
REM Generated by scripts/autostart.py. Safe to read, edit or delete.
REM Delete the shortcut in shell:startup to stop Tessa starting at login,
REM or turn it off in Task Manager > Startup.
cd /d "{REPO}"
set "LOGDIR={LOG_DIR}"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
for /f "tokens=1-3 delims=/-. " %%a in ("%DATE%") do set "STAMP=%%c-%%b-%%a"
echo. >> "%LOGDIR%\\daemon-%STAMP%.log"
echo ==== started %DATE% %TIME% ==== >> "%LOGDIR%\\daemon-%STAMP%.log"
"{sys.executable}" core\\server.py {args} >> "%LOGDIR%\\daemon-%STAMP%.log" 2>&1
echo ==== exited with %ERRORLEVEL% at %TIME% ==== >> "%LOGDIR%\\daemon-%STAMP%.log"
REM A non-zero exit leaves this window open so a failed start is VISIBLE.
if not "%ERRORLEVEL%"=="0" (
  echo.
  echo Tessa exited with %ERRORLEVEL%. The log is at:
  echo   %LOGDIR%\\daemon-%STAMP%.log
  pause
)
"""
    LAUNCHER.write_text(body, encoding="utf-8")
    return LAUNCHER


def _make_shortcut(target: Path) -> tuple[bool, str]:
    """Create the .lnk via WScript.Shell. No pywin32 on this machine."""
    STARTUP_DIR.mkdir(parents=True, exist_ok=True)
    ps = f"""
$s = New-Object -ComObject WScript.Shell
$lnk = $s.CreateShortcut('{SHORTCUT}')
$lnk.TargetPath = '{target}'
$lnk.WorkingDirectory = '{REPO}'
$lnk.Description = 'Tessa Core daemon'
$lnk.WindowStyle = 7
$lnk.Save()
"""
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0 or not SHORTCUT.exists():
        return False, (r.stderr or "shortcut was not created").strip()
    return True, str(SHORTCUT)


def install(voice: bool, stt_model: str) -> int:
    launcher = _write_launcher(voice, stt_model)
    ok, detail = _make_shortcut(launcher)
    if not ok:
        print(f"could not install: {detail}")
        return 1
    print("INSTALLED.")
    print(f"  shortcut : {SHORTCUT}")
    print(f"  launcher : {launcher}")
    print(f"  logs     : {LOG_DIR}")
    print(f"  voice    : {'ON - the microphone opens at login' if voice else 'off'}")
    print()
    print("To turn it off, either:")
    print("  python scripts/autostart.py --remove")
    print("  or delete the shortcut: press Win+R, type  shell:startup")
    print("  or Task Manager > Startup > Tessa > Disable")
    return 0


def remove() -> int:
    gone = False
    if SHORTCUT.exists():
        SHORTCUT.unlink()
        gone = True
    print("REMOVED." if gone else "Nothing to remove — no shortcut installed.")
    print(f"  {SHORTCUT}")
    if LAUNCHER.exists():
        print(f"  the launcher stays at {LAUNCHER} (harmless; delete if you like)")
    return 0


def status() -> int:
    print(f"shortcut installed : {SHORTCUT.exists()}  ({SHORTCUT})")
    if LAUNCHER.exists():
        body = LAUNCHER.read_text(encoding="utf-8")
        voice = "--voice" in body
        print(f"launcher           : {LAUNCHER}")
        print(f"  microphone at login: {'ON' if voice else 'off'}")

    sys.path.insert(0, str(REPO))
    try:
        from core.security import runtime as rt

        info = rt.read_runtime_file()
        if info:
            print(f"daemon LIVE        : pid {info['pid']} on port {info['port']} "
                  f"since {info['startedAt']}")
        else:
            print("daemon LIVE        : no (no valid runtime.json)")
    except Exception as exc:  # noqa: BLE001
        print(f"daemon LIVE        : could not tell ({exc})")

    logs = sorted(LOG_DIR.glob("daemon-*.log")) if LOG_DIR.exists() else []
    if not logs:
        print("logs               : none yet")
        return 0
    newest = logs[-1]
    print(f"newest log         : {newest}")
    tail = newest.read_text(encoding="utf-8", errors="replace").splitlines()[-12:]
    for line in tail:
        print(f"    {line}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Start Tessa at login")
    ap.add_argument("--install", action="store_true")
    ap.add_argument("--remove", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--voice", action="store_true",
                    help="open the microphone at login (opt-in, not the default)")
    ap.add_argument("--stt-model", default="base")
    a = ap.parse_args()

    if a.remove:
        raise SystemExit(remove())
    if a.install:
        raise SystemExit(install(a.voice, a.stt_model))
    raise SystemExit(status())
