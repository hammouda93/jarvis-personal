"""Offscreen fixture checks; these are not real Windows LIVE acceptance."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from PySide6.QtWidgets import QApplication

spec = importlib.util.spec_from_file_location("recipe_wizard", Path(__file__).resolve().parents[1] / "scripts/mcv2_test_wizard.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class RecipeWizardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_without_consent_no_receipt(self):
        with tempfile.TemporaryDirectory() as root:
            wizard = module.RecipeWizard(root)
            wizard.accept()
            self.assertFalse(wizard.receipt_path.exists())
            wizard.close()

    def test_simulation_completion_creates_only_unique_receipt(self):
        with tempfile.TemporaryDirectory() as root:
            witness = Path(root) / "witness.txt"
            witness.write_text("preserve me", encoding="utf-8")
            wizard = module.RecipeWizard(root)
            wizard.consent.setChecked(True)
            wizard.accept()
            receipt = json.loads(wizard.receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(receipt["status"], "simulated_completed")
            self.assertFalse(receipt["system_installation"])
            self.assertEqual(witness.read_text(encoding="utf-8"), "preserve me")
            self.assertEqual(len(list(Path(root).iterdir())), 2)
            wizard.close()
