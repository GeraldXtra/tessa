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


def _write_launcher(voice: bool, stt_model: str, delay: int = 20) -> Path:
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

REM -- WAIT FOR THE AUDIO STACK ------------------------------------------
REM A Startup-folder shortcut fires as soon as the shell is up, which can be
REM BEFORE the audio endpoints have finished enumerating. With --voice that
REM means opening a microphone that is not there yet: she either comes up deaf
REM or dies, in a minimised window nobody is watching. A Scheduled Task has a
REM built-in delay and a Startup shortcut does not, so the wait lives here.
REM Change the number or delete these two lines if it is not wanted.
timeout /t {delay} /nobreak > nul
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
for /f "tokens=1-3 delims=/-. " %%a in ("%DATE%") do set "STAMP=%%c-%%b-%%a"
echo. >> "%LOGDIR%\\daemon-%STAMP%.log"
echo ==== started %DATE% %TIME% ==== >> "%LOGDIR%\\daemon-%STAMP%.log"
REM -- THE DOUBLE-START GUARD --------------------------------------------
REM Exit 3 means a daemon is already live, so this login must not start a
REM second one. Two overlapping daemons forked the audit chain once already.
"{sys.executable}" scripts\\autostart.py --check >> "%LOGDIR%\\daemon-%STAMP%.log" 2>&1
if errorlevel 3 goto :alreadyrunning

"{sys.executable}" core\\server.py {args} >> "%LOGDIR%\\daemon-%STAMP%.log" 2>&1
echo ==== exited with %ERRORLEVEL% at %TIME% ==== >> "%LOGDIR%\\daemon-%STAMP%.log"
REM A non-zero exit leaves this window open so a failed start is VISIBLE.
if not "%ERRORLEVEL%"=="0" (
  echo.
  echo Tessa exited with %ERRORLEVEL%. The log is at:
  echo   %LOGDIR%\\daemon-%STAMP%.log
  pause
)
exit /b 0

:alreadyrunning
REM Not an error, and deliberately quiet: a daemon is already up and doing its
REM job. Recorded in the log so "why did nothing start at login" is answerable.
echo ==== skipped: a daemon was already running ==== >> "%LOGDIR%\\daemon-%STAMP%.log"
exit /b 0
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


EXIT_ALREADY_RUNNING = 3


def check_running() -> int:
    """
    THE DOUBLE-START GUARD. Exit 0 = safe to start. Exit 3 = one is already up.

    ────────────────────────────────────────────────────────────────────────────
    WHY THIS EXISTS, IN ONE SENTENCE

    Two overlapping daemons are what FORKED HIS AUDIT CHAIN at line 71 on
    2026-08-12 — a `daemon.start` at 14:00:56 and a `daemon.stop` at 14:00:58
    both claiming seq 69. That break cannot be repaired and is still there.

    Login is precisely when it would happen again: he leaves a daemon running in
    a PowerShell window, the machine sleeps rather than shuts down, and the next
    logon fires the Startup shortcut into a session that already has one.

    ────────────────────────────────────────────────────────────────────────────
    IT ASKS THE ONE FUNCTION THAT ALREADY KNOWS

    `rt.read_runtime_file()` implements CONTRACT §1 already: it reads
    runtime.json and returns None when the recorded pid is NOT alive, so a stale
    file left by a power cut is ignored rather than trusted. Re-deriving that
    here would be a second, drifting copy of a rule with exactly one correct
    implementation.

    ────────────────────────────────────────────────────────────────────────────
    WHICH WAY IT FAILS, AND WHY THAT DIRECTION

    An unreadable file, or a `core` that will not import, returns 0 — SAFE TO
    START. Failing the other way would let one broken file permanently stop her
    starting at login, with no window and no message to say why. A spurious
    second daemon is loud and fixable; a silent never-starts is neither.
    """
    sys.path.insert(0, str(REPO))
    try:
        from core.security import runtime as rt

        info = rt.read_runtime_file()
    except Exception as exc:  # noqa: BLE001
        print(f"guard: cannot tell ({type(exc).__name__}: {exc}) — starting anyway")
        return 0

    if info:
        print(f"guard: a daemon is ALREADY RUNNING — pid {info['pid']} on port "
              f"{info['port']} since {info['startedAt']}. Not starting a second one.")
        return EXIT_ALREADY_RUNNING

    print("guard: no live daemon found — safe to start")
    return 0


def install(voice: bool, stt_model: str, delay: int = 20) -> int:
    launcher = _write_launcher(voice, stt_model, delay)
    ok, detail = _make_shortcut(launcher)
    if not ok:
        print(f"could not install: {detail}")
        return 1
    print("INSTALLED.")
    print(f"  shortcut : {SHORTCUT}")
    print(f"  launcher : {launcher}")
    print(f"  logs     : {LOG_DIR}")
    print(f"  voice    : {'ON - the microphone opens at login' if voice else 'off'}")
    print(f"  delay    : {delay}s after logon, so the audio stack is ready")
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
    ap.add_argument("--check", action="store_true",
                    help="exit 3 if a daemon is already live (the double-start guard)")
    ap.add_argument("--voice", action="store_true",
                    help="open the microphone at login (opt-in, not the default)")
    ap.add_argument("--stt-model", default="base")
    ap.add_argument("--delay", type=int, default=20,
                    help="seconds to wait after logon before starting (audio readiness)")
    a = ap.parse_args()

    if a.check:
        raise SystemExit(check_running())
    if a.remove:
        raise SystemExit(remove())
    if a.install:
        raise SystemExit(install(a.voice, a.stt_model, a.delay))
    raise SystemExit(status())
