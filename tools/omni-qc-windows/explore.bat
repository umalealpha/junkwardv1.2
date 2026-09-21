@echo off
REM explore.bat — tell the checker a GOAL in plain words and let it work out the clicks.
REM     explore "/dashboard" "go to the Commissions screen"
REM     explore "/commissions/brokers" "find the broker with the worst loss ratio"
REM     explore --selftest        checks its own safety guard, no browser needed
REM
REM Same read-only login as everything else, plus its own blocklist: it refuses to
REM touch approve / pay / post / delete / save / export and 20 more. It thinks on
REM the PC's own AI by default, so nothing about the page leaves the machine.
REM It is an EXPLORER, not a verdict - confirm anything it finds with qc.bat.
setlocal
cd /d "%~dp0engine"
if "%~1"=="--selftest" (node explore.mjs --selftest & exit /b %ERRORLEVEL%)
if "%~2"=="" (echo usage: explore "^<screen^>" "^<goal in plain words^>"    ^| or: explore --selftest & exit /b 2)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0engine\qa-token.ps1" >nul 2>&1
node explore.mjs %*
set RC=%ERRORLEVEL%
echo.
echo Screenshots: %TEMP%\omni-qc
endlocal & exit /b %RC%
