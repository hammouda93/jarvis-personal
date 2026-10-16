"""Code-native planets and event-driven shooting stars for the operator canvas."""
import math
import re
import time

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainterPath, QPen, QRadialGradient


PLANETS = {
    "research": ("Recherche", "#77c8ff"), "browser": ("Navigateur", "#74dfd0"),
    "windows": ("Windows", "#71aaff"), "documents": ("Documents", "#edc17e"),
    "data": ("Données", "#a59df5"), "reports": ("Rapports", "#dfb9e8"),
    "communications": ("Messages", "#f2a6bb"), "developer": ("Développement", "#8fd6ab"),
    "ms_football": ("MS Football", "#e8c96c"), "automations": ("Veille", "#bba8f4"),
    "mcp": ("Services MCP", "#8cc4e4"), "workspace": ("Mon quotidien", "#8fe6d7"),
    "voice_channel": ("Appels", "#f0ac86"),
}
_STATES = {"running": "Analyse en cours", "report_ready": "Rapport disponible", "needs_review": "À examiner",
           "failed": "Erreur", "stopped": "Suspendu"}


class UniverseRenderer:
    def __init__(self, host):
        self.host = host
        self.data = {}
        self.states = {}
        self.selected = "interaction"
        self.provider = "Cerveau configuré"
        self._last_event = 0
        self.meteors = []
        self._last_tool = ""

    def positions(self):
        w, h = self.host.width(), self.host.height()
        if w < 620 or h < 420:
            core = QPointF(w * .5, max(70, h * .25))
            keys = ["research", "browser", "windows", "ms_football", "automations", "workspace", "mcp", "voice_channel"]
            if h < 460:
                keys = ["research", "ms_football", "automations", "voice_channel"]
            if h < 230:
                keys = ["automations", "voice_channel"]
            columns = 3 if len(keys) > 4 and w >= 430 else 2
            rows = math.ceil(len(keys) / columns)
            result = {"interaction": core}
            top, bottom = (max(130, h*.40), h-80) if h >= 330 else (h*.46, h-65)
            if h < 230:
                top = bottom = h*.72
            for i, key in enumerate(keys):
                result[key] = QPointF(w * ((i % columns + .5) / columns), top + (bottom-top) * (i // columns) / max(1, rows-1))
            return result
        center = QPointF(w * .5, h * .47)
        keys = list(PLANETS)[:11]
        result = {"interaction": center}
        for i, key in enumerate(keys):
            angle = -math.pi / 2 + (i + .15) * math.tau / len(keys)
            result[key] = QPointF(center.x() + w * .385 * math.cos(angle), center.y() + h * .27 * math.sin(angle))
        result["workspace"] = QPointF(w * .12, h-88)
        result["voice_channel"] = QPointF(w * .88, h-88)
        return result

    def pulse(self, source, target, *, kind="dispatch"):
        if source == target:
            return
        self.meteors.append({"source": source, "target": target, "start": self.host._phase, "kind": kind})
        self.meteors = self.meteors[-24:]

    def apply_snapshot(self, runtime):
        self.data = runtime.get("orchestra") or {}
        self.states = {}
        for run in self.data.get("runs", []):
            self.states.setdefault(run["role"], run["state"])
        for event in self.data.get("events", []):
            if event["sequence"] > self._last_event:
                if time.time() - event["created"] < 15:
                    self.pulse(event["source"], event["target"], kind=event["kind"])
                self._last_event = event["sequence"]
        self.host.update()

    def ingest_log(self, text):
        match = re.search(r"^\[AGENT_MODEL\] effective_provider=([^\s]+)", text)
        if match:
            self.provider = match.group(1).capitalize()
        call = re.search(r"^\[AGENT_TOOL\] call=([^\s]+)", text)
        if call:
            name = call.group(1)
            key = ("workspace" if name.startswith("workspace_") else "voice_channel" if name == "orchestra_call_owner"
                else "automations" if name.startswith("orchestra_") else "mcp" if name.startswith("mcp__")
                else "browser" if name.startswith("browser_") or name == "open_url" else "ms_football" if name.startswith("msf_")
                else "research" if name in {"research_web", "search_web"} else "windows")
            self._last_tool = key
            self.pulse("interaction", key)
        if text.startswith("[AGENT_TOOL] result=") and self._last_tool:
            self.pulse(self._last_tool, "interaction", kind="report" if "success=True" in text else "pause")

    def hit(self, point):
        compact = self.host.width() < 620 or self.host.height() < 420
        for key, center in self.positions().items():
            radius = (37 if compact else 63) if key == "interaction" else (23 if compact else 32)
            if (center.x()-point.x()) ** 2 + (center.y()-point.y()) ** 2 <= (radius+10) ** 2:
                return key
        return None

    def status(self, key):
        if key == "workspace":
            return "Profil et activités locales"
        if key == "voice_channel":
            return "À connecter" if not (self.data.get("voice") or {}).get("data", {}).get("enabled") else "Canal configuré · accès à vérifier"
        if key == "automations":
            return "Veille active" if ((self.data.get("engine") or {}).get("online") and
                (self.data.get("watch") or {}).get("data", {}).get("enabled")) else "Veille arrêtée"
        return _STATES.get(self.states.get(key), "À solliciter")

    def paint(self, painter):
        host = self.host
        rect = QRectF(host.rect())
        host._draw_background(painter, rect)
        w, h = host.width(), host.height()
        compact = w < 620 or h < 420
        points = self.positions()
        center = points["interaction"]
        painter.setPen(QColor("#8bbaca"))
        painter.setFont(QFont("Segoe UI", 9 if compact else 11, QFont.DemiBold))
        painter.drawText(QRectF(12, 12, w-24, 22), Qt.AlignLeft, "UNIVERS JARVIS")
        if not compact:
            painter.setFont(QFont("Segoe UI", 9))
            painter.setPen(QColor("#7894ae"))
            painter.drawText(QRectF(12, 35, w-24, 20), Qt.AlignLeft, "Planètes : agents et espaces · Sillages lumineux : échanges observés")
            for ratio in (.23, .335, .40):
                painter.setPen(QPen(QColor(101, 143, 179, 30), .8))
                painter.setBrush(Qt.NoBrush)
                painter.drawEllipse(center, w * (ratio + .05), h * ratio)
        for key, point in points.items():
            if key == "interaction":
                continue
            path = QPainterPath(center)
            path.quadTo(QPointF((center.x()+point.x())/2, point.y()), point)
            painter.setPen(QPen(QColor(75, 127, 159, 30), .7))
            painter.drawPath(path)
        self.meteors = [m for m in self.meteors if host._phase - m["start"] < 5]
        for meteor in self.meteors:
            if meteor["source"] not in points or meteor["target"] not in points:
                continue
            a, b = points[meteor["source"]], points[meteor["target"]]
            path = QPainterPath(a)
            path.quadTo(QPointF((a.x()+b.x())/2, min(a.y(), b.y())-35), b)
            progress = min(.995, max(0., (host._phase-meteor["start"]) / 5))
            color = QColor("#e7b68b" if meteor["kind"] == "pause" else "#c5b5ff" if meteor["kind"] == "report" else "#90f0ed")
            for index in range(14):
                end = max(0, progress-index*.011)
                start = max(0, end-.018)
                color.setAlpha(max(0, 230-index*16))
                painter.setPen(QPen(color, max(.5, 2.8-index*.14)))
                painter.drawLine(path.pointAtPercent(start), path.pointAtPercent(end))
            point = path.pointAtPercent(progress)
            glow = QRadialGradient(point, 12)
            glow.setColorAt(0, QColor("#f5ffff"))
            glow.setColorAt(.2, color)
            glow.setColorAt(1, QColor(110, 230, 255, 0))
            painter.setBrush(glow)
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(point, 12, 12)
        self._core(painter, center, compact)
        for key, point in points.items():
            if key != "interaction":
                self._planet(painter, key, point, compact)
        painter.setFont(QFont("Segoe UI", 8))
        painter.setPen(QColor("#92a9bf"))
        footer = "Clique une planète pour piloter" if not compact else "Équipe complète dans PILOTAGE"
        if h >= 330:
            painter.drawText(QRectF(10, h-22, w-20, 18), Qt.AlignCenter, footer)

    def _core(self, painter, center, compact):
        radius = 34 if compact else 62
        glow = QRadialGradient(center, radius*2.4)
        glow.setColorAt(0, QColor(90, 233, 248, 105))
        glow.setColorAt(.5, QColor(50, 135, 230, 25))
        glow.setColorAt(1, QColor(0, 0, 0, 0))
        painter.setBrush(glow)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(center, radius*2.4, radius*2.4)
        face = QRadialGradient(center-QPointF(radius*.3, radius*.3), radius*1.5)
        face.setColorAt(0, QColor("#2f91ae"))
        face.setColorAt(.5, QColor("#103b65"))
        face.setColorAt(1, QColor("#061426"))
        painter.setBrush(face)
        painter.setPen(QPen(QColor("#94e9f0"), 1.3))
        painter.drawEllipse(center, radius, radius)
        painter.setBrush(Qt.NoBrush)
        for i in range(3):
            painter.setPen(QPen(QColor(100, 215, 239, 80-i*15), .8))
            painter.drawArc(QRectF(center.x()-radius-5-i*5, center.y()-radius-5-i*5, (radius+5+i*5)*2, (radius+5+i*5)*2), int(self.host._phase*180+i*80)*16, 140*16)
        painter.setPen(QColor("#e5ffff"))
        painter.setFont(QFont("Segoe UI", 10 if compact else 16, QFont.DemiBold))
        painter.drawText(QRectF(center.x()-radius, center.y()-16, radius*2, 28), Qt.AlignCenter, "JARVIS")
        if not compact:
            painter.setFont(QFont("Segoe UI", 8))
            painter.drawText(QRectF(center.x()-90, center.y()+12, 180, 18), Qt.AlignCenter, "CHEF D’ORCHESTRE")
            painter.setPen(QColor("#90bbce"))
            painter.drawText(QRectF(center.x()-90, center.y()+radius+25, 180, 20), Qt.AlignCenter, self.provider)

    def _planet(self, painter, key, center, compact):
        radius = 20 if compact else 29
        label, color = PLANETS[key]
        active = self.states.get(key) == "running"
        selected = key == self.selected
        tint = QColor(color)
        if active or selected:
            glow = QRadialGradient(center, radius*2)
            tint.setAlpha(80 if active else 35)
            glow.setColorAt(0, tint)
            tint.setAlpha(0)
            glow.setColorAt(1, tint)
            painter.setPen(Qt.NoPen)
            painter.setBrush(glow)
            painter.drawEllipse(center, radius*2, radius*2)
        face = QRadialGradient(center-QPointF(radius*.35, radius*.35), radius*1.6)
        face.setColorAt(0, QColor(color).lighter(115 if active else 85))
        face.setColorAt(.35, QColor(color).darker(180))
        face.setColorAt(1, QColor("#061425"))
        painter.setBrush(face)
        painter.setPen(QPen(QColor(color), 1.5 if selected or active else .7))
        painter.drawEllipse(center, radius, radius)
        painter.save()
        painter.translate(center)
        painter.rotate(-23)
        painter.setBrush(Qt.NoBrush)
        ring = QColor(color)
        ring.setAlpha(120 if selected or active else 50)
        painter.setPen(QPen(ring, 2 if key in {"ms_football", "automations", "voice_channel"} else .6))
        painter.drawEllipse(QRectF(-radius*1.45, -radius*.34, radius*2.9, radius*.68))
        painter.restore()
        painter.setFont(QFont("Segoe UI", 8 if compact else 9, QFont.DemiBold))
        painter.setPen(QColor("#e0edf2"))
        width = min(140, self.host.width()/3-2) if compact else 150
        x = max(1, min(self.host.width()-width-1, center.x()-width/2))
        painter.drawText(QRectF(x, center.y()+radius+5, width, 20 if compact else 28), Qt.AlignHCenter | (Qt.AlignVCenter if compact else Qt.TextWordWrap), label)
        if not compact and key not in {"workspace", "voice_channel"}:
            painter.setFont(QFont("Segoe UI", 8))
            painter.setPen(QColor(color) if active else QColor("#829bb0"))
            painter.drawText(QRectF(x-8, center.y()+radius+30, width+16, 25), Qt.AlignCenter | Qt.TextWordWrap, self.status(key))
