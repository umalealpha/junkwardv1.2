# qa-token.ps1 — refresh %USERPROFILE%\.omni-qa-token from the read-only QA key.
#
# Windows twin of qa-token.sh. Omni's session tokens die after 15 hours, so the
# checker tops itself up before every run. The token is a READ-ONLY identity:
# the server refuses it on anything except reading, so it can open any page and
# change nothing.
#
#   powershell -ExecutionPolicy Bypass -File qa-token.ps1          # refresh if stale
#   powershell -ExecutionPolicy Bypass -File qa-token.ps1 -Force   # refresh always
#
# Never prints the key or the token.
param([switch]$Force)
$ErrorActionPreference = 'Stop'

$KeyFile = Join-Path $env:USERPROFILE '.omni-qa-key'
$TokFile = Join-Path $env:USERPROFILE '.omni-qa-token'
$Base    = if ($env:OMNI_BASE) { $env:OMNI_BASE } else { 'https://omni.alphadirect.co.bw' }

if (-not (Test-Path $KeyFile)) { Write-Error "NO_KEY: $KeyFile is missing — run SETUP.bat first"; exit 2 }

# A token younger than 12h is still inside the 15h server ceiling.
if (-not $Force -and (Test-Path $TokFile)) {
  $age = (Get-Date) - (Get-Item $TokFile).LastWriteTime
  if ($age.TotalMinutes -lt 720) { Write-Host "QA token still fresh — not refreshed"; exit 0 }
}

$key = (Get-Content $KeyFile -Raw).Trim()
# Cloudflare blocks the default PowerShell user-agent (error 1010), so send a browser one.
$ua  = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36'
try {
  $resp = Invoke-RestMethod -Method Post -Uri "$Base/api/v1/auth/qa-view/" `
            -ContentType 'application/json' -Headers @{ 'User-Agent' = $ua } `
            -Body (@{ key = $key } | ConvertTo-Json) -TimeoutSec 60
} catch {
  Write-Error "QA_TOKEN_FAILED: $($_.Exception.Message)"; exit 3
}
if (-not $resp.token) { Write-Error 'QA_TOKEN_FAILED: no token in the reply'; exit 3 }
Set-Content -Path $TokFile -Value $resp.token -NoNewline -Encoding ascii
Write-Host "QA token refreshed ($($resp.token.Length) chars)"
