"""Operator MCP configuration surface, zero network work on Qt GUI thread."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
)


class MCPConnectionsPanel(QFrame):
    requested = Signal(str, str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("mcpConnectionsPanel")
        self._servers = ()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(9, 8, 9, 8)
        layout.setSpacing(5)
        title = QLabel("⬡   MCP — SERVEURS INDÉPENDANTS")
        title.setObjectName("operatorSection")
        layout.addWidget(title)
        self.summary = QLabel("MCP facultatif · outils interdits par défaut.")
        self.summary.setObjectName("operatorMetric")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        self.server_name = QLineEdit()
        self.server_name.setObjectName("missionId")
        self.server_name.setPlaceholderText("Identifiant : github, calendar, research…")
        self.server_name.setMaxLength(36)
        self.server_url = QLineEdit()
        self.server_url.setObjectName("missionId")
        self.server_url.setPlaceholderText("URL du serveur MCP (HTTPS)")
        self.server_url.setMaxLength(600)
        layout.addWidget(self.server_name)
        layout.addWidget(self.server_url)
        self.add_button = QPushButton("＋ Ajouter serveur distant (désactivé)")
        self.hermes_button = QPushButton("＋ Hermes local, optionnel (sans installer)")
        for button in (self.add_button, self.hermes_button):
            button.setObjectName("missionButton")
            layout.addWidget(button)
        self.server_picker = QComboBox()
        self.server_picker.setObjectName("missionPicker")
        self.server_picker.addItem("Sélectionner serveur MCP…", "")
        layout.addWidget(self.server_picker)
        self.tool_picker = QComboBox()
        self.tool_picker.setObjectName("missionPicker")
        self.tool_picker.addItem("Sélectionner outil découvert…", "")
        layout.addWidget(self.tool_picker)
        top = QHBoxLayout()
        self.enable_button = QPushButton("Activer")
        self.disable_button = QPushButton("Désactiver")
        self.discover_button = QPushButton("Détecter les outils")
        for button in (self.enable_button, self.disable_button, self.discover_button):
            button.setObjectName("missionButton")
            top.addWidget(button)
        layout.addLayout(top)
        bottom = QHBoxLayout()
        self.allow_button = QPushButton("Autoriser outil")
        self.deny_button = QPushButton("Bloquer outil")
        for button in (self.allow_button, self.deny_button):
            button.setObjectName("missionButton")
            bottom.addWidget(button)
        layout.addLayout(bottom)
        self.feedback = QLabel(
            "Chaque serveur est isolé. Chaque outil doit être autorisé à part. "
            "Même autorisé, tout appel demande confirmation."
        )
        self.feedback.setWordWrap(True)
        self.feedback.setObjectName("operatorMetric")
        layout.addWidget(self.feedback)

        self.add_button.clicked.connect(
            lambda: self._send("add_http", self.server_name.text(), self.server_url.text())
        )
        self.hermes_button.clicked.connect(
            lambda: self._send("add_hermes", "hermes", "")
        )
        self.server_picker.currentIndexChanged.connect(self._select_server)
        self.enable_button.clicked.connect(lambda: self._for_server("enable"))
        self.disable_button.clicked.connect(lambda: self._for_server("disable"))
        self.discover_button.clicked.connect(lambda: self._for_server("discover"))
        self.allow_button.clicked.connect(lambda: self._for_tool("allow_tool"))
        self.deny_button.clicked.connect(lambda: self._for_tool("deny_tool"))

    def _send(self, operation: str, server: str, value: str) -> None:
        if not server or (operation == "add_http" and not value):
            self.feedback.setText("Un identifiant et une adresse valide sont nécessaires.")
            return
        self.feedback.setText("Demande transmise au worker Jarvis.")
        self.requested.emit(operation, server.strip(), value.strip())

    def _for_server(self, operation: str) -> None:
        self._send(operation, str(self.server_picker.currentData() or ""), "")

    def _for_tool(self, operation: str) -> None:
        self._send(
            operation, str(self.server_picker.currentData() or ""),
            str(self.tool_picker.currentData() or "")
        )

    def _select_server(self, index: int) -> None:
        server_id = str(self.server_picker.currentData() or "")
        self.tool_picker.blockSignals(True)
        self.tool_picker.clear()
        self.tool_picker.addItem("Sélectionner outil découvert…", "")
        for entry in self._servers:
            if entry["id"] != server_id:
                continue
            for item in entry.get("tools") or []:
                flag = "✓" if item.get("allowed") else "○"
                self.tool_picker.addItem(
                    f'{flag} {str(item.get("name", ""))[:70]}',
                    item.get("name", "")
                )
        self.tool_picker.blockSignals(False)

    def apply_snapshot(self, data: dict) -> None:
        enabled = data.get("enabled") is True
        servers = tuple(data.get("servers") or [])
        count = sum(row.get("allowed", 0) for row in servers)
        self.summary.setText(
            f"Runtime MCP : {'ON' if enabled else 'OFF — activer dans les paramètres'}"
            f" · serveurs {len(servers)} · outils autorisés {count}"
        )
        self._servers = servers
        old_id = str(self.server_picker.currentData() or "")
        old_tool = str(self.tool_picker.currentData() or "")
        choices = [(row["id"], row.get("enabled"), row.get("discovered")) for row in servers]
        current_choices = [
            (self.server_picker.itemData(i),
             self.server_picker.itemText(i).startswith("●"),
             None) for i in range(1, self.server_picker.count())
        ]
        if [(x[0], x[1]) for x in current_choices] != [
            (a, b) for a, b, _ in choices
        ]:
            self.server_picker.blockSignals(True)
            self.server_picker.clear()
            self.server_picker.addItem("Sélectionner serveur MCP…", "")
            for entry in servers:
                self.server_picker.addItem(
                    ("● " if entry.get("enabled") else "○ ") + entry["id"],
                    entry["id"],
                )
            idx = self.server_picker.findData(old_id)
            if idx >= 0:
                self.server_picker.setCurrentIndex(idx)
            self.server_picker.blockSignals(False)
        self._select_server(self.server_picker.currentIndex())
        idx = self.tool_picker.findData(old_tool)
        if idx >= 0:
            self.tool_picker.setCurrentIndex(idx)

    def show_result(self, result: dict) -> None:
        if result.get("success"):
            self.feedback.setText(
                str(result.get("message") or "Configuration MCP modifiée.")[:160]
            )
        else:
            self.feedback.setText(
                "MCP refusé : " + str(result.get("reason") or "échec")[:100]
            )
