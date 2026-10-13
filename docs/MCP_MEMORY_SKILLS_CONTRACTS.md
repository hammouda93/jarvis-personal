# Jarvis: MCP, Memory V5, Skills and Learning — boundary contracts (design v1)

Status: design/compatibility contract only, **not a feature activation**. Baseline: `fix/mcv2-generic-argument-grounding-v1` at `fa10d6a`.

## Architectural invariants

1. One conversational runtime, one authoritative action executor and one permission boundary. MCP/API, Chrome, Windows, local memory and future cloud agents are interchangeable *sources of capabilities*, not alternate bypasses of authorization.
2. Discovery and observation convey facts, never authority. Neither MCP tool descriptions nor prompts/resources, stored memories nor skills may authorize an operation.
3. Every tool invocation must preserve: principal/account scope, tool/schema identity and version, operator consent, argument validation, dispatch state, side-effect uncertainty and provenance.
4. Model/provider fallback preserves the mission objective, unresolved requirements, evidence links and human permissions. A fallback model never adds permissions or replays uncertain effects.
5. A received tool response is not independent proof that the *mission* is complete. A missing/unqueried resource is **unknown**, not absent.
6. No automatic migration of observed MCP data into personal Memory V5 or operational lessons/skills. Distinguish transient mission facts, approved long-term personal memories and validated procedural knowledge.
7. Skills and operational learning are independent OFF-by-default features. OFF/OFF must retain the full basic toolset, mission planner and observational abilities. Memory V5 remains separate and subject to its own existing enablement and explicit-write policy.
8. No secrets, arguments, raw private tool results or credentials in the activity/mission safety ledger. Content that needs to survive restart requires a separate scoped/encrypted evidence store and a retention policy, not a widening of safety receipts.

## Owned modules (current)

- `mcp_server_registry.py`: enabled services, discovered schemas, allowlists and fingerprint-pinned read grants.
- `mcp_sdk_transport.py`: official SDK, OAuth/transport, MCP tool invocation; server response is untrusted data.
- `mcp_argument_grounding.py`, `mcp_tool_registry.py`: pre-dispatch contract/permission/quotas and local receipt creation.
- `runtime_convergence.py`, `mission_semantics.py`, `mission_evidence.py`: mission lifetime, goals and evidence projection; currently MCP ledger intentionally stores dispatch metadata, not full remote factual observations.
- `memory_core_store.py`, `semantic_memory_runtime.py`, `memory_projection.py`: durable raw personal notes, rebuildable semantic projections and indexing.
- `agent_knowledge.py`, `skill_control.py`, `operational_preferences.py`: versioned skills/lessons and separate operator ON/OFF.
- `knowledge_broker.py`, `knowledge_policy.py`, `scoped_knowledge_store.py`: future scoped knowledge sharing and access checks.
- `capability_registry.py`, `connector_gateway.py`, `mission_orchestrator.py`: logical capability and delegation contracts, not yet a substitute for the live executor.

## Proposed shared data contracts (not yet implemented)

### CapabilityDescriptor
Immutable reference to backend, server/tool ID, schema fingerprint, input/output schemas, scope, risk class, required consent, availability and discovery timestamp. Tool-selection visibility is NOT permission.

### ToolExecutionReceipt
Opaque action/attempt ID, capability fingerprint, principal/scope, validation status, dispatch state (not_sent/sent/unknown), operation class (read/possible_mutation), result success, verification level, receipt timestamp and optional content reference. A confirmed server error for an operator-vetted read is recoverable; transport timeouts and possible writes remain uncertain.

### MissionObservation
Content reference and safe typed fact(s), source URI/ref or tool attempt, observed_at, trust level, retention/expiry, owner scope, and completeness/truncation indicators. Present the original content only through scoped on-demand retrieval; no mutation or privilege may be inferred from it.

### EvidenceBinding
Mission ID and step ID, required predicate, observation ref, validation status, independent corroboration (when required), and last checked time. Successful tool call alone cannot satisfy a goal. Unknown/unqueried sources remain explicitly unknown.

### OperationalKnowledgeCandidate
Evidence references, proposed procedure, version, scope, approval/testing state and reversible promotion history. Learning OFF prevents automatic candidate creation/promotion; Skills OFF prevents skill injection/execution, not tool use.

## Data-flow example (GitHub is only a test fixture)

Natural-language repository review -> discover allowed MCP capabilities -> if necessary use an authorized account-identity read -> resolve actual target repository owner from observed metadata -> validate requested tool arguments against discovered schema -> call read tools -> record source-labeled observations -> verify commits, PRs, CI separately -> answer only from consulted sources.

This pattern must work unchanged for other MCPs, Calendar/Drive, local app APIs and Windows/browser observations.

## Compatibility gates before enabling future capabilities

- Schema checks: arrays, nested objects, constraints, local refs, no external schema-reference fetch, no coercion and no leaked values.
- Safety: denied tool, unknown dispatch, failed observable read, write with partial outcome, restart/replay lock and quota/cooldown behavior.
- Evidence: incomplete/truncated response, mixed-source contradictions, no false absence, read-after-restart with correct owner scope.
- V5: explicit memory writes only, raw survives projection failure and account disconnection, no automatic promotion from MCP or skills.
- Skills/Learning OFF/OFF: no context injection or learning writes, with normal MCP/Windows/browser/memory use unaffected.
- Future ON modes: skill provenance/version/expiry, operator review, no privilege escalation or learning from untrusted instructions.
- Cloud/multi-user: isolate identities, credentials, scoped knowledge and context; preserve audit/proof on retries.

## Release policy

Implement changes in isolated, reviewable layers. Unit/contract tests on fake MCP servers first, Linux/Windows CI next, then real Windows LIVE acceptance. Do not merge to stable or alter user worktrees until the acceptance is verified. Keep prior feature branches and personal data unchanged.
