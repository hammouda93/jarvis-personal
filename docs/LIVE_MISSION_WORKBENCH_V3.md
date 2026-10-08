# Jarvis Personal — Live Mission Workbench V3

## Current position: stage 7 of 11 (code and CI validated / Windows acceptance pending)

The roadmap is rendered in the actual PySide6 **▤ Supervision** panel and
is also source-controlled in `jarvis_agent/project_roadmap.py`.
Each stage explicitly separates **code integration**, **automated tests**,
and **real Windows user acceptance**. No arbitrary completion percentage.

| # | Capability | Code state | CI state at start of V3 | Real Windows |
| --- | --- | --- | --- | --- |
| 1 | General brain & tools | Integrated | Previous branch green | Pending |
| 2 | Chrome + Windows perception | Integrated | Previous branch green | Pending |
| 3 | Semantic Memory V5 | Integrated | Previous branch green | Pending |
| 4 | Durable mission and passive kernel | Integrated | Previous branch green | Pending |
| 5 | Hermes-inspired reliability | Integrated | Previous branch green | Pending |
| 6 | Operator UI observability | Integrated | Previous branch green | Pending |
| 7 | Explicit multi-turn live missions | Integrated | V3 CI green (410 Windows Python, 45 Browser Bridge, 591 Linux Python) | Pending |
| 8 | Semantic goal-proof supervisor | Planned | — | Pending |
| 9 | Autonomous planning and agent delegation | Planned | — | Pending |
| 10 | Validated learning and Skills improvement | Planned | — | Pending |
| 11 | External integrations / scheduled personal work | Planned | — | Pending |

Source baseline for stages 1–6:
`feature/hermes-operator-console-v2` @ `8bfc8296e40bdba72d1cb6a2ab814ecfb9326701`
(399 Windows Python, 45 Browser Bridge, 580 Linux Python tests).

## Newly integrated

- In the operator console, the **MISSION EXPLICITE** control starts a
  mission with its typed goal; it is immediately submitted to the **same
  existing model**, through the original AgentRuntime and normal conversation
  pipeline. Nothing launches an additional LLM, scheduler, or desktop executor.
- While a mission is attached, the normal **text conversation** supplies later
  user instructions to that same mission, preserving its goal context and
  adding durable task checkpoints. Existing browser/Windows fast-paths are
  bypassed **only while explicit mission tracking is active**, so their work
  isn't invisible to the mission ledger.
- **Détacher sans conclure** disconnects the currently tracked mission
  without declaring success or discarding its saved state.
- **Reprendre le suivi** restores a saved `live_...` mission by exact ID
  and current owner. If the previous result is uncertain, the UI reports
  **BLOQUÉE**, and the existing runtime refuses execution until independent
  action verification. No replay happens on resume.
- All GUI-to-runtime mission commands enter a thread-safe FIFO, processed
  only by `AssistantWorker.run`. No model calls or mission mutations run
  on the UI thread.
- Selecting mission controls switches to typed mode first. This preserves the
  existing microphone OFF + spoken/text replies ON contract.
- The live Roadmap pane shows all 11 stages and the **actual** current
  code/testing position; it never represents automated CI as field validation.
- Existing interface, Chrome Bridge, memory, skills, observability, model
  routing and legacy stable branch unchanged.

## Deliberate limits

The user must intentionally press **Démarrer et exécuter**. There is
**no autonomous cross-turn intent classifier** yet, and the system does
not synthesize a semantic DAG or execute specialist agents on its own.
The semantic supervisor and autonomous delegation remain future stages.
Success of an action is **not proof** of the whole mission.
A blocked mission must not be automatically resumed through any fallback.
Do not advertise a full Hermes runtime integration.

## Explicit local Windows test, when the user chooses

```powershell
cd D:\Django_Projects\jarvis-main\jarvis-main
git status --short
git fetch origin
git switch feature/live-mission-workbench-v3
git pull --ff-only origin feature/live-mission-workbench-v3

C:\jv312\Scripts\python.exe -m unittest tests.test_mission_workbench tests.test_runtime_convergence tests.test_hermes_reliability tests.test_operator_console_ui -v

$env:JARVIS_RUNTIME_CONVERGENCE_ENABLED = "1"
$env:JARVIS_HERMES_RELIABILITY_ENABLED = "1"
# Launch Jarvis normally from this same PowerShell window.
```

Acceptance: operator UI can start a multi-turn mission with one typed objective,
show durable turn count and task history; detach; relaunch app; resume by ID;
preserve goal and use only one original agent runtime. An interrupted action
must require manual review. Confirm direct commands still work outside mission,
browser tab targeting never regresses, Unicode/installer/UIA/CUA still work,
text mode disables mic but voice response remains, and Memory V5 retrieval is
unchanged.

## Work governance

Stable checkpoint `aff001fd04bcb8adfc99204b346ce126e0ec8a46`
must remain intact. Feature PR stays DRAFT pending real desktop evidence.
After every development stage, include the branch, latest commit, precise
automated test results, real-world gates and next architecture objective.


## Stage 7 automated results / next stage

GitHub Actions V3 initial successful run:
`https://github.com/hammouda93/jarvis-personal/actions/runs/37777272136`
(410 Python Windows, 45 Browser Bridge Node, 591 Python Linux). This is
**automatic acceptance only**, not a claim that the user's Windows workflows
have succeeded. Stage 7 remains current for desktop validation.

**Next development target (stage 8):** independent, evidence-based semantic
supervision with explicit recovery and goal-level proof. A tool success or a
text completion is insufficient. No autonomous kernel/agent dispatch is
advertised or enabled yet.
