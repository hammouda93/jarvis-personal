from __future__ import annotations

import math
import random
import re
import sys
from collections import deque
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QKeyEvent,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .assistant_v3 import AssistantWorker
from .config import settings
from .states import AssistantState, STATE_LABELS


def _safe_console_log(value: object) -> None:
    """Write logs without letting Windows console encoding crash Jarvis."""
    text = str(value).replace("\u202f", " ").replace("\u00a0", " ")
    stream = sys.stdout
    encoding = getattr(stream, "encoding", None) or "utf-8"
    safe = text.encode(
        encoding,
        errors="backslashreplace",
    ).decode(
        encoding,
        errors="strict",
    )
    stream.write(safe + "\n")
    try:
        stream.flush()
    except Exception:
        pass


STATE_COLORS: dict[str, QColor] = {
    AssistantState.STARTING.value: QColor(63, 154, 196),
    AssistantState.CALIBRATING.value: QColor(72, 181, 205),
    AssistantState.IDLE.value: QColor(49, 98, 122),
    AssistantState.ARMED.value: QColor(66, 229, 232),
    AssistantState.WAKE.value: QColor(173, 251, 255),
    AssistantState.LISTENING.value: QColor(59, 235, 246),
    AssistantState.TRANSCRIBING.value: QColor(83, 210, 255),
    AssistantState.UNDERSTANDING.value: QColor(84, 201, 255),
    AssistantState.THINKING.value: QColor(102, 221, 255),
    AssistantState.ACTING.value: QColor(75, 242, 210),
    AssistantState.SPEAKING.value: QColor(96, 227, 255),
    AssistantState.SUCCESS.value: QColor(103, 255, 206),
    AssistantState.ERROR.value: QColor(255, 100, 126),
}


@dataclass(frozen=True)
class VisualNode:
    key: str
    label: str
    subtitle: str
    x: float
    y: float
    badge: str


NODES: tuple[VisualNode, ...] = (
    VisualNode("voice", "Votre voix", "Microphone", 0.10, 0.47, "MIC"),
    VisualNode("conversation", "Conversation", "Chat texte", 0.13, 0.72, "TXT"),
    VisualNode("understand", "Comprendre", "Whisper / STT", 0.28, 0.22, "STT"),
    VisualNode("think", "Réfléchir", "Cerebras", 0.50, 0.13, "AI"),
    VisualNode("mission", "Mission", "Objectif & plan", 0.68, 0.19, "M"),
    VisualNode("context", "Contexte", "Situation actuelle", 0.83, 0.25, "CTX"),
    VisualNode("memory", "Mémoire", "Historique & préférences", 0.27, 0.49, "MEM"),
    VisualNode("internet", "Internet", "Recherche arrière-plan", 0.78, 0.44, "WEB"),
    VisualNode("browser", "Navigateur", "Pages & onglets", 0.89, 0.61, "NAV"),
    VisualNode("windows", "Votre PC", "Fenêtres & applications", 0.75, 0.72, "PC"),
    VisualNode("ms_football", "MS Football", "Vos données", 0.62, 0.88, "MS"),
    VisualNode("verify", "Vérifier", "Résultat observé", 0.49, 0.88, "OK"),
    VisualNode("respond", "Vous répondre", "Voix & texte", 0.27, 0.87, "OUT"),
)

NODE_BY_KEY = {node.key: node for node in NODES}


TOOL_NODE_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^(research_web|search_web)$"), "internet"),
    (re.compile(r"^(open_web_search|open_url|browser\.)"), "browser"),
    (re.compile(r"^msf_"), "ms_football"),
    (
        re.compile(
            r"^(open_application|app\.|inspect_active_window|observe_screen|"
            r"click_ui_element|click_visual_target|write_ui_element|"
            r"write_visual_target|type_text_active_window|press_key|"
            r"close_window|close_tab|folder\.|open_file)"
        ),
        "windows",
    ),
    (
        re.compile(
            r"^(remember_information|recall_information|search_agent_knowledge|"
            r"save_verified_skill|save_feedback_lesson)"
        ),
        "memory",
    ),
)


def _tool_visual_node(tool_name: str) -> str:
    value = (tool_name or "").strip()
    for pattern, node in TOOL_NODE_RULES:
        if pattern.search(value):
            return node
    return "think"


