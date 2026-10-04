"""Shared voice/text completion gate and bounded, valid JSON model feedback."""
from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from .computer_use_controller import MUTATION_TOOLS, READ_TOOLS, single_native_request
from .semantic_grounding import normalized_text
from .ui_observation import object_detail


def compact_ui_tool_detail(detail: str, max_chars: int = 3500) -> str:
    payload = object_detail(detail)
    if not payload:
        return json.dumps({"error": str(detail)[:1200]}, ensure_ascii=False)
    # Preserve contracts/proofs; raw trees and binary artifacts never enter the planner.
    for key in ("accessibility_tree", "ax_tree", "screenshot", "image", "artifact", "controls",
                "capabilities", "observation_json", "observation", "artifacts", "visual_observation"):
        payload.pop(key, None)
    payload = json.loads(json.dumps(payload, ensure_ascii=False))
    observations = [payload.get(key) for key in ("ui_observation", "after_observation")]
    for observation in observations:
        if isinstance(observation, dict):
            observation["entities"] = observation.get("entities", [])[:24]
            observation["text_regions"] = observation.get("text_regions", [])[:16]
    def encode():
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    encoded = encode()
    while len(encoded) > max_chars:
        largest = max((obs for obs in observations if isinstance(obs, dict)
                       and (obs.get("entities") or obs.get("text_regions"))),
                      key=lambda obs: len(json.dumps(obs, ensure_ascii=False)), default=None)
        if largest is None:
            reduced = {key: payload[key] for key in (
                "error", "action_id", "verified", "ui_verification", "mission_state",
                "goal_completed") if key in payload} | {"planner_payload_reduced": True}
            def bound(value, limit):
                if isinstance(value,str): return value[:limit]
                if isinstance(value,list): return [bound(x,limit) for x in value[:24]]
                if isinstance(value,dict): return {k:bound(v,limit) for k,v in value.items()}
                return value
            for limit in (400,100,20):
                encoded=json.dumps(bound(reduced,limit),ensure_ascii=False,separators=(",", ":"))
                if len(encoded)<=max_chars: return encoded
            return json.dumps({"planner_payload_reduced":True,"action_id":payload.get("action_id"),
                "verified":payload.get("verified"),"goal_completed":payload.get("mission_state",{}).get("goal_completed"),
                "reason":"READ_UI_ENGINE_STATUS_FOR_PROOF"},ensure_ascii=False)
        key = "entities" if len(largest.get("entities", [])) > 8 else "text_regions"
        if not largest.get(key):
            key = "entities"
        largest[key] = largest[key][:max(0, len(largest[key])//2)]
        largest["planner_payload_truncated"] = True
        encoded = encode()
    return encoded


class ComputerUseRuntime:
    """Wrap any existing provider; Cerebras remains the planner."""
    def __init__(self, delegate: Any, tools: Any):
        self.delegate, self.tools = delegate, tools

    def reset(self):
        self.tools.ui_controller.cancel()
        self.delegate.reset()

    def cancel(self):
        self.tools.ui_controller.cancel()

    def warm_up(self, *, log=None):
        self.delegate.warm_up(log=log)

    @staticmethod
    def _single_native_proven(controller) -> bool:
        if controller.goal or len(controller.receipts) != 1:
            return False
        receipt = controller.receipts[0]
        operation = receipt.get("operation")
        if operation == "write":
            if not receipt.get("native_exact_write"):
                return False
            operation = "write_ui_element"
        return bool(receipt.get("delivered") and controller.pending and controller.pending.verdict
                    and controller.pending.verdict.passed
                    and single_native_request(controller.context.user_goal, operation))

    def run(self, user_text: str, *, log=None, phase=None):
        from .agent_runtime import AgentTurnResult

        request = normalized_text(user_text)
        approval = any(
            isinstance(getattr(self.delegate, key, None), dict)
            for key in ("_pending_function_approval", "_pending_mcp_approval")
        )
        if not approval and request in {
            "merci", "tres bien", "parfait", "super", "ok", "d accord",
            "c est bon", "oui c est bon", "thank you", "thanks", "great",
            "tres bien merci", "merci jarvis", "merci beaucoup", "parfait merci",
        }:
            return AgentTurnResult("Avec plaisir.", mission_status="answered")

        controller = self.tools.ui_controller
        if request in {"stop", "arrete", "annule", "cancel"}:
            controller.cancel()
            return AgentTurnResult(
                "Mission arrêtée.",
                goal_completed=False,
                mission_status="cancelled",
            )

        resume = bool(controller.context) and (
            approval
            or request in {
                "continue", "continuer", "poursuis", "reprends", "reessaie", "retry",
            }
        )
        controller.begin(
            user_text,
            mission_id=getattr(self.tools, "active_mission_id", "") or "",
            log=log,
            resume=resume,
        )

        # One model-owned loop only. Groq/Cerebras/Ollama/OpenAI already own
        # their tool-call loop. A second delegate.run() here duplicated planning,
        # polluted intent with UI checkpoint text, and multiplied API calls.
        result = self.delegate.run(user_text, log=log, phase=phase)
        actions = tuple(getattr(result, "actions", ()) or ())

        involved_ui = bool(
            controller.receipts
            or controller.goal
            or any(action.name in READ_TOOLS | MUTATION_TOOLS for action in actions)
        )
        if not involved_ui:
            return replace(result, mission_status="answered", goal_completed=None)

        summary = controller.summary()
        proven = summary["goal_completed"] and not summary["pending_verification"]
        if not controller.goal:
            proven = self._single_native_proven(controller)

        if log:
            reason = (
                "GOAL_VERIFIED"
                if proven
                else "GOAL_NOT_DEFINED"
                if not controller.goal
                else "GOAL_NOT_VERIFIED"
            )
            log(
                f"[UI_MISSION] goal_defined={int(bool(controller.goal))} "
                f"goal_completed={int(proven)} "
                f"pending_verification={int(summary['pending_verification'])} "
                f"reason={reason}"
            )

        if proven:
            return replace(
                result,
                goal_completed=True,
                mission_status="verified_complete",
                verification=summary,
            )

        pending_approval = any(
            isinstance(getattr(self.delegate, key, None), dict)
            for key in ("_pending_function_approval", "_pending_mcp_approval")
        )
        if pending_approval:
            return replace(
                result,
                goal_completed=False,
                mission_status="awaiting_approval",
                verification=summary,
            )

        # Read-only observations do not need an artificial frozen goal just to
        # be returned to the user. Likewise, a successful launcher receipt is
        # useful delivery evidence but is not falsely promoted to goal proof.
        relevant_actions = [
            action
            for action in actions
            if action.name in READ_TOOLS | MUTATION_TOOLS
        ]
        read_only = bool(relevant_actions) and all(
            action.name in READ_TOOLS for action in relevant_actions
        )
        launch_tools = {"open_application", "open_file", "open_folder", "open_url"}
        launch_only = bool(relevant_actions) and all(
            action.name in READ_TOOLS | launch_tools for action in relevant_actions
        ) and any(
            action.success and action.name in launch_tools
            for action in relevant_actions
        )
        blocked_goal_mutation = any(
            (not action.success)
            and action.name in MUTATION_TOOLS
            and (
                action.detail == "GOAL_NOT_DEFINED"
                or "GOAL_NOT_DEFINED" in str(action.detail)
            )
            for action in relevant_actions
        )

        if not controller.goal and not blocked_goal_mutation and (read_only or launch_only):
            return replace(
                result,
                goal_completed=None,
                mission_status="observed" if read_only else "delivered",
                verification=summary,
            )

        # No second hidden planner loop: expose the unresolved proof state.
        text = (
            "La mission n'est pas encore vérifiée. "
            + (
                "L'action précédente reste à confirmer ; je ne la répète pas sans nouvelle preuve."
                if summary["pending_verification"]
                else "Les observations ne prouvent pas encore tous les résultats demandés."
            )
        )
        return replace(
            result,
            text=text,
            goal_completed=False,
            mission_status="inconclusive",
            verification=summary,
        )
