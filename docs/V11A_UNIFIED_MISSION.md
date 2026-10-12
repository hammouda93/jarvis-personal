# V11A — Unified mission entry (incremental, not full autonomy)

Base: V10K `4136996`. No merge or protected checkpoint rewritten.

## Implemented

- Desktop assistant explicitly builds the existing live runtime with mission continuity enabled, without enabling an additional executor or granting tools new permissions. Existing Memory V5, Browser Bridge, computer use, Cerebras/Groq/extra brain cascade, MCP registry, and action ledgers remain in their original order.
- Generic multi-step goals and deep-research requests are admitted to a persistent mission without requiring a manual "Create mission" button. Simple requests retain the proven direct, inexpensive path.
- Optional **one** model-generated abstract plan is stored automatically for nontrivial admitted missions. It is NOT executed or approved by planning. The existing conversational executor still performs the work; no safe verifier, approval or proof mechanism is bypassed.
- The operator screen defaults to read-only mission status, bounded live mission log, **visible MCP control panel**, and tool receipts; manual plan editing and active supervisor controls are hidden behind optional technical diagnostics. Skills/Learning panels are not displayed and the existing settings remain unchanged.
- Live structured logs contain objective and step hints, tool names and received evidence levels. Raw tool argument bodies are *not* copied into the new console journal.

## Important unfinished work

1. This is an integration milestone, **not** end-to-end autonomous Mission OS. The original executor still has bounded model rounds, and complex multi-turn missions can still need recovery. A draft plan is not itself execution; the supervisor currently requires independently configured observation rules to claim semantic completion.
2. Generic **Deep Research** must be built separately: search provider pluralism, autonomous subquestions, document fetching and citations, evidence ledger, source conflict detection, adaptive stopping, budgets. Current `research_web` remains a single Groq search request, at most two per turn.
3. Mission switching/terminal classification needs a trusted goal-level controller. Merely detaching is not proof and an unresolved action must never be replayed.
4. The new visibility and automatic admission require Windows + Qt + real-provider live acceptance. V10K inherited three Windows artifact verification CI failures. No claim that end-to-end desktop tests have passed.
5. MCP servers/accounts still require explicit connection and tool-by-tool permissions. Do not assert Gmail/Calendar are connected. MS Football bridge is preserved; business-agent semantics are a subsequent task after inspecting the app code.
6. Skills/Learning remain OFF by default; no new training or skill dependencies.
