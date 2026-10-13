param(
    [string]$PythonExe = 'C:\jv312\Scripts\python.exe',
    [string]$EnvPath = '',
    [string]$BrowserBridgeConfig = '',
    [switch]$CheckOnly
)

$ErrorActionPreference = 'Stop'
$p0Root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$p0Python = (Resolve-Path -LiteralPath $PythonExe).Path
if (-not $EnvPath) {
    $EnvPath = Join-Path $p0Root '.env'
    if (-not (Test-Path -LiteralPath $EnvPath)) {
        $p0Common = & git -C $p0Root rev-parse --path-format=absolute --git-common-dir
        if ($LASTEXITCODE -ne 0) { throw 'Specify -EnvPath with the existing Jarvis .env file.' }
        $EnvPath = Join-Path (Split-Path -Parent $p0Common) '.env'
    }
}
$p0EnvFile = (Resolve-Path -LiteralPath $EnvPath).Path
if (-not $BrowserBridgeConfig) {
    $BrowserBridgeConfig = Join-Path $env:LOCALAPPDATA 'JarvisPersonal\browser-bridge\bridge.json'
}
$p0Bridge = [IO.Path]::GetFullPath($BrowserBridgeConfig)

$p0Bootstrap = @'
import os, runpy, sys
from pathlib import Path
from dotenv import load_dotenv
root, env_file, bridge, check_only = sys.argv[1:]
os.chdir(root)
sys.path.insert(0, root)
load_dotenv(env_file, override=False)
# This explicit acceptance profile applies only to this child process.
os.environ.update({
    "JARVIS_MEMORY_CORE_ENABLED": "1",
    "JARVIS_SEMANTIC_MEMORY_V5_ENABLED": "1",
    "JARVIS_MEMORY_AGENT_TOOLS_ENABLED": "1",
    "JARVIS_MEMORY_DIRECT_AGENT_ENABLED": "1",
    "JARVIS_MEMORY_SCOPE_GUARD_ENABLED": "1",
    "JARVIS_RUNTIME_CONVERGENCE_ENABLED": "1",
    "JARVIS_BROWSER_CORE_ENABLED": "1",
    "JARVIS_BROWSER_ENABLED": "0",
    "JARVIS_BROWSER_BRIDGE_CONFIG": bridge,
    "PYTHONIOENCODING": "utf-8",
    "PYTHONUTF8": "1",
})
sys.argv = ["runtime_preflight"]
from jarvis_agent.runtime_preflight import main as preflight
if preflight():
    raise SystemExit("Runtime dependencies need repair; see runtime_preflight output.")
print("P0 profile: direct SQLite memory, durable missions, normal-profile Chrome bridge.")
print("Existing data/provider preferences retained. No keys printed or configuration copied.")
if check_only == "1":
    print("CHECK ONLY: launcher dependencies validated; UI, browser connection and models not started.")
    raise SystemExit(0)
if not Path(bridge).is_file():
    raise SystemExit("Browser bridge configuration missing; follow docs/P0_WINDOWS_ACCEPTANCE.md.")
from jarvis_agent.browser_bridge_doctor import main as bridge_doctor
sys.argv = ["browser_bridge_doctor", "--config", bridge]
if bridge_doctor():
    raise SystemExit("Connect the P0 extension in normal Chrome before launching Jarvis.")
sys.argv = ["run_jarvis.py"]
runpy.run_path(str(Path(root) / "run_jarvis.py"), run_name="__main__")
'@

$p0PreviousPath = $env:PYTHONPATH
$p0PreviousHome = $env:PYTHONHOME
$p0PreviousEncoding = $env:PYTHONIOENCODING
$p0PreviousUtf8 = $env:PYTHONUTF8
Push-Location $p0Root
try {
    Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
    $env:PYTHONIOENCODING = 'utf-8'
    $env:PYTHONUTF8 = '1'
    $p0Utf8 = New-Object System.Text.UTF8Encoding($false)
    [Console]::OutputEncoding = $p0Utf8
    $OutputEncoding = $p0Utf8
    $p0Check = $(if ($CheckOnly) { '1' } else { '0' })
    if ($CheckOnly) {
        & $p0Python -u -c $p0Bootstrap $p0Root $p0EnvFile $p0Bridge $p0Check
    } else {
        New-Item -ItemType Directory -Path (Join-Path $p0Root '.cache') -Force | Out-Null
        $p0Log = Join-Path $p0Root ('.cache\p0_live_' + (Get-Date -Format 'yyyyMMdd_HHmmss') + '.log')
        Write-Host "P0 live log: $p0Log"
        # Native stderr is diagnostic output, not a PowerShell terminating error.
        $ErrorActionPreference = 'Continue'
        & $p0Python -u -c $p0Bootstrap $p0Root $p0EnvFile $p0Bridge $p0Check 2>&1 | Tee-Object -FilePath $p0Log
        $ErrorActionPreference = 'Stop'
    }
    $p0ExitCode = $LASTEXITCODE
}
finally {
    foreach ($p0Item in @(@('PYTHONPATH', $p0PreviousPath), @('PYTHONHOME', $p0PreviousHome),
                         @('PYTHONIOENCODING', $p0PreviousEncoding), @('PYTHONUTF8', $p0PreviousUtf8))) {
        if ($null -eq $p0Item[1]) {
            Remove-Item ('Env:' + $p0Item[0]) -ErrorAction SilentlyContinue
        } else {
            Set-Item ('Env:' + $p0Item[0]) $p0Item[1]
        }
    }
    Pop-Location
}
exit $p0ExitCode
