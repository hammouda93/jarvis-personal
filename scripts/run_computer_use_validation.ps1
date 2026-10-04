param([switch]$IncludeBrowser)
$ErrorActionPreference = "Stop"

$env:JARVIS_COMPATIBILITY_BASELINE = "1"
$env:JARVIS_COMPUTER_USE_ENABLED = "0"
$env:JARVIS_CUA_DRIVER_ENABLED = "0"
$env:JARVIS_VISION_ENABLED = "0"
$env:JARVIS_VISION_ACTIONS_ENABLED = "0"
$env:JARVIS_STRUCTURED_TRACING_ENABLED = "0"

python -m compileall -q jarvis_agent benchmarks tests
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
python -m unittest tests.test_cua_driver_bridge tests.test_windows_perception tests.test_native_tools tests.test_agent_runtime tests.test_screen_vision tests.test_perception_router tests.test_computer_use_engine tests.test_kernel_foundations tests.test_architecture_extensions tests.test_assistant_v3 tests.test_ui_logging -v
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
if ($IncludeBrowser) {
    python -m unittest tests.test_browser_adapter -v
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
exit 0
