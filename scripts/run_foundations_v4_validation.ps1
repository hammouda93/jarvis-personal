param([string]$PythonExe = 'python', [string]$NodeExe = 'node', [switch]$NewOnly)
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
Push-Location $root
try {
    $extra = @()
    if ($NewOnly) { $extra += '--new-only' }
    & $PythonExe scripts/run_foundations_v4_validation.py --node $NodeExe @extra
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & git diff --exit-code 500453228d9e00ae7a820cf60d0a1d73344c61ca -- jarvis_agent/windows_perception.py jarvis_agent/tools.py jarvis_agent/screen_vision.py jarvis_agent/mission.py jarvis_agent/native_tools.py jarvis_agent/browser_adapter.py jarvis_agent/memory.py
    if ($LASTEXITCODE -ne 0) { throw 'Protected installer/desktop primitives changed.' }
    Write-Host 'Automated checks passed. Windows live acceptance remains required.'
} finally { Pop-Location }
