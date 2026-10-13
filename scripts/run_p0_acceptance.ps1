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

# Pass a file and ordinary arguments. Windows PowerShell 5.1 strips quotes
# from embedded Python source supplied to -c through its native argv boundary.
$p0Bootstrap = Join-Path $p0Root 'scripts\p0_acceptance.py'
$p0Arguments = @('-u', $p0Bootstrap, '--env-path', $p0EnvFile, '--bridge-config', $p0Bridge)
if ($CheckOnly) { $p0Arguments += '--check-only' }

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
    if ($CheckOnly) {
        & $p0Python @p0Arguments
    } else {
        New-Item -ItemType Directory -Path (Join-Path $p0Root '.cache') -Force | Out-Null
        $p0Log = Join-Path $p0Root ('.cache\p0_live_' + (Get-Date -Format 'yyyyMMdd_HHmmss') + '.log')
        Write-Host "P0 live log: $p0Log"
        # Native stderr is diagnostic output, not a PowerShell terminating error.
        $ErrorActionPreference = 'Continue'
        & $p0Python @p0Arguments 2>&1 | Tee-Object -FilePath $p0Log
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
