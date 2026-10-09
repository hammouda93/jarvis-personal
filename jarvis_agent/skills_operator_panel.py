"""Worker-backed local procedural knowledge editor; never executes a Skill."""
from __future__ import annotations

import json
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
    QPushButton, QStyle, QVBoxLayout, QWidget,
)


class SkillsPanel(QWidget):
    requested = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._skill = None
        self._busy = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        header.addWidget(QLabel("SKILLS"), 1)
        self.refresh_button = QPushButton()
        self.refresh_button.setFixedSize(30, 30)
        self.refresh_button.setIcon(self.style().standardIcon(QStyle.SP_BrowserReload))
        self.refresh_button.setToolTip("Actualiser les Skills locaux")
        header.addWidget(self.refresh_button)
        layout.addLayout(header)
        self.picker = QComboBox()
        self.picker.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.picker.setMinimumContentsLength(12)
        self.picker.addItem("Aucun Skill charge", "")
        layout.addWidget(self.picker)
        self.enabled = QCheckBox("Actif")
        layout.addWidget(self.enabled)
        self.metadata = QLabel("")
        self.metadata.setWordWrap(True)
        layout.addWidget(self.metadata)
        self.goal = QLineEdit()
        self.goal.setMaxLength(500)
        self.scope = QLineEdit()
        self.scope.setMaxLength(120)
        self.procedure = QPlainTextEdit()
        self.checks = QPlainTextEdit()
        self.failures = QPlainTextEdit()
        for label, control in (("Objectif", self.goal), ("Application / domaine", self.scope),
                               ("Procedure", self.procedure), ("Criteres de reussite", self.checks),
                               ("Points de vigilance", self.failures)):
            layout.addWidget(QLabel(label))
            if isinstance(control, QPlainTextEdit):
                control.setFixedHeight(84)
                control.setMaximumBlockCount(24)
            layout.addWidget(control)
        self.save_button = QPushButton("Enregistrer")
        self.save_button.setIcon(self.style().standardIcon(QStyle.SP_DialogSaveButton))
        self.save_button.setToolTip("Enregistrer une nouvelle revision locale")
        layout.addWidget(self.save_button)
        layout.addWidget(QLabel("Historique"))
        self.versions = QComboBox()
        self.versions.setMinimumContentsLength(12)
        self.versions.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        layout.addWidget(self.versions)
        self.restore_button = QPushButton("Restaurer")
        self.restore_button.setIcon(self.style().standardIcon(QStyle.SP_BrowserReload))
        self.restore_button.setToolTip("Restaurer la procedure selectionnee dans une nouvelle revision")
        layout.addWidget(self.restore_button)
        self.feedback = QLabel("")
        self.feedback.setWordWrap(True)
        layout.addWidget(self.feedback)
        self.refresh_button.clicked.connect(lambda: self._request("list", {}))
        self.picker.currentIndexChanged.connect(self._select)
        self.enabled.toggled.connect(self._toggle)
        self.save_button.clicked.connect(self._save)
        self.restore_button.clicked.connect(self._restore)
        self._controls()

    def _controls(self):
        loaded = self._skill is not None and not self._busy
        for control in (self.enabled, self.goal, self.scope, self.procedure, self.checks,
                        self.failures, self.save_button, self.versions):
            control.setEnabled(loaded)
        self.restore_button.setEnabled(loaded and self.versions.count() > 0)
        self.refresh_button.setEnabled(not self._busy)
        self.picker.setEnabled(not self._busy)

    def _request(self, operation, payload):
        if self._busy:
            return
        self._busy = True
        self._controls()
        self.feedback.setText("En attente...")
        self.requested.emit(operation, json.dumps(payload, ensure_ascii=False))

    def _select(self):
        name = self.picker.currentData()
        if name:
            self._request("view", {"name": name})

    def _bound(self):
        return {"name": self._skill["name"], "expected_version": self._skill["version"]}

    def _toggle(self, enabled):
        if self._skill:
            self._request("enable" if enabled else "disable", self._bound())

    def _save(self):
        if not self._skill:
            return
        self._request("edit", {**self._bound(), "goal": self.goal.text(), "app_scope": self.scope.text(),
            "procedure": self.procedure.toPlainText().splitlines(),
            "success_checks": self.checks.toPlainText().splitlines(),
            "failure_patterns": self.failures.toPlainText().splitlines()})

    def _restore(self):
        if self._skill and self.versions.currentData():
            self._request("restore", {**self._bound(), "version": self.versions.currentData()})

    def show_result(self, result):
        self._busy = False
        if not result.get("success"):
            # A stale edit must be refreshed, not silently retried or overwritten.
            self.feedback.setText("Commande refusee : " + str(result.get("reason", "erreur"))[:100])
            self.enabled.blockSignals(True)
            self.enabled.setChecked(bool(self._skill and self._skill["active"]))
            self.enabled.blockSignals(False)
            self._controls()
            return
        selected = (result.get("skill") or {}).get("name", self.picker.currentData())
        self.picker.blockSignals(True)
        self.picker.clear()
        self.picker.addItem("Choisir un Skill", "")
        for item in result.get("skills", []):
            suffix = "actif" if item["active"] else "desactive"
            self.picker.addItem(f'{item["name"]} / v{item["version"]} / {suffix}', item["name"])
        index = self.picker.findData(selected)
        self.picker.setCurrentIndex(max(0, index))
        self.picker.blockSignals(False)
        self._skill = result.get("skill")
        self.enabled.blockSignals(True)
        self.enabled.setChecked(bool(self._skill and self._skill["active"]))
        self.enabled.blockSignals(False)
        self.versions.clear()
        if self._skill:
            item = self._skill
            self.goal.setText(item["goal"])
            self.scope.setText(item["app_scope"])
            for control, key in ((self.procedure, "procedure"), (self.checks, "success_checks"),
                                 (self.failures, "failure_patterns")):
                control.setPlainText("\n".join(item[key]))
            self.metadata.setText(f'Version {item["version"]} / Source : {item["source"]}')
            for revision in result.get("history", []):
                self.versions.addItem(f'v{revision["version"]} / {revision["change_kind"]}', revision["version"])
        else:
            for control in (self.goal, self.scope, self.procedure, self.checks, self.failures):
                control.clear()
            self.metadata.clear()
        self.feedback.setText("Connaissances locales actualisees.")
        self._controls()
        if result.get("operation") == "list" and selected and self.picker.currentData():
            self._select()
