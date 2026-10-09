from __future__ import annotations

import json
import queue
import re
import threading
import time
import traceback

from PySide6.QtCore import QObject, Signal, Slot

from .agent_runtime import AgentRuntimeUnavailable, build_agent_runtime
from .active_mission_supervisor import conversation_control
from .mission_workbench import MissionControlInbox, perform_mission_command
from .mcp_control import MCPControlInbox, perform_mcp_command
from .skill_control import SkillControlInbox, perform_skill_command
from .agent_knowledge import AGENT_KNOWLEDGE
from .operational_preferences import learning_enabled, skills_enabled
from .mcp_server_registry import MCPRegistry
from .audio import record_utterance, wait_for_double_clap
from .config import settings
from .language import normalize_language, repeat_prompt, tool_message
from .recognition import recognize_command
from .states import AssistantState, STATE_LABELS
from .stt import build_stt
from .tools import ToolIntent, execute, route
from .tts import ElevenLabsTTS


class TextTurnInbox:
    """Thread-safe text inbox used to interrupt passive microphone waits."""

    def __init__(self) -> None:
        self._queue: queue.Queue[str] = queue.Queue()
        self.event = threading.Event()

    def submit(self, text: str) -> bool:
        value = str(text or "").strip()
        if not value:
            return False
        self._queue.put(value)
        self.event.set()
        return True

    def pop_nowait(self) -> str | None:
        try:
            value = self._queue.get_nowait()
        except queue.Empty:
            self.event.clear()
            return None

        if self._queue.empty():
            self.event.clear()
            # Close the tiny race where a producer submits between empty()
            # and clear().
            if not self._queue.empty():
                self.event.set()
        return value

    def pending(self) -> bool:
        return self.event.is_set() or not self._queue.empty()


class InputModeGate:
    """Thread-safe admission gate for mutually exclusive text/voice input."""

    def __init__(self, inbox: TextTurnInbox) -> None:
        self.inbox = inbox
        self.changed = threading.Event()
        self._lock = threading.Lock()
        self._text_mode = False
        self._generation = 0

    def snapshot(self) -> tuple[bool, int]:
        with self._lock:
            return self._text_mode, self._generation

    def set_text_mode(self, enabled: bool) -> None:
        with self._lock:
            if self._text_mode != bool(enabled):
                self._text_mode = bool(enabled)
                self._generation += 1
        self.changed.set()

    def voice_allowed(self, generation: int) -> bool:
        with self._lock:
            return (
                not self._text_mode
                and self._generation == generation
                and not self.inbox.pending()
            )


class VoiceCaptureInterrupt:
    """Cancel a clap/recording operation as soon as text mode takes over."""

    def __init__(self, gate: InputModeGate, generation: int) -> None:
        self.gate = gate
        self.generation = generation

    def is_set(self) -> bool:
        return not self.gate.voice_allowed(self.generation)


