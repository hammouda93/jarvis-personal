# Jarvis — AIOS-inspired foundations

This document records the architecture concepts adopted after the AIOS /
Cerebrum code audit. Jarvis is **not** being migrated to AIOS.

## Non-regression rule

Historical Jarvis behavior remains authoritative while the current Windows/UI
and local-vision test battery is being validated.

All foundations in this document are either metadata-only, passive stores, or
disabled by default:

- `JARVIS_STRUCTURED_TRACING_ENABLED=0`
- `JARVIS_MODEL_TELEMETRY_ENABLED=0`
- `JARVIS_AGENT_REGISTRY_ENABLED=0`
- `JARVIS_DEV_SUPERVISOR_ENABLED=0`

Do not enable them merely to debug an unrelated Windows/UI test.

## 1. Kernel contracts

`jarvis_agent/kernel_contracts.py`

Introduces stable contracts without moving the current runtime:

- `MissionStatus`
- `EventKind`
- `KnowledgeScope`
- `PromotionTarget`
- `RiskLevel`
- `CapabilitySpec`
- `AgentManifest`
- `MissionContext`
- `CorrectionCandidate`

Knowledge scopes are intentionally distinct:

- `CORE`
- `APP`
- `DOMAIN`
- `AGENT`
- `SKILL`
- `USER`
- `TEST`
- `SESSION`

A user-specific correction must not silently become a CORE invariant.

## 2. Structured mission/event journal

`jarvis_agent/event_journal.py`

Local SQLite store:

`%LOCALAPPDATA%\JarvisPersonal\mission_events.sqlite3`

It is designed to reconstruct:

user input/intent summary → selected agent/capability → tool request → tool result
→ observation → proof → feedback → correction candidate → test/replay result.

The journal stores structured operational evidence, **not hidden model chain of
thought**. Secret-like fields and common personal paths/emails are redacted.

The journal is not connected to the live runtime while
`JARVIS_STRUCTURED_TRACING_ENABLED=0`.

## 3. Agent / capability registry

`jarvis_agent/capability_registry.py`

Current declarative manifests:

- windows
- browser
- ms_football
- communications
- developer

Examples of capabilities:

- `computer.observe`
- `computer.interact`
- `browser.search`
- `msf.read`
- `msf.commit_mutation`
- `communications.send`
- `developer.test`
- `developer.replay`

Each agent declares tool/connectors and memory scopes. This is the future
enforcement boundary; it is **not yet the active router**.

## 4. Connector registry

`jarvis_agent/connector_registry.py`

Defines a stable logical capability contract independent from the backend.

Backends:

1. API
2. MCP
3. local SDK
4. database
5. browser
6. Windows UI

Initial connector metadata:

- Gmail
- WhatsApp
- Instagram
- GitHub

No credentials are stored and no external account is connected by this module.

Example future flow:

`communications.send → Connector Gateway → Gmail API / MCP / UI backend`

The LLM should not need to know OAuth/token implementation details.

## 5. Scoped write barrier

`jarvis_agent/write_barrier.py`

AIOS-inspired read-after-write primitive for future concurrent agents.

A reader snapshots the current sequence for one user/scope and waits only for
writes that existed at snapshot time. Newer writes do not indefinitely block
the old read.

It is not wired into the current single-agent knowledge path yet.

## 6. Dev Supervisor contracts

`jarvis_agent/dev_supervisor.py`

Failure categories include:

- CORE_INVARIANT
- TOOL_PRIMITIVE
- APP_PROFILE
- SKILL
- USER_PREFERENCE
- STT
- MODEL_REASONING
- MISSING_CAPABILITY
- UI_CHANGED
- CONNECTOR
- UNKNOWN

A `FailureAssessment` can create a pending `CorrectionCandidate`.

A candidate is never promoted automatically. User validation can later send it
to one of:

- core regression + patch
- app profile
- domain knowledge
- agent policy
- verified skill
- user preference
- regression test
- session-only state

This is the base for:

`Observe → understand → investigate → correct → test → replay → validate → learn`

## 7. Regression registry

`jarvis_agent/regression_registry.py`

Regression packs can be selected by tags or changed file paths.

Initial packs:

- TEST-WIN-BASELINE
- TEST-VISION-LAYER
- TEST-MEMORY-KNOWLEDGE

Future Dev Supervisor patches should select relevant tests automatically before
running the full suite.

## 8. Passive LLM telemetry

`jarvis_agent/model_telemetry.py`

Local SQLite store:

`%LOCALAPPDATA%\JarvisPersonal\model_telemetry.sqlite3`

Tracks, when explicitly enabled/wired later:

- provider
- model
- task class
- latency
- success/failure
- rate-limit errors
- token counts when available
- estimated cost when available

This does **not** alter current Cerebras/Groq routing.

It is the lightweight foundation for a future adaptive `ModelRouter` without
bringing AIOS SmartRouting's Chroma/PuLP/LiteLLM stack into Jarvis.

## 9. CLI

Inspect the passive architecture:

```powershell
python -m jarvis_agent.kernel_cli agents
python -m jarvis_agent.kernel_cli capabilities
python -m jarvis_agent.kernel_cli connectors
python -m jarvis_agent.kernel_cli tests
python -m jarvis_agent.kernel_cli missions
python -m jarvis_agent.kernel_cli corrections
python -m jarvis_agent.kernel_cli approvals
python -m jarvis_agent.kernel_cli stats
```

