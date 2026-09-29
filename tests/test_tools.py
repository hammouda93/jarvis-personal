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

    def test_search_web(self):
        intent = route("recherche météo tunis sur internet")
        self.assertEqual(intent.name, "browser.search")
        self.assertEqual(intent.args["query"], "meteo tunis")

    def test_unknown_is_safe(self):
        intent = route("supprime tous mes fichiers")
        self.assertEqual(intent.name, "unknown")


if __name__ == "__main__":
    unittest.main()
