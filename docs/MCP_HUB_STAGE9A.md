# Jarvis Stage 9A — MCP Hub (Hermes-compatible architecture)

## Why now

Stage 9 includes the foundations of agent delegation and connector access. We
are first building the **independent MCP capability surface**, without replacing
Cerebras, the original Browser Bridge, UIA/CUA, mission checkpoints, Semantic
Memory V5 or learning/skills logic.

This is **not the completed multi-agent planner**. The roadmap remains at
**9/11 in progress**. Stages 1–8 were already integrated and passed automated
tests on their parent branches; user Windows acceptance is still pending.

## Hermes: use as reference and optional MCP provider

Hermes Agent supports configuration and automatic tool discovery from local
stdio MCP servers and remote HTTP MCP servers, per-server filters and various
authorization flows. Its CLI offers `hermes mcp serve` to expose a subset of
**Hermes conversations/messaging** functions to other MCP clients.

Jarvis uses the **official MCP Python SDK**, which is better than importing
Hermes' entire model/runtime into the existing Jarvis agent. If Hermes is
already installed and configured on the user's own Windows PC, Jarvis can
optionally add the *disabled* local stdio profile `hermes mcp serve` and
explicitly discover and allow its actual exported tools. That profile **does
not promise to proxy all other MCP servers attached to Hermes**.

No Hermes package, repository, model or agent server is auto-installed by
Jarvis, and no source code from Hermes is copied.

Official references:
- https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/mcp.md
- https://github.com/NousResearch/hermes-agent/blob/main/website/docs/reference/mcp-config-reference.md
- https://github.com/modelcontextprotocol/python-sdk

## Architecture (9A)

1. `MCPRegistry`: local per-user JSON file with server ID, transport, URL or
   approved Hermes-local executable, enabled flag, discovered **schemas** and
   per-tool approval state. No user tokens are persisted. Configuration writes
   are atomic.
2. `OfficialMCPTransport`: optional official SDK `ClientSession`, 
   `stdio_client` and Streamable HTTP transport. Sessions are opened ONLY on
   explicit discovery or a confirmed tool invocation. Timeouts are bounded.
   A result-unknown MCP operation is **never automatically retried**.
3. `MCPToolRegistry`: single adapter wrapping the original Jarvis tool
   registry. It exposes only a server's individually allowed discovered tools,
   namespaced `mcp__server__tool`. Name collisions fail closed.
   EVERY MCP invocation requires a fresh explicit user confirmation. A tool's
   claimed read-only metadata is not trusted to bypass the confirmation.
   The adapter is nested **inside the existing Hermes-inspired action
   checkpoint guard** when enabled; the old executor is unchanged.
4. `MCPControlInbox`: explicit configuration/discovery/tool selection
   commands serialized on the same assistant worker; no network operation on
   the Qt main UI thread.
5. UI section **MCP — SERVEURS INDÉPENDANTS** in **▤ Supervision** with server
   selection, independently enabled/discovered/allowed tools, HTTP URL entry,
   optional Hermes-local profile, and per-tool enable/disable buttons.
   Telemetry is read-only and never displays bearer tokens.

**Consent layers:** add server (disabled) → enable server → explicit discovery
(no tool execution) → enable individual tool → model may suggest that tool →
**user must still explicitly confirm each actual tool call**.

## Install (optional, once on the local Windows machine)

```powershell
cd D:\Django_Projects\jarvis-main\jarvis-main
C:\jv312\Scripts\python.exe -m pip install -r requirements-mcp.txt
$env:JARVIS_MCP_ENABLED = "1"
$env:JARVIS_HERMES_RELIABILITY_ENABLED = "1"
$env:JARVIS_RUNTIME_CONVERGENCE_ENABLED = "1"
# Start Jarvis normally in the same PowerShell session.
```

Do not include the `mcp` SDK in the base requirements until compatibility
has been proven on the user's Windows setup.

**Remote MCP URLs** must be HTTPS, apart from localhost loopback HTTP.
Authentication for already provisioned bearer-token servers can be supplied
in the local process environment using
`JARVIS_MCP_BEARER_<SERVER_ID_UPPERCASE>`. Never paste tokens into the URL,
UI, chat logs, GitHub or `mcp_servers.json`.

OAuth interactive flow, enterprise SSO, automatic provider token refresh,
the curated Hermes catalog and native ChatGPT connector token delegation are
**NOT** implemented in this stage. A ChatGPT-connected Gmail/GitHub/etc app
does **NOT** automatically grant Jarvis access to the same account; each
Jarvis MCP service needs its own explicit authorization and permissions.
Because different MCP providers support different auth schemes, no connector
is claimed active before an actual discovery succeeds.

**Local Hermes** requires a separate Hermes executable already installed
and ready to serve MCP. The configuration button itself never downloads or
launches it. Local command is intentionally fixed to `hermes mcp serve`
(no shell, no arbitrary stdio executable).
No remote MCP or Hermes connection has been authenticated during GitHub CI.

## Automated acceptance

`tests/test_mcp_hub.py`: no implicit server starts, no credentials stored,
HTTPS/localhost restrictions, per-server isolation, deny-by-default discovery,
per-tool allowlist, per-call approval, no-replay on uncertain tool call,
approval revocation, alias collision guard, preservation of native tools,
Qt per-server UI and read-only telemetry.

`.github/workflows/mcp-hub-stage9a.yml`: Windows critical Python regression
suite, Browser Bridge replays and Linux broad suite with the same four
historical Win32-specific exclusions as earlier stages. Fake MCP transports
are used; this is NOT a live remote authorization/integration test.

## Future 9B

Introduce protocol-compliant OAuth UX when providers require it, richer MCP
status/reconnect management, context-aware selection among tool capabilities,
and a safety-proof-aware autonomous planning/delegation engine. Jarvis skills
remain advisory and cannot hide other model-discoverable capabilities.
No extra agent will dispatch a second blind action.

No PR is merged before live Windows acceptance of Chrome, WhatsApp, generic
desktop, typed/voice behavior, memory and MCP provider-specific permissions.
