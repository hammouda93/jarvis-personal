"""Bounded mission control around existing native tools, not another UI agent."""
from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass, replace
from typing import Any, Callable

from .config import settings
from .kernel_contracts import MissionContext, MissionStatus
from .perception_router import PerceptionManager
from .semantic_grounding import normalized_text
from .target_resolver import TargetIntent, resolve_target
from .ui_geometry import CaptureGeometry
from .ui_observation import UIEntity, UIObservation, object_detail
from .ui_state import ProgressTracker, UIState
from .ui_verifier import CONDITION_KINDS, Postcondition, VerificationVerdict, parse_conditions, verify_conditions

READ_TOOLS = frozenset({"observe_ui", "inspect_active_window", "observe_screen",
                        "verify_ui_goal", "ui_engine_status", "define_ui_goal", "list_browser_pages"})
MUTATION_TOOLS = frozenset({
    "act_ui", "click_ui_element", "write_ui_element", "click_visual_target",
    "write_visual_target", "press_key", "type_text_active_window",
    "close_window", "close_tab", "open_application", "open_file", "open_folder", "open_url",
})


@dataclass
class PendingVerification:
    action_id: str
    before: UIObservation | None
    conditions: tuple[Postcondition, ...]
    signature: str
    require_transition: bool
    non_idempotent: bool = False
    verdict: VerificationVerdict | None = None
    progress_recorded: bool = False


def _result(name: str, success: bool, message: str, payload: dict[str, Any] | str):
    from .native_tools import AgentActionResult
    return AgentActionResult(name, success, message,
                             json.dumps(payload, ensure_ascii=False) if isinstance(payload, dict) else payload)


