# Verification build: proves the icons / 16 KB alignment / API 36 changes without
# the upload key. Produces a DEBUG-SIGNED bundle that must never be uploaded.
# For a real release use build-aab.ps1 instead.

$ErrorActionPreference = "Continue"
$androidDir = Join-Path $PSScriptRoot "android"
$log = Join-Path $PSScriptRoot "verify-build.log"
"=== Verify build started $(Get-Date) ===" | Out-File $log -Encoding utf8

# JAVA_HOME deliberately not inherited - see the explanation in build-aab.ps1.
if ($env:ALPHA_NEXUS_JAVA_HOME) { $env:JAVA_HOME = $env:ALPHA_NEXUS_JAVA_HOME }
else { $env:JAVA_HOME = "C:\Program Files\Eclipse Adoptium\jdk-17.0.19.10-hotspot" }
if (-not $env:ANDROID_HOME) { $env:ANDROID_HOME = "$env:LOCALAPPDATA\Android\Sdk" }
if (-not $env:JDK17_PATCH)  { $env:JDK17_PATCH  = "$env:TEMP\jdk17patch\classes" }
$env:PATH = "$env:JAVA_HOME\bin;$env:PATH"
$env:ANDROID_SDK_ROOT = $env:ANDROID_HOME

$javaExe = Join-Path $env:JAVA_HOME "bin\java.exe"
if (-not (Test-Path $javaExe)) {
    "FATAL: no java.exe at $javaExe. Set ALPHA_NEXUS_JAVA_HOME to a JDK 17+." | Out-File $log -Append; exit 1
}
$verLine = (& $javaExe -version 2>&1 | Select-Object -First 1) -join ''
if ($verLine -notmatch '"(\d+)') { "FATAL: cannot read Java version from: $verLine" | Out-File $log -Append; exit 1 }
$javaMajor = [int]$Matches[1]
if ($javaMajor -lt 17) {
    "FATAL: JAVA_HOME is Java $javaMajor. Gradle 8.14 / AGP 8.11 need 17+." | Out-File $log -Append; exit 1
}
if ((Test-Path $env:JDK17_PATCH) -and $javaMajor -eq 17) {
    $env:JAVA_TOOL_OPTIONS = "--patch-module=java.base=$($env:JDK17_PATCH)"
} else { $env:JAVA_TOOL_OPTIONS = "" }
"Java $javaMajor at $($env:JAVA_HOME)" | Out-File $log -Append

Set-Location $androidDir

# 1. The signing gate must REFUSE without the opt-in flag. Only meaningful while the
#    upload key is absent - once keystore.properties exists a release build SHOULD
#    succeed, and asserting a refusal then would cry wolf forever.
$keyFile = Join-Path $androidDir "keystore.properties"
if (Test-Path $keyFile) {
    "gate test skipped: keystore.properties present, so a release build is expected to succeed" | Out-File $log -Append
} else {
    "--- gate test: bundleRelease with no keystore.properties and no opt-in ---" | Out-File $log -Append
    $gate = & ".\gradlew.bat" ":app:bundleRelease" "--no-daemon" "--console=plain" 2>&1
    $gate | Out-File $log -Append
    if ($gate -match "Refusing to build a release artifact") {
        "GATE OK: build refused as designed" | Out-File $log -Append
    } else {
        # A real regression: the gate let an unsigned release through, OR the build died
        # for an unrelated reason and this test proved nothing. Either way, stop.
        "GATE FAILED: expected a refusal and did not get one - see the gradle output above" | Out-File $log -Append
        exit 1
    }
}

# 2. Now build with the explicit opt-in.
$success = $false
for ($attempt = 1; $attempt -le 6; $attempt++) {
    "--- gradle attempt $attempt $(Get-Date) ---" | Out-File $log -Append
    $out = & ".\gradlew.bat" ":app:bundleRelease" "-PallowDebugSigning=true" "--no-daemon" "--console=plain" 2>&1
    $out | Out-File $log -Append
    if ($out -match "BUILD SUCCESSFUL") { $success = $true; break }
    if ($out -match "Could not move temporary workspace|Couldn't move|Unable to delete") {
        $cachesRoot = Join-Path $env:USERPROFILE ".gradle\caches"
        Get-ChildItem $cachesRoot -Recurse -Directory -Filter "transforms*" -ErrorAction SilentlyContinue |
          ForEach-Object {
            Get-ChildItem $_.FullName -Directory -ErrorAction SilentlyContinue |
              Where-Object { $_.Name -match '^[a-f0-9]{32}-[a-f0-9]{8}-' } |
              ForEach-Object {
                $hash = $_.Name.Substring(0,32)
                $final = Join-Path $_.Parent.FullName $hash
                if ((Test-Path (Join-Path $_.FullName "metadata.bin")) -and -not (Test-Path $final)) {
                    try { Rename-Item -Path $_.FullName -NewName $hash -ErrorAction Stop } catch {}
                }
              }
          }
        Start-Sleep -Seconds 1
        continue
    }
    "Non-transform failure on attempt $attempt; aborting" | Out-File $log -Append
    break
}
"DONE success=$success $(Get-Date)" | Out-File $log -Append
if (-not $success) { exit 1 }
