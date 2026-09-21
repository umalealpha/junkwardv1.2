@echo off
REM click.bat — make the checker CLICK a button, not just look at the screen.
REM     click "/dashboard" "Refresh"
REM It uses the same read-only session, so a click can never move money,
REM approve anything or change data. Before+after screenshots are saved.
setlocal
if "%~2"=="" (echo usage: click "^<screen^>" "^<button text^>"   e.g.  click "/dashboard" "Refresh" & exit /b 2)
cd /d "%~dp0engine"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0engine\qa-token.ps1" >nul 2>&1
node act.mjs %1 %2
echo.
echo Screenshots: %TEMP%\omni-qc
endlocal
