"""Compact futuristic operator console: real observations, never demo values."""
from __future__ import annotations

import html
from collections import deque

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QTextBrowser,
    QStyle, QVBoxLayout, QWidget,
)

from .operator_telemetry import snapshot
from .project_roadmap import snapshot as roadmap_snapshot
from .mcp_operator_panel import MCPConnectionsPanel
from .mission_supervisor_panel import MissionSupervisorPanel
from .skills_operator_panel import SkillsPanel


_STATUS = {
    "dispatched": ("EN COURS / À VÉRIFIER", "#f7c679"),
    "unknown": ("INCERTAIN", "#f8a4a4"),
    "succeeded": ("OUTIL OK", "#70dbbe"),
    "failed": ("ÉCHEC", "#f8a4a4"),
    "resolved": ("REVU MANUELLEMENT", "#94c6f9"),
    "guarded": ("BLOQUÉ PAR PROTECTION", "#f7c679"),
    "running": ("EN COURS", "#71d6ef"),
    "waiting_external": ("PREUVE ATTENDUE", "#f7c679"),
    "blocked": ("BLOQUÉ", "#f8a4a4"),
    "completed": ("TERMINÉ", "#70dbbe"),
}


def _escape(value: object, max_chars: int = 160) -> str:
    return html.escape(str(value or "")[:max_chars], quote=True)


def _badge(value: object) -> str:
    status = str(value or "").lower()
    title, color = _STATUS.get(status, (status.upper() or "INCONNU", "#b9cedb"))
    return f'<span style="color:{color};font-weight:650">{_escape(title)}</span>'


def render_snapshot(data: dict) -> tuple[str, str, str]:
    """Return bounded, HTML-escaped mission, action and task views."""
    missions = data.get("missions") or []
    actions = data.get("actions") or []
    tasks = data.get("tasks") or []
    if not data.get("mission_enabled"):
        mission_html = '<p class="muted">Mission Convergence désactivée (mode de compatibilité).</p>'
        step_html = '<p class="muted">Plan non suivi en mode compatibilité.</p>'
    elif not missions:
        mission_html = '<p class="muted">Aucun checkpoint de mission enregistré.</p>'
        step_html = '<p class="muted">Aucune étape observée.</p>'
    else:
        mission_html = "".join(
            '<p style="margin-bottom:9px">'
            f'{_badge(x.get("status"))}<br><b>{_escape(x.get("goal"), 120)}</b><br>'
            f'<span style="color:#789fb4">{_escape(x.get("id"), 54)}</span> · '
            + ('<span style="color:#65ddb0">objectif prouvé</span>' if x.get("verified")
               else '<span style="color:#eec28c">objectif non prouvé</span>')
            + (' · <span style="color:#f7a1a1">révision exigée</span>' if x.get("needs_review") else "")
            + '</p>'
            for x in missions
        )
        step_html = "".join(
            f'<p style="margin-bottom:7px">{_badge(x.get("status"))} '
            f'<b>{_escape(x.get("name"), 50)}</b><br>'
            f'<span style="color:#91b4c7">{_escape(x.get("capability"), 65)}</span>'
            + (f'<br><span style="color:#8ddbcf">{_escape(", ".join(x.get("tools", [])), 160)}</span>' if x.get("tools") else "")
            + '</p>'
            for x in tasks
        ) or '<p class="muted">Aucune étape enregistrée.</p>'
    if not data.get("reliability_enabled"):
        action_html = '<p class="muted">Protection Hermes désactivée. Aucun suivi d’action actif.</p>'
    elif not actions:
        action_html = '<p class="muted">En attente du premier outil exécuté.</p>'
    else:
        action_html = "".join(
            f'<p style="margin-bottom:7px">{_badge(x.get("status"))} '
            f'<b>{_escape(x.get("tool"), 100)}</b>'
            + (' · <span style="color:#70dbbe">preuve outil ✓</span>' if x.get("verified")
               else ' · <span style="color:#eec28c">non vérifié</span>')
            + (f'<br><span style="color:#f7c679">raison : {_escape(x.get("guard_reason"), 65)}</span>'
               if x.get("status") == "guarded" and x.get("guard_reason") else "")
            + f'<br><span style="color:#779bad">{_escape(x.get("id"), 28)}…</span></p>'
            for x in actions
        )
    return mission_html, step_html, action_html


