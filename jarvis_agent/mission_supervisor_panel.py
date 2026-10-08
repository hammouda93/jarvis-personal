"""Operator review and bounded mission controls on the existing worker inbox."""
from __future__ import annotations

import json

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QComboBox, QFormLayout, QHBoxLayout, QLabel,
                              QLineEdit, QPushButton, QSpinBox, QStyle, QVBoxLayout, QWidget)

from .active_mission_supervisor import OBSERVATION_TOOLS, validate_rule


class MissionSupervisorPanel(QWidget):
    requested = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._plan = {}
        self._rules = {}
        self._criteria = ()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        title = QLabel("SUPERVISION ACTIVE")
        title.setObjectName("operatorSection")
        layout.addWidget(title)
        self.state_label = QLabel("PLANNING")
        self.state_label.setWordWrap(True)
        layout.addWidget(self.state_label)
        form = QFormLayout()
        self.criterion = QComboBox()
        self.observer = QComboBox()
        self.observer.addItems(sorted(OBSERVATION_TOOLS))
        self.arguments = QLineEdit("{}")
        self.arguments.setMaxLength(4000)
        self.path = QLineEdit('["snapshot", "url"]')
        self.path.setMaxLength(1500)
        self.expected = QLineEdit()
        self.expected.setMaxLength(2000)
        self.arguments.setToolTip('Objet JSON, par exemple {"tab_id": 17}')
        self.path.setToolTip('Chemin JSON dans une observation independante, par exemple ["snapshot", "url"]')
        self.expected.setToolTip('Valeur JSON exacte, par exemple "https://example.org/"')
        form.addRow("Critere", self.criterion)
        form.addRow("Observation", self.observer)
        form.addRow("Arguments", self.arguments)
        form.addRow("Champ observe", self.path)
        form.addRow("Valeur attendue", self.expected)
        layout.addLayout(form)
        self.add_rule = QPushButton("Enregistrer la verification")
        self.add_rule.setIcon(self.style().standardIcon(QStyle.SP_DialogApplyButton))
        self.add_rule.clicked.connect(self._add_rule)
        layout.addWidget(self.add_rule)
        budget = QFormLayout()
        self.actions = QSpinBox()
        self.actions.setRange(1, 512)
        self.actions.setValue(32)
        self.model_calls = QSpinBox()
        self.model_calls.setRange(1, 512)
        self.model_calls.setValue(12)
        self.seconds = QSpinBox()
        self.seconds.setRange(1, 86400)
        self.seconds.setValue(900)
        budget.addRow("Actions maximum", self.actions)
        budget.addRow("Requetes modele maximum", self.model_calls)
        budget.addRow("Duree maximum (s)", self.seconds)
        layout.addLayout(budget)
        commands = QHBoxLayout()
        self.approve_button = QPushButton("Valider le plan")
        self.approve_button.setIcon(self.style().standardIcon(QStyle.SP_DialogApplyButton))
        self.advance_button = QPushButton("Etape suivante")
        self.advance_button.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        commands.addWidget(self.approve_button)
        commands.addWidget(self.advance_button)
        layout.addLayout(commands)
        self.continuation = QLineEdit()
        self.continuation.setMaxLength(2000)
        self.continuation.setPlaceholderText("Reponse a une confirmation en attente")
        layout.addWidget(self.continuation)
        self.feedback = QLabel("")
        self.feedback.setWordWrap(True)
        layout.addWidget(self.feedback)
        self.approve_button.clicked.connect(self._approve)
        self.advance_button.clicked.connect(self._advance)
        self.approve_button.setEnabled(False)
        self.advance_button.setEnabled(False)

    def _add_rule(self):
        requirement = str(self.criterion.currentData() or "")
        if not requirement:
            return
        try:
            rule = {"tool": self.observer.currentText(),
                    "arguments": json.loads(self.arguments.text()),
                    "path": json.loads(self.path.text()), "equals": json.loads(self.expected.text())}
            validate_rule(rule)
        except (ValueError, TypeError):
            self.feedback.setText("Verification invalide : examiner les champs JSON.")
            return
        self._rules[requirement] = rule
        self.feedback.setText(f"{len(self._rules)} verification(s) preparee(s).")

    def _approve(self):
        if not self._plan:
            return
        packet = {"mission_id": self._plan["mission_id"], "digest": self._plan["digest"],
                  "rules": self._rules, "limits": {"actions": self.actions.value(),
                  "model_calls": self.model_calls.value(), "seconds": self.seconds.value()}}
        self.requested.emit("approve_supervision", json.dumps(packet, ensure_ascii=False))

    def _advance(self):
        self.requested.emit("advance_supervision", self.continuation.text().strip())
        self.continuation.clear()

    def apply_snapshot(self, data: dict):
        plan = data.get("supervised_plan") or {}
        active = data.get("active_supervisor") or {}
        enabled = data.get("active_supervisor_enabled") is True
        criteria = tuple(plan.get("criteria") or ())
        if plan.get("digest") != self._plan.get("digest"):
            self._rules = {}
        self._plan = dict(plan)
        if criteria != self._criteria:
            self.criterion.clear()
            for key in criteria:
                self.criterion.addItem(str(key), str(key))
            self._criteria = criteria
        status = str(active.get("state") or "PLANNING")
        usage = active.get("usage") or {}
        self.state_label.setText(
            f'{status} | {active.get("step_id", "")} | {active.get("tool", "")}\n'
            f'Actions : {usage.get("actions", 0)} | Modele : {usage.get("model_calls", 0)}'
            + ("\n" + str(active["reason"]) if active.get("reason") else "")
        )
        self.approve_button.setEnabled(enabled and bool(plan) and not active)
        self.add_rule.setEnabled(enabled and bool(plan) and not active)
        self.advance_button.setEnabled(enabled and status in {"READY", "WAITING_APPROVAL", "RECOVERING"})
        self.continuation.setEnabled(enabled and status == "WAITING_APPROVAL")

    def apply_result(self, result: dict):
        if result.get("operation") not in {"approve_supervision", "advance_supervision", "recover_supervision"}:
            return
        self.feedback.setText(str(result.get("status") or result.get("reason") or ""))
