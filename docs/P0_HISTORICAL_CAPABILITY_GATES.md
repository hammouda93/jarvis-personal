# Jarvis — historical capability and non-regression gates

This file is a **test contract**, not a claim that all paths currently work.

## Evidence levels

- **Historical LIVE**: previously observed on a real Windows desktop; does not prove the current commit.
- **CI**: deterministic/unit or replay validation only.
- **Current LIVE**: requires a new local Windows log showing tool dispatch + readback + outcome.
- **Not validated**: never infer success from a model response or a green CI.

Do not delete historic branches, rewrite commit history or clean the developer's dirty worktree to satisfy a test. Preserve all uncommitted files and personal data.

## Windows application capabilities

| Scenario | Historical success | Current gate |
| --- | --- | --- |
| Notepad open/inspect/write Unicode (French/Arabic) | Previously LIVE; current P0 new-document write had verified=true | Create a new blank document while preserving old tabs, verify exact text; then explicitly close **only the intended unsaved work** after observing the modal or its absence |
| Cursor setup from Downloads | Previously LIVE: `CursorUserSetup-x64-3.22.12.exe`, `Installation - Cursor (User)` | `find_file` read-only, `open_file(kind=installer)` with explicit authorization, independently inspect real setup controls; **do not begin installation** |
| Cursor license/Next | Previously LIVE on historical checkpoint | Interact only if specifically authorized; observe new dialog after each step; stop before install action |
| Calculator open/list/close | Previously LIVE | Ensure window identity and close readback |
| WhatsApp contact search | Previously LIVE | Observe native window and input; never send a message without the user's authorization |

## Browser and conversations

| Scenario | Historical success | Current gate |
| --- | --- | --- |
| Chrome native bridge, usual profile | Previously LIVE | Do not switch to a separate browser profile; prove active tab identity |
| YouTube search, result selection, Back, close only selected tab | Previously LIVE in separate tests | Correct page/URL and tab state after each action; retain other tabs |
| MS Football drawer → Joueurs | Current P0 LIVE navigation showed verified URL | Full mission goal certification; no hard-coded MS Football UI path |
| Memory V5 SQLite | Current LIVE successful across restart | Exact write receipt/readback and retrieval after fresh process |
| Provider cascade | Current LIVE Cerebras primary 429 → secondary | Keep primary/secondary/Groq ordering; observe no mutation replay and bounded context |
| Text+voice | Historical LIVE | Mic OFF on text input; TTS available; no unwanted activation |

## Non-regression protocol

1. Run Python regression and Browser Bridge replay from the exact candidate commit.
2. Run read-only smoke checks (memory search, `find_file`, application/window listing).
3. Run Notepad Unicode/new-document/unsaved close on **real Windows**. Save real tool result and readback; do not discard unrelated tabs.
4. Run Cursor setup discovery/open/inspect; stop before any installation operation.
5. Run browser tab navigation and closure in usual Chrome profile.
6. Run provider 402/429 fallback simulations and inspect API context budget.
7. Mark each scenario **current LIVE passed** only with a concrete log and independent evidence.

If a regression is detected, repair it on an isolated branch with a reproducing test. Do not remove a safety guard merely to make the test green.

## Structural acceptance standard

Jarvis must be generic: observe, select fresh refs, act, inspect new state, verify and recover. No UI coordinates, app-specific click macros, hard-coded Cursor/Notepad controls or site-specific selectors. Explicitly distinguish installed applications, on-disk installers, temporary documents, saved files, browser tabs and whole windows.

The complete autonomous mission OS, cloud workers, deep research, multi-agent work and premium operator interface remain strategic targets; this P0 checkpoint is only a compatibility and safety repair.
