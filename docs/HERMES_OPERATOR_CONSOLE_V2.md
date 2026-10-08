# Hermes-inspired Operator Console V2

This branch enhances the existing PySide6 Jarvis application with a real
operator console. Hermes (NousResearch/hermes-agent, commit 8a33891) is an
architecture reference only; no Hermes installation or runtime replacement.

## Existing UI preserved

The original animated graph and flow are intact. A new right-side tab
displays recent missions, task states, action checkpoint outcomes, model
provider and measured logical rounds, module flags, and numerical statistics
for the existing memory and operational knowledge stores.

All observations are sourced from existing local state or Qt worker signals.
SQLite access is read-only and bounded. The console never runs an action
or makes an additional model request. Stored action arguments and memory
contents are not displayed.

A tool reporting success is not automatically considered proof of the
mission goal. Uncertain actions remain marked uncertain.

## Windows test commands

Run in the existing Jarvis project folder:

    git status --short
    git fetch origin
    git switch feature/hermes-operator-console-v2
    git pull --ff-only origin feature/hermes-operator-console-v2

Then run:

    C:\jv312\Scripts\python.exe -m unittest tests.test_operator_telemetry tests.test_operator_console_ui tests.test_hermes_reliability tests.test_runtime_convergence -v

The new functionality is controlled by the pre-existing opt-in flags:

    JARVIS_HERMES_RELIABILITY_ENABLED=1
    JARVIS_RUNTIME_CONVERGENCE_ENABLED=1

Preserve the user's other settings and their normal launcher. The stable
branch remains unchanged. Do not merge until desktop acceptance verifies
Chrome navigation, real applications, Unicode input, voice/text separation,
memory retrieval, and no repetition of uncertain external actions.

## Remaining architecture work

Automatic multi-turn mission admission, authoritative kernel dispatch,
delegation, advanced context compression, and external platform connectors
require separate integration and real-world validation. The current UI does
not pretend those components have already been activated.


## Supervision expanded view

The `▤ Supervision` button in the existing window header switches to a
wide operator view; `◉ Revenir au graphe` returns to the classic animated
agent graph. The expanded view shows up to 18 recent persisted checkpoints
with scrollable mission, step, and tool panels. The normal view shows seven.
Compact and clean views remain available and are never bypassed by telemetry.


## Actual reliability decisions (V2.1)

A blocked action is **not an executed action**. The opt-in reliability
layer now records a separate `guarded` checkpoint with a fixed reason code.
The dashboard highlights such records as **BLOQUÉ PAR PROTECTION** instead
of misrepresenting them as tool execution errors or successful mutations.
The guard journal contains only tool name, HMAC argument digest, turn ID,
status and a fixed machine-readable refusal reason.

Additional Hermes-inspired progress guard: after **three successful,
byte-identical observations** from the same explicitly read-only tool with
the same arguments **in one turn**, a fourth identical observation is
denied with `no_observable_progress`. The agent must switch its
observation method, target or strategy. Any changed result resets this
counter. The guard NEVER promotes an unrecognized tool to read-only,
NEVER skips existing verification, and NEVER retries side effects.

There is still no proof that an identical observation means the entire
user goal is complete. The new guard is a loop-safety heuristic, not
a semantic goal verifier.

New negative/regression cases cover stalled perception, changed screens,
cross-turn reset, an unknown potentially mutating tool, persisted guard
reasons, privacy of journal arguments and the irreversible uncertain
side-effect barrier.


## Live session activity (independent of Kernel Shadow)

A new `AssistantWorker.operator_event` Qt signal reports a bounded and
privacy-minimal summary when an action turn actually completes or fails.
It covers **direct/legacy fast-path** operations as well as LLM-driven turns
even when `JARVIS_KERNEL_SHADOW_ENABLED=0`.

The operator console keeps a session-only ring buffer (36 recent entries)
for this view: source, tool names, outcome. It does **not** include user
utterances, messages, tool arguments, provider credentials, or full responses.
No cross-session recovery relies on these ephemeral events; durable evidence
still belongs to the existing opt-in journals.

The interface explicitly says *objectif : non vérifié* on this stream:
neither a successful tool nor an LLM reply is independent proof that the
user's entire mission succeeded. The UI does not activate or override any
existing permission or app-specific tool.
