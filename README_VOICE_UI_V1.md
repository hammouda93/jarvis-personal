# Jarvis Personal — Voice + Presence V1

This branch adds the first real vertical slice of Jarvis without deleting the legacy `jarvis.py`.

## Real flow

1. Launch `python run_jarvis.py`.
2. Jarvis opens its reactive Presence UI.
3. The microphone calibrates for about 1.2 seconds.
4. Double clap.
5. Jarvis says **"Oui monsieur ?"** with ElevenLabs.
6. Jarvis records one spoken request.
7. Faster-Whisper transcribes it locally.
8. The deterministic V1 router selects a typed tool.
9. The tool acts on Windows / the web.
10. Jarvis answers vocally.
11. The UI returns to the wake state.

## V1 commands

- `ouvre YouTube`
- `ouvre Google`
- `ouvre Chrome`
- `ouvre Spotify`
- `ouvre Cursor`
- `ouvre VS Code`
- `ouvre Téléchargements`
- `quelle heure est-il ?`
- `recherche <texte> sur Internet`
- `arrête Jarvis`

Unknown requests are not silently executed. They are acknowledged as unsupported.

## UI states

The orb reacts to:

`STARTING → CALIBRATING → WAKE → SPEAKING → LISTENING → TRANSCRIBING → UNDERSTANDING → ACTING → SPEAKING → SUCCESS/ERROR`

The microphone and TTS audio levels also drive the visual core/waveform.

## Configuration

Existing ElevenLabs values stay in `.env`.

Optional:

```env
JARVIS_INPUT_DEVICE=1
JARVIS_OUTPUT_DEVICE=5
JARVIS_WHISPER_MODEL=base
JARVIS_WHISPER_DEVICE=cpu
JARVIS_WHISPER_COMPUTE_TYPE=int8
JARVIS_UI_FULLSCREEN=0
```

Leave `JARVIS_STT_LANGUAGE` empty for automatic language detection.

## First run

The first STT request may take longer because Faster-Whisper downloads the local model the first time. Later runs reuse the model from the local cache.

## Architecture

This V1 deliberately separates UI, voice, STT, tools and state. A future `AIProvider` can therefore be added without rewriting the Windows tools or the Presence layer.


## Voice recognition tuning (V1.1)

The recorder now keeps a 600 ms pre-roll, so speaking immediately after
"Oui monsieur ?" no longer drops the beginning of the command. Voice onset is
adaptive and more sensitive, while requiring a short sustained onset to reject
clicks.

Faster-Whisper's second VAD is disabled because Jarvis already segments the
utterance itself; this avoids dropping very short commands. If automatic
language detection produces an unknown command, Jarvis retries once in
`JARVIS_STT_COMMAND_RETRY_LANGUAGE` (French by default) and only accepts that
retry when it maps to a known safe tool.

For a French-only testing session, you can force:

```env
JARVIS_STT_LANGUAGE=fr
```

Leave it blank for multilingual auto detection.


## Local AI brain

Open-ended requests now go to an AI provider instead of being treated as
unsupported commands. V1 uses Ollama locally and keeps the provider behind a
separate interface so a cloud provider can be added later without rewriting the
voice, UI or tool layers.

Default local model:

```text
gemma3:latest
```

Examples:

- "Qui es-tu ?" -> conversational AI answer
- "Explique-moi les agents IA" -> conversational AI answer
- "Cherche des informations sur les agents IA" -> AI may select browser.search
- "Ouvre l'outil Capture d'écran" -> typed Windows tool

A low-confidence STT transcript is re-decoded before any PC action. This prevents
a sentence such as "Qui es-tu ?" from accidentally becoming "Ouvre Chrome".

After one double clap, Jarvis stays in an active conversational session. It
returns to wake mode after the follow-up timeout or when the user says
"c'est tout" / "retourne en veille".


## Recognition hardening

V1 now runs French-first STT for short commands because automatic language
detection was misclassifying very short French phrases as Hebrew, English or
Polish. The generic Whisper prompt no longer contains application names, which
reduces hallucinated commands such as "YouTube" or "Chrome".

The recorder rejects very brief noise bursts, Whisper rejects probable silence
and strong token repetition, and low-confidence PC actions are blocked. Open
conversation is also confidence-gated before it reaches the AI brain.

A local AI decision is not allowed to operate the PC unless the transcribed user
request contains an explicit action request. This is an additional safety layer
on top of typed tools.


