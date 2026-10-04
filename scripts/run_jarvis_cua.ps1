param(
    [string]$LogPath = ""
)

$ErrorActionPreference = "Stop"

$driver = Get-Command cua-driver -ErrorAction SilentlyContinue
if (-not $driver) {
    Write-Host ""
    Write-Host "Cua Driver n'est pas installé ou n'est pas dans PATH."
    Write-Host "Jarvis n'a pas été lancé."
    exit 2
}

$env:JARVIS_KERNEL_SHADOW_ENABLED = "1"
$env:JARVIS_COMPATIBILITY_BASELINE = "1"
$env:JARVIS_CUA_DRIVER_ENABLED = "1"

# This validation isolates structured Windows Computer Use.
$env:JARVIS_VISION_ENABLED = "0"
$env:JARVIS_VISION_ACTIONS_ENABLED = "0"
$env:JARVIS_OPERATIONAL_LEARNING_ENABLED = "0"
$env:JARVIS_STRICT_PROOF_ENABLED = "0"

Write-Host "Jarvis UI V2 - structured Computer Use recovery"
Write-Host "  cua_driver=$($driver.Source)"
Write-Host "  compatibility_baseline=1"
Write-Host "  vision=0"
Write-Host "  provider_order=existing fast Windows path -> Cua Driver when useful"

& "$PSScriptRoot\run_jarvis_logged.ps1" -LogPath $LogPath
exit $LASTEXITCODE
