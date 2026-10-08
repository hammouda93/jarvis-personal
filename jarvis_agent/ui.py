from __future__ import annotations

import html
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
    QTextBrowser,
    QVBoxLayout,
    QTabWidget,
    QWidget,
)

from .assistant_v3 import AssistantWorker
from .operator_console import OperatorConsole
from .operator_telemetry import snapshot as operator_snapshot
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


NORMAL_MIN_SIZE = (1180, 720)
NORMAL_START_SIZE = (1500, 900)
COMPACT_MIN_SIZE = (640, 420)
COMPACT_START_SIZE = (760, 520)


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
    VisualNode("voice", "Votre voix", "Microphone", 0.11, 0.47, "MIC"),
    VisualNode("conversation", "Conversation", "Saisie directe", 0.13, 0.73, "TXT"),
    VisualNode("understand", "Comprendre", "Whisper / STT", 0.28, 0.23, "STT"),
    VisualNode("think", "Réfléchir", "Cerebras", 0.50, 0.14, "AI"),
    VisualNode("mission", "Mission", "Objectif & plan", 0.68, 0.22, "M"),
    VisualNode("context", "Contexte", "Situation actuelle", 0.83, 0.34, "CTX"),
    VisualNode("memory", "Mémoire", "Historique & préférences", 0.21, 0.59, "MEM"),
    VisualNode("internet", "Internet", "Recherche arrière-plan", 0.82, 0.51, "WEB"),
    VisualNode("browser", "Navigateur", "Pages & onglets", 0.82, 0.70, "NAV"),
    VisualNode("windows", "Votre PC", "Fenêtres & applications", 0.67, 0.78, "PC"),
    VisualNode("ms_football", "MS Football", "Vos données", 0.56, 0.88, "MS"),
    VisualNode("verify", "Vérifier", "Résultat observé", 0.43, 0.88, "OK"),
    VisualNode("respond", "Vous répondre", "Voix & texte", 0.28, 0.79, "OUT"),
)

NODE_BY_KEY = {node.key: node for node in NODES}


