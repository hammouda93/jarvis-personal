from __future__ import annotations

import math
import random
from collections import deque

from PySide6.QtCore import QPointF, QRectF, Qt, QThread, QTimer
from PySide6.QtGui import QColor, QFont, QKeyEvent, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QApplication, QLabel, QTextBrowser, QVBoxLayout, QWidget

from .assistant_v3 import AssistantWorker
from .config import settings
from .states import AssistantState, STATE_LABELS


STATE_COLORS: dict[str, QColor] = {
    AssistantState.STARTING.value: QColor(74, 142, 190),
    AssistantState.CALIBRATING.value: QColor(83, 156, 196),
    AssistantState.IDLE.value: QColor(56, 94, 116),
    AssistantState.ARMED.value: QColor(76, 213, 236),
    AssistantState.WAKE.value: QColor(175, 246, 255),
    AssistantState.LISTENING.value: QColor(74, 224, 255),
    AssistantState.TRANSCRIBING.value: QColor(140, 122, 255),
    AssistantState.UNDERSTANDING.value: QColor(159, 122, 255),
    AssistantState.THINKING.value: QColor(181, 132, 255),
    AssistantState.ACTING.value: QColor(88, 234, 205),
    AssistantState.SPEAKING.value: QColor(117, 226, 255),
    AssistantState.SUCCESS.value: QColor(112, 255, 202),
    AssistantState.ERROR.value: QColor(255, 102, 122),
}


