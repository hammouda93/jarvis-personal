"""Operator cockpit: teams, durable watch jobs, private reports and voice setup.

Forms edit local records. Requests are emitted to the ordinary worker queue.
No model, transport, desktop action or phone call runs on the GUI thread.
"""
from datetime import datetime, timedelta, timezone
import html
import json
import sqlite3

from PySide6.QtCore import QDate, QDateTime, QTime, Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDateTimeEdit, QFrame, QGridLayout,
    QLabel, QLineEdit, QPushButton, QScrollArea, QSizePolicy, QSpinBox, QTabWidget,
    QTextBrowser, QTextEdit, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .agent_profiles import AgentProfileStore
from .architecture_catalog import _LABELS
from .capability_registry import DEFAULT_CAPABILITY_REGISTRY
from .mcp_server_registry import MCPRegistry
from .mcp_tool_registry import MCPToolRegistry
from .native_tools import NATIVE_TOOLS
from .orchestra_store import OrchestraStore, WATCH_DEFAULTS, VOICE_DEFAULTS, JOB_FIELDS
from .orchestra_voice import VoiceChannel
from .personal_workspace import PersonalWorkspaceStore, _zone


LABELS = {"scheduled": "Programmé", "paused": "En pause", "running": "En cours / à examiner après interruption",
    "report_ready": "Rapport disponible", "needs_review": "À examiner", "cancelled": "Annulé", "failed": "Erreur", "stopped": "Suspendu"}
VOICE_LABELS = {"not_configured": "À connecter", "ready": "Outils prêts · appel réel à vérifier", "runtime_off": "MCP désactivé",
    "binding_changed": "Configuration modifiée · association à enregistrer à nouveau", "tools_not_allowed": "Outils à autoriser dans MCP",
    "inbox_read_consent_required": "Lecture du canal à préautoriser dans MCP"}


