param(
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$outDir = Join-Path (Get-Location) "jarvis_diagnostics_$stamp"
New-Item -ItemType Directory -Path $outDir -Force | Out-Null

$headPath = Join-Path $outDir "git_head.txt"
git rev-parse HEAD | Out-File -FilePath $headPath -Encoding utf8
git log -1 --oneline | Out-File -FilePath $headPath -Encoding utf8 -Append

$statsPath = Join-Path $outDir "knowledge_stats.json"
python -m jarvis_agent.knowledge_cli stats | Out-File -FilePath $statsPath -Encoding utf8

$exportPath = Join-Path $outDir "jarvis_agent_knowledge_export.json"
python -m jarvis_agent.knowledge_cli export --output $exportPath | Out-Null

$ollamaPath = Join-Path $outDir "ollama_models.txt"
try {
    ollama list 2>&1 | Out-File -FilePath $ollamaPath -Encoding utf8
}
catch {
    "Ollama command unavailable: $($_.Exception.Message)" | Out-File -FilePath $ollamaPath -Encoding utf8
}

$testExit = 0
if (-not $SkipTests) {
    $testsPath = Join-Path $outDir "unit_tests.log"
    $testNames = @(
        "tests.test_agent_knowledge",
        "tests.test_screen_vision",
        "tests.test_windows_perception",
        "tests.test_native_tools",
        "tests.test_tools",
        "tests.test_assistant_v3",
        "tests.test_agent_runtime"
    )
    python -m unittest $testNames -v 2>&1 | Tee-Object -FilePath $testsPath
    $testExit = $LASTEXITCODE
}

$readme = @"
Jarvis diagnostics package
Generated: $(Get-Date -Format o)

Safe files to share for debugging:
- git_head.txt
- knowledge_stats.json
- jarvis_agent_knowledge_export.json
- ollama_models.txt
- unit_tests.log (when tests were run)

The knowledge export is anonymized by default.
Do not add memory.sqlite3, agent_knowledge.sqlite3, .env or raw screenshots.
"@
$readme | Out-File -FilePath (Join-Path $outDir "SHARE_THESE_FILES.txt") -Encoding utf8

Write-Host ""
Write-Host "Diagnostics created:"
Write-Host $outDir
Write-Host ""
Get-ChildItem $outDir | Select-Object Name, Length

exit $testExit