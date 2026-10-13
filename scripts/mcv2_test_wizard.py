"""Interactive disposable wizard for LIVE acceptance. No system installation.

User/Jarvis operates the visible controls. This program supplies no UI driver,
tool dispatch, model client, licence acceptance or administrator escalation.
"""
from pathlib import Path
import json
import uuid
import sys

from PySide6.QtWidgets import QApplication, QWizard, QWizardPage, QVBoxLayout, QLabel, QCheckBox


class RecipeWizard(QWizard):
    def __init__(self, receipt_dir=None):
        super().__init__()
        self.setWindowTitle("Assistant de recette Jarvis — simulation")
        self.setMinimumSize(620, 360)
        self.setButtonText(QWizard.NextButton, "Suivant")
        self.setButtonText(QWizard.BackButton, "Précédent")
        self.setButtonText(QWizard.FinishButton, "Terminer la simulation")
        self.setButtonText(QWizard.CancelButton, "Annuler")
        self.receipt_dir = Path(receipt_dir or Path(__file__).resolve().parents[1] / ".cache")
        self.receipt_path = self.receipt_dir / ("mcv2_wizard_" + uuid.uuid4().hex + ".json")
        self.completed = False
        first = QWizardPage()
        first.setTitle("Autorisation de simulation")
        layout = QVBoxLayout(first)
        layout.addWidget(QLabel("Cet assistant ne fait aucune installation réelle.\n"
                                "Il teste uniquement des écrans et un reçu local de recette."))
        self.consent = QCheckBox("J'autorise uniquement cette simulation locale")
        self.consent.setObjectName("recipeConsent")
        layout.addWidget(self.consent)
        first.registerField("simulation_consent*", self.consent)
        self.addPage(first)
        second = QWizardPage()
        second.setTitle("Vérifier la portée")
        scope_layout = QVBoxLayout(second)
        scope_layout.addWidget(QLabel("Aucun programme installé, aucune licence acceptée,\n"
                                     "aucun fichier utilisateur modifié."))
        self.addPage(second)
        final = QWizardPage()
        final.setTitle("Résultat")
        self.result_label = QLabel("La simulation attend encore le bouton Terminer.")
        self.result_label.setWordWrap(True)
        final_layout = QVBoxLayout(final)
        final_layout.addWidget(self.result_label)
        self.addPage(final)

    def accept(self):
        if self.completed:
            super().accept()
            return
        if not self.field("simulation_consent"):
            return
        self.receipt_dir.mkdir(parents=True, exist_ok=True)
        # Unique file and exclusive create: no existing file can be overwritten.
        with self.receipt_path.open("x", encoding="utf-8") as stream:
            json.dump({"fixture_version": 1, "status": "simulated_completed",
                       "system_installation": False}, stream)
        self.completed = True
        self.result_label.setText("Terminé — simulation uniquement.\nReçu local : " + str(self.receipt_path))
        self.button(QWizard.BackButton).setEnabled(False)
        self.setButtonText(QWizard.FinishButton, "Fermer")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    wizard = RecipeWizard()
    wizard.show()
    sys.exit(app.exec())
