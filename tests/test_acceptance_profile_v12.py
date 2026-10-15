"""Launch gates only: temporary data, synthetic credentials, no desktop/network calls."""
from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from jarvis_agent import browser_bridge_doctor, operational_preferences as prefs, runtime_preflight
from scripts import p0_acceptance as launcher


class AcceptanceProfileTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="v12 acceptance spaces ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env_file = self.root / "existing config.env"
        self.env_file.write_text("# Existing configuration\n", encoding="utf-8")
        self.bridge = self.root / "bridge config.json"
        self.flags = {"skills_enabled": True, "learning_enabled": True}
        self.preferences = self.root / "operational_preferences.json"
        self.preferences.write_text(json.dumps({"schema": 1, **self.flags}), encoding="utf-8")
        for context in (
            patch.dict(os.environ, {"JARVIS_DATA_DIR": str(self.root),
                                    "GROQ_API_KEY": "synthetic-never-print-key",
                                    "JARVIS_AGENT_PROVIDER": "cerebras",
                                    "JARVIS_GROUNDING_MODEL": "old-explicit-model"}),
            patch.object(prefs, "_live_flags", None),
            patch.object(launcher.sys, "path", list(launcher.sys.path)),
            patch.object(launcher.sys, "argv", ["p0_acceptance", "--env-path", str(self.env_file),
                                               "--bridge-config", str(self.bridge), "--full"]),
        ):
            context.start()
            self.addCleanup(context.stop)
        previous_directory = Path.cwd()
        self.addCleanup(os.chdir, previous_directory)

    def run_launcher(self, preflight_result=0):
        output = StringIO()
        preflight = Mock(return_value=preflight_result)
        with patch.object(runtime_preflight, "main", preflight), \
                patch.object(browser_bridge_doctor, "main", return_value=0) as bridge, \
                patch.object(launcher.runpy, "run_path") as ui, \
                redirect_stdout(output):
            result = launcher.main()
        return result, output.getvalue(), preflight, bridge, ui

    def test_full_check_only_checks_automation_without_opening_ui_or_chrome(self):
        launcher.sys.argv.append("--check-only")
        args_seen = []
        with patch.object(runtime_preflight, "main", side_effect=lambda: args_seen.extend(launcher.sys.argv) or 0), \
                patch.object(browser_bridge_doctor, "main") as bridge, \
                patch.object(launcher.runpy, "run_path") as ui, redirect_stdout(StringIO()) as output:
            self.assertEqual(launcher.main(), 0)
        self.assertIn("--require-windows-automation", args_seen)
        self.assertIn("--require-grounding", args_seen)
        self.assertIn("commit=", output.getvalue())
        self.assertIn("CHECK ONLY", output.getvalue())
        self.assertNotIn("synthetic-never-print-key", output.getvalue())
        bridge.assert_not_called()
        ui.assert_not_called()
        self.assertEqual(os.environ["JARVIS_AGENT_PROVIDER"], "cerebras")
        self.assertEqual(os.environ["GROQ_API_KEY"], "synthetic-never-print-key")
        self.assertEqual(os.environ["JARVIS_DATA_DIR"], str(self.root))

    def test_full_profile_suppresses_old_opt_ins_without_rewriting_any_files(self):
        before = {path: path.read_bytes() for path in (self.env_file, self.preferences)}
        launcher.sys.argv.append("--check-only")
        self.run_launcher()
        self.assertEqual(prefs.effective_flags(SimpleNamespace(**self.flags)),
                         {"skills_enabled": False, "learning_enabled": False})
        self.assertEqual(prefs.load_preferences(self.preferences), self.flags)
        self.assertEqual(os.environ["JARVIS_GROUNDING_MODEL"], "")
        self.assertEqual(os.environ["JARVIS_COMPUTER_CORE_ENABLED"], "1")
        self.assertEqual(os.environ["JARVIS_MEMORY_DIRECT_AGENT_ENABLED"], "1")
        self.assertEqual(os.environ["JARVIS_BROWSER_ENABLED"], "0")
        self.assertEqual(before, {path: path.read_bytes() for path in before})

    def test_explicit_operator_change_can_replace_the_session_preferences(self):
        launcher.apply_profile(str(self.bridge), full=True)
        changed = {"skills_enabled": True, "learning_enabled": False}
        prefs.save_preferences(changed, path=self.preferences)
        self.assertEqual(prefs.effective_flags(SimpleNamespace()), changed)
        self.assertEqual(prefs.load_preferences(self.preferences), changed)

    def test_original_p0_profile_does_not_override_existing_computer_or_policy(self):
        with patch.dict(os.environ, {"JARVIS_COMPUTER_CORE_ENABLED": "0"}):
            launcher.apply_profile(str(self.bridge))
            self.assertEqual(os.environ["JARVIS_COMPUTER_CORE_ENABLED"], "0")
            self.assertEqual(os.environ["JARVIS_GROUNDING_MODEL"], "old-explicit-model")
            self.assertEqual(prefs.effective_flags(SimpleNamespace(skills_enabled=True,
                                          operational_learning_enabled=True)), self.flags)

    def test_failed_preflight_prevents_browser_and_ui_start(self):
        with patch.object(runtime_preflight, "main", return_value=2), \
                patch.object(browser_bridge_doctor, "main") as bridge, \
                patch.object(launcher.runpy, "run_path") as ui, redirect_stdout(StringIO()):
            with self.assertRaisesRegex(SystemExit, "dependencies need repair"):
                launcher.main()
        bridge.assert_not_called()
        ui.assert_not_called()

    def test_disconnected_bridge_prevents_ui_start(self):
        self.bridge.write_text("{}", encoding="utf-8")
        with patch.object(runtime_preflight, "main", return_value=0), \
                patch.object(browser_bridge_doctor, "main", return_value=2), \
                patch.object(launcher.runpy, "run_path") as ui, redirect_stdout(StringIO()):
            with self.assertRaisesRegex(SystemExit, "normal Chrome"):
                launcher.main()
        ui.assert_not_called()

    def test_validated_launch_runs_existing_entry_point_once(self):
        self.bridge.write_text("{}", encoding="utf-8")
        result, output, preflight, bridge, ui = self.run_launcher()
        self.assertEqual(result, 0)
        preflight.assert_called_once_with()
        bridge.assert_called_once_with()
        ui.assert_called_once_with(str(Path(launcher.__file__).resolve().parents[1] / "run_jarvis.py"),
                                   run_name="__main__")
        self.assertNotIn("synthetic-never-print-key", output)