class ComputerUseController:
    def __init__(self, *, perception: PerceptionManager | None = None,
                 browser: Any = None, store: Any = None):
        self.state = perception.state if perception else UIState()
        self.perception = perception or PerceptionManager(state=self.state, browser=browser)
        self.browser = browser or self.perception.browser
        self.store = store
        self.context: MissionContext | None = None
        self.goal: tuple[Postcondition, ...] = ()
        self.goal_before: UIObservation | None = None
        self.goal_verdict: VerificationVerdict | None = None
        self.pending: PendingVerification | None = None
        self.progress = ProgressTracker()
        self.started_at = time.monotonic()
        self.action_count = 0
        self.observation_count = 0
        self.cancelled = threading.Event()
        self._lock = threading.RLock()
        self._events: list[dict[str, Any]] = []
        self._log: Callable[[str], None] | None = None
        self.receipts: list[dict[str, Any]] = []

    def begin(self, user_text: str, *, mission_id: str = "", log=None, resume: bool = False) -> None:
        with self._lock:
            self._log = log
            self.cancelled.clear()
            self.started_at = time.monotonic()
            self.action_count = self.observation_count = 0
            if not resume or self.context is None:
                self.context = MissionContext(mission_id or "ui_" + uuid.uuid4().hex, user_text,
                                              status=MissionStatus.RUNNING, owner_agent_id="interaction")
                self.goal = ()
                self.goal_before = None
                self.goal_verdict = None
                self.pending = None
                self.receipts = []
                self.progress = ProgressTracker()
                self._events = []
                self.state.invalidate()
            elif mission_id:
                self.context.mission_id = mission_id
            self._persist()

    def cancel(self) -> None:
        self.cancelled.set()

    def pending_verification(self) -> bool:
        return self.pending is not None and (
            self.pending.verdict is None or self.pending.verdict.status in {"inconclusive", "unsafe"}
            or (self.pending.non_idempotent and not self.pending.verdict.passed)
        )

    def _emit(self, kind: str, **payload: Any) -> None:
        event = {"kind": kind, "mission_id": self.context.mission_id if self.context else "", **payload}
        self._events.append(event)
        self._events[:] = self._events[-128:]
        if self._log:
            # Decisions and evidence only; model-private reasoning is never accepted.
            self._log("[UI_ENGINE] " + json.dumps(event, ensure_ascii=False, separators=(",", ":"))[:4000])

    def drain_events(self) -> list[dict[str, Any]]:
        events, self._events = self._events, []
        return events

    def _persist(self) -> None:
        if self.context is None:
            return
        self.context.expected_state = {"goal": [asdict(x) for x in self.goal]}
        self.context.observed_state = self.summary(include_progress=False)
        if self.pending:
            self.context.pending_action = {"action_id": self.pending.action_id,
                                           "signature": self.pending.signature,
                                           "expected": [asdict(x) for x in self.pending.conditions]}
        else:
            self.context.pending_action = {}
        if self.store is not None:
            self.store.save(self.context)

    def summary(self, *, include_progress: bool = True) -> dict[str, Any]:
        current = self.state.current
        result = {
            "mission_id": self.context.mission_id if self.context else "",
            "goal": [asdict(x) for x in self.goal],
            "observation_id": current.observation_id if current else "",
            "scope": current.scope.as_dict() if current else {},
            "pending_action_id": self.pending.action_id if self.pending else "",
            "pending_verification": self.pending_verification(),
            "goal_completed": bool(self.goal_verdict and self.goal_verdict.passed),
            "last_verification": self.pending.verdict.as_dict() if self.pending and self.pending.verdict else None,
            "action_count": self.action_count, "observation_count": self.observation_count,
            "cancelled": self.cancelled.is_set(),
        }
        if include_progress:
            result["progress"] = self.progress.summary()
        return result

    def _budget_error(self, name: str) -> str:
        if self.cancelled.is_set():
            return "UI_MISSION_CANCELLED"
        if time.monotonic()-self.started_at > float(settings.ui_mission_timeout_s):
            return "UI_MISSION_TIMEOUT"
        if name in MUTATION_TOOLS and self.action_count >= int(settings.ui_max_actions):
            return "UI_ACTION_BUDGET_EXHAUSTED"
        if name in {"observe_ui", "inspect_active_window", "observe_screen", "verify_ui_goal"}:
            if self.observation_count >= int(settings.ui_max_observations):
                return "UI_OBSERVATION_BUDGET_EXHAUSTED"
        return ""

    def execute(self, name: str, arguments: dict[str, Any], delegate: Callable[..., Any]):
        with self._lock:
            if name not in READ_TOOLS and name not in MUTATION_TOOLS:
                return delegate(name, arguments)
            if self.context is None:
                self.begin("")
            error = self._budget_error(name)
            if error:
                return _result(name, False, "Le budget ou l'annulation impose l'arrêt.", {"error": error})
            if name in MUTATION_TOOLS and self.goal_verdict and self.goal_verdict.passed:
                return _result(name, False, "L'objectif est déjà vérifié. Aucune action supplémentaire.", "GOAL_ALREADY_COMPLETED")
            try:
                if name == "ui_engine_status":
                    return _result(name, True, "État du moteur.", self.summary())
                if name == "define_ui_goal":
                    return self._define_goal(arguments)
                if name == "list_browser_pages":
                    if self.browser is None:
                        return _result(name, False, "Aucune session CDP configurée.", "browser_unavailable")
                    return _result(name, True, "Onglets de la session configurée.", {"pages": self.browser.pages()})
                if name == "verify_ui_goal":
                    return self._verify_goal(arguments)
                if name in {"observe_ui", "inspect_active_window", "observe_screen"}:
                    return self._observe(name, arguments)
                if name == "act_ui":
                    return self._act(arguments, delegate)
                return self._legacy_mutation(name, arguments, delegate)
            except (ValueError, TypeError, OverflowError) as exc:
                return _result(name, False, "Arguments UI non valides.", {"error": "INVALID_UI_REQUEST", "detail": str(exc)})

    def _define_goal(self, args: dict[str, Any]):
        if self.goal or any(x.get("operation") not in {"open_application", "open_file", "open_folder", "open_url"}
                            for x in self.receipts):
            return _result("define_ui_goal", False, "L'objectif ne peut pas être affaibli après le début des actions.",
                           "GOAL_ALREADY_FROZEN")
        goal = parse_conditions(args.get("conditions"))
        if self.state.current is None or self.state.current.generation != self.state.generation:
            return _result("define_ui_goal", False, "Observez d'abord l'état initial frais.", "GOAL_BASELINE_REQUIRED")
        request = normalized_text(self.context.user_goal if self.context else "")
        if any(word in request.split() for word in ("envoie", "envoyer", "send")) and not any(
            x.kind == "new_text" and (x.role == "message" or x.region) for x in goal
        ):
            return _result("define_ui_goal", False, "L'envoi exige une preuve de nouveau message dans son scope.",
                           "GOAL_MISSING_NEW_MESSAGE_PROOF")
        if any(x.kind == "new_text" for x in goal) and not any(
            (x.kind == "text_present" and x.value and (x.role == "content_title" or x.region)) or
            (x.kind in {"selected", "target_present"} and x.label and x.role in {
                "conversation_item", "contact_result", "content_title"}) or
            (x.kind == "title_contains" and x.value) for x in goal
        ):
            return _result("define_ui_goal", False, "Un nouveau message exige aussi une preuve de destinataire ou de contexte.",
                           "GOAL_MISSING_CONTEXT_PROOF")
        self.goal = goal
        self.goal_before = self.state.current
        self._emit("decision", reason="GOAL_FROZEN", predicates=[asdict(x) for x in goal])
        self._persist()
        return _result("define_ui_goal", True, "Objectif observable enregistré.", self.summary())

    def _observe(self, name: str, args: dict[str, Any]):
        error = self._budget_error("observe_ui")
        if error:
            return _result(name, False, "Le budget d'observation impose l'arrêt.", {"error": error})
        self.observation_count += 1
        target = args.get("target")
        if target is not None and not isinstance(target, dict):
            raise ValueError("target must be an object")
        current = self.state.current
        title = str(args.get("title") or (current.scope.title if self.pending and current else "") or "") or None
        page_ref = str(args.get("page_ref") or (current.scope.page_ref if self.pending and current else "") or "")
        result = self.perception.perceive(
            title=title, focus=str(args.get("focus") or ""), target=target,
            force_visual=name == "observe_screen" or args.get("force_visual") is True,
            page_ref=page_ref, crop=args.get("crop"),
        )
        if not result.success:
            return _result(name, False, result.message, object_detail(result.detail))
        observation = self.state.current
        strategy = "crop" if args.get("crop") else "vision" if name == "observe_screen" or args.get("force_visual") else "structured_or_fused"
        strategy += ":"+json.dumps({"crop":args.get("crop"), "target":target},sort_keys=True,ensure_ascii=False)
        allowed = self.progress.record_observation(observation, strategy)
        self._emit("observation", observation_id=observation.observation_id,
                   scope=observation.scope.as_dict(), coverage=observation.coverage,
                   sensors=observation.sensors, uncertainties=observation.uncertainties)
        self._emit("transition", **self.state.transition)
        self._check_pending(observation)
        self._check_goal(observation)
        self._persist()
        payload = object_detail(result.detail)
        payload["mission_state"] = self.summary()
        if not allowed:
            payload["error"] = "NO_NEW_EVIDENCE"
            return _result(name, False, "Cette inspection n'ajoute plus de preuve. Changez de région ou de stratégie.", payload)
        return _result(name, True, result.message, payload)

    def _check_pending(self, after: UIObservation) -> None:
        if self.pending is None or (self.pending.verdict and self.pending.verdict.passed):
            return
        verdict = verify_conditions(
            self.pending.conditions, before=self.pending.before, after=after,
            action_id=self.pending.action_id, require_transition=self.pending.require_transition,
            allow_document_transition=bool(self.pending.before and self.pending.before.scope.page_ref),
        )
        self.pending.verdict = verdict
        self.state.add_proof(verdict)
        self._emit("proof", verification=verdict.as_dict())
        if not self.pending.progress_recorded or verdict.passed:
            self.progress.record_action(semantic_signature=self.pending.signature, before=self.pending.before,
                                        after=after, verdict=verdict)
            self.pending.progress_recorded = True

    def _check_goal(self, after: UIObservation) -> None:
        if not self.goal:
            return
        # A send/new-text goal requires an action receipt as well as a new state.
        if any(x.kind == "new_text" for x in self.goal) and not self.receipts:
            return
        verdict = verify_conditions(
            self.goal, before=self.goal_before, after=after,
            action_id="goal:" + (self.context.mission_id if self.context else ""),
            allow_document_transition=bool(self.goal_before and self.goal_before.scope.page_ref),
        )
        if verdict.passed and not self.pending_verification():
            self.goal_verdict = verdict
            self.state.add_proof(verdict)
            self.context.status = MissionStatus.COMPLETED
            self.context.proof_refs = [verdict.action_id]
            self._emit("proof", goal_completed=True, verification=verdict.as_dict())
        else:
            self.goal_verdict = verdict if not verdict.passed else None

    def _verify_goal(self, args: dict[str, Any]):
        if not self.goal:
            return _result("verify_ui_goal", False, "Définissez les prédicats de l'objectif avant les actions.",
                           "GOAL_NOT_DEFINED")
        if not (self.goal_verdict and self.goal_verdict.passed and self.state.current and
                self.state.current.observation_id.startswith("native_proof_")):
            target = next((x for x in self.goal if x.role or x.label), None)
            observed = self._observe("observe_ui", {
                **args, "target": {"role": target.role, "label": target.label,
                                   "region": target.region, "operation": "read"} if target else None,
            })
            if not observed.success:
                return _result("verify_ui_goal", False, observed.message, object_detail(observed.detail))
        proven = bool(self.goal_verdict and self.goal_verdict.passed)
        return _result("verify_ui_goal", proven,
                       "Objectif vérifié." if proven else "Les preuves ne démontrent pas encore l'objectif.",
                       {"goal_completed": proven, "ui_verification": self.goal_verdict.as_dict() if self.goal_verdict else None,
                        "mission_state": self.summary()})

    def _act(self, args: dict[str, Any], delegate: Callable[..., Any]):
        if not isinstance(args.get("target"), dict):
            raise ValueError("Provide an observed or semantic target")
        operation = str(args.get("operation") or "")
        if operation not in {"click", "write", "scroll", "key"}:
            raise ValueError("Unsupported UI operation")
        conditions = parse_conditions(args.get("expected"))
        if not self.goal and operation != "write":
            return _result("act_ui", False, "Définissez d'abord le résultat observable de la mission.", "GOAL_NOT_DEFINED")
        if self.pending_verification():
            return _result("act_ui", False, "Vérifiez l'action précédente avant une nouvelle mutation.",
                           {"error": "PENDING_POSTCONDITION", "mission_state": self.summary()})
        intent = TargetIntent.from_dict({**args["target"], "operation": operation})
        current = self.state.current
        if current is None or current.generation != self.state.generation or (
            time.monotonic()-current.monotonic_at > float(settings.ui_target_max_age_s)
        ):
            # Semantic intentions can be reacquired; stale opaque refs must be replaced by the planner.
            if intent.ref:
                return _result("act_ui", False, "La ref n'est plus fraîche. Réobservez puis recopiez une nouvelle ref.",
                               "STALE_OBSERVATION")
            seen = self._observe("observe_ui", {"title": args.get("title"), "page_ref": args.get("page_ref"),
                                               "target": asdict(intent)})
            if not seen.success:
                return seen
            current = self.state.current
        resolution = resolve_target(intent, current, generation=self.state.generation,
                                    max_age_s=float(settings.ui_target_max_age_s))
        if resolution.status != "resolved":
            self._emit("decision", reason=resolution.reason, candidates=resolution.candidates)
            return _result("act_ui", False, "La cible n'est pas résolue de façon unique et fraîche.",
                           {"target_resolution": resolution.as_dict()})
        target = resolution.target
        if target.sensor == "vision" and target.score < float(settings.vision_min_confidence):
            return _result("act_ui", False, "La cible visuelle est sous le seuil d'exécution.",
                           "VISUAL_SUPPORT_BELOW_THRESHOLD")
        signature = json.dumps((operation, target.semantic_roles, target.label, target.region,
                                str(args.get("text") or ""), str(args.get("key") or "")), ensure_ascii=False)
        if not self.progress.may_repeat(current, signature):
            return _result("act_ui", False, "Cette action répétée n'a pas produit l'effet attendu.", "NO_EFFECT_LOOP")
        send = "send_button" in target.semantic_roles
        if operation == "key" and not current.scope.page_ref:
            if target.sensor == "vision" or target.focused is not True:
                return _result("act_ui", False, "Le contrôle structuré n'a pas de preuve de focus.", "focus_not_proven")
            try:
                import win32gui
                from .windows_perception import _native_identity
                from .ui_observation import SurfaceIdentity
                foreground = _native_identity(win32gui.GetForegroundWindow())
                if not current.scope.same_surface(SurfaceIdentity.from_payload(foreground)):
                    return _result("act_ui", False, "La fenêtre ciblée n'est plus au premier plan.", "WRONG_FOREGROUND_WINDOW")
            except Exception:
                return _result("act_ui", False, "Le focus de fenêtre n'est pas prouvé.", "WINDOW_FOCUS_NOT_PROVEN")
        if operation == "key" and str(args.get("key") or "").casefold() in {"enter", "return", "{enter}"}:
            send = send or "message_composer" in target.semantic_roles
        if send and not any(x.kind == "new_text" and (x.role == "message" or x.region) for x in conditions):
            return _result("act_ui", False, "Un envoi doit vérifier un nouveau message dans le bon scope.",
                           "SEND_POSTCONDITION_REQUIRED")
        if send:
            context = tuple(x for x in self.goal if (
                x.kind == "text_present" and (x.role == "content_title" or x.region) or
                x.kind in {"target_present", "selected"} and x.label and x.role in {
                    "conversation_item", "contact_result", "content_title"} or x.kind == "title_contains"))
            if not context or not verify_conditions(context, before=None, after=current).passed:
                return _result("act_ui", False, "Le contexte de l'envoi n'est pas prouvé dans l'état actuel.",
                               "SEND_CONTEXT_NOT_PROVEN")
        if target.sensor == "vision" and getattr(settings, "vision_actions_enabled", False) is not True:
            return _result("act_ui", False, "Actions visuelles désactivées.", "vision_actions_disabled")
        self.action_count += 1
        action_id = "action_" + uuid.uuid4().hex[:12]
        self.pending = PendingVerification(action_id, current, conditions, signature,
                                           operation != "write", non_idempotent=send)
        self.goal_verdict = None
        self._emit("decision", action_id=action_id, reason="TARGET_RESOLVED",
                   target_ref=target.ref, backend=target.sensor, evidence_ids=target.evidence_ids)
        try:
            delivered = self._deliver(target, current, operation, args, delegate)
        except Exception as exc:
            delivered = _result("act_ui", False, "L'issue de l'exécution est incertaine.", {"error": str(exc)})
        self.state.invalidate()
        from .windows_perception import invalidate_ui_snapshot
        invalidate_ui_snapshot()
        payload = object_detail(delivered.detail)
        self.receipts.append({"action_id": action_id, "operation": operation, "target_ref": target.ref,
                              "delivered": delivered.success, "backend": target.sensor})
        # Reuse the exact native read-back for a pure write, avoiding extra vision.
        if delivered.success and payload.get("verified") is True and operation == "write" and len(conditions) == 1:
            expected = conditions[0]
            text = str(args.get("text") or "")
            mode = str(args.get("mode") or "replace")
            if mode == "replace" and expected.kind == "value_equals" and expected.value == text:
                proof_id = "native_proof_" + uuid.uuid4().hex[:12]
                entity = replace(target, ref=proof_id+":value", native_ref="", value=text,
                                 evidence_ids=(action_id+":native_readback",))
                proof = UIObservation(proof_id, current.scope, (entity,), generation=-1,
                                      coverage={"tree_complete": False}, sensors={"uia": {"status": "exact_readback"}})
                self.state.observe(proof)
                self._check_pending(proof)
                self._check_goal(proof)
        if self.pending.verdict is None or not self.pending.verdict.passed:
            evidence_target = next((x for x in conditions if x.role or x.label), None)
            self._observe("observe_ui", {
                "title": current.scope.title, "page_ref": current.scope.page_ref,
                "force_visual": target.sensor == "vision",
                "target": {"role": evidence_target.role, "label": evidence_target.label,
                           "region": evidence_target.region, "operation": "read"} if evidence_target else None,
                "focus": "Verify these postconditions from visible evidence: " +
                         json.dumps([asdict(x) for x in conditions], ensure_ascii=False),
            })
        verdict = self.pending.verdict
        self._persist()
        return _result("act_ui", delivered.success, delivered.message, {
            "action_id": action_id, "target_ref": target.ref, "backend": target.sensor,
            "operation": operation,
            "execution": payload, "verified": bool(verdict and verdict.passed),
            "ui_verification": verdict.as_dict() if verdict else None,
            "after_observation": self.state.current.as_dict() if self.state.current else None,
            "mission_state": self.summary(),
        })

    def _deliver(self, target: UIEntity, before: UIObservation, operation: str,
                 args: dict[str, Any], delegate: Callable[..., Any]):
        if before.scope.page_ref:
            if target.sensor == "vision":
                return _result("act_ui", True, "Action navigateur délivrée.", self.browser.act_visual(
                    before.scope.page_ref, target, operation, args, self.perception.last_visual))
            return _result("act_ui", True, "Action DOM délivrée.", self.browser.act(
                before.scope.page_ref, target.native_ref, operation, args))
        if target.sensor != "vision" and operation in {"click", "write"}:
            tool = "write_ui_element" if operation == "write" else "click_ui_element"
            arguments = {"ref": target.native_ref}
            if operation == "write":
                arguments.update(text=str(args.get("text") or ""), mode=str(args.get("mode") or "replace"))
            return delegate(tool, arguments)
        if target.sensor == "vision" or operation == "scroll":
            if not settings.vision_actions_enabled:
                return _result("act_ui", False, "Actions visuelles désactivées.", "vision_actions_disabled")
            from .screen_vision import (click_grounded_visual_target, write_grounded_visual_target,
                                        scroll_grounded_visual_target)
            metadata = dict(self.perception.last_visual)
            if not metadata and target.sensor != "vision":
                # A structural target may need a physical wheel event; capture
                # geometry without calling a VLM or replacing its UIA identity.
                from .screen_vision import _capture_window_bytes
                _, metadata = _capture_window_bytes(before.scope.title)
            if not metadata:
                return _result("act_ui", False, "Une capture fraîche est nécessaire.", "visual_capture_missing")
            geometry = CaptureGeometry.from_metadata(metadata)
            l, t, r, b = geometry.physical_crop
            x1, y1, x2, y2 = target.bounds
            box = [(x1-l)/(r-l)*1000, (y1-t)/(b-t)*1000, (x2-l)/(r-l)*1000, (y2-t)/(b-t)*1000]
            metadata.update(box_1000=box, label=target.label, role=target.technical_role)
            if operation == "write":
                return write_grounded_visual_target(metadata, text=str(args.get("text") or ""),
                                                     mode=str(args.get("mode") or "replace"))
            if operation == "scroll":
                return scroll_grounded_visual_target(metadata, direction=str(args.get("direction") or "down"))
            return click_grounded_visual_target(metadata)
        if operation == "key":
            if target.focused is not True:
                return _result("act_ui", False, "Le contrôle n'a pas de preuve de focus.", "focus_not_proven")
            return delegate("press_key", {"key": str(args.get("key") or "")})
        raise ValueError("Unsupported target/backend")

    def _legacy_mutation(self, name: str, args: dict[str, Any], delegate: Callable[..., Any]):
        if name in {"click_ui_element", "click_visual_target", "write_visual_target",
                    "press_key", "type_text_active_window"}:
            return _result(name, False, "Utilisez act_ui avec la cible observée et ses postconditions.",
                           "EXPLICIT_POSTCONDITION_REQUIRED")
        if self.pending_verification():
            return _result(name, False, "Une postcondition reste à vérifier. Utilisez observe_ui ou verify_ui_goal.",
                           {"error": "PENDING_POSTCONDITION", "mission_state": self.summary()})
        before = self.state.current
        self.action_count += 1
        action_id = "action_" + uuid.uuid4().hex[:12]
        result = delegate(name, args)
        payload = object_detail(result.detail)
        self.state.invalidate()
        from .windows_perception import invalidate_ui_snapshot
        invalidate_ui_snapshot()
        self.goal_verdict = None
        self.receipts.append({"action_id": action_id, "operation": name, "delivered": result.success})
        if result.success and payload.get("verified") is True and name in {"write_ui_element", "close_tab", "close_window"}:
            verdict = VerificationVerdict("passed", action_id, before.observation_id if before else "",
                                          action_id+":native_readback",
                                          ({"predicate": "native_exact_postcondition", "evidence_ids": [action_id+":receipt"]},),
                                          "NATIVE_POSTCONDITION_VERIFIED")
            self.pending = PendingVerification(action_id, before, (), name, False, verdict=verdict)
            payload["ui_verification"] = verdict.as_dict()
            self._emit("proof", verification=verdict.as_dict())
        elif name in {"open_application", "open_file", "open_folder", "open_url"}:
            # Launch delivery stays distinct from the subsequent UI objective.
            self.pending = None
        else:
            # Legacy clicks cannot manufacture an expected transition. The model
            # must use act_ui with explicit postconditions on the experimental path.
            self.pending = PendingVerification(action_id, before, (), name, True)
            payload["requires_explicit_postcondition"] = True
            payload["verified"] = False
        payload.update(action_id=action_id, mission_state=self.summary())
        self._persist()
        return replace(result, detail=json.dumps(payload, ensure_ascii=False))


