import tempfile
import unittest
from pathlib import Path

from jarvis_agent.memory import LocalMemory


class LocalMemoryTests(unittest.TestCase):
    def test_remember_and_recall(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = LocalMemory(Path(tmp) / "memory.sqlite3")
            item = memory.remember(
                "J'aime le golf le week-end.",
                tags="sport loisirs",
            )

            results = memory.search("golf")

            self.assertGreater(item.id, 0)
            self.assertEqual(len(results), 1)
            self.assertIn("golf", results[0].content.lower())

    def test_multiword_query_requires_all_significant_terms(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = LocalMemory(Path(tmp) / "memory.sqlite3")
            memory.remember(
                "Projet Atlas : ajouter un espace client",
                tags="Atlas",
            )
            memory.remember(
                "Mon autre projet de test s'appelle Polaris 418",
                tags="projet test",
            )

            self.assertEqual(memory.search("Atlas Nova"), [])
            atlas = memory.search("projet Atlas")
            self.assertEqual(len(atlas), 1)
            self.assertIn("Atlas", atlas[0].content)

    def test_recall_ignores_common_question_words(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = LocalMemory(Path(tmp) / "memory.sqlite3")
            memory.remember(
                "J'aime le golf le week-end.",
                tags="sport loisirs",
            )

            results = memory.search("Quel sport j'aime ?")

            self.assertEqual(len(results), 1)
            self.assertIn("golf", results[0].content.lower())

    def test_recall_generic_title_question_prefers_latest_matching_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = LocalMemory(Path(tmp) / "memory.sqlite3")
            memory.remember(
                "Nom du film à regarder ultérieurement",
                tags="film",
            )
            memory.remember(
                "Mon film test est Arrival",
                tags="film test",
            )

            results = memory.search("quel est le nom de mon film")

            self.assertGreaterEqual(len(results), 1)
            self.assertEqual(results[0].content, "Mon film test est Arrival")

    def test_unknown_memory_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = LocalMemory(Path(tmp) / "memory.sqlite3")
            self.assertEqual(memory.search("quelque chose"), [])


if __name__ == "__main__":
    unittest.main()
