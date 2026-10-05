param(
    [string]$PythonExe = ".\.venv\Scripts\python.exe",
    [switch]$MemoryCore,
    [switch]$BrowserCore,
    [switch]$ComputerCore,
    [switch]$All,
    [string]$BrowserBridgeConfig = "",
    [ValidateRange(1,5)][int]$GroundingBudgetSeconds = 3,
    [string]$GroundingModel = "",
    [string]$GroundingEndpoint = "http://127.0.0.1:11434/api/chat"
)

$ErrorActionPreference = "Stop"
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
Push-Location $root
try {
    if ($All) {
        $MemoryCore = $true
        $BrowserCore = $true
        $ComputerCore = $true
    }

    $python = $PythonExe
    if (Test-Path -LiteralPath $python) {
        $python = (Resolve-Path -LiteralPath $python).Path
    } else {
        $command = Get-Command $python -ErrorAction SilentlyContinue
        if (-not $command) {
            throw "Python introuvable: $PythonExe"
        }
        $python = $command.Source
    }

    Write-Host "Checking live runtime dependencies..."
    & $python -m jarvis_agent.runtime_preflight
    if ($LASTEXITCODE -ne 0) {
        throw (
            "Environnement live incomplet ou incohérent. Répare-le avec: " +
            ".\\scripts\\repair_live_environment.ps1 -PythonExe `"$python`""
        )
    }
    Write-Host ""
    $env:JARVIS_MEMORY_CORE_ENABLED = $(if ($MemoryCore) { "1" } else { "0" })
    $env:JARVIS_BROWSER_CORE_ENABLED = $(if ($BrowserCore) { "1" } else { "0" })
    $env:JARVIS_COMPUTER_CORE_ENABLED = $(if ($ComputerCore) { "1" } else { "0" })

    if ($BrowserCore) {
        # The V4 Browser Core owns the real-profile browser scope. Never enable
        # the legacy Playwright/CDP profile in the same process.
        $env:JARVIS_BROWSER_ENABLED = "0"

        if (-not $BrowserBridgeConfig) {
            $BrowserBridgeConfig = Join-Path $env:LOCALAPPDATA "JarvisPersonal\browser-bridge\bridge.json"
        }
        if (-not (Test-Path -LiteralPath $BrowserBridgeConfig)) {
            throw (
                "Browser Core activé mais bridge.json introuvable: $BrowserBridgeConfig. " +
                "Charge d'abord l'extension extensions/personal-ai-browser-bridge " +
                "dans ton profil Chrome normal puis exécute scripts/install_browser_bridge.ps1."
            )
        }
        $env:JARVIS_BROWSER_BRIDGE_CONFIG = (Resolve-Path -LiteralPath $BrowserBridgeConfig).Path

        Write-Host "Checking normal-profile Chrome bridge..."
        & $python -m jarvis_agent.browser_bridge_doctor --config $env:JARVIS_BROWSER_BRIDGE_CONFIG
        if ($LASTEXITCODE -ne 0) {
            throw (
                "Browser bridge non disponible. Ouvre ton Chrome normal, " +
                "vérifie l'extension Personal AI Browser Bridge puis clique son icône."
            )
        }
    }

    if ($ComputerCore) {
        $env:JARVIS_GROUNDING_BUDGET_S = [string]$GroundingBudgetSeconds
        if ($GroundingModel) {
            $env:JARVIS_GROUNDING_MODEL = $GroundingModel
            $env:JARVIS_GROUNDING_ENDPOINT = $GroundingEndpoint
        } else {
            Remove-Item Env:JARVIS_GROUNDING_MODEL -ErrorAction SilentlyContinue
        }
    }

    Write-Host "Personal AI Foundations V4 live"
    Write-Host ("  memory_core=" + $(if ($MemoryCore) { "on" } else { "off" }))
    Write-Host ("  browser_core=" + $(if ($BrowserCore) { "on (normal Chrome profile bridge)" } else { "off" }))
    Write-Host ("  computer_core=" + $(if ($ComputerCore) { "on (UIA -> OCR -> optional model)" } else { "off" }))
    if ($BrowserCore) {
        Write-Host "  legacy_browser_cdp=off"
        Write-Host "  bridge_config=$env:JARVIS_BROWSER_BRIDGE_CONFIG"
    }
    if ($ComputerCore) {
        Write-Host "  grounding_budget_s=$env:JARVIS_GROUNDING_BUDGET_S"
        Write-Host ("  grounding_model=" + $(if ($GroundingModel) { $GroundingModel } else { "none" }))
    }
    Write-Host ""

    & $python run_jarvis.py
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