Later, if structured tracing is enabled and wired:

```powershell
python -m jarvis_agent.kernel_cli trace <mission_id>
python -m jarvis_agent.kernel_cli models --hours 24
```

## 10. Persistent mission context

`jarvis_agent/mission_context_store.py`

Adds versioned local persistence for long-running missions:

- mission/user/agent identity
- current step and step id
- pending action/confirmation
- expected and observed state
- artifact refs
- proof refs
- knowledge refs
- resumable statuses

Updates support optimistic concurrency so two future agents cannot silently
overwrite the same mission state.

## 11. Knowledge isolation policy

`jarvis_agent/knowledge_policy.py`

Adds fail-closed access checks using:

- knowledge scope
- owner user
- owner agent
- organization
- sharing policy

Ordinary knowledge writes can never mutate `CORE`; Core changes must go through
the Dev Supervisor + regression path.

## 12. Mission scheduler and passive Jarvis Kernel

`jarvis_agent/mission_scheduler.py`
`jarvis_agent/kernel_policy.py`
`jarvis_agent/kernel_service.py`

The passive Kernel now models:

`authorize → approval if required → queue → start → complete → event/trace`

The scheduler provides priority plus stable FIFO, cancellation, queue status,
waiting time and turnaround time.

This layer is not in the live interaction path yet.

## 13. Human approval manager

`jarvis_agent/approval_manager.py`

External/destructive capabilities can create persistent approval requests.
Approved requests are consumable exactly once before execution.

This will eventually replace prompt-only security for high-risk agents and
connectors.

## 14. Correction candidate persistence

`jarvis_agent/correction_store.py`

Human feedback is staged as a pending correction candidate.

Detection, validation and promotion are separate:

`feedback → candidate → tests/proof → user validation → promotion`

A candidate cannot be promoted before explicit accepted validation.

## 15. Context broker

`jarvis_agent/context_broker.py`

Provides deterministic budgeted context selection for future specialized agents:

- required mission state first
- then optional items by priority/relevance
- deterministic token estimate
- omitted item tracking

It does not replace today's conversation history.

## 16. Lightweight model router

`jarvis_agent/model_router.py`

Implements the useful AIOS SmartRouting ideas without LiteLLM/Chroma/PuLP:

- task tags
- context eligibility
- latency budget
- cost budget
- passive historical success rate
- rate-limit penalty
- local-model preference
- temporary circuit breaker

It is intentionally not connected to the current Cerebras → secondary
Cerebras → Groq path during our live validation.

## 17. Replay sandbox contract

`jarvis_agent/replay_sandbox.py`

Provider-neutral lifecycle:

`reset → record → execute actions → observe → evaluate → artifacts`

A future LiteCUA/VMware/VirtualBox/Docker backend can implement this interface
without replacing real-PC Windows UIA.

## 18. Plugin manifest

`jarvis_agent/plugin_manifest.py`

Future plugins/agents can declare:

- agent and entrypoint
- capabilities
- tools/connectors
- domains/network hosts/file roots
- memory scopes
- risk
- confirmation requirements
- regression test pack

High-risk plugins without confirmation are rejected by validation. Dynamic
third-party loading is deliberately not implemented yet.

## 19. Mission Event Bus

`jarvis_agent/event_bus.py`

Adds an isolated in-process publisher/subscriber contract. A failing observer
cannot break the command path. The Event Journal remains the durable history;
the Event Bus is for future Dev Supervisor/metrics/agent observers.

## 20. Connector Gateway

`jarvis_agent/connector_gateway.py`

Separates logical capabilities from physical backends and enforces confirmation
for external side effects. Backend priority can be API → MCP → local/UI without
changing the agent's semantic request.

## 21. Validation

`tests/test_kernel_foundations.py`

Covers the passive architecture, including isolation, write barrier, mission
persistence, scheduler ordering, approval single-use, Kernel authorization,
connector permissions, correction promotion gates, replay, model telemetry and
routing, plugin safety and event observer isolation.

`scripts/run_architecture_foundations_validation.ps1` runs:

1. syntax preflight for all foundation modules
2. foundation unit tests
3. the historical Jarvis baseline regression

The acceptance condition is therefore:

**new architecture present + old Jarvis behavior still green.**

## What is deliberately NOT done yet

- no AIOS dependency
- no Cerebrum dependency
- no AIOS kernel server
- no Mem0 migration
- no SmartRouting replacement
- no active specialized-agent router
- no automatic Gmail/WhatsApp/Instagram access
- no automatic Dev Supervisor code patching
- no automatic promotion of feedback to Core
- no multi-agent scheduler in the live request path
- no VM controller replacing real Windows UIA

These remain staged future integrations after the current UI/vision test
battery is green.

## Future activation order

1. Finish Windows/UIA + local vision validation.
2. Enable operational learning only and validate skills/lessons.
3. Wire structured tracing passively.
4. Promote validated behaviors into the regression registry.
5. Introduce specialized agents behind the capability registry.
6. Add Connector Gateway (Gmail first; then GitHub/WhatsApp/Instagram as appropriate).
7. Add Dev Supervisor trace analysis and test selection.
8. Add isolated replay/sandbox before automatic patch execution.
9. Add multi-agent write barrier/scheduler when true concurrency is introduced.
10. Only then consider adaptive LLM routing from passive telemetry.