class OperatorConsole(QFrame):
    """Live projection plus explicit UI-only command signal (no direct agent access)."""

    mission_requested = Signal(str, str)
    mcp_requested = Signal(str, str, str)
    skill_requested = Signal(str, str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("operatorConsole")
        self.setMinimumWidth(280)
        self._last_model = {}
        self._last_snapshot = {}
        self._render_cache: dict[str, str] = {}
        self._live_events: deque[dict] = deque(maxlen=36)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(13, 13, 13, 13)
        layout.setSpacing(8)

        title = QLabel("◈   CENTRE DE CONTRÔLE")
        title.setObjectName("operatorHeading")
        subtitle = QLabel("ÉTATS OBSERVÉS  ·  ACTIONS CONTRÔLÉES")
        subtitle.setObjectName("operatorSubtitle")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        summary = QFrame()
        summary.setObjectName("operatorSummary")
        sum_layout = QVBoxLayout(summary)
        sum_layout.setContentsMargins(10, 9, 10, 9)
        sum_layout.setSpacing(3)
        self.mode_line = QLabel("Runtime  —")
        self.mission_line = QLabel("Missions  —")
        self.action_line = QLabel("Actions  —")
        self.model_line = QLabel("Modèle  —")
        self.capabilities_line = QLabel("Capacités  —")
        self.learning_line = QLabel("Skills et mémoire  —")
        self.phase_line = QLabel("État : initialisation")
        self.progress_line = QLabel("Feuille de route : chargement")
        for line in (self.phase_line, self.progress_line, self.mode_line, self.mission_line, self.action_line, self.model_line, self.capabilities_line, self.learning_line):
            line.setObjectName("operatorMetric")
            line.setWordWrap(True)
            sum_layout.addWidget(line)

        controls_box = QFrame()
        controls_box.setObjectName("operatorGroup")
        mission_layout = QVBoxLayout(controls_box)
        mission_layout.setContentsMargins(10, 8, 10, 8)
        mission_layout.setSpacing(6)
        control_title = QLabel("◉   MISSION EXPLICITE · MÊME CERVEAU")
        control_title.setObjectName("operatorSection")
        control_title.setWordWrap(True)
        mission_layout.addWidget(control_title)
        self.mission_goal_input = QLineEdit()
        self.mission_goal_input.setMaxLength(2000)
        self.mission_goal_input.setObjectName("missionGoal")
        self.mission_goal_input.setPlaceholderText("Objectif à poursuivre sur plusieurs échanges…")
        self.mission_begin_only_button = QPushButton("＋ Créer mission sans agir (plan d'abord)")
        self.mission_begin_only_button.setObjectName("missionButton")
        self.mission_begin_button = QPushButton("▶ Démarrer et exécuter")
        self.mission_begin_button.setObjectName("missionButton")
        self.mission_picker = QComboBox()
        self.mission_picker.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.mission_picker.setMinimumContentsLength(12)
        self.mission_picker.setObjectName("missionPicker")
        self.mission_picker.addItem("Choisir une mission enregistrée…", "")
        self._mission_choices = ()
        self.mission_picker.currentIndexChanged.connect(
            lambda _: self._select_saved_mission()
        )
        self.mission_id_input = QLineEdit()
        self.mission_id_input.setMaxLength(64)
        self.mission_id_input.setObjectName("missionId")
        self.mission_id_input.setPlaceholderText("Identifiant live_… d'une mission existante")
        self.mission_resume_button = QPushButton("↻ Reprendre le suivi")
        self.mission_resume_button.setObjectName("missionButton")
        self.mission_criteria_input = QLineEdit()
        self.mission_criteria_input.setObjectName("missionGoal")
        self.mission_criteria_input.setMaxLength(500)
        self.mission_criteria_input.setPlaceholderText(
            "Critères de preuve : resultat_observe, cible_confirmee…"
        )
        self.mission_auto_plan_button = QPushButton("◈ Générer un plan (même IA · 1 requête)")
        self.mission_auto_plan_button.setObjectName("missionButton")
        self.mission_plan_button = QPushButton("⊕ Enregistrer les critères de réussite")
        self.mission_plan_button.setObjectName("missionButton")
        self.mission_route_button = QPushButton("⬡ Proposer agents / capacités MCP")
        self.mission_route_button.setObjectName("missionButton")
        self.mission_review_button = QPushButton("◇ Vérifier la progression")
        self.mission_review_button.setObjectName("missionButton")
        self.mission_detach_button = QPushButton("Ⅱ Détacher sans conclure")
        self.mission_detach_button.setObjectName("missionButton")
        for button, text, icon in (
            (self.mission_begin_only_button, "Creer sans agir", QStyle.SP_FileIcon),
            (self.mission_begin_button, "Demarrer", QStyle.SP_MediaPlay),
            (self.mission_resume_button, "Reprendre", QStyle.SP_BrowserReload),
            (self.mission_detach_button, "Detacher", QStyle.SP_MediaPause),
            (self.mission_plan_button, "Criteres de reussite", QStyle.SP_DialogApplyButton),
            (self.mission_auto_plan_button, "Generer un plan", QStyle.SP_FileDialogDetailedView),
            (self.mission_route_button, "Proposer des agents", QStyle.SP_FileDialogListView),
            (self.mission_review_button, "Verifier la progression", QStyle.SP_DialogApplyButton),
        ):
            button.setToolTip(button.text())
            button.setText(text)
            button.setIcon(self.style().standardIcon(icon))
        buttons = QHBoxLayout()
        buttons.setSpacing(5)
        buttons.addWidget(self.mission_resume_button)
        buttons.addWidget(self.mission_detach_button)
        self.mission_feedback = QLabel(
            "Convergence requise · le bouton Démarrer transmet l'objectif "
            "au moteur actuel, pas à un nouvel agent."
        )
        self.mission_feedback.setWordWrap(True)
        self.mission_feedback.setObjectName("operatorMetric")
        mission_layout.addWidget(self.mission_goal_input)
        mission_layout.addWidget(self.mission_begin_only_button)
        mission_layout.addWidget(self.mission_begin_button)
        mission_layout.addWidget(self.mission_picker)
        mission_layout.addWidget(self.mission_id_input)
        mission_layout.addWidget(self.mission_criteria_input)
        mission_layout.addWidget(self.mission_plan_button)
        mission_layout.addWidget(self.mission_auto_plan_button)
        mission_layout.addWidget(self.mission_review_button)
        mission_layout.addWidget(self.mission_route_button)
        mission_layout.addLayout(buttons)
        mission_layout.addWidget(self.mission_feedback)
        self.mission_begin_button.clicked.connect(
            lambda: self._request_mission("begin", self.mission_goal_input.text())
        )
        self.mission_begin_only_button.clicked.connect(
            lambda: self._request_mission("begin_only", self.mission_goal_input.text())
        )
        self.mission_resume_button.clicked.connect(
            lambda: self._request_mission("resume", self.mission_id_input.text())
        )
        self.mission_review_button.clicked.connect(
            lambda: self._request_mission("review", self.mission_id_input.text())
        )
        self.mission_route_button.clicked.connect(
            lambda: self._request_mission("route", self.mission_id_input.text())
        )
        self.mission_plan_button.clicked.connect(
            lambda: self._request_mission("plan", self.mission_criteria_input.text())
        )
        self.mission_auto_plan_button.clicked.connect(
            lambda: self._request_mission("auto_plan", "")
        )
        self.mission_detach_button.clicked.connect(
            lambda: self._request_mission("detach", "")
        )

        self.mcp_panel = MCPConnectionsPanel(self)
        self.mcp_panel.requested.connect(self.mcp_requested.emit)
        self.active_panel = MissionSupervisorPanel(self)
        self.active_panel.requested.connect(self.mission_requested.emit)

        self._views = {}
        scroll = QScrollArea(self)
        self.scroll_area = scroll
        scroll.setObjectName("operatorScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        content.setObjectName("operatorScrollContent")
        event_list = QVBoxLayout(content)
        event_list.setContentsMargins(0, 0, 0, 0)
        event_list.setSpacing(7)
        event_list.addWidget(summary)
        event_list.addWidget(controls_box)
        for key, label in [
            ("missions", "◉   MISSIONS / OBJECTIFS"),
            ("tasks", "◇   ÉTAPES / SUPERVISION"),
            ("supervisor", "◉   CONTRÔLE SÉMANTIQUE / PREUVES"),
            ("routing", "⬡   PROPOSITIONS D'AGENTS / MCP (NON EXÉCUTÉES)"),
            ("actions", "⚙   OUTILS / VÉRIFICATIONS"),
            ("events", "⌁   JOURNAL DES ÉVÉNEMENTS"),
            ("live", "◈   ACTIVITÉ DE CETTE SESSION"),
            ("roadmap", "◫   FEUILLE DE ROUTE · GATES"),
        ]:
            group = QFrame()
            group.setObjectName("operatorGroup")
            group_layout = QVBoxLayout(group)
            group_layout.setContentsMargins(9, 8, 9, 7)
            group_layout.setSpacing(4)
            header = QLabel(label)
            header.setWordWrap(True)
            header.setObjectName("operatorSection")
            view = QTextBrowser()
            view.setOpenExternalLinks(False)
            view.setObjectName("operatorEvents")
            view.setMinimumHeight(76)
            view.setMaximumHeight(168)
            self._views[key] = view
            group_layout.addWidget(header)
            group_layout.addWidget(view)
            event_list.addWidget(group)
        event_list.addWidget(self.active_panel)
        event_list.addWidget(self.mcp_panel)
        self.skills_panel = SkillsPanel(self)
        self.skills_panel.requested.connect(self.skill_requested.emit)
        event_list.addWidget(self.skills_panel)
        event_list.addStretch(1)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        self.setStyleSheet("""
            QLabel, QCheckBox {color:#b4dce7;font-size:10px;}
            QLineEdit, QComboBox, QSpinBox, QTreeWidget, QPlainTextEdit {
                background:#061a2b;color:#e4f9ff;border:1px solid #286278;
                border-radius:6px;padding:5px;font-size:10px;
                selection-background-color:#174d60;
            }
            QHeaderView::section {background:#0b3445;color:#b4dce7;padding:4px;border:0;}
            QPushButton {background:#0b3445;color:#9ef3ed;border:1px solid #286278;
                border-radius:6px;padding:6px;font-size:10px;}
            QPushButton:hover {background:#155366;}
            QPushButton:disabled {color:#718c9b;}
            QCheckBox::indicator {width:14px;height:14px;}
            QFrame#operatorConsole {background: rgba(2,13,27,230);
                border:1px solid rgba(56,164,192,105);border-radius:15px;}
            QFrame#operatorSummary {background:#072138; border-radius:11px;
                border:1px solid #16435a;}
            QFrame#operatorGroup {background:rgba(5,25,43,180);
                border-radius:10px;border:1px solid #17384c;}
            QLabel#operatorHeading {color:#e1fbff; font-size:12px; font-weight:700;}
            QLabel#operatorSubtitle {color:#6dbac5;font-size:8px;}
            QLabel#operatorSection {color:#7edee5;font-size:9px;font-weight:650;}
            QLabel#operatorMetric {color:#b4dce7;font-size:10px;}
            QLineEdit#missionGoal,QLineEdit#missionId,QComboBox#missionPicker {
                background:#061a2b;color:#e4f9ff;border:1px solid #286278;
                border-radius:6px;padding:5px;font-size:10px;
            }
            QPushButton#missionButton {
                background:#0b3445;color:#9ef3ed;border:1px solid #286278;
                border-radius:6px;padding:6px;font-size:10px;
            }
            QPushButton#missionButton:hover {background:#155366;}
            QPushButton#missionButton:disabled {color:#718c9b;}
            QScrollArea#operatorScroll, QWidget#operatorScrollContent {
                background: transparent; border: none;
            }
            QTextBrowser#operatorEvents {background:transparent;border:none;
                color:#ccedf2;font-size:9px;selection-background-color:#174d60;}
        """)
        self.apply_snapshot(snapshot())

    def _select_saved_mission(self) -> None:
        selected = str(self.mission_picker.currentData() or "")
        if selected:
            self.mission_id_input.setText(selected)

    def _request_mission(self, operation: str, value: str) -> None:
        """Signal request only. Worker is authoritative and owns runtime."""
        raw = str(value or "").strip()
        if operation in {"begin", "begin_only", "resume", "plan"} and not raw:
            self.mission_feedback.setText("Objectif ou identifiant de mission requis.")
            return
        self.mission_requested.emit(operation, raw)

    def show_mcp_result(self, result: dict) -> None:
        self.mcp_panel.show_result(result)

    def show_mission_result(self, result: dict) -> None:
        self.active_panel.apply_result(result)
        success = result.get("success") is True
        operation = str(result.get("operation") or "")
        mid = str(result.get("mission_id") or "")
        if success and mid.startswith("live_"):
            self.mission_id_input.setText(mid)
        if success and operation in {"begin", "begin_only"}:
            self.mission_goal_input.clear()
        if success and operation == "begin_only":
            self.mission_feedback.setText(
                "Mission créée SANS action. Utilisez « Générer un plan » ; "
                "aucun outil, aucun modèle ou preuve lancé pour l'instant."
            )
            return
        if success and operation == "auto_plan":
            steps = max(0, min(6, int(result.get("step_count") or 0)))
            evidence = max(0, min(24, int(result.get("evidence_count") or 0)))
            ambiguities = max(0, min(8, int(result.get("unresolved_count") or 0)))
            self.mission_feedback.setText(
                f"Plan IA enregistré : {steps} étape(s), "
                f"{evidence} critère(s), {ambiguities} ambiguïté(s). "
                "Aucune action effectuée, aucune preuve validée. "
                "Utilisez « Vérifier la progression » avant de continuer."
            )
            return
        if success and operation == "plan":
            self.mission_criteria_input.clear()
            self.mission_feedback.setText(
                "Critères enregistrés. Aucun justificatif validé automatiquement."
            )
            return
        if success and operation == "route":
            proposal = result.get("proposal") or {}
            steps = list(proposal.get("steps") or [])[:18]
            blocks = [
                '<p><b>PLANIFICATION EN LECTURE SEULE</b><br>'
                'Candidats uniquement : aucune action, délégation ou '
                'preuve de réussite automatique.</p>'
            ]
            for step in steps:
                blocks.append(
                    f'<p><b>{_escape(step.get("step_id"), 70)}</b> '
                    f'· {_escape(step.get("routing_status"), 60)}<br>'
                    f'{_escape(step.get("intent"), 150)}</p>'
                )
                for item in list(step.get("candidates") or [])[:4]:
                    blocks.append(
                        f'<p style="margin-left:9px">'
                        f'<span style="color:#84dfdb">{_escape(item.get("agent"), 80)}</span>'
                        f' · {_escape(item.get("capability"), 100)}'
                        f'<br>Score de correspondance : {_escape(item.get("match_score"), 16)}'
                        f' · autorisation requise : {_escape(item.get("requires"), 80)}'
                        f'<br><span style="color:#e6bb7f">Candidat non approuvé pour exécution</span>'
                        '</p>'
                    )
            if not steps:
                blocks.append('<p>Aucun plan sémantique applicable enregistré.</p>')
            self._render_html("routing", "".join(blocks))
            self.mission_feedback.setText(
                "Propositions calculées, sans exécution. "
                "Choix final soumis au modèle et aux permissions existantes."
            )
            return
        if success and operation == "review":
            report = result.get("review") or {}
            state = str(report.get("state") or "non_renseigné")[:60]
            missing = len(report.get("missing_evidence") or [])
            self.mission_feedback.setText(
                f"Supervision : {state} · {missing} preuve(s) manquante(s). "
                "Aucun outil exécuté ni objectif automatiquement déclaré réussi."
            )
            return
        if not success:
            description = str(result.get("reason") or "commande refusée")[:160]
            self.mission_feedback.setText("Refus : " + description)
        elif result.get("manual_review_required"):
            self.mission_feedback.setText(
                "Mission BLOQUÉE : vérification indépendante exigée, aucune reprise automatique."
            )
        else:
            message = {
                "begin": "Mission ouverte. L'objectif est envoyé au moteur actuel.",
                "resume": "Mission rattachée. Envoyez votre prochaine instruction.",
                "detach": "Suivi détaché. Mission conservée sans déclarer la réussite.",
            }.get(operation, "État de la mission actualisé.")
            self.mission_feedback.setText(message)
        # Refresh is handled by the existing read-only timer.

    def _render_html(self, key: str, rendered: str) -> None:
        # Polling must not reset the operator's scroll/selection 40 times/min.
        if self._render_cache.get(key) == rendered:
            return
        self._render_cache[key] = rendered
        viewer = self._views[key]
        scrollbar = viewer.verticalScrollBar()
        position = scrollbar.value()
        viewer.setHtml(rendered)
        scrollbar.setValue(min(position, scrollbar.maximum()))

    def apply_snapshot(self, data: dict) -> None:
        self._last_snapshot = dict(data)
        self.active_panel.apply_snapshot(data)
        self.mcp_panel.apply_snapshot(data.get("mcp") or {})
        missions, steps, actions = render_snapshot(data)
        candidates = tuple(
            (str(m.get("id") or ""),
             str(m.get("status") or ""),
             str(m.get("goal") or "")[:62])
            for m in (data.get("missions") or [])
            if str(m.get("id") or "").startswith("live_")
            and str(m.get("status") or "") not in {"completed", "failed"}
        )
        if candidates != self._mission_choices:
            chosen = str(self.mission_picker.currentData() or "")
            self.mission_picker.blockSignals(True)
            self.mission_picker.clear()
            self.mission_picker.addItem("Choisir une mission enregistrée…", "")
            for mission_id, status, label in candidates:
                self.mission_picker.addItem(
                    (label or mission_id) + " · " + status,
                    mission_id,
                )
            index = self.mission_picker.findData(chosen)
            if index >= 0:
                self.mission_picker.setCurrentIndex(index)
            self.mission_picker.blockSignals(False)
            self._mission_choices = candidates
        self._render_html("missions", missions)
        self._render_html("tasks", steps)
        review = data.get("supervisor")
        if not data.get("mission_enabled"):
            review_html = "<p>Superviseur indisponible : missions désactivées.</p>"
        elif not isinstance(review, dict):
            review_html = "<p>Aucune mission à superviser.</p>"
        else:
            current = _escape(review.get("state") or "non_renseigné", 65)
            next_action = _escape(review.get("next_action") or "non_renseigné", 65)
            count = len(review.get("recorded_evidence") or [])
            needed = len(review.get("required_evidence") or [])
            pending = len(review.get("missing_evidence") or [])
            color = "#70dbbe" if review.get("goal_verified") is True else "#f3c481"
            review_html = (
                f'<p><span style="color:{color}"><b>État : {current}</b></span><br>'
                f'Prochaine vérification : <b>{next_action}</b><br>'
                f'Preuves étape enregistrées : {count}/{needed} · manquantes : {pending}<br>'
                '<span style="color:#a8cbd7">Un outil OK ne prouve pas l\'objectif final.</span></p>'
            )
            for entry in list(review.get("steps") or [])[:14]:
                missing = ", ".join(entry.get("waiting_for") or [])[:160]
                deps = ", ".join(entry.get("unmet_dependencies") or [])[:160]
                review_html += (
                    f'<p style="margin-bottom:7px"><b>{_escape(entry.get("id"), 65)}</b>'
                    f' · {_escape(entry.get("state"), 65)}<br>'
                    f'{_escape(entry.get("intent"), 140)}'
                    + (f'<br><span style="color:#f3c481">Preuves : {_escape(missing)}</span>' if missing else "")
                    + (f'<br><span style="color:#e2b18d">Dépendances : {_escape(deps)}</span>' if deps else "")
                    + '</p>'
                )
            if review.get("unresolved"):
                review_html += (
                    '<p style="color:#f3c481">Ambiguïtés : '
                    + _escape(", ".join(review["unresolved"])[:160])
                    + "</p>"
                )
        self._render_html("supervisor", review_html)
        self._render_html("actions", actions)
        entries = data.get("events") or []
        if not data.get("mission_enabled"):
            journal_html = "<p>Journal désactivé : Convergence OFF.</p>"
        elif not entries:
            journal_html = "<p>Aucun événement enregistré.</p>"
        else:
            journal_html = "".join(
                f'<p style="margin-bottom:6px"><b>{_escape(entry.get("kind"), 90)}</b>'
                f'<br><span style="color:#93bbca">{_escape(entry.get("component"), 80)}</span>'
                + (" · <span style='color:#70dbbe'>OK</span>" if entry.get("success") is True
                   else " · <span style='color:#f9a4a4'>ÉCHEC</span>" if entry.get("success") is False
                   else " · <span style='color:#e9bc83'>non évalué</span>")
                + "</p>"
                for entry in entries
            )
        self._render_html("events", journal_html)
        roadmap = roadmap_snapshot()
        self.progress_line.setText(
            f"Développement : étape {roadmap['current']}/{roadmap['total']}"
            f" · {roadmap['current_title']}"
        )
        labels = {"integrated": ("CODE INTÉGRÉ", "#6fe0c2"),
                  "in_progress": ("EN DÉVELOPPEMENT", "#f0c481"),
                  "planned": ("À CONSTRUIRE", "#879cac")}
        parts = []
        for stage in roadmap["stages"]:
            label, shade = labels.get(stage["implementation"], ("INCONNU", "#b0d0e0"))
            suffix = (
                " · CI V3 validée" if stage["automated"] == "green_current"
                else " · CI antérieure OK" if stage["automated"] == "green_ancestor"
                else " · CI à valider" if stage["automated"] == "pending"
                else ""
            )
            parts.append(
                f'<p style="margin-bottom:6px"><span style="color:{shade}">'
                f'{stage["number"]:02d} · {_escape(label)}</span> '
                f'<b>{_escape(stage["title"], 95)}</b>'
                f'<br><span style="color:#8faeba">{_escape(suffix)}</span>'
                '<br><span style="color:#edbe8c">Windows réel : non validé</span></p>'
            )
        self._render_html("roadmap", "".join(parts))
        self.mission_begin_button.setEnabled(bool(data.get("mission_enabled")))
        self.mission_begin_only_button.setEnabled(bool(data.get("mission_enabled")))
        self.mission_resume_button.setEnabled(bool(data.get("mission_enabled")))
        self.mission_detach_button.setEnabled(bool(data.get("mission_enabled")))
        self.mission_review_button.setEnabled(bool(data.get("mission_enabled")))
        self.mission_route_button.setEnabled(bool(data.get("mission_enabled")))
        self.mission_plan_button.setEnabled(bool(data.get("mission_enabled")))
        self.mission_auto_plan_button.setEnabled(bool(data.get("mission_enabled")))
        mission_on = bool(data.get("mission_enabled"))
        reliability_on = bool(data.get("reliability_enabled"))
        kernel_mode = "Shadow configuré (passif)" if data.get("kernel_shadow_enabled") else "Shadow désactivé"
        self.mode_line.setText(
            f"Kernel : {kernel_mode}  ·  Missions : {'ON' if mission_on else 'OFF'}"
            f"  ·  Fiabilité : {'ON' if reliability_on else 'OFF'}"
        )
        self.capabilities_line.setText(
            f"Mémoire V5 config : {'ON' if data.get('semantic_memory_enabled') else 'OFF'}"
            f"  ·  Navigateur : {'ON' if data.get('browser_core_enabled') else 'OFF'}"
            f"  ·  PC : {'ON' if data.get('computer_core_enabled') else 'OFF'}"
            f"  ·  Apprentissage : {'ON' if data.get('learning_enabled') else 'OFF'}"
        )
        knowledge = data.get("knowledge_stats") or {}
        memory = data.get("memory_stats") or {}
        def count(value):
            return "—" if value is None else str(value)
        self.learning_line.setText(
            f"Skills : {count(knowledge.get('skills'))}  ·  Leçons : {count(knowledge.get('lessons'))}"
            f"  ·  Profils : {count(knowledge.get('app_profiles'))}"
            f"  ·  Souvenirs : {count(memory.get('raw_count'))}"
        )
        self.mission_line.setText(
            f"Missions observées : {len(data.get('missions') or [])}"
            + (" · échantillon récent" if mission_on else " · non instrumentées")
        )
        self.action_line.setText(
            f"Actions récentes : {len(data.get('actions') or [])}"
            f"  ·  incertains : {data.get('unresolved_visible', 0)}"
            f"  ·  protections : {data.get('guarded_visible', 0)}"
        )
        self.update_model(self._last_model)

    def update_live_event(self, event: dict) -> None:
        """Local Qt-worker signal; shows real activity even with shadow OFF."""
        if not isinstance(event, dict):
            return
        safe = {
            "source": str(event.get("source") or "inconnu")[:50],
            "tools": [str(x)[:70] for x in
                      list(event.get("tools") or [])[:8]],
            "count": max(0, min(100, int(event.get("count") or 0))),
            "success": event.get("success") is True,
            "verified": event.get("verified") is True,
        }
        self._live_events.appendleft(safe)
        lines = []
        for item in self._live_events:
            title = "OBSERVÉ" if item["success"] else "ÉCHEC / INCOMPLET"
            color = "#70dbbe" if item["success"] else "#f8a4a4"
            # A working tool is not independent evidence that the goal is met.
            tools_text = ", ".join(item["tools"]) or "conversation / raisonnement"
            lines.append(
                f'<p style="margin-bottom:7px">'
                f'<span style="color:{color}">{title}</span> '
                f'<b>{_escape(item["source"], 50)}</b><br>'
                f'<span style="color:#abcad5">{_escape(tools_text, 190)}</span><br>'
                '<span style="color:#eec28c">objectif : non vérifié</span>'
                '</p>'
            )
        self._render_html("live", "".join(lines))

    def update_phase(self, phase: str) -> None:
        allowed = {
            "starting": "INITIALISATION", "calibrating": "CALIBRATION",
            "idle": "EN VEILLE", "armed": "EN ÉCOUTE DE RÉVEIL",
            "wake": "RÉVEIL", "listening": "ÉCOUTE",
            "transcribing": "TRANSCRIPTION", "understanding": "COMPRÉHENSION",
            "thinking": "RAISONNEMENT", "acting": "ACTION",
            "speaking": "RÉPONSE", "success": "TERMINÉ",
            "error": "ERREUR",
        }
        self.phase_line.setText(
            "État réel : " + allowed.get(str(phase), "NON RENSEIGNÉ")
        )

    def update_model(self, data: dict) -> None:
        self._last_model = dict(data or {})
        name = str(self._last_model.get("provider") or "non renseigné")[:40]
        effective = str(self._last_model.get("effective_provider") or "")[:40]
        used = self._last_model.get("rounds_used")
        budget = self._last_model.get("rounds_limit")
        suffix = (
            f" · cycles {used}/{budget}"
            if isinstance(used, int) and isinstance(budget, int)
            else " · budget non mesuré"
        )
        err = str(self._last_model.get("failure_category") or "").strip()
        if err:
            suffix += f" · erreur : {err[:35]}"
        if effective and effective != name:
            suffix += f" · réponse via {effective}"
        usage = self._last_model.get("reported_usage")
        if isinstance(usage, dict) and isinstance(usage.get("total_tokens"), int):
            suffix += f" · tokens API {usage['total_tokens']}"
        self.model_line.setText(f"Modèle : {name}{suffix}")
