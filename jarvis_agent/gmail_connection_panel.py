"""Gmail's plugin-style onboarding. Local metadata only on the Qt thread."""
import json
import sqlite3

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QSizePolicy, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .gmail_connection import GROUPS, PIPEDREAM_ENDPOINT, managed_server_id, snapshot
from .mcp_server_registry import MCPRegistry
from .personal_workspace import PersonalWorkspaceStore


STATES = {"not_configured": "À connecter", "disabled": "Serveur désactivé",
    "discovery_required": "Connexion à tester", "no_gmail_tools": "Aucun outil Gmail reconnu",
    "tools_to_allow": "Outils découverts · accès à choisir", "tools_available": "Outils Gmail autorisés"}
ERRORS = {"gmail_binding_changed": "L’association Gmail a changé. Recharge avant de continuer.",
    "mcp_failure_cooldown": "Une tentative vient d’échouer. Attends quinze secondes avant de reconnecter.",
    "mcp_hourly_quota_exhausted": "Le quota MCP de cette heure est atteint. Consulte les quotas dans les réglages avancés.",
    "mcp_unknown_requires_review": "Un envoi reste incertain. Vérifie son résultat dans le service et examine le journal MCP avant une nouvelle demande.",
    "gmail_server_conflict": "Cet emplacement est déjà utilisé. Recharge ou choisis ton serveur existant.",
    "gmail_server_not_found": "Ce serveur n’existe plus. Recharge la liste.",
    "gmail_connection_changed": "La connexion a changé. Recharge les capacités avant d’autoriser.",
    "gmail_inventory_changed": "Les outils ou leurs accès ont changé. Recharge puis vérifie ta sélection.",
    "mcp_tool_review_changed": "Un outil a changé. Recharge les capacités avant d’autoriser.",
    "gmail_no_tools": "Aucun outil Gmail reconnu. Sélectionne Gmail dans le navigateur du fournisseur.",
    "authorization_required": "Autorisation requise. Reconnecte le compte ou consulte les réglages avancés.",
    "transport_error": "La connexion n’a pas abouti. Vérifie le réseau et le compte, puis réessaie.",
    "oauth_cancelled": "Connexion annulée. Aucun accès aux mails n’a été accordé par cette opération.",
    "gmail_oauth_cancelled": "Connexion annulée. Aucun accès aux mails n’a été accordé par cette opération.",
    "oauth_timeout": "Le délai de connexion a expiré. Tu peux recommencer depuis ce bouton."}


