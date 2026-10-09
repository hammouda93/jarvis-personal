"""SQLite transactions that release file handles at the end of a with block."""
import sqlite3


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()
