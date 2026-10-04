param(
    [string]$LogPath = "",
    [switch]$EnableVisualActions
)

$ErrorActionPreference = "Stop"

$env:JARVIS_KERNEL_SHADOW_ENABLED = "1"
$env:JARVIS_COMPATIBILITY_BASELINE = "0"
$env:JARVIS_VISION_ENABLED = "1"
$env:JARVIS_VISION_LOCAL_ONLY = "1"
$env:JARVIS_STRICT_PROOF_ENABLED = "1"
$env:JARVIS_OPERATIONAL_LEARNING_ENABLED = "0"

if ($EnableVisualActions) {
    $env:JARVIS_VISION_ACTIONS_ENABLED = "1"
} else {
    $env:JARVIS_VISION_ACTIONS_ENABLED = "0"
}

Write-Host "Jarvis hybrid perception mode"
Write-Host "  baseline=$env:JARVIS_COMPATIBILITY_BASELINE"
Write-Host "  vision=$env:JARVIS_VISION_ENABLED"
Write-Host "  visual_actions=$env:JARVIS_VISION_ACTIONS_ENABLED"
Write-Host "  strict_proof=$env:JARVIS_STRICT_PROOF_ENABLED"

& "$PSScriptRoot\run_jarvis_logged.ps1" -LogPath $LogPath
exit $LASTEXITCODE
