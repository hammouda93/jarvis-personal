param(
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-p]{32}$')][string]$ExtensionId,
    [Parameter(Mandatory=$true)][string]$PythonExe,
    [ValidateRange(1024,65535)][int]$Port = 47653,
    [string]$InstallDirectory = (Join-Path $env:LOCALAPPDATA 'JarvisPersonal\browser-bridge')
)
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$pythonPath = (Resolve-Path -LiteralPath $PythonExe).Path
$installPath = [IO.Path]::GetFullPath($InstallDirectory)
New-Item -ItemType Directory -Path $installPath -Force | Out-Null
# Restrict pairing secret to the current Windows user and SYSTEM.
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
& icacls.exe $installPath '/inheritance:r' '/grant:r' "*$($sid):(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Cannot protect browser pairing configuration.' }
$configPath = Join-Path $installPath 'bridge.json'
$launcherPath = Join-Path $installPath 'native_host.cmd'
$manifestPath = Join-Path $installPath 'host.json'
$token = [byte[]]::new(32)
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
try { $rng.GetBytes($token) } finally { $rng.Dispose() }
$utf8 = New-Object System.Text.UTF8Encoding($false)
$config = @{port=$Port; token=[Convert]::ToBase64String($token); extension_id=$ExtensionId}
[IO.File]::WriteAllText($configPath, ($config | ConvertTo-Json), $utf8)
if ($root.Contains('%') -or $pythonPath.Contains('%') -or $installPath.Contains('%')) { throw 'Percent characters in native host paths are unsupported.' }
$launcher = "@echo off`r`nsetlocal DisableDelayedExpansion`r`nchcp 65001 >nul`r`ncd /d `"$root`" || exit /b 1`r`n`"$pythonPath`" -m jarvis_agent.browser_native_host --config `"$configPath`" %*`r`n"
[IO.File]::WriteAllText($launcherPath, $launcher, $utf8)
$manifest = @{name='com.jarvis.personal_ai_browser_bridge'; description='Personal AI Browser Bridge';
    path=$launcherPath; type='stdio'; allowed_origins=@("chrome-extension://$ExtensionId/")}
[IO.File]::WriteAllText($manifestPath, ($manifest | ConvertTo-Json), $utf8)
$registryPath = 'HKCU:\Software\Google\Chrome\NativeMessagingHosts\com.jarvis.personal_ai_browser_bridge'
New-Item -Path $registryPath -Force | Out-Null
Set-Item -Path $registryPath -Value $manifestPath
Write-Host "Registered for extension $ExtensionId in the current user's Chrome."
Write-Host "Click the extension's action button to connect."
Write-Host "JARVIS_BROWSER_BRIDGE_CONFIG=$configPath"
