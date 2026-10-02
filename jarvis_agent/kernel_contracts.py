from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class MissionStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    WAITING_USER = "waiting_user"
    WAITING_EXTERNAL = "waiting_external"
    BLOCKED = "blocked"
    FAILED = "failed"
    COMPLETED = "completed"


class EventKind(str, Enum):
    MISSION_CREATED = "mission.created"
    USER_INPUT = "user.input"
    INTENT_RESOLVED = "intent.resolved"
    AGENT_SELECTED = "agent.selected"
    LLM_REQUEST = "llm.request"
    LLM_RESULT = "llm.result"
    TOOL_REQUESTED = "tool.requested"
    TOOL_RESULT = "tool.result"
    OBSERVATION = "observation"
    PROOF = "proof"
    USER_FEEDBACK = "user.feedback"
    CORRECTION_CANDIDATE = "correction.candidate"
    TEST_STARTED = "test.started"
    TEST_RESULT = "test.result"
    REPLAY_STARTED = "replay.started"
    REPLAY_RESULT = "replay.result"
    MISSION_COMPLETED = "mission.completed"
    MISSION_FAILED = "mission.failed"


class KnowledgeScope(str, Enum):
    CORE = "core"
    APP = "app"
    DOMAIN = "domain"
    AGENT = "agent"
    SKILL = "skill"
    USER = "user"
    TEST = "test"
    SESSION = "session"


class PromotionTarget(str, Enum):
    CORE_INVARIANT = "core_invariant"
    APP_PROFILE = "app_profile"
    DOMAIN_RULE = "domain_rule"
    AGENT_POLICY = "agent_policy"
    SKILL = "skill"
    USER_PREFERENCE = "user_preference"
    REGRESSION_TEST = "regression_test"
    SESSION_ONLY = "session_only"


class RiskLevel(str, Enum):
    READ = "read"
    REVERSIBLE = "reversible"
    EXTERNAL_SIDE_EFFECT = "external_side_effect"
    DESTRUCTIVE = "destructive"


@dataclass(frozen=True)
class CapabilitySpec:
    name: str
    description: str
    risk: RiskLevel = RiskLevel.READ
    requires_confirmation: bool = False
    tags: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["risk"] = self.risk.value
        data["tags"] = list(self.tags)
        return data


@dataclass(frozen=True)
class AgentManifest:
    agent_id: str
    display_name: str
    version: str
    capabilities: tuple[str, ...]
    allowed_tools: tuple[str, ...] = ()
    allowed_connectors: tuple[str, ...] = ()
    memory_scopes: tuple[KnowledgeScope, ...] = ()
    max_concurrency: int = 1
    description: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "display_name": self.display_name,
            "version": self.version,
            "capabilities": list(self.capabilities),
            "allowed_tools": list(self.allowed_tools),
            "allowed_connectors": list(self.allowed_connectors),
            "memory_scopes": [scope.value for scope in self.memory_scopes],
            "max_concurrency": int(self.max_concurrency),
            "description": self.description,
        }


@dataclass
class MissionContext:
    mission_id: str
    user_goal: str
    status: MissionStatus = MissionStatus.CREATED
    parent_mission_id: str | None = None
    user_id: str | None = None
    owner_agent_id: str | None = None
    current_step: str = ""
    pending_confirmation: bool = False
    expected_state: dict[str, Any] = field(default_factory=dict)
    observed_state: dict[str, Any] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


@dataclass
class CorrectionCandidate:
    candidate_id: str
    mission_id: str
    summary: str
    proposed_scope: KnowledgeScope
    promotion_target: PromotionTarget
    source_event_ids: list[str] = field(default_factory=list)
    user_id: str | None = None
    agent_id: str | None = None
    app_id: str | None = None
    domain: str | None = None
    skill_id: str | None = None
    test_ids: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    validated: bool = False
    rejected: bool = False

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["proposed_scope"] = self.proposed_scope.value
        data["promotion_target"] = self.promotion_target.value
        return data
