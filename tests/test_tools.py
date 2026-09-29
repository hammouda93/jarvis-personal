import unittest

from jarvis_agent.tools import route


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

    def test_unknown_is_safe(self):
        intent = route("supprime tous mes fichiers")
        self.assertEqual(intent.name, "unknown")


if __name__ == "__main__":
    unittest.main()