class ReactiveOrb(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(620, 460)
        self._phase = 0.0
        self._state = AssistantState.STARTING.value
        self._level_target = 0.0
        self._level = 0.0
        self._history = deque([0.0] * 72, maxlen=72)
        self._particles = [
            (
                random.random() * math.tau,
                random.uniform(1.18, 2.15),
                random.uniform(0.22, 0.85),
                random.uniform(0.25, 1.0),
            )
            for _ in range(78)
        ]

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)

    def set_state(self, state: str) -> None:
        self._state = state
        self.update()

    def set_audio_level(self, level: float) -> None:
        self._level_target = max(0.0, min(1.0, float(level)))

    def _tick(self) -> None:
        speed = {
            AssistantState.IDLE.value: 0.007,
            AssistantState.CALIBRATING.value: 0.012,
            AssistantState.LISTENING.value: 0.020,
            AssistantState.TRANSCRIBING.value: 0.028,
            AssistantState.UNDERSTANDING.value: 0.034,
            AssistantState.THINKING.value: 0.048,
            AssistantState.ACTING.value: 0.041,
            AssistantState.SPEAKING.value: 0.023,
            AssistantState.SUCCESS.value: 0.016,
            AssistantState.ERROR.value: 0.014,
        }.get(self._state, 0.012)

        self._phase += speed
        self._level += (self._level_target - self._level) * 0.23
        self._level_target *= 0.94
        self._history.append(self._level)
        self.update()

    def _color(self, alpha: int = 255) -> QColor:
        base = STATE_COLORS.get(self._state, QColor(74, 224, 255))
        color = QColor(base)
        color.setAlpha(max(0, min(255, alpha)))
        return color

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        rect = self.rect()
        painter.fillRect(rect, QColor(2, 7, 13))

        bg = QRadialGradient(
            QPointF(rect.center()),
            max(rect.width(), rect.height()) * 0.68,
        )
        bg.setColorAt(0.0, QColor(8, 28, 42))
        bg.setColorAt(0.48, QColor(3, 14, 24))
        bg.setColorAt(1.0, QColor(1, 4, 8))
        painter.fillRect(rect, bg)

        cx = rect.center().x()
        cy = rect.center().y() - 8
        center = QPointF(cx, cy)
        base_r = min(rect.width(), rect.height()) * 0.145

        breathing = (math.sin(self._phase * 3.2) + 1.0) * 0.5
        state_boost = {
            AssistantState.WAKE.value: 0.17,
            AssistantState.LISTENING.value: 0.09,
            AssistantState.TRANSCRIBING.value: 0.06,
            AssistantState.UNDERSTANDING.value: 0.08,
            AssistantState.ACTING.value: 0.10,
            AssistantState.SPEAKING.value: 0.13,
            AssistantState.SUCCESS.value: 0.12,
            AssistantState.ERROR.value: 0.07,
        }.get(self._state, 0.02)
        radius = base_r * (
            1.0 + 0.025 * breathing + state_boost + self._level * 0.28
        )

        aura = QRadialGradient(center, radius * 3.0)
        aura.setColorAt(0.0, self._color(int(85 + self._level * 90)))
        aura.setColorAt(0.23, self._color(42))
        aura.setColorAt(0.64, self._color(10))
        aura.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(aura)
        painter.drawEllipse(center, radius * 3.0, radius * 3.0)

        particle_speed = 0.55 if self._state == AssistantState.IDLE.value else 1.0
        painter.setBrush(Qt.NoBrush)
        for i, (angle, distance, size, opacity) in enumerate(self._particles):
            a = angle + self._phase * particle_speed * (0.35 + (i % 7) * 0.055)
            drift = 1.0 + math.sin(self._phase * 2.1 + i) * 0.055
            r = radius * distance * drift
            x = cx + math.cos(a) * r
            y = cy + math.sin(a) * r * 0.63
            painter.setPen(Qt.NoPen)
            painter.setBrush(self._color(int(35 + opacity * 125)))
            painter.drawEllipse(QPointF(x, y), size * 1.8, size * 1.8)

        for idx, mult in enumerate((1.42, 1.67, 1.96)):
            ring_r = radius * mult
            pen = QPen(self._color(46 - idx * 8))
            pen.setWidthF(1.0)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(center, ring_r, ring_r)

            arc_pen = QPen(self._color(120 - idx * 18))
            arc_pen.setWidthF(1.8 if idx == 0 else 1.2)
            painter.setPen(arc_pen)
            angle = int((self._phase * (95 + idx * 35) + idx * 73) * 16)
            span = int((68 - idx * 10) * 16)
            arc_rect = QRectF(
                cx - ring_r,
                cy - ring_r,
                ring_r * 2,
                ring_r * 2,
            )
            painter.drawArc(arc_rect, angle, span)
            painter.drawArc(arc_rect, angle + 180 * 16, span // 2)

        core_gradient = QRadialGradient(center, radius)
        core_gradient.setColorAt(0.0, QColor(232, 252, 255, 245))
        core_gradient.setColorAt(0.10, self._color(245))
        core_gradient.setColorAt(0.38, self._color(150))
        core_gradient.setColorAt(0.72, self._color(38))
        core_gradient.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(core_gradient)
        painter.drawEllipse(center, radius, radius)

        split_pen = QPen(QColor(225, 250, 255, 180))
        split_pen.setWidthF(1.6)
        painter.setPen(split_pen)
        aperture = radius * (0.76 + self._level * 0.08)
        for offset in (-1, 1):
            start = 35 if offset > 0 else 215
            painter.drawArc(
                QRectF(
                    cx - aperture,
                    cy - aperture * 0.64,
                    aperture * 2,
                    aperture * 1.28,
                ),
                int((start + math.sin(self._phase * 2.0) * 9) * 16),
                int(110 * 16),
            )

        singularity = radius * (0.18 + self._level * 0.08)
        singularity_gradient = QRadialGradient(center, singularity)
        singularity_gradient.setColorAt(0.0, QColor(255, 255, 255, 255))
        singularity_gradient.setColorAt(0.28, self._color(240))
        singularity_gradient.setColorAt(1.0, self._color(0))
        painter.setBrush(singularity_gradient)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(center, singularity, singularity)

        if self._state in {
            AssistantState.LISTENING.value,
            AssistantState.SPEAKING.value,
        }:
            width = min(rect.width() * 0.62, 600)
            left = cx - width / 2
            baseline = rect.height() * 0.82
            step = width / max(1, len(self._history) - 1)
            wave_pen = QPen(self._color(135))
            wave_pen.setWidthF(1.4)
            painter.setPen(wave_pen)

            previous = None
            for i, value in enumerate(self._history):
                x = left + i * step
                amplitude = 6.0 + value * 42.0
                y = baseline + math.sin(i * 0.58 + self._phase * 8.0) * amplitude
                point = QPointF(x, y)
                if previous is not None:
                    painter.drawLine(previous, point)
                previous = point


class JarvisWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Jarvis Personal")
        self.setMinimumSize(980, 680)
        self.resize(1180, 760)
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.setStyleSheet("background-color: rgb(2, 7, 13);")
        self._drag_position = None
        self._close_requested = False
        self._allow_close = False

        self.orb = ReactiveOrb(self)

        self.title = QLabel("J  A  R  V  I  S")
        self.title.setAlignment(Qt.AlignCenter)
        self.title.setStyleSheet(
            "color: rgba(207,244,255,220);"
            "font-size: 16px;"
            "font-weight: 600;"
            "letter-spacing: 8px;"
        )

        self.state_label = QLabel("INITIALISATION")
        self.state_label.setAlignment(Qt.AlignCenter)
        self.state_label.setStyleSheet(
            "color: rgba(119,225,255,230);"
            "font-size: 13px;"
            "font-weight: 600;"
            "letter-spacing: 3px;"
        )

        self.status_label = QLabel("Démarrage…")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet(
            "color: rgba(225,244,250,220);"
            "font-size: 18px;"
            "font-weight: 400;"
        )

        self.transcript_label = QLabel("")
        self.transcript_label.setAlignment(Qt.AlignCenter)
        self.transcript_label.setWordWrap(True)
        self.transcript_label.setMaximumWidth(820)
        self.transcript_label.setStyleSheet(
            "color: rgba(242,250,252,235);"
            "font-size: 22px;"
            "font-weight: 400;"
            "padding: 8px 28px;"
        )

        self.detail_label = QLabel("")
        self.detail_label.setAlignment(Qt.AlignCenter)
        self.detail_label.setStyleSheet(
            "color: rgba(138,177,192,185);"
            "font-size: 11px;"
            "letter-spacing: 1px;"
        )

        self.conversation_box = QTextBrowser()
        self.conversation_box.setReadOnly(True)
        self.conversation_box.setMinimumHeight(150)
        self.conversation_box.setMaximumHeight(240)
        self.conversation_box.setStyleSheet(
            "QTextBrowser {"
            "background-color: rgba(5,16,25,210);"
            "color: rgba(226,244,250,235);"
            "border: 1px solid rgba(80,160,190,90);"
            "border-radius: 10px;"
            "font-size: 13px;"
            "padding: 10px;"
            "}"
        )
        self._conversation_lines: list[str] = []

        self.hint_label = QLabel("ESC · fermer    F11 · plein écran")
        self.hint_label.setAlignment(Qt.AlignCenter)
        self.hint_label.setStyleSheet(
            "color: rgba(90,125,140,140);"
            "font-size: 10px;"
            "letter-spacing: 1px;"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 28, 40, 24)
        layout.setSpacing(4)
        layout.addWidget(self.title)
        layout.addWidget(self.state_label)
        layout.addWidget(self.orb, 1)
        layout.addWidget(self.status_label)
        layout.addWidget(self.transcript_label)
        layout.addWidget(self.conversation_box)
        layout.addWidget(self.detail_label)
        layout.addWidget(self.hint_label)

        self._thread = QThread(self)
        self._worker = AssistantWorker()
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._on_worker_thread_finished)
        self._worker.state_changed.connect(self._on_state)
        self._worker.status_changed.connect(self.status_label.setText)
        self._worker.transcript_changed.connect(self._on_transcript)
        self._worker.response_changed.connect(self._on_response)
        self._worker.detail_changed.connect(self.detail_label.setText)
        self._worker.audio_level_changed.connect(self.orb.set_audio_level)
        self._worker.log_line.connect(print)

        self._thread.start()

        if settings.ui_fullscreen:
            self.showFullScreen()

    def _on_state(self, state: str) -> None:
        self.orb.set_state(state)
        try:
            enum_state = AssistantState(state)
            self.state_label.setText(STATE_LABELS[enum_state])
        except ValueError:
            self.state_label.setText(state.upper())

        color = STATE_COLORS.get(state, QColor(74, 224, 255))
        self.state_label.setStyleSheet(
            f"color: rgba({color.red()},{color.green()},{color.blue()},230);"
            "font-size: 13px;"
            "font-weight: 600;"
            "letter-spacing: 3px;"
        )

    def _append_conversation(self, speaker: str, text: str) -> None:
        value = (text or "").strip()
        if not value:
            return
        self._conversation_lines.append(f"{speaker}: {value}")
        self._conversation_lines = self._conversation_lines[-80:]
        self.conversation_box.setPlainText(
            "\n\n".join(self._conversation_lines)
        )
        bar = self.conversation_box.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _on_transcript(self, text: str) -> None:
        self.transcript_label.setText(f"« {text} »" if text else "")
        if text:
            self._append_conversation("Vous", text)

    def _on_response(self, text: str) -> None:
        self._append_conversation("Jarvis", text)

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

        # Never destroy the QThread while a local model/TTS request is still
        # running. Ask the worker to stop, keep the window alive, and close only
        # after worker.finished -> thread.finished.
        self._close_requested = True
        self.status_label.setText("Arrêt de Jarvis…")
        self.hint_label.setText("Veuillez patienter pendant l'arrêt en cours")
        self._worker.stop()
        event.ignore()


def run_ui() -> int:
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("Jarvis Personal")
    app.setFont(QFont("Segoe UI", 10))
    window = JarvisWindow()
    window.show()
    return app.exec()
