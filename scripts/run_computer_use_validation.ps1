param([switch]$IncludeBrowser)
$ErrorActionPreference = "Stop"

$JarvisValidationEnvironment = @{}
foreach ($key in @("JARVIS_COMPATIBILITY_BASELINE", "JARVIS_COMPUTER_USE_ENABLED", "JARVIS_CUA_DRIVER_ENABLED", "JARVIS_VISION_ENABLED", "JARVIS_VISION_ACTIONS_ENABLED", "JARVIS_STRUCTURED_TRACING_ENABLED")) {
    $JarvisValidationEnvironment[$key] = [Environment]::GetEnvironmentVariable($key, "Process")
}
$JarvisValidationExitCode = 0
try {
    $env:JARVIS_COMPATIBILITY_BASELINE = "1"
    $env:JARVIS_COMPUTER_USE_ENABLED = "0"
    $env:JARVIS_CUA_DRIVER_ENABLED = "0"
    $env:JARVIS_VISION_ENABLED = "0"
    $env:JARVIS_VISION_ACTIONS_ENABLED = "0"
    $env:JARVIS_STRUCTURED_TRACING_ENABLED = "0"

    python -m compileall -q jarvis_agent benchmarks tests
    $JarvisValidationExitCode = $LASTEXITCODE
    if ($JarvisValidationExitCode -eq 0) {
        python -m unittest tests.test_cua_driver_bridge tests.test_windows_perception tests.test_native_tools tests.test_agent_runtime tests.test_screen_vision tests.test_perception_router tests.test_computer_use_engine tests.test_multi_surface_engine tests.test_kernel_foundations tests.test_architecture_extensions tests.test_assistant_v3 tests.test_ui_logging -v
        $JarvisValidationExitCode = $LASTEXITCODE
    }
    if ($JarvisValidationExitCode -eq 0 -and $IncludeBrowser) {
        python -m unittest tests.test_browser_adapter -v
        $JarvisValidationExitCode = $LASTEXITCODE
    }
} finally {
    foreach ($key in $JarvisValidationEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($key, $JarvisValidationEnvironment[$key], "Process")
    }
}
exit $JarvisValidationExitCode
