param(
    [string]$LogPath = "",
    [switch]$StructuredOnly,
    [switch]$EnableVisualActions,
    [switch]$EnableGrounding,
    [string]$VisionModel = "",
    [string]$GroundingModel = "",
    [string]$CdpUrl = ""
)

$ErrorActionPreference = "Stop"

$JarvisPreviousEnvironment = @{}
foreach ($key in @("JARVIS_COMPATIBILITY_BASELINE", "JARVIS_COMPUTER_USE_ENABLED", "JARVIS_AGENT_PROVIDER", "JARVIS_STRUCTURED_TRACING_ENABLED", "JARVIS_STRICT_PROOF_ENABLED", "JARVIS_OPERATIONAL_LEARNING_ENABLED", "JARVIS_AGENT_MAX_TOOL_ROUNDS", "JARVIS_VISION_LOCAL_ONLY", "JARVIS_VISION_SCHEMA_ENABLED", "JARVIS_VISION_NUM_PREDICT", "JARVIS_VISION_ENABLED", "JARVIS_VISION_ACTIONS_ENABLED", "JARVIS_VISION_GROUNDING_ENABLED", "JARVIS_VISION_MODEL", "JARVIS_VISION_GROUNDING_MODEL", "JARVIS_CUA_DRIVER_ENABLED", "JARVIS_BROWSER_ENABLED", "JARVIS_BROWSER_CDP_URL")) {
    $JarvisPreviousEnvironment[$key] = [Environment]::GetEnvironmentVariable($key, "Process")
}
$JarvisComputerUseExitCode = 0
try {
    $env:JARVIS_COMPATIBILITY_BASELINE = "0"
    $env:JARVIS_COMPUTER_USE_ENABLED = "1"
    $env:JARVIS_AGENT_PROVIDER = "cerebras"
    $env:JARVIS_STRUCTURED_TRACING_ENABLED = "1"
    $env:JARVIS_STRICT_PROOF_ENABLED = "1"
    $env:JARVIS_OPERATIONAL_LEARNING_ENABLED = "0"
    $env:JARVIS_AGENT_MAX_TOOL_ROUNDS = "24"
    $env:JARVIS_VISION_LOCAL_ONLY = "1"
    $env:JARVIS_VISION_SCHEMA_ENABLED = "1"
    $env:JARVIS_VISION_NUM_PREDICT = "1024"
    $env:JARVIS_VISION_ENABLED = $(if ($StructuredOnly) { "0" } else { "1" })
    $env:JARVIS_VISION_ACTIONS_ENABLED = $(if ($EnableVisualActions -and -not $StructuredOnly) { "1" } else { "0" })
    $env:JARVIS_VISION_GROUNDING_ENABLED = $(if (($EnableGrounding -or $GroundingModel) -and -not $StructuredOnly) { "1" } else { "0" })
    if ($VisionModel) { $env:JARVIS_VISION_MODEL = $VisionModel }
    if ($GroundingModel) { $env:JARVIS_VISION_GROUNDING_MODEL = $GroundingModel }

    # Keep an explicitly configured driver. Enable a PATH driver automatically.
    $driver = Get-Command cua-driver -ErrorAction SilentlyContinue
    if ($driver) { $env:JARVIS_CUA_DRIVER_ENABLED = "1" }

    $env:JARVIS_BROWSER_ENABLED = $(if ($CdpUrl) { "1" } else { "0" })
    if ($CdpUrl) {
        python -c "import playwright" 2>$null
        if ($LASTEXITCODE -ne 0) {
            throw "Install the optional adapter first: python -m pip install -r requirements-browser.txt"
        }
        $env:JARVIS_BROWSER_CDP_URL = $CdpUrl
    }

    Write-Host "Personal AI Agent - Computer Use Engine V2"
    Write-Host "  planner=Cerebras; exact UIA/Cua paths retained"
    Write-Host "  vision=$env:JARVIS_VISION_ENABLED; visual_actions=$env:JARVIS_VISION_ACTIONS_ENABLED"
    Write-Host "  target_grounder=$env:JARVIS_VISION_GROUNDING_ENABLED; browser=$env:JARVIS_BROWSER_ENABLED"
    Write-Host "  structured_tracing=1; completion requires observable proof"

    & "$PSScriptRoot\run_jarvis_logged.ps1" -LogPath $LogPath
     $JarvisComputerUseExitCode = $LASTEXITCODE
} finally {
    foreach ($key in $JarvisPreviousEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($key, $JarvisPreviousEnvironment[$key], "Process")
    }
}
exit $JarvisComputerUseExitCode