class AssistantWorker(QObject):
    """Voice shell around the model-native Agent Runtime.

    Natural language goes to one conversational agent loop. The old rule router
    is retained only for explicit Jarvis lifecycle commands, not for deciding
    normal app/folder/web tasks.
    """

    state_changed = Signal(str)
    status_changed = Signal(str)
    transcript_changed = Signal(str)
    detail_changed = Signal(str)
    audio_level_changed = Signal(float)
    log_line = Signal(str)
    conversation_message = Signal(str, str, str)
    telemetry_changed = Signal(dict)
    operator_event = Signal(dict)
    mission_control_result = Signal(dict)
    mcp_control_result = Signal(dict)
    skill_control_result = Signal(dict)
    finished = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._stop = threading.Event()
        self._stt = build_stt()
        self._tts = ElevenLabsTTS()
        self._agent = build_agent_runtime()
        self._conversation_language = "fr"
        self._pending_direct_follow_up = ""
        # Session-local surface grounding. This is not persistent learning:
        # it only prevents an ambiguous follow-up such as "Recherche Hamza"
        # from being hijacked by the browser fast-path after a desktop app
        # or Explorer surface has just become the user's active context.
        self._active_surface_kind = ""
        self._text_inbox = TextTurnInbox()
        self._mission_control = MissionControlInbox()
        self._mcp_control = MCPControlInbox()
        self._skill_control = SkillControlInbox()
        self._skill_store = AGENT_KNOWLEDGE
        self._mcp_registry = MCPRegistry()
        self._input_mode = InputModeGate(self._text_inbox)
        self._announced_input_generation = -1
        self._reply_with_voice = True
        self._reply_source = "voice"
        self._kernel_shadow = None
        self._kernel_shadow_boot_error = ""
        if settings.kernel_shadow_enabled:
            try:
                from .shadow_kernel_runtime import KernelShadowObserver

                self._kernel_shadow = KernelShadowObserver.from_settings(
                    settings
                )
            except Exception as exc:
                # Shadow mode must never prevent the authoritative runtime
                # from starting. The error is surfaced once boot logging is
                # available.
                self._kernel_shadow_boot_error = (
                    f"{type(exc).__name__}: {exc}"
                )

    def _emit_operator_model(self) -> None:
        """Send measured counters to GUI; GUI never touches the live agent."""
        try:
            from .operator_telemetry import runtime_model_snapshot
            self.telemetry_changed.emit(runtime_model_snapshot(self._agent))
        except (AttributeError, RuntimeError, TypeError):
            pass

    def _state(self, state: AssistantState, status: str | None = None) -> None:
        self.state_changed.emit(state.value)
        self.status_changed.emit(status or STATE_LABELS[state])
        self.log_line.emit(f"[STATE] {state.value}")

    def _level(self, value: float) -> None:
        self.audio_level_changed.emit(max(0.0, min(1.0, float(value))))

    def _speak(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        text = re.sub(r"[*_#]+", "", text).replace("`", "").strip()
        self._state(AssistantState.SPEAKING, text)
        self.log_line.emit(f"[TTS] {text}")
        self._tts.speak(text, on_level=self._level)
        if settings.tts_settle_s > 0:
            time.sleep(settings.tts_settle_s)

    def _agent_phase(self, phase: str) -> None:
        if phase == "thinking":
            self._state(AssistantState.THINKING, "Jarvis réfléchit…")
        elif phase == "acting":
            self._state(AssistantState.ACTING, "Jarvis agit…")
        elif phase in {"observing", "verifying", "waiting_approval", "recovering", "blocked"}:
            state = AssistantState.RECOVERING if phase == "blocked" else AssistantState(phase)
            self._state(state)
        elif phase.startswith("mission_progress:"):
            try:
                report = json.loads(phase.partition(":")[2])
            except (ValueError, TypeError):
                return
            self.mission_control_result.emit({"success": True, "operation": "delegation_progress", **report})
            self._state(AssistantState.ACTING if report.get("status") == "READY" else AssistantState.VERIFYING,
                        "Progression enregistree; objectif complet " + ("verifie" if report.get("goal_verified") else "non encore verifie"))
        elif phase.startswith("researching:"):
            raw_payload = phase.split(":", 1)[1]
            try:
                payload = json.loads(raw_payload)
            except (TypeError, ValueError, json.JSONDecodeError):
                payload = {}
            query = str(payload.get("query") or "").strip()
            reason = str(payload.get("reason") or "").strip()
            if reason == "local_failure":
                prefix = (
                    "Je n'ai pas pu résoudre ce point avec les méthodes "
                    "locales disponibles. "
                )
            else:
                prefix = (
                    "Ce point nécessite une information externe que je "
                    "n'ai pas localement. "
                )
            subject = (
                f"Je vais lancer une recherche en arrière-plan pour vérifier : {query}."
                if query
                else "Je vais lancer une recherche en arrière-plan pour vérifier la solution."
            )
            self.log_line.emit(
                f"[RESEARCH] announce reason={reason or 'unspecified'} "
                f"query={query!r}"
            )
            self._deliver_reply(prefix + subject)
            self._state(
                AssistantState.THINKING,
                "Recherche en arrière-plan…",
            )

    @Slot()
    def stop(self) -> None:
        self._stop.set()
        self._mission_control.stop_requested.set()
        self._input_mode.changed.set()

    def submit_text(self, text: str) -> bool:
        """Queue a typed turn without touching the agent from the UI thread."""
        accepted = self._text_inbox.submit(text)
        if accepted:
            self._input_mode.changed.set()
        return accepted

    def set_text_mode(self, enabled: bool) -> None:
        """Switch text/voice admission without touching the agent context."""
        self._input_mode.set_text_mode(enabled)

    def submit_mission_control(self, operation: str, value: str = "") -> bool:
        """Safe UI handoff; no mission method runs on the Qt GUI thread."""
        if self._text_inbox.pending():
            return False  # Do not attach an older queued message to a new mission.
        accepted = self._mission_control.submit(operation, value)
        if accepted:
            self._input_mode.changed.set()
        return accepted

    def submit_mcp_control(self, operation: str, server_id: str = "",
                           value: str = "") -> bool:
        """No connection or subprocess is opened from the UI thread."""
        accepted = self._mcp_control.submit(operation, server_id, value)
        if accepted:
            self._input_mode.changed.set()
        return accepted

    def submit_skill_control(self, operation: str, value: str = "{}") -> bool:
        accepted = self._skill_control.submit(operation, value)
        if accepted:
            self._input_mode.changed.set()
        return accepted

    def _run_skill_control(self, command) -> None:
        try:
            result = perform_skill_command(self._skill_store, command)
        except (ValueError, KeyError) as exc:
            result = {"success": False, "operation": command.operation, "reason": str(exc)[:100]}
        except Exception as exc:
            result = {"success": False, "operation": command.operation, "reason": type(exc).__name__}
        self.skill_control_result.emit(result)
        if command.operation == "policy_set" and result.get("success"):
            flags = result["preferences"]
            self.log_line.emit(f"[OPERATIONAL_POLICY] skills={int(flags['skills_enabled'])} learning={int(flags['learning_enabled'])}")

    def _run_mcp_control(self, command) -> None:
        try:
            result = perform_mcp_command(self._mcp_registry, command,
                stop_event=self._mcp_control.stop_requested)
        except Exception as exc:
            # Never leak API tokens, bearer headers or arbitrary MCP output.
            result = {"success": False, "operation": command.operation,
                      "server_id": command.server_id,
                      "reason": type(exc).__name__}
        finally:
            if command.operation == "oauth_authorize":
                self._mcp_control.finish_authorization()
        self.mcp_control_result.emit(result)
        self.log_line.emit(
            "[MCP_CONTROL] operation=" + command.operation
            + " success=" + str(bool(result.get("success")))
        )

    def _run_mission_control(self, command) -> None:
        """Run only from AssistantWorker.run; do not bypass safety proof gates."""
        try:
            result = perform_mission_command(self._agent, command,
                stop_event=self._mission_control.stop_requested,
                log=self.log_line.emit, phase=self._agent_phase,
                progress=lambda report: self.mission_control_result.emit({
                    "success": True, "operation": "delegation_progress", **report}))
        except Exception as exc:
            # Unexpected storage/backend errors should surface in the operator
            # instead of terminating the speech/text worker. Avoid storing
            # exception bodies because they can contain paths or secrets.
            result = {
                "success": False, "operation": command.operation,
                "reason": type(exc).__name__,
            }
        finally:
            if command.operation in {"run_supervision", "advance_supervision"}:
                self._mission_control.finish_coordination()
        self.mission_control_result.emit(result)
        if not result.get("success"):
            self.log_line.emit(
                "[MISSION_CONTROL] rejected=" + str(result.get("reason"))[:150]
            )
            return
        self.log_line.emit(
            f"[MISSION_CONTROL] operation={command.operation} status="
            f"{result.get('status', '')} id={result.get('mission_id', '')}"
        )
        if command.operation in ("begin", "begin_only", "resume"):
            # Old direct-app questions must not hijack an attached mission.
            self._pending_direct_follow_up = ""
        if command.operation == "begin":
            # One ordinary model turn, same runtime/voice/chat path; no second
            # model, no separate planning executor, no pseudo-tool instructions.
            self._process_user_text(command.value, source="text")
        if command.operation in {"advance_supervision", "run_supervision"} and result.get("text"):
            self._reply_source = "text"
            self._reply_with_voice = True
            self._deliver_reply(str(result["text"]))
            self._restore_mission_phase()

    def _restore_mission_phase(self) -> None:
        mid = getattr(self._agent, "active_mission_id", None)
        if not mid:
            self._state(AssistantState.SUCCESS, "Prêt")
            return
        try:
            data = self._agent.mission_snapshot(mid)
            status = (data.get("active_supervisor") or {}).get("state", "")
            # Snapshot's supervisor key is a public projection, not model prose.
            status = status or (data.get("supervisor") or {}).get("state", "")
            if data.get("status") == "blocked" or data.get("manual_review_required"):
                status = "BLOCKED"
        except (AttributeError, KeyError, OSError, ValueError, RuntimeError):
            status = ""
        state, label = {
            "WAITING_APPROVAL": (AssistantState.WAITING_APPROVAL, "Confirmation explicite attendue"),
            "BLOCKED": (AssistantState.RECOVERING, "Mission conservee; revision necessaire avant reprise"),
            "RECOVERING": (AssistantState.RECOVERING, "Preuve finale attendue; aucune action repetee"),
            "READY": (AssistantState.PAUSED, "Checkpoint conserve; objectif complet non verifie"),
        }.get(status, (AssistantState.PAUSED, "Mission conservee; objectif complet non verifie"))
        self._state(state, label)

    def _apply_input_mode(self) -> tuple[bool, int]:
        text_mode, generation = self._input_mode.snapshot()
        if generation != self._announced_input_generation:
            self._announced_input_generation = generation
            self._level(0.0)
            self.log_line.emit(
                f"[INPUT_MODE] {'text' if text_mode else 'voice'} "
                f"microphone={'off' if text_mode else 'enabled'} "
                f"generation={generation}"
            )
            if text_mode:
                self._state(
                    AssistantState.ARMED,
                    "Mode texte — microphone coupé · réponses texte et voix",
                )
        return text_mode, generation

    def _deliver_reply(self, text: str) -> None:
        value = (text or "").strip()
        if not value:
            return
        value = re.sub(r"[*_#]+", "", value).replace("`", "").strip()
        source = self._reply_source
        self.conversation_message.emit("assistant", value, source)
        if source == "text":
            self.log_line.emit(f"[TEXT_REPLY] {value}")
        if self._reply_with_voice:
            self._speak(value)
        else:
            self.status_changed.emit("Réponse texte prête")


    def _shadow_observe(
        self,
        user_text: str,
        *,
        source: str,
        actions=(),
        action_names=None,
        response_text: str = "",
        success: bool | None = None,
    ) -> None:
        """Best-effort passive mirror; never affect the live control path."""
        # Show *all* observed runtime turns, including direct fast-paths,
        # even when Kernel Shadow and durable reliability are switched off.
        # Do not transmit typed text, tool arguments or result bodies.
        try:
            observed = tuple(actions or ())
            names = (
                tuple(str(name)[:80] for name in action_names or ())
                if action_names is not None
                else tuple(str(getattr(action, "name", "") or "")[:80]
                           for action in observed)
            )
            outcome = (
                bool(success) if success is not None
                else all(bool(getattr(action, "success", False))
                         for action in observed)
            )
            self.operator_event.emit({
                "source": str(source)[:50],
                "tools": list(names[:8]),
                "count": len(observed),
                "success": outcome,
                "verified": False,  # Final goal proof must be independent.
            })
        except Exception:
            # Observability can never interrupt a voice, text or tool turn.
            pass
        if self._kernel_shadow is None:
            return
        try:
            observation = self._kernel_shadow.observe_turn(
                user_text,
                source=source,
                actions=tuple(actions or ()),
                action_names=tuple(action_names or ()),
                response_text=response_text,
                success=success,
            )
            self.log_line.emit(
                "[KERNEL_SHADOW] "
                f"mission={observation.mission_id} "
                f"source={source} "
                f"actions={observation.action_count} "
                f"success={int(observation.success)} "
                "authoritative=0"
            )
        except Exception as exc:
            self.log_line.emit(
                "[KERNEL_SHADOW] observer_error="
                f"{type(exc).__name__}:{exc} "
                "live_runtime_unchanged=1"
            )

    def _handle_lifecycle(self, user_text: str) -> tuple[bool, bool]:
        """Return (handled, keep_listening)."""
        intent = route(user_text)
        if intent.name not in {"assistant.stop", "assistant.sleep"}:
            return False, True

        result = execute(intent)
        spoken = tool_message(intent, result, self._conversation_language)
        self.log_line.emit(
            f"[DIRECT] lifecycle={intent.name} success={result.success}"
        )
        self._shadow_observe(
            user_text,
            source="lifecycle_fast_path",
            actions=(result,),
            action_names=(intent.name,),
            response_text=spoken,
            success=result.success,
        )
        self._deliver_reply(spoken)

        if result.should_exit:
            self._stop.set()
            return True, False
        if result.end_session:
            return True, False
        return True, True

    @staticmethod
    def _is_simple_direct_action(
        user_text: str,
        intent: ToolIntent,
        active_surface_kind: str = "",
    ) -> bool:
        """Fast path only for one explicit deterministic action.

        Complex/compound language still goes to the conversational agent loop.
        This follows the local-first rule: simple commands should not depend on
        an LLM when the typed intent is already unambiguous.
        """
        if intent.name not in {
            "browser.open_url",
            "browser.search",
            "browser.search_site",
            "browser.search_prompt",
            "browser.close_tab",
            "browser.back",
            "app.open",
            "folder.open",
            "folder.open_named",
            "folder.open_prompt",
            "system.time",
        }:
            return False

        text = user_text.lower()
        # Never let the legacy router truncate a compound mission. If the
        # utterance contains sequencing or a second obvious action, let the
        # native agent handle the complete objective.
        if (
            intent.name != "browser.search_site"
            and re.search(r"\b(et|puis|ensuite|après|apres|and|then)\b", text)
        ):
            return False
        if (
            intent.name != "browser.search_site"
            and re.search(r"\b(ouvre|ouvrir|lance)\b", text)
            and re.search(r"\b(recherche|cherche)\b", text)
        ):
            return False

        # "Open X" can refer to a downloaded file/installer rather than the
        # installed application itself. Let the agent choose open_file when the
        # utterance clearly carries file semantics.
        if intent.name == "app.open" and (
            re.search(r"\b(fichier|file|setup|installateur|installation)\b", text)
            or re.search(r"\.(exe|msi|pdf|txt|docx?|xlsx?|zip)\b", text)
        ):
            return False

        # Opening/navigating inside an already visible UI (a result, video,
        # tab, item, button...) is not the same as opening the site/application
        # itself. Keep these requests in the agent loop so it can inspect the
        # real interface instead of reducing them to browser.open_url/app.open.
        if intent.name in {"browser.open_url", "app.open"} and (
            re.search(
                r"\b(premier|premiere|première|deuxieme|deuxième|troisieme|"
                r"troisième|resultat|résultat|video|vidéo|onglet|tab|bouton|"
                r"element|élément|lien|link|short|story)\b",
                text,
            )
            or re.search(
                r"\b(dans|inside|within|sur)\b.{0,55}"
                r"\b(onglet|tab|page|fenetre|fenêtre|interface)\b",
                text,
            )
        ):
            return False

        # An unqualified "Recherche X" belongs to the currently grounded
        # surface. Only keep the deterministic browser fast-path when the
        # current surface is already a browser, or when the request explicitly
        # asks for web search. Desktop apps and Explorer must go through the
        # agent so it can inspect the actual search control.
        if (
            intent.name == "browser.search"
            and str(intent.args.get("scope") or "context").strip().casefold() != "web"
            and active_surface_kind in {"app", "filesystem"}
        ):
            return False

        # A search targeted at a visible field/page is UI interaction, not a
        # generic Google search. Let the agent inspect the current application
        # and operate the real control instead of hijacking the request through
        # the browser.search fast path.
        if intent.name == "browser.search" and (
            re.search(
                r"\b(dans|inside|within)\b.{0,60}"
                r"\b(barre|champ|zone|field|box|page|fenêtre|fenetre|application|app)\b",
                text,
            )
            or re.search(
                r"\b(barre|champ|zone|field|box)\b.{0,35}"
                r"\b(recherche|search)\b",
                text,
            )
        ):
            return False
        return True

    def _update_surface_context(
        self,
        action_name: str,
        *,
        success: bool,
    ) -> None:
        if not success:
            return
        if action_name in {
            "browser.open_url",
            "browser.search",
            "browser.search_site",
            "browser.back",
            "browser.close_tab",
            "open_url",
            "open_web_search",
            "list_browser_pages",
            "inspect_browser_page",
            "activate_browser_page",
            "write_browser_element",
            "click_browser_element",
            "press_browser_element",
            "scroll_browser_element",
        }:
            self._active_surface_kind = "browser"
        elif action_name in {
            "app.open",
            "app.open_named",
            "open_application",
            "open_file",
        }:
            self._active_surface_kind = "app"
        elif action_name in {
            "folder.open",
            "folder.open_named",
            "open_folder",
        }:
            self._active_surface_kind = "filesystem"

    def _record_direct_agent_context(
        self,
        user_text: str,
        response_text: str,
        intent: ToolIntent,
        result,
    ) -> None:
        self._update_surface_context(
            intent.name,
            success=bool(result.success),
        )
        recorder = getattr(self._agent, "record_external_turn", None)
        if not callable(recorder):
            return
        try:
            recorder(
                user_text,
                response_text,
                action_name=intent.name,
                action_detail=str(result.detail or ""),
                success=bool(result.success),
            )
            self.log_line.emit(
                f"[CONTEXT] direct={intent.name} "
                f"success={1 if result.success else 0}"
            )
        except Exception as exc:
            # A context sync failure must never break the action that already
            # succeeded locally.
            self.log_line.emit(
                f"[CONTEXT] direct_sync_failed="
                f"{type(exc).__name__}: {exc}"
            )

    def _handle_simple_direct_action(
        self,
        user_text: str,
        intent: ToolIntent,
    ) -> bool:
        # Opt-in Browser Core owns browser intents; the historical fast path
        # uses OS input and cannot provide tab-scoped isolation.
        from .foundation_tools import enabled
        if enabled("JARVIS_BROWSER_CORE_ENABLED") and (intent.name.startswith("browser.") or
                (intent.name == "app.open" and str(intent.args.get("app", "")).lower() == "chrome")):
            return False
        if not self._is_simple_direct_action(
            user_text,
            intent,
            self._active_surface_kind,
        ):
            return False

        result = execute(intent)
        self._pending_direct_follow_up = result.follow_up or ""
        self.log_line.emit(
            f"[DIRECT] simple={intent.name} success={result.success} "
            f"args={intent.args} follow_up={result.follow_up}"
        )
        spoken = tool_message(intent, result, self._conversation_language)
        self._record_direct_agent_context(
            user_text,
            spoken,
            intent,
            result,
        )
        self._shadow_observe(
            user_text,
            source="direct_fast_path",
            actions=(result,),
            action_names=(intent.name,),
            response_text=spoken,
            success=result.success,
        )
        self.detail_changed.emit(
            f"{intent.name}:{'ok' if result.success else 'erreur'}"
        )
        self._deliver_reply(spoken)
        self._state(
            AssistantState.SUCCESS if result.success else AssistantState.ERROR,
            "Prêt" if result.success else "Action non terminée",
        )
        return True

    def _process_user_text(
        self,
        user_text: str,
        *,
        source: str,
        legacy_intent: ToolIntent | None = None,
    ) -> bool:
        """Process voice or typed text through one authoritative turn path."""
        user_text = str(user_text or "").strip()
        if not user_text:
            return True

        source = "text" if source == "text" else "voice"
        text_mode, generation = self._input_mode.snapshot()
        if source == "voice" and (
            text_mode
            or not self._input_mode.voice_allowed(generation)
            or self._stop.is_set()
        ):
            self.log_line.emit(
                "[VOICE] turn discarded because voice input is disabled"
            )
            return False
        self._reply_source = source
        self._reply_with_voice = True
        self.conversation_message.emit("user", user_text, source)
        if source == "text":
            self.log_line.emit(
                f"[YOU] {user_text} [source=text lang={self._conversation_language}]"
            )

        lifecycle_handled, keep_listening = self._handle_lifecycle(user_text)
        if lifecycle_handled:
            return keep_listening

        if (
            self._pending_direct_follow_up
            and not getattr(self._agent, "active_mission_id", None)
        ):
            follow_up = self._pending_direct_follow_up
            self._pending_direct_follow_up = ""
            from .foundation_tools import enabled
            if follow_up == "search_query" and enabled("JARVIS_BROWSER_CORE_ENABLED"):
                follow_up = "foundation_browser_query"
            if follow_up == "search_query":
                follow_intent = ToolIntent("browser.search", {"query": user_text})
            elif follow_up == "folder_name":
                follow_intent = ToolIntent("folder.open_named", {"query": user_text})
            else:
                follow_intent = ToolIntent("unknown", {"text": user_text})

            if follow_intent.name != "unknown":
                result = execute(follow_intent)
                self.log_line.emit(
                    f"[DIRECT] follow_up={follow_up} "
                    f"success={result.success} args={follow_intent.args}"
                )
                self._pending_direct_follow_up = result.follow_up or ""
                response = tool_message(
                    follow_intent,
                    result,
                    self._conversation_language,
                )
                self._record_direct_agent_context(
                    user_text,
                    response,
                    follow_intent,
                    result,
                )
                self._shadow_observe(
                    user_text,
                    source="direct_follow_up",
                    actions=(result,),
                    action_names=(follow_intent.name,),
                    response_text=response,
                    success=result.success,
                )
                self._deliver_reply(response)
                self._state(
                    AssistantState.SUCCESS if result.success else AssistantState.ERROR,
                    "Prêt" if result.success else "Action non terminée",
                )
                return True

        resolved_intent = legacy_intent or route(user_text)
        if (
            not getattr(self._agent, "active_mission_id", None)
            and self._handle_simple_direct_action(user_text, resolved_intent)
        ):
            return True

        self._state(
            AssistantState.UNDERSTANDING,
            "Compréhension de votre demande…",
        )

        control = getattr(self, "_mission_control", None)
        reserved = bool(control and getattr(self._agent, "active_mission_id", None)
                        and control.reserve_conversation())
        try:
            with conversation_control(control.stop_requested if control else self._stop):
                turn = self._agent.run(
                    user_text,
                    log=self.log_line.emit,
                    phase=self._agent_phase,
                )
        except AgentRuntimeUnavailable as exc:
            self._emit_operator_model()
            self.log_line.emit(f"[AGENT] unavailable: {exc}")
            self._shadow_observe(
                user_text,
                source="agent_runtime",
                response_text=str(exc),
                success=False,
            )
            self._state(AssistantState.ERROR, "Cerveau agent indisponible")
            self._deliver_reply(
                "Mon cerveau agent n'est pas disponible pour le moment. "
                "Vérifiez le modèle configuré puis réessayez."
            )
            if getattr(self._agent, "active_mission_id", None):
                self._restore_mission_phase()
            else:
                self._state(AssistantState.ERROR, "Cerveau agent indisponible")
            return True
        except Exception as exc:
            self.log_line.emit("[MISSION] runtime_paused error_type=" + type(exc).__name__)
            self._emit_operator_model()
            self._deliver_reply("L'execution est suspendue. Je ne relance aucune action incertaine; consultez la mission avant toute reprise.")
            if getattr(self._agent, "active_mission_id", None):
                self._restore_mission_phase()
            else:
                self._state(AssistantState.ERROR, "Execution suspendue")
            return True
        finally:
            if reserved:
                control.finish_coordination()

        self._emit_operator_model()
        self._shadow_observe(
            user_text,
            source="agent_runtime",
            actions=turn.actions,
            response_text=turn.text,
        )

        for action in turn.actions:
            self._update_surface_context(
                action.name,
                success=bool(action.success),
            )

        if turn.actions:
            details = " · ".join(
                f"{action.name}:{'ok' if action.success else 'erreur'}"
                for action in turn.actions
            )
            self.detail_changed.emit(details)
            self.log_line.emit(
                f"[AGENT] completed_actions={len(turn.actions)}"
            )
        else:
            self.detail_changed.emit("Conversation")

        self._deliver_reply(turn.text)

        if turn.should_exit:
            self._stop.set()
            return False

        if turn.end_session:
            self.log_line.emit("[SESSION] agent requested standby")
            return False

        self._restore_mission_phase()
        self._level(0.0)
        return True

    def _listen_turn(self, *, first_turn: bool) -> bool:
        text_mode, generation = self._input_mode.snapshot()
        if text_mode or self._text_inbox.pending():
            return False

        self._state(AssistantState.LISTENING, "Je vous écoute…")
        timeout = None if first_turn else settings.conversation_followup_timeout_s

        audio = record_utterance(
            self._stop,
            on_level=self._level,
            on_status=self.status_changed.emit,
            start_timeout_s=timeout,
            interrupt_event=VoiceCaptureInterrupt(self._input_mode, generation),
        )

        if (
            not self._input_mode.voice_allowed(generation)
            or self._stop.is_set()
        ):
            self.log_line.emit(
                "[VOICE] capture discarded after input change"
            )
            return False

        if audio is None:
            if self._text_inbox.pending():
                self.log_line.emit("[TEXT] microphone wait interrupted by typed turn")
                return False
            if first_turn and not self._stop.is_set():
                self.log_line.emit("[VOICE] aucune phrase détectée")
            else:
                self.log_line.emit(
                    "[SESSION] silence — retour au mode réveil, contexte conservé"
                )
            return False

        self._state(AssistantState.TRANSCRIBING, "Transcription locale…")
        if settings.stt_provider == "groq":
            self.log_line.emit(
                f"[STT] provider=groq model={settings.groq_stt_model}"
            )
        else:
            self.log_line.emit(
                f"[STT] provider=local model={settings.whisper_model} "
                f"device={settings.whisper_device}"
            )

        stt_started = time.perf_counter()
        transcript, legacy_intent = recognize_command(
            self._stt,
            audio,
            log=self.log_line.emit,
            preferred_language=self._conversation_language,
        )
        if (
            not self._input_mode.voice_allowed(generation)
            or self._stop.is_set()
        ):
            self.log_line.emit(
                "[VOICE] stale transcription discarded after input change"
            )
            return False
        self.log_line.emit(
            f"[PERF] stt_total_seconds="
            f"{time.perf_counter() - stt_started:.2f}"
        )

        if not transcript.text:
            self._state(AssistantState.ERROR, "Phrase non comprise")
            self._speak(repeat_prompt(self._conversation_language))
            return True

        self._conversation_language = normalize_language(transcript.language)
        user_text = transcript.text.strip()
        self.transcript_changed.emit(user_text)
        self.log_line.emit(
            f"[YOU] {user_text} [lang={self._conversation_language}]"
        )

        weak_text = (
            transcript.avg_logprob is not None
            and transcript.avg_logprob < -0.95
        )
        probable_silence = (
            transcript.no_speech_probability is not None
            and transcript.no_speech_probability >= 0.45
        )
        if weak_text or probable_silence:
            self.log_line.emit(
                "[STT] transcript too uncertain for agent actions"
            )
            self._speak(repeat_prompt(self._conversation_language))
            return True

        if (
            self._pending_direct_follow_up
            and transcript.avg_logprob is not None
            and transcript.avg_logprob < -0.70
        ):
            self.log_line.emit(
                "[STT] direct follow-up kept pending because transcript is weak"
            )
            self._speak(repeat_prompt(self._conversation_language))
            return True

        return self._process_user_text(
            user_text,
            source="voice",
            legacy_intent=legacy_intent,
        )

    @Slot()
    def run(self) -> None:
        self.log_line.emit("[BOOT] Jarvis native agent runtime started")
        self._emit_operator_model()
        if self._kernel_shadow is not None:
            self.log_line.emit(
                "[KERNEL_SHADOW] enabled=1 authoritative=0 "
                "dispatch=0 routing_override=0"
            )
        elif settings.kernel_shadow_enabled:
            self.log_line.emit(
                "[KERNEL_SHADOW] enabled=0 init_error="
                + self._kernel_shadow_boot_error
                + " live_runtime_unchanged=1"
            )
        self.log_line.emit(
            f"[AI] provider={settings.agent_provider} "
            f"stt_provider={settings.stt_provider} "
            f"local_model={settings.ollama_agent_model} "
            f"groq_model={settings.groq_agent_model} "
            f"cerebras_model={settings.cerebras_agent_model} "
            f"openai_model={settings.openai_agent_model}"
        )
        mode = (
            "BASELINE"
            if not (
                learning_enabled(settings)
                or settings.vision_enabled
                or settings.strict_proof_enabled
            )
            else "EXTENDED"
        )
        self.log_line.emit(
            f"[MODE] {mode} "
            f"learning={int(learning_enabled(settings))} "
            f"skills={int(skills_enabled(settings))} "
            f"vision={int(settings.vision_enabled)} "
            f"visual_actions={int(settings.vision_actions_enabled)} "
            f"focused_typing={int(settings.focused_typing_fallback_enabled)} "
            f"strict_proof={int(settings.strict_proof_enabled)}"
        )
        self._state(AssistantState.STARTING, "Initialisation de Jarvis…")

        try:
            self.status_changed.emit("Chargement du cerveau local…")
            self._agent.warm_up(log=self.log_line.emit)
            while not self._stop.is_set():
                self._input_mode.changed.clear()
                text_mode, generation = self._apply_input_mode()
                self.transcript_changed.emit("")
                self.detail_changed.emit("")

                skill_command = self._skill_control.pop_nowait()
                if skill_command is not None:
                    self._run_skill_control(skill_command)
                    continue

                mcp_command = self._mcp_control.pop_nowait()
                if mcp_command is not None:
                    self._run_mcp_control(mcp_command)
                    continue

                mission_command = self._mission_control.pop_nowait()
                if mission_command is not None:
                    self._run_mission_control(mission_command)
                    continue

                typed = self._text_inbox.pop_nowait()
                if typed is not None:
                    self.log_line.emit("[TEXT] typed conversation turn")
                    self._process_user_text(typed, source="text")
                    continue

                if text_mode:
                    # Hard admission barrier: no clap detector, recorder or STT
                    # is allowed to start while Conversation texte is selected.
                    self._input_mode.changed.wait(0.25)
                    continue

                self._state(
                    AssistantState.CALIBRATING,
                    "Calibration du microphone…",
                )
                detected = wait_for_double_clap(
                    self._stop,
                    on_level=self._level,
                    on_status=self.status_changed.emit,
                    on_armed=lambda: self._state(
                        AssistantState.ARMED,
                        "Prêt — double clap pour réveiller Jarvis",
                    ),
                    interrupt_event=VoiceCaptureInterrupt(
                        self._input_mode,
                        generation,
                    ),
                )
                if self._stop.is_set():
                    break
                if not self._input_mode.voice_allowed(generation):
                    continue
                if not detected:
                    break

                self._state(AssistantState.WAKE, "Réveil détecté")
                self.log_line.emit("[WAKE] double clap")
                self._speak(settings.wake_phrase)

                if self._stop.is_set():
                    break

                # Important: agent context intentionally survives wake/sleep
                # and is shared with typed turns.
                self.log_line.emit("[SESSION] conversation active")
                first_turn = True

                while not self._stop.is_set():
                    if not self._input_mode.voice_allowed(generation):
                        self.log_line.emit(
                            "[TEXT] voice session interrupted by text mode"
                        )
                        break
                    keep_listening = self._listen_turn(first_turn=first_turn)
                    if not keep_listening:
                        break
                    first_turn = False

            self._state(AssistantState.IDLE, "Jarvis arrêté")

        except Exception as exc:
            self.log_line.emit(f"[ERROR] {type(exc).__name__}: {exc}")
            self.log_line.emit(traceback.format_exc())
            self._state(
                AssistantState.ERROR,
                f"Erreur · {type(exc).__name__}",
            )
        finally:
            self.finished.emit()
