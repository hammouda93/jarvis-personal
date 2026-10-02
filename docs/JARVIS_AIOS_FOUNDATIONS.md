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

## 22. Mission task graph

`jarvis_agent/task_graph.py`

Separates mission orchestration from resource scheduling.

A mission can model:

- parent/child task dependencies
- ready/running/waiting/completed/failed/cancelled states
- waiting for user
- waiting for external systems
- downstream cancellation after a failed prerequisite

This is the layer AIOS itself does not fully provide for our long-running
Jarvis workflows.

## 23. Dev Supervisor component map and planner

`jarvis_agent/component_registry.py`
`jarvis_agent/supervisor_planner.py`

The component registry maps known Jarvis code surfaces to capabilities and
regression packs, including:

- Windows/UIA
- local screen vision
- agent runtime
- operational knowledge
- MS Football
- voice interaction
- passive Kernel foundations

The Supervisor Planner combines:

correction candidate
+ changed paths
+ tags
+ component defaults
→ selected regression packs
+ replay requirement
+ promotion destination
+ mandatory user validation

## 24. Per-agent concurrency

`jarvis_agent/mission_scheduler.py`

The passive scheduler now enforces per-agent concurrency limits. The passive
`JarvisKernel` derives those limits from `AgentManifest.max_concurrency`.

This prevents a future specialized agent from running more simultaneous
syscalls than its declared capacity while preserving priority + FIFO ordering.

## 25. Explicit Agent Factory and child-agent lifecycle

`jarvis_agent/agent_factory.py`

Specialized agents are created only from trusted builders registered by Jarvis.
A plugin manifest is never enough to import arbitrary Python code.

Lifecycle:

`created → running → suspended/resumed → completed/failed/terminated`

The factory enforces the agent manifest concurrency limit and tracks parent
process ids for future child agents.

## 26. Scoped Tool Gateway and execution managers

`jarvis_agent/tool_gateway.py`
`jarvis_agent/execution_managers.py`

The Tool Gateway is a hard permission boundary:

`agent_id + tool_name → manifest permission check → executor`

A model cannot gain a tool merely by asking for it in a prompt.

Execution managers provide typed dispatch for Kernel syscalls:

- TOOL
- CONNECTOR
- MEMORY
- callback-based LLM/storage/test/replay adapters

The current live NativeToolRegistry remains authoritative until the Kernel path
is explicitly activated.

## 27. Persistent mission orchestration

`jarvis_agent/mission_orchestrator.py`
`jarvis_agent/task_graph_store.py`
`jarvis_agent/kernel_request_store.py`

Mission context, task DAG and Kernel requests can all be persisted.

Restart safety rule:

- QUEUED requests may be safely restored.
- RUNNING requests are never automatically replayed.
- RUNNING requests become `recovery_required` for verification by the future
  Dev Supervisor.
- step ids allow the orchestrator to rebuild `request_id ↔ task_id` mappings.

This avoids duplicate external side effects after a crash.

## 28. Persistent scoped knowledge backend

`jarvis_agent/scoped_knowledge_store.py`

Provides an SQLite backend for the existing KnowledgeBroker contract while
preserving:

- user ownership
- agent ownership
- organization partition
- APP/DOMAIN/SKILL/USER/etc. scope
- private/shared policy
- metadata and relevance

The current operational knowledge database is not migrated. Instead, `agent_knowledge_adapter.py` projects the existing skills/lessons/app profiles into scoped Kernel knowledge while preserving the original SQLite store as the source of truth.

## 29. Cross-agent Context Injector

`jarvis_agent/context_injector.py`

Builds context for one agent using:

mission context
+ scoped knowledge visible to the user/agent principal
+ agent-declared memory scopes
+ deterministic token budget

A mission from another user is rejected. Knowledge remains filtered by the
KnowledgeAccessPolicy before context selection.

This is the Jarvis equivalent of the useful AIOS ContextInjector concept,
without Mem0.

## 30. Resource-aware scheduler

`jarvis_agent/mission_scheduler.py`

The scheduler now supports independent concurrency limits for:

- agents
- syscall/resource classes

This allows future limits such as:

- one expensive LLM request at a time
- several safe reads
- restricted concurrent external connectors
- isolated test/replay workers

