import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jarvis_agent.tools import _chrome_profile_directory, route


class ToolRouterTests(unittest.TestCase):
    def test_open_youtube(self):
        intent = route("Jarvis, ouvre YouTube")
        self.assertEqual(intent.name, "browser.open_url")
        self.assertEqual(intent.args["url"], "https://www.youtube.com")

    def test_open_vscode(self):
        intent = route("ouvre VS Code")
        self.assertEqual(intent.name, "app.open")
        self.assertEqual(intent.args["app"], "vscode")

    def test_time(self):
        intent = route("Quelle heure est-il ?")
        self.assertEqual(intent.name, "system.time")

    def test_time_variant(self):
        intent = route("Dis-moi quelle heure il est")
        self.assertEqual(intent.name, "system.time")

    def test_search_web_suffix(self):
        intent = route("recherche météo Tunis sur internet")
        self.assertEqual(intent.name, "browser.search")
        self.assertEqual(intent.args["query"], "meteo tunis")

    def test_search_web_prefix(self):
        intent = route("Recherche sur Internet à propos des agents IA")
        self.assertEqual(intent.name, "browser.search")
        self.assertEqual(intent.args["query"], "agents ia")

    def test_search_without_subject_requests_followup(self):
        intent = route("Recherche sur Internet")
        self.assertEqual(intent.name, "browser.search_prompt")

    def test_open_snipping_tool(self):
        intent = route("ouvre l'outil capture écran")
        self.assertEqual(intent.name, "app.open")
        self.assertEqual(intent.args["app"], "snippingtool")

    def test_sleep_session(self):
        intent = route("c'est tout")
        self.assertEqual(intent.name, "assistant.sleep")


    def test_time_joined_stt_variant(self):
        intent = route("Quelleur est-il ?")
        self.assertEqual(intent.name, "system.time")

    def test_downloads_misheard_as_chargements(self):
        intent = route("Ouvre les chargements")
        self.assertEqual(intent.name, "folder.open")
        self.assertEqual(intent.args["folder"], "downloads")

    def test_downloads_misheard_oufre(self):
        intent = route("Oufre téléchargement")
        self.assertEqual(intent.name, "folder.open")
        self.assertEqual(intent.args["folder"], "downloads")

    def test_a_plus_sleeps_session(self):
        intent = route("Jarvis a plus")
        self.assertEqual(intent.name, "assistant.sleep")


    def test_close_jarvis(self):
        intent = route("Fermez Jarvis")
        self.assertEqual(intent.name, "assistant.stop")


    def test_misheard_auvre_youtube(self):
        intent = route("Auvre YouTube")
        self.assertEqual(intent.name, "browser.open_url")

    def test_named_folder_directly(self):
        intent = route("Ouvre le dossier baristas")
        self.assertEqual(intent.name, "folder.open_named")
        self.assertEqual(intent.args["query"], "baristas")

    def test_vague_folder_request_asks_followup(self):
        intent = route("Je veux ouvrir un dossier spécifique")
        self.assertEqual(intent.name, "folder.open_prompt")


    def test_nested_named_folder(self):
        intent = route("Ouvre le dossier media dans baristas")
        self.assertEqual(intent.name, "folder.open_named")
        self.assertEqual(intent.args["query"], "media")
        self.assertEqual(intent.args["within"], "baristas")

    def test_chrome_last_used_profile_is_discovered(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = (
                Path(tmp)
                / "Google"
                / "Chrome"
                / "User Data"
                / "Local State"
            )
            state.parent.mkdir(parents=True)
            state.write_text(
                json.dumps({"profile": {"last_used": "Profile 3"}}),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"LOCALAPPDATA": tmp}):
                self.assertEqual(
                    _chrome_profile_directory(),
                    "Profile 3",
                )

    def test_unknown_is_safe(self):
        intent = route("supprime tous mes fichiers")
        self.assertEqual(intent.name, "unknown")


if __name__ == "__main__":
    unittest.main()
