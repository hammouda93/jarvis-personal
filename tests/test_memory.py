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

    def test_unknown_memory_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = LocalMemory(Path(tmp) / "memory.sqlite3")
            self.assertEqual(memory.search("quelque chose"), [])


if __name__ == "__main__":
    unittest.main()
