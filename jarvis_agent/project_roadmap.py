"""Release gates for Jarvis Personal — code progress is not live reliability.

A roadmap is not a product-quality percentage. Every milestone explicitly
separates implementation, automated verification, and real Windows acceptance.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Stage:
    number: int
    key: str
    title: str
    implementation: str  # integrated | in_progress | planned
    automated: str       # green_ancestor | pending | not_applicable
    windows_real: str    # full milestone acceptance; partial human reports are separate
    evidence: str        # known path/suite or future deliverable

    def as_dict(self) -> dict:
        return {
            "number": self.number,
            "key": self.key,
            "title": self.title,
            "implementation": self.implementation,
            "automated": self.automated,
            "windows_real": self.windows_real,
            "evidence": self.evidence,
        }


# Reviewed repository architecture; not a performance benchmark. Test passes
# here mean prior CI smoke/regression checks, not real desktop acceptance.
STAGES: tuple[Stage, ...] = (
    Stage(1, "agent_tools", "Cerveau & outils génériques", "integrated",
          "green_ancestor", "not_validated", "jarvis_agent/agent_runtime.py"),
    Stage(2, "browser_desktop", "Chrome + Windows / UIA / CUA", "integrated",
          "green_ancestor", "not_validated", "tests/browser_bridge_replay.mjs"),
    Stage(3, "memory", "Mémoire sémantique V5", "integrated",
          "green_ancestor", "not_validated", "tests/test_semantic_memory_v5.py"),
    Stage(4, "mission_foundations", "Kernel passif & missions persistées", "integrated",
          "green_ancestor", "not_validated", "tests/test_runtime_convergence.py"),
    Stage(5, "reliability", "Fiabilité inspirée d'Hermes", "integrated",
          "green_ancestor", "not_validated", "tests/test_hermes_reliability.py"),
    Stage(6, "observability", "Interface / supervision observables", "integrated",
          "green_ancestor", "not_validated", "tests/test_operator_console_ui.py"),
    Stage(7, "mission_live", "Missions contrôlables depuis l'interface", "integrated",
          "green_ancestor", "not_validated", "tests/test_mission_workbench.py"),
    Stage(8, "semantic_supervisor", "Preuves et superviseur sémantique", "integrated",
          "green_ancestor", "not_validated", "tests/test_semantic_goal_supervisor.py"),
    Stage(9, "multi_agent", "Missions continues, delegation et MCP (9D-9F)", "in_progress",
          "green_ancestor", "not_validated", "tests/test_mission_continuation.py"),
    Stage(10, "skills_learning", "Apprentissage & Skills validés", "planned",
          "not_applicable", "not_validated", "promotion contrôlée des expériences"),
    Stage(11, "integrations", "Intégrations personnelles et tâches durables", "planned",
          "not_applicable", "not_validated", "connecteurs + planificateur durable"),
)
CURRENT_STAGE_KEY = "multi_agent"


def snapshot() -> dict:
    stages = [stage.as_dict() for stage in STAGES]
    active = next(stage for stage in STAGES if stage.key == CURRENT_STAGE_KEY)
    return {
        "current": active.number,
        "total": len(stages),
        "stage_key": active.key,
        "current_title": active.title,
        "stages": stages,
        "release_ready": False,
        "human_windows_evidence": {
            "source": "user_continuation_brief_2026-10-09",
            "observed": ["conversation_text_voice", "microphone_off_in_text_mode", "windows_tts_fallback",
                         "chrome_normal_profile_youtube_navigation", "targeted_tab_close", "unicode_composer_input",
                         "installer_uia_intermediate_steps", "installer_close_dialog_recovery"],
            "not_independently_verified": ["message_recipient_delivery", "business_final_mutation",
                                           "installer_completion_and_desktop_shortcut", "memory_after_restart"],
        },
        "notice": (
            "Étapes 1–8 intégrées et testées automatiquement. "
            "9A-9C preservees; supervision, delegation et transports MCP integres. "
            "CI Windows/Linux verte au checkpoint 210e9d3. Continuation 9F, "
            "contexte V5 et geometrie Qt testes localement. OAuth personnel non connecte; "
            "Skills avances et taches durables en developpement. Parcours Windows reels "
            "signales par l'utilisateur; campagne finale complete encore attendue."
        ),
    }
