"""Operator review and bounded mission controls on the existing worker inbox."""
from __future__ import annotations

import json

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QComboBox, QFormLayout, QHBoxLayout, QLabel,
                              QLineEdit, QPushButton, QSpinBox, QStyle, QTreeWidget,
                              QTreeWidgetItem, QVBoxLayout, QWidget)

from .active_mission_supervisor import OBSERVATION_TOOLS, validate_rule
from .controlled_delegation import responsibility_catalog


class MissionSupervisorPanel(QWidget):
    requested = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._plan = {}
        self._rules = {}
        self._criteria = ()
        self._assignments = {}
        self._steps = ()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        title = QLabel("SUPERVISION ACTIVE")
        title.setObjectName("operatorSection")
        layout.addWidget(title)
        self.state_label = QLabel("PLANNING")
        self.state_label.setWordWrap(True)
        layout.addWidget(self.state_label)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.criterion = QComboBox()
        self.observer = QComboBox()
        self.observer.addItems(sorted(OBSERVATION_TOOLS))
        self.observer.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.observer.setMinimumContentsLength(12)
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
        self.add_rule = QPushButton("Enregistrer")
        self.add_rule.setToolTip("Enregistrer la verification independante")
        self.add_rule.setIcon(self.style().standardIcon(QStyle.SP_DialogApplyButton))
        self.add_rule.clicked.connect(self._add_rule)
        layout.addWidget(self.add_rule)
        responsibility = QFormLayout()
        responsibility.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.step_choice = QComboBox()
        self.agent_choice = QComboBox()
        for choice in (self.criterion, self.step_choice, self.agent_choice):
            choice.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            choice.setMinimumContentsLength(12)
        for item in responsibility_catalog():
            self.agent_choice.addItem(item["name"], item["id"])
        self.tool_scope = QLineEdit()
        self.tool_scope.setMaxLength(6000)
        self.tool_scope.setToolTip('Liste JSON des outils, par exemple ["browser_observe_dom"]. Vide : outils natifs du role.')
        responsibility.addRow("Etape", self.step_choice)
        responsibility.addRow("Responsabilite", self.agent_choice)
        responsibility.addRow("Perimetre d'outils", self.tool_scope)
        layout.addLayout(responsibility)
        self.assign_button = QPushButton("Affecter")
        self.assign_button.setToolTip("Affecter la responsabilite selectionnee")
        self.assign_button.setIcon(self.style().standardIcon(QStyle.SP_DialogApplyButton))
        self.assign_button.clicked.connect(self._assign)
        layout.addWidget(self.assign_button)
        budget = QFormLayout()
        budget.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.actions = QSpinBox()
        self.actions.setRange(1, 512)
        self.actions.setValue(32)
        self.model_calls = QSpinBox()
        self.model_calls.setRange(1, 512)
        self.model_calls.setValue(12)
        self.seconds = QSpinBox()
        self.seconds.setRange(1, 86400)
        self.seconds.setValue(900)
        self.network_calls = QSpinBox()
        self.network_calls.setRange(1, 512)
        self.network_calls.setValue(48)
        self.recoveries = QSpinBox()
        self.recoveries.setRange(1, 512)
        self.recoveries.setValue(2)
        self.total_units = QSpinBox()
        self.total_units.setRange(1, 512)
        self.total_units.setValue(96)
        budget.addRow("Actions maximum", self.actions)
        budget.addRow("Requetes modele maximum", self.model_calls)
        budget.addRow("Duree maximum (s)", self.seconds)
        budget.addRow("Operations reseau maximum", self.network_calls)
        budget.addRow("Reprises maximum", self.recoveries)
        budget.addRow("Unites totales maximum", self.total_units)
        layout.addLayout(budget)
        commands = QHBoxLayout()
        self.approve_button = QPushButton("Valider")
        self.approve_button.setToolTip("Valider le plan, ses limites et ses verifications")
        self.approve_button.setIcon(self.style().standardIcon(QStyle.SP_DialogApplyButton))
        self.advance_button = QPushButton("Avancer")
        self.advance_button.setToolTip("Executer une etape bornee")
        self.advance_button.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        commands.addWidget(self.approve_button)
        commands.addWidget(self.advance_button)
        layout.addLayout(commands)
        coordination = QHBoxLayout()
        self.step_limit = QSpinBox()
        self.step_limit.setRange(1, 24)
        self.step_limit.setValue(24)
        self.step_limit.setToolTip("Nombre maximum d'etapes pour cette execution")
        self.run_button = QPushButton("Poursuivre")
        self.run_button.setIcon(self.style().standardIcon(QStyle.SP_MediaSkipForward))
        self.stop_button = QPushButton()
        self.stop_button.setIcon(self.style().standardIcon(QStyle.SP_MediaStop))
        self.stop_button.setToolTip("Arreter avant la prochaine action")
        self.stop_button.setAccessibleName("Arreter la coordination")
        self.stop_button.setFixedSize(32, 32)
        self.run_button.clicked.connect(lambda: self.requested.emit("run_supervision", str(self.step_limit.value())))
        self.stop_button.clicked.connect(lambda: self.requested.emit("cancel_supervision", ""))
        coordination.addWidget(self.step_limit)
        coordination.addWidget(self.run_button)
        coordination.addWidget(self.stop_button)
        layout.addLayout(coordination)
        self.continuation = QLineEdit()
        self.continuation.setMaxLength(2000)
        self.continuation.setPlaceholderText("Reponse a une confirmation en attente")
        layout.addWidget(self.continuation)
        self.feedback = QLabel("")
        self.feedback.setWordWrap(True)
        layout.addWidget(self.feedback)
        self.reports = QTreeWidget()
        self.reports.setHeaderLabels(["Etape", "Responsable", "Etat", "Actions"])
        self.reports.setRootIsDecorated(False)
        self.reports.setMinimumHeight(100)
        self.reports.setMaximumHeight(190)
        layout.addWidget(self.reports)
        self.approve_button.clicked.connect(self._approve)
        self.advance_button.clicked.connect(self._advance)
        self.approve_button.setEnabled(False)
        self.advance_button.setEnabled(False)
        self.run_button.setEnabled(False)
        self.stop_button.setEnabled(False)

    def _assign(self):
        step_id = self.step_choice.currentData()
        if not step_id:
            return
        entry = {"agent": self.agent_choice.currentData()}
        raw = self.tool_scope.text().strip()
        if raw:
            try:
                names = json.loads(raw)
                if not isinstance(names, list) or any(not isinstance(t, str) for t in names):
                    raise ValueError("invalid_tool_scope")
                entry["tools"] = names
            except (ValueError, TypeError):
                self.feedback.setText("Perimetre invalide : une liste JSON est requise.")
                return
        self._assignments[step_id] = entry
        self.feedback.setText(f"{len(self._assignments)} responsabilite(s) preparee(s).")

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
                  "rules": self._rules, "assignments": self._assignments,
                  "limits": {"actions": self.actions.value(), "model_calls": self.model_calls.value(),
                  "seconds": self.seconds.value(), "network_calls": self.network_calls.value(),
                  "recoveries": self.recoveries.value(), "total_units": self.total_units.value()}}
        self.requested.emit("approve_supervision", json.dumps(packet, ensure_ascii=False))

    def _advance(self):
        self.requested.emit("advance_supervision", self.continuation.text().strip())
        self.continuation.clear()

    def apply_snapshot(self, data: dict):
        plan = data.get("supervised_plan") or {}
        active = data.get("active_supervisor") or {}
        enabled = data.get("active_supervisor_enabled") is True
        criteria = tuple(plan.get("criteria") or ())
        if (plan.get("mission_id"), plan.get("digest")) != (self._plan.get("mission_id"), self._plan.get("digest")):
            self._rules = {}
            self._assignments = {}
        self._plan = dict(plan)
        if criteria != self._criteria:
            self.criterion.clear()
            for key in criteria:
                self.criterion.addItem(str(key), str(key))
            self._criteria = criteria
        steps = tuple((s["id"], s.get("intent", "")) for s in plan.get("steps", []))
        if steps != self._steps:
            self.step_choice.clear()
            for step_id, intent in steps:
                self.step_choice.addItem(str(step_id), step_id)
                self.step_choice.setItemData(self.step_choice.count() - 1, str(intent), 3)
            self._steps = steps
        status = str(active.get("state") or "PLANNING")
        usage = active.get("usage") or {}
        self.state_label.setText(
            f'{status} | {active.get("step_id", "")} | {active.get("tool", "")}\n'
            f'Actions : {usage.get("actions", 0)} | Modele : {usage.get("model_calls", 0)}'
            + ("\n" + str(active["reason"]) if active.get("reason") else "")
        )
        self.approve_button.setEnabled(enabled and bool(plan) and not active)
        self.add_rule.setEnabled(enabled and bool(plan) and not active)
        self.assign_button.setEnabled(enabled and bool(plan) and not active)
        self.advance_button.setEnabled(enabled and status in {"READY", "WAITING_APPROVAL", "RECOVERING"})
        self.run_button.setEnabled(enabled and status == "READY")
        self.stop_button.setEnabled(enabled and status in {"ACTING", "OBSERVING", "VERIFYING", "READY"})
        self.continuation.setEnabled(enabled and status == "WAITING_APPROVAL")
        self.reports.clear()
        for report in active.get("reports", []):
            label = "Verifie" if report.get("verified") else "Confirmation" if report.get("approval_pending") else "Preuve attendue"
            QTreeWidgetItem(self.reports, [str(report.get("step_id", "")), str(report.get("agent", "")),
                                         label, str(report.get("action_count", 0))])

    def apply_result(self, result: dict):
        if result.get("operation") not in {"approve_supervision", "advance_supervision", "recover_supervision",
                                           "run_supervision", "delegation_progress"}:
            return
        self.feedback.setText(str(result.get("status") or result.get("reason") or ""))