## Agent Core / Planner / Mission Engine

The voice layer no longer has to decide every action itself.

Current orchestration:

```text
voice
  -> local STT
  -> deterministic fast-path when confidence is high
  -> Agent Core
       -> Planner
       -> Capability Registry
       -> Mission
            -> typed step 1
            -> typed step 2
            -> ...
       -> Mission Engine
       -> result/context
  -> TTS
```

Key files:

- `jarvis_agent/agent_core.py`: orchestration and recent mission context.
- `jarvis_agent/planner.py`: objective-to-plan reasoning. Ollama is the current
  provider, behind a Planner interface.
- `jarvis_agent/registry.py`: dynamic list of tools the planner is allowed to
  use and argument canonicalization.
- `jarvis_agent/mission.py`: typed sequential execution with per-step status,
  stop-on-failure and mission logs.

A request such as:

```text
Ouvre Chrome et recherche les agents IA
```

can now be planned as two steps rather than being reduced to the first command.

Generic application and folder discovery are capabilities rather than one
hard-coded phrase per target. A planner mistake such as `app.open("VLC")` is
canonicalized to generic application discovery. Named folders can carry a
parent scope, e.g. `query="media", within="baristas"`.

The current development mode remains French-first:

```env
JARVIS_STT_LANGUAGE=fr
JARVIS_PLANNER_PROVIDER=ollama
```

The Planner protocol is intentionally provider-independent so a stronger cloud
planner can be added later without rewriting voice capture, tools or the Mission
Engine.


## V2 pivot: model-native agent loop

The previous Planner/Mission prototype proved typed tools and sequential execution,
but it still behaved too much like a command router around a JSON planner. V2
switches the active voice path to a model-native tool loop.

Active path:

```text
voice
  -> Whisper small (French-first)
  -> one conversational AI runtime
       -> model chooses zero, one, or multiple tools
       -> Jarvis executes typed generic tools
       -> tool results are returned to the model
       -> model observes them and continues/replans
       -> final natural-language answer
  -> ElevenLabs
```

Normal app/folder/web requests are no longer decided by the legacy phrase router.
That router is kept only for explicit Jarvis lifecycle commands such as sleep/stop.

The local runtime uses a tool-calling model:

```env
JARVIS_AGENT_PROVIDER=ollama
JARVIS_OLLAMA_AGENT_MODEL=qwen3:4b
```

Qwen3 is used here because its Ollama model supports native tool calls. The
previous Gemma 3 model can still be kept for other experiments, but is no longer
the recommended agent brain for this branch.

The same runtime also has an optional OpenAI Responses provider:

```env
JARVIS_AGENT_PROVIDER=openai
OPENAI_API_KEY=...
JARVIS_OPENAI_AGENT_MODEL=gpt-6-astra
```

The OpenAI provider uses native function calls and can expose hosted web search.
The Windows tools themselves do not change when the model provider changes.

Important behavioral change: conversation context survives the microphone
follow-up timeout and the next wake. A short period of silence no longer erases
what the user and Jarvis were doing. Say "nouvelle conversation" to explicitly
reset the agent context.

Generic model tools currently exposed:

- `open_application(name)`
- `open_folder(name, within?)`
- `open_url(url)`
- `search_web(query)`
- `get_current_time()`
- `return_to_standby()`

The objective is to add a small number of broad capabilities, not one code path
per spoken phrase or per application.


## Connected capabilities / MCP (V2)

Jarvis separates the **brain** from the **accesses** it is allowed to use.

- The model does not automatically inherit ChatGPT account plugins.
- Local Windows/browser tools remain first-party Jarvis capabilities.
- When `JARVIS_AGENT_PROVIDER=openai`, trusted remote MCP servers or private
  MCP servers exposed through OpenAI Secure MCP Tunnel can be declared in
  `connectors.local.json`.
- Copy `connectors.example.json` to `connectors.local.json`. The local file
  is gitignored.
- Never place OAuth/access tokens inside the JSON file. Use
  `authorization_env` and keep the real token in `.env`.
- Connectors default to approval-required behavior. When the Responses API
  returns an MCP approval request, Jarvis asks the user for a verbal yes/no and
  resumes the same response after approval.
- A connector may restrict `allowed_tools` so Jarvis imports only the actions
  it actually needs.

This layer is intended for services such as source control, mail, calendars,
business systems, and future domain packs. The same permission model will later
be shared with the local-agent provider.
