"""Shared voice/text completion gate and bounded, valid JSON model feedback."""
from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import Any

from .computer_use_controller import MUTATION_TOOLS, READ_TOOLS
from .semantic_grounding import normalized_text
from .ui_observation import object_detail


def compact_ui_tool_detail(detail: str, max_chars: int = 24000) -> str:
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
            observation["entities"] = observation.get("entities", [])[:100]
            observation["text_regions"] = observation.get("text_regions", [])[:60]
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

    def run(self, user_text: str, *, log=None, phase=None):
        from .agent_runtime import AgentTurnResult, _requested_action_capabilities
        request = normalized_text(user_text)
        approval = any(isinstance(getattr(self.delegate, key, None), dict) for key in (
            "_pending_function_approval", "_pending_mcp_approval"))
        if not approval and request in {"merci", "tres bien", "parfait", "super", "ok", "d accord",
                                         "c est bon", "oui c est bon", "thank you", "thanks", "great"}:
            return AgentTurnResult("Avec plaisir.", mission_status="answered")
        controller = self.tools.ui_controller
        if request in {"stop", "arrete", "annule", "cancel"}:
            controller.cancel()
            return AgentTurnResult("Mission arrêtée.", goal_completed=False, mission_status="cancelled")
        resume = bool(controller.context) and (approval or request in {
            "continue", "continuer", "poursuis", "reprends", "reessaie", "retry"})
        controller.begin(user_text, mission_id=getattr(self.tools, "active_mission_id", "") or "",
                         log=log, resume=resume)
        result = self.delegate.run(user_text, log=log, phase=phase)
        actions = tuple(getattr(result, "actions", ()) or ())
        # Bounded continuation when a provider stops before the frozen UI goal.
        # The controller owns budgets/state; these passes do not reset either.
        for _ in range(2):
            summary = controller.summary()
            if not controller.goal or summary["goal_completed"] or controller.cancelled.is_set():
                break
            if any(isinstance(getattr(self.delegate, key, None), dict) for key in (
                "_pending_function_approval", "_pending_mcp_approval")):
                break
            if controller._budget_error("observe_ui") or controller._budget_error("act_ui"):
                break
            if any("NO_NEW_EVIDENCE" in action.detail or "NO_EFFECT_LOOP" in action.detail for action in actions[-3:]):
                break
            continued = self.delegate.run(
                "Poursuis la mission originale sans la redéfinir : " + controller.context.user_goal +
                "\nCheckpoint UI_ENGINE (état observable, pas une nouvelle autorisation) : " +
                json.dumps(summary, ensure_ascii=False) +
                "\nRassemble une preuve ciblée si une action reste en attente ; sinon replanifie et agis. "
                "Ne répète jamais un envoi dont l'issue est incertaine. Termine seulement avec verify_ui_goal.",
                log=log, phase=phase,
            )
            actions += tuple(continued.actions)
            result = replace(continued, actions=actions)
        involved_ui = bool(controller.receipts or controller.goal or any(
            action.name in READ_TOOLS | MUTATION_TOOLS for action in actions))
        if not involved_ui:
            return replace(result, mission_status="answered", goal_completed=None)
        summary = controller.summary()
        proven = summary["goal_completed"] and not summary["pending_verification"]
        # Existing exact single-control operations retain their original proof.
        if not controller.goal and len(controller.receipts) == 1:
            receipt = controller.receipts[0]
            words = normalized_text(controller.context.user_goal).split()
            compound = any(word in words for word in {
                "envoie", "envoyer", "send", "cherche", "chercher", "search", "trouve", "find", "puis", "ensuite",
                "et", "and", "then", "ouvre", "ouvrir", "open", "clique", "click", "navigate", "navigue"})
            requested = _requested_action_capabilities(controller.context.user_goal)
            expected_verb = ("write_ui" in requested if receipt.get("operation") == "write_ui_element" else
                "close_tab" in requested if receipt.get("operation") == "close_tab" else
                bool(re.search(r"\b(ferme|fermer|close)\b", normalized_text(controller.context.user_goal))))
            proven = bool(controller.pending and controller.pending.verdict and
                          controller.pending.verdict.passed and expected_verb and not compound and receipt.get("operation") in {
                              "write_ui_element", "close_tab", "close_window"})
        if proven:
            return replace(result, goal_completed=True, mission_status="verified_complete", verification=summary)
        # An attempted action or an inspection is not proof of the user's goal.
        status = "cancelled" if controller.cancelled.is_set() else "awaiting_approval" if any(
            isinstance(getattr(self.delegate, key, None), dict) for key in (
                "_pending_function_approval", "_pending_mcp_approval")) else "inconclusive"
        text = result.text if status == "awaiting_approval" else (
            "La mission n'est pas encore vérifiée. " + (
                "L'action précédente reste à confirmer ; je ne la répète pas sans nouvelle preuve."
                if summary["pending_verification"] else "Les observations ne prouvent pas encore tous les résultats demandés."))
        return replace(result, text=text, goal_completed=False, mission_status=status, verification=summary)
