param(
    [string]$PythonExe = 'python',
    [string]$NodeExe = 'node',
    [switch]$FullRegression,
    [switch]$LiveSemanticModel,
    [string]$SemanticProvider = 'auto',
    [string]$SemanticModel = '',
    [switch]$AllowCloudSemanticMemory
)

$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
Push-Location $root
try {
    Write-Host '=== Semantic Memory Core V5 validation ==='
    Write-Host ('Branch: ' + (& git branch --show-current).Trim())
    Write-Host ('HEAD:   ' + (& git log -1 --oneline).Trim())
    Write-Host ''

    Write-Host '1/4 Compile semantic memory modules...'
    & $PythonExe -m py_compile jarvis_agent/semantic_memory.py jarvis_agent/memory_core_store.py jarvis_agent/memory_semantic_interpreter.py jarvis_agent/semantic_memory_runtime.py jarvis_agent/semantic_memory_cli.py jarvis_agent/foundation_tools.py jarvis_agent/agent_runtime.py scripts/evaluate_semantic_memory_v5.py
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    Write-Host '2/4 Run V5 semantic invariants...'
    & $PythonExe -m unittest tests.test_semantic_memory_v5 -v
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    Write-Host '3/4 Run V4 foundation non-regressions...'
    if ($FullRegression) {
        & $PythonExe scripts/run_foundations_v4_validation.py --node $NodeExe
    } else {
        & $PythonExe scripts/run_foundations_v4_validation.py --node $NodeExe --new-only
    }
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    Write-Host '4/4 Confirm protected historical desktop/installer primitives...'
    & git diff --exit-code 500453228d9e00ae7a820cf60d0a1d73344c61ca -- jarvis_agent/windows_perception.py jarvis_agent/tools.py jarvis_agent/screen_vision.py jarvis_agent/mission.py jarvis_agent/native_tools.py jarvis_agent/browser_adapter.py jarvis_agent/memory.py
    if ($LASTEXITCODE -ne 0) {
        throw 'Protected installer/desktop primitives changed.'
    }

    if ($LiveSemanticModel) {
        Write-Host ''
        Write-Host '5/5 Run synthetic live semantic-model acceptance...'
        $evalArgs = @(
            '-m',
            'scripts.evaluate_semantic_memory_v5',
            '--provider', $SemanticProvider
        )
        if ($SemanticModel) {
            $evalArgs += @('--model', $SemanticModel)
        }
        if ($AllowCloudSemanticMemory) {
            $evalArgs += '--allow-cloud'
        }
        & $PythonExe @evalArgs
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }

    Write-Host ''
    Write-Host 'Semantic Memory V5 automated validation PASSED.'
    Write-Host 'No real user memory was used by the V5 unit suite.'
    if ($LiveSemanticModel) {
        Write-Host 'Synthetic live semantic-model acceptance PASSED.'
    } else {
        Write-Host 'Synthetic live semantic-model acceptance was not requested.'
    }
    exit 0
}
finally {
    Pop-Location
}
