param(
    [string]$LogPath = ""
)

$ErrorActionPreference = "Stop"

$driver = Get-Command cua-driver -ErrorAction SilentlyContinue
if (-not $driver) {
    Write-Host ""
    Write-Host "Cua Driver n'est pas installé ou n'est pas dans PATH."
    Write-Host "Jarvis n'a pas été lancé: aucun fallback silencieux n'est autorisé dans ce mode."
    Write-Host ""
    Write-Host "Après installation officielle, vérifie avec:"
    Write-Host "  cua-driver --version"
    Write-Host "  cua-driver doctor"
    exit 2
}

Write-Host "Jarvis OpenClaw-style Computer Use mode"
Write-Host "  cua_driver=$($driver.Source)"
Write-Host "  compatibility_baseline=1"
Write-Host "  local_vision=0"
Write-Host "  provider_order=pywinauto -> cua-driver (only when UIA is insufficient)"

$env:JARVIS_KERNEL_SHADOW_ENABLED = "1"
$env:JARVIS_COMPATIBILITY_BASELINE = "1"
$env:JARVIS_CUA_DRIVER_ENABLED = "1"

# Keep the previous experimental Ollama vision path out of this validation.
$env:JARVIS_VISION_ENABLED = "0"
$env:JARVIS_VISION_ACTIONS_ENABLED = "0"

& "$PSScriptRoot\run_jarvis_logged.ps1" -LogPath $LogPath
exit $LASTEXITCODE