class PreflightStatusTests(unittest.TestCase):
    def diagnose(self, *, system="Windows", version=(3, 12, 0), paths=(), missing="", flags=()):
        def check(name, purpose):
            return {"module": name, "purpose": purpose, "ok": name != missing,
                    "error": "synthetic missing dependency" if name == missing else ""}
        with patch.object(runtime_preflight.platform, "system", return_value=system), \
                patch.object(runtime_preflight.sys, "version_info", version), \
                patch.object(runtime_preflight.sys, "path", list(paths)), \
                patch.object(runtime_preflight.sys, "argv", ["runtime_preflight", *flags]), \
                patch.object(runtime_preflight, "_check", side_effect=check), \
                redirect_stdout(StringIO()) as output:
            code = runtime_preflight.main()
        packet = json.loads(output.getvalue())
        self.assertEqual(packet["ok"], code == 0)
        return code, packet

    def test_complete_windows_dependencies_pass_the_full_profile(self):
        code, packet = self.diagnose(flags=("--require-grounding",))
        self.assertEqual(code, 0)
        self.assertIn("winrt.windows.media.ocr", [row["module"] for row in packet["checks"]])

    def test_path_contamination_is_also_false_in_json(self):
        code, packet = self.diagnose(paths=(r"D:\jarvis\.cache\foundation-test-deps",))
        self.assertEqual(code, 2)
        self.assertTrue(packet["path_contamination"])

    def test_incompatible_python_is_also_false_in_json(self):
        code, packet = self.diagnose(version=(3, 9, 0), flags=("--require-windows-automation",))
        self.assertEqual(code, 2)
        self.assertFalse(packet["windows_automation_compatible"])

    def test_missing_ocr_returns_actionable_repair_command(self):
        code, packet = self.diagnose(missing="winrt.windows.media.ocr", flags=("--require-grounding",))
        self.assertEqual(code, 2)
        self.assertIn(" -Grounding", packet["repair"])

    def test_full_windows_profile_is_not_validated_on_linux(self):
        code, packet = self.diagnose(system="Linux", flags=("--require-grounding",))
        self.assertEqual(code, 2)
        self.assertIn("require Windows", packet["platform_reason"])

    def test_basic_preflight_remains_available_on_linux(self):
        code, _ = self.diagnose(system="Linux")
        self.assertEqual(code, 0)