class PersonalJarvisCanvas(QWidget):
    """Pure visual projection of the runtime signals already emitted by Jarvis."""

    route_changed = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(760, 560)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self._phase = 0.0
        self._state = AssistantState.STARTING.value
        self._level_target = 0.0
        self._level = 0.0
        self._route: list[str] = []
        self._current_tool = ""
        self._saw_action = False
        self._animations_frozen = False
        self._source_mode = "voice"
        self._history = deque([0.0] * 64, maxlen=64)

        rng = random.Random(42)
        self._stars = tuple(
            (
                rng.random(),
                rng.random(),
                rng.uniform(0.45, 1.8),
                rng.randint(30, 160),
            )
            for _ in range(110)
        )

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)

    @property
    def route(self) -> tuple[str, ...]:
        return tuple(self._route)

    def set_animations_frozen(self, frozen: bool) -> None:
        self._animations_frozen = bool(frozen)
        self.update()

    def set_source_mode(self, mode: str) -> None:
        self._source_mode = "conversation" if mode == "conversation" else "voice"

    def begin_request(self, source: str | None = None) -> None:
        if source:
            self.set_source_mode(source)
        self._route = []
        self._current_tool = ""
        self._saw_action = False
        self._append_route(self._source_mode)

    def _append_route(self, key: str) -> None:
        if key not in NODE_BY_KEY:
            return
        if self._route and self._route[-1] == key:
            return
        self._route.append(key)
        if len(self._route) > 10:
            self._route = self._route[-10:]
        self.route_changed.emit(tuple(self._route))
        self.update()

    def set_state(self, state: str) -> None:
        self._state = state

        if state == AssistantState.LISTENING.value:
            if not self._route or self._route[-1] == "respond":
                self.begin_request("voice")
            else:
                self._append_route("voice")
        elif state == AssistantState.TRANSCRIBING.value:
            self._append_route("understand")
        elif state == AssistantState.UNDERSTANDING.value:
            self._append_route("understand")
        elif state == AssistantState.THINKING.value:
            self._append_route("think")
        elif state == AssistantState.SPEAKING.value:
            self._append_route("respond")
        elif state == AssistantState.SUCCESS.value and self._saw_action:
            if "verify" not in self._route[-2:]:
                self._append_route("verify")

        self.update()

    def set_audio_level(self, level: float) -> None:
        self._level_target = max(0.0, min(1.0, float(level)))

    def set_transcript(self, text: str) -> None:
        if text:
            # Voice transcript is a genuine new request boundary in the current runtime.
            self.begin_request("voice")
            self._append_route("understand")

    def ingest_detail(self, detail: str) -> None:
        if detail and detail != "Conversation":
            self._saw_action = True

    def ingest_log(self, line: object) -> None:
        text = str(line)

        if text.startswith("[AGENT] provider="):
            self._append_route("think")

        if text.startswith("[KERNEL_SHADOW] mission="):
            self._append_route("mission")

        if text.startswith("[KNOWLEDGE]"):
            self._append_route("memory")

        if text.startswith("[RESEARCH] announce"):
            self._append_route("internet")

        match = re.search(r"\[AGENT_TOOL\] call=([^\s]+)", text)
        if match:
            tool_name = match.group(1).strip()
            self._current_tool = tool_name
            self._saw_action = True
            self._append_route(_tool_visual_node(tool_name))

        result = re.search(
            r"\[AGENT_TOOL\] result=([^\s]+)\s+success=(True|False)",
            text,
        )
        if result:
            self._saw_action = True
            if result.group(2) == "True":
                self._append_route("verify")

        if text.startswith("[DIRECT] simple="):
            self._saw_action = True
            direct = re.search(r"simple=([^\s]+)", text)
            if direct:
                self._append_route(_tool_visual_node(direct.group(1)))
                if "success=True" in text:
                    self._append_route("verify")

        self.update()

    def _tick(self) -> None:
        if not self._animations_frozen:
            speed = {
                AssistantState.IDLE.value: 0.006,
                AssistantState.LISTENING.value: 0.018,
                AssistantState.TRANSCRIBING.value: 0.026,
                AssistantState.UNDERSTANDING.value: 0.030,
                AssistantState.THINKING.value: 0.040,
                AssistantState.ACTING.value: 0.034,
                AssistantState.SPEAKING.value: 0.020,
                AssistantState.ERROR.value: 0.010,
            }.get(self._state, 0.012)
            self._phase += speed

        self._level += (self._level_target - self._level) * 0.22
        self._level_target *= 0.93
        self._history.append(self._level)
        self.update()

    def _accent(self, alpha: int = 255) -> QColor:
        base = STATE_COLORS.get(self._state, QColor(72, 226, 240))
        color = QColor(base)
        color.setAlpha(max(0, min(255, alpha)))
        return color

    def _node_center(self, node: VisualNode, rect: QRectF) -> QPointF:
        return QPointF(
            rect.left() + rect.width() * node.x,
            rect.top() + rect.height() * node.y,
        )

    def _draw_background(self, painter: QPainter, rect: QRectF) -> None:
        bg = QLinearGradient(rect.topLeft(), rect.bottomRight())
        bg.setColorAt(0.0, QColor(1, 8, 19))
        bg.setColorAt(0.46, QColor(2, 12, 27))
        bg.setColorAt(1.0, QColor(0, 4, 12))
        painter.fillRect(rect, bg)

        nebula = QRadialGradient(
            QPointF(rect.width() * 0.47, rect.height() * 0.48),
            max(rect.width(), rect.height()) * 0.58,
        )
        nebula.setColorAt(0.0, QColor(0, 139, 198, 35))
        nebula.setColorAt(0.30, QColor(0, 85, 135, 18))
        nebula.setColorAt(0.72, QColor(3, 22, 55, 9))
        nebula.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.fillRect(rect, nebula)

        painter.setPen(Qt.NoPen)
        for sx, sy, size, alpha in self._stars:
            shimmer = 0.72 + 0.28 * math.sin(self._phase * 1.7 + sx * 19.0)
            painter.setBrush(QColor(114, 221, 255, int(alpha * shimmer)))
            painter.drawEllipse(
                QPointF(rect.width() * sx, rect.height() * sy),
                size,
                size,
            )

    def _draw_orbits(self, painter: QPainter, center: QPointF, scale: float) -> None:
        painter.save()
        painter.translate(center)

        for index, (rx, ry, rotation) in enumerate(
            (
                (2.55, 0.85, -8),
                (2.10, 1.28, 18),
                (1.78, 1.68, -28),
                (2.65, 1.55, 34),
            )
        ):
            painter.save()
            painter.rotate(rotation + math.sin(self._phase + index) * 2.2)
            pen = QPen(QColor(61, 191, 231, 28 + index * 5))
            pen.setWidthF(1.0)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(
                QRectF(
                    -scale * rx,
                    -scale * ry,
                    scale * rx * 2,
                    scale * ry * 2,
                )
            )
            painter.restore()

        painter.restore()

    def _draw_core(self, painter: QPainter, center: QPointF, radius: float) -> None:
        breathing = 1.0 + math.sin(self._phase * 2.4) * 0.018
        radius *= breathing + self._level * 0.08

        glow = QRadialGradient(center, radius * 2.9)
        glow.setColorAt(0.0, QColor(96, 239, 255, 92))
        glow.setColorAt(0.28, QColor(29, 180, 236, 48))
        glow.setColorAt(0.63, QColor(4, 95, 177, 15))
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(center, radius * 2.9, radius * 2.9)

        shell = QRadialGradient(
            QPointF(center.x() - radius * 0.24, center.y() - radius * 0.28),
            radius * 1.4,
        )
        shell.setColorAt(0.0, QColor(207, 252, 255, 235))
        shell.setColorAt(0.16, QColor(74, 224, 255, 210))
        shell.setColorAt(0.42, QColor(8, 105, 177, 175))
        shell.setColorAt(0.76, QColor(2, 24, 58, 230))
        shell.setColorAt(1.0, QColor(0, 6, 18, 245))
        painter.setBrush(shell)
        painter.setPen(QPen(QColor(86, 226, 255, 180), 1.6))
        painter.drawEllipse(center, radius, radius)

        # Longitude / latitude mesh gives the nucleus a planetary data-core look.
        painter.setBrush(Qt.NoBrush)
        for idx, ratio in enumerate((0.28, 0.52, 0.74)):
            pen = QPen(QColor(89, 226, 255, 55 + idx * 10))
            pen.setWidthF(0.8)
            painter.setPen(pen)
            painter.drawEllipse(
                QRectF(
                    center.x() - radius * ratio,
                    center.y() - radius,
                    radius * ratio * 2,
                    radius * 2,
                )
            )
            painter.drawEllipse(
                QRectF(
                    center.x() - radius,
                    center.y() - radius * ratio,
                    radius * 2,
                    radius * ratio * 2,
                )
            )

        # Saturn-like energy rings.
        for index, factor in enumerate((1.18, 1.42, 1.72)):
            pen = QPen(QColor(75, 211, 255, 80 - index * 14))
            pen.setWidthF(1.8 if index == 0 else 1.1)
            painter.setPen(pen)
            painter.drawEllipse(
                QRectF(
                    center.x() - radius * factor,
                    center.y() - radius * factor * 0.28,
                    radius * factor * 2,
                    radius * factor * 0.56,
                )
            )

        painter.setPen(QColor(226, 252, 255, 245))
        font = QFont("Segoe UI", max(11, int(radius * 0.16)))
        font.setWeight(QFont.DemiBold)
        painter.setFont(font)
        painter.drawText(
            QRectF(
                center.x() - radius * 0.75,
                center.y() - radius * 0.24,
                radius * 1.5,
                radius * 0.34,
            ),
            Qt.AlignCenter,
            "PERSONAL JARVIS",
        )

        painter.setPen(QColor(99, 231, 255, 230))
        font = QFont("Segoe UI", max(8, int(radius * 0.095)))
        font.setLetterSpacing(QFont.AbsoluteSpacing, 2.0)
        painter.setFont(font)
        painter.drawText(
            QRectF(
                center.x() - radius * 0.72,
                center.y() + radius * 0.08,
                radius * 1.44,
                radius * 0.28,
            ),
            Qt.AlignCenter,
            "AI KERNEL · CEREBRAS",
        )

    def _edge_path(
        self,
        start: QPointF,
        end: QPointF,
        center: QPointF,
    ) -> QPainterPath:
        path = QPainterPath(start)
        mid = QPointF((start.x() + end.x()) / 2, (start.y() + end.y()) / 2)
        dx = mid.x() - center.x()
        dy = mid.y() - center.y()
        control = QPointF(mid.x() + dx * 0.18, mid.y() + dy * 0.18)
        path.quadTo(control, end)
        return path

    def _draw_connections(
        self,
        painter: QPainter,
        rect: QRectF,
        center: QPointF,
    ) -> None:
        points = {node.key: self._node_center(node, rect) for node in NODES}

        # Permanent faint neural links.
        for node in NODES:
            target = points[node.key]
            path = self._edge_path(center, target, center)
            painter.setPen(QPen(QColor(46, 155, 210, 23), 0.9))
            painter.drawPath(path)

        # Actual runtime route becomes the bright live path.
        if len(self._route) < 2:
            return

        for index, (left, right) in enumerate(zip(self._route, self._route[1:])):
            start = points.get(left)
            end = points.get(right)
            if start is None or end is None:
                continue
            path = self._edge_path(start, end, center)

            glow_pen = QPen(QColor(44, 225, 255, 55), 7.0)
            glow_pen.setCapStyle(Qt.RoundCap)
            painter.setPen(glow_pen)
            painter.drawPath(path)

            active_pen = QPen(QColor(89, 242, 255, 215), 1.7)
            active_pen.setCapStyle(Qt.RoundCap)
            painter.setPen(active_pen)
            painter.drawPath(path)

            t = (self._phase * 0.72 + index * 0.17) % 1.0
            pulse = path.pointAtPercent(t)
            pulse_glow = QRadialGradient(pulse, 11)
            pulse_glow.setColorAt(0.0, QColor(230, 255, 255, 245))
            pulse_glow.setColorAt(0.28, QColor(74, 236, 255, 210))
            pulse_glow.setColorAt(1.0, QColor(74, 236, 255, 0))
            painter.setPen(Qt.NoPen)
            painter.setBrush(pulse_glow)
            painter.drawEllipse(pulse, 11, 11)

    def _draw_node(
        self,
        painter: QPainter,
        node: VisualNode,
        rect: QRectF,
    ) -> None:
        pos = self._node_center(node, rect)
        active = node.key in self._route[-6:]
        current = bool(self._route and self._route[-1] == node.key)
        base = min(rect.width(), rect.height())
        radius = max(31.0, min(49.0, base * (0.065 if active else 0.056)))

        if active:
            halo = QRadialGradient(pos, radius * 1.85)
            halo.setColorAt(
                0.0,
                QColor(66, 234, 255, 96 if current else 64),
            )
            halo.setColorAt(0.55, QColor(23, 157, 219, 25))
            halo.setColorAt(1.0, QColor(0, 0, 0, 0))
            painter.setPen(Qt.NoPen)
            painter.setBrush(halo)
            painter.drawEllipse(pos, radius * 1.85, radius * 1.85)

        face = QRadialGradient(
            QPointF(pos.x() - radius * 0.23, pos.y() - radius * 0.28),
            radius * 1.25,
        )
        face.setColorAt(
            0.0,
            QColor(18, 55, 81, 245) if active else QColor(13, 33, 53, 236),
        )
        face.setColorAt(1.0, QColor(2, 13, 28, 245))
        painter.setBrush(face)
        painter.setPen(
            QPen(
                QColor(88, 231, 255, 220 if active else 86),
                1.7 if current else 1.1,
            )
        )
        painter.drawEllipse(pos, radius, radius)

        painter.setPen(
            QColor(218, 253, 255, 245)
            if active
            else QColor(170, 209, 223, 210)
        )
        badge_font = QFont("Segoe UI", max(7, int(radius * 0.20)))
        badge_font.setWeight(QFont.Bold)
        painter.setFont(badge_font)
        painter.drawText(
            QRectF(
                pos.x() - radius,
                pos.y() - radius * 0.64,
                radius * 2,
                radius * 0.36,
            ),
            Qt.AlignCenter,
            node.badge,
        )

        title_font = QFont("Segoe UI", max(8, int(radius * 0.25)))
        title_font.setWeight(QFont.DemiBold)
        painter.setFont(title_font)
        painter.drawText(
            QRectF(
                pos.x() - radius * 1.45,
                pos.y() - radius * 0.13,
                radius * 2.9,
                radius * 0.46,
            ),
            Qt.AlignCenter,
            node.label,
        )

        painter.setPen(
            QColor(100, 221, 244, 220)
            if active
            else QColor(101, 151, 174, 190)
        )
        subtitle_font = QFont("Segoe UI", max(6, int(radius * 0.16)))
        painter.setFont(subtitle_font)
        painter.drawText(
            QRectF(
                pos.x() - radius * 1.65,
                pos.y() + radius * 0.34,
                radius * 3.3,
                radius * 0.60,
            ),
            Qt.AlignHCenter | Qt.AlignTop,
            node.subtitle,
        )

        if current:
            ring_r = radius * (1.08 + math.sin(self._phase * 4.0) * 0.035)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor(136, 250, 255, 235), 1.7))
            painter.drawEllipse(pos, ring_r, ring_r)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHints(
            QPainter.Antialiasing
            | QPainter.TextAntialiasing
            | QPainter.SmoothPixmapTransform,
            True,
        )

        rect = QRectF(self.rect())
        self._draw_background(painter, rect)

        center = QPointF(rect.width() * 0.50, rect.height() * 0.53)
        scale = min(rect.width(), rect.height()) * 0.115

        self._draw_orbits(painter, center, scale)
        self._draw_connections(painter, rect, center)
        self._draw_core(painter, center, scale * 1.08)

        for node in NODES:
            self._draw_node(painter, node, rect)


