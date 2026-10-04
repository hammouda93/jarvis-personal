param(
    [string]$LogPath = "",
    [int]$CdpPort = 9222
)

$ErrorActionPreference = "Stop"

$driver = Get-Command cua-driver -ErrorAction SilentlyContinue
if (-not $driver) { Write-Host "Cua Driver introuvable."; exit 2 }
& python -c "import playwright; print('ok')" 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installez le backend navigateur: python -m pip install -r requirements-browser.txt"
    exit 3
}
$chromeCandidates = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
)
$chrome = $chromeCandidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $chrome) { Write-Host "Google Chrome introuvable."; exit 4 }
$browserProfile = Join-Path $env:LOCALAPPDATA "PersonalJarvis\BrowserProfile"
New-Item -ItemType Directory -Force -Path $browserProfile | Out-Null
$endpoint = "http://127.0.0.1:$CdpPort"
$debugJson = "$endpoint/json/version"
$ready = $false
try { Invoke-RestMethod -Uri $debugJson -TimeoutSec 1 | Out-Null; $ready = $true } catch {}
if (-not $ready) {
    Start-Process -FilePath $chrome -ArgumentList @(
        "--remote-debugging-port=$CdpPort",
        "--user-data-dir=$browserProfile",
        "--force-renderer-accessibility=complete",
        "about:blank"
    )
    for ($i=0; $i -lt 20; $i++) {
        Start-Sleep -Milliseconds 250
        try { Invoke-RestMethod -Uri $debugJson -TimeoutSec 1 | Out-Null; $ready = $true; break } catch {}
    }
}
if (-not $ready) { Write-Host "Chrome CDP non joignable sur $endpoint"; exit 5 }
$env:JARVIS_KERNEL_SHADOW_ENABLED = "1"
$env:JARVIS_COMPATIBILITY_BASELINE = "0"
$env:JARVIS_CUA_DRIVER_ENABLED = "1"
$env:JARVIS_BROWSER_ENABLED = "1"
$env:JARVIS_BROWSER_CDP_URL = $endpoint
$env:JARVIS_BROWSER_CDP_PORT = "$CdpPort"
$env:JARVIS_BROWSER_PROFILE_DIR = $browserProfile
$env:JARVIS_VISION_ENABLED = "1"
$env:JARVIS_VISION_LOCAL_ONLY = "1"
$env:JARVIS_VISION_ACTIONS_ENABLED = "1"
$env:JARVIS_OPERATIONAL_LEARNING_ENABLED = "0"
$env:JARVIS_STRICT_PROOF_ENABLED = "0"
$env:JARVIS_FOCUSED_TYPING_FALLBACK_ENABLED = "0"
Write-Host "Personal Agent Runtime Fusion V3"
Write-Host "  browser=DOM/CDP $endpoint"
Write-Host "  windows=UIA -> Cua -> vision fallback"
& "$PSScriptRoot\run_jarvis_logged.ps1" -LogPath $LogPath
exit $LASTEXITCODE