TOOL_NODE_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^(research_web|search_web)$"), "internet"),
    (re.compile(r"^(open_web_search|open_url|browser\.)"), "browser"),
    (re.compile(r"^msf_"), "ms_football"),
    (
        re.compile(
            r"^(open_application|app\.|inspect_interface|inspect_active_window|observe_screen|"
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

        orbit_specs = (
            (1.72, 0.40, -7, 34),
            (1.98, 0.64, 17, 27),
            (2.18, 0.90, -25, 21),
            (2.38, 1.16, 31, 15),
        )
        for index, (rx, ry, rotation, alpha) in enumerate(orbit_specs):
            painter.save()
            painter.rotate(rotation + math.sin(self._phase * 0.55 + index) * 1.4)
            pen = QPen(QColor(78, 204, 236, alpha))
            pen.setWidthF(0.85 if index < 2 else 0.7)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            orbit_rect = QRectF(
                -scale * rx,
                -scale * ry,
                scale * rx * 2,
                scale * ry * 2,
            )
            painter.drawArc(
                orbit_rect,
                int((18 + index * 49 + self._phase * 5) * 16),
                int((210 - index * 18) * 16),
            )
            painter.restore()

        # Sparse orbital particles make the core feel alive without becoming noisy.
        for index in range(7):
            angle = self._phase * (0.25 + index * 0.02) + index * 0.88
            rx = scale * (1.72 + (index % 3) * 0.23)
            ry = scale * (0.43 + (index % 4) * 0.11)
            point = QPointF(math.cos(angle) * rx, math.sin(angle) * ry)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(115, 237, 255, 115 if index < 3 else 70))
            painter.drawEllipse(point, 1.5 if index < 3 else 1.0, 1.5 if index < 3 else 1.0)

        painter.restore()

    def _draw_core(self, painter: QPainter, center: QPointF, radius: float) -> None:
        breathing = 1.0 + math.sin(self._phase * 1.8) * 0.009
        radius *= breathing + self._level * 0.035

        # Large, soft atmospheric glow.
        glow = QRadialGradient(center, radius * 2.45)
        glow.setColorAt(0.0, QColor(92, 235, 255, 78))
        glow.setColorAt(0.28, QColor(29, 174, 226, 34))
        glow.setColorAt(0.68, QColor(8, 71, 127, 10))
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(center, radius * 2.45, radius * 2.45)

        # Dark glass core with a bright off-centre data light.
        shell = QRadialGradient(
            QPointF(center.x() - radius * 0.32, center.y() - radius * 0.34),
            radius * 1.45,
        )
        shell.setColorAt(0.0, QColor(124, 239, 255, 230))
        shell.setColorAt(0.12, QColor(39, 182, 225, 210))
        shell.setColorAt(0.34, QColor(8, 83, 143, 190))
        shell.setColorAt(0.70, QColor(3, 25, 55, 238))
        shell.setColorAt(1.0, QColor(0, 7, 18, 252))
        painter.setBrush(shell)
        painter.setPen(QPen(QColor(99, 231, 248, 185), 1.5))
        painter.drawEllipse(center, radius, radius)

        # Subtle globe/data mesh.
        painter.setBrush(Qt.NoBrush)
        for ratio, alpha in ((0.30, 45), (0.52, 37), (0.73, 29)):
            painter.setPen(QPen(QColor(116, 228, 248, alpha), 0.7))
            painter.drawEllipse(
                QRectF(
                    center.x() - radius * ratio,
                    center.y() - radius,
                    radius * ratio * 2,
                    radius * 2,
                )
            )
        for ratio, alpha in ((0.25, 43), (0.48, 34), (0.69, 24)):
            painter.setPen(QPen(QColor(107, 217, 242, alpha), 0.7))
            painter.drawEllipse(
                QRectF(
                    center.x() - radius,
                    center.y() - radius * ratio,
                    radius * 2,
                    radius * ratio * 2,
                )
            )

        # Thin equatorial energy rings.
        for index, factor in enumerate((1.10, 1.31, 1.58)):
            painter.setPen(
                QPen(
                    QColor(80, 219, 247, 88 - index * 20),
                    1.35 if index == 0 else 0.8,
                )
            )
            painter.drawEllipse(
                QRectF(
                    center.x() - radius * factor,
                    center.y() - radius * factor * 0.21,
                    radius * factor * 2,
                    radius * factor * 0.42,
                )
            )

        # Micro points on the shell.
        painter.setPen(Qt.NoPen)
        for index in range(18):
            angle = index * (math.tau / 18.0) + self._phase * 0.08
            ring = radius * (0.72 + (index % 3) * 0.08)
            px = center.x() + math.cos(angle) * ring
            py = center.y() + math.sin(angle) * ring * 0.72
            painter.setBrush(QColor(144, 241, 255, 55 + (index % 4) * 18))
            painter.drawEllipse(QPointF(px, py), 1.2, 1.2)

        title_rect = QRectF(
            center.x() - radius * 0.88,
            center.y() - radius * 0.25,
            radius * 1.76,
            radius * 0.30,
        )
        painter.setPen(QColor(236, 253, 255, 248))
        font = QFont("Segoe UI", max(11, int(radius * 0.135)))
        font.setWeight(QFont.Weight.DemiBold)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.6)
        painter.setFont(font)
        painter.drawText(title_rect, Qt.AlignCenter, "PERSONAL JARVIS")

        painter.setPen(QColor(112, 232, 248, 232))
        font = QFont("Segoe UI", max(8, int(radius * 0.082)))
        font.setWeight(QFont.Weight.DemiBold)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 2.1)
        painter.setFont(font)
        painter.drawText(
            QRectF(
                center.x() - radius * 0.72,
                center.y() + radius * 0.08,
                radius * 1.44,
                radius * 0.18,
            ),
            Qt.AlignCenter,
            "AI KERNEL",
        )

        painter.setPen(QColor(116, 174, 196, 215))
        font = QFont("Segoe UI", max(7, int(radius * 0.058)))
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.9)
        painter.setFont(font)
        painter.drawText(
            QRectF(
                center.x() - radius * 0.76,
                center.y() + radius * 0.28,
                radius * 1.52,
                radius * 0.16,
            ),
            Qt.AlignCenter,
            "CEREBRAS · ORCHESTRATION CORE",
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

        structural_edges = (
            ("voice", "understand"),
            ("understand", "think"),
            ("think", "mission"),
            ("mission", "context"),
            ("think", "memory"),
            ("think", "internet"),
            ("internet", "browser"),
            ("think", "windows"),
            ("windows", "ms_football"),
            ("windows", "verify"),
            ("verify", "respond"),
            ("memory", "respond"),
        )

        for left, right in structural_edges:
            start = points.get(left)
            end = points.get(right)
            if start is None or end is None:
                continue
            path = self._edge_path(start, end, center)
            painter.setPen(QPen(QColor(65, 145, 184, 22), 0.75))
            painter.drawPath(path)

        # The actual runtime route is the only bright connection layer.
        if len(self._route) < 2:
            return

        for index, (left, right) in enumerate(zip(self._route, self._route[1:])):
            start = points.get(left)
            end = points.get(right)
            if start is None or end is None:
                continue
            path = self._edge_path(start, end, center)

            external = left == "internet" or right == "internet"
            if external:
                glow = QColor(255, 184, 87, 52)
                line = QColor(245, 196, 111, 220)
                pulse_color = QColor(255, 217, 151, 245)
            else:
                glow = QColor(52, 228, 255, 50)
                line = QColor(93, 239, 255, 220)
                pulse_color = QColor(230, 255, 255, 250)

            glow_pen = QPen(glow, 5.6)
            glow_pen.setCapStyle(Qt.RoundCap)
            painter.setPen(glow_pen)
            painter.drawPath(path)

            active_pen = QPen(line, 1.45)
            active_pen.setCapStyle(Qt.RoundCap)
            painter.setPen(active_pen)
            painter.drawPath(path)

            t = (self._phase * 0.60 + index * 0.13) % 1.0
            pulse = path.pointAtPercent(t)
            pulse_glow = QRadialGradient(pulse, 8.0)
            pulse_glow.setColorAt(0.0, pulse_color)
            pulse_glow.setColorAt(0.34, QColor(line.red(), line.green(), line.blue(), 170))
            pulse_glow.setColorAt(1.0, QColor(line.red(), line.green(), line.blue(), 0))
            painter.setPen(Qt.NoPen)
            painter.setBrush(pulse_glow)
            painter.drawEllipse(pulse, 8.0, 8.0)

    def _draw_node(
        self,
        painter: QPainter,
        node: VisualNode,
        rect: QRectF,
    ) -> None:
        pos = self._node_center(node, rect)
        route_tail = self._route[-8:]
        active = node.key in route_tail
        current = bool(self._route and self._route[-1] == node.key)
        completed = active and not current
        external = node.key == "internet"
        unavailable = False

        base = min(rect.width(), rect.height())
        radius = max(22.0, min(31.0, base * (0.040 if active else 0.036)))

        if current:
            halo = QRadialGradient(pos, radius * 2.45)
            halo.setColorAt(
                0.0,
                QColor(255, 190, 96, 85)
                if external
                else QColor(73, 237, 255, 88),
            )
            halo.setColorAt(
                0.45,
                QColor(245, 166, 72, 24)
                if external
                else QColor(30, 160, 212, 22),
            )
            halo.setColorAt(1.0, QColor(0, 0, 0, 0))
            painter.setPen(Qt.NoPen)
            painter.setBrush(halo)
            painter.drawEllipse(pos, radius * 2.45, radius * 2.45)

        face = QRadialGradient(
            QPointF(pos.x() - radius * 0.24, pos.y() - radius * 0.26),
            radius * 1.30,
        )
        if unavailable:
            face.setColorAt(0.0, QColor(15, 31, 44, 210))
            face.setColorAt(1.0, QColor(3, 12, 22, 235))
        elif active:
            face.setColorAt(0.0, QColor(18, 69, 91, 240))
            face.setColorAt(1.0, QColor(2, 17, 31, 246))
        else:
            face.setColorAt(0.0, QColor(12, 34, 52, 225))
            face.setColorAt(1.0, QColor(2, 12, 25, 238))

        border = (
            QColor(242, 192, 115, 215)
            if external and active
            else QColor(94, 232, 247, 210 if active else 72)
        )
        if unavailable:
            border = QColor(73, 105, 119, 68)

        painter.setBrush(face)
        painter.setPen(QPen(border, 1.45 if current else 0.95))
        painter.drawEllipse(pos, radius, radius)

        # Inner symbol disc.
        painter.setPen(Qt.NoPen)
        painter.setBrush(
            QColor(87, 232, 247, 34 if not unavailable else 12)
        )
        painter.drawEllipse(pos, radius * 0.57, radius * 0.57)

        painter.setPen(
            QColor(226, 253, 255, 242)
            if active
            else QColor(150, 194, 211, 205)
        )
        badge_font = QFont("Segoe UI", max(7, int(radius * 0.34)))
        badge_font.setWeight(QFont.Weight.Bold)
        painter.setFont(badge_font)
        painter.drawText(
            QRectF(
                pos.x() - radius * 0.72,
                pos.y() - radius * 0.34,
                radius * 1.44,
                radius * 0.68,
            ),
            Qt.AlignCenter,
            node.badge,
        )

        label_width = radius * 4.2
        label_top = pos.y() + radius + 7.0
        title_color = (
            QColor(233, 253, 255, 246)
            if active
            else QColor(180, 215, 227, 215)
        )
        if unavailable:
            title_color = QColor(112, 143, 156, 180)

        painter.setPen(title_color)
        title_font = QFont("Segoe UI", max(9, int(radius * 0.36)))
        title_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(title_font)
        painter.drawText(
            QRectF(
                pos.x() - label_width / 2,
                label_top,
                label_width,
                20,
            ),
            Qt.AlignHCenter | Qt.AlignTop,
            node.label,
        )

        painter.setPen(
            QColor(104, 215, 235, 205)
            if active
            else QColor(91, 137, 157, 175)
        )
        if unavailable:
            painter.setPen(QColor(92, 119, 131, 155))
        subtitle_font = QFont("Segoe UI", max(7, int(radius * 0.25)))
        painter.setFont(subtitle_font)
        painter.drawText(
            QRectF(
                pos.x() - label_width * 0.60,
                label_top + 19,
                label_width * 1.20,
                24,
            ),
            Qt.AlignHCenter | Qt.AlignTop,
            node.subtitle,
        )

        if active:
            try:
                route_index = route_tail.index(node.key)
            except ValueError:
                route_index = -1
            if route_index >= 0:
                bubble = QPointF(pos.x() + radius * 0.82, pos.y() - radius * 0.82)
                painter.setPen(QPen(border, 1.0))
                painter.setBrush(QColor(2, 17, 30, 245))
                painter.drawEllipse(bubble, 8.5, 8.5)
                painter.setPen(QColor(229, 253, 255, 238))
                step_font = QFont("Segoe UI", 7)
                step_font.setWeight(QFont.Weight.Bold)
                painter.setFont(step_font)
                painter.drawText(
                    QRectF(bubble.x() - 8.5, bubble.y() - 8.5, 17, 17),
                    Qt.AlignCenter,
                    str(route_index + 1),
                )

        if completed:
            tick = QPointF(pos.x() - radius * 0.80, pos.y() - radius * 0.78)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(69, 234, 195, 215))
            painter.drawEllipse(tick, 4.0, 4.0)

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
        scale = min(rect.width(), rect.height()) * 0.148

        self._draw_orbits(painter, center, scale)
        self._draw_connections(painter, rect, center)
        self._draw_core(painter, center, scale)

        for node in NODES:
            self._draw_node(painter, node, rect)


