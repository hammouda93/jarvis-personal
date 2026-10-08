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

This is the first **integration seam**, not an authoritative Kernel
migration. It does not create an LLM plan, run `MissionOrchestrator` tasks
through `KernelDispatcher`, authenticate connectors, verify user goals,
manage cross-process concurrency or automatically select mission grouping.
Those remain later V1 milestones. Do not delete or supersede working
`KernelShadowObserver`, `StructuredTracingRuntime`, or current Browser
Bridge behavior.
