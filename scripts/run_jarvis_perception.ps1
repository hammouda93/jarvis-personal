param(
    [string]$LogPath = "",
    [string]$VisionModel = "gemma3:latest"
)

$ErrorActionPreference = "Stop"

$driver = Get-Command cua-driver -ErrorAction SilentlyContinue
if (-not $driver) {
    Write-Host ""
    Write-Host "Cua Driver n'est pas installé ou n'est pas dans PATH."
    Write-Host "Jarvis n'a pas été lancé."
    exit 2
}

$ollama = Get-Command ollama -ErrorAction SilentlyContinue
if (-not $ollama) {
    Write-Host ""
    Write-Host "Ollama n'est pas installé ou n'est pas dans PATH."
    Write-Host "La perception visuelle locale ne peut pas être activée."
    exit 3
}

$modelList = (& $ollama.Source list 2>$null | Out-String)
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "Ollama n'est pas joignable. Démarrez Ollama avant Jarvis."
    exit 4
}

$modelStem = ($VisionModel -split ":")[0]
if ($modelList -notmatch [regex]::Escape($modelStem)) {
    Write-Host ""
    Write-Host "Le modèle vision '$VisionModel' n'est pas installé."
    Write-Host "Installez-le avec: ollama pull $VisionModel"
    Write-Host "Jarvis n'a pas été lancé afin de ne pas retomber sur une vision absente."
    exit 5
}

$env:JARVIS_KERNEL_SHADOW_ENABLED = "1"
$env:JARVIS_COMPATIBILITY_BASELINE = "0"
$env:JARVIS_CUA_DRIVER_ENABLED = "1"

# Structured perception stays first. Vision is a local fallback only when
# UIA/Cua declares semantic coverage insufficient.
$env:JARVIS_VISION_ENABLED = "1"
$env:JARVIS_VISION_MODEL = $VisionModel
$env:JARVIS_VISION_LOCAL_ONLY = "1"
$env:JARVIS_VISION_ACTIONS_ENABLED = "1"
$env:JARVIS_VISION_MIN_CONFIDENCE = "0.82"
$env:JARVIS_VISION_SAVE_EVIDENCE = "0"

# Keep unrelated experimental systems disabled on this validation branch.
$env:JARVIS_OPERATIONAL_LEARNING_ENABLED = "0"
$env:JARVIS_STRICT_PROOF_ENABLED = "0"
$env:JARVIS_FOCUSED_TYPING_FALLBACK_ENABLED = "0"

Write-Host "Jarvis Visual Perception V1 - UIA/Cua first, local vision fallback"
Write-Host "  cua_driver=$($driver.Source)"
Write-Host "  vision_model=$VisionModel"
Write-Host "  vision_local_only=1"
Write-Host "  vision_actions=1"
Write-Host "  visual_min_confidence=0.82"
Write-Host "  learning=0 strict_proof=0 focused_typing=0"
Write-Host "  provider_order=UIA -> Cua -> local vision when semantic coverage is insufficient"

& "$PSScriptRoot\run_jarvis_logged.ps1" -LogPath $LogPath
exit $LASTEXITCODE
