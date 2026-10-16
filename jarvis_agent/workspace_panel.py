"""Daily organisation UI: real local records and explicit service readiness.

Local edits use the shared transactional store. Delegation only emits a typed
chat request; no model, MCP, browser or credential access occurs on this thread.
"""
from __future__ import annotations

import html
import json
import sqlite3
import uuid

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QGridLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QSizePolicy, QTabWidget, QTextBrowser, QTextEdit, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .mcp_personal_workspace import CONNECTIONS
from .mcp_tool_registry import _alias
from .personal_workspace import (
    ACTIVITY_DEFAULTS, DOMAINS, KINDS, PRIORITIES, STATUSES,
    PersonalWorkspaceStore, service_snapshot,
)


SERVICE_STATES = {"native": "Chemin natif Jarvis", "not_configured": "À connecter",
    "runtime_off": "MCP désactivé", "disabled": "Serveur désactivé",
    "discovery_required": "Outils à découvrir", "tools_available": "Outils exposés · accès à vérifier",
    "no_allowed_tools": "Outils à autoriser"}
_ERRORS = {"workspace_changed_reload_required": "Cet élément a changé ailleurs. Recharge-le avant d’enregistrer.",
    "invalid_workspace_timezone": "Choisis un fuseau valide, par exemple Africa/Tunis ou Africa/Abidjan.",
    "invalid_workspace_due_date": "La date doit être au format AAAA-MM-JJ.",
    "invalid_workspace_due_time": "L’heure doit être au format HH:mm, avec une date.",
    "invalid_workspace_server_id": "L’identifiant du serveur utilise lettres, chiffres, tirets ou soulignements."}


def _escape(value):
    return html.escape(str(value), quote=True)


