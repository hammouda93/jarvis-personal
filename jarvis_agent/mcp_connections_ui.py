"""Separate per-MCP service management inside the existing PySide6 Jarvis UI.

No automatic network activity, install steps, credential display, or tool
execution while opening this pane. Connections are explicit and opt-in.
"""
from __future__ import annotations

import re

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QLineEdit, QTextBrowser,
)

from .mcp_hub import HUB, MCPHub


class MCPConnectionsPanel(QFrame):
    """Configuration UI: every service/server is handled independently."""

    request_probe = Signal(str, str)

    def __init__(self, parent=None, *, hub: MCPHub | None = None):
        super().__init__(parent)
        self.hub = hub if hub is not None else HUB
        self.setObjectName("mcpConnectionsPanel")
        self._entries: list[dict] = []
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(14, 14, 14, 14)

        title = QLabel("◈   MCP / CONNECTEURS")
        title.setObjectName("mcpTitle")
        subtitle = QLabel(
            "Chaque service est indépendant. Ajouter ≠ connecter. "
            "Autorisations séparées de ChatGPT."
        )
        subtitle.setWordWrap(True)
        subtitle.setObjectName("mcpHint")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        presets = QLabel("Services suggérés (non activés automatiquement)")
        layout.addWidget(presets)
        self.presets = QComboBox()
        for preset in self.hub.catalog():
            self.presets.addItem(
                preset["name"] + " — " + preset["category"],
                preset["id"],
            )
        self.presets.currentIndexChanged.connect(self._show_preset)
        layout.addWidget(self.presets)
        self.preset_detail = QLabel()
        self.preset_detail.setWordWrap(True)
        layout.addWidget(self.preset_detail)

        self.server_id = QLineEdit()
        self.server_id.setPlaceholderText("ID unique, ex. google_sheets")
        self.server_id.setMaxLength(64)
        self.server_label = QLineEdit()
        self.server_label.setPlaceholderText("Identifiant MCP du serveur")
        self.server_label.setMaxLength(64)
        self.server_url = QLineEdit()
        self.server_url.setPlaceholderText("https://fournisseur.example/mcp")
        self.server_url.setMaxLength(1200)
        self.allowed_tools = QLineEdit()
        self.allowed_tools.setPlaceholderText(
            "Noms d'outils autorisés, séparés par virgules"
        )
        self.allowed_tools.setMaxLength(2000)
        self.auth_env = QLineEdit()
        self.auth_env.setPlaceholderText(
            "Nom de variable d'environnement (facultatif) : MCP_TOKEN"
        )
        self.auth_env.setMaxLength(120)
        for field in (
            self.server_id, self.server_label, self.server_url,
            self.allowed_tools, self.auth_env,
        ):
            layout.addWidget(field)

        self.add_button = QPushButton("＋ Ajouter (désactivé)")
        self.add_button.clicked.connect(self._add)
        layout.addWidget(self.add_button)

        self.servers = QComboBox()
        self.servers.currentIndexChanged.connect(self._show_server)
        layout.addWidget(self.servers)
        commands = QHBoxLayout()
        self.enable_button = QPushButton("Activer")
        self.disable_button = QPushButton("Désactiver")
        self.probe_button = QPushButton("Tester la connexion")
        for button in (self.enable_button, self.disable_button, self.probe_button):
            commands.addWidget(button)
        self.enable_button.clicked.connect(lambda: self._toggle(True))
        self.disable_button.clicked.connect(lambda: self._toggle(False))
        self.probe_button.clicked.connect(self._probe)
        layout.addLayout(commands)

        hermes = QLabel(
            "Hermes (optionnel) : pont MCP de messagerie uniquement, "
            "pas un relais automatique de Google Sheets / Maps."
        )
        hermes.setWordWrap(True)
        layout.addWidget(hermes)
        self.hermes_binary = QLineEdit("hermes")
        self.hermes_binary.setPlaceholderText(
            "Chemin de hermes.exe si Hermes est déjà installé"
        )
        layout.addWidget(self.hermes_binary)
        self.hermes_probe = QPushButton("◇ Tester Hermes MCP (sans envoyer)")
        self.hermes_probe.clicked.connect(
            lambda: self.request_probe.emit(
                "hermes", self.hermes_binary.text().strip()
            )
        )
        layout.addWidget(self.hermes_probe)

        self.status = QLabel("En attente de configuration")
        self.status.setObjectName("mcpHint")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.details = QTextBrowser()
        self.details.setOpenExternalLinks(False)
        self.details.setMinimumHeight(90)
        layout.addWidget(self.details, 1)

        self.setStyleSheet("""
            QFrame#mcpConnectionsPanel {background:#031625;color:#cde6ed;
                border:1px solid #194b62;border-radius:12px;}
            QLabel#mcpTitle {color:#9cf4ec;font-size:13px;font-weight:700;}
            QLabel#mcpHint {color:#86aabc;font-size:10px;}
            QLineEdit, QComboBox {background:#08283c;color:#e4f9ff;
                border:1px solid #2d6177;border-radius:6px;
                padding:5px;font-size:10px;}
            QPushButton {background:#113c50;color:#a9ede8;
                border:1px solid #37738b;border-radius:5px;
                padding:7px;font-size:10px;}
            QPushButton:disabled {color:#6f91a0;}
            QTextBrowser {background:#041a2a;color:#bde6ea;
                border:1px solid #21465a;border-radius:8px;}
        """)
        self._show_preset()
        self.refresh()

    def _show_preset(self, *args):
        entry = next(
            (item for item in self.hub.catalog()
             if item["id"] == self.presets.currentData()), None
        )
        if entry:
            self.preset_detail.setText(
                entry["details"] + " · Requis : " + entry["prerequisites"]
            )
            if not self.server_id.text():
                self.server_id.setText(entry["id"])
            if not self.server_label.text():
                self.server_label.setText(entry["id"])

    def refresh(self):
        try:
            rows = self.hub.list_servers()
        except (OSError, ValueError, TypeError):
            rows = []
        self._entries = rows
        previous = self.servers.currentData()
        self.servers.blockSignals(True)
        self.servers.clear()
        self.servers.addItem("Sélectionner un serveur configuré…", "")
        for item in rows:
            self.servers.addItem(
                item["id"] + " · " + item["status"], item["id"]
            )
        selected = self.servers.findData(previous)
        if selected >= 0:
            self.servers.setCurrentIndex(selected)
        self.servers.blockSignals(False)
        self._show_server()

    def _show_server(self, *args):
        item = next(
            (x for x in self._entries if x["id"] == self.servers.currentData()),
            None
        )
        if not item:
            self.details.setPlainText(
                "Aucun serveur sélectionné. Aucun MCP actif par défaut."
            )
        else:
            self.details.setPlainText(
                f"Serveur : {item['id']}\n"
                f"État : {item['status']}\n"
                f"Authentification : {'prête' if item['auth_ready'] else 'manquante'} "
                f"(variable {item['auth_env'] or 'aucune'})\n"
                f"Outils autorisés : {', '.join(item['tool_allowlist'])}\n"
                "Connecté : NON — aucune vérification réseau automatique."
            )
        active = bool(item and item["eligible"])
        self.enable_button.setEnabled(active and not item["enabled"])
        self.disable_button.setEnabled(active and item["enabled"])
        self.probe_button.setEnabled(active and item["enabled"] and item["auth_ready"])

    def _add(self):
        try:
            tools = tuple(x.strip() for x in self.allowed_tools.text().split(","))
            self.hub.add_remote(
                server_id=self.server_id.text().strip(),
                label=self.server_label.text().strip(),
                endpoint=self.server_url.text().strip(),
                allowed_tools=tools,
                authorization_env=self.auth_env.text().strip(),
            )
        except (ValueError, PermissionError, OSError, KeyError) as exc:
            self.status.setText("Configuration refusée : " + type(exc).__name__)
            return
        self.status.setText("Serveur ajouté (désactivé). Aucun jeton enregistré.")
        self.refresh()

    def _toggle(self, enabled: bool):
        server_id = str(self.servers.currentData() or "")
        try:
            self.hub.set_enabled(server_id, enabled=enabled)
        except (ValueError, KeyError, PermissionError, OSError) as exc:
            self.status.setText("Activation refusée : " + type(exc).__name__)
            return
        self.status.setText(
            "Configuration activée ; connexion NON testée."
            if enabled else "Serveur désactivé."
        )
        self.refresh()

    def _probe(self):
        server_id = str(self.servers.currentData() or "")
        if server_id:
            self.request_probe.emit("remote", server_id)
            self.status.setText("Demande de test réseau explicite envoyée.")

    def show_probe_result(self, result: dict):
        if result.get("status") == "online_probed":
            listed = ", ".join(
                str(x.get("name") or "")[:80]
                for x in result.get("tools", [])[:20]
            )
            self.status.setText(
                "Connexion vérifiée ; aucun outil exécuté. "
                + f"Outils exposés : {result.get('discovered_count', 0)}"
            )
            self.details.setPlainText(
                "Outils autorisés détectés : " + (listed or "aucun")
            )
        else:
            self.status.setText(
                "Test indisponible : " + str(result.get("reason") or "échec")[:75]
            )
