param()

$ErrorActionPreference = "Stop"

$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

Write-Host "=== Jarvis vision validation ==="
Write-Host ""

Write-Host "1/2 Historical baseline regression..."
powershell -ExecutionPolicy Bypass -File .\scripts\run_baseline_regression.ps1
if ($LASTEXITCODE -ne 0) {
    Write-Host "Baseline regression FAILED. Vision tests not started."
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "2/2 Targeted UIA + local vision tests..."

$env:JARVIS_COMPATIBILITY_BASELINE = "0"
$env:JARVIS_OPERATIONAL_LEARNING_ENABLED = "0"
$env:JARVIS_VISION_ENABLED = "1"
$env:JARVIS_VISION_ACTIONS_ENABLED = "1"
$env:JARVIS_FOCUSED_TYPING_FALLBACK_ENABLED = "0"
$env:JARVIS_STRICT_PROOF_ENABLED = "0"

$tests = @(
    "tests.test_screen_vision",
    "tests.test_windows_perception.WindowsPerceptionTests.test_dialog_snapshot_keeps_lower_writable_field_and_save_button",
    "tests.test_native_tools.NativeToolRegistryTests.test_click_visual_target_routes_to_local_visual_action",
    "tests.test_native_tools.NativeToolRegistryTests.test_write_visual_target_routes_to_local_visual_action",
    "tests.test_agent_runtime.AgentRuntimeTests.test_compact_inspection_preserves_capability_refs",
    "tests.test_agent_runtime.AgentRuntimeTests.test_groq_vision_mode_exposes_observation_but_not_visual_click",
    "tests.test_agent_runtime.AgentRuntimeTests.test_groq_visual_write_satisfies_write_goal_after_verification",
    "tests.test_agent_runtime.AgentRuntimeTests.test_groq_visual_click_requires_after_state_verification",
    "tests.test_agent_runtime.AgentRuntimeTests.test_groq_search_submission_reuses_current_ui_instead_of_reopening_site"
)

$command = "python -m unittest " + ($tests -join " ") + " -v 2>&1"
cmd.exe /d /s /c $command
exit $LASTEXITCODE