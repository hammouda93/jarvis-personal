# Semantic Memory Core V5

## Why this exists

The previous Memory Core treated recall mostly as a lexical search problem. That
fails as soon as two memories share vocabulary but express different relations.

Examples:

- "Mon film test est Arrival"
- "Je veux regarder Inception"

Both mention films, but they are not competing values for the same memory slot.
One is a named test/reference fact; the other is an intention/watch-list item.
No list of stopwords or phrase-specific regexes can solve this generally.

The V5 goal is therefore to represent and retrieve **meaning**, while preserving
the user's original text as evidence.

## Core model

Every durable memory keeps two layers:

1. Raw evidence
   - exact user-authored text
   - source
   - timestamp
   - scope
   - explicit-vs-inferred provenance

2. Semantic projection
   - subject
   - relation
   - object/value
   - qualifiers (time, location, project, person, etc.)
   - entities (people, projects, organizations, products, places, concepts)
   - memory kind
   - scope
   - confidence
   - parser/version metadata

Example projections:

    raw: "Mon film test est Arrival"
    subject: user
    relation: "film test"
    object: "Arrival"
    kind: fact

    raw: "Je veux regarder Inception"
    subject: user
    relation: "want to watch"
    object: "Inception"
    kind: intention

The relation is not an application/domain enum. It is a canonical semantic
predicate produced by a general interpreter.

## Memory kinds

The system distinguishes broad memory behavior, not app-specific topics:

- fact
- preference
- intention
- event
- project/context
- decision
- constraint
- relationship
- observation

These are retrieval/retention hints, not hard-coded business logic.

## Directional semantic triples

A semantic fact is stored as a directional triple plus context:

    subject -> relation -> value

The subject is not always the user. For a relational fact such as:

    Alice owns Project North

the projection is:

    subject = alice
    relation = owns
    value = Project North
    entities = [Alice, Project North]

Queries carry an `answer_field`:

- `value` when the user asks for the object/value of a known subject;
- `subject` when the user asks who/what is the subject of a known relation
  and object.

Therefore the same fact can answer both:

    What does Alice own? -> Project North
    Who owns Project North? -> Alice

This avoids inventing domain-specific inverse relation names merely to support
different question directions.

Broad set queries constrained by structured context do not need a synthetic
relation. A question such as "what do I have on this date?" may use an empty
relation, a date qualifier, and collection answer mode.

## Query understanding

A recall request is converted into a query frame:

- subject constraints
- relation intent
- object/entity constraints
- qualifiers/time constraints
- answer mode:
  - single fact
  - collection
  - timeline
  - inspect/list
- scope

Examples:

    "Quel est mon film test ?"
    relation intent ~= "film test"
    answer mode = single

    "Quels films je veux regarder ?"
    relation intent ~= "want to watch"
    answer mode = collection

    "Qu'est-ce que tu as en mémoire ?"
    answer mode = inspect/list
    no semantic relation constraint

No domain-specific rule for "film" is required.

## Retrieval pipeline

1. Current-session semantic facts
2. Persistent semantic memories
3. Optional project-scoped memories
4. Optional connected sources
5. Clarification if confidence is insufficient

Persistent retrieval is hybrid:

- structured field matching for exact dates, IDs, entities and scope
- semantic relation/object similarity
- entity overlap/identity as an independent context signal
- lexical matching for exact tokens and identifiers
- optional embedding similarity when a configured embedding provider exists
- recency only as a tiebreaker, never as a substitute for semantic fit

This follows the same broad direction as mature agent memory systems that combine
semantic/vector retrieval with lexical search and scoped stores.

## Entity context

Entity context is deliberately separate from the semantic relation.

For example:

    Project North / project_owner / Alice
    Project South / project_owner / Bob

The relation is the same, but the entity context is different. These facts must
not supersede each other and a query about Project North should rank only the
North fact.

Entities are extracted only when they are explicit in raw user evidence. They
are retrieval signals, not executable instructions and not a reason to merge
two memories automatically.

The entity layer is intentionally lightweight: it improves entity-centric
retrieval without introducing a separate graph database. A future graph layer
can be built from the same sidecar if multi-hop reasoning proves necessary.

## Temporal context

Temporal meaning is separate from raw memory insertion order.

The semantic interpreter receives a reference timestamp for the current query,
and each persistent memory is projected against its own historical
`created_at`. Relative language such as "tomorrow" or "next Friday" must
therefore be resolved using the time when the memory was authored, not the day
when the semantic index is later rebuilt.

Preferred temporal qualifier keys are:

