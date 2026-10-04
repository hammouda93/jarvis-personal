"""Replaceable perception/grounding contract and strict local output validation."""
from __future__ import annotations

import math
import base64
import json
import time
import urllib.request
from urllib.parse import urlparse
from typing import Any, Protocol

from .ui_geometry import normalized_box


class VisionProvider(Protocol):
    def understand(self, image: bytes, *, focus: str, metadata: dict[str, Any]) -> dict[str, Any]: ...
    def ground(self, image: bytes, *, target: str, metadata: dict[str, Any]) -> dict[str, Any]: ...


class LocalOllamaVisionProvider:
    """Current local provider, usable independently of the Cerebras planner."""
    def __init__(self, *, endpoint: str, model: str, grounding_model: str = "",
                 timeout_s: float = 20, num_predict: int = 900, schema_enabled: bool = True,
                 local_only: bool = True):
        if local_only and urlparse(endpoint).hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("vision_local_only")
        self.endpoint, self.model = endpoint, model
        self.grounding_model = grounding_model or model
        self.timeout_s, self.num_predict, self.schema_enabled = timeout_s, num_predict, schema_enabled

    def request(self, image: bytes, *, prompt: str, grounding: bool = False) -> tuple[str, float]:
        schema = TARGET_SCHEMA if grounding else OBSERVATION_SCHEMA
        payload = {
            "model": self.grounding_model if grounding else self.model, "stream": False,
            "format": schema if self.schema_enabled else "json",
            "messages": [{"role": "user", "content": prompt,
                          "images": [base64.b64encode(image).decode("ascii")]}],
            "options": {"temperature": 0, "num_predict": max(128, int(self.num_predict))},
        }
        request = urllib.request.Request(self.endpoint, data=json.dumps(payload).encode("utf-8"),
                                         headers={"Content-Type": "application/json"}, method="POST")
        started = time.perf_counter()
        with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
            body = json.loads(response.read().decode("utf-8", errors="replace"))
        content = str((body.get("message") or {}).get("content") or body.get("response") or "").strip()
        return content, time.perf_counter()-started

    def understand(self, image: bytes, *, focus: str, metadata: dict[str, Any]) -> dict[str, Any]:
        content, elapsed = self.request(image, prompt=focus)
        result = json.loads(content)
        if not isinstance(result, dict) or not isinstance(result.get("targets"), list):
            raise ValueError("invalid_visual_observation")
        return {**metadata, "observation_json": result, "model": self.model, "seconds": elapsed}

    def ground(self, image: bytes, *, target: str, metadata: dict[str, Any]) -> dict[str, Any]:
        content, elapsed = self.request(image, prompt=target, grounding=True)
        result = validated_localization(json.loads(content))
        if result is None:
            raise ValueError("visual_target_not_grounded")
        return {**metadata, **result, "model": self.grounding_model, "seconds": elapsed}


TARGET_SCHEMA = {
    "type": "object",
    "properties": {
        "found": {"type": "boolean"}, "label": {"type": "string"}, "role": {"type": "string"},
        "box_1000": {"type": "array", "items": {"type": "number", "minimum": 0, "maximum": 1000},
                     "minItems": 4, "maxItems": 4},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1}, "reason": {"type": "string"},
    },
    "required": ["found", "label", "role", "box_1000", "confidence", "reason"],
    "additionalProperties": False,
}
OBSERVATION_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "visible_text": {"type": "array", "items": {"type": "string"}},
        "targets": {"type": "array", "items": {
            "type": "object", "properties": {
                "label": {"type": "string"}, "role": {"type": "string"},
                "region": {"type": "string"}, "value": {"type": "string"},
                "focused": {"type": "boolean"},
                "box_1000": TARGET_SCHEMA["properties"]["box_1000"],
                "confidence": TARGET_SCHEMA["properties"]["confidence"],
            }, "required": ["label", "role", "region", "value", "focused", "box_1000", "confidence"],
            "additionalProperties": False,
        }},
        "ambiguities": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "visible_text", "targets", "ambiguities"],
    "additionalProperties": False,
}


def validated_localization(parsed: dict[str, Any]) -> dict[str, Any] | None:
    confidence = parsed.get("confidence")
    box = normalized_box(parsed.get("box_1000"))
    if (
        parsed.get("found") is not True or box is None
        or isinstance(confidence, bool) or not isinstance(confidence, (int, float))
        or not math.isfinite(confidence) or not 0 <= confidence <= 1
        or not isinstance(parsed.get("label", ""), str) or not isinstance(parsed.get("role", ""), str)
    ):
        return None
    return {**parsed, "box_1000": list(box), "confidence": float(confidence)}