class OrchestraPanel(QFrame):
    request_ready = Signal(str)
    profile_requested = Signal(str)
    connections_requested = Signal()
    universe_requested = Signal(bool)

    def __init__(self, parent=None, *, store=None, profiles=None, workspace=None, voice=None):
        super().__init__(parent)
        self.setObjectName("orchestraPanel")
        self.store, self.profiles, self.workspace = store or OrchestraStore(), profiles or AgentProfileStore(), workspace or PersonalWorkspaceStore()
        self.voice = voice or VoiceChannel(MCPToolRegistry(NATIVE_TOOLS, registry=MCPRegistry()), store=self.store, workspace=self.workspace)
        self._filling = self._refreshing = False
        self._job_id, self._job_revision = "", 0
        self._job_dirty = self._watch_dirty = self._voice_dirty = False
        self._watch_revision = self._voice_revision = 0
        self._runtime, self._signatures = {}, {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        title = QLabel("PILOTAGE · JARVIS ET SON ÉQUIPE")
        title.setWordWrap(True)
        layout.addWidget(title)
        self.tabs = QTabWidget()
        self.tabs.setUsesScrollButtons(True)
        layout.addWidget(self.tabs, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)
        layout.addWidget(self.status)
        self._team_page()
        self._watch_page()
        self._reports_page()
        self._voice_page()
        self.setStyleSheet("""
            QFrame#orchestraPanel,QWidget#orchestraPage,QScrollArea {background:#071624;border:0;color:#d7e9f1;}
            QLabel,QCheckBox {color:#c5dce8;font-size:11px;}
            QLineEdit,QComboBox,QTextEdit,QTextBrowser,QTreeWidget,QSpinBox,QDateTimeEdit {
                background:#0a2133;color:#d7eaf2;border:1px solid #29475e;border-radius:4px;padding:4px;font-size:11px;}
            QHeaderView::section {background:#113049;color:#bbdce8;padding:5px;border:0;}
            QTabWidget::pane {border:0;} QTabBar::tab {background:#102b40;color:#abc7da;padding:6px;}
            QTabBar::tab:selected {background:#254158;color:#b6f1e8;}
            QPushButton {background:#15394e;color:#b8eae2;border:1px solid #38637c;border-radius:4px;padding:6px;font-size:11px;}
            QPushButton:disabled {color:#73889c;}
        """)
        self.new_job()
        self.reload_watch()
        self.reload_voice()
        self.refresh()
        self.focus_planet("interaction")

    def _page(self, title):
        body = QWidget()
        body.setObjectName("orchestraPage")
        body.setMinimumWidth(0)
        body.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(5, 5, 5, 5)
        page = QScrollArea()
        page.setWidgetResizable(True)
        page.setFrameShape(QFrame.NoFrame)
        page.setWidget(body)
        self.tabs.addTab(page, title)
        return page, layout

    @staticmethod
    def _note(layout, text):
        label = QLabel(text)
        label.setWordWrap(True)
        label.setTextFormat(Qt.PlainText)
        layout.addWidget(label)
        return label

    def _field(self, layout, label, field):
        self._note(layout, label)
        layout.addWidget(field)

    @staticmethod
    def _button(layout, text, callback):
        button = QPushButton(text)
        button.clicked.connect(callback)
        layout.addWidget(button)
        return button

    def _roles(self):
        box = QComboBox()
        box.setMinimumWidth(0)
        box.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        for role in DEFAULT_CAPABILITY_REGISTRY.agents():
            box.addItem(self.role_name(role.agent_id), role.agent_id)
        return box

    def role_name(self, role):
        profile = self.profiles.get(role)
        return profile["settings"]["name"] if profile["revision"] else _LABELS[role][0]

    def _dirty(self, area):
        if not self._filling:
            setattr(self, "_" + area + "_dirty", True)

    def _error(self, exc):
        reason = str(exc)
        messages = {"orchestra_changed_reload_required": "Cet élément a changé ailleurs. Recharge-le avant d’enregistrer.",
            "orchestra_job_running": "Ce travail est en cours. Arrête la veille avant d’examiner une interruption.",
            "orchestra_stop_engine_before_review": "Arrête la veille et attends la fin de l’appel en cours avant de classer cette interruption.",
            "orchestra_voice_schema_needs_adapter": "Les outils vocaux doivent respecter le contrat du canal. Choisis les bons champs ou un adaptateur compatible.",
            "orchestra_voice_contact_and_tools_required": "Choisis ton contact, le serveur et au moins un outil vocal découvert.",
            "orchestra_voice_tool_not_discovered": "L’outil choisi n’est plus dans la découverte MCP. Actualise la connexion.",
            "orchestra_invalid_daily_time": "L’heure quotidienne doit être au format HH:mm.",
            "orchestra_local_time_unavailable": "Cette heure n’existe pas dans ce fuseau. Choisis une autre heure."}
        messages.update(orchestra_disable_watch_and_voice_before_review="Désactive et enregistre la veille et le canal vocal avant de classer cet envoi.",
            orchestra_voice_dispatch_still_recent="Cet envoi est encore récent. Attends sa fin et vérifie le service ; son classement est bloqué dix minutes après réservation.")
        self.status.setText(messages.get(reason, "Vérifie la saisie et les connexions. Les champs en cours sont conservés."))

    def _team_page(self):
        self.team_page, layout = self._page("Équipe")
        self.universe_toggle = QCheckBox("Afficher l’univers spatial (sinon : flux classique)")
        self.universe_toggle.setChecked(True)
        self.universe_toggle.toggled.connect(self.universe_requested.emit)
        layout.addWidget(self.universe_toggle)
        self._note(layout, "Un seul interlocuteur : Jarvis. Les spécialistes analysent dans des conversations séparées, deux au maximum en parallèle. Jarvis garde le contrôle des actions externes.")
        self.role_tree = QTreeWidget()
        self.role_tree.setHeaderLabels(["Spécialiste", "Dernier travail"])
        self.role_tree.setRootIsDecorated(False)
        self.role_tree.setColumnWidth(0, 155)
        self.role_tree.setMinimumHeight(180)
        self.role_tree.currentItemChanged.connect(lambda *_: self.show_role())
        layout.addWidget(self.role_tree)
        self.role_detail = QTextBrowser()
        self.role_detail.setMinimumHeight(160)
        self.role_detail.setOpenExternalLinks(False)
        layout.addWidget(self.role_detail)
        self.team_request = QTextEdit()
        self.team_request.setAcceptRichText(False)
        self.team_request.setMinimumHeight(90)
        self._field(layout, "Ce que tu veux confier à cet agent", self.team_request)
        self.delegate_button = self._button(layout, "Confier l’analyse au spécialiste", self.delegate_selected)
        self._button(layout, "Configurer ses compétences et outils", lambda: self.profile_requested.emit(self.selected_role()))

    def selected_role(self):
        current = self.role_tree.currentItem()
        return current.data(0, Qt.UserRole) if current else "interaction"

    def show_role(self):
        role = self.selected_role()
        profile = self.profiles.get(role)
        settings = profile["settings"]
        selected = settings["tools"]
        tools = "Sélection selon le rôle et les droits actifs" if selected is None else ", ".join(selected) or "Aucun outil sélectionné"
        content = "<h3>" + html.escape(self.role_name(role)) + "</h3><p>" + html.escape(settings["purpose"]) + "</p>"
        content += "<b>Compétences</b><p>" + "<br>".join(html.escape(c) for c in settings["competencies"]) + "</p>"
        content += "<b>Outils du profil</b><p>" + html.escape(tools) + "</p>"
        content += "<p>Limites du profil : " + str(settings["model_calls"]) + " requêtes, " + str(settings["actions"]) + " outils, " + str(settings["seconds"]) + " s.</p>"
        content += "<p>Dans une session spécialisée : lectures autorisées et préparations locales. Les autres actions passent par Jarvis et ses confirmations.</p>"
        if content != self._signatures.get("role_detail"):
            self.role_detail.setHtml(content)
            self._signatures["role_detail"] = content

    def delegate_selected(self):
        text = self.team_request.toPlainText().strip()
        if not 1 <= len(text) <= 4000:
            self.status.setText("Saisis une demande de 1 à 4 000 caractères.")
            return
        self.request_ready.emit("Confie cette analyse à l’agent " + self.selected_role() + " dans sa session spécialisée :\n" + text)

    def focus_planet(self, role):
        if role == "automations":
            self.tabs.setCurrentWidget(self.watch_page)
        elif role == "voice_channel":
            self.tabs.setCurrentWidget(self.voice_page)
        else:
            self.tabs.setCurrentWidget(self.team_page)
            for i in range(self.role_tree.topLevelItemCount()):
                item = self.role_tree.topLevelItem(i)
                if item.data(0, Qt.UserRole) == role:
                    self.role_tree.setCurrentItem(item)
                    break

    def _watch_page(self):
        self.watch_page, layout = self._page("Veille")
        self.watch_status = self._note(layout, "Veille désactivée")
        self._note(layout, "Jarvis peut rechercher et préparer pendant ton sommeil, avec l’application ouverte et le PC réveillé. Les envois et modifications externes restent à valider. Les rapports restent silencieux.")
        self.watch_enabled = QCheckBox("Autoriser les travaux programmés arrivés à échéance")
        self.watch_enabled.toggled.connect(lambda: self._dirty("watch"))
        layout.addWidget(self.watch_enabled)
        self.watch_fields = {}
        for key, label, low, high in (("max_runs_day", "Travaux maximum sur 24 h", 1, 24), ("model_calls", "Requêtes de cerveau par travail", 1, 24),
                                      ("tool_calls", "Outils par travail", 1, 48), ("seconds", "Durée avant arrêt à la prochaine étape · secondes", 15, 600)):
            spin = QSpinBox()
            spin.setRange(low, high)
            spin.valueChanged.connect(lambda: self._dirty("watch"))
            self.watch_fields[key] = spin
            self._field(layout, label, spin)
        self._button(layout, "Enregistrer le mode veille", self.save_watch)
        self._button(layout, "Recharger les limites", self.reload_watch)
        self.job_tree = QTreeWidget()
        self.job_tree.setHeaderLabels(["Travail", "État"])
        self.job_tree.setRootIsDecorated(False)
        self.job_tree.setMinimumHeight(150)
        self.job_tree.currentItemChanged.connect(self._select_job)
        layout.addWidget(self.job_tree)
        self.job_title, self.job_request, self.job_role = QLineEdit(), QTextEdit(), self._roles()
        self.job_title.setMaxLength(150)
        self.job_request.setAcceptRichText(False)
        self.job_request.setMinimumHeight(100)
        self.job_due = QDateTimeEdit()
        self.job_due.setCalendarPopup(True)
        self.job_due.setDisplayFormat("dd/MM/yyyy HH:mm")
        self.job_timezone, self.job_daily = QLineEdit(), QLineEdit()
        self.job_daily.setMaxLength(5)
        self.job_daily.setPlaceholderText("HH:mm · vide pour une seule fois")
        self.job_enabled = QCheckBox("Travail activé")
        self.job_notify = QCheckBox("Me prévenir par appel si mon canal l’autorise")
        for label, field in (("Travail à préparer", self.job_title), ("Consigne et contraintes", self.job_request), ("Agent responsable", self.job_role),
            ("Première exécution · heure du fuseau ci-dessous", self.job_due), ("Fuseau horaire", self.job_timezone), ("Répétition quotidienne · facultatif", self.job_daily)):
            self._field(layout, label, field)
        layout.addWidget(self.job_enabled)
        layout.addWidget(self.job_notify)
        for field in (self.job_title, self.job_request, self.job_timezone, self.job_daily):
            field.textChanged.connect(lambda: self._dirty("job"))
        self.job_role.currentIndexChanged.connect(lambda: self._dirty("job"))
        self.job_due.dateTimeChanged.connect(lambda: self._dirty("job"))
        self.job_enabled.toggled.connect(lambda: self._dirty("job"))
        self.job_notify.toggled.connect(lambda: self._dirty("job"))
        self.save_job_button = self._button(layout, "Enregistrer le travail", self.save_job)
        self._button(layout, "Nouveau travail", self.new_job)
        self._button(layout, "Recharger la sélection", self.reload_job)
        self._button(layout, "Annuler ce travail", self.cancel_job)
        self._button(layout, "Classer une exécution interrompue sans la rejouer", self.review_job)

    def reload_watch(self):
        try:
            row = self.store.config("watch", WATCH_DEFAULTS)
            self._filling = True
            self.watch_enabled.setChecked(row["data"]["enabled"])
            for key, field in self.watch_fields.items():
                field.setValue(row["data"][key])
            self._watch_revision, self._watch_dirty = row["revision"], False
        except (ValueError, OSError, sqlite3.Error) as exc:
            self._error(exc)
        finally:
            self._filling = False

    def save_watch(self):
        try:
            data = {"enabled": self.watch_enabled.isChecked(), **{k: f.value() for k, f in self.watch_fields.items()}}
            self.store.save_config("watch", data, expected_revision=self._watch_revision)
            self.reload_watch()
            self.refresh()
            self.status.setText("Veille autorisée avec ces limites." if data["enabled"] else "Veille arrêtée à la prochaine étape. Un appel déjà en cours doit se terminer.")
        except (ValueError, OSError, sqlite3.Error) as exc:
            self._error(exc)

    def _fill_job(self, row):
        data = row["data"] if row else {"title": "", "request": "", "role": "interaction", "daily_time": "",
            "timezone": self.workspace.profile()["data"]["timezone"], "first_at": (datetime.now(timezone.utc)+timedelta(minutes=30)).isoformat(),
            "enabled": True, "notify_owner": False}
        moment = datetime.fromisoformat(data["first_at"]).astimezone(_zone(data["timezone"]))
        self._filling = True
        self.job_title.setText(data["title"])
        self.job_request.setPlainText(data["request"])
        self.job_role.setCurrentIndex(self.job_role.findData(data["role"]))
        self.job_timezone.setText(data["timezone"])
        self.job_daily.setText(data["daily_time"])
        self.job_due.setDateTime(QDateTime(QDate(moment.year, moment.month, moment.day), QTime(moment.hour, moment.minute), Qt.UTC))
        self.job_enabled.setChecked(data["enabled"])
        self.job_notify.setChecked(data["notify_owner"])
        self._job_id, self._job_revision = (row["id"], row["revision"]) if row else ("", 0)
        self._job_dirty, self._filling = False, False

    def new_job(self):
        if self._job_dirty:
            self.status.setText("Enregistre ou recharge la saisie avant de créer un autre travail.")
            return
        self._fill_job(None)

    def reload_job(self):
        try:
            self._fill_job(self.store.job(self._job_id) if self._job_id else None)
        except (ValueError, OSError, sqlite3.Error) as exc:
            self._error(exc)

    def _select_job(self, current, previous):
        if self._refreshing or current is None:
            return
        if self._job_dirty:
            self.status.setText("Enregistre ou recharge ta saisie avant de changer de travail.")
            self._refreshing = True
            self.job_tree.setCurrentItem(previous)
            self._refreshing = False
            return
        try:
            self._fill_job(self.store.job(current.data(0, Qt.UserRole)))
        except (ValueError, OSError, sqlite3.Error) as exc:
            self._error(exc)

    def save_job(self):
        try:
            date, clock = self.job_due.date(), self.job_due.time()
            zone = self.job_timezone.text().strip()
            moment = datetime(date.year(), date.month(), date.day(), clock.hour(), clock.minute(), tzinfo=_zone(zone))
            if datetime.fromtimestamp(moment.timestamp(), _zone(zone)).replace(tzinfo=None) != moment.replace(tzinfo=None):
                raise ValueError("orchestra_local_time_unavailable")
            data = {"title": self.job_title.text(), "request": self.job_request.toPlainText(), "role": self.job_role.currentData(),
                "first_at": moment.isoformat(), "daily_time": self.job_daily.text(), "timezone": zone,
                "enabled": self.job_enabled.isChecked(), "notify_owner": self.job_notify.isChecked()}
            row = self.store.save_job(data, identity=self._job_id, expected_revision=self._job_revision)
            self._fill_job(row)
            self.refresh()
            self.status.setText("Travail enregistré. Il démarrera à échéance si la veille est active et Jarvis ouvert.")
        except (ValueError, KeyError, OSError, sqlite3.Error) as exc:
            self._error(exc)

    def cancel_job(self):
        if self._job_dirty:
            self.status.setText("Enregistre ou recharge ta saisie avant d’annuler ce travail.")
            return
        try:
            self.store.cancel_job(self._job_id, expected_revision=self._job_revision)
            self.reload_job()
            self.refresh()
        except (ValueError, OSError, sqlite3.Error) as exc:
            self._error(exc)

    def review_job(self):
        if self._job_dirty:
            self.status.setText("Enregistre ou recharge ta saisie avant de classer cette interruption.")
            return
        try:
            self.store.review_interrupted(self._job_id, expected_revision=self._job_revision)
            self.reload_job()
            self.refresh()
            self.status.setText("Interruption classée à examiner. Aucun travail n’a été rejoué.")
        except (ValueError, OSError, sqlite3.Error) as exc:
            self._error(exc)

    def _reports_page(self):
        self.reports_page, layout = self._page("Rapports")
        self._note(layout, "Les rapports conservent la demande, le résultat et l’utilisation du budget. Un rapport de spécialiste ne certifie pas une action externe.")
        self.report_tree = QTreeWidget()
        self.report_tree.setHeaderLabels(["Agent", "État"])
        self.report_tree.setRootIsDecorated(False)
        self.report_tree.setMinimumHeight(170)
        self.report_tree.currentItemChanged.connect(lambda *_: self.show_report())
        layout.addWidget(self.report_tree)
        self.report_view = QTextBrowser()
        self.report_view.setOpenExternalLinks(False)
        self.report_view.setMinimumHeight(320)
        layout.addWidget(self.report_view, 1)
        self._button(layout, "Confier la suite à Jarvis", self.continue_report)

    def show_report(self):
        current = self.report_tree.currentItem()
        if current is None:
            return
        row = self.store.run(current.data(0, Qt.UserRole))
        if not row:
            return
        content = "DEMANDE\n" + row["request"] + "\n\nRAPPORT · " + LABELS.get(row["state"], row["state"]) + "\n" + row["report"]
        usage = row["receipt"].get("worker_usage") or {}
        content += "\n\nUtilisation : " + str(usage.get("model_calls", 0)) + " requêtes de cerveau · " + str(usage.get("tool_calls", 0)) + " appels d’outils."
        if content != self._signatures.get("report_content"):
            self.report_view.setPlainText(content)
            self._signatures["report_content"] = content

    def continue_report(self):
        current = self.report_tree.currentItem()
        if current:
            self.request_ready.emit("Consulte le rapport du spécialiste " + current.data(0, Qt.UserRole)
                + ", résume ce qui reste à faire et propose la suite. Les actions externes conservent leurs confirmations.")

    def _voice_page(self):
        self.voice_page, layout = self._page("Appels")
        self.voice_status = self._note(layout, "Canal vocal à connecter")
        self._note(layout, "Le service MCP fournit le numéro, la téléphonie, la transcription et la voix. Jarvis reçoit les demandes, prépare le travail et renvoie un rapport. La réception est relevée chaque minute pendant la veille. Le coût dépend du fournisseur choisi.")
        self.voice_fields = {}
        for key, label in (("server", "Serveur MCP vocal"), ("dial_tool", "Outil pour appeler"), ("inbox_tool", "Outil pour lire les demandes vocales"), ("reply_tool", "Outil pour répondre à la conversation")):
            field = QComboBox()
            field.setMinimumWidth(0)
            field.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            field.currentIndexChanged.connect(lambda: self._dirty("voice"))
            self.voice_fields[key] = field
            self._field(layout, label, field)
        for key, label, placeholder in (("contact_id", "Ton contact destinataire et expéditeur autorisé", "Identifiant réel ou numéro attendu par ce service"),
            ("contact_field", "Champ destinataire du service", "contact_id"), ("message_field", "Champ texte du service", "message")):
            field = QLineEdit()
            field.setMaxLength(180)
            field.setPlaceholderText(placeholder)
            field.textChanged.connect(lambda: self._dirty("voice"))
            self.voice_fields[key] = field
            self._field(layout, label, field)
        self.voice_flags = {}
        for key, label in (("enabled", "Activer cette association vocale"), ("automatic_calls", "Autoriser Jarvis à appeler ce contact uniquement"),
            ("automatic_replies", "Autoriser les réponses vocales aux demandes de ce contact"), ("allow_night_calls", "Autoriser aussi les appels entre 22 h et 7 h")):
            field = QCheckBox(label)
            field.toggled.connect(lambda: self._dirty("voice"))
            self.voice_flags[key] = field
            layout.addWidget(field)
        self.voice_quota = QSpinBox()
        self.voice_quota.setRange(1, 12)
        self.voice_quota.valueChanged.connect(lambda: self._dirty("voice"))
        self._field(layout, "Appels maximum sur 24 h", self.voice_quota)
        self.voice_fields["server"].currentIndexChanged.connect(self._voice_tool_choices)
        self.save_voice_button = self._button(layout, "Enregistrer mon canal et ces autorisations", self.save_voice)
        self._button(layout, "Recharger l’association", self.reload_voice)
        self._button(layout, "Ouvrir les connexions MCP", self.connections_requested.emit)
        self._button(layout, "Demander un appel de test", lambda: self.request_ready.emit("Appelle mon contact propriétaire configuré pour me dire : Bonjour, ici Jarvis. Ceci est un essai du canal vocal."))
        self._note(layout, "Journal vocal : un envoi incertain bloque les nouveaux envois du même type. Vérifie le service et son journal MCP ; un reçu ne certifie jamais que tu as décroché.")
        self.call_journal = QTextBrowser()
        self.call_journal.setOpenExternalLinks(False)
        self.call_journal.setMinimumHeight(160)
        layout.addWidget(self.call_journal)
        self._button(layout, "Après contrôle du service : classer le dernier envoi incertain", self.review_call)

    def review_call(self):
        try:
            row = next((c for c in self.store.calls(100) if c["state"] in {"unknown", "dispatching"}), None)
            if row is None:
                self.status.setText("Aucun envoi incertain à classer.")
                return
            self.store.review_call(row["id"], expected_state=row["state"])
            self.refresh()
            self.status.setText("Examen manuel enregistré. Aucun appel rejoué. Les blocages du journal MCP restent à examiner séparément.")
        except (ValueError, OSError, sqlite3.Error) as exc:
            self._error(exc)

    def _voice_tool_choices(self):
        server_id = self.voice_fields["server"].currentData()
        entry = self.voice.gateway.registry.get_server(server_id or "") or {}
        for key in ("dial_tool", "inbox_tool", "reply_tool"):
            box = self.voice_fields[key]
            current = box.currentData()
            box.blockSignals(True)
            box.clear()
            box.addItem("Aucun", "")
            for name in sorted(entry.get("tools") or {}):
                box.addItem(name, name)
            if box.findData(current) >= 0:
                box.setCurrentIndex(box.findData(current))
            box.blockSignals(False)

    def reload_voice(self):
        try:
            row = self.store.config("voice", VOICE_DEFAULTS)
            self._filling = True
            box = self.voice_fields["server"]
            box.clear()
            box.addItem("Choisir un serveur", "")
            for server in self.voice.gateway.registry.list_servers():
                box.addItem(server["id"], server["id"])
            if row["data"]["server"] and box.findData(row["data"]["server"]) < 0:
                box.addItem(row["data"]["server"] + " · absent", row["data"]["server"])
            box.setCurrentIndex(max(0, box.findData(row["data"]["server"])))
            self._voice_tool_choices()
            for key, field in self.voice_fields.items():
                if isinstance(field, QComboBox):
                    field.setCurrentIndex(max(0, field.findData(row["data"][key])))
                else:
                    field.setText(row["data"][key])
            for key, field in self.voice_flags.items():
                field.setChecked(row["data"][key])
            self.voice_quota.setValue(row["data"]["max_calls_day"])
            self._voice_revision, self._voice_dirty = row["revision"], False
        except (ValueError, OSError, sqlite3.Error) as exc:
            self._error(exc)
        finally:
            self._filling = False

    def save_voice(self):
        try:
            data = {**VOICE_DEFAULTS, **{k: f.currentData() if isinstance(f, QComboBox) else f.text() for k, f in self.voice_fields.items()},
                    **{k: f.isChecked() for k, f in self.voice_flags.items()}, "max_calls_day": self.voice_quota.value()}
            self.voice.save(data, expected_revision=self._voice_revision)
            self.reload_voice()
            self.refresh()
            self.status.setText("Association vocale enregistrée. La téléphonie réelle doit être vérifiée avec ce service.")
        except (ValueError, TypeError, OSError, sqlite3.Error) as exc:
            self._error(exc)

    def _tree(self, widget, rows, signature_key, build):
        signature = json.dumps(rows, sort_keys=True)
        if self._signatures.get(signature_key) == signature:
            return
        current = widget.currentItem()
        selected = current.data(0, Qt.UserRole) if current else ""
        self._refreshing = True
        widget.clear()
        for row in rows:
            item = QTreeWidgetItem(build(row))
            item.setData(0, Qt.UserRole, row["id"])
            widget.addTopLevelItem(item)
            if row["id"] == selected:
                widget.setCurrentItem(item)
        if widget.currentItem() is None and widget.topLevelItemCount() and widget is not self.job_tree:
            widget.setCurrentItem(widget.topLevelItem(0))
        self._refreshing = False
        self._signatures[signature_key] = signature

    def refresh(self, runtime=None):
        if runtime is not None:
            self._runtime = runtime
        try:
            data = self.store.snapshot()
            latest = {}
            for run in data.get("runs", []):
                latest.setdefault(run["role"], run["state"])
            roles = [{"id": m.agent_id, "name": self.role_name(m.agent_id), "state": latest.get(m.agent_id, "idle")}
                     for m in DEFAULT_CAPABILITY_REGISTRY.agents()]
            self._tree(self.role_tree, roles, "roles", lambda r: [r["name"], LABELS.get(r["state"], "À solliciter")])
            self.show_role()
            self._tree(self.job_tree, data.get("jobs", []), "jobs", lambda r: [r["data"]["title"], LABELS.get(r["state"], r["state"])])
            self._tree(self.report_tree, data.get("runs", []), "reports", lambda r: [r["role"], LABELS.get(r["state"], r["state"])])
            self.show_report()
            watch = data.get("watch") or self.store.config("watch", WATCH_DEFAULTS)
            online = (data.get("engine") or {}).get("online")
            self.watch_status.setText("Veille active · moteur présent" if online and watch["data"]["enabled"] else
                "Veille autorisée · démarrage du moteur attendu" if watch["data"]["enabled"] else "Veille désactivée")
            if not self._watch_dirty and watch["revision"] != self._watch_revision:
                self.reload_watch()
            readiness = self.voice.readiness()
            self.voice_status.setText(VOICE_LABELS[readiness["state"]] + (" · réception en veille" if online and watch["data"]["enabled"] else " · réception arrêtée"))
            voice = data.get("voice") or self.store.config("voice", VOICE_DEFAULTS)
            if not self._voice_dirty and voice["revision"] != self._voice_revision:
                self.reload_voice()
            calls = self.store.calls(12)
            content = "\n\n".join(datetime.fromtimestamp(c["created"], timezone.utc).strftime("%d/%m %H:%M UTC") +
                " · " + ("Appel" if c["kind"] == "call" else "Réponse") + " · " + c["state"] + "\n" + c["message"] +
                "\nReçu : " + json.dumps(c["receipt"], ensure_ascii=False) for c in calls) or "Aucun envoi vocal enregistré."
            if content != self._signatures.get("calls"):
                self.call_journal.setPlainText(content)
                self._signatures["calls"] = content
        except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as exc:
            self._refreshing = self._filling = False
            self._error(exc)