def ui_tool_definitions(make_tool: Callable[..., Any], *, browser_available: bool = False) -> list[dict[str, Any]]:
    condition = {"type": "object", "properties": {
        "kind": {"type": "string", "enum": sorted(CONDITION_KINDS)},
        "value": {"type": "string"}, "role": {"type": "string"}, "label": {"type": "string"}, "region": {"type": "string"},
    }, "required": ["kind"], "additionalProperties": False}
    conditions = {"type": "array", "items": condition, "minItems": 1, "maxItems": 24}
    target = {"type": "object", "properties": {key: {"type": "string"} for key in (
        "ref", "role", "label", "region", "within", "operation")}, "additionalProperties": False}
    observe_properties = {
        "title": {"type": "string"}, "page_ref": {"type": "string"}, "focus": {"type": "string"},
        "target": target, "force_visual": {"type": "boolean"},
        "crop": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4},
    }
    tools = [
        make_tool("observe_ui", "Observe et fusionne UIA/Cua/DOM puis vision seulement si la cible utile manque. "
                  "target peut préciser role/label/region. Les refs sont opaques et expirent après mutation. "
                  "force_visual/crop apportent une preuve ciblée nouvelle.", observe_properties, []),
        make_tool("define_ui_goal", "Avant une mission UI multi-étapes, définis TOUS les prédicats observables de "
                  "l'objectif utilisateur. Ils ne peuvent pas être affaiblis après action. Un envoi exige new_text "
                  "avec role=message ou une région précise, ainsi que le bon destinataire.", {"conditions": conditions}, ["conditions"]),
        make_tool("act_ui", "Résout une cible observée/sémantique, agit avec le meilleur backend puis vérifie "
                  "les postconditions expected sur une nouvelle observation. Utilise cette primitive pour les "
                  "clics et actions opaques. success signifie livraison ; verified et goal_completed prouvent le résultat.",
                  {"target": target, "operation": {"type": "string", "enum": ["click", "write", "scroll", "key"]},
                   "expected": conditions, "text": {"type": "string"},
                   "mode": {"type": "string", "enum": ["replace", "append", "insert"]},
                   "direction": {"type": "string", "enum": ["up", "down"]}, "key": {"type": "string"},
                   "title": {"type": "string"}, "page_ref": {"type": "string"}},
                  ["target", "operation", "expected"]),
        make_tool("verify_ui_goal", "Vérifie l'objectif enregistré, sur des preuves fraîches et dans le bon scope. "
                  "Ne termine une mission multi-étapes que si goal_completed=true.", observe_properties, []),
        make_tool("ui_engine_status", "Lit les prédicats, preuves, action en attente et progrès sans réinspecter l'écran.", {}, []),
    ]
    if browser_available:
        tools.append(make_tool("list_browser_pages", "Liste les onglets de la session CDP explicitement configurée, "
                               "sans lancer ni redémarrer de navigateur.", {}, []))
    return tools
