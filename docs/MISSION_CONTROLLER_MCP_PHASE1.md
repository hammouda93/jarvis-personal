# Mission Controller V2 × MCP — phase 1 acceptance

This is an additive integration of the existing MCP Control Center/SDK with the
Mission Controller V2 evidence journal. It is **not** authorization to connect
accounts, send messages, change remote data, install software, or merge branches.

## Capability and consent boundary

- A server remains disabled until the operator explicitly enables it.
- Discovery exposes metadata only; each exposed tool requires allowlisting.
- Every MCP tool invocation requires an explicit approval at the runtime
  boundary. No previous authorization is transferable to a new invocation.
- Quota/cooldown and the prior-outcome-unknown activity lock remain active.
- A local denial or preflight error reports `dispatched=false`, so the mission
  does not mistake it for a potentially executed remote mutation.
- A post-dispatch error reports `dispatched=true, outcome_unknown=true`;
  never retry a remote effect until the ambiguity is externally reviewed.
- A server response with `success=true` is a **transport result**, not
  independent proof that a multi-step mission was completed.

## Mission evidence

The existing mission TaskGraph persists a **redacted local receipt**, not an
external response or tool arguments. A valid successful or uncertain dispatch
projects an `mcp_invocation` fact with only:

- server ID / tool name / locally reserved attempt ID
- reported tool status and outcome uncertainty
- independent verification flag `false`
- original mission step and goal revision

These facts can be inspected after restart. They **cannot** certify goal
completion or fulfill typed preconditions based merely on untrusted MCP output.
Do not add remote text, external JSON, credentials, message bodies, or access
tokens to the evidence ledger.

## Offline acceptance gates

Run the regression suites on both Ubuntu and Windows. Required checks:

1. Legacy Window/Browser/Memory behavior passes.
2. Disabled server, denied tool and missing per-call approval dispatch nothing.
3. Remote tool output never enters the durable evidence projection.
4. Missing/ambiguous post-dispatch result never becomes a success; it prevents
   automatic replay, including across mission restart.
5. Restart restores local attempt identity with `goal_proof=false`.
6. The MCP Control Center and Brain Manager remain usable and their settings
   are not silently changed by mission execution.

## Suggested first LIVE acceptance (only after green CI)

Use one **user-chosen non-destructive, read-only MCP service** in the existing
Control Center. Confirm the server is really connected; list the discovered
tools, allow exactly one safe tool, explicitly approve that particular call
and inspect the resulting mission evidence. Do not use a message-send/delete/
purchase/installation tool for the first LIVE test. Disconnect or disable the
service afterward if desired.

## Next development phase

- Mission capability discovery: select an allowlisted MCP tool only when it
  satisfies a known mission need, without promoting remote text to instructions.
- Read-only verification adapters for specific externally observable facts.
- Typed tool output contracts with provenance and least privilege.
- Cross-service missions with per-step authorizations and checkpoint recovery.
- Remote-operation resource budget and operator UI summaries.
- User-opted service connections (Gmail, Calendar, Drive, GitHub, etc.).
  Connection credentials or OAuth consent must be supplied by the operator;
  no implicit connection on import or Jarvis startup.

**Not yet LIVE verified. Never equate green CI with a successfully completed
mission on the user's own Windows session.**
