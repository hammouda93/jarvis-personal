from __future__ import annotations

import base64
import io
import json
import os
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import settings
from .windows_perception import _native_target_window


@dataclass(frozen=True)
class ScreenObservation:
    success: bool
    message: str
    detail: str = ""


def _vision_endpoint() -> str:
    base = settings.ollama_base_url.rstrip("/")
    return f"{base}/api/chat"


def _is_local_endpoint(url: str) -> bool:
    try:
        host = (urllib.parse.urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return host in {"127.0.0.1", "localhost", "::1"}


def _capture_window_bytes(
    title: str | None = None,
) -> tuple[bytes, dict[str, Any]]:
    try:
        from PIL import ImageGrab
    except ImportError as exc:
        raise RuntimeError(
            "Pillow n'est pas installé. Exécutez pip install -r requirements.txt."
        ) from exc

    item = _native_target_window(title)
    if item is None:
        raise RuntimeError(
            f"Fenêtre introuvable: {title}." if title else "Aucune fenêtre de travail détectée."
        )

    bounds = tuple(item.get("bounds") or (0, 0, 0, 0))
    left, top, right, bottom = [int(value) for value in bounds]
    if right <= left or bottom <= top:
        raise RuntimeError("La fenêtre détectée n'a pas de dimensions valides.")

    image = ImageGrab.grab(
        bbox=(left, top, right, bottom),
        all_screens=True,
    ).convert("RGB")

    max_width = max(640, int(settings.vision_max_width))
    if image.width > max_width:
        ratio = max_width / float(image.width)
        image = image.resize(
            (max_width, max(1, int(image.height * ratio)))
        )

    buffer = io.BytesIO()
    image.save(
        buffer,
        format="JPEG",
        quality=78,
        optimize=True,
    )
    return buffer.getvalue(), {
        "title": str(item.get("title") or ""),
        "bounds": [left, top, right, bottom],
        "captured_width": image.width,
        "captured_height": image.height,
    }


def _save_debug_evidence(data: bytes, metadata: dict[str, Any]) -> str:
    local = os.getenv("LOCALAPPDATA", "").strip()
    root = (
        Path(local) / "JarvisPersonal" / "evidence"
        if local
        else Path.home() / ".jarvis_personal" / "evidence"
    )
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    path = root / f"screen_{stamp}.jpg"
    path.write_bytes(data)

    files = sorted(
        root.glob("screen_*.jpg"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for old in files[max(1, int(settings.vision_evidence_max_files)) :]:
        try:
            old.unlink()
        except OSError:
            pass
    return str(path)


def observe_screen(
    *,
    title: str | None = None,
    focus: str = "",
) -> ScreenObservation:
    if not settings.vision_enabled:
        return ScreenObservation(
            False,
            "La perception visuelle locale est désactivée.",
            "vision_disabled",
        )

    endpoint = _vision_endpoint()
    if settings.vision_local_only and not _is_local_endpoint(endpoint):
        return ScreenObservation(
            False,
            "La perception visuelle refuse un endpoint distant.",
            "vision_local_only",
        )

    try:
        image_bytes, metadata = _capture_window_bytes(title)
    except Exception as exc:
        return ScreenObservation(
            False,
            "Impossible de capturer la fenêtre.",
            str(exc),
        )

    question = (focus or "").strip()
    prompt = (
        "Tu es le capteur visuel local de Jarvis. Analyse uniquement ce qui est "
        "réellement visible dans cette capture Windows. Ne déduis pas des éléments "
        "cachés. Réponds brièvement en JSON avec les clés: summary, visible_text, "
        "important_regions, candidate_actions, ambiguities. Pour candidate_actions, "
        "décris les éléments par leur texte et position relative (haut gauche, centre, "
        "etc.), jamais par des coordonnées absolues."
    )
    if question:
        prompt += f"\nObjectif précis à observer: {question[:700]}"

    payload = {
        "model": settings.vision_model,
        "stream": False,
        "messages": [
            {
                "role": "user",
                "content": prompt,
                "images": [
                    base64.b64encode(image_bytes).decode("ascii")
                ],
            }
        ],
        "options": {
            "temperature": 0,
            "num_predict": max(128, int(settings.vision_num_predict)),
        },
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    started = time.perf_counter()
    try:
        with urllib.request.urlopen(
            request,
            timeout=float(settings.vision_timeout_s),
        ) as response:
            body = json.loads(
                response.read().decode("utf-8", errors="replace")
            )
    except Exception as exc:
        return ScreenObservation(
            False,
            "Le capteur visuel local n'est pas disponible.",
            str(exc),
        )

    content = str(
        ((body.get("message") or {}).get("content"))
        or body.get("response")
        or ""
    ).strip()
    if not content:
        return ScreenObservation(
            False,
            "Le capteur visuel local n'a retourné aucune observation.",
            "empty_vision_response",
        )

    saved_path = ""
    if settings.vision_save_evidence:
        try:
            saved_path = _save_debug_evidence(image_bytes, metadata)
        except OSError:
            saved_path = ""

    detail = {
        **metadata,
        "model": settings.vision_model,
        "seconds": round(time.perf_counter() - started, 3),
        "observation": content[:8000],
        "evidence_saved": bool(saved_path),
    }
    if saved_path:
        detail["evidence_path"] = saved_path

    return ScreenObservation(
        True,
        "Observation visuelle locale terminée.",
        json.dumps(detail, ensure_ascii=False),
    )