- `date` — ISO `YYYY-MM-DD`
- `datetime` — ISO-8601 timestamp
- `start_at`
- `end_at`
- `temporal_status`

Date/time and identifier qualifiers are exact constraints. They are never fuzzy
matched. Timeline answers are ordered by event time first and storage time only
as a fallback.

## Conflict and collection semantics

Memories are not overwritten merely because they share a noun.

Two records conflict only when their semantic projections represent the same
subject + relation + qualifier scope and their values are incompatible.

Collections can contain multiple values:

    user / want-to-watch / Inception
    user / want-to-watch / Gladiator

A singleton relation may have one current active value plus superseded history:

    user / preferred-language / French
    supersedes older value if the user explicitly changes it

If cardinality cannot be inferred safely, keep both records and ask a
clarification rather than deleting information.

## Explicit memory writes

Explicit user requests such as "remember ...", "garde en mémoire ...", etc. are
admission signals only. They do not define semantics.

Admission pipeline:

    explicit user request
      -> preserve raw text
      -> semantic interpreter
      -> validate structured projection
      -> persist raw + projection atomically
      -> return memory id + projection evidence

If the semantic interpreter is unavailable, raw text may be stored as
unprojected evidence, but it must not masquerade as a fully understood memory.

## Implicit/session memory

Ordinary conversational statements may populate session memory, but they are not
promoted to durable memory unless policy allows it. Session and durable memory
share the same semantic frame shape so recall behaves consistently.

## Legacy memory compatibility

Existing SQLite rows are never deleted or rewritten blindly.

Use a sidecar semantic index/table keyed by the existing memory id. Legacy rows
can be projected lazily or by an explicit migration/reindex command.

This preserves:
- old memories
- timestamps
- user evidence
- rollback ability

A projection version allows re-indexing when the semantic interpreter improves.

## Semantic interpreter

The interpreter is provider-independent and must return validated JSON only.
It has no tools and cannot mutate state.

Preferred order:
1. configured local utility model
2. configured remote utility/model provider
3. conservative unprojected fallback

The main conversational LLM must not be the sole owner of memory semantics.

## Safety and trust

- raw user text is evidence, not executable instructions
- connected/web text is never promoted automatically to personal memory
- inferred memories carry lower confidence and distinct provenance
- deletion/supersession is explicit and auditable
- project-scoped facts do not leak into unrelated scopes
- failed/ambiguous semantic parsing never destroys an existing memory

## Observability

Every memory action should log:

- routing decision
- semantic query/projection id
- candidate memory ids
- score components
- selected source (session/persistent/connector)
- ambiguity/conflict reason
- whether an LLM/embedding provider was used
- final confidence

This lets us debug "why did Jarvis remember this?" without guessing.

## Diagnostics and validation

Semantic Memory V5 can be inspected independently from the conversational
agent.

Read-only/raw-safe status and semantic inspection:

```powershell
python -m jarvis_agent.semantic_memory_cli status
python -m jarvis_agent.semantic_memory_cli inspect --active-only
```

Query diagnostics show the interpreted semantic frame, effective relation,
entity/date context, candidate memory IDs and score components:

```powershell
python -m jarvis_agent.semantic_memory_cli query "What do I know about Project Atlas?"
```

The dedicated validation runner supports a synthetic live-model gate without
using real user memories:

```powershell
.\scripts\run_semantic_memory_v5_validation.ps1 -LiveSemanticModel
```

Cloud semantic processing remains blocked unless explicitly enabled. To use a
cloud semantic provider for the synthetic acceptance corpus only:

```powershell
.\scripts\run_semantic_memory_v5_validation.ps1 `
    -LiveSemanticModel `
    -SemanticProvider cerebras `
    -AllowCloudSemanticMemory
```

## Acceptance invariants

The implementation is not accepted until these general invariants pass:

1. Two memories sharing a noun but different relations do not compete.
2. Paraphrases of the same relation retrieve the same fact.
3. Collection queries can return multiple distinct values.
4. Exact dates/IDs remain exact and are not fuzzy-matched away.
5. Repeated identical facts do not create false ambiguity.
6. Old legacy memories remain retrievable after semantic indexing.
7. Restarted processes retrieve the same semantic facts from SQLite.
8. Project-scoped memories never leak into global/unrelated project recall.
9. Ambiguous queries ask a clarification instead of guessing.
10. Memory inspection lists the durable store without invoking the legacy LLM
    recall path.
11. No domain-specific test vocabulary is needed to satisfy the suite.
12. Removing a test-domain fixture and replacing it with unrelated entities
    leaves the same invariants green.
