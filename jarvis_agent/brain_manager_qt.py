"""User-managed provider panel. Secrets live in Windows Credential Manager, not JSON."""
from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QComboBox, QTableWidget, QTableWidgetItem, QMessageBox, QHeaderView,
)

from .brain_cascade import (
    ExtraBrain, BrainConfigError, load_extra_brains, save_extra_brains,
    store_api_key, delete_api_key, resolved_api_key, _PRESETS,
)


class BrainProbe(QThread):
    """An explicit, user-triggered inexpensive tool-call probe."""
    finished_probe = Signal(str)

    def __init__(self, brain: ExtraBrain, parent=None):
        super().__init__(parent)
        self.brain = brain

    def run(self):
        try:
            from openai import OpenAI
            key = resolved_api_key(self.brain)
            if not key:
                raise RuntimeError("credential_missing")
            client = OpenAI(api_key=key, base_url=self.brain.base_url,
                            timeout=self.brain.timeout_s, max_retries=0)
            tool = {"type": "function", "function": {
                "name": "jarvis_probe", "description": "Return successful probe",
                "parameters": {"type": "object", "properties": {}},
            }}
            args = {
                "model": self.brain.model,
                "messages": [{"role": "user", "content": "Call the jarvis_probe function once."}],
                "tools": [tool], "tool_choice": "auto",
                self.brain.completion_parameter: 128,
            }
            result = client.chat.completions.create(**args)
            choices = getattr(result, "choices", []) or []
            calls = getattr(choices[0].message, "tool_calls", []) if choices else []
            if not any(call.function.name == "jarvis_probe" for call in (calls or [])):
                raise RuntimeError("tool_calling_not_confirmed")
            self.finished_probe.emit("Connexion et function calling vérifiés.")
        except Exception as exc:
            # Never render API errors verbatim; SDK error bodies can include
            # provider-side content or sensitive request details.
            self.finished_probe.emit("Test non validé : " + type(exc).__name__)


