@chcp 65001>nul
@if /i "%TESSA_PROMPT%"=="shell" goto tessa_own
@prompt $e]133;D$e\$e]7;file://localhost/$P$e\$e]133;A$e\$e[%TESSA_SGR_FOLDER%m$P$e[0m$S$S$e[%TESSA_SGR_DIM%m$G$e[0m$S$e]133;B$e\
@goto tessa_banner
:tessa_own
@prompt $e]133;D$e\$e]7;file://localhost/$P$e\$e]133;A$e\%TESSA_OWN_PROMPT%$e]133;B$e\
:tessa_banner
@if defined TESSA_BANNER call :tessa_say
@set TESSA_PROMPT=
@set TESSA_SGR_FOLDER=
@set TESSA_SGR_DIM=
@set TESSA_SGR_INPUT=
@set TESSA_OWN_PROMPT=
@set TESSA_BANNER=
@set TESSA_ESC=
@goto :eof
:tessa_say
@setlocal EnableDelayedExpansion
@echo(!TESSA_ESC![!TESSA_SGR_DIM!m!TESSA_BANNER!!TESSA_ESC![0m
@endlocal
@goto :eof
