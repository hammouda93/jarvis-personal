param()

$ErrorActionPreference = "Stop"

Write-Host "=== Jarvis post-fix validation ==="
Write-Host ""

$targeted = @(
    "tests.test_agent_knowledge.AgentKnowledgeStoreTests.test_skill_upsert_versions_and_merges",
    "tests.test_agent_runtime.AgentRuntimeTests.test_cerebras_secondary_failover_matches_quota_and_service_errors",
    "tests.test_agent_runtime.AgentRuntimeTests.test_sensitive_local_function_waits_for_user_approval"
)

$targetCommand = "python -m unittest " + ($targeted -join " ") + " -v 2>&1"
cmd.exe /d /s /c $targetCommand
$targetExit = $LASTEXITCODE

if ($targetExit -ne 0) {
    Write-Host ""
    Write-Host "Targeted validation FAILED. Full suite not started."
    exit $targetExit
}

Write-Host ""
Write-Host "Targeted validation OK. Running full diagnostics..."
Write-Host ""

powershell -ExecutionPolicy Bypass -File .\scripts\run_learning_diagnostics.ps1
exit $LASTEXITCODE