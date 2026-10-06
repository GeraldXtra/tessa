@echo off
REM ==========================================================================
REM  start-tessa.cmd - starts the Tessa Core daemon with NO WINDOW.
REM
REM  Run by the Startup shortcut (shell:startup\Tessa.lnk) at sign-in, and by
REM  "tcli daemon start" / "tcli daemon restart", which pass --now.
REM
REM  WHY NO WINDOW: every daemon from 23 Sep to 5 Oct died when the console
REM  window this file used to leave open was closed ("forrtl: error (200):
REM  program aborting due to window-CLOSE event"). pythonw.exe has no console,
REM  so there is nothing on screen to close. Its output still goes to
REM  data\logs\daemon-<date>.log, so a failed start leaves its traceback there;
REM  "tcli daemon status" shows the last lines.
REM
REM  ONE DAEMON ONLY is decided by the daemon itself (a guard it owns), so a
REM  second launch is refused in about a second and changes nothing. That
REM  replaces the runtime.json check (autostart.py --check) that ran here.
REM
REM  To stop Tessa starting at sign-in: delete shell:startup\Tessa.lnk, or
REM  Task Manager > Startup apps > Tessa > Disable.
REM
REM  KEEP THIS FILE ASCII, WITH CRLF LINE ENDINGS AND NO BOM: cmd.exe misreads
REM  an LF-only batch file and fails on a BOM in the first line.
REM ==========================================================================
setlocal
cd /d "C:\dev\tessa"
set "LOGDIR=C:\dev\tessa\data\logs"
set "PYW=C:\Users\SERIOUS-PC\AppData\Local\Programs\Python\Python312\pythonw.exe"

REM Test-only TESSA_* overrides must never reach a real launch. Cleared by
REM name, not with FOR /F over SET: that runs a child cmd, which gets a NEW
REM console (a Windows Terminal window) whenever this file runs without one.
REM A new TESSA_* test variable must be added to this line.
set "TESSA_RUNTIME_DIR="

REM Intel's OpenMP runtime (loaded with Whisper) must not install its console
REM handler. The daemon also sets this itself, first thing. Both, on purpose.
set "FOR_DISABLE_CONSOLE_CTRL_HANDLER=1"

if /i "%~1"=="--now" goto :launch
REM -- WAIT FOR THE AUDIO STACK ------------------------------------------
REM At sign-in the shortcut fires before the audio endpoints have finished
REM enumerating; with --voice that means opening a microphone that is not
REM there yet. This minimised window waits 20 s and then closes itself.
title Tessa - starting in 20 seconds
timeout /t 20 /nobreak > nul

:launch
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
for /f "tokens=1-3 delims=/-. " %%a in ("%DATE%") do set "STAMP=%%c-%%b-%%a"
echo. >> "%LOGDIR%\daemon-%STAMP%.log"
echo ==== launcher %DATE% %TIME% %~1 ==== >> "%LOGDIR%\daemon-%STAMP%.log"
REM START /B opens no console; pythonw.exe is window-less, so START returns at
REM once and this window closes. The daemon inherits the redirect.
start "" /b "%PYW%" "C:\dev\tessa\core\server.py" --dev --voice --stt-model base >> "%LOGDIR%\daemon-%STAMP%.log" 2>&1
endlocal
exit /b 0
