import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jarvis_agent.tools import (
    ToolIntent,
    _chrome_profile_directory,
    _contextual_site_search_url,
    _launch_chrome,
    _find_named_app,
    _find_named_file,
    _open_application,
    execute,
    route,
)
from jarvis_agent.windows_app_discovery import (
    WindowsAppCandidate,
    discover_start_apps,
)


class ToolRouterTests(unittest.TestCase):
    def test_open_youtube(self):
        intent = route("Jarvis, ouvre YouTube")
        self.assertEqual(intent.name, "browser.open_url")
        self.assertEqual(intent.args["url"], "https://www.youtube.com")

    def test_open_notepad_routes_to_known_app(self):
        intent = route("ouvre le Bloc-notes")
        self.assertEqual(intent.name, "app.open")
        self.assertEqual(intent.args["app"], "notepad")

    def test_open_vscode(self):
        intent = route("ouvre VS Code")
        self.assertEqual(intent.name, "app.open")
        self.assertEqual(intent.args["app"], "vscode")

    def test_time(self):
        intent = route("Quelle heure est-il ?")
        self.assertEqual(intent.name, "system.time")

    def test_time_variant(self):
        intent = route("Dis-moi quelle heure il est")
        self.assertEqual(intent.name, "system.time")

    def test_search_web_suffix(self):
        intent = route("recherche météo Tunis sur internet")
        self.assertEqual(intent.name, "browser.search")
        self.assertEqual(intent.args["query"], "meteo tunis")

    def test_search_web_prefix(self):
        intent = route("Recherche sur Internet à propos des agents IA")
        self.assertEqual(intent.name, "browser.search")
        self.assertEqual(intent.args["query"], "agents ia")

    def test_compound_youtube_search_routes_as_one_deterministic_site_search(self):
        intent = route("ouvre youtube et recherche messi")
        self.assertEqual(intent.name, "browser.search_site")
        self.assertEqual(intent.args["site"], "youtube")
        self.assertEqual(intent.args["query"], "messi")

    def test_close_named_tab_routes_as_browser_primitive(self):
        intent = route("fermes l'onglet youtube uniquement")
        self.assertEqual(intent.name, "browser.close_tab")
        self.assertEqual(intent.args["name"], "youtube")

    @patch("jarvis_agent.windows_perception.close_tab")
    def test_direct_close_tab_targets_last_browser_work_surface(
        self,
        close_tab_mock,
    ):
        close_tab_mock.return_value = type(
            "Result",
            (),
            {
                "success": True,
                "message": "closed",
                "detail": "{}",
            },
        )()
        with patch(
            "jarvis_agent.tools._LAST_BROWSER_URL",
            "https://www.youtube.com/results?search_query=messi",
        ):
            result = execute(
                ToolIntent(
                    "browser.close_tab",
                    {"name": "youtube"},
                )
            )

        self.assertTrue(result.success)
        close_tab_mock.assert_called_once_with(
            "youtube",
            window_title="YouTube - Google Chrome",
        )

    def test_browser_back_routes_as_browser_primitive(self):
        intent = route("retour en arrière")
        self.assertEqual(intent.name, "browser.back")

    def test_youtube_context_builds_youtube_search_url(self):
        url, site = _contextual_site_search_url(
            "messi",
            "https://www.youtube.com/",
        )
        self.assertEqual(site, "YouTube")
        self.assertIn("youtube.com/results?search_query=messi", url)

    @patch("jarvis_agent.tools._open_browser_url")
    def test_plain_search_reuses_last_youtube_context(self, open_mock):
        open_mock.return_value = True
        with patch("jarvis_agent.tools._LAST_BROWSER_URL", ""):
            opened = execute(
                ToolIntent(
                    "browser.open_url",
                    {"url": "https://www.youtube.com"},
                )
            )
            searched = execute(
                ToolIntent(
                    "browser.search",
                    {"query": "messi", "scope": "context"},
                )
            )

        self.assertTrue(opened.success)
        self.assertTrue(searched.success)
        self.assertEqual(open_mock.call_count, 2)
        self.assertIn(
            "youtube.com/results?search_query=messi",
            open_mock.call_args_list[-1].args[0],
        )
        self.assertIn("YouTube", searched.message)

    @patch("jarvis_agent.tools._open_browser_url")
    def test_explicit_internet_search_ignores_youtube_context(self, open_mock):
        open_mock.return_value = True
        with patch("jarvis_agent.tools._LAST_BROWSER_URL", "https://www.youtube.com"):
            result = execute(
                ToolIntent(
                    "browser.search",
                    {"query": "messi", "scope": "web"},
                )
            )

        self.assertTrue(result.success)
        self.assertIn(
            "google.com/search?q=messi",
            open_mock.call_args.args[0],
        )

    def test_search_without_subject_requests_followup(self):
        intent = route("Recherche sur Internet")
        self.assertEqual(intent.name, "browser.search_prompt")

    def test_open_snipping_tool(self):
        intent = route("ouvre l'outil capture écran")
        self.assertEqual(intent.name, "app.open")
        self.assertEqual(intent.args["app"], "snippingtool")

    def test_sleep_session(self):
        intent = route("c'est tout")
        self.assertEqual(intent.name, "assistant.sleep")


    def test_time_joined_stt_variant(self):
        intent = route("Quelleur est-il ?")
        self.assertEqual(intent.name, "system.time")

    def test_downloads_misheard_as_chargements(self):
        intent = route("Ouvre les chargements")
        self.assertEqual(intent.name, "folder.open")
        self.assertEqual(intent.args["folder"], "downloads")

    def test_downloads_misheard_oufre(self):
        intent = route("Oufre téléchargement")
        self.assertEqual(intent.name, "folder.open")
        self.assertEqual(intent.args["folder"], "downloads")

    def test_a_plus_sleeps_session(self):
        intent = route("Jarvis a plus")
        self.assertEqual(intent.name, "assistant.sleep")


    def test_close_jarvis(self):
        intent = route("Fermez Jarvis")
        self.assertEqual(intent.name, "assistant.stop")


    def test_misheard_auvre_youtube(self):
        intent = route("Auvre YouTube")
        self.assertEqual(intent.name, "browser.open_url")

    def test_named_folder_directly(self):
        intent = route("Ouvre le dossier baristas")
        self.assertEqual(intent.name, "folder.open_named")
        self.assertEqual(intent.args["query"], "baristas")

    def test_vague_folder_request_asks_followup(self):
        intent = route("Je veux ouvrir un dossier spécifique")
        self.assertEqual(intent.name, "folder.open_prompt")


    def test_nested_named_folder(self):
        intent = route("Ouvre le dossier media dans baristas")
        self.assertEqual(intent.name, "folder.open_named")
        self.assertEqual(intent.args["query"], "media")
        self.assertEqual(intent.args["within"], "baristas")

    @patch("jarvis_agent.tools._file_search_roots")
    def test_named_file_finds_recent_downloaded_installer(
        self,
        roots_mock,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            older = root / "OtherSetup.exe"
            newer = root / "CursorUserSetup-x64-3.22.12.exe"
            older.write_bytes(b"")
            newer.write_bytes(b"")
            os.utime(older, (1, 1))
            os.utime(newer, (2, 2))
            roots_mock.return_value = [root]

            path, matches = _find_named_file("Cursor Setup")

            self.assertEqual(path, newer)
            self.assertIn(newer, matches)

    def test_start_apps_discovers_packaged_app_by_display_name(self):
        payload = json.dumps(
            [
                {
                    "Name": "WhatsApp",
                    "AppID": "5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App",
                },
                {
                    "Name": "Calculator",
                    "AppID": "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App",
                },
            ]
        )

        def runner(_script, _timeout):
            return payload

        candidates = discover_start_apps("WhatsApp", runner=runner)

        self.assertTrue(candidates)
        self.assertEqual(candidates[0].name, "WhatsApp")
        self.assertEqual(candidates[0].source, "start_apps")
        self.assertEqual(candidates[0].score, 1.0)
        self.assertTrue(candidates[0].app_id.endswith("!App"))

    @patch("jarvis_agent.tools._find_named_app")
    @patch("jarvis_agent.tools.launch_registered_app")
    @patch("jarvis_agent.tools.resolve_registered_app")
    def test_named_app_launches_registered_windows_app(
        self,
        resolve_mock,
        launch_mock,
        find_mock,
    ):
        candidate = WindowsAppCandidate(
            name="WhatsApp",
            source="start_apps",
            score=1.0,
            app_id="5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App",
        )
        resolve_mock.return_value = (candidate, (candidate,))
        launch_mock.return_value = True

        result = execute(
            ToolIntent("app.open_named", {"query": "WhatsApp"})
        )

        self.assertTrue(result.success)
        self.assertIn("WhatsApp", result.message)
        launch_mock.assert_called_once_with(candidate)
        find_mock.assert_not_called()

    @patch("jarvis_agent.tools._app_binary_roots")
    @patch("jarvis_agent.tools._app_search_roots")
    def test_named_app_does_not_match_short_exe_substring(
        self,
        shortcut_roots_mock,
        binary_roots_mock,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrong = root / "ex.exe"
            wrong.write_bytes(b"")
            shortcut_roots_mock.return_value = []
            binary_roots_mock.return_value = [root]

            path, matches = _find_named_app("cursor-setup.exe")

            self.assertIsNone(path)
            self.assertNotIn(wrong, matches)

    @patch("jarvis_agent.tools._app_binary_roots")
    @patch("jarvis_agent.tools._app_search_roots")
    def test_named_app_discovers_matching_executable(
        self,
        shortcut_roots_mock,
        binary_roots_mock,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Programs"
            cursor_dir = root / "Cursor"
            cursor_dir.mkdir(parents=True)
            executable = cursor_dir / "Cursor.exe"
            executable.write_bytes(b"")

            shortcut_roots_mock.return_value = []
            binary_roots_mock.return_value = [root]

            path, matches = _find_named_app("Cursor")

            self.assertEqual(path, executable)
            self.assertIn(executable, matches)

    @patch("jarvis_agent.tools.os.startfile")
    @patch("jarvis_agent.tools._find_named_app")
    @patch("jarvis_agent.tools.launch_registered_app")
    @patch("jarvis_agent.tools.resolve_registered_app")
    @patch("jarvis_agent.tools._spawn")
    def test_cursor_falls_back_to_dynamic_windows_discovery(
        self,
        spawn_mock,
        resolve_mock,
        launch_mock,
        find_mock,
        startfile_mock,
    ):
        spawn_mock.return_value = False
        resolve_mock.return_value = (None, ())
        launch_mock.return_value = False
        shortcut = Path(r"C:\Users\test\Desktop\Cursor.lnk")
        find_mock.return_value = (shortcut, [shortcut])

        result = _open_application("cursor")

        self.assertTrue(result.success)
        find_mock.assert_called_once_with("Cursor")
        startfile_mock.assert_called_once_with(str(shortcut))

    @patch("jarvis_agent.tools._spawn")
    def test_notepad_uses_system_candidates(self, spawn_mock):
        spawn_mock.return_value = True

        result = _open_application("notepad")

        self.assertTrue(result.success)
        candidates = spawn_mock.call_args.args[0]
        self.assertTrue(
            any("notepad" in str(item).lower() for item in candidates)
        )

    def test_chrome_last_used_profile_is_discovered(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = (
                Path(tmp)
                / "Google"
                / "Chrome"
                / "User Data"
                / "Local State"
            )
            state.parent.mkdir(parents=True)
            state.write_text(
                json.dumps({"profile": {"last_used": "Profile 3"}}),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"LOCALAPPDATA": tmp}):
                self.assertEqual(
                    _chrome_profile_directory(),
                    "Profile 3",
                )


    @patch("jarvis_agent.tools.subprocess.Popen")
    @patch("jarvis_agent.tools._chrome_profile_directory")
    @patch("jarvis_agent.tools._chrome_executable")
    def test_jarvis_chrome_launch_enables_renderer_accessibility(
        self,
        executable_mock,
        profile_mock,
        popen_mock,
    ):
        executable_mock.return_value = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
        profile_mock.return_value = "Profile 3"

        ok = _launch_chrome(url="https://example.com")

        self.assertTrue(ok)
        args = popen_mock.call_args.args[0]
        self.assertIn("--force-renderer-accessibility=complete", args)
        self.assertIn("--profile-directory=Profile 3", args)
        self.assertEqual(args[-1], "https://example.com")

    def test_unknown_is_safe(self):
        intent = route("supprime tous mes fichiers")
        self.assertEqual(intent.name, "unknown")


if __name__ == "__main__":
    unittest.main()
