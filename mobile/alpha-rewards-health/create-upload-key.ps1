# Creates a BRAND NEW upload-signing key for Alpha Nexus, and exports the
# certificate that Google needs for an upload key reset.
#
# Run this only after the Play Console account owner has granted the permission
# to request an upload key reset. Order matters:
#   1. this script                -> creates the key + upload_certificate.pem
#   2. Play Console               -> request upload key reset, attach the .pem
#   3. wait for Google (days)     -> they confirm by email
#   4. build-aab.ps1              -> build signed with the new key, then upload
# Building and uploading BEFORE Google confirms the reset will be rejected.
#
# The password is typed by you, verified, and written only to
# android/keystore.properties, which git ignores. It is never displayed.
#
# The previous upload key was lost because its password was never recorded.
# This script refuses to continue until you confirm you have saved the new one.

$ErrorActionPreference = "Stop"

$keystore = Join-Path $PSScriptRoot "android\app\upload.keystore"
$props    = Join-Path $PSScriptRoot "android\keystore.properties"
$alias    = "upload"
$desktop  = [Environment]::GetFolderPath('Desktop')
$pemOut   = Join-Path $desktop "Alpha-Nexus-upload_certificate.pem"

if (-not $env:JAVA_HOME) { $env:JAVA_HOME = "C:\Program Files\Eclipse Adoptium\jdk-17.0.19.10-hotspot" }
$keytool = Join-Path $env:JAVA_HOME "bin\keytool.exe"
if (-not (Test-Path $keytool)) {
    $onPath = Get-Command keytool -ErrorAction SilentlyContinue
    if ($onPath) { $keytool = $onPath.Source }
}
if (-not (Test-Path $keytool)) {
    Write-Host "Cannot find keytool. Set JAVA_HOME to a JDK 17 and run again." -ForegroundColor Red
    exit 1
}

# Never silently replace an existing key - that would lose it exactly like last time.
if (Test-Path $keystore) {
    Write-Host ""
    Write-Host "An upload key already exists at:" -ForegroundColor Yellow
    Write-Host "  $keystore"
    Write-Host "Refusing to overwrite it. If you truly need a new one, rename the old file first."
    exit 1
}

Write-Host ""
Write-Host "Alpha Nexus - create a new upload key" -ForegroundColor Cyan
Write-Host "-------------------------------------"
Write-Host "This key is how Google knows a build came from Alpha Direct."
Write-Host "If its password is lost, the app cannot be updated. That already happened once."
Write-Host ""
Write-Host "STEP 1. Open your password manager and create the entry FIRST." -ForegroundColor Yellow
Write-Host "        Name it: Alpha Nexus - Play Store upload key"
Write-Host "        Put the password in it, and save it, BEFORE typing it below."
Write-Host ""
$ready = Read-Host "Have you saved the password in your password manager? (type YES)"
if ($ready -ne "YES") {
    Write-Host "Stopped. Nothing was created. Save the password first, then run this again." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "STEP 2. Type the same password twice. It stays hidden." -ForegroundColor Yellow
$s1 = Read-Host "Password" -AsSecureString
$s2 = Read-Host "Confirm password" -AsSecureString
$p1 = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($s1))
$p2 = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($s2))

if ($p1 -ne $p2)          { Write-Host "The two entries do not match. Nothing created." -ForegroundColor Red; exit 1 }
if ($p1.Length -lt 8)     { Write-Host "Too short - use at least 8 characters. Nothing created." -ForegroundColor Red; exit 1 }
# Gradle reads keystore.properties as ISO-8859-1, so a non-ASCII character would be mangled.
if ($p1 -match '[^\x20-\x7e]') {
    Write-Host "Use only standard keyboard characters (no accents or symbols outside a UK/US keyboard)." -ForegroundColor Red
    Write-Host "Nothing created."
    exit 1
}

Write-Host ""
Write-Host "Creating the key..." -ForegroundColor Cyan
& $keytool -genkeypair -v -storetype PKCS12 `
    -keystore $keystore -storepass $p1 -keypass $p1 -alias $alias `
    -keyalg RSA -keysize 2048 -validity 10950 `
    -dname "CN=Alpha Nexus, O=Alpha Direct Insurance Company, L=Gaborone, ST=Gaborone, C=BW" | Out-Null
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $keystore)) {
    Write-Host "keytool failed. Nothing usable was created." -ForegroundColor Red; exit 1
}

# Prove the key opens with the password just set, before relying on it.
$listing = & $keytool -list -v -keystore $keystore -storepass $p1
if ($LASTEXITCODE -ne 0) {
    Write-Host "The new key could not be reopened. Something is wrong - do not use it." -ForegroundColor Red; exit 1
}
$fp = ($listing | Select-String -Pattern 'SHA256:\s*(.+)$' | Select-Object -First 1).Matches.Groups[1].Value.Trim()

# Export the certificate Google asks for during the reset.
& $keytool -export -rfc -keystore $keystore -alias $alias -storepass $p1 -file $pemOut | Out-Null
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $pemOut)) {
    Write-Host "Could not export the certificate." -ForegroundColor Red; exit 1
}

# Written with WriteAllLines: UTF-8 with NO byte-order mark. Set-Content -Encoding utf8
# on PowerShell 5.1 adds one, and Java's Properties.load would read it into the first
# key name, silently blanking storeFile.
[IO.File]::WriteAllLines($props, @(
  "storeFile=upload.keystore"
  "storePassword=$p1"
  "keyAlias=$alias"
  "keyPassword=$p1"
))

Write-Host ""
Write-Host "Done." -ForegroundColor Green
Write-Host ""
Write-Host "The certificate to give Google is on your Desktop:" -ForegroundColor Cyan
Write-Host "  Alpha-Nexus-upload_certificate.pem"
Write-Host ""
Write-Host "Key fingerprint (SHA-256), to compare against Play Console afterwards:"
Write-Host "  $fp"
Write-Host ""
Write-Host "Next: in Play Console, Test and release -> Setup -> App integrity ->"
Write-Host "      Request upload key reset, and attach that .pem file."
Write-Host ""
Write-Host "Only AFTER Google confirms the reset, build the upload file:" -ForegroundColor Yellow
Write-Host "  powershell -ExecutionPolicy Bypass -File `"$PSScriptRoot\build-aab.ps1`""
