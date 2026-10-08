"""Deterministic, non-authoritative semantic supervisor for Jarvis missions.

Does NOT plan, call LLMs, infer goal success from tool responses, or dispatch.
Only a trusted verifier may register actual goal evidence upstream.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class StepReview:
    step_id: str
    intent: str
    state: str
    waiting_for: tuple[str, ...]
    unmet_dependencies: tuple[str, ...]
    recorded_evidence: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.step_id,
            "intent": self.intent,
            "state": self.state,
            "waiting_for": list(self.waiting_for),
            "unmet_dependencies": list(self.unmet_dependencies),
            "recorded_evidence": list(self.recorded_evidence),
        }


def evaluate_mission(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Read-only proof/dependency check of *recorded* mission facts.

    It cannot independently authenticate a proof reference: the existing
    register_goal_evidence trusted-verifier boundary must do that. This
    evaluator deliberately never turns a tool success into a goal proof.
    """
    status = str(snapshot.get("status") or "")
    blocked = bool(snapshot.get("manual_review_required")) or status == "blocked"
    actual_goal_verified = (
        snapshot.get("goal_verified") is True and status == "completed"
    )
    contract = snapshot.get("semantic_plan")
    evidence = snapshot.get("plan_evidence") or {}
    if not isinstance(evidence, dict):
        evidence = {}
    evidence_keys = {
        str(key) for key, ref in evidence.items()
        if str(ref or "").strip()
    }
    tasks = snapshot.get("tasks") or ()
    action_count = sum(len(task.get("action_names") or ())
                       for task in tasks if isinstance(task, dict))
    task_failures = sum(
        str(task.get("status") or "") in {"failed", "waiting_external"}
        for task in tasks if isinstance(task, dict)
    )

    if not isinstance(contract, dict):
        state = ("manual_review" if blocked else
                 "goal_verified" if actual_goal_verified else
                 "plan_needed")
        next_action = ("independent_review" if blocked else
                       "none" if actual_goal_verified else
                       "register_plan")
        return {
            "mission_id": str(snapshot.get("mission_id") or "")[:70],
            "status": status,
            "state": state,
            "next_action": next_action,
            "goal_verified": actual_goal_verified,
            "plan_registered": False,
            "action_count": action_count,
            "failed_or_uncertain_turns": task_failures,
            "steps": [],
            "unresolved": [],
            "required_evidence": [],
            "recorded_evidence": [],
            "missing_evidence": [],
            "note": "Aucun plan sémantique enregistré; réussite non présumée.",
        }

    steps = contract.get("steps") or []
    if not isinstance(steps, list):
        steps = []
    unresolved = [
        str(item)[:120] for item in (contract.get("unresolved") or [])[:20]
    ]
    # Existing contract validates cycles and duplicate step IDs at admission.
    # Recheck untrusted/persisted representations defensively.
    valid = {
        str(step.get("step_id") or "") for step in steps
        if isinstance(step, dict)
    }
    reviews: list[StepReview] = []
    already_verified: set[str] = set()
    remaining = [s for s in steps[:24] if isinstance(s, dict)]
    # Iterate in dependency order, not necessarily the stored source order.
    for _ in range(len(remaining) + 1):
        progressed = False
        for entry in list(remaining):
            sid = str(entry.get("step_id") or "")[:80]
            dependencies = [
                str(dep) for dep in (entry.get("depends_on") or [])[:24]
            ]
            known_steps = {r.step_id for r in reviews}
            if any(dep in valid and dep not in known_steps for dep in dependencies):
                continue
            required = tuple(dict.fromkeys(
                str(item)[:120]
                for item in (entry.get("required_evidence") or [])[:24]
                if str(item or "").strip()
            ))
            recorded = tuple(x for x in required if x in evidence_keys)
            missing = tuple(x for x in required if x not in evidence_keys)
            unmet = tuple(dep for dep in dependencies if dep not in already_verified)
            if not required:
                # A step without explicit evidence is NOT automatically verified.
                current = "evidence_spec_needed" if not unmet else "waiting_dependency"
            elif missing:
                current = "waiting_evidence" if not unmet else "waiting_dependency"
            else:
                current = "verified" if not unmet else "waiting_dependency"
            if current == "verified":
                already_verified.add(sid)
            reviews.append(StepReview(
                sid, str(entry.get("intent") or "")[:180],
                current, missing, unmet, recorded,
            ))
            remaining.remove(entry)
            progressed = True
        if not progressed:
            break
    for item in remaining:
        sid = str(item.get("step_id") or "")[:80]
        reviews.append(StepReview(
            sid, str(item.get("intent") or "")[:180],
            "invalid_dependencies", (), (), (),
        ))

    required_all = tuple(dict.fromkeys(
        need for step in reviews
        for need in (*step.waiting_for, *step.recorded_evidence)
    ))
    missing_all = tuple(need for need in required_all if need not in evidence_keys)
    if blocked:
        state, next_action = "manual_review", "independent_review"
    elif unresolved:
        state, next_action = "clarification_required", "clarify_entities"
    elif remaining:
        state, next_action = "invalid_plan", "repair_plan"
    elif actual_goal_verified:
        state, next_action = "goal_verified", "none"
    elif any(step.state == "evidence_spec_needed" for step in reviews):
        state, next_action = "evidence_spec_needed", "define_goal_evidence"
    elif missing_all:
        state, next_action = "evidence_missing", "observe_and_verify"
    else:
        # Even complete step proofs do NOT prove the whole mission; a
        # separate trusted goal completion gate exists on the live runtime.
        state, next_action = "goal_proof_pending", "independent_goal_review"
    return {
        "mission_id": str(snapshot.get("mission_id") or "")[:70],
        "status": status,
        "state": state,
        "next_action": next_action,
        "goal_verified": actual_goal_verified,
        "plan_registered": True,
        "action_count": action_count,
        "failed_or_uncertain_turns": task_failures,
        "steps": [s.as_dict() for s in reviews],
        "unresolved": unresolved,
        "required_evidence": list(required_all),
        "recorded_evidence": [
            x for x in required_all if x in evidence_keys
        ],
        "missing_evidence": list(missing_all),
        "note": (
            "Une réussite d'outil n'est pas une preuve finale. "
            "Supervision non autoritaire; aucun outil exécuté."
        ),
    }
