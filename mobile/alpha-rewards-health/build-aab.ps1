# Build the Alpha Nexus release AAB on this Windows machine.
#
# Two machine-specific workarounds are mandatory here:
#  1. AF_UNIX sockets are disabled, so JDK 17's NIO Selector.open() throws
#     "Unable to establish loopback connection" and Gradle cannot start. The
#     patched java.base classes force the WEPoll selector instead.
#  2. Gradle's dependency-transform cache races on this filesystem
#     ("Could not move temporary workspace"); the retry loop renames the
#     pending transform dirs and tries again.

$ErrorActionPreference = "Continue"
$androidDir = Join-Path $PSScriptRoot "android"
$log = Join-Path $PSScriptRoot "build-aab.log"
"=== Build started $(Get-Date) ===" | Out-File $log

# JAVA_HOME is deliberately NOT inherited. This machine has a machine-wide JAVA_HOME
# pointing at JDK 11 (another project needs it), but AGP 8.11 / Gradle 8.14 require 17+
# and the loopback patch classes below are compiled for 17 - inheriting 11 fails with
# "class file version 61.0 ... only recognizes up to 55.0". Override deliberately with
# ALPHA_NEXUS_JAVA_HOME when building on another machine.
if ($env:ALPHA_NEXUS_JAVA_HOME) { $env:JAVA_HOME = $env:ALPHA_NEXUS_JAVA_HOME }
else { $env:JAVA_HOME = "C:\Program Files\Eclipse Adoptium\jdk-17.0.19.10-hotspot" }
if (-not $env:ANDROID_HOME) { $env:ANDROID_HOME = "$env:LOCALAPPDATA\Android\Sdk" }
if (-not $env:JDK17_PATCH)  { $env:JDK17_PATCH  = "$env:TEMP\jdk17patch\classes" }
$env:PATH = "$env:JAVA_HOME\bin;$env:PATH"
$env:ANDROID_SDK_ROOT = $env:ANDROID_HOME

$javaExe = Join-Path $env:JAVA_HOME "bin\java.exe"
if (-not (Test-Path $javaExe)) {
    "FATAL: no java.exe at $javaExe. Set ALPHA_NEXUS_JAVA_HOME to a JDK 17+." | Out-File $log -Append
    exit 1
}
$verLine = (& $javaExe -version 2>&1 | Select-Object -First 1) -join ''
if ($verLine -notmatch '"(\d+)') {
    "FATAL: cannot read the Java version from: $verLine" | Out-File $log -Append; exit 1
}
$javaMajor = [int]$Matches[1]
if ($javaMajor -lt 17) {
    "FATAL: JAVA_HOME is Java $javaMajor ($env:JAVA_HOME). Gradle 8.14 / AGP 8.11 need 17+." | Out-File $log -Append
    exit 1
}
"Java $javaMajor at $($env:JAVA_HOME)" | Out-File $log -Append

# Only needed where AF_UNIX sockets are disabled (see the comment at the top). The patch
# is version-specific, so only apply it to the JDK it was compiled for.
if ((Test-Path $env:JDK17_PATCH) -and $javaMajor -eq 17) {
    $env:JAVA_TOOL_OPTIONS = "--patch-module=java.base=$($env:JDK17_PATCH)"
} else {
    $env:JAVA_TOOL_OPTIONS = ""
    "NOTE: loopback patch not applied. Fine unless Gradle fails with 'Unable to establish loopback connection'." | Out-File $log -Append
}

Set-Location $androidDir

$cachesRoot = Join-Path $env:USERPROFILE ".gradle\caches"
function Rename-PendingTransforms {
    $renamed = 0
    $tdirs = Get-ChildItem $cachesRoot -Recurse -Directory -Filter "transforms*" -ErrorAction SilentlyContinue
    foreach ($td in $tdirs) {
        $items = Get-ChildItem $td.FullName -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match '^[a-f0-9]{32}-[a-f0-9]{8}-' }
        foreach ($dir in $items) {
            $hash = $dir.Name.Substring(0, 32)
            $finalDir = Join-Path $td.FullName $hash
            $metaFile = Join-Path $dir.FullName "metadata.bin"
            if ((Test-Path $metaFile) -and (-not (Test-Path $finalDir))) {
                try { Rename-Item -Path $dir.FullName -NewName $hash -ErrorAction Stop; $renamed++ } catch {}
            }
        }
    }
    return $renamed
}

$success = $false
for ($attempt = 1; $attempt -le 6; $attempt++) {
    "--- gradle attempt $attempt $(Get-Date) ---" | Out-File $log -Append
    $out = & ".\gradlew.bat" ":app:bundleRelease" "--no-daemon" "--console=plain" "--stacktrace" 2>&1
    $out | Out-File $log -Append
    if ($out -match "BUILD SUCCESSFUL") { $success = $true; break }
    if ($out -match "Could not move temporary workspace|Couldn't move|Unable to delete") {
        $n = Rename-PendingTransforms
        "Renamed $n pending transform dirs; retrying" | Out-File $log -Append
        Start-Sleep -Seconds 1
        continue
    }
    "Non-transform failure on attempt $attempt; aborting retries" | Out-File $log -Append
    break
}

"=== success=$success $(Get-Date) ===" | Out-File $log -Append
$aab = Join-Path $androidDir "app\build\outputs\bundle\release\app-release.aab"
if (Test-Path $aab) {
    $f = Get-Item $aab
    "AAB: {0:N1} MB, modified {1}" -f ($f.Length / 1MB), $f.LastWriteTime | Out-File $log -Append
}
"DONE success=$success" | Out-File $log -Append
if (-not $success) { exit 1 }
