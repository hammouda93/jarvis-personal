param()

$ErrorActionPreference = "Stop"

$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

Write-Host "=== Jarvis architecture foundations validation ==="
Write-Host ""

Write-Host "1/3 Python syntax preflight..."
$compile = "python -m py_compile jarvis_agent\kernel_contracts.py jarvis_agent\event_journal.py jarvis_agent\incident_bundle.py jarvis_agent\tracing_runtime.py jarvis_agent\event_bus.py jarvis_agent\write_barrier.py jarvis_agent\mission_context_store.py jarvis_agent\knowledge_policy.py jarvis_agent\knowledge_broker.py jarvis_agent\scoped_knowledge_store.py jarvis_agent\capability_registry.py jarvis_agent\agent_factory.py jarvis_agent\plugin_manifest.py jarvis_agent\plugin_loader.py jarvis_agent\plugin_policy.py jarvis_agent\connector_registry.py jarvis_agent\connector_gateway.py jarvis_agent\mcp_connector_adapter.py jarvis_agent\approval_manager.py jarvis_agent\kernel_policy.py jarvis_agent\kernel_request_store.py jarvis_agent\tool_gateway.py jarvis_agent\workspace_storage.py jarvis_agent\execution_managers.py jarvis_agent\mission_scheduler.py jarvis_agent\task_graph.py jarvis_agent\task_graph_store.py jarvis_agent\mission_orchestrator.py jarvis_agent\kernel_service.py jarvis_agent\context_broker.py jarvis_agent\context_injector.py jarvis_agent\component_registry.py jarvis_agent\dev_supervisor.py jarvis_agent\supervisor_planner.py jarvis_agent\supervisor_validation.py jarvis_agent\correction_store.py jarvis_agent\regression_registry.py jarvis_agent\regression_runner.py jarvis_agent\replay_sandbox.py jarvis_agent\replay_adapter.py jarvis_agent\model_telemetry.py jarvis_agent\model_router.py jarvis_agent\llm_manager.py jarvis_agent\secret_provider.py jarvis_agent\kernel_cli.py"
cmd.exe /d /s /c $compile
if ($LASTEXITCODE -ne 0) {
    Write-Host "Foundation syntax preflight FAILED."
    exit $LASTEXITCODE
}
Write-Host "Foundation syntax preflight OK."
Write-Host ""

Write-Host "2/3 Foundation unit tests..."
$foundation = "python -m unittest tests.test_kernel_foundations -v 2>&1"
cmd.exe /d /s /c $foundation
$foundationExit = $LASTEXITCODE
if ($foundationExit -ne 0) {
    Write-Host "Foundation unit tests FAILED."
    exit $foundationExit
}
Write-Host ""

Write-Host "3/3 Historical baseline non-regression..."
$baselineOutput = & powershell -ExecutionPolicy Bypass -File .\scripts\run_baseline_regression.ps1 2>&1
$baselineExit = $LASTEXITCODE
$baselineOutput | ForEach-Object { Write-Host $_ }
if ($baselineExit -ne 0) {
    Write-Host "Historical baseline FAILED."
    exit $baselineExit
}

Write-Host ""
Write-Host "Architecture foundations OK; live behavior remains disabled-by-default."
exit 0