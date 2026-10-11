"""Generic local-artifact intent and browser-to-desktop context tests.

No application names, installer selectors, real OS actions or paid model calls.
"""
from __future__ import annotations

import unittest

from jarvis_agent.foundation_tools import FoundationToolAdapter
from jarvis_agent.instruction_clauses import has_windows_objective
from jarvis_agent.native_tools import AgentActionResult


class LocalArtifactScopeV10JTests(unittest.TestCase):
    def test_generic_installer_file_language_is_windows_objective(self):
        for phrase in (
            "Ouvre le setup téléchargé.",
            "Lance le programme d'installation téléchargé.",
            "Ouvre le fichier téléchargé.",
            "Démarre le setup dans Téléchargements.",
            "Ouvre ExampleUserSetup.exe depuis Downloads.",
            "Open the downloaded installer.",
        ):
            with self.subTest(phrase=phrase):
                self.assertTrue(has_windows_objective(phrase))

    def test_web_only_request_does_not_change_to_windows_scope(self):
        for phrase in (
            "Ouvre le navigateur Chrome.",
            "Reviens dans l'onglet du site web.",
            "Ouvre la page web https://example.org",
        ):
            with self.subTest(phrase=phrase):
                self.assertFalse(has_windows_objective(phrase))

    def test_browser_context_releases_for_local_setup_then_open_file(self):
        class Delegate:
            def execute(self, name, arguments, *, approved=False):
                return AgentActionResult(
                    name=name, success=True,
                    message="generic delegated call",
                    detail="file-dispatch-only",
                )

        adapter = FoundationToolAdapter(Delegate(), browser=object())
        adapter.browser_mode = True
        adapter.begin_turn("Lance le setup téléchargé depuis Downloads.")
        self.assertFalse(adapter.browser_mode)
        result = adapter.execute(
            "open_file",
            {"name": "ExampleUserSetup", "within": "Downloads"},
        )
        self.assertTrue(result.success)
        self.assertEqual(result.name, "open_file")

    def test_quoted_names_do_not_change_execution_domain(self):
        adapter = FoundationToolAdapter(object(), browser=object())
        adapter.browser_mode = True
        adapter.begin_turn('Ouvre le "ExampleUserSetup.exe" téléchargé.')
        self.assertFalse(adapter.browser_mode)

    def test_browser_objective_retains_browser_domain(self):
        adapter = FoundationToolAdapter(object(), browser=object())
        adapter.browser_mode = True
        adapter.begin_turn("Reviens à la page du site web.")
        self.assertTrue(adapter.browser_mode)


if __name__ == "__main__":
    unittest.main()
