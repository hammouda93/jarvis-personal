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