class PersonalWorkspacePanel(QFrame):
    request_ready = Signal(str)
    connections_requested = Signal()

    def __init__(self, parent=None, *, store=None):
        super().__init__(parent)
        self.setObjectName("personalWorkspacePanel")
        self.store = store or PersonalWorkspaceStore()
        self._runtime = {}
        self._activity_id = ""
        self._activity_revision = 0
        self._activity_dirty = False
        self._profile_dirty = False
        self._profile_revision = 0
        self._binding_revision = 0
        self._filling = False
        self._refreshing = False
        self._tree_signature = None
        self._preparations_signature = None
        self._overview_signature = None
        self._prepared = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        title = QLabel("MON QUOTIDIEN · VIE ET ACTIVITÉS")
        title.setWordWrap(True)
        layout.addWidget(title)
        self.tabs = QTabWidget()
        self.tabs.setUsesScrollButtons(True)
        layout.addWidget(self.tabs, 1)
        self.status_line = QLabel()
        self.status_line.setWordWrap(True)
        self.status_line.setTextFormat(Qt.PlainText)
        layout.addWidget(self.status_line)
        self._build_overview()
        self._build_activities()
        self._build_profile()
        self._build_preparations()
        self._build_services()
        self.setStyleSheet("""
            QFrame#personalWorkspacePanel,QWidget#workspacePage {background:#061725;color:#d6e9ee;border:0;}
            QLabel,QCheckBox {color:#c4dde7;font-size:11px;}
            QLineEdit,QTextEdit,QTextBrowser,QComboBox,QTreeWidget {background:#081f30;color:#d6e9ee;
                border:1px solid #264b60;border-radius:4px;font-size:11px;padding:3px;}
            QHeaderView::section {background:#103246;color:#b9dce5;padding:5px;border:0;}
            QTabWidget::pane {border:0;} QTabBar::tab {background:#0b293d;color:#b4d2e0;padding:6px;}
            QTabBar::tab:selected {background:#17495b;color:#abf1e5;}
            QPushButton {background:#10394b;color:#afe9e1;border:1px solid #286278;border-radius:4px;padding:6px;font-size:11px;}
            QPushButton:disabled {color:#738b9a;}
            QScrollArea {border:0;background:#061725;}
        """)
        self.reload_profile()
        self.new_activity()
        self.refresh()
        self.tabs.setCurrentWidget(self.overview_page)

    def _page(self, title):
        page = QWidget()
        page.setObjectName("workspacePage")
        page.setMinimumWidth(0)
        page.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(5, 5, 5, 5)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(page)
        self.tabs.addTab(scroll, title)
        return scroll, layout

    @staticmethod
    def _combo(options, *, empty=None):
        box = QComboBox()
        box.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        box.setMinimumContentsLength(8)
        box.setMinimumWidth(0)
        if empty is not None:
            box.addItem(empty, "")
        for key, label in options.items():
            box.addItem(label, key)
        return box

    @staticmethod
    def _caption(layout, caption, field):
        label = QLabel(caption)
        label.setWordWrap(True)
        layout.addWidget(label)
        layout.addWidget(field)

    def _error(self, exc):
        reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        if reason.startswith("invalid_workspace_field:"):
            field = reason.split(":", 1)[1]
            labels = {"title": "titre", "subject": "personne ou projet", "details": "informations",
                "operation": "action souhaitée", "instructions": "contenu de la préparation",
                "about": "activités et projets", "objectives": "priorités", "preferences": "préférences"}
            message = "Vérifie le champ « " + labels.get(field, "valeur saisie") + " » : une valeur manque ou dépasse la limite."
        else:
            message = _ERRORS.get(reason, "L’espace personnel n’a pas pu enregistrer ou relire les données. Ta saisie est conservée.")
        self.status_line.setText(message)

    def _build_overview(self):
        self.overview_page, layout = self._page("Aujourd’hui")
        note = QLabel("Tes tâches, rendez-vous et suivis locaux. Les agendas et messages externes seront consultés par les services connectés.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.overview = QTextBrowser()
        self.overview.setOpenLinks(False)
        self.overview.setOpenExternalLinks(False)
        self.overview.setMinimumHeight(290)
        self.overview.anchorClicked.connect(lambda url: self.open_activity(url.path()) if url.scheme() == "activity" else None)
        layout.addWidget(self.overview, 1)
        buttons = QGridLayout()
        organise = QPushButton("Organiser ma journée")
        organise.clicked.connect(lambda: self.request_ready.emit(
            "Consulte mon profil et mon tableau de bord local, puis aide-moi à organiser ma journée. "
            "Distingue les activités locales des agendas externes. Ne crée ni ne modifie d’activité sans ma demande."))
        add = QPushButton("Ajouter une activité")
        add.clicked.connect(self.new_activity)
        refresh = QPushButton("Actualiser")
        refresh.clicked.connect(lambda: self.refresh())
        buttons.addWidget(organise, 0, 0, 1, 2)
        buttons.addWidget(add, 1, 0)
        buttons.addWidget(refresh, 1, 1)
        layout.addLayout(buttons)

    def _build_activities(self):
        self.activities_page, layout = self._page("Activités")
        self.domain_filter = self._combo(DOMAINS, empty="Tous les domaines")
        self.status_filter = self._combo(STATUSES, empty="Tous les états")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Titre, joueur, client ou projet…")
        for field in (self.domain_filter, self.status_filter, self.search):
            layout.addWidget(field)
        self.activity_tree = QTreeWidget()
        self.activity_tree.setHeaderLabels(["Activité", "Échéance", "État"])
        self.activity_tree.setRootIsDecorated(False)
        self.activity_tree.setMinimumHeight(160)
        self.activity_tree.setColumnWidth(0, 180)
        self.activity_tree.currentItemChanged.connect(self._select_activity)
        layout.addWidget(self.activity_tree)
        self.activity_fields = {key: QLineEdit() for key in ("title", "subject", "due_on", "at_time")}
        self.activity_fields["title"].setMaxLength(200)
        self.activity_fields["subject"].setMaxLength(200)
        self.activity_fields["due_on"].setPlaceholderText("2026-10-20 · facultatif")
        self.activity_fields["due_on"].setMaxLength(10)
        self.activity_fields["at_time"].setPlaceholderText("18:30 · heure du profil")
        self.activity_fields["at_time"].setMaxLength(5)
        self.activity_fields.update(domain=self._combo(DOMAINS), kind=self._combo(KINDS),
                                    status=self._combo(STATUSES), priority=self._combo(PRIORITIES),
                                    service=self._combo({e.identifier: e.label for e in CONNECTIONS}, empty="Aucun service externe"))
        self.activity_details = QTextEdit()
        self.activity_details.setAcceptRichText(False)
        self.activity_details.setMinimumHeight(100)
        self.activity_fields["details"] = self.activity_details
        for key, caption in (("title", "Activité"), ("domain", "Domaine"), ("kind", "Type"),
            ("subject", "Joueur, client, personne ou projet"), ("status", "État local"), ("priority", "Priorité"),
            ("due_on", "Date · AAAA-MM-JJ"), ("at_time", "Heure locale · HH:mm"),
            ("service", "Service associé"), ("details", "Informations et contraintes")):
            field = self.activity_fields[key]
            self._caption(layout, caption, field)
            signal = field.currentIndexChanged if isinstance(field, QComboBox) else field.textChanged
            signal.connect(self._activity_changed)
        note = QLabel("Le statut est un suivi local. Il ne confirme pas une livraison, un message envoyé ou une modification dans un autre service.")
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QGridLayout()
        self.save_activity_button = QPushButton("Enregistrer l’activité")
        self.save_activity_button.clicked.connect(self.save_activity)
        new = QPushButton("Nouvelle")
        new.clicked.connect(self.new_activity)
        reload_button = QPushButton("Recharger la sélection")
        reload_button.clicked.connect(self.reload_activity)
        self.delegate_activity_button = QPushButton("Confier à Jarvis")
        self.delegate_activity_button.clicked.connect(self.delegate_activity)
        buttons.addWidget(self.save_activity_button, 0, 0, 1, 2)
        buttons.addWidget(new, 1, 0)
        buttons.addWidget(reload_button, 1, 1)
        buttons.addWidget(self.delegate_activity_button, 2, 0, 1, 2)
        layout.addLayout(buttons)
        layout.addStretch()
        self.domain_filter.currentIndexChanged.connect(lambda: self.refresh())
        self.status_filter.currentIndexChanged.connect(lambda: self.refresh())
        self.search.textChanged.connect(lambda: self.refresh())

    def _activity_changed(self, *_):
        if not self._filling:
            self._activity_dirty = True

    def _select_activity(self, current, previous=None):
        if self._refreshing or current is None:
            return
        identity = current.data(0, Qt.UserRole)
        if self._activity_dirty and identity != self._activity_id:
            self.status_line.setText("Enregistre ou recharge la saisie en cours avant de changer d’activité.")
            self._refreshing = True
            self.activity_tree.setCurrentItem(previous)
            self._refreshing = False
            return
        self.open_activity(identity)

    def new_activity(self):
        if self._activity_dirty:
            self.status_line.setText("Enregistre ou recharge ta saisie avant de créer une autre activité.")
            return
        self._fill_activity(None)
        self._refreshing = True
        self.activity_tree.setCurrentItem(None)
        self.activity_tree.clearSelection()
        self._refreshing = False
        if hasattr(self, "tabs"):
            self.tabs.setCurrentWidget(self.activities_page)

    def reload_activity(self):
        self._activity_dirty = False
        self.open_activity(self._activity_id) if self._activity_id else self.new_activity()

    def _fill_activity(self, row):
        self._filling = True
        data = row["data"] if row else ACTIVITY_DEFAULTS
        self._activity_id = row["id"] if row else ""
        self._activity_revision = row["revision"] if row else 0
        for key, field in self.activity_fields.items():
            value = data[key]
            if isinstance(field, QComboBox):
                field.setCurrentIndex(field.findData(value))
            elif isinstance(field, QTextEdit):
                field.setPlainText(value)
            else:
                field.setText(value)
        self._activity_dirty = False
        self._filling = False
        self.delegate_activity_button.setEnabled(bool(row))

    def open_activity(self, identity):
        if self._activity_dirty:
            self.status_line.setText("Enregistre ou recharge ta saisie avant d’ouvrir une autre activité.")
            return
        try:
            row = self.store.activity(identity)
            if row is None:
                raise ValueError("workspace_record_not_found")
            self._fill_activity(row)
            self.tabs.setCurrentWidget(self.activities_page)
        except (OSError, ValueError, sqlite3.Error) as exc:
            self._error(exc)

    def save_activity(self):
        try:
            data = {key: (field.currentData() if isinstance(field, QComboBox) else
                          field.toPlainText() if isinstance(field, QTextEdit) else field.text())
                    for key, field in self.activity_fields.items()}
            row = self.store.save_activity(data, identity=self._activity_id,
                expected_revision=self._activity_revision, request_key=uuid.uuid4().hex)
            self._fill_activity(row)
            self.refresh()
            self.status_line.setText("Activité enregistrée localement.")
        except (OSError, ValueError, sqlite3.Error) as exc:
            self._error(exc)

    def delegate_activity(self):
        if self._activity_dirty:
            self.status_line.setText("Enregistre tes modifications avant de confier l’activité à Jarvis.")
            return
        if self._activity_id:
            self.request_ready.emit("Consulte l’activité locale " + self._activity_id
                + " avec workspace_read, puis aide-moi à la traiter en respectant ses informations et contraintes. "
                "Consulte les vrais outils disponibles pour les services externes ; le statut local ne prouve aucun résultat externe.")

    def _build_profile(self):
        self.profile_page, layout = self._page("Mon profil")
        note = QLabel("Ce que tu choisis de confier à Jarvis. Aucun profil ChatGPT ni compte externe n’est importé automatiquement.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.profile_fields = {key: QLineEdit() for key in ("display_name", "timezone", "roles")}
        self.profile_fields["display_name"].setMaxLength(100)
        self.profile_fields["timezone"].setPlaceholderText("Africa/Tunis ou Africa/Abidjan")
        self.profile_fields["roles"].setPlaceholderText("Footballeur ; développeur ; responsable d’activité")
        self.profile_fields["roles"].setMaxLength(1200)
        for key in ("about", "objectives", "preferences"):
            field = QTextEdit()
            field.setAcceptRichText(False)
            field.setMinimumHeight(90)
            self.profile_fields[key] = field
        for key, caption in (("display_name", "Comment Jarvis doit t’appeler"), ("timezone", "Fuseau horaire"),
            ("roles", "Rôles · séparés par des points-virgules"), ("about", "Tes activités et projets"),
            ("objectives", "Tes priorités"), ("preferences", "Tes préférences")):
            field = self.profile_fields[key]
            self._caption(layout, caption, field)
            field.textChanged.connect(self._profile_changed)
        self.use_profile = QCheckBox("Inclure automatiquement mon profil dans les échanges")
        self.use_profile.toggled.connect(self._profile_changed)
        layout.addWidget(self.use_profile)
        consent = QLabel("Quand cette option est enregistrée, un extrait de ton profil rejoint les requêtes envoyées aux cerveaux configurés. "
                         "Une demande explicite de lecture du profil permet aussi à Jarvis de le consulter. "
                         "N’y saisis pas de clés API ou mots de passe.")
        consent.setWordWrap(True)
        layout.addWidget(consent)
        self.save_profile_button = QPushButton("Enregistrer mon profil")
        self.save_profile_button.clicked.connect(self.save_profile)
        reload_button = QPushButton("Recharger le profil enregistré")
        reload_button.clicked.connect(self.reload_profile)
        layout.addWidget(self.save_profile_button)
        layout.addWidget(reload_button)
        layout.addStretch()

    def _profile_changed(self, *_):
        if not self._filling:
            self._profile_dirty = True

    def reload_profile(self):
        try:
            row = self.store.profile()
            self._filling = True
            self._profile_revision = row["revision"]
            for key, field in self.profile_fields.items():
                value = "; ".join(row["data"][key]) if key == "roles" else row["data"][key]
                field.setPlainText(value) if isinstance(field, QTextEdit) else field.setText(value)
            self.use_profile.setChecked(row["data"]["use_for_agent"])
            self._profile_dirty = False
            self._filling = False
        except (OSError, ValueError, sqlite3.Error) as exc:
            self._filling = False
            self._error(exc)

    def save_profile(self):
        try:
            data = {key: (field.toPlainText() if isinstance(field, QTextEdit) else field.text())
                    for key, field in self.profile_fields.items()}
            data["roles"] = [r.strip() for r in data["roles"].split(";") if r.strip()]
            data["use_for_agent"] = self.use_profile.isChecked()
            self.store.save_profile(data, expected_revision=self._profile_revision)
            self.reload_profile()
            self.refresh()
            self.status_line.setText("Profil enregistré. Les prochains échanges utilisent le choix de personnalisation enregistré.")
        except (OSError, ValueError, sqlite3.Error) as exc:
            self._error(exc)

    def _build_preparations(self):
        self.preparations_page, layout = self._page("Préparations")
        note = QLabel("Préparer un message, un rendez-vous ou une action métier, puis le confier à Jarvis quand le service sera disponible.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.preparation_tree = QTreeWidget()
        self.preparation_tree.setHeaderLabels(["Préparation locale", "Service"])
        self.preparation_tree.setRootIsDecorated(False)
        self.preparation_tree.setMinimumHeight(150)
        self.preparation_tree.currentItemChanged.connect(self._show_preparation)
        layout.addWidget(self.preparation_tree)
        self.preparation_detail = QTextBrowser()
        self.preparation_detail.setOpenExternalLinks(False)
        self.preparation_detail.setMinimumHeight(140)
        layout.addWidget(self.preparation_detail)
        self.preparation_service = self._combo({e.identifier: e.label for e in CONNECTIONS})
        self.preparation_operation = QLineEdit()
        self.preparation_operation.setMaxLength(120)
        self.preparation_operation.setPlaceholderText("Préparer un rendez-vous, relancer un client…")
        self.preparation_instructions = QTextEdit()
        self.preparation_instructions.setAcceptRichText(False)
        self.preparation_instructions.setMinimumHeight(100)
        for caption, field in (("Service", self.preparation_service), ("Action souhaitée", self.preparation_operation),
                               ("Contenu, destinataire, dates et contraintes", self.preparation_instructions)):
            self._caption(layout, caption, field)
        save = QPushButton("Enregistrer la préparation locale")
        save.clicked.connect(self.save_preparation)
        self.delegate_preparation_button = QPushButton("Confier la préparation sélectionnée à Jarvis")
        self.delegate_preparation_button.setEnabled(False)
        self.delegate_preparation_button.clicked.connect(self.delegate_preparation)
        layout.addWidget(save)
        layout.addWidget(self.delegate_preparation_button)
        layout.addStretch()

    def save_preparation(self):
        try:
            row = self.store.prepare_action(service=self.preparation_service.currentData(),
                operation=self.preparation_operation.text(), instructions=self.preparation_instructions.toPlainText(),
                request_key=uuid.uuid4().hex)
            self.preparation_operation.clear()
            self.preparation_instructions.clear()
            self.refresh()
            for index in range(self.preparation_tree.topLevelItemCount()):
                item = self.preparation_tree.topLevelItem(index)
                if item.data(0, Qt.UserRole) == row["id"]:
                    self.preparation_tree.setCurrentItem(item)
            self.status_line.setText("Préparation enregistrée localement. Rien n’a été envoyé au service.")
        except (OSError, ValueError, sqlite3.Error) as exc:
            self._error(exc)

    def _show_preparation(self, current, previous=None):
        self.delegate_preparation_button.setEnabled(current is not None)
        if current is None:
            self.preparation_detail.clear()
            self._preparation_html = None
            return
        row = next((r for r in self._prepared if r["id"] == current.data(0, Qt.UserRole)), None)
        if row:
            data = row["data"]
            service = next((s for s in self._services if s["id"] == data["service"]), {})
            content = ("<b>" + _escape(data["operation"]) + "</b><p>"
                + _escape(data["instructions"]).replace("\n", "<br>") + "</p><p>État : préparation locale, "
                "aucune action externe exécutée.</p><p>Service : "
                + _escape(SERVICE_STATES.get(service.get("state"), "À vérifier")) + "</p>")
            if content != getattr(self, "_preparation_html", None):
                self.preparation_detail.setHtml(content)
                self._preparation_html = content

    def delegate_preparation(self):
        current = self.preparation_tree.currentItem()
        if current:
            identity = current.data(0, Qt.UserRole)
            self.request_ready.emit("Consulte la préparation locale " + identity
                + " avec workspace_read. Aide-moi à réaliser l’action décrite, avec les vrais outils disponibles. "
                  "Vérifie les paramètres, destinataires et autorisations, puis le résultat. "
                  "Si la connexion ou un outil manque, conserve la préparation et explique le blocage. "
                  "Cette préparation ne prouve aucune exécution précédente.")

    def _build_services(self):
        self.services_page, layout = self._page("Services")
        self.services_view = QTextBrowser()
        self.services_view.setOpenExternalLinks(False)
        self.services_view.setMinimumHeight(240)
        layout.addWidget(self.services_view)
        self.binding_service = self._combo({e.identifier: e.label for e in CONNECTIONS if e.integration != "native"})
        self.binding_server = QComboBox()
        self.binding_server.setEditable(True)
        self.binding_server.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.binding_server.setMinimumContentsLength(8)
        self._caption(layout, "Service à associer", self.binding_service)
        self._caption(layout, "Identifiant du serveur MCP · existant ou à configurer ensuite", self.binding_server)
        self.binding_service.currentIndexChanged.connect(self._load_binding)
        save = QPushButton("Enregistrer l’association")
        save.clicked.connect(self.save_binding)
        connect = QPushButton("Ouvrir le centre de connexions")
        connect.clicked.connect(self.connections_requested.emit)
        layout.addWidget(save)
        layout.addWidget(connect)
        note = QLabel("Une association permet de retrouver le bon serveur, même si plusieurs services partagent un MCP. "
                      "Les outils doivent ensuite être découverts et autorisés dans le centre de connexions.")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch()
        self._load_binding()

    def _load_binding(self, *_):
        try:
            service = self.binding_service.currentData()
            row = self.store.bindings().get(service)
            default = next(e.catalog_id for e in CONNECTIONS if e.identifier == service)
            self._binding_revision = row["revision"] if row else 0
            self.binding_server.setEditText(row["server"] if row else default)
        except (OSError, ValueError, sqlite3.Error) as exc:
            self._error(exc)

    def save_binding(self):
        try:
            self.store.bind_service(self.binding_service.currentData(), self.binding_server.currentText().strip(),
                                    expected_revision=self._binding_revision)
            self._load_binding()
            self.refresh()
            self.status_line.setText("Association enregistrée. Les droits MCP restent gérés dans le centre de connexions.")
        except (OSError, ValueError, sqlite3.Error) as exc:
            self._error(exc)

    def refresh(self, runtime=None):
        if runtime is not None:
            self._runtime = runtime
        try:
            mcp = self._runtime.get("mcp") or {}
            servers = mcp.get("servers") or []
            available = {_alias(s["id"], t["name"]): {"server": s["id"]} for s in servers
                if s.get("enabled") is True for t in s.get("tools", []) if t.get("allowed") is True}
            self._services = service_snapshot(self.store, servers, runtime_enabled=mcp.get("enabled") is True, available=available)
            service_html = "<h3>Connexions prévues et état observé</h3>" + "".join(
                "<p><b>" + _escape(s["name"]) + "</b><br>" + _escape(SERVICE_STATES[s["state"]])
                + (" · " + str(s["tool_count"]) + " outils observés" if s["tool_count"] else "") + "</p>" for s in self._services)
            if service_html != getattr(self, "_service_html", None):
                position = self.services_view.verticalScrollBar().value()
                self.services_view.setHtml(service_html)
                self.services_view.verticalScrollBar().setValue(position)
                self._service_html = service_html
            current_server = self.binding_server.currentText()
            server_ids = [s.get("id", "") for s in servers]
            if server_ids != getattr(self, "_server_choices", None):
                self.binding_server.blockSignals(True)
                self.binding_server.clear()
                self.binding_server.addItems(["", *server_ids])
                self.binding_server.setEditText(current_server)
                self.binding_server.blockSignals(False)
                self._server_choices = server_ids
            brief = self.store.brief()
            signature = json.dumps({"date": brief["date"], "timezone": brief["timezone"],
                "minute": brief["now"][:16], "groups": brief["groups"], "partial": brief["inventory_partial"]}, sort_keys=True)
            if signature != self._overview_signature:
                blocks = ["<h3>" + _escape(brief["date"]) + " · " + _escape(brief["timezone"]) + "</h3>"]
                labels = {"overdue": "À reprendre", "today": "Aujourd’hui", "upcoming": "Sept prochains jours",
                          "waiting": "En attente", "unscheduled": "Sans date"}
                for key, label in labels.items():
                    blocks.append("<h4>" + label + " · " + str(brief["counts"][key]) + "</h4>")
                    for row in brief["groups"][key]:
                        data = row["data"]
                        blocks.append('<p><a style="color:#72ded2" href="activity:' + row["id"] + '">' + _escape(data["title"]) + "</a><br>"
                            + _escape(DOMAINS[data["domain"]]) + " · " + _escape((data["due_on"] + " " + data["at_time"]).strip())
                            + " · " + _escape(STATUSES[data["status"]]) + "</p>")
                if not brief["active_total"]:
                    blocks.append("<p>Aucune activité locale enregistrée. Ajoute une tâche ou complète ton profil pour commencer.</p>")
                if brief["inventory_partial"]:
                    blocks.append("<p>Vue partielle des activités actives ; utilise les filtres pour retrouver les autres.</p>")
                blocks.append("<p>Vue locale uniquement. Les échéances sont visibles dans Jarvis ouvert ; "
                              "aucune notification en arrière-plan ou réservation externe n’est certifiée ici.</p>")
                position = self.overview.verticalScrollBar().value()
                self.overview.setHtml("".join(blocks))
                self.overview.verticalScrollBar().setValue(position)
                self._overview_signature = signature
            rows = self.store.activities(domain=self.domain_filter.currentData() or "", status=self.status_filter.currentData() or "",
                                          query=self.search.text(), limit=100)
            signature = json.dumps(rows, sort_keys=True)
            if signature != self._tree_signature:
                selected = self.activity_tree.currentItem()
                selected_id = selected.data(0, Qt.UserRole) if selected else self._activity_id
                self._refreshing = True
                self.activity_tree.clear()
                for row in rows:
                    data = row["data"]
                    item = QTreeWidgetItem([data["title"], (data["due_on"] + " " + data["at_time"]).strip(), STATUSES[data["status"]]])
                    item.setData(0, Qt.UserRole, row["id"])
                    item.setToolTip(0, DOMAINS[data["domain"]] + " · " + data["subject"])
                    self.activity_tree.addTopLevelItem(item)
                    if row["id"] == selected_id:
                        self.activity_tree.setCurrentItem(item)
                self._refreshing = False
                self._tree_signature = signature
            self._prepared = self.store.preparations()
            signature = json.dumps(self._prepared, sort_keys=True)
            if signature != self._preparations_signature:
                current = self.preparation_tree.currentItem()
                identity = current.data(0, Qt.UserRole) if current else ""
                self.preparation_tree.clear()
                for row in self._prepared:
                    item = QTreeWidgetItem([row["data"]["operation"], row["data"]["service"]])
                    item.setData(0, Qt.UserRole, row["id"])
                    self.preparation_tree.addTopLevelItem(item)
                    if row["id"] == identity:
                        self.preparation_tree.setCurrentItem(item)
                self._preparations_signature = signature
            self._show_preparation(self.preparation_tree.currentItem())
            if not self._profile_dirty and self.store.profile()["revision"] != self._profile_revision:
                self.reload_profile()
        except (OSError, ValueError, sqlite3.Error) as exc:
            self._refreshing = False
            self._error(exc)