Priority + FIFO remains unchanged.

## 31. Passive routed LLM manager

`jarvis_agent/llm_manager.py`

Future Kernel LLM execution can use:

- LightweightModelRouter
- provider/model adapters
- telemetry
- temporary circuit breaker
- retry/fallback
- task tags
- latency/cost/context constraints

This is **not** connected to the live Cerebras → secondary Cerebras → Groq path
during current testing.

## 32. Incident Bundle for Dev Supervisor

`jarvis_agent/incident_bundle.py`

Builds a bounded failure evidence package from the Event Journal:

- user inputs
- intent/agent/model decision events
- tool/syscall events
- observations
- proofs
- user feedback
- errors
- relevant component ids
- event ids

It deliberately does not reconstruct or store hidden chain-of-thought.

This is the evidence package required for cases such as:

`"Open Chrome" → Edge opened → user says "No, wrong"`

## 33. MCP and replay adapters

`jarvis_agent/mcp_connector_adapter.py`
`jarvis_agent/replay_adapter.py`

MCP capabilities must be explicitly mapped; arbitrary remote tools are not
auto-discovered into the agent permission set.

The replay adapter is backend-neutral and can later talk to a localhost-only
VM/MCP controller inspired by LiteCUA without importing AIOS code.

## 34. Plugin and secret security

`jarvis_agent/plugin_loader.py`
`jarvis_agent/plugin_policy.py`
`jarvis_agent/secret_provider.py`

Plugin manifests are metadata-only until trusted Jarvis code registers an
executable agent builder.

Plugin policy enforces declared:

- tools
- connectors
- network hosts
- file roots

Secrets can remain environment-backed for compatibility. An optional
non-enumerating Windows Credential Manager provider can read one exact target
without listing the Windows vault.

## 35. Legacy operational knowledge adapter

`jarvis_agent/agent_knowledge_adapter.py`

Bridges the existing `agent_knowledge.sqlite3` store into the scoped Kernel
knowledge model without migration.

Projection rules:

- existing skills → `SKILL`
- existing app profiles → `APP`
- existing lessons → `USER` by default, never silently `CORE`

The adapter enforces the configured owner user id. Future compatible writes can
map back into the existing store, while Core changes remain forbidden through
ordinary knowledge writes.

## 36. Kernel Dispatcher

`jarvis_agent/kernel_dispatcher.py`

Closes the passive execution loop:

`KernelRequest → Scheduler → ExecutionManager → Kernel.complete → KernelResponse`

Manager failures are converted into failed Kernel responses so scheduler
capacity is always released. This is still not wired into the voice/runtime
path.

## 37. Local RPC security contract

`jarvis_agent/local_rpc_security.py`

Any future Kernel/Replay RPC endpoint is expected to be loopback-only
(`127.0.0.1`, `::1`, or localhost) and capability-token protected.

No server is started by this module. In particular, `0.0.0.0` is rejected by
the policy contract.

## 38. Current model catalog

`jarvis_agent/model_catalog.py`

Describes the models already present in Jarvis (Cerebras GPT-OSS, Groq GPT-OSS,
local Ollama agent/vision models, optional OpenAI provider) as candidates for
the passive future router.

It does not change the current live failover chain.

Inspect it with:

```powershell
python -m jarvis_agent.kernel_cli model-catalog
```

## 39. Feedback Promotion Gate

`jarvis_agent/promotion_gate.py`

Human validation is necessary but not sufficient for promotion.

- CORE → never an automatic memory write; requires Dev Supervisor patch/test/replay.
- REGRESSION_TEST → controlled registry update.
- APP / DOMAIN / AGENT / SKILL / USER / SESSION → may become scoped knowledge only after evidence + tests/replay requirements + explicit user validation.

This prevents a user-specific correction from silently changing Core behavior.

## 40. Kernel foundations regression pack

`TEST-KERNEL-FOUNDATIONS`

The regression registry now contains a dedicated pack for Kernel/multi-agent
changes. The architecture validation runner performs:

1. Python syntax preflight
2. Kernel/foundation unit tests
3. architecture extension tests
4. historical Jarvis baseline regression

The acceptance rule remains:

**new foundations present + old live Jarvis behavior unchanged.**

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