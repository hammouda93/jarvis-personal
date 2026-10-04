from __future__ import annotations

import base64
import io
import json
import os
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import settings
from .windows_perception import _native_target_window, _send_keys


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


def _extract_json_object(content: str) -> dict[str, Any]:
    """Parse a JSON object from a local vision response, including fenced JSON."""
    text = str(content or "").strip()
    if not text:
        return {}
    if text.startswith("```"):
        text = re.sub(r"^\s*```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```\s*$", "", text)
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            value = json.loads(text[start : end + 1])
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _call_local_vision(
    image_bytes: bytes,
    *,
    prompt: str,
    num_predict: int | None = None,
) -> tuple[str, float]:
    endpoint = _vision_endpoint()
    payload = {
        "model": settings.vision_model,
        "stream": False,
        "format": "json",
        "keep_alive": "30m",
        "messages": [
            {
                "role": "user",
                "content": prompt,
                "images": [base64.b64encode(image_bytes).decode("ascii")],
            }
        ],
        "options": {
            "temperature": 0,
            "num_predict": max(
                96,
                int(
                    num_predict
                    if num_predict is not None
                    else settings.vision_num_predict
                ),
            ),
        },
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    with urllib.request.urlopen(
        request,
        timeout=float(settings.vision_timeout_s),
    ) as response:
        body = json.loads(
            response.read().decode("utf-8", errors="replace")
        )
    content = str(
        ((body.get("message") or {}).get("content"))
        or body.get("response")
        or ""
    ).strip()
    return content, time.perf_counter() - started


def _normalized_box(value: Any) -> list[int] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        box = [int(round(float(item))) for item in value]
    except (TypeError, ValueError):
        return None
    x1, y1, x2, y2 = box
    if not (0 <= x1 < x2 <= 1000 and 0 <= y1 < y2 <= 1000):
        return None
    return box


def _capture_window_bytes(
    title: str | None = None,
    *,
    max_width: int | None = None,
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

    target_width = max(
        640,
        int(
            max_width
            if max_width is not None
            else settings.vision_max_width
        ),
    )
    if image.width > target_width:
        ratio = target_width / float(image.width)
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
    compact: bool = False,
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
        image_bytes, metadata = _capture_window_bytes(
            title,
            max_width=(
                min(1024, max(640, int(settings.vision_max_width)))
                if compact
                else None
            ),
        )
    except Exception as exc:
        return ScreenObservation(
            False,
            "Impossible de capturer la fenêtre.",
            str(exc),
        )

    question = (focus or "").strip()
    if compact:
        prompt = (
            "Analyse uniquement cette capture Windows pour la mission donnée. "
            "Réponds uniquement en JSON compact avec: summary, targets, ambiguities. "
            "targets contient seulement les contrôles visibles utiles à la mission, "
            "avec label, role, box_1000=[x1,y1,x2,y2] et confidence. "
            "Ne décris pas le décor et n'invente aucune cible."
        )
    else:
        prompt = (
            "Tu es le capteur visuel local de Jarvis. Analyse uniquement ce qui est "
            "réellement visible dans cette capture Windows. Ne déduis jamais un élément "
            "caché. Réponds uniquement en JSON avec les clés: summary, visible_text, "
            "important_regions, candidate_actions, targets, ambiguities. "
            "targets doit contenir uniquement les contrôles clairement visibles et utiles, "
            "chaque cible avec label, role, box_1000=[x1,y1,x2,y2] dans un repère normalisé "
            "0..1000 relatif à la capture, et confidence entre 0 et 1. "
            "N'invente pas de boîte si la cible n'est pas clairement localisable."
        )
    if question:
        prompt += f"\nObjectif précis à observer: {question[:700]}"

    try:
        content, elapsed = _call_local_vision(
            image_bytes,
            prompt=prompt,
            num_predict=(
                min(180, int(settings.vision_num_predict))
                if compact
                else None
            ),
        )
    except Exception as exc:
        return ScreenObservation(
            False,
            "Le capteur visuel local n'est pas disponible.",
            str(exc),
        )
    if not content:
        return ScreenObservation(
            False,
            "Le capteur visuel local n'a retourné aucune observation.",
            "empty_vision_response",
        )

    parsed = _extract_json_object(content)
    if not parsed:
        return ScreenObservation(
            False,
            "Le capteur visuel local n'a pas retourné une observation JSON exploitable.",
            "invalid_visual_observation",
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
        "seconds": round(elapsed, 3),
        "observation": content[:8000],
        "observation_json": parsed,
        "sensor": "visual",
        "compact": bool(compact),
        "evidence_saved": bool(saved_path),
    }
    if saved_path:
        detail["evidence_path"] = saved_path

    return ScreenObservation(
        True,
        "Observation visuelle locale terminée.",
        json.dumps(detail, ensure_ascii=False),
    )



def locate_visual_target(
    *,
    target: str,
    title: str | None = None,
) -> ScreenObservation:
    """Locate one clearly visible UI target using the local vision model."""
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

    description = str(target or "").strip()
    if len(description) < 2:
        return ScreenObservation(False, "La cible visuelle est trop vague.")

    try:
        image_bytes, metadata = _capture_window_bytes(title)
    except Exception as exc:
        return ScreenObservation(
            False,
            "Impossible de capturer la fenêtre.",
            str(exc),
        )

    prompt = (
        "Tu localises UNE cible visible dans une capture Windows pour Jarvis. "
        f"Cible demandée: {description[:500]!r}. "
        "Réponds uniquement en JSON: "
        '{"found":true|false,"label":"...","role":"...",'
        '"box_1000":[x1,y1,x2,y2],"confidence":0.0,"reason":"..."}. '
        "box_1000 utilise 0..1000 pour toute la largeur/hauteur de la capture. "
        "Ne retourne found=true que si la cible est clairement identifiable. "
        "En cas de plusieurs candidats ambigus, retourne found=false."
    )
    try:
        content, elapsed = _call_local_vision(
            image_bytes,
            prompt=prompt,
        )
    except Exception as exc:
        return ScreenObservation(
            False,
            "Le capteur visuel local n'est pas disponible.",
            str(exc),
        )

    parsed = _extract_json_object(content)
    box = _normalized_box(parsed.get("box_1000"))
    try:
        confidence = float(parsed.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    found = bool(parsed.get("found")) and box is not None
    if not found:
        return ScreenObservation(
            False,
            "La cible visuelle n'a pas été localisée de façon fiable.",
            json.dumps(
                {
                    **metadata,
                    "target": description,
                    "seconds": round(elapsed, 3),
                    "vision": parsed or content[:1200],
                },
                ensure_ascii=False,
            ),
        )

    detail = {
        **metadata,
        "target": description,
        "label": str(parsed.get("label") or "")[:200],
        "role": str(parsed.get("role") or "")[:120],
        "box_1000": box,
        "confidence": max(0.0, min(confidence, 1.0)),
        "reason": str(parsed.get("reason") or "")[:500],
        "model": settings.vision_model,
        "seconds": round(elapsed, 3),
    }
    return ScreenObservation(
        True,
        "Cible visuelle locale localisée.",
        json.dumps(detail, ensure_ascii=False),
    )


def click_visual_target(
    *,
    target: str,
    title: str | None = None,
) -> ScreenObservation:
    """Locate and click a visible target locally; always requires after-state verification."""
    if not settings.vision_actions_enabled:
        return ScreenObservation(
            False,
            "Les actions visuelles locales sont désactivées.",
            "vision_actions_disabled",
        )

    located = locate_visual_target(target=target, title=title)
    if not located.success:
        return located

    try:
        detail = json.loads(located.detail or "{}")
    except json.JSONDecodeError:
        detail = {}
    confidence = float(detail.get("confidence") or 0.0)
    threshold = max(0.0, min(float(settings.vision_min_confidence), 1.0))
    if confidence < threshold:
        return ScreenObservation(
            False,
            "La confiance visuelle est insuffisante pour cliquer.",
            json.dumps(
                {
                    **detail,
                    "required_confidence": threshold,
                },
                ensure_ascii=False,
            ),
        )

    box = _normalized_box(detail.get("box_1000"))
    bounds = list(detail.get("bounds") or [])
    if box is None or len(bounds) != 4:
        return ScreenObservation(
            False,
            "La cible visuelle ne contient pas une géométrie valide.",
            located.detail,
        )

    left, top, right, bottom = [int(value) for value in bounds]
    x1, y1, x2, y2 = box
    width = right - left
    height = bottom - top
    x = left + int(round(((x1 + x2) / 2.0) / 1000.0 * width))
    y = top + int(round(((y1 + y2) / 2.0) / 1000.0 * height))
    if not (left <= x <= right and top <= y <= bottom):
        return ScreenObservation(
            False,
            "Le point visuel calculé est hors de la fenêtre cible.",
            located.detail,
        )

    try:
        from pywinauto import mouse

        mouse.click(button="left", coords=(x, y))
    except Exception as exc:
        return ScreenObservation(
            False,
            "Le clic visuel local a échoué.",
            str(exc),
        )

    result = {
        **detail,
        "clicked_screen_point": [x, y],
        "verified": False,
        "note": (
            "Clic visuel envoyé. Réinspecter l'interface ou refaire une "
            "observation visuelle avant d'affirmer le résultat."
        ),
    }
    return ScreenObservation(
        True,
        "Clic visuel local envoyé.",
        json.dumps(result, ensure_ascii=False),
    )



def write_visual_target(
    *,
    target: str,
    text: str,
    title: str | None = None,
    mode: str = "replace",
) -> ScreenObservation:
    """Visually focus one field and type into that exact target."""
    value = str(text or "")
    if not value:
        return ScreenObservation(False, "Le texte à saisir est vide.")
    if len(value) > 4000:
        return ScreenObservation(
            False,
            "Le texte est trop long pour une saisie visuelle directe.",
        )

    normalized_mode = str(mode or "replace").strip().lower()
    if normalized_mode not in {"replace", "append", "insert"}:
        return ScreenObservation(
            False,
            "Mode d'écriture visuelle invalide.",
            normalized_mode,
        )

    clicked = click_visual_target(target=target, title=title)
    if not clicked.success:
        return clicked
    time.sleep(0.08)

    try:
        import win32clipboard

        previous_text: str | None = None
        try:
            win32clipboard.OpenClipboard()
            try:
                if win32clipboard.IsClipboardFormatAvailable(
                    win32clipboard.CF_UNICODETEXT
                ):
                    previous_text = win32clipboard.GetClipboardData(
                        win32clipboard.CF_UNICODETEXT
                    )
            except Exception:
                previous_text = None
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            previous_text = None

        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(
                value,
                win32clipboard.CF_UNICODETEXT,
            )
        finally:
            win32clipboard.CloseClipboard()

        if normalized_mode == "replace":
            _send_keys("^a")
        elif normalized_mode == "append":
            _send_keys("^{END}")
        _send_keys("^v")

        if previous_text is not None:
            try:
                win32clipboard.OpenClipboard()
                try:
                    win32clipboard.EmptyClipboard()
                    win32clipboard.SetClipboardText(
                        previous_text,
                        win32clipboard.CF_UNICODETEXT,
                    )
                finally:
                    win32clipboard.CloseClipboard()
            except Exception:
                pass
    except Exception as exc:
        return ScreenObservation(
            False,
            "La saisie dans la cible visuelle a échoué.",
            str(exc),
        )

    try:
        click_detail = json.loads(clicked.detail or "{}")
    except json.JSONDecodeError:
        click_detail = {}
    detail = {
        **click_detail,
        "mode": normalized_mode,
        "text_length": len(value),
        "verified": False,
        "note": (
            "Texte saisi dans une cible localisée visuellement. "
            "Réinspecter ou observer l'écran avant d'affirmer le résultat."
        ),
    }
    return ScreenObservation(
        True,
        "Texte saisi dans la cible visuelle.",
        json.dumps(detail, ensure_ascii=False),
    )
