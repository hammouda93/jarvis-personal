param(
    [string]$LogPath = ""
)

$ErrorActionPreference = "Stop"

$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

if ([string]::IsNullOrWhiteSpace($LogPath)) {
    $LogPath = "jarvis_live_$(Get-Date -Format 'yyyyMMdd_HHmmss').log"
}

Write-Host "Jarvis UTF-8 log: $LogPath"
python run_jarvis.py 2>&1 | Tee-Object -FilePath $LogPath
exit $LASTEXITCODE