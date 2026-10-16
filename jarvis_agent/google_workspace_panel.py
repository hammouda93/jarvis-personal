"""One Google account, five services. Qt reads public metadata only."""
from __future__ import annotations

import json

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QFileDialog, QFrame, QLabel, QMessageBox, QPushButton,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from .google_workspace_catalog import CLIENT_SLOT, SERVICES
from .google_workspace_connection import OPERATIONS, snapshot
from .mcp_server_registry import MCPRegistry
from .personal_workspace import PersonalWorkspaceStore

FEATURES = {
    "gmail": "Rechercher et lire · brouillons · envoyer et répondre · libellés · corbeille",
    "google_calendar": "Agendas · rendez-vous · création, modification et annulation d’événements",
    "google_drive": "Recherche · métadonnées · export texte · dossiers · copie, renommage et corbeille",
    "google_sheets": "Lire des plages · créer un classeur · remplacer, ajouter ou effacer des cellules",
    "google_docs": "Lire le texte · créer · ajouter ou remplacer du texte avec contrôle de révision",
}
FUNCTION_LABELS = {
    "gmail_get_profile": "Profil", "gmail_get_mailbox_counts": "Non-lus", "gmail_search_messages": "Recherche",
    "gmail_get_message": "Lire un message", "gmail_get_thread": "Lire un fil", "gmail_list_labels": "Libellés",
    "gmail_list_drafts": "Lister les brouillons", "gmail_get_draft": "Lire un brouillon",
    "gmail_create_draft": "Créer un brouillon", "gmail_update_draft": "Modifier un brouillon",
    "gmail_send_message": "Envoyer", "gmail_reply_message": "Répondre", "gmail_send_draft": "Envoyer un brouillon",
    "gmail_modify_message_labels": "Classer / lu ou non lu", "gmail_trash_message": "Corbeille", "gmail_untrash_message": "Restaurer",
    "list_calendars": "Lister les agendas", "list_calendar_events": "Lister les événements", "get_calendar_event": "Lire un événement",
    "create_calendar_event": "Créer", "update_calendar_event": "Modifier", "delete_calendar_event": "Annuler un événement",
    "search_drive_files": "Recherche", "get_drive_file": "Métadonnées", "get_drive_export": "Export texte",
    "create_drive_folder": "Créer un dossier", "copy_drive_file": "Copier", "update_drive_file": "Renommer / décrire", "trash_drive_file": "Corbeille",
    "get_spreadsheet": "Métadonnées", "get_sheet_values": "Lire une plage", "create_spreadsheet": "Créer un classeur",
    "update_sheet_values": "Remplacer des valeurs", "append_sheet_values": "Ajouter des lignes", "clear_sheet_values": "Effacer des valeurs",
    "get_document": "Lire le texte", "create_document": "Créer", "append_document_text": "Ajouter du texte", "replace_document_text": "Remplacer du texte",
}
STAGES = {"preparing": "Préparation…", "waiting_consent": "Choisis ton compte et autorise Google dans le navigateur.",
    "callback_received": "Retour du navigateur reçu…", "token_exchange": "Validation de l’autorisation…",
    "session_initializing": "Vérification de l’identité Google…", "credentials_storage": "Enregistrement dans le coffre sécurisé…",
    "authorized": "Autorisation enregistrée. Termine la vérification Gmail ci-dessous."}
ERRORS = {
    "google_desktop_client_required": "Choisis le JSON d’un client OAuth de type Application de bureau, téléchargé depuis Google Cloud. Une clé API ou un compte de service ne convient pas.",
    "google_client_import_failed": "Le fichier n’a pas pu être lu. Choisis de nouveau le JSON téléchargé depuis Google Cloud.",
    "google_client_required": "Importe d’abord la configuration Google de l’application.",
    "google_client_changed": "La configuration Google a changé. Actualise puis recommence la connexion.",
    "google_bindings_changed": "Une connexion a changé pendant le consentement. Actualise puis recommence ; les connexions existantes sont préservées.",
    "google_no_services_authorized": "Aucun des cinq services n’a été autorisé. Reconnecte Google et vérifie le consentement.",
    "google_browser_open_failed": "Le navigateur par défaut n’a pas pu être lancé. Vérifie son installation puis réessaie.",
    "google_consent_denied": "Autorisation refusée dans Google. La connexion précédente est conservée.",
    "oauth_cancelled": "Connexion annulée. La connexion précédente est conservée.",
    "oauth_timeout": "Le délai a expiré. Recommence ici ; ne réutilise pas un ancien onglet de retour.",
    "google_identity_unverified": "Google n’a pas confirmé l’identité du compte. Aucune nouvelle connexion activée.",
    "google_connection_failed": "La connexion n’a pas abouti. Vérifie les API activées, le client OAuth et le compte de test dans Google Cloud, puis réessaie.",
    "google_not_connected": "Connecte d’abord ton compte Google.",
    "google_vault_unavailable": "Le coffre sécurisé du système est indisponible. Sur Windows, lance Jarvis avec ton compte habituel. Sur Linux, une session Secret Service déverrouillée est nécessaire ; aucun stockage en clair n’est utilisé.",
    "google_dependency_missing": "Les dépendances des connecteurs manquent. Installe requirements-mcp.txt avec le Python utilisé par Jarvis, puis relance.",
}


