# V11A — Unified mission entry (incremental, not full autonomy)

Base: V10K `4136996`. No merge or protected checkpoint rewritten.

## Implemented

- Desktop assistant explicitly builds the existing live runtime with mission continuity enabled, without enabling an additional executor or granting tools new permissions. Existing Memory V5, Browser Bridge, computer use, Cerebras/Groq/extra brain cascade, MCP registry, and action ledgers remain in their original order.
- Generic multi-step goals and deep-research requests are admitted to a persistent mission without requiring a manual "Create mission" button. Simple requests retain the proven direct, inexpensive path.
- Optional **one** model-generated abstract plan is stored automatically for nontrivial admitted missions. It is NOT executed or approved by planning. The existing conversational executor still performs the work; no safe verifier, approval or proof mechanism is bypassed.
- The operator screen defaults to read-only mission status, bounded live mission log, **existing separate MCP Services tab**, and tool receipts; manual plan editing and active supervisor controls are hidden behind optional technical diagnostics. Skills/Learning panels are not displayed and the existing settings remain unchanged.
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

## MCP navigation note

MCP's existing dedicated Services tab is authoritative. Do not reparent the MCP widget into the operator console: it was found to break the Services tab and cause horizontal overflow on the 1024px Qt layout. The mission console stays a read-only view; account connection/permissions remain in Services.

## Mission context recovery — general rule

Do not special-case YouTube or any application. A failed read-only sensor call is not evidence of an external mutation; preserve the original goal and permit the next turn to observe current state. A definitely refused pre-dispatch call is similarly non-mutating. Unknown/possibly executed mutations remain BLOCKED for independent review. Chrome DOM observation now reports `ready:false` during initial navigation rather than throwing on the still-empty tab URL; the adapter performs a small bounded number of read-only observations. A fresh user continuation can use the persisted mission and the existing browser grounding. Full unattended rescheduling and semantic goal verification remain future work.

## Generic cross-capability domain context (MS Football live finding)

Real desktop logs showed an already open browser UI, but a business domain follow-up selected only database schemas and rejected `browser_find` / `browser_click` as unavailable. The cause was `_tool_definitions(ms_football_only=True)` discarding the actual browser capability surface, then the efficient context catalog offering only the now-filtered schemas. Fix the **generic contract**: a domain adds its business APIs while the already active browser scope retains DOM discovery and observation/click tools. Model instructions prioritize the observed UI for UI requests, while a data question still uses the business API. This does not target a specific site, menu label or URL. In feedback-only turns mutating browser tools remain hidden; all original approvals and dispatch safeguards remain intact.

## Post-acceptance general recovery (2026-10-12)

Live user logs showed a browser command successfully reaching a real page, but the
following model request exhausted Cerebras quota, and the fallback budget rejected
a lengthy transcript. A **model-only** `AgentRuntimeUnavailable` is now recorded
as a resumable checkpoint with partial tool names if and only if no uncertain
failed effect or unknown tool scope exists. A later user turn must first observe
the current actual state; it must not replay the earlier side effect. Other
exceptions (timeouts, transport uncertainty, storage issues) retain the BLOCKED
safety gate. Zero-result DOM finds additionally return a short **fresh observed
control inventory** without a click, instead of misleading Jarvis that a
capability is missing. This does not solve API quota or deliver autonomous
multi-turn scheduling by itself.

## Evidence-linked trace and bounded stale DOM reads

Trace projection can correlate a **verified browser navigation** to the label of a unique DOM link previously returned by `browser_find`, provided the exact observed link href equals the actual verified destination and the positive user step contains the label. It logs `EVIDENCE_CORRELATED` but never certifies a task, an optional action, or an entire mission. No browser call or model request is added. With efficient context enabled, an older successful browser DOM observation on a tab may be archived when a later **verified** browser action supplies a fresh post-observation for that same tab. It remains recoverable by evidence ID; mutation receipts, unknown effects, current snapshots and permissions are not compressed.

## Exact browser navigation evidence in user replies

When the user explicitly asks Jarvis to verify or explain the resulting page, the agent may add a short, exact destination receipt from a successful browser mutation with `verified=true`, `postcondition=navigation_observed` and a real post-observation HTTP(S) URL. This is evidence of the **navigation only**, not proof the entire mission is completed. If the model already includes the exact URL, no duplicate receipt is added. No website-specific paths or selectors.
