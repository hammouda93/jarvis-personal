param()

$ErrorActionPreference = "Stop"
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

Write-Host "=== Personal AI Runtime Foundation Stability V2 ==="
$branch = (& git branch --show-current).Trim()
$head = (& git log -1 --oneline).Trim()
Write-Host "Branch: $branch"
Write-Host "HEAD:   $head"
Write-Host ""

if ($branch -eq "feature/native-agent-loop-v2") {
    Write-Host "REFUSED: protected baseline must never be used for development validation."
    exit 10
}

Write-Host "1/2 Compile runtime modules..."
$modules = @(
    "jarvis_agent/agent_runtime.py",
    "jarvis_agent/assistant_v3.py",
    "jarvis_agent/native_tools.py",
    "jarvis_agent/screen_vision.py",
    "jarvis_agent/tools.py",
    "jarvis_agent/windows_perception.py",
    "jarvis_agent/windows_app_discovery.py",
    "jarvis_agent/browser_adapter.py",
    "jarvis_agent/background_web_research.py",
    "jarvis_agent/shadow_kernel_runtime.py"
)
& python -m py_compile @modules
if ($LASTEXITCODE -ne 0) {
    Write-Host "Compile FAILED."
    exit $LASTEXITCODE
}
Write-Host "Compile OK."
Write-Host ""

Write-Host "2/2 Run full regression set..."
$tests = @(
    "tests.test_kernel_foundations",
    "tests.test_architecture_extensions",
    "tests.test_windows_perception",
    "tests.test_tools",
    "tests.test_stt",
    "tests.test_background_web_research",
    "tests.test_memory",
    "tests.test_native_tools",
    "tests.test_assistant_v3",
    "tests.test_ui_logging",
    "tests.test_agent_runtime",
    "tests.test_browser_dom_contract",
    "tests.test_cua_driver_bridge",
    "tests.test_perception_router",
    "tests.test_screen_vision",
    "tests.test_text_input_mode_v3"
)
& python -m unittest @tests -v
$code = $LASTEXITCODE
if ($code -ne 0) {
    Write-Host ""
    Write-Host "Runtime foundation validation FAILED."
    exit $code
}

Write-Host ""
Write-Host "Automated runtime foundation validation PASSED."
Write-Host "Live Windows gates remain mandatory before merge."
exit 0
