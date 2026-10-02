# Jarvis local learning and visual fallback

Jarvis keeps personal memory and operational learning separate.

## Local files

By default on Windows:

- `%LOCALAPPDATA%\JarvisPersonal\memory.sqlite3`
  - personal facts
  - written only after an explicit user memory request
- `%LOCALAPPDATA%\JarvisPersonal\agent_knowledge.sqlite3`
  - reusable skills
  - behavioral lessons
  - local application profiles
  - recent execution/proof records
- `%LOCALAPPDATA%\JarvisPersonal\evidence\`
  - optional visual debug screenshots
  - disabled by default

Git updates do not overwrite these files.

## Operational knowledge

### Skills

A skill is a reusable procedure, not a recording of a private conversation.
A skill can be saved only after the current turn contains a verified mutation.

Stored fields include:

- generic name and goal
- optional app scope
- abstract procedure
- observable success checks
- known failure patterns
- confidence and success/failure counters
- version/timestamps

Do not store contact names, message contents, secrets, passwords, tokens or
fixed screen coordinates in a skill.

### Lessons

Lessons capture reusable corrections such as:

- a confirmation is not a request to repeat the previous mutation
- contact search is different from a message composer
- a UI ref is not a result ranking

The runtime exposes lesson writing only on turns that look like clear user
corrections, and still validates the call.

### App profiles

Jarvis can remember locally observed application aliases, window-title
patterns, capabilities and a reusable launch hint. When a valid learned launch
path still exists, Jarvis can reuse it before rescanning Windows.

### Execution proofs

Action turns are recorded with a compact proof ledger. A run is marked verified
only when the runtime sees a suitable after-state/self-verification signal.
This is used for auditing and for deciding whether a reusable skill may be
learned.

## Automatic retrieval

Before a GPT-OSS turn, Jarvis searches the local operational store for relevant
skills, lessons and app profiles. Matching knowledge is injected as ephemeral
system context and removed from normal conversation history afterwards.

The injected knowledge is guidance, not truth about the current screen. Jarvis
must still inspect/observe the current state.

## Local visual fallback

`observe_screen` is a fallback sensor for cases where UI Automation is
ambiguous or incomplete.

Default pipeline:

1. capture the target window in memory
2. send the image only to the configured local Ollama endpoint
3. use `gemma3:latest` by default for visual description
4. return a textual observation to GPT-OSS
5. discard the image

Screenshots are not saved by default.

Environment options:

- `JARVIS_VISION_ENABLED=true`
- `JARVIS_VISION_MODEL=gemma3:latest`
- `JARVIS_VISION_TIMEOUT_S=20`
- `JARVIS_VISION_MAX_WIDTH=1600`
- `JARVIS_VISION_LOCAL_ONLY=true`
- `JARVIS_VISION_SAVE_EVIDENCE=false`
- `JARVIS_VISION_EVIDENCE_MAX_FILES=30`

With local-only enabled, Jarvis refuses to send screenshot vision requests to a
non-localhost Ollama endpoint.

## Inspecting and sharing knowledge safely

Show counters:

```powershell
python -m jarvis_agent.knowledge_cli stats
```

Create an anonymized diagnostic export:

```powershell
python -m jarvis_agent.knowledge_cli export --output jarvis_agent_knowledge_export.json
```

The default export removes raw recent user goals and local launch hints and
redacts common emails, secrets, phone-like values and Windows user paths.

Do not share the raw SQLite databases unless specifically needed for local
debugging. Prefer the anonymized JSON export plus the Jarvis runtime log.

A raw export can be created deliberately with `--raw`, but it should stay
private.

## One-command diagnostics

From the project root:

```powershell
.\scripts\run_learning_diagnostics.ps1
```

This creates a timestamped `jarvis_diagnostics_...` folder containing the
current Git commit, knowledge counters, anonymized knowledge export, Ollama
model list and unit-test log. The generated `SHARE_THESE_FILES.txt` lists the
safe files to share.

Do not share `.env`, raw SQLite databases or raw evidence screenshots by
default.
