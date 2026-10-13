"""Cross-capability regression tests, no real app, installer or API starts.

Each historical success has an observable offline contract; Windows LIVE
acceptance remains separate. Never execute the sample setup executable.
"""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from jarvis_agent import windows_perception as wp
from jarvis_agent.auto_mission import prepare
from jarvis_agent.mission_outcome_policy import failure_requires_review
from jarvis_agent.tools import _find_named_file, ToolIntent, execute


class InstallerDiscoveryCompatibilityTests(unittest.TestCase):
    def test_installer_found_by_natural_name_without_specific_app_rule(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            setup = folder / "CursorUserSetup-x64-3.22.12.exe"
            decoy = folder / "Cursor installation instructions.txt"
            setup.write_bytes(b"")
            decoy.write_text("never launch", encoding="utf-8")
            with patch("jarvis_agent.tools._file_search_roots", return_value=[folder]):
                for query in ("Cursor", "Cursor Setup", "installateur de Cursor",
                              "Cursor installer", "installation Cursor"):
                    with self.subTest(query=query):
                        selected, matches = _find_named_file(query, kind="installer")
                        self.assertEqual(selected, setup)
                        self.assertIn(setup, matches)
                selected, matches = _find_named_file("installateur de Cursor")
                self.assertNotIn(decoy, matches)
                # A generic filename request must not silently become
                # permission to execute an .exe when a document also matches.
                plain, _ = _find_named_file("Cursor")
                self.assertNotEqual(plain, setup)

    def test_open_file_dispatches_only_exact_discovered_artifact(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            setup = folder / "ExampleUserSetup-x64-3.22.12.exe"
            setup.write_bytes(b"")
            with patch("jarvis_agent.tools._file_search_roots", return_value=[folder]), patch(
                "jarvis_agent.tools.os.startfile", create=True
            ) as opener:
                outcome = execute(ToolIntent(
                    "file.open_named", {"query": "installateur Example"}
                ))
            self.assertTrue(outcome.success)
            opener.assert_called_once_with(str(setup))
            self.assertIn(str(setup), outcome.detail)

    def test_explicit_installer_intent_requires_file_open_permission(self):
        from jarvis_agent.instruction_clauses import (
            has_installer_file_target, authorized_installer_open,
        )
        self.assertTrue(has_installer_file_target(
            "Retrouve l'installateur Cursor dans Téléchargements."))
        self.assertFalse(authorized_installer_open(
            "Retrouve l'installateur Cursor dans Téléchargements, ne l'ouvre pas."))
        self.assertTrue(authorized_installer_open(
            "Retrouve l'installateur Cursor, ouvre ce fichier, inspecte ses options."
        ))
        self.assertFalse(has_installer_file_target(
            "Ouvre l'application Cursor."
        ))

    def test_search_does_not_launch_unrelated_executable(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            (folder / "UnrelatedSetup.exe").write_bytes(b"")
            with patch("jarvis_agent.tools._file_search_roots", return_value=[folder]), patch(
                "jarvis_agent.tools.os.startfile", create=True
            ) as opener:
                outcome = execute(ToolIntent(
                    "file.open_named", {"query": "installateur Example"}
                ))
            self.assertFalse(outcome.success)
            opener.assert_not_called()


class CloseWithoutSavingRegressionTests(unittest.TestCase):
    def _close_window(self, *, remaining=True, reliable=True):
        window = Mock()
        window.handle = 4343
        window.element_info.name = "Untitled - Editor"
        desktop = Mock()
        if reliable:
            desktop.windows.return_value = [SimpleNamespace(handle=4343)] if remaining else []
        else:
            desktop.windows.side_effect = RuntimeError("Win32 enumeration unavailable")
        fresh = SimpleNamespace(success=True, detail=json.dumps({
            "observation_id": "obs77",
            "window": {"title": "Editor", "type": "Window"},
            "controls": [
                {"ref": "obs77:e1", "type": "Button", "name": "Don't Save",
                 "enabled": True},
                {"ref": "obs77:e2", "type": "Document",
                 "name": "Untitled", "value": "PRIVATE CONTENT"},
            ],
        }))
        with (patch.object(wp, "_window_by_title", return_value=window),
              patch.object(wp, "_element_name", return_value="Untitled - Editor"),
              patch.object(wp, "_desktop", return_value=desktop),
              patch.object(wp, "invalidate_ui_snapshot"),
              patch.object(wp, "inspect_active_window", return_value=fresh),
              patch.object(wp.time, "sleep")):
            result = wp.close_window("Editor")
        self.assertEqual(window.close.call_count, 1)
        return result

    def test_blocked_close_returns_grounded_dialog_not_false_success(self):
        outcome = self._close_window(remaining=True)
        data = json.loads(outcome.detail)
        self.assertFalse(outcome.success)
        self.assertTrue(data["dispatched"])
        self.assertEqual(data["postcondition"], "window_present")
        self.assertFalse(data["outcome_unknown"])
        self.assertEqual(data["post_observation"]["observation_id"], "obs77")
        self.assertNotIn("PRIVATE CONTENT", outcome.detail)
        self.assertFalse(failure_requires_review([
            SimpleNamespace(name="close_window", success=False, detail=outcome.detail)
        ]))

    def test_closed_window_confirms_absence(self):
        outcome = self._close_window(remaining=False)
        self.assertTrue(outcome.success)
        data = json.loads(outcome.detail)
        self.assertTrue(data["verified"])
        self.assertEqual(data["postcondition"], "window_absent")

    def test_unreliable_close_readback_remains_blocked(self):
        outcome = self._close_window(remaining=True, reliable=False)
        self.assertFalse(outcome.success)
        data = json.loads(outcome.detail)
        self.assertTrue(data["outcome_unknown"])
        self.assertTrue(failure_requires_review([
            SimpleNamespace(name="close_window", success=False, detail=outcome.detail)
        ]))


class SafeMissionPivotTests(unittest.TestCase):
    def test_close_unsaved_new_goal_detaches_old_waiting_open_write_goal(self):
        agent = Mock()
        agent.active_mission_id = "old-mission"
        agent.mission_snapshot.return_value = {
            "status": "waiting_external",
            "user_goal": "Ouvre Bloc-notes, crée un nouveau document vide, puis écris Bonjour.",
            "active_supervisor": {},
        }
        agent.detach_mission.side_effect = lambda: setattr(agent, "active_mission_id", None)
        events = []
        result = prepare(agent, "ok fermes bloc note sans enregistrer", log=events.append)
        self.assertFalse(result.track)
        agent.detach_mission.assert_called_once_with()
        agent.begin_mission.assert_not_called()
        self.assertTrue(any("PREVIOUS_DETACHED" in x for x in events))

    def test_close_followup_of_original_close_goal_keeps_checkpoint(self):
        agent = Mock()
        agent.active_mission_id = "old-mission"
        agent.mission_snapshot.return_value = {
            "status": "waiting_external",
            "user_goal": "Ouvre un fichier, puis ferme la fenêtre sans enregistrer.",
            "active_supervisor": {},
        }
        prepare(agent, "ferme la fenêtre sans enregistrer")
        agent.detach_mission.assert_not_called()


if __name__ == "__main__":
    unittest.main()
