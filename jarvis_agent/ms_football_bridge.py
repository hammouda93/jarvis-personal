from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .config import settings


@dataclass(frozen=True)
class MSFootballBridgeResult:
    success: bool
    message: str
    detail: str = ""


class MSFootballBridge:
    """Authenticated local client for the MS Football Django bridge."""

    def __init__(self) -> None:
        self.base_url = settings.ms_football_bridge_url
        self.token = settings.ms_football_bridge_token

    def available(self) -> bool:
        return bool(self.base_url and self.token)

    def call(
        self,
        tool: str,
        arguments: dict[str, Any] | None = None,
    ) -> MSFootballBridgeResult:
        if not self.available():
            return MSFootballBridgeResult(
                False,
                (
                    "Le connecteur MS Football n'est pas configuré. "
                    "Configurez JARVIS_MS_FOOTBALL_BRIDGE_TOKEN."
                ),
            )

        payload = json.dumps(
            {
                "tool": tool,
                "arguments": dict(arguments or {}),
            },
            ensure_ascii=False,
        ).encode("utf-8")
        req = urllib.request.Request(
            self.base_url + "/tool",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.token}",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=15.0) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", errors="replace")
            except Exception:
                detail = str(exc)
            return MSFootballBridgeResult(
                False,
                f"MS Football bridge error {exc.code}.",
                detail[:1200],
            )
        except urllib.error.URLError as exc:
            return MSFootballBridgeResult(
                False,
                "Le bridge MS Football n'est pas joignable sur ce PC.",
                str(exc),
            )
        except TimeoutError:
            return MSFootballBridgeResult(
                False,
                "Le bridge MS Football a mis trop de temps à répondre.",
            )
        except (ValueError, TypeError) as exc:
            return MSFootballBridgeResult(
                False,
                "Réponse invalide du bridge MS Football.",
                str(exc),
            )

        if not isinstance(data, dict) or not data.get("ok"):
            return MSFootballBridgeResult(
                False,
                "Le bridge MS Football a refusé la demande.",
                json.dumps(data, ensure_ascii=False)[:1200],
            )

        result = data.get("result")
        return MSFootballBridgeResult(
            True,
            f"MS Football: {tool} exécuté.",
            json.dumps(
                result,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        )


MS_FOOTBALL_BRIDGE = MSFootballBridge()
