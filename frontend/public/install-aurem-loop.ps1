# Aurem Loop one-line installer (for developers).
#
#   powershell -NoProfile -ExecutionPolicy Bypass -c "irm https://auremcto.com/install-aurem-loop.ps1 | iex"
#
# What it does:
#   [1/3] downloads the v0.8.0 release zip
#   [2/3] verifies its SHA-256 against the published release value
#   [3/3] extracts it and runs the release's own install.ps1
#
# The release installer then does the real work: copies the harness
# files (hash-verified), never overwrites settings.json / identity.json,
# creates the Start-menu shortcut and the per-user scheduled task
# AuremLoop. It ASKS before downloading the model — never silent.
# Ollama is NEVER auto-installed — by design, message only.
#
# HOSTING (settled by the Emergent agent, Nov 16 — deployment still owed):
# this file is served as a static file from frontend/public (same
# mechanism as /llms.txt), and the zip comes from the new public
# endpoint GET /api/harness/download-public/{version} (rate-limited
# 5/min/IP; 404s if the version is not the latest published release,
# 503 if the artifact is missing). The old default below —
# https://auremcto.com/downloads/aurem-loop-0.8.0.zip — was WRONG:
# any URL outside /api/* falls through to the SPA shell on this
# domain, so that path could never serve a zip. The endpoint default
# below is the correct one. Until the deployment lands, download
# this script and run it with -ZipUrl pointing at a URL that serves
# the zip.
#
# GATED DOWNLOAD (optional): if the zip is served only through the
# signed-in download endpoint, pass your ORA token and the script
# downloads through it instead of the public URL:
#   .\install-aurem-loop.ps1 -Token "<your ORA token>"
# (A personalized one-line command with a short-lived token is a
# server step — the website can render it for signed-in users.)
#
# GATE NOTE: this script is public by nature — anyone who has the
# command can install without an account. The sign-in gate applies
# to the website download button, not to this command. That is the
# trade-off of a one-line installer, and it is why the .exe installer
# stays the default for most users.

[CmdletBinding()]
param(
    [string]$ZipUrl = "https://auremcto.com/api/harness/download-public/0.8.0",
    [string]$Token = "",
    [string]$InstallDir = "",
    [switch]$NoTask,
    [switch]$WhatIf
)
$ErrorActionPreference = "Stop"

$Version = "0.8.0"
$ExpectedSha256 = "998abc0d46d9acc47de186519d211bf58fff9890ebed85476b58ce724d914543"
$DownloadEndpoint = "https://auremcto.com/api/aurem-dev/harness/download"

$zipPath = Join-Path $env:TEMP "aurem-loop-0.8.0.zip"
$extractDir = Join-Path $env:TEMP "aurem-loop-0.8.0"

if ($WhatIf) {
    if ($Token) {
        Write-Output "WHATIF: would download the $Version release zip through the gated endpoint (token given), verify its SHA-256, extract it, and run its install.ps1."
    } else {
        Write-Output "WHATIF: would download the $Version release zip from $ZipUrl, verify its SHA-256, extract it, and run its install.ps1."
    }
    exit 0
}

# [1/3] Download the release zip.
Write-Output "[1/3] Downloading Aurem Loop $Version..."
if ($Token) {
    Invoke-WebRequest -UseBasicParsing -Method Post -Uri $DownloadEndpoint `
        -Headers @{ Authorization = "Bearer $Token" } -OutFile $zipPath
} else {
    Invoke-WebRequest -UseBasicParsing -Uri $ZipUrl -OutFile $zipPath
}
Write-Output "[1/3] Downloaded to $zipPath"

# [2/3] Verify the SHA-256 against the published release value.
Write-Output "[2/3] Checking SHA-256..."
$actualSha = (Get-FileHash $zipPath -Algorithm SHA256).Hash.ToLower()
if ($actualSha -ne $ExpectedSha256.ToLower()) {
    Write-Output "SHA-256 MISMATCH — the download is not the published $Version release."
    Write-Output "Expected: $ExpectedSha256"
    Write-Output "Got:      $actualSha"
    Write-Output "Delete $zipPath and try again. If it still mismatches, do not install it."
    exit 1
}
Write-Output "[2/3] SHA-256 matches the published release."

# [3/3] Extract and run the release's own installer.
Write-Output "[3/3] Extracting and running the installer..."
if (Test-Path $extractDir) {
    Remove-Item $extractDir -Recurse -Force
}
Expand-Archive -Force -Path $zipPath -DestinationPath $extractDir
$releaseInstaller = Join-Path $extractDir "install.ps1"
if (-not (Test-Path $releaseInstaller)) {
    Write-Output "install.ps1 was not found inside the release zip — the download is damaged."
    exit 1
}
$installArgs = @()
if ($InstallDir) { $installArgs += "-InstallDir"; $installArgs += $InstallDir }
if ($NoTask) { $installArgs += "-NoTask" }
& $releaseInstaller @installArgs
if ($LASTEXITCODE -ne 0) {
    Write-Output "The release installer failed (exit $LASTEXITCODE). Your settings and projects were not touched."
    Write-Output "The downloaded files are still in $extractDir if you need them."
    exit $LASTEXITCODE
}

Remove-Item $zipPath -Force -ErrorAction SilentlyContinue
Remove-Item $extractDir -Recurse -Force -ErrorAction SilentlyContinue
Write-Output "[3/3] Done."
Write-Output "Start the front screen with: schtasks /run /tn AuremLoop"
Write-Output "Then open the sign-in link from the Start menu (Aurem Loop) or run: python `$env:USERPROFILE\aurem-loop\loop.py link --open"
