from __future__ import annotations

import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import TextIO

from .config import PROJECT_ROOT


class _TeeStream:
    def __init__(self, primary: TextIO, mirror: TextIO) -> None:
        self.primary = primary
        self.mirror = mirror
        self._lock = threading.Lock()
        self.encoding = getattr(primary, "encoding", "utf-8")

    def write(self, value: str) -> int:
        text = str(value)
        with self._lock:
            written = self.primary.write(text)
            self.mirror.write(text)
            self.mirror.flush()
            try:
                self.primary.flush()
            except Exception:
                pass
        return written if isinstance(written, int) else len(text)

    def flush(self) -> None:
        with self._lock:
            try:
                self.primary.flush()
            except Exception:
                pass
            try:
                self.mirror.flush()
            except Exception:
                pass

    def isatty(self) -> bool:
        try:
            return bool(self.primary.isatty())
        except Exception:
            return False

    def fileno(self) -> int:
        return self.primary.fileno()

    def __getattr__(self, name):
        return getattr(self.primary, name)


_SESSION_FILE: TextIO | None = None
_SESSION_PATH: Path | None = None


def install_session_logging() -> Path:
    """Tee stdout/stderr to a durable timestamped log file."""
    global _SESSION_FILE, _SESSION_PATH

    if _SESSION_PATH is not None:
        return _SESSION_PATH

    logs_dir = PROJECT_ROOT / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = logs_dir / f"jarvis-{timestamp}.log"
    handle = path.open("a", encoding="utf-8", buffering=1)

    _SESSION_FILE = handle
    _SESSION_PATH = path
    sys.stdout = _TeeStream(sys.stdout, handle)
    sys.stderr = _TeeStream(sys.stderr, handle)
    print(f"[LOG] session_file={path}")
    return path