class GoogleWorkspacePanel(QFrame):
    requested = Signal(str, str, str)

    def __init__(self, parent=None, *, registry=None, workspace=None):
        super().__init__(parent)
        self.registry = registry or MCPRegistry()
        self.workspace = workspace or PersonalWorkspaceStore()
        self._pending, self._external_busy, self._state = "", False, {}
        self.setObjectName("googleWorkspacePanel")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        body.setObjectName("googleWorkspaceBody")
        body.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(6, 6, 6, 6)
        self.scroll.setWidget(body)
        outer.addWidget(self.scroll)
        self._note(layout, "GOOGLE WORKSPACE · CONNEXION DIRECTE")
        self._note(layout, "Un compte Google pour Gmail, Calendar, Drive, Sheets et Docs. Sans Pipedream ni serveur supplémentaire.")
        self.status = self._note(layout, "")
        self.feedback = self._note(layout, "Aucun mail ni document n’est lu pendant la connexion. La vérification ci-dessous est en lecture seule.")
        self.setup_toggle = self._button(layout, "Configuration initiale de l’application ▸", self._toggle_setup)
        self.setup_toggle.setCheckable(True)
        self.setup_body = QFrame()
        setup = QVBoxLayout(self.setup_body)
        setup.setContentsMargins(0, 0, 0, 0)
        self._note(setup, "Une seule fois : Google exige une application OAuth identifiée. Il n’est pas possible de réutiliser la connexion de ChatGPT.")
        self._note(setup, "1. Dans Google Cloud, crée ou choisis un projet. Active Gmail API, Google Calendar API, Google Drive API, Google Sheets API et Google Docs API.")
        self._note(setup, "2. Dans Google Auth Platform, configure l’écran de consentement. Pour un usage personnel en mode Test, ajoute ton adresse aux utilisateurs tests.")
        self._note(setup, "3. Crée un client OAuth « Application de bureau », télécharge son JSON, puis importe-le ici. Ne colle aucun secret dans la conversation.")
        self.console_button = self._button(setup, "Ouvrir Google Cloud", lambda: QDesktopServices.openUrl(QUrl("https://console.cloud.google.com/auth/clients")))
        self.import_button = self._button(setup, "Importer la configuration Google (.json)", self.choose_client)
        self._note(setup, "En mode Test, Google peut faire expirer l’accès hors ligne après 7 jours : reconnecte alors le compte. Le coffre Windows protège les autorisations ; aucun fichier de jetons en clair n’est créé.")
        layout.addWidget(self.setup_body)
        self.connect_button = self._button(layout, "Connecter Google", self.start_connection)
        self.cancel_button = self._button(layout, "Annuler la connexion", self.cancel_connection)
        self.runtime_button = self._button(layout, "Activer mes connexions dans Jarvis", lambda: self._send("runtime_set", "", "true"))
        self.test_button = self._button(layout, "Vérifier Gmail · lecture seule", self.test_gmail)
        self._note(layout, "Cette vérification interroge directement Google : compte et compteurs exacts de non-lus. Aucun modèle IA ni ancienne mission n’est utilisé.")
        self.service_labels = {}
        for service, label in SERVICES.items():
            self.service_labels[service] = self._note(layout, label + " · à connecter\n" + FEATURES[service])
        self._note(layout, "Les lectures autorisées sont utilisables directement. Les envois et modifications gardent leur confirmation. Les contenus demandés dans la conversation peuvent être transmis à ton fournisseur IA.")
        self._note(layout, "Après connexion, demande par exemple : « Quels sont mes rendez-vous demain ? » ou « Retrouve mon document de suivi dans Drive ». Les permissions Google et les fonctions ci-dessus restent la limite réelle.")
        self.disconnect_button = self._button(layout, "Déconnecter Google de Jarvis", self.disconnect)
        self.reload_button = self._button(layout, "Actualiser l’état local", self.refresh)
        layout.addStretch(1)
        self.setStyleSheet("""
            QFrame#googleWorkspacePanel,QWidget#googleWorkspaceBody,QScrollArea {background:#081c2c;}
            QLabel {color:#c5e6ed;font-size:11px;}
            QPushButton {background:#163e4b;color:#c5eee5;border:1px solid #286278;border-radius:4px;padding:7px;font-size:11px;}
            QPushButton:disabled {color:#6e8791;}
        """)
        self.refresh()
        self.setup_toggle.setChecked(not self._state.get("client_configured"))
        self._toggle_setup()

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

    def _toggle_setup(self):
        opened = self.setup_toggle.isChecked()
        self.setup_body.setVisible(opened)
        self.setup_toggle.setText("Configuration initiale de l’application ▾" if opened else "Configuration initiale de l’application ▸")

    def refresh(self):
        try:
            self._state = snapshot(self.registry, self.workspace)
        except Exception:
            self._state = {}
            self.status.setText("État local indisponible. Vérifie le stockage de Jarvis.")
            self._update_actions()
            return
        state = self._state
        connected = bool(state["server_id"] and state["enabled"])
        self.status.setText(("Autorisation enregistrée : " + state["email"] + "\nAPI à vérifier, aucune session permanente présumée.") if connected else
            "Prêt à connecter ton compte Google." if state["client_configured"] else "Commence par la configuration Google de l’application, une seule fois.")
        self.connect_button.setText("Reconnecter ou changer de compte Google" if state["server_id"] else "Connecter Google")
        for service in state["services"]:
            available = connected and service["binding_active"] and service["tools"]
            text = (str(len(service["tools"])) + " fonctions autorisées · API à tester") if available else "non activé avec ce compte"
            functions = " · ".join(FUNCTION_LABELS[name] for name in service["tools"]) if available else (
                "Fonctions prévues : " + FEATURES[service["id"]] if not state["server_id"] else "Ce service n’est pas actif dans cette connexion.")
            self.service_labels[service["id"]].setText(service["label"] + " · " + text + "\n" + functions)
        self._update_actions()

    def _update_actions(self):
        busy = bool(self._pending or self._external_busy)
        state = self._state
        connected = bool(state.get("server_id") and state.get("enabled"))
        gmail = next((s for s in state.get("services", []) if s["id"] == "gmail"), {})
        self.connect_button.setEnabled(not busy and state.get("client_configured") is True)
        self.test_button.setEnabled(not busy and connected and state.get("runtime_enabled") is True and bool(gmail.get("binding_active") and gmail.get("tools")))
        self.disconnect_button.setEnabled(not busy and connected)
        self.runtime_button.setVisible(connected and not state.get("runtime_enabled"))
        self.runtime_button.setEnabled(not busy)
        for button in (self.import_button, self.console_button, self.reload_button):
            button.setEnabled(not busy)
        self.cancel_button.setVisible(self._pending == "google_connect")
        self.cancel_button.setEnabled(self._pending == "google_connect")

    def set_busy(self, busy):
        self._external_busy = bool(busy)
        self._update_actions()

    def _send(self, operation, server, value=""):
        if self._pending or self._external_busy:
            return
        self._pending = operation
        self.feedback.setText("Demande transmise à Jarvis…")
        self._update_actions()
        self.requested.emit(operation, server, value)

    def choose_client(self):
        path, _ = QFileDialog.getOpenFileName(self, "Configuration OAuth Google · Application de bureau", "", "Configuration Google (*.json)")
        if path:
            self._send("google_import_client", CLIENT_SLOT, json.dumps({"path": path}))

    def start_connection(self):
        if not self._state.get("client_configured"):
            return
        self._send("google_connect", CLIENT_SLOT, json.dumps({key: self._state[key]
            for key in ("client_fingerprint", "binding_revisions")}))

    def cancel_connection(self):
        if self._pending == "google_connect":
            self.requested.emit("cancel_oauth", CLIENT_SLOT, "")
            self.feedback.setText("Annulation demandée…")

    def test_gmail(self):
        self._send("google_test", self._state.get("server_id", ""))

    def disconnect(self):
        if QMessageBox.question(self, "Déconnecter Google", "Retirer l’accès Google de Jarvis ? Aucun mail ni document ne sera supprimé. La configuration de l’application reste disponible.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self._send("google_disconnect", self._state.get("server_id", ""))

    def show_progress(self, progress):
        if progress.get("operation") == "google_connect" and self._pending == "google_connect":
            self.feedback.setText(STAGES.get(progress.get("stage"), "Connexion Google en cours…"))

    def show_result(self, result):
        operation = result.get("operation")
        if operation not in OPERATIONS and not (operation == "runtime_set" and self._pending == operation):
            return
        self._pending = ""
        self.refresh()
        if result.get("success"):
            self.feedback.setText(str(result.get("message") or "Configuration enregistrée."))
            if operation == "google_import_client":
                self.setup_toggle.setChecked(False)
                self._toggle_setup()
        else:
            self.feedback.setText(str(result.get("message") or ERRORS.get(result.get("reason"), ERRORS["google_connection_failed"])))
