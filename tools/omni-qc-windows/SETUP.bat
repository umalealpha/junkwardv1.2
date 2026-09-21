@echo off
REM SETUP.bat — one double-click install of the Omni QC checker on Windows.
REM Installs the browser driver, drops the read-only key in place, installs the
REM /qc and /qctest skills for Claude Code, then PROVES it works:
REM   the self-test page MUST fail  +  a real Omni screen MUST pass.
setlocal enabledelayedexpansion
cd /d "%~dp0"
echo ============================================================
echo   Omni QC  -  Windows setup
echo ============================================================
echo.

REM --- 1. Node.js -------------------------------------------------------------
where node >nul 2>&1
if errorlevel 1 (
  echo [X] Node.js is not installed.
  echo     Install it first - https://nodejs.org  ^(pick the big green LTS button^)
  echo     Then run this file again.
  pause & exit /b 1
)
for /f "delims=" %%v in ('node -v') do set NODEV=%%v
echo [1/6] Node.js found: !NODEV!

REM --- 2. the checker's own bits ----------------------------------------------
echo [2/6] Installing the checker ^(about a minute^)...
pushd engine
call npm install --no-audit --no-fund --loglevel=error
if errorlevel 1 (echo [X] npm install failed. & popd & pause & exit /b 1)

REM --- 3. the browser it drives ------------------------------------------------
echo [3/6] Installing the browser it drives ^(about two minutes^)...
call npx --yes playwright install chromium
if errorlevel 1 (echo [X] browser install failed. & popd & pause & exit /b 1)
popd

REM --- 4. the read-only key -----------------------------------------------------
if exist "%USERPROFILE%\.omni-qa-key" (
  echo [4/6] Read-only key already in place.
) else (
  if exist "secrets\omni-qa-key.txt" (
    copy /y "secrets\omni-qa-key.txt" "%USERPROFILE%\.omni-qa-key" >nul
    echo [4/6] Read-only key installed.
  ) else (
    echo.
    echo [4/6] I need the read-only Omni key once.
    echo       On the Mac it is in the file  .omni-qa-key  in your home folder.
    echo       Get it onto the clipboard with this in the Mac Terminal:
    echo           cat ~/.omni-qa-key ^| pbcopy
    echo       Then paste it below and press Enter.
    echo.
    set /p QAKEY=Key:
    if "!QAKEY!"=="" (echo [X] Nothing pasted. & pause & exit /b 1)
    > "%USERPROFILE%\.omni-qa-key" echo(!QAKEY!
    echo       Key saved. It is read-only - it cannot change anything in Omni.
  )
)

REM --- 5. the /qc and /qctest skills for Claude Code -----------------------------
if not exist "%USERPROFILE%\.claude\skills" mkdir "%USERPROFILE%\.claude\skills"
xcopy /e /i /y "skills\qc"     "%USERPROFILE%\.claude\skills\qc"     >nul
xcopy /e /i /y "skills\qctest" "%USERPROFILE%\.claude\skills\qctest" >nul
REM remember where this folder lives, so Claude Code can always find the engine
> "%USERPROFILE%\.omni-qc-home" echo %~dp0
echo [5/6] /qc and /qctest installed for Claude Code.

REM --- 6. prove it --------------------------------------------------------------
echo [6/6] Proving it works...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0engine\qa-token.ps1" -Force
if errorlevel 1 (echo [X] Could not get a read-only session. Check the internet, then re-run. & pause & exit /b 1)

echo.
echo   -- self-test: a page that does not exist. It MUST be caught. --
pushd engine
node qc.mjs _canary
set CANARY=!ERRORLEVEL!
echo.
echo   -- real test: the Omni dashboard. It MUST pass. --
node qc.mjs dashboard
set REAL=!ERRORLEVEL!
echo.
echo   -- safety guard on the clever layer. It MUST say ALL CORRECT. --
node explore.mjs --selftest
set GUARD=!ERRORLEVEL!
popd

echo.
echo ============================================================
if "!CANARY!"=="1" (echo   Self-test  : GOOD  ^(the checker caught the broken page^)) else (echo   Self-test  : BAD  - the checker is blind, do not trust it)
if "!REAL!"=="0"   (echo   Real screen: GOOD  ^(the Omni dashboard loaded and passed^)) else (echo   Real screen: FAILED - open the screenshot below and look)
if "!GUARD!"=="0"  (echo   Safety guard: GOOD  ^(refuses every money button, allows navigation^)) else (echo   Safety guard: BAD - do not use explore.bat until this is fixed)
echo   Screenshots: %TEMP%\omni-qc
echo ============================================================
echo.
echo   From now on:  double-click qc.bat, or type  qc all
echo   In Claude Code on this PC:  /qc dashboard   or   /qctest
echo.
pause
endlocal
