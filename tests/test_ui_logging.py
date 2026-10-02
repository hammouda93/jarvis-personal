import unittest
from unittest.mock import patch

from jarvis_agent.ui import _safe_console_log


class _CP1252Stream:
    encoding = "cp1252"

    def __init__(self):
        self.buffer = ""

    def write(self, value):
        value.encode(self.encoding, errors="strict")
        self.buffer += value
        return len(value)

    def flush(self):
        return None


class SafeConsoleLogTests(unittest.TestCase):
    def test_unicode_log_does_not_crash_cp1252_console(self):
        stream = _CP1252Stream()

        with patch("jarvis_agent.ui.sys.stdout", stream):
            _safe_console_log("Bonjour\u202fJarvis سلام")

        self.assertIn("Bonjour Jarvis", stream.buffer)
        self.assertIn("\\u", stream.buffer)


if __name__ == "__main__":
    unittest.main()
