@echo off
REM qc.bat — run the Omni QC checker.
REM     qc dashboard          check one screen
REM     qc all                check every screen it knows
REM     qc uc_all             check the UniCoin portal only
REM     qc _canary            self-test: this one MUST fail
REM     qc all --report       also send the verdict to Telegram
setlocal
cd /d "%~dp0engine"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0engine\qa-token.ps1" >nul 2>&1
if "%~1"=="" (
  node qc.mjs all
) else (
  node qc.mjs %*
)
set RC=%ERRORLEVEL%
echo.
echo Screenshots: %TEMP%\omni-qc
if %RC%==0 (echo RESULT: all checked screens OK) else (echo RESULT: something failed - open the screenshots above)
endlocal & exit /b %RC%
