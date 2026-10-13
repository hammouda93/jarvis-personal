# Jarvis — Personal MCP Connection Plan (2026-10-13)

**Purpose:** configure the operator's actual tool ecosystem without inventing
servers or granting consent. This is an inventory and onboarding surface, not
an automatic account-connection script.

## Identity and status contract

- *Unconfigured* = no matching local MCP server registration.
- *Registered, disabled* = a local profile only, not an active connection.
- *Enabled, not tested* = transport has not been observed working.
- *Discovered* = the last successful discovery is on record, **not** proof
  of an active live session or permitted mutation.
- *Approved tools* = only the explicitly allowlisted tool schemas; each
  execution still requires an operator action as enforced by the MCP registry.
- *API bridge needed* = no reviewed direct MCP endpoint configured for this
  service. It may require a custom owner-hosted API/MCP adapter.
- *Native* = already a capability of Jarvis Windows/Chrome; no MCP required.

The GUI **Mon espace** shows these states without making a network call. A
button may fill the existing *Ajouter* screen for documented HTTP endpoints,
but **does not** add, enable, connect, grant scopes, store keys, discover
tools, launch local software or perform account login.

## Recommended real connection sequence

| Stage | Services | First acceptance |
|---|---|---|
| 0 — regression gate | Mission Controller V2, browser/Windows/memory/cascade | Windows + Linux CI and safe local checks |
| 1 — first read-only MCP | GitHub | Fetch repositories, branches, commits, PR/CI; no writes |
| 2 — Workspace as available | Gmail, Calendar, Drive, Docs, Sheets, Slides, People | Read/search only; no sending, posting, editing |
| 3 — deployment & data | Cloudflare, Heroku API, PostgreSQL read-only | Diagnostics without configuration edits |
| 4 — business | MS Football Django, coaching_app, Coach Workspace | Scoped read APIs, separate tenant and role access |
| 5 — communications & football media | WhatsApp, YouTube, SportsBase, Dailymotion | Read and draft; publish/send requires per-action review |
| 6 — quality-of-life | Maps, research, reminders, prayer and Quran data | API budgets, sources, timezones, optional extra services |

### Reviewed official remote MCP URLs (server-specific eligibility applies)

- GitHub: `https://api.githubcopilot.com/mcp/`
  - Documentation: https://github.com/github/github-mcp-server
  - Fine-grained PAT; begin with repository metadata read scopes.
- Google Workspace, **Developer Preview prerequisites apply**:
  - Gmail: `https://gmailmcp.googleapis.com/mcp/v1`
  - Calendar: `https://calendarmcp.googleapis.com/mcp/v1`
  - Drive: `https://drivemcp.googleapis.com/mcp/v1`
  - Docs: `https://docsmcp.googleapis.com/mcp/v1`
  - Sheets: `https://sheetsmcp.googleapis.com/mcp/v1`
  - Slides: `https://slidesmcp.googleapis.com/mcp/v1`
  - People: `https://people.googleapis.com/mcp/v1`
  - Documentation: https://developers.google.com/workspace/guides/configure-mcp-servers
- Google Maps Grounding Lite: `https://mapstools.googleapis.com/mcp`
  - API key restrictions and billing/quota must be inspected first.
  - Documentation: https://developers.google.com/maps/ai/grounding-lite
- Cloudflare API MCP: `https://mcp.cloudflare.com/mcp`
  - OAuth and scoped permissions; no write scopes for first tests.
  - Documentation: https://developers.cloudflare.com/changelog/post/2026-10-13-cloudflare-mcp-events/

Do **not** treat these URLs as proof that a user account is eligible or
connected. Heroku's MCP hosting offering does not automatically mean the
operator has a remote account-management MCP endpoint. Similarly, neither
personal WhatsApp, SportsBase nor owner Django applications are assumed to
expose compatible authorized public MCP servers.

## Operator procedure

1. Complete CI and retrieve the appropriate integration branch.
2. Start Jarvis normally, open **MCP Control Center → Mon espace**.
3. Choose **GitHub**, click **Préparer la configuration**. This merely
   pre-fills **Ajouter** with a reviewed remote endpoint.
4. **Explicitly** register and enable the profile, provide a limited
   credential using the OS-backed vault, then test connectivity/discovery.
5. Allow only a known **read-only** tool and approve its one test call.
6. Compare read result to GitHub. Verify the mission checkpoint shows an MCP
   invocation, not fabricated mission completion.
7. Continue service by service; postpone any side-effecting tools until
   read-only and permission tests are demonstrably reliable.

**Never** paste tokens or OAuth secrets into the chat, GitHub commits, or
Jarvis raw logs. Keep the dynamic OAuth login in the operator UI. No Windows
PC or personal Google/GitHub account has been connected by committing this
feature branch.