class BrainManagerPanel(QWidget):
    """Manage *additional* brains. First three are immutable legacy defaults."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._brains: tuple[ExtraBrain, ...] = ()
        self._probe = None
        layout = QVBoxLayout(self)
        info = QLabel(
            "CASCADE : Cerebras Primary → Cerebras Secondary → Groq → modèles ajoutés. "
            "Les trois premiers sont préservés. Toute clé ajoutée reste dans le coffre Windows."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        self.table = QTableWidget(0, 4, self)
        self.table.setHorizontalHeaderLabels(["Priorité", "Fournisseur", "Modèle", "État"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        layout.addWidget(self.table)

        form = QVBoxLayout()
        self.identifier = QLineEdit(self)
        self.identifier.setPlaceholderText("Identifiant unique (ex. gemini-perso)")
        form.addWidget(self.identifier)

        self.provider = QComboBox(self)
        self.provider.addItems(["gemini", "openai", "grok", "openrouter", "custom"])
        self.provider.currentTextChanged.connect(self._provider_changed)
        form.addWidget(self.provider)

        self.model = QLineEdit(self)
        self.model.setPlaceholderText("Identifiant exact du modèle API")
        form.addWidget(self.model)

        self.endpoint = QLineEdit(self)
        self.endpoint.setPlaceholderText("URL de base HTTPS de l'API")
        form.addWidget(self.endpoint)

        self.environment = QLineEdit(self)
        self.environment.setPlaceholderText("Variable d'environnement alternative (facultatif)")
        form.addWidget(self.environment)

        self.credential = QLineEdit(self)
        self.credential.setPlaceholderText("Clé API — coffre Windows, jamais dans JSON")
        self.credential.setEchoMode(QLineEdit.Password)
        form.addWidget(self.credential)
        layout.addLayout(form)

        row = QHBoxLayout()
        add = QPushButton("Ajouter")
        add.clicked.connect(self._add)
        row.addWidget(add)
        remove = QPushButton("Supprimer")
        remove.clicked.connect(self._remove)
        row.addWidget(remove)
        toggle = QPushButton("Activer / désactiver")
        toggle.clicked.connect(self._toggle)
        row.addWidget(toggle)
        layout.addLayout(row)

        row2 = QHBoxLayout()
        move_up = QPushButton("Monter")
        move_up.clicked.connect(lambda: self._move(-1))
        row2.addWidget(move_up)
        move_down = QPushButton("Descendre")
        move_down.clicked.connect(lambda: self._move(1))
        row2.addWidget(move_down)
        probe = QPushButton("Tester l'API")
        probe.clicked.connect(self._test)
        row2.addWidget(probe)
        layout.addLayout(row2)

        self.status = QLabel("Les changements prennent effet au prochain démarrage de Jarvis.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self._provider_changed(self.provider.currentText())
        self._refresh()

    def _provider_changed(self, provider: str):
        self.endpoint.setText(_PRESETS.get(provider, ""))

    def _refresh(self):
        try:
            self._brains = load_extra_brains()
        except BrainConfigError as exc:
            self.status.setText("Configuration invalide : " + str(exc))
            self._brains = ()
        builtins = [
            ("1", "Cerebras Primary", "GPT-OSS 120B", "Hérité"),
            ("2", "Cerebras Secondary", "GPT-OSS 120B", "Hérité"),
            ("3", "Groq", "GPT-OSS 120B", "Hérité"),
        ]
        extras = [
            (str(i + 4), brain.provider + " / " + brain.id, brain.model,
             "Activé" if brain.enabled else "Désactivé")
            for i, brain in enumerate(self._brains)
        ]
        self.table.setRowCount(len(builtins) + len(extras))
        for i, cells in enumerate(builtins + extras):
            for j, value in enumerate(cells):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~__import__("PySide6.QtCore", fromlist=["Qt"]).Qt.ItemIsEditable)
                self.table.setItem(i, j, item)

    def _selection(self):
        row = self.table.currentRow() - 3
        if row < 0 or row >= len(self._brains):
            self.status.setText("Sélectionne un cerveau ajouté (priorité 4 ou suivante).")
            return None
        return row

    def _persist(self, brains):
        try:
            save_extra_brains(brains)
            self.status.setText("Configuration enregistrée. Redémarre Jarvis pour l'activer.")
            self._refresh()
        except (OSError, BrainConfigError) as exc:
            self.status.setText("Enregistrement impossible : " + type(exc).__name__)

    def _add(self):
        raw = {
            "id": self.identifier.text().strip(),
            "provider": self.provider.currentText(),
            "model": self.model.text().strip(),
            "base_url": self.endpoint.text().strip(),
            "api_key_env": self.environment.text().strip(),
        }
        try:
            brain = ExtraBrain.parse(raw)
            if brain.id in {item.id for item in self._brains}:
                raise BrainConfigError("duplicate_brain_id")
            if self.credential.text():
                store_api_key(brain.id, self.credential.text())
            self._persist([*self._brains, brain])
            self.credential.clear()
        except Exception as exc:
            self.credential.clear()
            self.status.setText("Ajout impossible : " + type(exc).__name__ + " — " +
                                str(exc)[:100] if isinstance(exc, BrainConfigError)
                                else "Ajout impossible : " + type(exc).__name__)

    def _remove(self):
        row = self._selection()
        if row is None:
            return
        brain = self._brains[row]
        if QMessageBox.question(self, "Supprimer", "Supprimer la configuration " + brain.id + " ?") != QMessageBox.Yes:
            return
        self._persist([item for item in self._brains if item.id != brain.id])
        try:
            delete_api_key(brain.id)
        except Exception:
            pass

    def _toggle(self):
        row = self._selection()
        if row is not None:
            entries = list(self._brains)
            entries[row] = replace(entries[row], enabled=not entries[row].enabled)
            self._persist(entries)

    def _move(self, direction):
        row = self._selection()
        if row is None:
            return
        target = row + direction
        if not 0 <= target < len(self._brains):
            return
        entries = list(self._brains)
        entries[row], entries[target] = entries[target], entries[row]
        self._persist(entries)
        self.table.selectRow(target + 3)

    def _test(self):
        row = self._selection()
        if row is None:
            return
        if self._probe is not None and self._probe.isRunning():
            return
        if QMessageBox.question(
            self, "Test API payant",
            "Tester ce modèle enverra une petite requête API potentiellement facturée. Continuer ?",
        ) != QMessageBox.Yes:
            return
        self.status.setText("Test de connexion et function calling en cours…")
        self._probe = BrainProbe(self._brains[row], self)
        self._probe.finished_probe.connect(self.status.setText)
        self._probe.start()
