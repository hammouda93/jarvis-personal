"""Two independent opt-in switches; the worker applies and persists changes."""
import json
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QLabel, QVBoxLayout, QWidget
from .config import settings
from .operational_preferences import effective_flags


class OperationalPreferencesPanel(QWidget):
    requested = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._flags = effective_flags(settings)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.skills = QCheckBox("Utiliser les Skills")
        self.learning = QCheckBox("Apprentissage opérationnel")
        self.feedback = QLabel("")
        self.feedback.setWordWrap(True)
        layout.addWidget(self.skills)
        layout.addWidget(self.learning)
        layout.addWidget(self.feedback)
        self._render()
        self.skills.toggled.connect(self._submit)
        self.learning.toggled.connect(self._submit)

    def _render(self):
        for control, key in ((self.skills, "skills_enabled"), (self.learning, "learning_enabled")):
            control.blockSignals(True)
            control.setChecked(self._flags[key])
            control.setEnabled(True)
            control.blockSignals(False)

    def _submit(self):
        self.skills.setEnabled(False)
        self.learning.setEnabled(False)
        self.feedback.setText("Changement en attente...")
        self.requested.emit("policy_set", json.dumps({"skills_enabled": self.skills.isChecked(),
                                                    "learning_enabled": self.learning.isChecked()}))

    def show_result(self, result):
        if result.get("success"):
            self._flags = result["preferences"]
            self.feedback.setText("Réglages enregistrés.")
        else:
            self.feedback.setText("Réglages inchangés : " + str(result.get("reason", "erreur"))[:100])
        self._render()
