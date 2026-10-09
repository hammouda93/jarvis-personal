"""Independent, operator-controlled preferences for procedural knowledge.

Personal memory, mission checkpoints and native tools are deliberately outside
this policy. Preference changes are admitted by the existing serialized worker.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import threading

_KEYS = {"skills_enabled", "learning_enabled"}
_DEFAULTS = {"skills_enabled": False, "learning_enabled": False}
_live_flags = None
_lock = threading.RLock()


def preferences_path() -> Path:
    root = os.getenv("JARVIS_DATA_DIR", "").strip()
    if not root:
        local = os.getenv("LOCALAPPDATA", "").strip()
        root = str(Path(local) / "JarvisPersonal" if local else Path.home() / ".jarvis_personal")
    return Path(root) / "operational_preferences.json"


def validate_flags(flags: dict) -> dict:
    if not isinstance(flags, dict) or set(flags) != _KEYS or any(type(v) is not bool for v in flags.values()):
        raise ValueError("invalid_operational_preferences")
    return dict(flags)


def load_preferences(path: Path | None = None) -> dict:
    try:
        packet = json.loads((path or preferences_path()).read_text(encoding="utf-8"))
        if not isinstance(packet, dict) or set(packet) != {"schema", *_KEYS} or packet["schema"] != 1:
            raise ValueError("invalid_operational_preferences")
        return validate_flags({key: packet[key] for key in _KEYS})
    except (OSError, ValueError, TypeError):
        # An unreadable file must not silently reenable learning from legacy env.
        return dict(_DEFAULTS)


def effective_flags(config) -> dict:
    with _lock:
        if _live_flags is not None:
            return dict(_live_flags)
    return {"skills_enabled": bool(getattr(config, "skills_enabled", False)),
            "learning_enabled": bool(getattr(config, "operational_learning_enabled", False))}


def skills_enabled(config) -> bool:
    return effective_flags(config)["skills_enabled"]


def learning_enabled(config) -> bool:
    return effective_flags(config)["learning_enabled"]


def save_preferences(flags: dict, *, path: Path | None = None) -> dict:
    global _live_flags
    checked = validate_flags(flags)
    target = path or preferences_path()
    with _lock:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".operational_preferences_", dir=target.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({"schema": 1, **checked}, stream, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        # Publish only after the durable replace succeeds.
        _live_flags = checked
        return dict(checked)
