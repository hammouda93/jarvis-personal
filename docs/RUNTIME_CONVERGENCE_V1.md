# Runtime Convergence V1a — opt-in live checkpoints

Base: `fix/browser-live-contracts-v2` at `aff001fd04bcb8adfc99204b346ce126e0ec8a46`.

## Scope implemented

`LiveMissionContinuityRuntime` wraps the already-selected agent runtime **without
changing its tool calls, prompt, browser behavior, UIA/Cua behavior, model,
memory pipeline or spoken/text response**. It stores mission context and a
task graph in a separate SQLite directory. Each *real* turn is checkpointed
as RUNNING before the delegate executes. Once returned, the state records
only action names, counts and outcomes, not arbitrary tool arguments,
message bodies or API credentials.

Two modes:
- **Implicit:** a standard standalone conversational turn is recorded and
  then detached. A text-only turn is marked conversationally completed,
  **not** externally goal-verified. A turn containing actions stays
  `WAITING_EXTERNAL` until a separate proof mechanism is implemented.
- **Explicit:** callers may use `begin_mission(goal)`, `run(...)` across
  turns, `resume_mission(id)`, and `complete_mission(proof_ref=...)`.
  An explicit mission always waits for separate goal proof.

`resume_mission` never replays actions or calls Cerebras. A crashed
RUNNING task becomes `WAITING_EXTERNAL`, the mission becomes BLOCKED,
and human inspection is required. A failed tool or delegate exception
also blocks the mission. A supplied `proof_ref` is *not independently
validated by this component*: its caller must be a trusted verifier.

## Enable on a development machine

Default is OFF. Do not enable in production before live acceptance.

In PowerShell, set in the **same shell** used to launch Jarvis:

```powershell
$env:JARVIS_RUNTIME_CONVERGENCE_ENABLED = "1"
$env:JARVIS_RUNTIME_CONVERGENCE_DIR = "$env:LOCALAPPDATA\JarvisPersonal\runtime_convergence_testing"
# Keep all your existing feature flags and launcher unchanged.
```

Disable the integration without deleting any previous files:

```powershell
$env:JARVIS_RUNTIME_CONVERGENCE_ENABLED = "0"
```

Inspect missions with the existing `MissionContextStore` and
`TaskGraphStore`. No database migration touches legacy memory.

## Validation gates

```powershell
python -m unittest tests.test_runtime_convergence -v
python -m unittest tests.test_foundations_v4 tests.test_browser_dom_contract tests.test_agent_runtime tests.test_semantic_memory_v5 -q
node --test tests/browser_bridge_replay.mjs
```

Before promotion: verify with the usual Chrome profile, YouTube search /
select result / back / close only tab, Notepad Unicode, Cursor installer,
microphone-off text mode with spoken replies, memory restart, WhatsApp
target proof and a forced Cerebras 429. Verify that failure states do not
autonomously repeat writes or sends.

## Explicit non-goals of V1a

This is an **opt-in integration seam**, not an authoritative Kernel
migration. It does not create an LLM plan, run `MissionOrchestrator` tasks
through `KernelDispatcher`, authenticate connectors, verify user goals,
manage cross-process concurrency or automatically select mission grouping.
Those remain later V1 milestones. Do not delete or supersede working
`KernelShadowObserver`, `StructuredTracingRuntime`, or current Browser
Bridge behavior.

## Additional V1 continuity and supervisor safeguards

- Explicit missions carry the original high-level goal and the last five
  checkpoint results into the **existing** LLM call, only if the installed
  delegate exposes `run_with_context`. No extra Cerebras request is made.
  Inputs are marked as *DATA ONLY* and capped in size; the legacy text/voice
  runtime remains the executor.
- Every checkpoint has a stable mission ID and an isolated structured event
  journal (mission created, user turn, result, unresolved outcome, proof). Only
  tool names/counts and status are recorded; arbitrary tool argument bodies,
  private messages and tool details are not captured by this new layer.
- The existing `CapabilityAgentRouter` can classify observed tool names into
  suggested domain executors after the action. This routing is
  `authoritative: false`; it never submits tools or changes the selected
  executor. Unknown/ambiguous tool ownership is surfaced as requiring review.
- `mission_snapshot(mission_id)` exposes persistent task states and suggested
  routes without running Cerebras or changing any computer state.
- `resume_mission(mission_id)` never replays uncertain actions. It marks an
  interrupted step for independent verification. `resolve_recovery` requires
  an explicit trusted decision and evidence reference:
  - `completed`: confirmed external effect; mark the prior step settled.
  - `not_executed`: independently verified no effect; cancel only that
    attempt, without retrying.
  - `cancel`: abort the entire mission.
- `complete_mission(proof_ref=...)` is reserved for a trusted caller after
  independent end-goal inspection. **A raw text reference is not itself proof**:
  the caller must establish its validity, and the LLM must never call this
  method based solely on its own claim.

### Not yet activated

The app does not automatically transform arbitrary consecutive chat turns
into one explicit cross-turn mission. Those require explicit
`begin_mission`/`resume_mission` calls by an authorized application
controller. The current wrapper records ordinary chat turns individually,
without guessing whether a later user utterance is a follow-up.

The authoritative orchestration path (`MissionOrchestrator.dispatch_ready`,
`KernelDispatcher`, permission-gated connectors and real task plans) remains
behind future live gates. No new routes are allowed to dispatch externally
based solely on these passive decisions.

### One-shot branch validation

To test after the full development gate passes (without touching reference
branches), inspect `git status --short` first and preserve any local edits.

```powershell
cd D:\Django_Projects\jarvis-main\jarvis-main
git fetch origin
git switch feature/personal-agent-runtime-convergence-v1
git pull --ff-only origin feature/personal-agent-runtime-convergence-v1
C:\jv312\Scripts\python.exe -m unittest tests.test_runtime_convergence tests.test_foundations_v4 tests.test_browser_dom_contract tests.test_agent_runtime tests.test_semantic_memory_v5 -q
& "C:\Users\salah\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe" --test tests\browser_bridge_replay.mjs
```

Enable the convergence flag only after completing the baseline-off comparison.
The normal Jarvis launcher and existing Memory/Browser/Computer feature flags
remain unchanged. No automatic migration or merge happens.
