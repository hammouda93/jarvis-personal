"""General mission regressions. No live UI, provider or personal-data access."""
import json
import unittest
from unittest.mock import Mock, patch

from jarvis_agent.agent_runtime import (GroqResponsesAgent, _requested_action_capabilities,
    _requests_result_selection, _requests_ui_submission, _requests_search_submission)
from jarvis_agent.foundation_tools import FoundationToolAdapter
from jarvis_agent.native_tools import AgentActionResult
from jarvis_agent.tools import route


class GeneralMissionTests(unittest.TestCase):
    def test_prohibitions_are_not_positive_capabilities(self):
        for text in ("Ferme uniquement ce document. Ne ferme aucun autre onglet.",
                     "Ouvre le fichier et lis le texte; ne recherche rien sur le web.",
                     "Never close tabs or write text.", "N'ecris pas dans ce document."):
            with self.subTest(text=text):
                self.assertEqual(_requested_action_capabilities(text), set())

    def test_positive_action_and_negative_constraint_remain_separate(self):
        self.assertEqual(_requested_action_capabilities(
            "Ecris le texte dans ce champ, puis ne ferme aucun onglet."), {"write_ui"})
        self.assertEqual(_requested_action_capabilities(
            "Ferme cet onglet. Ne modifie aucun fichier."), {"close_tab"})

    def test_conditional_or_quoted_action_is_not_a_required_repair(self):
        self.assertEqual(_requested_action_capabilities('Explique la phrase "ferme cet onglet".'), set())
        self.assertEqual(_requested_action_capabilities("Si necessaire, ferme cet onglet."), set())

    def test_displayed_is_not_display_imperative(self):
        for text in ("Verifie le texte reellement affiche dans le Bloc-notes",
                     "Lis la valeur affichee dans une application inconnue"):
            self.assertEqual(route(text).name, "unknown")
        self.assertEqual(route("Affiche Bloc-notes").name, "app.open")

    def test_compound_or_forbidden_close_does_not_enter_direct_fastpath(self):
        for text in ("Ne ferme aucun onglet", "Ouvre un fichier puis ouvre Chrome",
                     "Ferme ce document. Ne ferme aucun autre onglet."):
            self.assertEqual(route(text).name, "unknown")
        self.assertEqual(route("Ferme cet onglet").name, "browser.close_tab")

    def test_fresh_browser_snapshot_does_not_remove_file_discovery(self):
        delegate = Mock()
        delegate.ollama_tools.return_value = [
            {"type": "function", "function": {"name": name, "description": "local",
                "parameters": {"type": "object", "properties": {}}}}
            for name in ("open_file", "list_windows", "browser_observe_dom", "browser_verify")]
        agent = GroqResponsesAgent(delegate)
        agent._ephemeral_context = "BROWSER_GROUNDING_READ_ONLY:{}"
        names = {item["function"]["name"] for item in agent._tool_definitions()}
        self.assertIn("open_file", names)
        self.assertIn("list_windows", names)

    def test_windows_request_after_browser_resets_scope_without_app_allowlist(self):
        adapter = FoundationToolAdapter(Mock(), browser=object())
        adapter.browser_mode = True
        adapter.begin_turn("Retrouve sur mon Bureau le fichier dont le nom commence par Fixture")
        self.assertFalse(adapter.browser_mode)

    def test_browser_to_file_to_browser_to_file_in_same_mission(self):
        delegate, browser = Mock(), Mock()
        delegate.execute.return_value = AgentActionResult("open_file", True, "Observed")
        browser.call.return_value = {"verified": True, "tab": {"tab_id": 7}}
        adapter = FoundationToolAdapter(delegate, browser=browser)
        adapter.begin_turn("Ouvre le fichier Fixture. Ouvre ensuite le navigateur. Reviens au document.")
        adapter.execute("browser_activate_tab", {"tab_id": 7})
        self.assertTrue(adapter.browser_mode)
        result = adapter.execute("open_file", {"query": "Fixture"})
        self.assertTrue(result.success)
        self.assertFalse(adapter.browser_mode)
        adapter.execute("browser_activate_tab", {"tab_id": 7})
        self.assertTrue(adapter.browser_mode)
        adapter.execute("open_file", {"query": "Fixture"})
        self.assertFalse(adapter.browser_mode)

    def test_browser_only_or_negative_windows_request_keeps_os_guard(self):
        delegate = Mock()
        adapter = FoundationToolAdapter(delegate, browser=object())
        adapter.begin_turn("Ouvre le navigateur. Ne modifie aucun fichier.")
        self.assertTrue(adapter.browser_mode)
        result = adapter.execute("press_key", {"key": "Ctrl+S"})
        self.assertFalse(result.success)
        delegate.execute.assert_not_called()

    def test_browser_document_is_not_a_windows_scope_grant(self):
        adapter = FoundationToolAdapter(Mock(), browser=object())
        adapter.begin_turn("Modifie le document dans le navigateur")
        self.assertTrue(adapter.browser_mode)
        self.assertFalse(adapter.execute("press_key", {"key": "Ctrl+S"}).success)

    def test_negative_or_optional_selection_and_submission_do_not_create_repairs(self):
        self.assertFalse(_requests_result_selection("Ne clique jamais le premier resultat."))
        self.assertFalse(_requests_ui_submission("Si necessaire, envoie le message."))
        self.assertFalse(_requests_search_submission("Ne lance aucune recherche."))
        self.assertTrue(_requests_result_selection("Ouvre le premier resultat."))
