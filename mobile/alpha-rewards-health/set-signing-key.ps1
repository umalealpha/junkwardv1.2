# Sets up the Alpha Nexus upload-signing key so the release build can be signed.
#
# Run this, paste the password when asked (it stays hidden and never leaves this
# machine), and it writes android/keystore.properties, the file Gradle reads.
# The password is checked against the keystore before anything is written, so a
# wrong password fails here instead of producing an unsigned bundle.

$ErrorActionPreference = "Stop"

$keystore = Join-Path $PSScriptRoot "android\app\release.keystore"
$target   = Join-Path $PSScriptRoot "android\keystore.properties"
if (-not $env:JAVA_HOME) { $env:JAVA_HOME = "C:\Program Files\Eclipse Adoptium\jdk-17.0.19.10-hotspot" }
$keytool = Join-Path $env:JAVA_HOME "bin\keytool.exe"
if (-not (Test-Path $keytool)) {
    $onPath = Get-Command keytool -ErrorAction SilentlyContinue
    if ($onPath) { $keytool = $onPath.Source }
}

if (-not (Test-Path $keystore)) { Write-Host "Cannot find the signing key at $keystore" -ForegroundColor Red; exit 1 }
if (-not (Test-Path $keytool))  { Write-Host "Cannot find keytool at $keytool" -ForegroundColor Red; exit 1 }

Write-Host ""
Write-Host "Alpha Nexus signing key" -ForegroundColor Cyan
Write-Host "The password is the one used when the key was created on 30 June 2026."
Write-Host "It should be in your password manager under Alpha Nexus / Google Play."
Write-Host ""

$secure = Read-Host "Password" -AsSecureString
$plain  = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
            [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))

# Verify before writing, and discover the key alias from the keystore itself.
$listing = & $keytool -list -v -keystore $keystore -storepass $plain 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "That password did not open the key. Nothing was saved." -ForegroundColor Red
    Write-Host "Try again, or ask Google Play for an upload-key reset (Play Console -> Test and release -> Setup -> App integrity)."
    exit 1
}

$alias = ($listing | Select-String -Pattern '^Alias name:\s*(.+)$' |
          Select-Object -First 1).Matches.Groups[1].Value.Trim()
if (-not $alias) { Write-Host "Could not read the key alias." -ForegroundColor Red; exit 1 }

# Java's Properties.load reads ISO-8859-1 and does NOT strip a byte-order mark, so a BOM
# would arrive as part of the first key name (BOM + "storeFile") and that key would silently
# read as null in build.gradle. Set-Content -Encoding utf8 on Windows PowerShell 5.1 DOES
# emit a BOM, so write the bytes directly instead - WriteAllLines uses UTF-8 without one.
if ($plain -match '[^\x20-\x7e]') {
    Write-Host ""
    Write-Host "That password contains a non-ASCII character." -ForegroundColor Red
    Write-Host "Gradle reads this file as ISO-8859-1 and would mangle it. Nothing was saved."
    Write-Host "Use the Play Console upload-key reset instead, and choose an ASCII password."
    exit 1
}
[IO.File]::WriteAllLines($target, @(
  "storeFile=release.keystore"
  "storePassword=$plain"
  "keyAlias=$alias"
  "keyPassword=$plain"
))

Write-Host ""
Write-Host "Saved. Key alias '$alias' verified." -ForegroundColor Green
Write-Host "This file is ignored by git and stays on this machine only."
Write-Host ""
Write-Host "Now build the upload file:" -ForegroundColor Cyan
Write-Host "  powershell -ExecutionPolicy Bypass -File `"$PSScriptRoot\build-aab.ps1`""
