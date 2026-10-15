"""Exercise the P0 launcher through Windows PowerShell's real native argv boundary."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


@unittest.skipUnless(sys.platform == "win32", "Windows PowerShell native argument contract")
class P0LauncherTests(unittest.TestCase):
    def launch(self, *, check_only, full=False):
        shell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        if not shell.is_file():
            self.skipTest("Windows PowerShell 5.1 unavailable")
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="p0 launcher spaces ") as folder:
            env_path = Path(folder) / "empty config.env"
            env_path.write_text("# Isolated launcher test; no personal credentials.\n", encoding="utf-8")
            command = [str(shell), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                       str(root / "scripts/run_p0_acceptance.ps1"), "-PythonExe", sys.executable,
                       "-EnvPath", str(env_path), "-BrowserBridgeConfig",
                       str(Path(folder) / "missing bridge.json")]
            if check_only:
                command.append("-CheckOnly")
            if full:
                command.append("-Full")
            return subprocess.run(command, cwd=root, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=60)

    def test_check_only_with_spaces_through_windows_powershell(self):
        result = self.launch(check_only=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("CHECK ONLY", result.stdout)
        self.assertNotIn("SyntaxError", result.stdout + result.stderr)

    def test_logged_launch_reports_missing_bridge_without_starting_ui(self):
        result = self.launch(check_only=False)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, output)
        self.assertIn("P0 live log:", output)
        self.assertNotIn("NativeCommandError", output)
        self.assertIn("Browser bridge configuration missing", output)
        self.assertNotIn("NativeCommandError", output)
        self.assertNotIn("SyntaxError", output)

    def test_full_check_only_passes_flags_through_windows_powershell(self):
        result = self.launch(check_only=True, full=True)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertIn("V12 full profile | commit=", output)
        self.assertIn("winrt.windows.media.ocr", output)
        self.assertIn("Skills/Learning off for this session", output)
        self.assertIn("CHECK ONLY", output)

    def test_full_logged_launch_preserves_missing_bridge_gate(self):
        result = self.launch(check_only=False, full=True)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, output)
        self.assertIn("V12 full profile | commit=", output)
        self.assertIn("Browser bridge configuration missing", output)


if __name__ == "__main__":
    unittest.main()
