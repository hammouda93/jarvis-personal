param()

$ErrorActionPreference = "Stop"

$env:JARVIS_COMPATIBILITY_BASELINE = "1"
$env:JARVIS_OPERATIONAL_LEARNING_ENABLED = "0"
$env:JARVIS_VISION_ENABLED = "0"
$env:JARVIS_STRICT_PROOF_ENABLED = "0"

Write-Host "=== Jarvis historical baseline regression ==="
Write-Host "learning=0 vision=0 strict_proof=0"
Write-Host ""

$tests = @(
    "tests.test_windows_perception",
    "tests.test_tools",
    "tests.test_native_tools",
    "tests.test_assistant_v3",
    "tests.test_ui_logging",
    "tests.test_agent_runtime.AgentRuntimeTests.test_groq_baseline_hides_new_learning_and_vision_tools",
    "tests.test_agent_runtime.AgentRuntimeTests.test_groq_repairs_write_goal_after_only_opening_application",
    "tests.test_agent_runtime.AgentRuntimeTests.test_groq_blocks_window_close_when_user_requested_tab",
    "tests.test_agent_runtime.AgentRuntimeTests.test_groq_skips_extra_inspection_when_write_self_verifies",
    "tests.test_agent_runtime.AgentRuntimeTests.test_groq_verifies_ui_after_write_before_concluding",
    "tests.test_agent_runtime.AgentRuntimeTests.test_groq_repairs_pseudo_tool_text_into_real_call"
)

$command = "python -m unittest " + ($tests -join " ") + " -v 2>&1"
cmd.exe /d /s /c $command
exit $LASTEXITCODE