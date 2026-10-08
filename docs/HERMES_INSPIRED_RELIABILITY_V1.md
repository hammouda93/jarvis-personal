# Hermes-Inspired Reliability V1 — selective design reference

## Source and compatibility boundary

Reference: NousResearch/hermes-agent (MIT-licensed), source inspected at
`8a33891bdd58c3e0795ebfb277bcdde1003e91ea` (2026-10-08).
Ideas studied in `agent/turn_tool_round.py`,
`agent/tool_guardrails.py`, `agent/turn_api_error.py`,
`agent/iteration_budget.py`, and `tools/computer_use/tool.py`.
No Hermes distribution, packages or code have been copied into Jarvis.

Base: `feature/personal-agent-runtime-convergence-v1` at
`47f9423c0aded98c1437352d255b102ae1e973b8`.

## What this implements

**Optional:** The feature flag `JARVIS_HERMES_RELIABILITY_ENABLED` defaults to
`0`. When disabled, the existing model, tools, wrappers and launchers run
through their previous paths.

When enabled:
- The original `NativeToolRegistry` or `FoundationToolRegistry` is
  wrapped at the tool boundary. Exactly **one call** to the original
  `execute` is made for an admitted action. No second executor.
- Before *each* admitted tool execution, a checkpoint is written and committed
  in a separate, closed SQLite connection. This follows the **principle** of
  Hermes' pre-execution persistence, not its code.
- The journal stores tool name, random action ID, turn ID, status, verification
  bit and a **keyed digest of arguments**, never raw arguments or response
  text. Local integrity key lives separately from the database.
- After a crash or a reported `outcome_unknown`, replay of the **same
  potentially mutating tool + arguments** is blocked across restarts until
  a trusted verifier explicitly reconciles the action. Unknown/unregistered
  tool names are treated conservatively as mutating; harmless observations
  use a fixed read-only allowlist.
- Two identical failed actions in one turn suppress a third identical call.
  Other tools, target selections and novel observations remain available.
- The tool's original successful result is returned unmodified; failures
  are not silently promoted to successes.
- `resolve_unknown(action_id, independently_observed=..., evidence_ref=...)`
  is a *trusted controller* operation requiring external verification.
  Accepted outcomes: `effect_observed`, `no_effect`, `cancel`.
  **No automatic retry**, no automatic user-goal certification.
- Failure classification distinguishes `rate_limit`, `authentication`,
  `non_retryable_client`, `transient_provider`,
  `transient_transport` and `unknown`. Diagnostic only: no new
  fallback chain or unapproved waiting loop.
- On Cerebras, the opt-in
  `HermesBudgetedCerebrasAgent` limits **logical _chat rounds**, not total
  HTTP calls. Default matches the existing `agent_max_tool_rounds`; explicit
  override `JARVIS_HERMES_MODEL_ROUND_BUDGET`. Existing primary/secondary
  Cerebras and Groq fallback sequence stays unchanged. The budget does not
  shorten existing missions unless the user deliberately lowers it.

## User-level development test

Do not merge into the stable branch before manual Windows acceptance.

```powershell
cd D:\Django_Projects\jarvis-main\jarvis-main
git status --short
git fetch origin
git switch feature/hermes-inspired-reliability-v1
git pull --ff-only origin feature/hermes-inspired-reliability-v1

# New unit tests / critical regressions
C:\jv312\Scripts\python.exe -m unittest tests.test_hermes_reliability tests.test_runtime_convergence -v

# Normal launcher in SAME PowerShell window; opt in only after baseline check:
$env:JARVIS_HERMES_RELIABILITY_ENABLED = "1"
$env:JARVIS_HERMES_RELIABILITY_DIR = "$env:LOCALAPPDATA\JarvisPersonal\action_reliability_testing"
$env:JARVIS_HERMES_MODEL_ROUND_BUDGET = "8"
```

**Important:** Never delete the action journal just to clear an unknown-effect
blocker. Inspect the real application's current state before resolving it.
If a tool call is blocked, do not assume the external action failed: it may
have succeeded just before a crash.

Compare with the opt-in flag OFF and ON:
- Chrome existing profile: YouTube search, first real observed video result,
  back, close only the YouTube tab.
- Notepad: Unicode typing and post-action observation.
- Generic unknown Windows app and Cursor installer progress.
- Text mode disables microphone while response remains spoken.
- Semantic Memory V5, mission continuation after restart.
- Cerebras 429/timeout produces a truthful failure without duplicating an
  external write.
- Artificially lost tool result: no second identical external action until
  independently reconciled.

## Deliberate non-goals

Does **not** automatically plan or schedule missions; does not install
Hermes; does not mutate Skills or learning; does not alter Chrome Bridge,
UIA, CUA, legacy code paths or external provider credentials.
No authorization is bypassed. The new reliability layer does not classify
the *semantic* safety of unknown commands — trusted authorization and
goal-level evidence remain responsibilities of the existing policy and
supervisor. Do not consider an HTTP response or LLM text independent proof.

## Data and security

The journal file itself is local. Argument fingerprints are computed with
HMAC-SHA256 using a locally generated random key, so the database contains
neither a phone number, message body nor password as clear text.
Application files and older SQLite stores are not migrated.

## Acceptance gates

- New unit tests green; critical Windows Python + 45 Browser replays green.
- Broad cross-platform Python suite green, excluding only four known
  Windows-only tests on Linux.
- Manual computer-use test cases green, including the negative cases.
- Separate PR for code review and **no merge until real Windows acceptance**.
