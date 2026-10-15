"""Local profile editor. Saving settings neither starts agents nor grants tools."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QGridLayout, QLabel,
    QLineEdit, QPushButton, QScrollArea, QSizePolicy, QSpinBox, QTextEdit,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .agent_profiles import AgentProfileStore, defaults
from .capability_registry import DEFAULT_CAPABILITY_REGISTRY
from .controlled_delegation import _BROWSER, _COMPUTER, base_role_tools


class AgentProfilePanel(QFrame):
    profile_saved = Signal(str, int)

    def __init__(self, parent=None, *, store=None):
        super().__init__(parent)
        self.setObjectName("agentProfilePanel")
        self.store = store or AgentProfileStore()
        self._snapshot = {}
        self._role = ""
        self._revision = 0
        self._drafts = {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        page = QWidget()
        page.setObjectName("agentProfilePage")
        page.setMinimumWidth(0)
        page.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout = QVBoxLayout(page)
        self.notice = QLabel("Profils appliqués aux étapes de délégation supervisée. Chaque étape conserve sa version. "
            "Les demandes ordinaires utilisent le runtime partagé. L’isolation des workers et de leur mémoire reste à développer.")
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        self.roles = QComboBox()
        self.roles.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.roles.setMinimumContentsLength(10)
        for manifest in DEFAULT_CAPABILITY_REGISTRY.agents():
            self.roles.addItem(manifest.display_name, manifest.agent_id)
        layout.addWidget(self.roles)
        self.name = QLineEdit()
        self.name.setMaxLength(100)
        self.purpose = QTextEdit()
        self.purpose.setAcceptRichText(False)
        self.purpose.setFixedHeight(95)
        self.competencies = QTextEdit()
        self.competencies.setAcceptRichText(False)
        self.competencies.setFixedHeight(95)
        for label, field in (("Nom du profil", self.name), ("Responsabilité et limites", self.purpose),
                             ("Compétences — une par ligne", self.competencies)):
            layout.addWidget(QLabel(label))
            layout.addWidget(field)
        self.manifest_info = QLabel()
        self.manifest_info.setTextFormat(Qt.PlainText)
        self.manifest_info.setWordWrap(True)
        layout.addWidget(self.manifest_info)
        budget_grid = QGridLayout()
        self.budgets = {}
        for row, (key, label, low, high) in enumerate((
            ("context_tokens", "Contexte estimé / requête", 1500, 16000),
            ("model_calls", "Appels modèle / étape", 1, 64),
            ("actions", "Actions / étape", 1, 128), ("seconds", "Secondes / étape", 10, 3600))):
            caption = QLabel(label)
            caption.setWordWrap(True)
            spin = QSpinBox()
            spin.setRange(low, high)
            self.budgets[key] = spin
            budget_grid.addWidget(caption, row, 0)
            budget_grid.addWidget(spin, row, 1)
        budget_grid.setColumnStretch(0, 1)
        layout.addLayout(budget_grid)
        self.restrict_tools = QCheckBox("Limiter les outils à cette sélection")
        self.restrict_tools.toggled.connect(lambda checked: self.tools.setEnabled(checked))
        layout.addWidget(self.restrict_tools)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filtrer les outils…")
        self.filter.textChanged.connect(self._filter)
        layout.addWidget(self.filter)
        self.tools = QTreeWidget()
        self.tools.setHeaderLabels(["Outil", "Source"])
        self.tools.setRootIsDecorated(False)
        self.tools.setMinimumHeight(170)
        layout.addWidget(self.tools)
        selection = QGridLayout()
        for column, (caption, checked) in enumerate((("Tout cocher", True), ("Tout décocher", False))):
            button = QPushButton(caption)
            button.clicked.connect(lambda _=False, checked=checked: self._check_all(checked))
            selection.addWidget(button, 0, column)
        layout.addLayout(selection)
        info = QLabel("Une sélection vide interdit les outils de cette délégation. Les outils MCP exigent aussi "
            "une affectation explicite dans le plan et les droits du Centre MCP. Les compétences décrivent le rôle ; "
            "elles n’installent aucune fonction. Les confirmations existantes restent applicables.")
        info.setWordWrap(True)
        layout.addWidget(info)
        buttons = QGridLayout()
        self.save_button = QPushButton("Enregistrer le profil")
        self.reload_button = QPushButton("Recharger")
        self.defaults_button = QPushButton("Valeurs d’origine")
        self.save_button.clicked.connect(self.save_profile)
        self.reload_button.clicked.connect(self.reload_profile)
        self.defaults_button.clicked.connect(lambda: self._fill(defaults(self._role)))
        buttons.addWidget(self.save_button, 0, 0)
        buttons.addWidget(self.reload_button, 0, 1)
        buttons.addWidget(self.defaults_button, 1, 0, 1, 2)
        layout.addLayout(buttons)
        self.status = QLabel()
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(page)
        outer.addWidget(scroll)
        self.roles.currentIndexChanged.connect(self._change_role)
        self.setStyleSheet("""
            QFrame#agentProfilePanel,QWidget#agentProfilePage {background:#061725;color:#cfebf0;border:0;}
            QLabel,QCheckBox {color:#c3e5ed;font-size:11px;}
            QLineEdit,QTextEdit,QComboBox,QSpinBox,QTreeWidget {background:#081f30;color:#d5eaf0;
                border:1px solid #23465a;border-radius:4px;font-size:11px;padding:3px;}
            QHeaderView::section {background:#103246;color:#b7dce5;padding:5px;border:0;}
            QPushButton {background:#10394b;color:#a0e8df;border:1px solid #286278;
                border-radius:4px;padding:6px;font-size:11px;}
            QPushButton:disabled {color:#738b9a;}
            QTreeWidget:disabled {color:#a4bdc9;}
        """)
        self._change_role()

    def _settings(self):
        return {"name": self.name.text(), "purpose": self.purpose.toPlainText(),
            "competencies": [s.strip() for s in self.competencies.toPlainText().splitlines() if s.strip()],
            "tools": ([self.tools.topLevelItem(i).text(0) for i in range(self.tools.topLevelItemCount())
                if self.tools.topLevelItem(i).checkState(0) == Qt.Checked] if self.restrict_tools.isChecked() else None),
            **{key: spin.value() for key, spin in self.budgets.items()}}

    def _change_role(self, *_):
        if self._role:
            self._drafts[self._role] = (self._revision, self._settings())
        self._role = self.roles.currentData()
        if self._role in self._drafts:
            self._revision, draft = self._drafts[self._role]
            self._fill(draft)
        else:
            self.reload_profile()

    def reload_profile(self):
        try:
            row = self.store.get(self._role)
            self._revision = row["revision"]
            self._fill(row["settings"])
            self.save_button.setEnabled(True)
            self.status.setText("Version enregistrée : " + str(self._revision) + " · aucun agent lancé")
        except (OSError, ValueError, TypeError):
            self._fill(defaults(self._role))
            self.save_button.setEnabled(False)
            self.status.setText("Configuration illisible. Corriger le fichier puis recharger ; aucun profil n’a été remplacé.")

    def select_role(self, role):
        index = self.roles.findData(role)
        if index >= 0:
            self.roles.setCurrentIndex(index)

    def apply_snapshot(self, data):
        # Telemetry refresh must never overwrite unsaved operator edits.
        before = self._settings()
        self._snapshot = data
        self._populate_tools(before["tools"])

    def _fill(self, settings):
        self.name.setText(settings["name"])
        self.purpose.setPlainText(settings["purpose"])
        self.competencies.setPlainText("\n".join(settings["competencies"]))
        for key, spin in self.budgets.items():
            spin.setValue(settings[key])
        manifest = DEFAULT_CAPABILITY_REGISTRY.get_agent(self._role)
        self.manifest_info.setText("Scopes mémoire déclarés : " + ", ".join(s.value for s in manifest.memory_scopes)
            + "\nConnecteurs du manifeste : " + (", ".join(manifest.allowed_connectors) or "aucun"))
        self.restrict_tools.setChecked(settings["tools"] is not None)
        self._populate_tools(settings["tools"])

    def _populate_tools(self, selected):
        from .architecture_catalog import _NATIVE, _COMPUTER as computer_sensors
        native = set(_BROWSER) | set(_COMPUTER) | set(computer_sensors)
        for names in _NATIVE.values():
            native.update(names)
        for manifest in DEFAULT_CAPABILITY_REGISTRY.agents():
            native.update(manifest.allowed_tools)
        native.update({"remember_information", "recall_information", "semantic_memory_search", "semantic_memory_inspect"})
        candidates = {name: "natif déclaré" for name in base_role_tools(self._role, frozenset(native))}
        for server in (self._snapshot.get("mcp") or {}).get("servers", []):
            for tool in server.get("tools", []):
                if tool.get("allowed") is True:
                    candidates["mcp__" + server["id"] + "__" + tool["name"]] = "MCP · affectation requise"
        for name in selected or []:
            candidates.setdefault(name, "indisponible dans cet inventaire")
        signature = (tuple(sorted(candidates.items())), tuple(selected) if selected is not None else None)
        if signature == getattr(self, "_tools_signature", None):
            return
        self._tools_signature = signature
        position = self.tools.verticalScrollBar().value()
        self.tools.clear()
        for name, source in sorted(candidates.items()):
            item = QTreeWidgetItem([name, source])
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(0, Qt.Checked if selected is None or name in selected else Qt.Unchecked)
            self.tools.addTopLevelItem(item)
        self.tools.setEnabled(self.restrict_tools.isChecked())
        self.tools.setColumnWidth(0, 190)
        self.tools.verticalScrollBar().setValue(position)
        self._filter(self.filter.text())

    def _filter(self, text):
        for index in range(self.tools.topLevelItemCount()):
            item = self.tools.topLevelItem(index)
            item.setHidden(text.casefold() not in item.text(0).casefold())

    def _check_all(self, checked):
        self.restrict_tools.setChecked(True)
        for index in range(self.tools.topLevelItemCount()):
            self.tools.topLevelItem(index).setCheckState(0, Qt.Checked if checked else Qt.Unchecked)

    def save_profile(self):
        try:
            row = self.store.save(self._role, self._settings(), expected_revision=self._revision)
        except (OSError, ValueError, TypeError) as exc:
            message = ("Profil modifié ailleurs : recharger avant d’enregistrer."
                if str(exc) == "agent_profile_changed_reload_required" else "Profil non enregistré : vérifier les champs et les limites.")
            self.status.setText(message)
            return
        self._revision = row["revision"]
        self._drafts.pop(self._role, None)
        self.status.setText("Profil enregistré · version " + str(self._revision) + ". Appliqué aux prochaines étapes supervisées ; aucun droit ajouté.")
        self.profile_saved.emit(self._role, self._revision)
