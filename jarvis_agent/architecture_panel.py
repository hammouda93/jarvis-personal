"""Inspectable architecture, agent responsibilities and real configuration.

All interactions are local projections or navigation signals. No agent,
network session, tool grant or model request is started by this panel.
"""
from __future__ import annotations

import html

from PySide6.QtCore import Qt, Signal, QRectF, QPointF
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPainterPath
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QSizePolicy,
    QSplitter, QTabWidget, QTextBrowser, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .architecture_catalog import build_snapshot


_MODES = {"shared_runtime": "Runtime partagé", "declared": "Rôle déclaré", "planned": "Planifié"}
_STAGES = {"integrated": "Intégré", "in_progress": "En développement", "planned": "Planifié"}
_RISKS = {"read": "lecture", "reversible": "modification réversible",
          "external_side_effect": "effet externe", "destructive": "destruction", "unknown": "à définir"}


def _escape(value):
    return html.escape(str(value), quote=True)


class ArchitectureMap(QWidget):
    """Code-native diagram of authority and execution, not simulated activity."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(410)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAccessibleName("Architecture Jarvis : orchestration, spécialistes, outils, preuves et mémoire")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#061725"))
        painter.setFont(QFont("Segoe UI", 9))
        margin, gap = 12, 10
        width = self.width() - 2 * margin
        compact = self.width() < 340
        nodes = [
            (QRectF(margin, 8, width, 51), "JARVIS · interlocuteur unique", "Cerveaux en cascade · conversation", "#63d9d4"),
            (QRectF(margin, 79, width, 51), "MISSION · objectif et contraintes", "Plan · contexte · permissions" if compact else "Plan · contexte utile · budget · permissions", "#7dc2ed"),
            (QRectF(margin, 151, (width-gap)/2, 58), "Spécialistes", "Web · Windows\nRecherche" if compact else "Browser · Windows\nRecherche · Documents", "#7dc2ed"),
            (QRectF(margin+(width+gap)/2, 151, (width-gap)/2, 58), "Agents métier", "MS Football\nAutres domaines" if compact else "MS Football\nAutres domaines à définir", "#c6adf4"),
            (QRectF(margin, 232, width, 51), "OUTILS · exécution contrôlée", "Natifs · MCP/API autorisés" if compact else "Natifs · MCP/API · autorisations existantes", "#80cba7"),
            (QRectF(margin, 305, width, 51), "PREUVES · vérifier" if compact else "PREUVES · vérifier puis poursuivre", "Observer · adapter · suspendre" if compact else "Observer · replanifier · suspendre si incertain", "#e9c586"),
            (QRectF(margin, 375, width, 28), "SQLite · mission · Skills" if compact else "Mémoire SQLite · mission · Skills séparés", "", "#8ba8c0"),
        ]
        painter.setPen(QPen(QColor("#416176"), 1.3))
        for start, end in ((0, 1), (1, 2), (1, 3), (2, 4), (3, 4), (4, 5), (5, 6)):
            a, b = nodes[start][0], nodes[end][0]
            source, target = QPointF(a.center().x(), a.bottom()), QPointF(b.center().x(), b.top()-3)
            path = QPainterPath(source)
            path.cubicTo(QPointF(source.x(), source.y()+10), QPointF(target.x(), target.y()-10), target)
            painter.drawPath(path)
            painter.drawLine(target, target+QPointF(-3, -5))
            painter.drawLine(target, target+QPointF(3, -5))
        feedback = QPainterPath(QPointF(margin, nodes[5][0].center().y()))
        feedback.cubicTo(QPointF(2, 330), QPointF(2, 104), QPointF(margin, 104))
        painter.drawPath(feedback)
        for rect, title, detail, color in nodes:
            painter.setPen(QPen(QColor(color), 1))
            painter.setBrush(QColor("#0b2739"))
            painter.drawRoundedRect(rect, 7, 7)
            painter.setPen(QColor(color))
            painter.setFont(QFont("Segoe UI", 9, QFont.DemiBold))
            title_rect = QRectF(rect.left()+5, rect.top()+3, rect.width()-10, 22 if detail else rect.height()-6)
            painter.drawText(title_rect, Qt.AlignCenter | Qt.TextWordWrap, title)
            if detail:
                painter.setFont(QFont("Segoe UI", 8))
                painter.setPen(QColor("#c3d9e2"))
                painter.drawText(QRectF(rect.left()+4, rect.top()+25, rect.width()-8, rect.height()-27),
                                 Qt.AlignCenter | Qt.TextWordWrap, detail)


class ArchitecturePanel(QFrame):
    section_requested = Signal(str)
    review_requested = Signal(str, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("architecturePanel")
        self._data = {}
        self._render_cache = {}
        self._agents = []
        self._tree_signature = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        title = QLabel("JARVIS · ARCHITECTURE ET CAPACITÉS")
        title.setWordWrap(True)
        layout.addWidget(title)
        self.tabs = QTabWidget()
        self.tabs.setUsesScrollButtons(True)
        layout.addWidget(self.tabs, 1)
        overview = QWidget()
        overview.setObjectName("architectureOverview")
        overview.setMinimumWidth(0)
        overview.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        overview_layout = QVBoxLayout(overview)
        overview_layout.setContentsMargins(6, 6, 6, 6)
        self.notice = QLabel("Un runtime partagé aujourd’hui. Spécialistes isolés : évolution progressive.")
        self.notice.setWordWrap(True)
        overview_layout.addWidget(self.notice)
        overview_layout.addWidget(ArchitectureMap())
        self.feature_view = QTextBrowser()
        self.feature_view.setMinimumHeight(130)
        self.feature_view.setMaximumHeight(210)
        overview_layout.addWidget(self.feature_view)
        self.block_label = QLabel()
        self.block_label.setTextFormat(Qt.PlainText)
        self.block_label.setWordWrap(True)
        overview_layout.addWidget(self.block_label)
        self.review_button = QPushButton("Examiner le blocage MCP")
        self.review_button.clicked.connect(self._request_review)
        overview_layout.addWidget(self.review_button)
        navigation = QGridLayout()
        navigation.setContentsMargins(0, 0, 0, 0)
        navigation.setSpacing(4)
        for index, (label, section) in enumerate((("Missions", "control"), ("Cerveaux", "brains"), ("Connexions", "mcp"))):
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, target=section: self.section_requested.emit(target))
            navigation.addWidget(button, index // 2, index % 2, 1, 2 if index == 2 else 1)
        overview_layout.addLayout(navigation)
        preferences = QPushButton("Préférences Skills / Learning")
        preferences.clicked.connect(lambda: self.section_requested.emit("skills"))
        overview_layout.addWidget(preferences)
        overview_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(overview)
        self.tabs.addTab(scroll, "Architecture")

        agents_page = QWidget()
        agents_layout = QVBoxLayout(agents_page)
        agents_layout.setContentsMargins(5, 5, 5, 5)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Agent, compétence, outil ou service…")
        self.search.textChanged.connect(self._filter_agents)
        agents_layout.addWidget(self.search)
        splitter = QSplitter(Qt.Vertical)
        self.agent_tree = QTreeWidget()
        self.agent_tree.setHeaderLabels(["Agent / rôle", "Exécution"])
        self.agent_tree.setRootIsDecorated(False)
        self.agent_tree.setMinimumHeight(150)
        self.agent_tree.setColumnWidth(0, 160)
        self.agent_tree.currentItemChanged.connect(self._select_agent)
        self.agent_detail = QTextBrowser()
        self.agent_detail.setOpenExternalLinks(False)
        self.agent_detail.setMinimumHeight(170)
        splitter.addWidget(self.agent_tree)
        splitter.addWidget(self.agent_detail)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([260, 520])
        agents_layout.addWidget(splitter, 1)
        self.tabs.addTab(agents_page, "Agents et outils")

        self.roadmap_view = QTextBrowser()
        self.roadmap_view.setOpenExternalLinks(False)
        self.tabs.addTab(self.roadmap_view, "Développement")
        self.setStyleSheet("""
            QFrame#architecturePanel {background:#061725;color:#cfebf0;border:0;}
            QWidget#architectureOverview,QScrollArea {background:#061725;border:0;}
            QLabel {color:#c3e5ed;font-size:11px;}
            QLineEdit,QTreeWidget,QTextBrowser {background:#081f30;color:#d5eaf0;
                border:1px solid #23465a;border-radius:5px;font-size:11px;}
            QHeaderView::section {background:#103246;color:#b7dce5;padding:5px;border:0;}
            QTabWidget::pane {border:0;} QTabBar::tab {background:#0b293d;color:#a5cbd6;padding:7px;}
            QTabBar::tab:selected {background:#17495b;color:#a1f3e9;}
            QPushButton {background:#10394b;color:#a0e8df;border:1px solid #286278;border-radius:5px;padding:6px;font-size:11px;}
            QPushButton:disabled {color:#738b9a;}
        """)
        self.apply_snapshot({})

    def apply_snapshot(self, runtime: dict) -> None:
        self._data = build_snapshot(runtime)
        self._agents = self._data["agents"]
        self._set_html(self.feature_view, "features", "<b>Configuration de ce processus</b><br>" + "<br>".join(
            _escape(row["name"]) + " : <b>" + ("activé" if row["enabled"] else "désactivé") + "</b>"
            for row in self._data["features"]) + "<p>Activé ne signifie pas validé en conditions réelles.</p>")
        blocks = self._data["blocks"]
        self.block_label.setText("\n".join(
            "MCP · " + row["server"] + "/" + row["tool"] + " · tentative #" + str(row["attempt_id"])
            + (" à examiner" if row["reviewable"] else " peut encore être en cours")
            for row in blocks[:4]) or "Aucun verrou MCP dans le journal affiché.")
        self.review_button.setVisible(bool(blocks))
        self.review_button.setEnabled(any(b["reviewable"] and type(b["attempt_id"]) is int for b in blocks))
        signature = repr(self._agents)
        if signature != self._tree_signature:
            selected = self.agent_tree.currentItem()
            selected_id = selected.data(0, Qt.UserRole) if selected else "interaction"
            self.agent_tree.blockSignals(True)
            self.agent_tree.clear()
            current = None
            for agent in self._agents:
                item = QTreeWidgetItem([agent["name"], _MODES[agent["mode"]]])
                item.setData(0, Qt.UserRole, agent["id"])
                item.setToolTip(0, agent["purpose"])
                item.setToolTip(1, agent["module"])
                self.agent_tree.addTopLevelItem(item)
                if agent["id"] == selected_id:
                    current = item
            self.agent_tree.setCurrentItem(current or self.agent_tree.topLevelItem(0))
            self.agent_tree.blockSignals(False)
            self._tree_signature = signature
            self._filter_agents(self.search.text())
            self._select_agent(self.agent_tree.currentItem())
        roadmap = self._data["roadmap"]
        priority = roadmap["development_priority"]
        self._set_html(self.roadmap_view, "roadmap", "<h3>Priorité actuelle</h3><p>" + _escape(priority["title"])
            + "</p><p>" + _escape(priority["next"]) + "</p><h3>Étapes du projet</h3>" + "".join(
                "<p><b>" + str(stage["number"]) + ". " + _escape(stage["title"]) + "</b><br>"
                + _STAGES.get(stage["implementation"], _escape(stage["implementation"]))
                + " · tests : " + _escape(stage["automated"]) + "<br>Recette Windows complète : "
                + _escape(stage["windows_real"]) + "</p>" for stage in roadmap["stages"])
            + "<p>" + _escape(roadmap["notice"]) + "</p>")

    def _set_html(self, view: QTextBrowser, key: str, content: str) -> None:
        if self._render_cache.get(key) == content:
            return
        position = view.verticalScrollBar().value()
        view.setHtml(content)
        view.verticalScrollBar().setValue(position)
        self._render_cache[key] = content

    def _filter_agents(self, text: str) -> None:
        query = text.strip().casefold()
        for index, agent in enumerate(self._agents):
            # Explicit metadata fields only; never search private tool outputs.
            haystack = " ".join([agent["name"], agent["purpose"], *agent["native_tools"],
                *agent["declared_tools"], *agent["connectors"],
                *[c["name"] for c in agent["capabilities"]],
                *[t["name"] for s in agent["connections"] for t in s["tools"]]]).casefold()
            self.agent_tree.topLevelItem(index).setHidden(bool(query and query not in haystack))

    def _select_agent(self, current, previous=None) -> None:
        if current is None:
            return
        agent = next((a for a in self._agents if a["id"] == current.data(0, Qt.UserRole)), None)
        if agent is None:
            return
        blocks = ["<h3>" + _escape(agent["name"]) + "</h3>", "<p>" + _escape(agent["purpose"]) + "</p>",
            "<p><b>" + _MODES[agent["mode"]] + "</b><br>" + _escape(agent["module"]) + "</p>",
            "<p>Ce panneau ne démarre aucun worker indépendant. Les outils utilisent leurs contrôles existants.</p>",
            "<h4>Compétences du manifeste ou de la cible</h4>"]
        for capability in agent["capabilities"]:
            blocks.append("<p><b>" + _escape(capability["name"]) + "</b><br>" + _escape(capability["description"])
                + "<br>Risque déclaré : " + _escape(_RISKS.get(capability["risk"], capability["risk"]))
                + (" · confirmation requise" if capability["requires_confirmation"] else "") + "</p>")
        if agent["native_tools"]:
            blocks.append("<h4>Outils intégrés au runtime partagé</h4><p>" + "<br>".join(map(_escape, agent["native_tools"])) + "</p>")
        if agent["declared_tools"]:
            blocks.append("<h4>Outils déclarés du profil</h4><p>" + "<br>".join(map(_escape, agent["declared_tools"])) + "</p>")
        if agent["connectors"]:
            blocks.append("<h4>Connecteurs déclarés</h4><p>" + ", ".join(map(_escape, agent["connectors"])) + "</p>")
        for server in agent["connections"]:
            blocks.append("<h4>Service : " + _escape(server["id"]) + "</h4><p>" + _escape(server["connection_state"])
                + " · " + ("session observée ouverte" if server["connected_now"] else "aucune session permanente attestée") + "</p>")
            blocks.extend("<p>" + _escape(tool["name"]) + " · " + (
                "autorisé dans la configuration active" if tool["effective"] else "non exposé / service désactivé") + "</p>"
                for tool in server["tools"])
            if server["inventory_partial"]:
                blocks.append("<p>Inventaire affiché partiel.</p>")
        blocks.append("<h4>Mémoire</h4><p>Scopes déclarés : " + _escape(", ".join(agent["memory_scopes"]) or "à définir")
            + ". La mémoire personnelle, le contexte de mission et les Skills restent distincts.</p><p>"
            + _escape(self._data["notice"]) + "</p>")
        self.agent_detail.setHtml("".join(blocks))

    def _request_review(self) -> None:
        block = next((b for b in self._data.get("blocks", [])
                      if b["reviewable"] and type(b["attempt_id"]) is int), None)
        if block:
            self.review_requested.emit(block["server"], block["attempt_id"])
