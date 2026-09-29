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
qwen3:4b-instruct
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
