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

## Bounded multi-query web research

Optional model-authored `research_web.subqueries` (up to 3) now triggers a bounded sequence of Groq server-side browser searches. Jarvis aggregates per-query answers, URL-deduplicates source receipts, notes the executed queries, and aborts further queries on reported quota/auth/billing failures. Quick single-query search remains the default. This is a first multi-search increment; it does **not** independently download and verify page/PDF bodies, and each provider call may consume a quota or incur charges. Deep Research source reading, cross-provider fallback and explicit monetary limits remain outstanding.

## Normal-profile Chrome regression guard (V11A hotfix)

A compound command with comma-separated browser stages, e.g. `Ouvre YouTube, recherche Messi, ouvre la première vidéo`, must allow the **initial** `open_url` and continue to require a fresh observed DOM reference for selecting the video. The existing Native Browser Bridge drives the user's **same normal Chrome profile**; no Playwright profile, copy, CDP switch, or manual browser startup is introduced. Also distinguish an `open_url` refusal made **before dispatch** from an unknown external effect: only that exact local guard refusal can continue on the next turn; real tool failures and uncertain effects remain BLOCKED. Regression tests protect both cases.
