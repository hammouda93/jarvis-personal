param(
    [switch]$SkipCompile
)

$ErrorActionPreference = "Stop"
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

Write-Host "=== Personal AI checkpoint full validation ==="
Write-Host ""

$branchRaw = (& git branch --show-current)
$branch = if ($branchRaw) {
    ($branchRaw | Out-String).Trim()
} elseif ($env:GITHUB_HEAD_REF) {
    $env:GITHUB_HEAD_REF.Trim()
} elseif ($env:GITHUB_REF_NAME) {
    $env:GITHUB_REF_NAME.Trim()
} else {
    "(detached)"
}
$headRaw = (& git log -1 --oneline)
$head = if ($headRaw) { ($headRaw | Out-String).Trim() } else { "(unknown)" }
Write-Host "Branch: $branch"
Write-Host "HEAD:   $head"
Write-Host ""

if ($branch -eq "feature/native-agent-loop-v2") {
    Write-Host "REFUSED: protected baseline must never be used for checkpoint development."
    exit 10
}

if (-not $SkipCompile) {
    Write-Host "1/2 Python syntax preflight..."
    $modules = @(
        "jarvis_agent/agent_runtime.py",
        "jarvis_agent/assistant_v3.py",
        "jarvis_agent/native_tools.py",
        "jarvis_agent/tools.py",
        "jarvis_agent/windows_app_discovery.py",
        "jarvis_agent/windows_perception.py",
        "jarvis_agent/perception_router.py",
        "jarvis_agent/screen_vision.py",
        "jarvis_agent/cua_driver_bridge.py",
        "jarvis_agent/browser_adapter.py",
        "jarvis_agent/background_web_research.py",
        "jarvis_agent/shadow_kernel_runtime.py",
        "jarvis_agent/event_bus.py",
        "jarvis_agent/event_journal.py"
    )
    & python -m py_compile @modules
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Syntax preflight FAILED."
        exit $LASTEXITCODE
    }
    Write-Host "Syntax preflight OK."
    Write-Host ""
}

Write-Host "2/2 Full checkpoint unit/integration regression..."
$tests = @(
    "tests.test_kernel_foundations",
    "tests.test_architecture_extensions",
    "tests.test_windows_perception",
    "tests.test_tools",
    "tests.test_stt",
    "tests.test_background_web_research",
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
$testExit = $LASTEXITCODE
if ($testExit -ne 0) {
    Write-Host ""
    Write-Host "Checkpoint automated validation FAILED."
    exit $testExit
}

Write-Host ""
Write-Host "Automated checkpoint validation PASSED."
Write-Host "IMPORTANT: this does NOT validate Windows live behavior."
Write-Host ""
Write-Host "Live gates still required, in this order:"
Write-Host "  1. YouTube: open -> search -> first result -> back -> close tab only"
Write-Host "  2. Cursor installer: open downloaded installer -> continue generic UI flow"
Write-Host "  3. Windows app discovery: open an installed modern app without app-specific code"
Write-Host "  4. WhatsApp: search a contact and preserve multi-step mission context"
Write-Host "  5. research_web: confirm no visible browser is opened"
Write-Host "  6. autonomous research: confirm announcement appears only when self-initiated"
Write-Host "  7. Kernel Shadow: confirm logs remain non-authoritative"
Write-Host ""
Write-Host "Do not close the checkpoint until the automated suite AND the relevant live gates pass."
exit 0
