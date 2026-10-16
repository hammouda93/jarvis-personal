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
    Stage(10, "skills_learning", "Skills revisionnes et apprentissage opt-in", "in_progress",
          "green_ancestor", "not_validated", "tests/test_operational_preferences.py"),
    Stage(11, "integrations", "Centre MCP, espace personnel et tâches durables", "in_progress",
          "pending", "not_validated", "tests/test_mcp_control_center.py"),
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
        "development_priority": {
            "phase": "V19",
            "title": "Connexions simples et choix des fonctions en français",
            "implementation": "integrated",
            "automated": "local_regression_passed",
            "windows_real": "not_validated",
            "evidence": "docs/V19_FRIENDLY_CONNECTIONS.md",
            "next": "Valider Gmail sur le poste réel, puis reprendre ce parcours pour Calendar et les autres services",
        },
        "human_windows_evidence": {
            "source": "user_windows_logs_reviewed_2026-10-15",
            "observed": ["conversation_text_voice", "microphone_off_in_text_mode", "windows_tts_fallback",
                         "chrome_normal_profile_youtube_navigation", "targeted_tab_close", "unicode_composer_input",
                         "installer_uia_intermediate_steps", "installer_close_dialog_recovery",
                         "raw_memory_after_restart", "session_recall", "uia_replace_append",
                         "v13_empty_document_precondition_refused_old_buffer", "v13_new_document_unicode_readback",
                         "v13_code_recall_after_restart", "v13_google_search_dom_observed"],
            "not_independently_verified": ["message_recipient_delivery", "business_final_mutation",
                                           "installer_completion_and_desktop_shortcut", "semantic_projection_cold_start",
                                           "composed_browser_windows_mission", "saved_file_exact_content"],
            "v12_reported_failures": ["mission_control_wrapped_provider_context", "search_bootstrap_before_article",
                                      "new_document_replaced_existing_buffer"],
            "v13_retest_required": ["independent_goal_after_blocked_mission", "search_and_article_summary",
                                    "new_document_preserves_existing_text", "architecture_ui_real_windows"],
            "v13_reported_failures": ["independent_memory_goal_classified_as_continue", "context_growth_blocks_fallback",
                                       "browser_find_timeout", "provider_pause_after_close_window"],
            "v14_retest_required": ["profile_editor_real_windows", "new_goal_context_isolation",
                                    "continued_mission_constraints_and_receipts", "search_article_summary_with_provider_cascade"],
            "v14_reported_failures": ["saved_fact_missing_from_next_request", "selected_schema_removed_by_budget",
                                      "youtube_no_dispatch", "installer_no_dispatch", "incomplete_turn_displayed_as_success"],
            "v15_retest_required": ["saved_movie_followed_by_site_search", "selected_tools_dispatch_after_budget_reduction",
                                    "memory_date_lookup", "notepad_direct_open", "downloaded_installer_discovery"],
            "v16_retest_required": ["personal_profile_and_timezone", "daily_activity_after_restart",
                                    "business_follow_up_via_chat", "local_preparation_without_dispatch",
                                    "mcp_service_binding_and_live_result"],
            "v17_retest_required": ["isolated_specialist_reports", "spatial_cockpit_windows",
                                    "scheduled_work_on_awake_machine", "owner_voice_mcp_and_actual_phone_session"],
            "v18_retest_required": ["pipedream_google_oauth_on_owner_pc", "actual_gmail_inventory_and_account_identity",
                                    "gmail_reads_with_configured_brains", "confirmed_gmail_write_and_service_receipt"],
            "v19_retest_required": ["friendly_connection_flow_on_windows", "existing_partial_permissions_preserved",
                                    "advanced_settings_accessible_in_narrow_panel"],
        },
        "notice": (
            "Étapes 1–8 intégrées et testées automatiquement. "
            "9A-9C preservees; supervision, delegation et transports MCP integres. "
            "CI Windows/Linux verte sur V17 8b463a6 (1490 tests Windows). "
            "Recette V13 du 15 octobre : mémoire après redémarrage, protection du document et écriture Unicode observées. "
            "Contexte volumineux et résumé navigateur restent à valider. "
            "V14 ajoute les profils opérateur et un budget de requête ; la recette a révélé une perte du fait récent et des outils sélectionnés. "
            "V15 corrige ces régressions et l'affichage des tours suspendus. Recette Windows réelle encore attendue. "
            "V16 ajoute le profil personnel, les activités datées et les préparations locales dans QUOTIDIEN, accessibles aux outils de Jarvis. "
            "Les MCP seront connectés séparément ; une préparation ne vaut ni envoi ni résultat externe. "
            "Priorité : gestion quotidienne, football personnel, MS Football et projets logiciels. "
            "V17 ajoute des conversations spécialisées séparées, deux analyses parallèles, une file de veille locale et un univers interactif. "
            "Les agents lisent et préparent ; les actions externes restent à Jarvis. La téléphonie exige un service MCP vocal réel. "
            "V18 ajoute la connexion Gmail guidée via Pipedream ou un serveur existant, la sélection des capacités et une vérification du compte. "
            "V19 simplifie ce parcours avec des groupes de fonctions en français et des réglages avancés repliés. "
            "Skills et apprentissage OFF par defaut. Centre MCP et controle d'acces ajoutes. OAuth personnel non connecte; "
            "Skills avances et taches durables en developpement. Parcours Windows reels "
            "signales par l'utilisateur; campagne finale complete encore attendue."
        ),
    }
