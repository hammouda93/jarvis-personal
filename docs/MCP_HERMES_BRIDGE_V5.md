# Jarvis V5 — MCP multi-connector hub and Hermes compatibility

## Development position: stage 9/11 — foundations, not autonomous delegation

Previous base: `feature/semantic-goal-supervisor-v4`
@ `daf178faa8a3a86534c1835a1a08466d625b000d`.
Protected stable `fix/browser-live-contracts-v2` unchanged.
This design deliberately does **not** install or import the Hermes Agent runtime.

## Why Hermes as OPTIONAL backend, not the core executor

Hermes Agent (NousResearch, MIT) already supports a mature MCP client,
catalog discovery, local stdio transports, remote HTTP, OAuth, tool filters,
per-server health and tool previews. The reference implementation's MCP
tool modules depend on its own CLI, transport lifecycle, OAuth vault, profile
and agent event loop. Direct wholesale copy into Jarvis would create a
second lifecycle and duplicate authorities. Original architecture and test
contracts must remain intact.

There **is** a narrow documented Hermes MCP server interface:
`hermes mcp serve` (stdio), with ten messaging/channel tools, including
`messages_read`, `messages_send`, `channels_list`, and approvals.
Jarvis can connect to this only when the user has *independently installed
and configured Hermes* (and the appropriate messaging gateway is available).
That bridge is NOT a universal proxy for every Hermes MCP server. In
particular, it does not automatically expose Google Maps or Sheets.

Reference:
https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp

MIT license: https://github.com/NousResearch/hermes-agent/blob/main/LICENSE
All new V5 bridge code is original Jarvis code and imports none of Hermes's
files, so no third-party source was vendored into the repository.

## Implemented in V5

- **Separate ▤ MCP panel** for per-service settings. Suggested categories:
  Gmail, Sheets, Maps, Drive, WhatsApp, GitHub. These are **templates**, not
  live accounts, verified integrations, or included OAuth credentials.
- Add a remote HTTPS MCP server with unique ID, label, explicit allowlisted
  tool names, optional environment-variable auth reference. New entries
  start **disabled**. User can enable/disable each one separately.
- The UI never prints token values and never imports credentials or OAuth
  permissions from this ChatGPT conversation. It does not launch any service
  when it opens.
- An explicit **Tester la connexion** connects through the Python MCP SDK,
  initializes a session and lists only allowlisted tools. It runs on a
  daemonized background thread, never the voice/model worker thread, with
  bounded timeout. **No tool is executed during a probe**.
- Separate **Tester Hermes MCP** button connects to the documented
  `hermes mcp serve` stdio bridge if the user has selected the existing
  executable. The ten known messaging tools are the hard ceiling.
  The bridge receives a restricted subprocess environment.
- The **MCPClientBridge** internal programmatic call path is not exposed as
  a free-form ChatGPT/Groq/Cerebras tool. If invoked by a trusted controller
  it requires explicit approval, strict tool allowlist, and a durable
  pre-action HMAC checkpoint. Ambiguous timeout outcomes become `unknown`
  and cannot be blindly retried. UI has no send/publish buttons yet.
- `MCPHub` rejects HTTP/plaintext URLs, userinfo, URL query tokens,
  loopback/private IP literals and unsafe inherited configurations. Only
  remote HTTPS is available through the current Jarvis UI.
- Both transports are **optional**; the original Jarvis runtime retains
  authority and its earlier browser, desktop, memory, voice and Skills
  functionality is unchanged.

## What is NOT delivered

- Automatic OAuth setup or importing ChatGPT's connected accounts.
- Google Sheets / Maps / WhatsApp **connected and usable by default**.
- Automatically selecting newly discovered tools in the Cerebras brain.
- Hermes as a second LLM or full planning engine.
- Hermes WhatsApp Cloud Business authorization or personal WhatsApp account
  login. The business API requires a separate supported Meta setup.
- Automatic tool dispatch or independent goal proof from a successful MCP call.
- Generic local stdio installers / shell commands. Hermes is the only
  allowed manual bridge launcher, and only `hermes mcp serve`.
- Multi-agent orchestration and supervisor dispatch (subsequent work).
- Privacy and security acceptance on the user's real Windows setup.

## Optional Python dependency

`pip install -r requirements-mcp.txt` supplies the official MCP SDK and
httpx for HTTP tool servers. No dependency is installed or downloaded by
the app merely from opening the MCP tab. All automated tests use isolated
in-process fake transports and exercise zero external accounts.

If the selected Hermes executable is absent, the UI must report a
missing/unavailable probe. It MUST NOT silently install Hermes or execute a
different shell command.

## Upcoming gates

1. Green full CI for the exact branch HEAD: Windows Python, Linux Python,
   Browser Bridge Node, and dedicated MCP/Hermes no-network tests.
2. Explicit real Windows user testing (when the owner chooses): UI,
   verified remote MCP handshake, per-server OAuth login (future), real
   Hermes installation/channel authorization, permission/review states,
   no duplicate external sends after timeout.
3. Before exposing actions to Cerebras's tool definitions: central
   supervisor authorization, one-time scoped approval tickets, sanitized
   result routing, full audit and independent goal verification. Skills
   remain advisory and never limit newly discovered native capabilities.

Roadmap: stages 1–8 code/CI verified previously; stage 9 MCP foundations
under development. All real Windows acceptance gates are still pending.


## Automated checks, initial V5 branch result

GitHub Actions V5 run `37790071623`, on the implementation HEAD before
final roadmap copy changes: **445/445 Python Windows**, **45/45 Browser
Bridge Node**, **626/626 Python Linux** passed.

The current milestone remains **stage 9 in progress** because the
autonomous planner, centrally verified permission tickets, seamless OAuth,
Cerebras MCP-tool routing and true third-party end-to-end connections are
not yet delivered. CI of the MCP foundation does not claim those features
are complete. The real Windows acceptance gate is still open.

Official Hermes server exposes only its messaging tool surface over
`hermes mcp serve`; for Google Maps/Sheets, Jarvis's own remote MCP
connection remains necessary unless a future explicit hosted relay is
implemented.
