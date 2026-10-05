"""Adapter for the existing SQLite schema with explicit connection lifetime."""
from __future__ import annotations

import sqlite3

from .memory import LocalMemory


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


class MemoryCoreStore(LocalMemory):
    def _connect(self):
        return sqlite3.connect(str(self.db_path), timeout=5, factory=_ClosingConnection)
