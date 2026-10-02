from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .kernel_contracts import CorrectionCandidate
from .supervisor_planner import SupervisorPlan


@dataclass(frozen=True)
class SupervisorValidationState:
    candidate_id: str
    tests_passed: bool
    replay_passed: bool
    proof_present: bool
    ready_for_user_validation: bool
    blockers: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "tests_passed": self.tests_passed,
            "replay_passed": self.replay_passed,
            "proof_present": self.proof_present,
            "ready_for_user_validation": self.ready_for_user_validation,
            "blockers": list(self.blockers),
        }


class SupervisorValidationGate:
    """Require evidence before asking the user to validate a correction."""

    def evaluate(
        self,
        candidate: CorrectionCandidate,
        plan: SupervisorPlan,
        *,
        test_results: dict[str, bool] | None = None,
        replay_success: bool | None = None,
        proof_refs: list[str] | None = None,
    ) -> SupervisorValidationState:
        tests = dict(test_results or {})
        required_tests = list(plan.selected_test_ids)
        tests_passed = all(
            tests.get(test_id) is True
            for test_id in required_tests
        )

        if plan.requires_replay:
            replay_passed = replay_success is True
        else:
            replay_passed = True

        proof_present = bool(proof_refs or candidate.evidence)
        blockers: list[str] = []

        if candidate.validated or candidate.rejected:
            blockers.append("candidate_already_resolved")
        if required_tests and not tests_passed:
            blockers.append("regression_tests_not_green")
        if plan.requires_replay and not replay_passed:
            blockers.append("replay_not_green")
        if not proof_present:
            blockers.append("proof_missing")

        ready = (
            not blockers
            and bool(plan.requires_user_validation)
        )
        return SupervisorValidationState(
            candidate_id=candidate.candidate_id,
            tests_passed=tests_passed,
            replay_passed=replay_passed,
            proof_present=proof_present,
            ready_for_user_validation=ready,
            blockers=tuple(blockers),
        )
