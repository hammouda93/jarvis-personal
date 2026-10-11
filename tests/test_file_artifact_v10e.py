import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from jarvis_agent.file_artifact import verify_file_artifact, guard_saved_file_answer
from jarvis_agent.native_tools import AgentActionResult, NATIVE_TOOLS


class FileArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "fixture.txt"
        self.path.write_bytes("Texte francais 67890\r\n".encode())
        self.args = {"path": str(self.path), "expected_name": self.path.name,
                     "expected_text": "Texte francais 67890\n"}

    def test_actual_path_name_and_content_are_independently_read(self):
        result = verify_file_artifact(self.args)
        self.assertTrue(result["verified"])
        self.assertEqual(result["path"], str(self.path.resolve()))
        self.assertEqual(result["name"], self.path.name)
        self.assertNotIn("content", result)
        self.assertTrue(self.path.exists())

    def test_wrong_name_path_or_content_never_verifies(self):
        for override in ({"expected_name": "invented.txt"}, {"path": str(self.path.with_name("missing.txt"))},
                         {"expected_text": "Wrong"}):
            self.assertFalse(verify_file_artifact({**self.args, **override})["verified"])

    def test_exists_alone_is_not_content_proof_and_network_paths_are_rejected(self):
        for args in ({"path": str(self.path), "expected_name": self.path.name},
                     {**self.args, "path": "\\\\fixture-host\\share\\file.txt"},
                     {**self.args, "path": "fixture.txt"}):
            self.assertFalse(verify_file_artifact(args)["verified"])

    def test_binary_hash_verification_and_size_bound(self):
        self.path.write_bytes(b"\x00\xffBinary fixture")
        digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        args = {"path": str(self.path), "expected_name": self.path.name, "expected_sha256": digest}
        self.assertTrue(verify_file_artifact(args)["verified"])
        self.assertFalse(verify_file_artifact({**args, "expected_sha256": "0" * 64})["verified"])
        with patch("jarvis_agent.file_artifact.MAX_ARTIFACT_BYTES", 3):
            self.assertFalse(verify_file_artifact(args)["verified"])

    def test_native_tool_is_read_only_and_shortcut_never_proves_save(self):
        result = NATIVE_TOOLS.execute("verify_file_artifact", self.args)
        self.assertTrue(result.success)
        self.assertTrue(json.loads(result.detail)["verified"])
        shortcut = AgentActionResult("press_key", True, "Dispatched", '{"verified":true}')
        answer, guarded = guard_saved_file_answer("Enregistre ce fichier", "Le fichier est enregistre.", [shortcut])
        self.assertTrue(guarded)
        self.assertIn("pas de preuve", answer)
        self.assertEqual(guard_saved_file_answer("Enregistre ce fichier", "Verifie.", [result]), ("Verifie.", False))

    def test_memory_storage_claim_is_not_a_file_save_claim(self):
        answer = "Le fait est enregistre en memoire."
        memory = AgentActionResult("semantic_memory_store", True, answer,
                                   '{"stored_raw":true,"memory_id":"fixture"}')
        self.assertEqual(guard_saved_file_answer("Enregistre ce fait en memoire", answer, [memory]),
                         (answer, False))