class StatusChip(QFrame):
    def __init__(self, title: str, detail: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("statusChip")
        self.setMinimumWidth(116)

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
        layout.setContentsMargins(8, 5, 10, 5)
        layout.setSpacing(7)
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
        self.setMaximumWidth(315)

        title = QLabel("Trajet de la demande")
        title.setObjectName("panelTitle")

        self.state = QLabel("● En attente")
        self.state.setObjectName("panelState")

        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self.state)

        self.idle_card = QFrame()
        self.idle_card.setObjectName("idleCard")
        idle_kicker = QLabel("PERSONAL JARVIS PRÊT")
        idle_kicker.setObjectName("idleKicker")
        idle_title = QLabel("En attente d’une demande")
        idle_title.setObjectName("idleTitle")
        idle_help = QLabel(
            "Le noyau reste en veille.\nDeux claquements pour parler."
        )
        idle_help.setObjectName("idleHelp")
        idle_help.setWordWrap(True)

        idle_layout = QVBoxLayout(self.idle_card)
        idle_layout.setContentsMargins(15, 15, 15, 15)
        idle_layout.setSpacing(6)
        idle_layout.addWidget(idle_kicker)
        idle_layout.addWidget(idle_title)
        idle_layout.addWidget(idle_help)

        self.steps_container = QFrame()
        self.steps_container.setObjectName("stepsContainer")
        self._step_labels: list[QLabel] = []
        steps = QVBoxLayout(self.steps_container)
        steps.setContentsMargins(0, 0, 0, 0)
        steps.setSpacing(6)
        for _ in range(8):
            label = QLabel("")
            label.setObjectName("flowStep")
            label.setWordWrap(True)
            label.hide()
            self._step_labels.append(label)
            steps.addWidget(label)

        self.module_line = QLabel("Module actif  —")
        self.module_line.setObjectName("moduleLine")
        self.module_line.hide()

        divider = QFrame()
        divider.setObjectName("divider")
        divider.setFrameShape(QFrame.HLine)

        alternate_title = QLabel("RECHERCHE EXTERNE")
        alternate_title.setObjectName("sectionTitle")

        alternate = QLabel("Réfléchir  →  Internet  →  Réfléchir")
        alternate.setObjectName("alternateFlow")
        alternate.setWordWrap(True)

        alternate_help = QLabel(
            "Uniquement si une information externe est réellement nécessaire."
        )
        alternate_help.setObjectName("panelMuted")
        alternate_help.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        layout.addLayout(head)
        layout.addWidget(self.idle_card)
        layout.addWidget(self.steps_container)
        layout.addWidget(self.module_line)
        layout.addStretch(1)
        layout.addWidget(divider)
        layout.addWidget(alternate_title)
        layout.addWidget(alternate)
        layout.addWidget(alternate_help)

        self.set_route(())

    def set_route(self, route: tuple[str, ...] | list[str]) -> None:
        visible = list(route)[-8:]
        active_route = bool(visible)

        self.idle_card.setVisible(not active_route)
        self.steps_container.setVisible(active_route)
        self.module_line.setVisible(active_route)

        for idx, label in enumerate(self._step_labels):
            if idx >= len(visible):
                label.hide()
                continue
            node = NODE_BY_KEY.get(visible[idx])
            if node is None:
                label.hide()
                continue

            is_current = idx == len(visible) - 1
            marker = "●" if is_current else "✓"
            label.setText(
                f"{idx + 1:02d}   {node.label}\n"
                f"      {node.subtitle}   {marker}"
            )
            label.setProperty("current", is_current)
            label.style().unpolish(label)
            label.style().polish(label)
            label.show()

        if visible:
            current = NODE_BY_KEY.get(visible[-1])
            self.module_line.setText(
                f"Module actif  {current.label if current else '—'}"
            )
            self.state.setText("● En cours")
        else:
            self.module_line.setText("Module actif  —")
            self.state.setText("● En attente")


class JarvisWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Personal Jarvis")
        self.setMinimumSize(*NORMAL_MIN_SIZE)
        self.resize(*NORMAL_START_SIZE)
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.setObjectName("root")
        self._drag_position = None
        self._close_requested = False
        self._allow_close = False
        self._compact_mode = False
        self._normal_geometry = None

        self.canvas = PersonalJarvisCanvas(self)
        self.flow_panel = FlowPanel(self)
        self.operator_console = OperatorConsole(self)
        self.side_tabs = QTabWidget(self)
        self.side_tabs.setObjectName("sideTabs")
        self.side_tabs.setMinimumWidth(340)
        self.side_tabs.setMaximumWidth(410)
        self.side_tabs.addTab(self.operator_console, "◉  CONTRÔLE")
        self.side_tabs.addTab(self.flow_panel, "◇  TRAJET")
        self.canvas.setMinimumSize(520, 360)

        title = QLabel("PERSONAL JARVIS")
        title.setObjectName("brandTitle")
        self.brand_title = title
        tagline = QLabel(
            "Personal AI Agent réellement intelligent, généraliste, robuste et évolutif"
        )
        tagline.setObjectName("brandTagline")
        self.brand_tagline = tagline

        brand = QVBoxLayout()
        brand.setContentsMargins(0, 0, 8, 0)
        brand.setSpacing(1)
        brand.addWidget(title)
        brand.addWidget(tagline)

        self.chip_cerebras = StatusChip("Cerebras", "En attente")
        self.chip_windows = StatusChip("Windows", "En attente")
        self.chip_msf = StatusChip("MS Football", "En attente")
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

        self.operator_full_button = QPushButton("▤  Supervision")
        self.operator_full_button.setObjectName("topButton")
        self.operator_full_button.setCheckable(True)
        self.operator_full_button.setToolTip(
            "Agrandir la console réelle de missions, actions, mémoire et modèle."
        )

        self.freeze_button = QPushButton("Ⅱ  Figer les animations")
        self.freeze_button.setObjectName("topButton")
        self.freeze_button.setCheckable(True)

        self.compact_button = QPushButton("⇲  Compact")
        self.compact_button.setObjectName("topButton")
        self.compact_button.setCheckable(True)
        self.compact_button.setToolTip(
            "Réduire Jarvis pour voir les applications pendant les tests."
        )

        self.min_button = QPushButton("—")
        self.min_button.setObjectName("windowButton")
        self.max_button = QPushButton("□")
        self.max_button.setObjectName("windowButton")
        self.close_button = QPushButton("×")
        self.close_button.setObjectName("windowButton")

        controls = QHBoxLayout()
        controls.setSpacing(6)
        controls.addWidget(self.clean_button)
        controls.addWidget(self.operator_full_button)
        controls.addWidget(self.freeze_button)
        controls.addWidget(self.compact_button)
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
        middle.addWidget(self.side_tabs)

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

        self.voice_button = QPushButton("●  Voix active")
        self.voice_button.setObjectName("modeButton")
        self.voice_button.setProperty("active", True)

        self.conversation_button = QPushButton("⌨  Conversation texte")
        self.conversation_button.setObjectName("modeButton")
        self.conversation_button.setCheckable(True)
        self.conversation_button.setToolTip(
            "Passer en conversation texte : microphone coupé, réponses écrites et vocales."
        )

        self.chat_panel = QFrame()
        self.chat_panel.setObjectName("chatPanel")
        self.chat_panel.setMaximumHeight(250)

        chat_title = QLabel("CONVERSATION")
        chat_title.setObjectName("chatTitle")
        chat_help = QLabel("Même contexte et mêmes outils · micro coupé · réponses texte + voix")
        chat_help.setObjectName("chatHelp")

        chat_head = QHBoxLayout()
        chat_head.setContentsMargins(0, 0, 0, 0)
        chat_head.addWidget(chat_title)
        chat_head.addStretch(1)
        chat_head.addWidget(chat_help)

        self.chat_history = QTextBrowser()
        self.chat_history.setObjectName("chatHistory")
        self.chat_history.setOpenExternalLinks(False)
        self.chat_history.setPlaceholderText(
            "Les échanges texte et voix apparaîtront ici."
        )

        self.chat_input = QLineEdit()
        self.chat_input.setObjectName("chatInput")
        self.chat_input.setPlaceholderText(
            "Écrivez à Jarvis…  Entrée pour envoyer"
        )
        self.chat_input.setClearButtonEnabled(True)

        self.chat_send_button = QPushButton("Envoyer  ↵")
        self.chat_send_button.setObjectName("chatSend")

        chat_entry = QHBoxLayout()
        chat_entry.setContentsMargins(0, 0, 0, 0)
        chat_entry.setSpacing(8)
        chat_entry.addWidget(self.chat_input, 1)
        chat_entry.addWidget(self.chat_send_button)

        chat_layout = QVBoxLayout(self.chat_panel)
        chat_layout.setContentsMargins(13, 10, 13, 11)
        chat_layout.setSpacing(7)
        chat_layout.addLayout(chat_head)
        chat_layout.addWidget(self.chat_history, 1)
        chat_layout.addLayout(chat_entry)
        self.chat_panel.hide()

        self.bottom_hint = QLabel("Deux claquements pour parler")
        self.bottom_hint.setObjectName("bottomHint")
        self.bottom_hint.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.bottom_dock = QFrame()
        self.bottom_dock.setObjectName("bottomDock")
        bottom = QHBoxLayout(self.bottom_dock)
        bottom.setContentsMargins(10, 8, 10, 8)
        bottom.setSpacing(8)
        bottom.addWidget(self.voice_button)
        bottom.addWidget(self.conversation_button)
        bottom.addStretch(1)
        bottom.addWidget(self.bottom_hint)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 18, 24, 18)
        root.setSpacing(10)
        root.addLayout(header)
        root.addLayout(middle, 1)
        root.addLayout(status_stack)
        root.addWidget(self.chat_panel)
        root.addWidget(self.bottom_dock)

        self.setStyleSheet(
            """
            QWidget#root {
                background: #010712;
                color: #e7fbff;
            }
            QLabel#brandTitle {
                color: #effcff;
                font-size: 20px;
                font-weight: 650;
                letter-spacing: 3px;
            }
            QLabel#brandTagline {
                color: rgba(116, 197, 221, 205);
                font-size: 9px;
            }
            QFrame#statusChip {
                background: rgba(3, 15, 28, 120);
                border: 1px solid rgba(55, 132, 163, 45);
                border-radius: 11px;
            }
            QFrame#statusChip[active="true"] {
                background: rgba(3, 31, 43, 190);
                border: 1px solid rgba(80, 231, 238, 145);
            }
            QLabel#chipDot {
                color: #61efd8;
                font-size: 9px;
            }
            QLabel#chipTitle {
                color: rgba(230, 250, 254, 236);
                font-size: 9px;
                font-weight: 600;
            }
            QLabel#chipDetail {
                color: rgba(96, 157, 181, 188);
                font-size: 8px;
            }
            QPushButton#topButton,
            QPushButton#windowButton,
            QPushButton#modeButton,
            QPushButton#futureButton {
                color: rgba(207, 244, 250, 225);
                background: rgba(3, 15, 27, 150);
                border: 1px solid rgba(63, 137, 164, 65);
                border-radius: 12px;
                padding: 7px 11px;
            }
            QPushButton#topButton:hover,
            QPushButton#windowButton:hover {
                background: rgba(5, 29, 44, 210);
                border-color: rgba(73, 213, 229, 145);
            }
            QPushButton#topButton:checked {
                color: #e7feff;
                background: rgba(4, 45, 58, 210);
                border-color: rgba(72, 229, 239, 180);
            }
            QPushButton#windowButton {
                min-width: 22px;
                max-width: 28px;
                padding: 5px 3px;
                border-radius: 9px;
            }
            QTabWidget#sideTabs::pane {
                border: 1px solid rgba(54, 160, 196, 84);
                border-radius: 13px;
                background: rgba(2, 13, 27, 220);
            }
            QTabWidget#sideTabs QTabBar::tab {
                background: #061a2b;
                color: #82bcc9;
                padding: 8px 12px;
                margin-right: 3px;
                border-radius: 8px;
                font-size: 9px;
            }
            QTabWidget#sideTabs QTabBar::tab:selected {
                background: #124158;
                color: #e9fdff;
            }
            QFrame#flowPanel {
                background: rgba(2, 13, 27, 220);
                border: 1px solid rgba(54, 160, 196, 84);
                border-radius: 17px;
            }
            QLabel#panelTitle {
                color: #effcff;
                font-size: 14px;
                font-weight: 650;
            }
            QLabel#panelState {
                color: #6ae6cf;
                font-size: 8px;
            }
            QFrame#idleCard {
                background: rgba(4, 26, 42, 125);
                border: 1px solid rgba(62, 146, 177, 62);
                border-radius: 13px;
            }
            QLabel#idleKicker {
                color: rgba(101, 226, 239, 220);
                font-size: 8px;
                font-weight: 650;
                letter-spacing: 1px;
            }
            QLabel#idleTitle {
                color: rgba(233, 252, 255, 240);
                font-size: 12px;
                font-weight: 600;
            }
            QLabel#idleHelp {
                color: rgba(113, 161, 181, 185);
                font-size: 9px;
                line-height: 1.3;
            }
            QFrame#stepsContainer {
                background: transparent;
                border: none;
            }
            QLabel#flowStep {
                color: rgba(150, 190, 207, 205);
                background: rgba(1, 11, 22, 70);
                border: 1px solid rgba(57, 113, 137, 32);
                border-radius: 9px;
                padding: 7px 9px;
                font-size: 9px;
            }
            QLabel#flowStep[current="true"] {
                color: #e9fdff;
                background: rgba(4, 42, 58, 165);
                border: 1px solid rgba(78, 228, 241, 125);
            }
            QLabel#moduleLine {
                color: rgba(100, 207, 226, 205);
                font-size: 8px;
                padding: 2px 4px;
            }
            QLabel#sectionTitle {
                color: rgba(116, 169, 190, 185);
                font-size: 8px;
                font-weight: 650;
                letter-spacing: 1px;
            }
            QLabel#alternateFlow {
                color: rgba(130, 226, 235, 215);
                font-size: 9px;
                font-weight: 600;
                padding: 4px 0;
            }
            QLabel#panelMuted {
                color: rgba(94, 132, 149, 175);
                font-size: 8px;
            }
            QFrame#divider {
                color: rgba(69, 124, 147, 50);
                background: rgba(69, 124, 147, 45);
                max-height: 1px;
            }
            QLabel#liveStatus {
                color: rgba(222, 249, 253, 238);
                font-size: 13px;
                font-weight: 500;
            }
            QLabel#transcript {
                color: rgba(112, 220, 238, 220);
                font-size: 12px;
                padding: 1px 14px;
            }
            QLabel#detail {
                color: rgba(87, 135, 154, 165);
                font-size: 8px;
                letter-spacing: 1px;
            }
            QFrame#chatPanel {
                background: rgba(2, 15, 28, 232);
                border: 1px solid rgba(67, 174, 204, 90);
                border-radius: 16px;
            }
            QLabel#chatTitle {
                color: rgba(220, 250, 255, 240);
                font-size: 9px;
                font-weight: 700;
                letter-spacing: 1px;
            }
            QLabel#chatHelp {
                color: rgba(92, 150, 170, 180);
                font-size: 8px;
            }
            QTextBrowser#chatHistory {
                color: rgba(221, 249, 253, 235);
                background: rgba(0, 7, 16, 150);
                border: 1px solid rgba(54, 125, 151, 45);
                border-radius: 10px;
                padding: 7px;
                font-size: 10px;
            }
            QLineEdit#chatInput {
                color: #e9fdff;
                background: rgba(0, 8, 17, 210);
                border: 1px solid rgba(70, 170, 195, 90);
                border-radius: 11px;
                padding: 8px 11px;
                selection-background-color: rgba(48, 190, 210, 170);
            }
            QLineEdit#chatInput:focus {
                border-color: rgba(91, 232, 241, 190);
            }
            QPushButton#chatSend {
                color: #e8fdff;
                background: rgba(4, 48, 58, 210);
                border: 1px solid rgba(70, 226, 235, 145);
                border-radius: 11px;
                padding: 8px 14px;
                font-size: 9px;
                font-weight: 600;
            }
            QPushButton#chatSend:hover {
                background: rgba(6, 67, 79, 230);
            }
            QFrame#bottomDock {
                background: rgba(2, 13, 25, 190);
                border: 1px solid rgba(52, 123, 151, 55);
                border-radius: 16px;
            }
            QPushButton#modeButton {
                color: #dffcff;
                background: rgba(4, 48, 58, 190);
                border-color: rgba(70, 226, 235, 150);
                font-size: 9px;
                font-weight: 600;
            }
            QPushButton#futureButton:disabled {
                color: rgba(107, 137, 149, 145);
                background: rgba(2, 15, 26, 120);
                border-color: rgba(47, 85, 100, 48);
                font-size: 9px;
            }
            QLabel#bottomHint {
                color: rgba(103, 160, 181, 190);
                font-size: 9px;
                padding-right: 8px;
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
        self._worker.state_changed.connect(self.operator_console.update_phase)
        self._worker.status_changed.connect(self.status_label.setText)
        self._worker.transcript_changed.connect(self._on_transcript)
        self._worker.detail_changed.connect(self._on_detail)
        self._worker.audio_level_changed.connect(self.canvas.set_audio_level)
        self._worker.log_line.connect(self._on_log)
        self._worker.log_line.connect(_safe_console_log)
        self._worker.conversation_message.connect(self._on_conversation_message)
        self._worker.telemetry_changed.connect(self.operator_console.update_model)
        self._worker.operator_event.connect(self.operator_console.update_live_event)
        self._worker.mission_control_result.connect(self.operator_console.show_mission_result)
        self.operator_console.mission_requested.connect(self._on_mission_request)

        self.canvas.route_changed.connect(self._on_route_changed)

        self.clean_button.toggled.connect(self._set_clean_view)
        self.operator_full_button.toggled.connect(self._set_operator_full_mode)
        self.freeze_button.toggled.connect(self._set_animations_frozen)
        self.compact_button.toggled.connect(self._set_compact_mode)
        self.conversation_button.toggled.connect(self._set_text_panel)
        self.chat_send_button.clicked.connect(self._submit_text)
        self.chat_input.returnPressed.connect(self._submit_text)
        self.min_button.clicked.connect(self.showMinimized)
        self.max_button.clicked.connect(self._toggle_maximize)
        self.close_button.clicked.connect(self.close)

        # The console reads only local, already-persisted snapshots. No UI
        # polling ever calls the model, any action tool, or the worker thread.
        self._operator_timer = QTimer(self)
        self._operator_timer.setInterval(1400)
        self._operator_timer.timeout.connect(self._refresh_operator_console)
        self._operator_timer.start()
        self._thread.start()

        if settings.ui_fullscreen:
            self.showFullScreen()

    def _refresh_operator_console(self) -> None:
        if self._compact_mode or self.clean_button.isChecked():
            return
        try:
            self.operator_console.apply_snapshot(
                operator_snapshot(
                    max_items=18 if self.operator_full_button.isChecked() else 7
                )
            )
        except (OSError, ValueError, RuntimeError):
            # Read-only monitoring must never stop user interaction.
            self.operator_console.mode_line.setText("Télémétrie temporairement indisponible")

    def _on_mission_request(self, operation: str, value: str) -> None:
        """UI only submits a command; all mission methods run on worker."""
        if not self._thread.isRunning():
            self.operator_console.mission_feedback.setText("Jarvis n'est pas actif.")
            return
        if not self.conversation_button.isChecked():
            # Stops mic admission before queuing the explicit text mission.
            self.conversation_button.setChecked(True)
        accepted = self._worker.submit_mission_control(operation, value)
        if not accepted:
            self.operator_console.mission_feedback.setText(
                "Commande refusée : saisie invalide ou demande déjà en attente."
            )
        else:
            self.operator_console.mission_feedback.setText(
                "Demande reçue · traitement sur le moteur Jarvis existant."
            )

    def _set_text_panel(self, enabled: bool) -> None:
        self._worker.set_text_mode(bool(enabled))
        self.chat_panel.setVisible(bool(enabled))
        self.conversation_button.setText(
            "🎤  Revenir à la voix"
            if enabled
            else "⌨  Conversation texte"
        )
        self.conversation_button.setToolTip(
            "Réactiver le microphone et le réveil par double clap."
            if enabled
            else "Passer en conversation texte : micro coupé, réponses écrites et vocales."
        )
        self.bottom_hint.setText(
            "Micro coupé · écrivez votre demande · réponse texte et voix"
            if enabled
            else "Deux claquements pour parler"
        )
        if enabled:
            self.chat_input.setFocus()

    def _submit_text(self) -> None:
        text = self.chat_input.text().strip()
        if not text:
            return
        if not self._thread.isRunning():
            self.status_label.setText("Jarvis n'est pas actif")
            return

        # submit_text only touches a thread-safe queue/event. The agent itself
        # remains serialized on AssistantWorker's thread.
        accepted = self._worker.submit_text(text)
        if not accepted:
            return
        self.chat_input.clear()
        self.canvas.begin_request("conversation")
        self.transcript_label.setText(f"« {text} »")
        self.status_label.setText("Message texte envoyé à Jarvis…")

    def _on_conversation_message(
        self,
        role: str,
        text: str,
        source: str,
    ) -> None:
        value = str(text or "").strip()
        if not value:
            return
        safe = html.escape(value).replace("\n", "<br>")
        if role == "user":
            label = "VOUS · TEXTE" if source == "text" else "VOUS · VOIX"
            block = (
                "<div style='margin:5px 0 7px 0;'>"
                "<span style='color:#72ddea;font-size:9px;font-weight:600;'>"
                f"{label}</span><br>"
                "<span style='color:#dcf8fc;font-size:11px;'>"
                f"{safe}</span></div>"
            )
        else:
            block = (
                "<div style='margin:5px 0 9px 18px;'>"
                "<span style='color:#6ff0cf;font-size:9px;font-weight:600;'>"
                "JARVIS</span><br>"
                "<span style='color:#eefeff;font-size:11px;'>"
                f"{safe}</span></div>"
            )
        self.chat_history.append(block)
        scrollbar = self.chat_history.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

        if source == "text":
            self.chat_panel.show()
            if not self.conversation_button.isChecked():
                self.conversation_button.setChecked(True)
            if role == "user":
                self.canvas.begin_request("conversation")
            else:
                self.canvas._append_route("respond")

    def _set_operator_full_mode(self, enabled: bool) -> None:
        if enabled and self.clean_button.isChecked():
            self.clean_button.setChecked(False)
        if enabled and self._compact_mode:
            self.operator_full_button.setChecked(False)
            return
        self.side_tabs.setCurrentWidget(self.operator_console)
        self.side_tabs.setMaximumWidth(16777215 if enabled else 410)
        self.canvas.setVisible(not enabled and not self._compact_mode)
        self.operator_full_button.setText(
            "◉  Revenir au graphe" if enabled else "▤  Supervision"
        )
        self._refresh_operator_console()

    def _set_clean_view(self, enabled: bool) -> None:
        if enabled and self.operator_full_button.isChecked():
            # Clean view must never become an empty screen while expanded
            # supervision has hidden the normal animated canvas.
            self.operator_full_button.setChecked(False)
        self.side_tabs.setVisible(not enabled and not self._compact_mode)
        self.detail_label.setVisible(not enabled)

    def _set_animations_frozen(self, frozen: bool) -> None:
        self.canvas.set_animations_frozen(frozen)
        self.freeze_button.setText(
            "▶  Reprendre les animations"
            if frozen
            else "Ⅱ  Figer les animations"
        )

    def _set_compact_mode(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._compact_mode:
            return

        self._compact_mode = enabled
        if enabled:
            if not self.isMaximized() and not self.isFullScreen():
                self._normal_geometry = self.geometry()
            self.showNormal()
            self.setMinimumSize(*COMPACT_MIN_SIZE)
            self.canvas.hide()
            self.side_tabs.hide()
            self.brand_tagline.hide()
            self.chip_msf.hide()
            self.chip_research.hide()
            self.clean_button.hide()
            self.operator_full_button.setChecked(False)
            self.operator_full_button.hide()
            self.freeze_button.hide()
            self.detail_label.hide()
            self.transcript_label.hide()
            if not self.conversation_button.isChecked():
                self.conversation_button.setChecked(True)
            else:
                self.chat_panel.show()
            self.resize(*COMPACT_START_SIZE)
            self.compact_button.setText("⇱  Normal")
            self.bottom_hint.setText("Mode compact · texte + voix disponibles")
            self.chat_input.setFocus()
        else:
            self.setMinimumSize(*NORMAL_MIN_SIZE)
            self.canvas.setVisible(not self.operator_full_button.isChecked())
            self.operator_full_button.show()
            if not self.clean_button.isChecked():
                self.side_tabs.show()
                self.detail_label.show()
            self.brand_tagline.show()
            self.chip_msf.show()
            self.chip_research.show()
            self.clean_button.show()
            self.freeze_button.show()
            self.transcript_label.show()
            self.compact_button.setText("⇲  Compact")
            if self._normal_geometry is not None:
                self.setGeometry(self._normal_geometry)
            else:
                self.resize(*NORMAL_START_SIZE)

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

        text = str(line)
        if text.startswith("[AI] provider="):
            provider = re.search(r"provider=([^\s]+)", text)
            if provider:
                value = provider.group(1).strip()
                self.chip_cerebras.detail.setText(
                    "Principal" if value == "cerebras" else value
                )

        if text.startswith("[AGENT] provider=cerebras"):
            self.chip_cerebras.detail.setText("Actif")

        # Actual numbered model rounds, emitted by the existing runtime.
        current_round = re.search(
            r"^\[AGENT\] provider=([^\s]+) model=([^\s]+) round=(\d+)",
            text,
        )
        if current_round:
            previous = dict(self.operator_console._last_model)
            previous["provider"] = current_round.group(1)
            previous["rounds_used"] = int(current_round.group(3))
            self.operator_console.update_model(previous)

        call = re.search(r"\[AGENT_TOOL\] call=([^\s]+)", text)
        if call:
            node = _tool_visual_node(call.group(1))
            if node == "windows":
                self.chip_windows.detail.setText("Actif")
            elif node == "ms_football":
                self.chip_msf.detail.setText("Actif")
            elif node == "internet":
                self.chip_research.detail.setText("Active")

        result = re.search(
            r"\[AGENT_TOOL\] result=([^\s]+)\s+success=(True|False)",
            text,
        )
        if result:
            node = _tool_visual_node(result.group(1))
            detail = "Terminé" if result.group(2) == "True" else "Erreur"
            if node == "windows":
                self.chip_windows.detail.setText(detail)
            elif node == "ms_football":
                self.chip_msf.detail.setText(detail)
            elif node == "internet":
                self.chip_research.detail.setText(detail)

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