class StatusChip(QFrame):
    def __init__(self, title: str, detail: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("statusChip")
        self.setMinimumWidth(142)

        self.dot = QLabel("●")
        self.dot.setObjectName("chipDot")
        self.title = QLabel(title)
        self.title.setObjectName("chipTitle")
        self.detail = QLabel(detail)
        self.detail.setObjectName("chipDetail")

        texts = QVBoxLayout()
        texts.setContentsMargins(0, 0, 0, 0)
        texts.setSpacing(0)
        texts.addWidget(self.title)
        texts.addWidget(self.detail)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(11, 7, 12, 7)
        layout.setSpacing(8)
        layout.addWidget(self.dot)
        layout.addLayout(texts)

    def set_active(self, active: bool) -> None:
        self.setProperty("active", bool(active))
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()


class FlowPanel(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("flowPanel")
        self.setMinimumWidth(285)
        self.setMaximumWidth(330)

        title = QLabel("Trajet de la demande")
        title.setObjectName("panelTitle")

        self.state = QLabel("● En attente")
        self.state.setObjectName("panelState")

        head = QHBoxLayout()
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self.state)

        self._step_labels: list[QLabel] = []
        steps = QVBoxLayout()
        steps.setSpacing(5)
        for _ in range(8):
            label = QLabel("")
            label.setObjectName("flowStep")
            label.setWordWrap(True)
            label.hide()
            self._step_labels.append(label)
            steps.addWidget(label)

        divider = QFrame()
        divider.setObjectName("divider")
        divider.setFrameShape(QFrame.HLine)

        alternate_title = QLabel("Trajet alternatif")
        alternate_title.setObjectName("sectionTitle")

        alternate = QLabel("Réfléchir  →  Internet  →  Réfléchir")
        alternate.setObjectName("alternateFlow")
        alternate.setWordWrap(True)

        alternate_help = QLabel(
            "Recherche d'informations en arrière-plan si une source externe "
            "est réellement nécessaire."
        )
        alternate_help.setObjectName("panelMuted")
        alternate_help.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)
        layout.addLayout(head)
        layout.addLayout(steps)
        layout.addStretch(1)
        layout.addWidget(divider)
        layout.addWidget(alternate_title)
        layout.addWidget(alternate)
        layout.addWidget(alternate_help)

    def set_route(self, route: tuple[str, ...] | list[str]) -> None:
        visible = list(route)[-8:]
        for idx, label in enumerate(self._step_labels):
            if idx >= len(visible):
                label.hide()
                continue
            node = NODE_BY_KEY.get(visible[idx])
            if node is None:
                label.hide()
                continue
            prefix = "●" if idx == len(visible) - 1 else "✓"
            label.setText(
                f"{idx + 1:02d}   {node.label}\n"
                f"      {node.subtitle}   {prefix}"
            )
            label.setProperty("current", idx == len(visible) - 1)
            label.style().unpolish(label)
            label.style().polish(label)
            label.show()

        self.state.setText("● En cours…" if visible else "● En attente")


class JarvisWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Personal Jarvis")
        self.setMinimumSize(1180, 720)
        self.resize(1500, 900)
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.setObjectName("root")
        self._drag_position = None
        self._close_requested = False
        self._allow_close = False

        self.canvas = PersonalJarvisCanvas(self)
        self.flow_panel = FlowPanel(self)

        title = QLabel("PERSONAL JARVIS")
        title.setObjectName("brandTitle")
        tagline = QLabel(
            "Personal AI Agent réellement intelligent, généraliste, robuste et évolutif"
        )
        tagline.setObjectName("brandTagline")

        brand = QVBoxLayout()
        brand.setContentsMargins(0, 0, 8, 0)
        brand.setSpacing(1)
        brand.addWidget(title)
        brand.addWidget(tagline)

        self.chip_cerebras = StatusChip("Cerebras", "Connecté")
        self.chip_windows = StatusChip("Windows", "Disponible")
        self.chip_msf = StatusChip("MS Football", "Prêt")
        self.chip_research = StatusChip("Recherche arrière-plan", "En veille")

        chips = QHBoxLayout()
        chips.setSpacing(8)
        chips.addWidget(self.chip_cerebras)
        chips.addWidget(self.chip_windows)
        chips.addWidget(self.chip_msf)
        chips.addWidget(self.chip_research)

        self.clean_button = QPushButton("◉  Vue épurée")
        self.clean_button.setObjectName("topButton")
        self.clean_button.setCheckable(True)

        self.freeze_button = QPushButton("Ⅱ  Figer les animations")
        self.freeze_button.setObjectName("topButton")
        self.freeze_button.setCheckable(True)

        self.min_button = QPushButton("—")
        self.min_button.setObjectName("windowButton")
        self.max_button = QPushButton("□")
        self.max_button.setObjectName("windowButton")
        self.close_button = QPushButton("×")
        self.close_button.setObjectName("windowButton")

        controls = QHBoxLayout()
        controls.setSpacing(6)
        controls.addWidget(self.clean_button)
        controls.addWidget(self.freeze_button)
        controls.addSpacing(6)
        controls.addWidget(self.min_button)
        controls.addWidget(self.max_button)
        controls.addWidget(self.close_button)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(14)
        header.addLayout(brand)
        header.addStretch(1)
        header.addLayout(chips)
        header.addStretch(1)
        header.addLayout(controls)

        middle = QHBoxLayout()
        middle.setSpacing(14)
        middle.addWidget(self.canvas, 1)
        middle.addWidget(self.flow_panel)

        self.status_label = QLabel("Initialisation de Personal Jarvis…")
        self.status_label.setObjectName("liveStatus")
        self.status_label.setAlignment(Qt.AlignCenter)

        self.transcript_label = QLabel("")
        self.transcript_label.setObjectName("transcript")
        self.transcript_label.setWordWrap(True)
        self.transcript_label.setAlignment(Qt.AlignCenter)

        self.detail_label = QLabel("")
        self.detail_label.setObjectName("detail")
        self.detail_label.setAlignment(Qt.AlignCenter)

        status_stack = QVBoxLayout()
        status_stack.setSpacing(2)
        status_stack.addWidget(self.status_label)
        status_stack.addWidget(self.transcript_label)
        status_stack.addWidget(self.detail_label)

        self.conversation_button = QPushButton("▣  Conversation")
        self.conversation_button.setObjectName("modeButton")
        self.conversation_button.setCheckable(True)
        self.voice_button = QPushButton("◉  Voix")
        self.voice_button.setObjectName("modeButton")
        self.voice_button.setCheckable(True)
        self.voice_button.setChecked(True)

        self.text_input = QLineEdit()
        self.text_input.setObjectName("conversationInput")
        self.text_input.setPlaceholderText(
            "Conversation texte — interface prête; moteur vocal inchangé"
        )
        self.text_input.setReadOnly(True)
        self.text_input.setToolTip(
            "Cette branche modifie uniquement l'interface. "
            "Le moteur de conversation texte n'est volontairement pas modifié."
        )

        self.send_button = QPushButton("➜")
        self.send_button.setObjectName("sendButton")
        self.send_button.setEnabled(False)
        self.send_button.setToolTip(
            "Envoi texte non connecté dans cette branche UI-only."
        )

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        bottom.addWidget(self.conversation_button)
        bottom.addWidget(self.voice_button)
        bottom.addWidget(self.text_input, 1)
        bottom.addWidget(self.send_button)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 18, 24, 18)
        root.setSpacing(10)
        root.addLayout(header)
        root.addLayout(middle, 1)
        root.addLayout(status_stack)
        root.addLayout(bottom)

        self.setStyleSheet(
            """
            QWidget#root {
                background: #020813;
                color: #dff9ff;
            }
            QLabel#brandTitle {
                color: #f0fdff;
                font-size: 20px;
                font-weight: 650;
                letter-spacing: 3px;
            }
            QLabel#brandTagline {
                color: rgba(130, 215, 238, 205);
                font-size: 10px;
            }
            QFrame#statusChip {
                background: rgba(4, 19, 35, 205);
                border: 1px solid rgba(53, 141, 178, 90);
                border-radius: 13px;
            }
            QFrame#statusChip[active="true"] {
                border: 1px solid rgba(80, 239, 245, 210);
                background: rgba(3, 35, 49, 230);
            }
            QLabel#chipDot {
                color: #59f5da;
                font-size: 12px;
            }
            QLabel#chipTitle {
                color: rgba(232, 251, 255, 240);
                font-size: 11px;
                font-weight: 600;
            }
            QLabel#chipDetail {
                color: rgba(105, 179, 205, 190);
                font-size: 9px;
            }
            QPushButton#topButton,
            QPushButton#windowButton,
            QPushButton#modeButton,
            QPushButton#sendButton {
                color: rgba(209, 247, 255, 235);
                background: rgba(4, 18, 33, 215);
                border: 1px solid rgba(64, 151, 190, 95);
                border-radius: 14px;
                padding: 8px 13px;
            }
            QPushButton#topButton:hover,
            QPushButton#modeButton:hover,
            QPushButton#windowButton:hover {
                border-color: rgba(77, 229, 244, 180);
                background: rgba(6, 32, 50, 235);
            }
            QPushButton#topButton:checked,
            QPushButton#modeButton:checked {
                border-color: rgba(69, 239, 247, 230);
                color: #dffeff;
                background: rgba(4, 54, 69, 238);
            }
            QPushButton#windowButton {
                min-width: 22px;
                max-width: 28px;
                padding: 6px 4px;
                border-radius: 10px;
            }
            QPushButton#sendButton:disabled {
                color: rgba(90, 129, 145, 150);
                border-color: rgba(49, 91, 110, 70);
            }
            QFrame#flowPanel {
                background: rgba(2, 15, 31, 225);
                border: 1px solid rgba(54, 169, 208, 100);
                border-radius: 18px;
            }
            QLabel#panelTitle {
                color: #ecfcff;
                font-size: 15px;
                font-weight: 650;
            }
            QLabel#panelState {
                color: #68efd6;
                font-size: 9px;
            }
            QLabel#flowStep {
                color: rgba(160, 199, 217, 205);
                background: rgba(1, 12, 24, 90);
                border-radius: 9px;
                padding: 7px 9px;
                font-size: 10px;
            }
            QLabel#flowStep[current="true"] {
                color: #e9fdff;
                background: rgba(4, 47, 66, 190);
                border: 1px solid rgba(78, 233, 247, 150);
            }
            QLabel#sectionTitle {
                color: rgba(141, 194, 215, 210);
                font-size: 10px;
                font-weight: 600;
            }
            QLabel#alternateFlow {
                color: #85eaf4;
                font-size: 11px;
                font-weight: 600;
                padding: 6px 2px;
            }
            QLabel#panelMuted {
                color: rgba(117, 158, 178, 180);
                font-size: 9px;
            }
            QFrame#divider {
                color: rgba(77, 135, 160, 80);
                background: rgba(77, 135, 160, 60);
                max-height: 1px;
            }
            QLabel#liveStatus {
                color: rgba(216, 249, 255, 240);
                font-size: 15px;
                font-weight: 500;
            }
            QLabel#transcript {
                color: rgba(124, 228, 247, 230);
                font-size: 14px;
                padding: 2px 16px;
            }
            QLabel#detail {
                color: rgba(104, 154, 177, 190);
                font-size: 9px;
                letter-spacing: 1px;
            }
            QLineEdit#conversationInput {
                color: rgba(215, 245, 250, 235);
                background: rgba(3, 18, 34, 235);
                border: 1px solid rgba(58, 134, 168, 90);
                border-radius: 17px;
                padding: 10px 15px;
                selection-background-color: rgba(54, 214, 233, 120);
            }
            QLineEdit#conversationInput:focus {
                border-color: rgba(69, 230, 242, 185);
            }
            """
        )

        self._thread = QThread(self)
        self._worker = AssistantWorker()
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._on_worker_thread_finished)
        self._worker.state_changed.connect(self._on_state)
        self._worker.status_changed.connect(self.status_label.setText)
        self._worker.transcript_changed.connect(self._on_transcript)
        self._worker.detail_changed.connect(self._on_detail)
        self._worker.audio_level_changed.connect(self.canvas.set_audio_level)
        self._worker.log_line.connect(self._on_log)
        self._worker.log_line.connect(_safe_console_log)

        self.canvas.route_changed.connect(self._on_route_changed)

        self.clean_button.toggled.connect(self._set_clean_view)
        self.freeze_button.toggled.connect(self._set_animations_frozen)
        self.min_button.clicked.connect(self.showMinimized)
        self.max_button.clicked.connect(self._toggle_maximize)
        self.close_button.clicked.connect(self.close)
        self.voice_button.clicked.connect(lambda: self._set_mode("voice"))
        self.conversation_button.clicked.connect(
            lambda: self._set_mode("conversation")
        )

        self._thread.start()

        if settings.ui_fullscreen:
            self.showFullScreen()

    def _set_mode(self, mode: str) -> None:
        conversation = mode == "conversation"
        self.conversation_button.setChecked(conversation)
        self.voice_button.setChecked(not conversation)
        self.canvas.set_source_mode("conversation" if conversation else "voice")
        if conversation:
            self.text_input.setPlaceholderText(
                "Conversation texte — prête visuellement (runtime inchangé)"
            )
        else:
            self.text_input.setPlaceholderText(
                "Mode voix actif · deux claquements pour parler"
            )

    def _set_clean_view(self, enabled: bool) -> None:
        self.flow_panel.setVisible(not enabled)
        self.detail_label.setVisible(not enabled)

    def _set_animations_frozen(self, frozen: bool) -> None:
        self.canvas.set_animations_frozen(frozen)
        self.freeze_button.setText(
            "▶  Reprendre les animations"
            if frozen
            else "Ⅱ  Figer les animations"
        )

    def _toggle_maximize(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def _on_route_changed(self, route: object) -> None:
        values = tuple(route or ())
        self.flow_panel.set_route(values)
        active = set(values[-4:])
        self.chip_cerebras.set_active("think" in active)
        self.chip_windows.set_active("windows" in active)
        self.chip_msf.set_active("ms_football" in active)
        self.chip_research.set_active("internet" in active)
        self.chip_research.detail.setText(
            "Active" if "internet" in active else "En veille"
        )

    def _on_state(self, state: str) -> None:
        self.canvas.set_state(state)
        try:
            enum_state = AssistantState(state)
            state_text = STATE_LABELS[enum_state]
        except ValueError:
            state_text = state.upper()

        if state == AssistantState.IDLE.value:
            state_text = "En veille · deux claquements pour parler"
        self.status_label.setText(state_text)

    def _on_transcript(self, text: str) -> None:
        self.canvas.set_transcript(text)
        self.transcript_label.setText(f"« {text} »" if text else "")

    def _on_detail(self, detail: str) -> None:
        self.canvas.ingest_detail(detail)
        self.detail_label.setText(detail)

    def _on_log(self, line: str) -> None:
        self.canvas.ingest_log(line)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key_Escape:
            self.close()
            return
        if event.key() == Qt.Key_F11:
            if self.isFullScreen():
                self.showNormal()
            else:
                self.showFullScreen()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_position = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_position is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_position)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:
        self._drag_position = None
        event.accept()

    def _on_worker_thread_finished(self) -> None:
        if self._close_requested:
            self._allow_close = True
            QTimer.singleShot(0, self.close)

    def closeEvent(self, event) -> None:
        if self._allow_close or not self._thread.isRunning():
            event.accept()
            return

        self._close_requested = True
        self.status_label.setText("Arrêt de Personal Jarvis…")
        self._worker.stop()
        event.ignore()


def run_ui() -> int:
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("Personal Jarvis")
    app.setFont(QFont("Segoe UI", 10))
    window = JarvisWindow()
    window.show()
    return app.exec()