class GmailConnectionPanel(QFrame):
    requested = Signal(str, str, str)
    request_ready = Signal(str)
    advanced_requested = Signal(str)

    def __init__(self, parent=None, *, registry=None, workspace=None):
        super().__init__(parent)
        self.setObjectName("gmailConnectionPanel")
        self.registry = registry or MCPRegistry()
        self.workspace = workspace or PersonalWorkspaceStore()
        self._busy = self._filling = self._dirty = False
        self._review = None
        self._signature = ""
        self._runtime_enabled = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("gmailConnectionBody")
        body.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(6, 6, 6, 6)
        self.scroll.setWidget(body)
        outer.addWidget(self.scroll)
        self._note(layout, "GMAIL · CONNEXION ET CAPACITÉS")
        self.status = self._note(layout, "À connecter")
        self.feedback = self._note(layout, "Aucun mail n’est lu ou envoyé pendant cette configuration.")
        self._note(layout, "1. Connecte ton compte. 2. Choisis les outils disponibles. 3. Vérifie ta boîte avec Jarvis.")
        self.provider = QComboBox()
        self.provider.addItem("Connexion Google gérée par Pipedream", "pipedream")
        self.provider.addItem("Utiliser un serveur MCP déjà configuré", "existing")
        layout.addWidget(self.provider)
        self.server_picker = QComboBox()
        layout.addWidget(self.server_picker)
        self.provider_note = self._note(layout, "")
        self.connect_button = self._button(layout, "Connecter Gmail avec Google", self.start_connection)
        self.cancel_button = self._button(layout, "Annuler la connexion", self.cancel_connection)
        self.cancel_button.setEnabled(False)
        self.refresh_button = self._button(layout, "Actualiser les capacités depuis le serveur", self.reconnect)
        self.capabilities = self._note(layout, "Les capacités apparaîtront après connexion.")
        self.tools = QTreeWidget()
        self.tools.setHeaderLabels(["Outil Gmail", "Fonction"])
        self.tools.setRootIsDecorated(False)
        self.tools.setMinimumHeight(190)
        self.tools.setColumnWidth(0, 160)
        self.tools.itemChanged.connect(self._selection_changed)
        layout.addWidget(self.tools)
        choices = QHBoxLayout()
        self.reads_button = QPushButton("Cocher les lectures")
        self.all_button = QPushButton("Tout cocher")
        self.reads_button.clicked.connect(lambda: self.select_tools(reads_only=True))
        self.all_button.clicked.connect(lambda: self.select_tools(reads_only=False))
        choices.addWidget(self.reads_button)
        choices.addWidget(self.all_button)
        layout.addLayout(choices)
        self.auto_reads = QCheckBox("Lectures automatiques")
        self.auto_reads.setChecked(True)
        self.auto_reads.toggled.connect(self._selection_changed)
        layout.addWidget(self.auto_reads)
        self._note(layout, "Les outils cochés seront proposés à Jarvis. Les envois, modifications et suppressions conservent leurs confirmations. Les contenus consultés pourront être traités par le cerveau IA configuré.")
        self.grant_button = self._button(layout, "Enregistrer les capacités cochées", self.grant)
        self.reload_button = self._button(layout, "Recharger la sélection", self.reload)
        self.runtime_button = self._button(layout, "Activer MCP dans Jarvis", lambda: self.requested.emit("runtime_set", "", "true"))
        self.test_button = self._button(layout, "Vérifier ma boîte avec Jarvis", self.test_mailbox)
        self._note(layout, "Le compte Gmail réellement utilisé reste à vérifier avec un outil du service. Une connexion OAuth ou un inventaire ne prouve pas l’identité de la boîte ni l’envoi d’un message.")
        self.advanced_button = self._button(layout, "Ouvrir les réglages MCP avancés", lambda: self.advanced_requested.emit(self.server_id()))
        layout.addStretch(1)
        self.provider.currentIndexChanged.connect(self._provider_changed)
        self.server_picker.currentIndexChanged.connect(self._server_changed)
        self._initial_choices()
        for picker in (self.provider, self.server_picker):
            picker.setMinimumWidth(0)
            picker.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            picker.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.refresh()
        self.setStyleSheet("""
            QFrame#gmailConnectionPanel,QWidget#gmailConnectionBody,QScrollArea {background:#081c2c;}
            QLabel,QCheckBox {color:#c5e6ed;font-size:11px;}
            QTreeWidget,QComboBox {background:#061723;color:#dceef3;border:1px solid #286278;font-size:11px;}
            QHeaderView::section {background:#163e4b;color:#dceef3;}
            QPushButton {background:#163e4b;color:#c5eee5;border:1px solid #286278;border-radius:4px;padding:6px;font-size:11px;}
            QPushButton:disabled {color:#6e8791;}
        """)

    @staticmethod
    def _note(layout, text):
        label = QLabel(text)
        label.setWordWrap(True)
        label.setTextFormat(Qt.PlainText)
        layout.addWidget(label)
        return label

    @staticmethod
    def _button(layout, title, callback):
        button = QPushButton(title)
        button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        button.clicked.connect(callback)
        layout.addWidget(button)
        return button

    def _initial_choices(self):
        binding = self.workspace.bindings().get("gmail") or {}
        entry = self.registry.get_server(binding.get("server")) if binding.get("server") else None
        if entry:
            self.provider.setCurrentIndex(0 if entry.get("url") == PIPEDREAM_ENDPOINT else 1)
        self._provider_changed()
        index = self.server_picker.findData(binding.get("server"))
        if index >= 0:
            self.server_picker.setCurrentIndex(index)

    def _provider_changed(self):
        managed = self.provider.currentData() == "pipedream"
        self.server_picker.setVisible(not managed)
        self.provider_note.setText("Pipedream gère la connexion et les appels au service. Dans le navigateur, sélectionne Gmail et vérifie les droits Google demandés. Ton contenu transite par ce fournisseur ; ses conditions et quotas s’appliquent." if managed else
            "Le serveur choisi conserve son mode d’authentification. Ce bouton teste sa connexion et l’associe à Gmail. Les outils d’autres services ne sont pas sélectionnés ici.")
        self.connect_button.setText("Connecter Gmail avec Google" if managed else "Tester et associer ce serveur")
        self._dirty, self._review, self._signature = False, None, ""
        self.refresh()

    def _server_changed(self):
        if not self._filling:
            self._dirty, self._review, self._signature = False, None, ""
            self.refresh()

    def server_id(self):
        if self.provider.currentData() == "pipedream":
            return managed_server_id(self.registry, self.workspace)
        return self.server_picker.currentData() or ""

    def _selection_changed(self, *_):
        if not self._filling:
            self._dirty = True

    def select_tools(self, *, reads_only):
        if self._review:
            kinds = {t["name"]: t["group"] for t in self._review["tools"]}
            for index in range(self.tools.topLevelItemCount()):
                item = self.tools.topLevelItem(index)
                item.setCheckState(0, Qt.Checked if not reads_only or kinds[item.data(0, Qt.UserRole)] == "read" else Qt.Unchecked)

    def start_connection(self):
        try:
            sid = self.server_id()
            if not sid:
                self.feedback.setText("Choisis un serveur existant ou la connexion gérée.")
                return
            revision = (self.workspace.bindings().get("gmail") or {}).get("revision", 0)
            self._pending_server = sid
            self.set_busy(True)
            self.feedback.setText("Connexion en cours dans le navigateur. Termine la sélection Gmail et le consentement, puis reviens dans Jarvis.")
            self.requested.emit("gmail_connect", sid, json.dumps({"provider": self.provider.currentData(), "binding_revision": revision}))
        except (ValueError, KeyError, OSError, sqlite3.Error):
            self.feedback.setText("Configuration indisponible. Recharge avant de réessayer.")

    def cancel_connection(self):
        self.requested.emit("cancel_oauth", getattr(self, "_pending_server", ""), "")
        self.feedback.setText("Annulation demandée ; attente de la fin de la connexion.")

    def reconnect(self):
        sid = self.server_id()
        if self.registry.get_server(sid):
            self.requested.emit("reconnect", sid, "")

    def grant(self):
        if self._review is None or not self._review["tools"]:
            return
        selected = [self.tools.topLevelItem(i).data(0, Qt.UserRole) for i in range(self.tools.topLevelItemCount())
                    if self.tools.topLevelItem(i).checkState(0) == Qt.Checked]
        packet = {k: self._review[k] for k in ("binding_revision", "connection", "inventory")}
        packet.update(selected=selected, automatic_reads=self.auto_reads.isChecked())
        self.requested.emit("gmail_grant", self._review["server"], json.dumps(packet))

    def reload(self):
        self._dirty, self._review, self._signature = False, None, ""
        self.refresh()

    def test_mailbox(self):
        self.request_ready.emit("Vérifie quel compte Gmail est réellement connecté avec les outils disponibles, puis indique le nombre de messages non lus si cette lecture est possible. Ne modifie rien dans ma boîte.")

    def set_busy(self, busy):
        self._busy = bool(busy)
        self.cancel_button.setEnabled(self._busy)
        for widget in (self.provider, self.server_picker, self.connect_button, self.refresh_button, self.tools,
            self.reads_button, self.all_button, self.auto_reads, self.grant_button, self.reload_button, self.runtime_button,
            self.test_button, self.advanced_button):
            widget.setEnabled(not self._busy)
        if not busy:
            self.refresh()

    def show_result(self, result):
        operation = result.get("operation")
        if operation == "gmail_connect":
            self._busy = False
            if result.get("success"):
                self._dirty = False
                self._signature = ""
                self._initial_choices()
            self.set_busy(False)
        elif operation == "gmail_grant" and result.get("success"):
            self.reload()
        if operation in {"gmail_connect", "gmail_grant", "runtime_set", "reconnect"}:
            self.feedback.setText(str(result.get("message")) if result.get("success") and result.get("message") else
                ERRORS.get(result.get("reason"), "La demande n’a pas abouti. Vérifie les réglages et recharge les capacités."))
        self.refresh()

    def refresh(self):
        if self._busy:
            return
        try:
            self._filling = True
            selected = self.server_picker.currentData()
            rows = self.registry.list_servers()
            if [(self.server_picker.itemData(i)) for i in range(self.server_picker.count())] != [r["id"] for r in rows]:
                self.server_picker.blockSignals(True)
                self.server_picker.clear()
                for row in rows:
                    self.server_picker.addItem(row["id"], row["id"])
                index = self.server_picker.findData(selected)
                if index >= 0:
                    self.server_picker.setCurrentIndex(index)
                self.server_picker.blockSignals(False)
            sid = self.server_id()
            state = snapshot(self.registry, self.workspace, server_id=sid) if sid else None
            if state is None:
                self.status.setText("Choisis un serveur MCP existant.")
                self.connect_button.setEnabled(False)
                return
            self.connect_button.setEnabled(True)
            self.status.setText(STATES[state["state"]] + (" · MCP actif" if state["runtime_enabled"] else " · MCP désactivé") +
                (" · autorisation à renouveler" if state["connection_state"] == "authorization_required" else ""))
            self.capabilities.setText("\n".join(label + " : " + str(state["groups"][group]["allowed"]) + " autorisé(s) / " +
                str(state["groups"][group]["discovered"]) + " découvert(s)" for group, label in GROUPS.items()) +
                ("\nInventaire partiel : des capacités peuvent manquer." if state["inventory_partial"] else ""))
            signature = json.dumps({k: state[k] for k in ("server", "binding_revision", "connection", "inventory")}, sort_keys=True)
            stale = bool(self._dirty and signature != self._signature)
            if not self._dirty and signature != self._signature:
                self.tools.clear()
                for tool in state["tools"]:
                    item = QTreeWidgetItem([tool["name"], GROUPS[tool["group"]]])
                    item.setData(0, Qt.UserRole, tool["name"])
                    item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                    item.setCheckState(0, Qt.Checked if tool["allowed"] else Qt.Unchecked)
                    item.setToolTip(0, tool["name"] + "\n" + tool["description"])
                    self.tools.addTopLevelItem(item)
                self._review, self._signature = state, signature
            if stale:
                self.feedback.setText("Les capacités ou accès ont changé. Ta sélection est conservée ; recharge avant d’enregistrer.")
            can_grant = bool(state["tools"] and state["bound_server"] == sid and
                state["state"] not in {"disabled", "discovery_required"} and not stale)
            self.grant_button.setEnabled(can_grant)
            self.refresh_button.setEnabled(self.registry.get_server(sid) is not None)
            self.runtime_button.setVisible(not state["runtime_enabled"])
            self.test_button.setEnabled(state["runtime_enabled"] and state["state"] == "tools_available")
            self.reads_button.setEnabled(bool(state["tools"]))
            self.all_button.setEnabled(bool(state["tools"]))
        except (ValueError, TypeError, KeyError, OSError, sqlite3.Error):
            self.status.setText("Configuration Gmail temporairement indisponible.")
            self.grant_button.setEnabled(False)
        finally:
            self._filling = False
