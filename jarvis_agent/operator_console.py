"""Compact futuristic operator console: real observations, never demo values."""
from __future__ import annotations

import html

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QScrollArea, QTextBrowser,
    QVBoxLayout, QWidget,
)

from .operator_telemetry import snapshot


_STATUS = {
    "dispatched": ("EN COURS / À VÉRIFIER", "#f7c679"),
    "unknown": ("INCERTAIN", "#f8a4a4"),
    "succeeded": ("OUTIL OK", "#70dbbe"),
    "failed": ("ÉCHEC", "#f8a4a4"),
    "resolved": ("REVU MANUELLEMENT", "#94c6f9"),
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
            + f'<br><span style="color:#779bad">{_escape(x.get("id"), 28)}…</span></p>'
            for x in actions
        )
    return mission_html, step_html, action_html


class OperatorConsole(QFrame):
    """Observable UI-only projection, with no control/write API."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("operatorConsole")
        self.setMinimumWidth(320)
        self._last_model = {}
        self._last_snapshot = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(13, 13, 13, 13)
        layout.setSpacing(8)

        title = QLabel("◈   CENTRE DE CONTRÔLE")
        title.setObjectName("operatorHeading")
        subtitle = QLabel("ÉTATS RÉELS  ·  AUCUNE ACTION AUTOMATIQUE")
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
        for line in (self.mode_line, self.mission_line, self.action_line, self.model_line, self.capabilities_line):
            line.setObjectName("operatorMetric")
            line.setWordWrap(True)
            sum_layout.addWidget(line)
        layout.addWidget(summary)

        self._views = {}
        for key, label in [
            ("missions", "◉   MISSIONS / OBJECTIFS"),
            ("tasks", "◇   ÉTAPES / SUPERVISION"),
            ("actions", "⚙   OUTILS / VÉRIFICATIONS"),
        ]:
            group = QFrame()
            group.setObjectName("operatorGroup")
            group_layout = QVBoxLayout(group)
            group_layout.setContentsMargins(9, 8, 9, 7)
            group_layout.setSpacing(4)
            header = QLabel(label)
            header.setObjectName("operatorSection")
            view = QTextBrowser()
            view.setOpenExternalLinks(False)
            view.setObjectName("operatorEvents")
            view.setMinimumHeight(76)
            view.setMaximumHeight(168)
            self._views[key] = view
            group_layout.addWidget(header)
            group_layout.addWidget(view)
            layout.addWidget(group)
        layout.addStretch(1)
        self.setStyleSheet("""
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
            QTextBrowser#operatorEvents {background:transparent;border:none;
                color:#ccedf2;font-size:9px;selection-background-color:#174d60;}
        """)
        self.apply_snapshot(snapshot())

    def apply_snapshot(self, data: dict) -> None:
        self._last_snapshot = dict(data)
        missions, steps, actions = render_snapshot(data)
        self._views["missions"].setHtml(missions)
        self._views["tasks"].setHtml(steps)
        self._views["actions"].setHtml(actions)
        mission_on = bool(data.get("mission_enabled"))
        reliability_on = bool(data.get("reliability_enabled"))
        kernel_mode = "observateur" if data.get("kernel_shadow_enabled") else "non connecté"
        self.mode_line.setText(
            f"Kernel : {kernel_mode}  ·  Missions : {'ON' if mission_on else 'OFF'}"
            f"  ·  Fiabilité : {'ON' if reliability_on else 'OFF'}"
        )
        self.capabilities_line.setText(
            f"Mémoire V5 : {'ON' if data.get('semantic_memory_enabled') else 'OFF'}"
            f"  ·  Navigateur : {'ON' if data.get('browser_core_enabled') else 'OFF'}"
            f"  ·  PC : {'ON' if data.get('computer_core_enabled') else 'OFF'}"
            f"  ·  Apprentissage : {'ON' if data.get('learning_enabled') else 'OFF'}"
        )
        self.mission_line.setText(
            f"Missions observées : {len(data.get('missions') or [])}"
            + (" · échantillon récent" if mission_on else " · non instrumentées")
        )
        self.action_line.setText(
            f"Actions récentes : {len(data.get('actions') or [])}"
            f"  ·  états incertains affichés : {data.get('unresolved_visible', 0)}"
        )
        self.update_model(self._last_model)

    def update_model(self, data: dict) -> None:
        self._last_model = dict(data or {})
        name = str(self._last_model.get("provider") or "non renseigné")[:40]
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
        self.model_line.setText(f"Modèle : {name}{suffix}")
