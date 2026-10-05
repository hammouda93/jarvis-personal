param(
    [string]$PythonExe = ".\\.venv\\Scripts\\python.exe",
    [switch]$Grounding
)

$ErrorActionPreference = "Stop"
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$previousPythonPath = $env:PYTHONPATH
$previousPythonHome = $env:PYTHONHOME
Push-Location $root
try {
    # Never let Codex/test dependency overlays satisfy live requirements.
    Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
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

    Write-Host "Repairing Personal AI live environment"
    Write-Host "  python=$python"
    Write-Host ""

    & $python -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    & $python -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    if ($Grounding) {
        & $python -m pip install -r requirements-grounding.txt
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }

    if ($env:OS -eq "Windows_NT") {
        # A normal requirements install does not necessarily repair an
        # incomplete pywin32 DLL deployment, so force-refresh the wheel.
        & $python -m pip install --upgrade --force-reinstall "pywin32>=306"
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }

    Write-Host ""
    Write-Host "Running dependency preflight..."
    $preflightArgs = @("-m", "jarvis_agent.runtime_preflight")
    if ($Grounding) {
        $preflightArgs += "--require-grounding"
    }
    & $python @preflightArgs
    exit $LASTEXITCODE
}
finally {
    if ($null -eq $previousPythonPath) {
        Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    } else {
        $env:PYTHONPATH = $previousPythonPath
    }
    if ($null -eq $previousPythonHome) {
        Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
    } else {
        $env:PYTHONHOME = $previousPythonHome
    }
    Pop-Location
}
