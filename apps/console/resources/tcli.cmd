@echo off
rem ==========================================================================
rem  tcli - open Tessa Console at the folder you are standing in.
rem
rem  THIS FILE IS FOR TERMINALS ONLY: cmd, PowerShell, Git Bash.
rem  The Explorer address bar does NOT come through here. It resolves tcli via
rem  the App Paths registry key, which points straight at TessaConsole.exe and
rem  therefore flashes no window at all. A .cmd launched by ShellExecute would
rem  flash a black console every single time - which is exactly why the
rem  Explorer route deliberately does not use this file.
rem
rem  Typed in a terminal there is no flash: it runs inside the console that is
rem  already open.
rem
rem  THIS FILE MUST BE SAVED WITH CRLF LINE ENDINGS.
rem  cmd.exe mis-parses an LF-only batch file: a multi-line IF block runs its
rem  own body as commands and REM text is executed a word at a time. That is
rem  not a style preference, it is why this file is written with a script.
rem  The IF below is therefore a single line plus GOTO, not a parenthesised
rem  block, so the file stays robust even if an editor rewrites the endings.
rem ==========================================================================

setlocal
set "TESSA_EXE=%LOCALAPPDATA%\Programs\TessaConsole\TessaConsole.exe"

if not exist "%TESSA_EXE%" goto :notinstalled

rem  NO --cwd. It was here, and MEASURING IT PROVED IT WRONG.
rem
rem  The reasoning was that START might reset the child directory differently
rem  per host shell, so the folder should be stated explicitly. Measured, START
rem  inherits %CD% correctly and Electron reports it verbatim as the
rem  second-instance workingDirectory:
rem
rem    workingDirectory="...	cli-tests\EVM Anti-drain Wallet"
rem
rem  Passing --cwd was actively harmful. Electron hands second-instance the
rem  child's argv AFTER Chromium has appended its own switches, and the element
rem  after --cwd arrived as --allow-file-access-from-files. The Console tried to
rem  stat a switch name, failed, and opened the tab in the home directory
rem  instead of the folder he was standing in.
rem
rem  main/index.ts now also refuses a --cwd value beginning with "-", so both
rem  halves are safe. Not passing it at all is the simpler half.
rem
rem  The empty "" is START's window-TITLE argument. Omit it and START treats
rem  the quoted exe path as a title and opens a stray console instead.
start "" "%TESSA_EXE%" %*
endlocal
exit /b 0

:notinstalled
echo.
echo   tcli: Tessa Console is not installed.
echo.
echo   Expected it at:
echo     %TESSA_EXE%
echo.
echo   Build and install it with:
echo     npm run reinstall -w @tessa/console
echo.
endlocal
exit /b 9009
