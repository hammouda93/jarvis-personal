"""Gmail's plugin-style onboarding. Local metadata only on the Qt thread."""
import json
import sqlite3

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QSizePolicy, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .gmail_connection import GROUPS, PIPEDREAM_ENDPOINT, managed_server_id, snapshot
from .connector_functions_panel import ConnectorFunctionsPanel
from .mcp_server_registry import MCPRegistry
from .personal_workspace import PersonalWorkspaceStore


STATES = {"not_configured": "À connecter", "disabled": "À reconnecter",
    "discovery_required": "Connexion à vérifier", "no_gmail_tools": "Gmail à sélectionner chez le fournisseur",
    "tools_to_allow": "Connecté · fonctions à choisir", "tools_available": "Fonctions Gmail enregistrées"}
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
    "oauth_timeout": "Le délai de connexion a expiré. Recommence depuis Jarvis ; une ancienne page de retour ne peut pas terminer cette tentative.",
    "oauth_token_exchange_failed": "Le fournisseur n’a pas remis d’accès utilisable après le consentement. Le détail de l’échange apparaît ci-dessous.",
    "oauth_connection_failed": "La connexion n’a pas pu être terminée après son démarrage. Le détail de l’étape apparaît ci-dessous."}
STAGES = {"preparing": "Préparation de la connexion", "metadata": "Vérification du fournisseur",
    "registration": "Enregistrement de Jarvis", "waiting_consent": "Termine le consentement dans le navigateur",
    "callback_received": "Retour du navigateur reçu", "token_exchange": "Échange de l’autorisation avec le fournisseur",
    "session_initializing": "Ouverture de la session MCP", "tools_discovery": "Découverte des fonctions",
    "credentials_storage": "Enregistrement sécurisé de la connexion", "authorized": "Autorisation enregistrée",
    "service_inventory": "Vérification des fonctions disponibles"}


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
        self._state = None
        self._runtime_enabled = False
        self._connection_stage = "preparing"
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
        self._note(layout, "GMAIL")
        self.status = self._note(layout, "À connecter")
        self.feedback = self._note(layout, "Aucun mail n’est lu ou envoyé pendant cette configuration.")
        self.step = self._note(layout, "1. Connecte ton compte Gmail.")
        self.provider_note = self._note(layout, "")
        self.access_note = self._note(layout, "Connecter Gmail autorise Jarvis à utiliser ses fonctions disponibles. Les connexions déjà autorisées dans Jarvis seront aussi activées.")
        self.connect_button = self._button(layout, "Connecter Gmail", self.start_connection)
        self.cancel_button = self._button(layout, "Annuler la connexion", self.cancel_connection)
        self.cancel_button.hide()
        self.functions_section = QFrame()
        functions_layout = QVBoxLayout(self.functions_section)
        functions_layout.setContentsMargins(0, 0, 0, 0)
        self._note(functions_layout, "Ce que Jarvis peut faire pour toi")
        self.available_functions = self._note(functions_layout, "")
        self.functions = ConnectorFunctionsPanel({"read": "Lire et rechercher", "draft": "Gérer les brouillons",
            "send": "Envoyer et répondre", "organize": "Classer les messages",
            "delete": "Supprimer des messages", "other": "Autres fonctions"})
        self.functions.selection_changed.connect(self._group_selection_changed)
        choices = QHBoxLayout()
        self.reads_button = QPushButton("Lecture seule")
        self.all_button = QPushButton("Tout sélectionner")
        self.reads_button.clicked.connect(lambda: self.select_tools(reads_only=True))
        self.all_button.clicked.connect(lambda: self.select_tools(reads_only=False))
        choices.addWidget(self.reads_button)
        choices.addWidget(self.all_button)
        self._note(functions_layout, "Les envois et modifications demandent confirmation. Les contenus consultés peuvent être traités par ton fournisseur IA.")
        layout.addWidget(self.functions_section)
        self.reload_button = self._button(layout, "Recharger les fonctions", self.reload)
        self.runtime_note = self._note(layout, "Cette activation permet à Jarvis d’utiliser aussi les autres services déjà autorisés.")
        self.runtime_button = self._button(layout, "Activer mes connexions dans Jarvis", lambda: self.requested.emit("runtime_set", "", "true"))
        self.test_button = self._button(layout, "Vérifier le compte (facultatif)", self.test_mailbox)
        self._note(layout, "Tu peux vérifier le compte utilisé dans la conversation avec Jarvis.")
        self.advanced_toggle = QPushButton("Réglages avancés ▸")
        self.advanced_toggle.setCheckable(True)
        layout.addWidget(self.advanced_toggle)
        self.advanced_body = QFrame()
        advanced = QVBoxLayout(self.advanced_body)
        advanced.setContentsMargins(0, 0, 0, 0)
        self.advanced_toggle.toggled.connect(self._toggle_advanced)
        layout.addWidget(self.advanced_body)
        self.advanced_body.hide()
        self.provider = QComboBox()
        self.provider.addItem("Connexion Google gérée par Pipedream", "pipedream")
        self.provider.addItem("Utiliser un serveur MCP déjà configuré", "existing")
        advanced.addWidget(self.provider)
        self.server_picker = QComboBox()
        advanced.addWidget(self.server_picker)
        self.account_button = self._button(advanced, "Connexion avec droits personnalisés", lambda: self.start_connection(access="manual"))
        self.refresh_button = self._button(advanced, "Actualiser les fonctions", self.reconnect)
        self.capabilities = self._note(advanced, "Les fonctions apparaîtront après connexion.")
        advanced.addWidget(self.functions)
        advanced.addLayout(choices)
        self.tools = QTreeWidget()
        self.tools.setHeaderLabels(["Nom technique", "Fonction"])
        self.tools.setRootIsDecorated(False)
        self.tools.setMinimumHeight(190)
        self.tools.setColumnWidth(0, 160)
        self.tools.itemChanged.connect(self._selection_changed)
        advanced.addWidget(self.tools)
        self.auto_reads = QCheckBox("Lectures sans confirmation")
        self.auto_reads.setChecked(True)
        self.auto_reads.toggled.connect(self._selection_changed)
        advanced.addWidget(self.auto_reads)
        self.grant_button = self._button(advanced, "Enregistrer mes choix avancés", self.grant)
        self._note(advanced, "Les lectures restent limitées aux outils reconnus par la politique de Jarvis. Les autres appels conservent leur confirmation.")
        self.advanced_button = self._button(advanced, "Ouvrir le centre MCP", lambda: self.advanced_requested.emit(self.server_id()))
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
        self.provider_note.setText("Connexion gérée par Pipedream. Sélectionne Gmail dans le navigateur ; tes données transitent par ce fournisseur." if managed else
            "Connexion avec le service déjà configuré. Ses accès et son authentification sont conservés.")
        self.connect_button.setText("Connecter Gmail avec Google" if managed else "Connecter ce service à Gmail")
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
            self.functions.set_selection(self._selected_names())
            self._update_actions()

    def _selected_names(self):
        return [self.tools.topLevelItem(i).data(0, Qt.UserRole) for i in range(self.tools.topLevelItemCount())
                if self.tools.topLevelItem(i).checkState(0) == Qt.Checked]

    def _group_selection_changed(self, names):
        self._filling = True
        try:
            selected = set(names)
            for i in range(self.tools.topLevelItemCount()):
                item = self.tools.topLevelItem(i)
                item.setCheckState(0, Qt.Checked if item.data(0, Qt.UserRole) in selected else Qt.Unchecked)
        finally:
            self._filling = False
        self._selection_changed()

    def _toggle_advanced(self, expanded):
        self.advanced_body.setVisible(expanded)
        self.advanced_toggle.setText("Réglages avancés ▾" if expanded else "Réglages avancés ▸")

    def select_tools(self, *, reads_only):
        if self._review:
            kinds = {t["name"]: t["group"] for t in self._review["tools"]}
            self._group_selection_changed([name for name, group in kinds.items() if not reads_only or group == "read"])

    def _update_actions(self, *, stale=None):
        state = self._state
        if stale is None:
            stale = bool(self._dirty and state and self._state_signature(state) != self._signature)
        connected = bool(state and state["bound_server"] == state["server"] and
            state["connection_state"] != "authorization_required" and
            state["state"] not in {"not_configured", "disabled", "discovery_required", "no_gmail_tools"})
        permitted = bool(connected and any(t["allowed"] for t in state["tools"]))
        full_access = bool(connected and all(t["allowed"] for t in state["tools"]))
        can_grant = bool(connected and state["tools"] and not stale and not self._busy)
        self.connect_button.setVisible(not connected or not full_access)
        self.access_note.setVisible(not connected or not full_access)
        self.connect_button.setEnabled(not self._busy and bool(state))
        self.cancel_button.setVisible(self._busy)
        self.cancel_button.setEnabled(self._busy)
        self.functions_section.setVisible(connected)
        self.grant_button.setVisible(connected and (self._dirty or not permitted))
        self.grant_button.setEnabled(can_grant)
        self.reload_button.setVisible(bool(stale))
        activate = bool(permitted and not state["runtime_enabled"] and not self._dirty)
        self.runtime_button.setVisible(activate)
        self.runtime_note.setVisible(activate)
        ready = bool(permitted and state["runtime_enabled"] and not self._dirty)
        if full_access and ready:
            self.status.setText("Gmail connecté")
        if self.provider.currentData() == "pipedream":
            self.provider_note.setText("Connexion gérée par Pipedream ; tes données transitent par ce fournisseur." +
                (" Sélectionne Gmail dans le navigateur." if not connected else ""))
        self.test_button.setVisible(ready)
        self.test_button.setEnabled(ready and not self._busy)
        self.step.setText(STAGES.get(self._connection_stage, STAGES["preparing"]) + "…" if self._busy else
            "Connecte Gmail pour utiliser ses fonctions avec Jarvis." if not connected or not permitted else
            "Modifications avancées à enregistrer." if self._dirty else
            "Les connexions sont en pause. Tu peux les réactiver." if activate else
            "Gmail est prêt. Demande directement ce dont tu as besoin à Jarvis.")

    @staticmethod
    def _state_signature(state):
        return json.dumps({k: state[k] for k in ("server", "binding_revision", "connection", "inventory")}, sort_keys=True)

    def start_connection(self, checked=False, *, access="plugin"):
        try:
            sid = self.server_id()
            if not sid:
                self.feedback.setText("Choisis un serveur existant ou la connexion gérée.")
                return
            revision = (self.workspace.bindings().get("gmail") or {}).get("revision", 0)
            self._pending_server = sid
            self.set_busy(True)
            self.feedback.setText("Connexion demandée. Le navigateur va s’ouvrir : utilise ton compte Pipedream MCP, sélectionne Gmail et termine le consentement, puis reviens dans Jarvis.")
            self.requested.emit("gmail_connect", sid, json.dumps({"provider": self.provider.currentData(), "binding_revision": revision, "access": access}))
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
        selected = self._selected_names()
        packet = {k: self._review[k] for k in ("binding_revision", "connection", "inventory")}
        packet.update(selected=selected, automatic_reads=self.auto_reads.isChecked())
        self.requested.emit("gmail_grant", self._review["server"], json.dumps(packet))

    def reload(self):
        self._dirty, self._review, self._signature = False, None, ""
        self.refresh()

    def test_mailbox(self):
        self.request_ready.emit("Vérifie quel compte Gmail est réellement connecté avec les outils disponibles, puis indique le nombre de messages non lus si cette lecture est possible. Ne modifie rien dans ma boîte.")

    def set_busy(self, busy):
        if busy and not self._busy:
            self._connection_stage = "preparing"
        self._busy = bool(busy)
        self.cancel_button.setEnabled(self._busy)
        for widget in (self.provider, self.server_picker, self.connect_button, self.refresh_button, self.tools,
            self.reads_button, self.all_button, self.auto_reads, self.grant_button, self.reload_button, self.runtime_button,
            self.test_button, self.advanced_button, self.functions, self.account_button, self.advanced_toggle):
            widget.setEnabled(not self._busy)
        self._update_actions()
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
            if result.get("success"):
                message = (
                    "MCP activé dans Jarvis." if operation == "runtime_set" and self.registry.runtime_enabled() else
                    "MCP désactivé dans Jarvis." if operation == "runtime_set" else
                    "Inventaire actualisé. Vérifie les capacités avant de les enregistrer." if operation == "reconnect" else
                    result.get("message") or "Configuration enregistrée.")
                self.feedback.setText(str(message))
            else:
                message = ERRORS.get(result.get("reason"),
                    "La demande n’a pas abouti. Vérifie les réglages et recharge les capacités.")
                details = result.get("diagnostic") or {}
                if details.get("stage") in STAGES:
                    message += "\nÉtape : " + STAGES[details["stage"]] + "."
                status = details.get("http_status")
                if type(status) is int and 100 <= status <= 599:
                    message += " HTTP " + str(status) + "."
                from .mcp_oauth import OAUTH_ERRORS
                if details.get("oauth_error") in OAUTH_ERRORS:
                    message += " Code : " + details["oauth_error"] + "."
                self.feedback.setText(message)
        self.refresh()

    def show_progress(self, progress):
        stage = progress.get("stage")
        if self._busy and stage in STAGES:
            self._connection_stage = stage
            self.step.setText(STAGES[stage] + "…")

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
            self._state = state
            if state is None:
                self.status.setText("Choisis un serveur MCP existant.")
                self._update_actions()
                return
            self.status.setText(STATES[state["state"]] +
                (" · autorisation à renouveler" if state["connection_state"] == "authorization_required" else ""))
            self.capabilities.setText("\n".join(label + " : " + str(state["groups"][group]["allowed"]) + " autorisé(s) / " +
                str(state["groups"][group]["discovered"]) + " découvert(s)" for group, label in GROUPS.items()) +
                ("\nInventaire partiel : des capacités peuvent manquer." if state["inventory_partial"] else ""))
            self.available_functions.setText("\n".join("• " + self.functions.groups[group]
                for group in GROUPS if state["groups"][group]["discovered"]))
            signature = self._state_signature(state)
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
                self.functions.load(state["tools"], self._selected_names())
            if stale:
                self.feedback.setText("Les capacités ou accès ont changé. Ta sélection est conservée ; recharge avant d’enregistrer.")
            self._update_actions(stale=stale)
            self.refresh_button.setEnabled(self.registry.get_server(sid) is not None)
            self.reads_button.setEnabled(bool(state["tools"]))
            self.all_button.setEnabled(bool(state["tools"]))
        except (ValueError, TypeError, KeyError, OSError, sqlite3.Error):
            self.status.setText("Configuration Gmail temporairement indisponible.")
            self.grant_button.setEnabled(False)
        finally:
            self._filling = False
