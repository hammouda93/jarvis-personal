"""Operator MCP configuration surface, zero network work on Qt GUI thread."""
from __future__ import annotations

import json
from PySide6.QtCore import Qt, Signal
from .mcp_service_catalog import SERVICE_CARDS, find_card
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
    QPushButton, QSpinBox, QStyle, QTabWidget, QVBoxLayout,
)


class MCPConnectionsPanel(QFrame):
    requested = Signal(str, str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("mcpConnectionsPanel")
        self._servers = ()
        self._authorization_pending = ""
        self._selected_server = ""
        self._quota_dirty = False
        self._runtime_pending = False
        self._runtime_enabled = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(9, 8, 9, 8)
        layout.setSpacing(5)
        root = layout
        title = QLabel("MCP CONTROL CENTER")
        title.setObjectName("operatorSection")
        layout.addWidget(title)
        self.summary = QLabel("MCP facultatif · outils interdits par défaut.")
        self.summary.setObjectName("operatorMetric")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.runtime_switch = QCheckBox("MCP actif")
        self.runtime_switch.setToolTip("Exposer au runtime uniquement les outils MCP explicitement autorises")
        self.runtime_switch.toggled.connect(self._set_runtime)
        root.addWidget(self.runtime_switch)
        self.tabs = QTabWidget()
        self.services_page, self.catalog_page, self.add_page = QFrame(), QFrame(), QFrame()
        for page, label in ((self.services_page, "Services"), (self.catalog_page, "Catalogue"), (self.add_page, "Ajouter")):
            self.tabs.addTab(page, label)
        root.addWidget(self.tabs)
        services_layout = QVBoxLayout(self.services_page)
        layout = QVBoxLayout(self.catalog_page)
        self.catalog_picker = QComboBox()
        self.catalog_picker.setObjectName("missionPicker")
        self.catalog_picker.addItem("Services documentes...", "")
        for card in SERVICE_CARDS:
            self.catalog_picker.addItem(
                card.label + " · " + card.category, card.identifier
            )
        layout.addWidget(self.catalog_picker)
        self.catalog_note = QLabel(
            "Catalogue verifie le 09/10/2026 ; comptes non testes. "
            "WhatsApp personnel : aucun serveur officiel reference."
        )
        self.catalog_note.setWordWrap(True)
        self.catalog_note.setTextFormat(Qt.PlainText)
        self.catalog_note.setObjectName("operatorMetric")
        layout.addWidget(self.catalog_note)
        self.catalog_picker.currentIndexChanged.connect(
            self._select_catalog_service
        )
        self.catalog_endpoint = QLabel()
        self.catalog_endpoint.setTextFormat(Qt.PlainText)
        self.catalog_endpoint.setWordWrap(True)
        self.catalog_endpoint.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.catalog_endpoint)
        self.catalog_prepare_button = QPushButton("Configurer ce service")
        self.catalog_prepare_button.setIcon(self.style().standardIcon(QStyle.SP_FileDialogNewFolder))
        self.catalog_prepare_button.clicked.connect(self._prepare_catalog_service)
        layout.addWidget(self.catalog_prepare_button)
        layout.addStretch(1)
        layout = QVBoxLayout(self.add_page)
        self.server_name = QLineEdit()
        self.server_name.setObjectName("missionId")
        self.server_name.setPlaceholderText("Identifiant : github, calendar, research…")
        self.server_name.setMaxLength(36)
        self.server_url = QLineEdit()
        self.server_url.setObjectName("missionId")
        self.server_url.setPlaceholderText("URL du serveur MCP (HTTPS)")
        self.server_url.setMaxLength(600)
        layout.addWidget(self.server_name)
        self.transport_picker = QComboBox()
        self.transport_picker.addItem("HTTP", "http")
        self.transport_picker.addItem("stdio local", "stdio")
        layout.addWidget(self.transport_picker)
        layout.addWidget(self.server_url)
        self.stdio_command = QLineEdit()
        self.stdio_command.setMaxLength(800)
        self.stdio_command.setPlaceholderText("Chemin de l'executable local")
        self.stdio_arguments = QLineEdit("[]")
        self.stdio_arguments.setMaxLength(8000)
        self.stdio_arguments.setToolTip('Arguments JSON ; scripts et fichiers avec chemins absolus.')
        self.stdio_environment = QLineEdit("{}")
        self.stdio_environment.setMaxLength(3000)
        self.stdio_environment.setToolTip('References uniquement : {"SERVICE_TOKEN": "JARVIS_MCP_SERVICE_TOKEN"}')
        self.stdio_warning = QLabel("Ce programme local aura les droits de votre compte Windows/Linux.")
        self.stdio_warning.setWordWrap(True)
        self.stdio_trust = QCheckBox("Confiance locale")
        self.stdio_trust.setToolTip("Programme local de confiance : il s'execute avec les droits de votre compte.")
        for widget in (self.stdio_command, self.stdio_arguments, self.stdio_environment, self.stdio_warning, self.stdio_trust):
            layout.addWidget(widget)
        self.transport_picker.currentIndexChanged.connect(self._select_transport)
        self._select_transport()
        self.add_button = QPushButton("Ajouter distant")
        self.add_button.setToolTip("Ajouter un serveur distant desactive; aucun outil n'est autorise automatiquement.")
        self.hermes_button = QPushButton("Hermes local")
        self.hermes_button.setToolTip("Enregistrer Hermes local, sans installer ni demarrer de programme.")
        for button in (self.add_button, self.hermes_button):
            button.setIcon(self.style().standardIcon(QStyle.SP_FileDialogNewFolder))
            button.setObjectName("missionButton")
            layout.addWidget(button)
        layout.addStretch(1)
        layout = services_layout
        self.server_picker = QComboBox()
        self.server_picker.setObjectName("missionPicker")
        self.server_picker.addItem("Sélectionner serveur MCP…", "")
        layout.addWidget(self.server_picker)
        self.connection_status = QLabel("Aucun serveur sélectionné.")
        self.connection_status.setObjectName("operatorMetric")
        self.connection_status.setWordWrap(True)
        self.connection_status.setTextFormat(Qt.PlainText)
        layout.addWidget(self.connection_status)
        self.endpoint_label = QLabel()
        self.endpoint_label.setTextFormat(Qt.PlainText)
        self.endpoint_label.setWordWrap(True)
        layout.addWidget(self.endpoint_label)
        top = QHBoxLayout()
        self.enable_button = QPushButton("Activer")
        self.disable_button = QPushButton("Desactiver")
        self.connect_button = QPushButton("Connecter")
        self.connect_button.setIcon(self.style().standardIcon(QStyle.SP_DialogApplyButton))
        self.disconnect_button = QPushButton()
        self.disconnect_button.setIcon(self.style().standardIcon(QStyle.SP_DialogCloseButton))
        self.disconnect_button.setToolTip("Deconnecter et revoquer les outils ; conserver les credentials")
        self.disconnect_button.setAccessibleName("Deconnecter le serveur MCP")
        self.discover_button = QPushButton()
        self.discover_button.setIcon(self.style().standardIcon(QStyle.SP_BrowserReload))
        self.discover_button.setToolTip("Reconnecter et decouvrir les capacites ; une seule tentative")
        self.discover_button.setAccessibleName("Reconnecter le serveur MCP")
        for button in (self.connect_button, self.disconnect_button, self.discover_button):
            top.addWidget(button)
        layout.addLayout(top)
        switches = QHBoxLayout()
        for button in (self.enable_button, self.disable_button):
            switches.addWidget(button)
        layout.addLayout(switches)
        self.service_tabs = QTabWidget()
        tools_page, credentials_page, activity_page = QFrame(), QFrame(), QFrame()
        for page, label in ((tools_page, "Capacites"), (credentials_page, "Acces"), (activity_page, "Journal")):
            self.service_tabs.addTab(page, label)
        services_layout.addWidget(self.service_tabs)
        tools_layout = QVBoxLayout(tools_page)
        activity_layout = QVBoxLayout(activity_page)
        layout = QVBoxLayout(credentials_page)
        self.credential_kind = QComboBox()
        for label, value in (("Bearer / PAT", "bearer"), ("Cle API : X-API-Key", "X-API-Key"), ("Cle API : X-Goog-Api-Key", "X-Goog-Api-Key")):
            self.credential_kind.addItem(label, value)
        layout.addWidget(self.credential_kind)
        self.bearer = QLineEdit()
        self.bearer.setEchoMode(QLineEdit.Password)
        self.bearer.setMaxLength(12000)
        self.bearer.setPlaceholderText("Credential HTTP")
        layout.addWidget(self.bearer)
        credentials = QHBoxLayout()
        self.store_credential_button = QPushButton("Coffre")
        self.store_credential_button.setToolTip("Enregistrer le bearer dans le coffre du systeme")
        self.store_credential_button.setIcon(self.style().standardIcon(QStyle.SP_DialogSaveButton))
        self.forget_credential_button = QPushButton()
        self.forget_credential_button.setToolTip("Retirer les credentials locaux et revoquer les outils")
        self.forget_credential_button.setAccessibleName("Retirer les credentials MCP")
        self.forget_credential_button.setIcon(self.style().standardIcon(QStyle.SP_DialogCancelButton))
        credentials.addWidget(self.store_credential_button)
        credentials.addWidget(self.forget_credential_button)
        layout.addLayout(credentials)
        self.oauth_scope = QLineEdit()
        self.oauth_scope.setMaxLength(600)
        self.oauth_scope.setPlaceholderText("Scopes OAuth explicites (facultatifs)")
        layout.addWidget(self.oauth_scope)
        self.oauth_registered = QCheckBox("Client preenregistre")
        self.oauth_registered.setToolTip("Utiliser un client OAuth preenregistre au lieu de l'enregistrement dynamique.")
        layout.addWidget(self.oauth_registered)
        self.oauth_client_id = QLineEdit()
        self.oauth_client_id.setMaxLength(1200)
        self.oauth_client_id.setPlaceholderText("Client ID")
        self.oauth_client_secret = QLineEdit()
        self.oauth_client_secret.setMaxLength(6000)
        self.oauth_client_secret.setEchoMode(QLineEdit.Password)
        self.oauth_client_secret.setPlaceholderText("Client secret (si requis)")
        self.oauth_auth_method = QComboBox()
        for label, value in (("Client public", "none"), ("Secret : HTTP Basic", "client_secret_basic"), ("Secret : POST", "client_secret_post")):
            self.oauth_auth_method.addItem(label, value)
        self.oauth_port = QSpinBox()
        self.oauth_port.setRange(1024, 65535)
        self.oauth_port.setValue(8973)
        self.oauth_callback = QLabel()
        self.oauth_callback.setWordWrap(True)
        self.oauth_callback.setTextFormat(Qt.PlainText)
        self.oauth_port.valueChanged.connect(lambda port: self.oauth_callback.setText(f"http://127.0.0.1:{port}/oauth/callback"))
        self.oauth_callback.setText("http://127.0.0.1:8973/oauth/callback")
        self._oauth_client_widgets = (self.oauth_client_id, self.oauth_client_secret, self.oauth_auth_method, self.oauth_port, self.oauth_callback)
        for widget in self._oauth_client_widgets:
            layout.addWidget(widget)
            widget.setVisible(False)
        self.oauth_registered.toggled.connect(lambda checked: [widget.setVisible(checked) for widget in self._oauth_client_widgets])
        oauth = QHBoxLayout()
        self.oauth_button = QPushButton("OAuth")
        self.oauth_button.setToolTip("Ouvrir le consentement OAuth explicite")
        self.oauth_button.setIcon(self.style().standardIcon(QStyle.SP_DialogApplyButton))
        self.cancel_oauth_button = QPushButton()
        self.cancel_oauth_button.setIcon(self.style().standardIcon(QStyle.SP_BrowserStop))
        self.cancel_oauth_button.setToolTip("Annuler la demande OAuth en cours")
        self.cancel_oauth_button.setAccessibleName("Annuler OAuth")
        self.cancel_oauth_button.setEnabled(False)
        oauth.addWidget(self.oauth_button)
        oauth.addWidget(self.cancel_oauth_button)
        layout.addLayout(oauth)
        layout.addStretch(1)
        layout = activity_layout
        quotas = QFormLayout()
        quotas.setRowWrapPolicy(QFormLayout.WrapAllRows)
        self.session_quota = QSpinBox()
        self.session_quota.setRange(1, 200)
        self.session_quota.setValue(100)
        self.call_quota = QSpinBox()
        self.call_quota.setRange(1, 1000)
        self.call_quota.setValue(60)
        quotas.addRow("Sessions / heure UTC", self.session_quota)
        quotas.addRow("Appels / heure UTC", self.call_quota)
        layout.addLayout(quotas)
        self.session_quota.valueChanged.connect(lambda: setattr(self, "_quota_dirty", True))
        self.call_quota.valueChanged.connect(lambda: setattr(self, "_quota_dirty", True))
        self.save_quotas_button = QPushButton("Appliquer les quotas")
        self.save_quotas_button.setIcon(self.style().standardIcon(QStyle.SP_DialogSaveButton))
        layout.addWidget(self.save_quotas_button)
        self.activity_label = QLabel("Aucune tentative enregistree.")
        self.activity_label.setWordWrap(True)
        self.activity_label.setTextFormat(Qt.PlainText)
        layout.addWidget(self.activity_label)
        layout.addStretch(1)
        layout = tools_layout
        self.tool_picker = QComboBox()
        self.tool_picker.setObjectName("missionPicker")
        self.tool_picker.addItem("Sélectionner outil découvert…", "")
        layout.addWidget(self.tool_picker)
        self.tool_description = QLabel()
        self.tool_description.setTextFormat(Qt.PlainText)
        self.tool_description.setWordWrap(True)
        layout.addWidget(self.tool_description)
        self.tool_picker.currentIndexChanged.connect(self._select_tool)
        bottom = QHBoxLayout()
        self.allow_button = QPushButton("Autoriser")
        self.allow_button.setIcon(self.style().standardIcon(QStyle.SP_DialogApplyButton))
        self.allow_button.setToolTip("Autoriser cet outil seulement ; chaque appel exige encore confirmation")
        self.deny_button = QPushButton("Bloquer")
        self.deny_button.setIcon(self.style().standardIcon(QStyle.SP_DialogCancelButton))
        self.deny_button.setToolTip("Retirer cet outil des capacites de Jarvis")
        for button in (self.allow_button, self.deny_button):
            button.setObjectName("missionButton")
            bottom.addWidget(button)
        layout.addLayout(bottom)
        revocation = QHBoxLayout()
        self.revoke_button = QPushButton("Revoquer les outils")
        self.revoke_button.setIcon(self.style().standardIcon(QStyle.SP_DialogCancelButton))
        self.remove_button = QPushButton()
        self.remove_button.setIcon(self.style().standardIcon(QStyle.SP_TrashIcon))
        self.remove_button.setToolTip("Retirer ce serveur du registre local")
        self.remove_button.setAccessibleName("Retirer le serveur MCP")
        revocation.addWidget(self.revoke_button)
        revocation.addWidget(self.remove_button)
        layout.addLayout(revocation)
        self.inventory = QPlainTextEdit()
        self.inventory.setReadOnly(True)
        self.inventory.setMinimumHeight(140)
        self.inventory.setMaximumHeight(240)
        layout.addWidget(self.inventory)
        self.feedback = QLabel(
            "Chaque serveur est isolé. Chaque outil doit être autorisé à part. "
            "Même autorisé, tout appel demande confirmation."
        )
        self.feedback.setWordWrap(True)
        self.feedback.setObjectName("operatorMetric")
        self.feedback.setTextFormat(Qt.PlainText)
        root.addWidget(self.feedback)
        root.addStretch(1)

        self.add_button.clicked.connect(
            self._add_server
        )
        self.hermes_button.clicked.connect(
            lambda: self._send("add_hermes", "hermes", "")
        )
        self.server_picker.currentIndexChanged.connect(self._select_server)
        self.enable_button.clicked.connect(lambda: self._for_server("enable"))
        self.disable_button.clicked.connect(lambda: self._for_server("disable"))
        self.connect_button.clicked.connect(lambda: self._for_server("connect"))
        self.disconnect_button.clicked.connect(lambda: self._for_server("disconnect"))
        self.discover_button.clicked.connect(lambda: self._for_server("reconnect"))
        self.allow_button.clicked.connect(lambda: self._for_tool("allow_tool"))
        self.deny_button.clicked.connect(lambda: self._for_tool("deny_tool"))
        self.revoke_button.clicked.connect(lambda: self._for_server("revoke_tools"))
        self.remove_button.clicked.connect(lambda: self._for_server("remove_server"))
        self.store_credential_button.clicked.connect(self._store_bearer)
        self.forget_credential_button.clicked.connect(lambda: self._for_server("forget_credentials"))
        self.oauth_button.clicked.connect(self._authorize)
        self.cancel_oauth_button.clicked.connect(lambda: self._send("cancel_oauth", self._authorization_pending, ""))
        self.save_quotas_button.clicked.connect(lambda: self._send("set_quotas", str(self.server_picker.currentData() or ""),
            json.dumps({"sessions_per_hour": self.session_quota.value(), "calls_per_hour": self.call_quota.value()})))
        for picker in self.findChildren(QComboBox):
            picker.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            picker.setMinimumContentsLength(12)
        self._select_server(0)
        self.setStyleSheet("""
            QFrame#mcpConnectionsPanel {background:#081c2c;}
            QLabel, QCheckBox {color:#c5e6ed;font-size:11px;}
            QLineEdit,QComboBox,QSpinBox,QPlainTextEdit {background:#061723;color:#e4f9ff;
                border:1px solid #286278;border-radius:4px;padding:5px;font-size:11px;}
            QPushButton {background:#163e4b;color:#c5eee5;border:1px solid #286278;
                border-radius:4px;padding:6px;font-size:11px;}
            QPushButton:disabled {color:#6e8791;}
            QTabWidget::pane {border:0;}
            QTabBar::tab {background:#102a38;color:#b4dce7;padding:7px;font-size:11px;}
            QTabBar::tab:selected {background:#225164;color:#ffffff;}
        """)

    def _set_runtime(self, checked):
        self._runtime_pending = True
        self.runtime_switch.setEnabled(False)
        self._send("runtime_set", "", "true" if checked else "false")

    def _authorize(self):
        server = str(self.server_picker.currentData() or "")
        if server:
            options = {"scope": self.oauth_scope.text().strip()}
            if self.oauth_registered.isChecked():
                if not self.oauth_client_id.text().strip():
                    self.feedback.setText("Client ID requis.")
                    return
                options.update(client_id=self.oauth_client_id.text().strip(),
                    client_secret=self.oauth_client_secret.text(), auth_method=self.oauth_auth_method.currentData(),
                    callback_port=self.oauth_port.value())
            self.oauth_client_secret.clear()
            self._authorization_pending = server
            self._select_server(self.server_picker.currentIndex())
            self._send("oauth_authorize", server, json.dumps(options))

    def _store_bearer(self):
        value = self.bearer.text().strip()
        if value:
            kind = self.credential_kind.currentData()
            self._send("store_bearer" if kind == "bearer" else "store_api_key",
                str(self.server_picker.currentData() or ""), value if kind == "bearer" else json.dumps({"header": kind, "key": value}))
            self.bearer.clear()

    def _select_transport(self):
        local = self.transport_picker.currentData() == "stdio"
        self.server_url.setVisible(not local)
        for widget in (self.stdio_command, self.stdio_arguments, self.stdio_environment, self.stdio_warning, self.stdio_trust):
            widget.setVisible(local)
        if hasattr(self, "add_button"):
            self.add_button.setText("Ajouter local" if local else "Ajouter distant")

    def _add_server(self):
        if self.transport_picker.currentData() != "stdio":
            self._send("add_http", self.server_name.text(), self.server_url.text())
            return
        if not self.stdio_trust.isChecked():
            self.feedback.setText("La confiance explicite dans le programme local est requise.")
            return
        try:
            packet = {"command": self.stdio_command.text().strip(),
                      "args": json.loads(self.stdio_arguments.text()),
                      "env_refs": json.loads(self.stdio_environment.text()), "trusted": True}
        except ValueError:
            self.feedback.setText("Arguments ou references d'environnement JSON invalides.")
            return
        self._send("add_stdio", self.server_name.text(), json.dumps(packet))

    def _select_catalog_service(self, index: int) -> None:
        card = find_card(str(self.catalog_picker.currentData() or ""))
        if card is None:
            return
        self.catalog_note.setText(
            card.label + " : " + card.requirement + " " + card.warning
        )
        self.catalog_endpoint.setText((card.endpoint or "stdio local, installation existante requise")
                                     + "\n" + card.documentation)

    def _prepare_catalog_service(self):
        card = find_card(str(self.catalog_picker.currentData() or ""))
        if card is None:
            return
        self.server_name.setText(card.identifier)
        self.server_url.setText(card.endpoint)
        self.transport_picker.setCurrentIndex(1 if card.authentication == "stdio" else 0)
        self.tabs.setCurrentWidget(self.add_page)
        self.feedback.setText("Configuration preparee, aucun serveur ajoute ni compte autorise.")

    def _send(self, operation: str, server: str, value: str) -> None:
        if (not server and operation != "runtime_set") or (operation == "add_http" and not value):
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
        if server_id != self._selected_server:
            self.bearer.clear()
            self.oauth_client_secret.clear()
            self.oauth_client_id.clear()
            self.oauth_scope.clear()
            self.oauth_registered.setChecked(False)
            self._quota_dirty = False
            self._selected_server = server_id
        self.tool_picker.blockSignals(True)
        self.tool_picker.clear()
        self.tool_picker.addItem("Sélectionner outil découvert…", "")
        self.connection_status.setText("Aucun serveur sélectionné.")
        self.endpoint_label.clear()
        self.inventory.clear()
        self.tool_description.clear()
        self.store_credential_button.setEnabled(False)
        self.forget_credential_button.setEnabled(False)
        self.oauth_button.setEnabled(False)
        self.save_quotas_button.setEnabled(False)
        self.oauth_registered.setEnabled(False)
        self.oauth_scope.setEnabled(False)
        self.activity_label.setText("Aucune tentative enregistree.")
        for entry in self._servers:
            if entry["id"] != server_id:
                continue
            http = entry.get("kind") == "http"
            self.store_credential_button.setEnabled(http)
            self.forget_credential_button.setEnabled(http)
            self.oauth_button.setEnabled(http)
            self.oauth_registered.setEnabled(http)
            self.oauth_scope.setEnabled(http)
            self.save_quotas_button.setEnabled(True)
            limits = entry.get("quotas") or {}
            if not self._quota_dirty:
                for widget, key, default in ((self.session_quota, "sessions_per_hour", 100), (self.call_quota, "calls_per_hour", 60)):
                    widget.blockSignals(True)
                    widget.setValue(limits.get(key, default))
                    widget.blockSignals(False)
            activity = entry.get("activity") or {}
            if activity.get("unavailable"):
                self.activity_label.setText("Journal MCP indisponible ; les appels seront bloques.")
            else:
                history = [str(row.get("utc", ""))[:19] + " : " + str(row.get("operation", ""))
                           + " / " + str(row.get("outcome", "")) for row in activity.get("recent", [])[:4]]
                self.activity_label.setText("Heure UTC : sessions " + str(activity.get("sessions_used", 0))
                    + " ; appels " + str(activity.get("calls_used", 0)) + ("\n" + "\n".join(history) if history else ""))
            last = str(entry.get("last_discovery_success_utc") or "")
            state = {"disconnected": "Deconnecte", "tested_session_closed": "Connexion testee ; session fermee",
                     "authorization_required": "Autorisation necessaire", "transport_error": "Erreur de connexion"}.get(entry.get("connection_state"), "Etat inconnu")
            self.connection_status.setText(
                ("Configuration activée" if entry.get("enabled") else "Configuration désactivée")
                + " · " + state
                + (
                    " · dernière découverte réussie : " + last[:25]
                    if last else " · aucune découverte réussie enregistrée"
                )
                + " · credentials : " + str(entry.get("credential_source", "environment"))
            )
            self.endpoint_label.setText(str(entry.get("endpoint", "")))
            lines = ["Capacites annoncees : " + ", ".join(k for k, v in (entry.get("capabilities") or {}).items() if v is True)]
            for key, heading in (("resources", "Ressources"), ("resource_templates", "Modeles de ressources"), ("prompts", "Prompts")):
                lines.append("\n" + heading + " : " + str(len(entry.get(key) or [])))
                for item in entry.get(key) or []:
                    lines.append(str(item.get("name", "")) + "  " + str(item.get("uri", item.get("uri_template", ""))))
                    if item.get("description"):
                        lines.append(str(item["description"]))
            if entry.get("inventory_truncated"):
                lines.append("Inventaire limite : " + ", ".join(entry["inventory_truncated"]))
            self.inventory.setPlainText("\n".join(lines))
            for item in entry.get("tools") or []:
                flag = "✓" if item.get("allowed") else "○"
                self.tool_picker.addItem(
                    f'{flag} {str(item.get("name", ""))[:70]}',
                    item.get("name", "")
                )
        self.tool_picker.blockSignals(False)
        busy = bool(self._authorization_pending)
        self.cancel_oauth_button.setEnabled(busy)
        self.server_picker.setEnabled(not busy)
        for button in (self.add_button, self.hermes_button, self.enable_button, self.disable_button,
                       self.connect_button, self.disconnect_button, self.discover_button,
                       self.allow_button, self.deny_button, self.revoke_button, self.remove_button):
            button.setEnabled(not busy and (bool(server_id) or button in (self.add_button, self.hermes_button)))
        if busy:
            self.oauth_registered.setEnabled(False)
            self.oauth_scope.setEnabled(False)
            for button in (self.store_credential_button, self.forget_credential_button, self.oauth_button, self.save_quotas_button):
                button.setEnabled(False)
        for widget in self._oauth_client_widgets:
            widget.setEnabled(not busy and self.oauth_registered.isEnabled())
        self._select_tool()

    def _select_tool(self, index=0):
        name = self.tool_picker.currentData()
        description = next((str(tool.get("description", "")) for entry in self._servers
            if entry["id"] == self.server_picker.currentData() for tool in entry.get("tools") or []
            if tool.get("name") == name), "")
        self.tool_description.setText(description)
        for button in (self.allow_button, self.deny_button):
            button.setEnabled(bool(name) and not self._authorization_pending)

    def apply_snapshot(self, data: dict) -> None:
        enabled = data.get("enabled") is True
        self._runtime_enabled = enabled
        if not self._runtime_pending:
            self.runtime_switch.blockSignals(True)
            self.runtime_switch.setChecked(enabled)
            self.runtime_switch.blockSignals(False)
        servers = tuple(data.get("servers") or [])
        count = sum(row.get("allowed", 0) for row in servers)
        self.summary.setText(
            f"Runtime MCP : {'ON' if enabled else 'OFF'}"
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
        if result.get("operation") == "runtime_set":
            self._runtime_pending = False
            self.runtime_switch.setEnabled(True)
            if result.get("success"):
                self._runtime_enabled = self.runtime_switch.isChecked()
            self.runtime_switch.blockSignals(True)
            self.runtime_switch.setChecked(self._runtime_enabled)
            self.runtime_switch.blockSignals(False)
        if result.get("operation") == "oauth_authorize":
            self._authorization_pending = ""
            self._select_server(self.server_picker.currentIndex())
        if result.get("operation") == "set_quotas" and result.get("success"):
            self._quota_dirty = False
        if result.get("success"):
            self.feedback.setText(
                str(result.get("message") or "Configuration MCP modifiée.")[:160]
            )
        else:
            self.feedback.setText(
                "MCP refusé : " + str(result.get("reason") or "échec")[:100]
            )
