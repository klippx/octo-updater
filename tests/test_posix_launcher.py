import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import octo_updater


class SelectedExecutableTests(unittest.TestCase):
    def test_selects_vanilla_fixes_when_enabled_installed_and_present(self):
        with tempfile.TemporaryDirectory() as client_dir:
            Path(client_dir, "VanillaFixes.exe").touch()
            cfg = {
                "mods": {
                    "VanillaFixes": {
                        "enabled": True,
                        "installed_version": "1.0",
                    }
                }
            }

            path, label = octo_updater.selected_game_executable(client_dir, cfg)

            self.assertEqual(label, "VanillaFixes.exe")
            self.assertEqual(path, os.path.join(client_dir, label))

    def test_falls_back_to_wow_when_vanilla_fixes_is_not_enabled(self):
        with tempfile.TemporaryDirectory() as client_dir:
            Path(client_dir, "VanillaFixes.exe").touch()

            path, label = octo_updater.selected_game_executable(client_dir, {})

            self.assertEqual(label, "WoW.exe")
            self.assertEqual(path, os.path.join(client_dir, label))


class LutrisEligibilityTests(unittest.TestCase):
    def test_launch_command_is_fixed_argv_and_requires_numeric_id(self):
        self.assertEqual(
            octo_updater.lutris_launch_command("/usr/bin/lutris", "42"),
            ["/usr/bin/lutris", "lutris:rungameid/42"],
        )
        with self.assertRaisesRegex(ValueError, "numeric"):
            octo_updater.lutris_launch_command(
                "/usr/bin/lutris", "42;touch /tmp/no")

    def test_returns_all_numeric_wine_entries_ordered_by_directory_similarity(self):
        with tempfile.TemporaryDirectory() as client_dir:
            games = [
                {
                    "id": 42,
                    "name": "OctoWoW",
                    "runner": "wine",
                    "directory": client_dir,
                },
                {
                    "id": 43,
                    "name": "Native",
                    "runner": "linux",
                    "directory": client_dir,
                },
                {
                    "id": "not-numeric",
                    "name": "Bad ID",
                    "runner": "wine",
                    "directory": client_dir,
                },
                {
                    "id": 44,
                    "name": "Near match",
                    "runner": "wine",
                    "directory": client_dir + "-other",
                },
                {
                    "id": 45,
                    "name": "No directory",
                    "runner": "wine",
                    "directory": None,
                },
            ]

            matches = octo_updater.eligible_lutris_games(
                json.dumps(games), os.path.join(client_dir, "."))

            self.assertEqual(
                [game["id"] for game in matches],
                ["42", "44", "45"],
            )
            self.assertIsNone(matches[-1]["directory"])

    def test_rejects_non_list_json(self):
        with self.assertRaisesRegex(ValueError, "not a game list"):
            octo_updater.eligible_lutris_games("{}", "/tmp/game")

    def test_exact_match_takes_precedence_over_less_similar_path(self):
        games = [
            {
                "id": 4,
                "name": "Prefix",
                "runner": "wine",
                "directory": "/games/wrath",
            },
            {
                "id": 5,
                "name": "Exact",
                "runner": "wine",
                "directory": "/games/wrath/game-files",
            },
        ]

        matches = octo_updater.eligible_lutris_games(
            json.dumps(games),
            "/games/wrath/game-files",
        )

        self.assertEqual([game["id"] for game in matches], ["5", "4"])

    def test_uses_entry_with_deepest_shared_path(self):
        games = [
            {
                "id": 3,
                "name": "Shallow prefix",
                "runner": "wine",
                "directory": "/games",
            },
            {
                "id": 4,
                "name": "Wrath",
                "runner": "wine",
                "directory": "/games/wrath",
            },
        ]

        matches = octo_updater.eligible_lutris_games(
            json.dumps(games),
            "/games/wrath/arbitrary/deep/game-files",
        )

        self.assertEqual([game["id"] for game in matches], ["4", "3"])

    def test_uses_path_components_not_similar_string_prefixes(self):
        games = [
            {
                "id": 4,
                "name": "Similar name",
                "runner": "wine",
                "directory": "/games/wrath",
            },
            {
                "id": 5,
                "name": "Component match",
                "runner": "wine",
                "directory": "/games/wrathguild/runtime",
            },
        ]

        matches = octo_updater.eligible_lutris_games(
            json.dumps(games),
            "/games/wrathguild/arbitrary/game-files",
        )

        self.assertEqual([game["id"] for game in matches], ["5", "4"])

    def test_matches_arbitrarily_named_sibling_directories(self):
        with tempfile.TemporaryDirectory() as game_root:
            client_dir = os.path.join(game_root, "any-game-folder")
            wine_dir = os.path.join(game_root, "any-prefix-folder")
            os.makedirs(client_dir)
            os.makedirs(wine_dir)
            games = [{
                "id": 4,
                "name": "Wrath",
                "runner": "wine",
                "directory": wine_dir,
            }]

            matches = octo_updater.eligible_lutris_games(
                json.dumps(games), client_dir)

        self.assertEqual([game["id"] for game in matches], ["4"])

    def test_matches_arbitrarily_named_directory_ancestor(self):
        with tempfile.TemporaryDirectory() as game_root:
            client_dir = os.path.join(game_root, "files", "game")
            os.makedirs(client_dir)
            games = [{
                "id": 4,
                "name": "Wrath",
                "runner": "wine",
                "directory": game_root,
            }]

            matches = octo_updater.eligible_lutris_games(
                json.dumps(games), client_dir)

        self.assertEqual([game["id"] for game in matches], ["4"])

    def test_matches_arbitrary_cousin_layout_by_shared_path(self):
        with tempfile.TemporaryDirectory() as game_root:
            client_dir = os.path.join(game_root, "game", "files")
            os.makedirs(client_dir)
            other_dir = os.path.join(game_root, "runner", "prefix")
            os.makedirs(other_dir)
            games = [{
                "id": 4,
                "name": "Cousin directory",
                "runner": "wine",
                "directory": other_dir,
            }]

            matches = octo_updater.eligible_lutris_games(
                json.dumps(games), client_dir)

        self.assertEqual([game["id"] for game in matches], ["4"])

    def test_keeps_root_only_and_missing_directory_entries_as_fallbacks(self):
        games = [{
            "id": 4,
            "name": "Unrelated",
            "runner": "wine",
            "directory": "/opt/unrelated",
        }, {
            "id": 5,
            "name": "Desired without directory",
            "runner": "wine",
            "directory": None,
        }]

        matches = octo_updater.eligible_lutris_games(
            json.dumps(games), "/games/wow")

        self.assertEqual([game["id"] for game in matches], ["4", "5"])


class LutrisDiscoveryTests(unittest.TestCase):
    @staticmethod
    def _messages(log_mock):
        return "\n".join(call.args[0] for call in log_mock.call_args_list)

    def test_reports_missing_lutris(self):
        with mock.patch.object(
                octo_updater.shutil, "which", return_value=None), \
                mock.patch.object(octo_updater, "log") as logger:
            result = octo_updater.discover_lutris_games("/games/OctoWoW")

        self.assertEqual(result["status"], "missing")
        self.assertEqual(result["matches"], [])
        messages = self._messages(logger)
        self.assertEqual(messages, "[Lutris] Lutris was not found in PATH.")

    def test_discovers_exact_match_without_output_script_probe(self):
        with tempfile.TemporaryDirectory() as client_dir:
            payload = json.dumps([{
                "id": 42,
                "name": "OctoWoW",
                "runner": "wine",
                "directory": client_dir,
            }])
            completed = subprocess.CompletedProcess(
                ["lutris"], 0, payload, "")
            with mock.patch.object(
                    octo_updater.shutil, "which",
                    return_value="/usr/bin/lutris"), \
                    mock.patch.object(
                        octo_updater.subprocess, "run",
                        return_value=completed) as run, \
                    mock.patch.object(octo_updater, "log") as logger:
                result = octo_updater.discover_lutris_games(client_dir)

        self.assertEqual(result["status"], "ready")
        self.assertEqual(
            result["matches"],
            [{
                "id": "42",
                "name": "OctoWoW",
                "runner": "wine",
                "directory": client_dir,
            }],
        )
        run.assert_called_once()
        self.assertEqual(
            run.call_args.args[0],
            ["/usr/bin/lutris", "--list-games", "--installed", "--json"],
        )
        self.assertNotIn("shell", run.call_args.kwargs)
        self.assertNotIn("--output-script", run.call_args.args[0])
        self.assertEqual(
            self._messages(logger),
            '[Lutris] Found one Wine entry: "OctoWoW" (ID 42).',
        )

    def test_reports_timeout_and_malformed_json_as_probe_errors(self):
        with mock.patch.object(
                octo_updater.shutil, "which",
                return_value="/usr/bin/lutris"), \
                mock.patch.object(
                    octo_updater.subprocess, "run",
                    side_effect=subprocess.TimeoutExpired(["lutris"], 10)), \
                mock.patch.object(octo_updater, "log") as timeout_logger:
            timeout = octo_updater.discover_lutris_games("/games/OctoWoW")
        self.assertEqual(timeout["status"], "error")
        self.assertIn(
            "discovery timed out after 10s",
            self._messages(timeout_logger).lower(),
        )

        completed = subprocess.CompletedProcess(["lutris"], 0, "{", "")
        with mock.patch.object(
                octo_updater.shutil, "which",
                return_value="/usr/bin/lutris"), \
                mock.patch.object(
                    octo_updater.subprocess, "run", return_value=completed), \
                mock.patch.object(octo_updater, "log") as malformed_logger:
            malformed = octo_updater.discover_lutris_games("/games/OctoWoW")
        self.assertEqual(malformed["status"], "error")
        messages = self._messages(malformed_logger)
        self.assertIn("Malformed or unexpected JSON output", messages)
        self.assertEqual(malformed_logger.call_count, 1)

    def test_logs_one_concise_no_match_result(self):
        payload = json.dumps([
            {
                "id": 41,
                "name": "Wrong runner",
                "runner": "linux",
                "directory": "/games/OctoWoW",
            },
            {
                "id": 42,
                "name": "Wrong folder",
                "runner": "linux",
                "directory": "/opt/AnotherWoW",
            },
            {
                "id": "not-numeric",
                "name": "Bad ID",
                "runner": "wine",
                "directory": "/games/OctoWoW",
            },
        ])
        completed = subprocess.CompletedProcess(["lutris"], 0, payload, "")
        with mock.patch.object(
                octo_updater.shutil, "which",
                return_value="/usr/bin/lutris"), \
                mock.patch.object(
                    octo_updater.subprocess, "run", return_value=completed), \
                mock.patch.object(octo_updater, "log") as logger:
            result = octo_updater.discover_lutris_games("/games/OctoWoW")

        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["matches"], [])
        messages = self._messages(logger)
        self.assertIn(
            "[Lutris] No installed Wine entry for /games/OctoWoW.",
            messages,
        )
        self.assertIn("runner is not literal 'wine'", messages)
        self.assertIn("ID is not numeric", messages)
        self.assertEqual(logger.call_count, 4)

    def test_accepts_closest_shared_path_without_output_script_probe(self):
        client_dir = "/games/wrath/arbitrary/game-files"
        payload = json.dumps([{
            "id": 4,
            "name": "Wrath",
            "runner": "wine",
            "directory": "/games/wrath",
        }])
        completed = subprocess.CompletedProcess(["lutris"], 0, payload, "")
        with mock.patch.object(
                octo_updater.shutil, "which",
                return_value="/usr/bin/lutris"), \
                mock.patch.object(
                    octo_updater.subprocess, "run",
                    return_value=completed) as run, \
                mock.patch.object(octo_updater, "log") as logger:
            result = octo_updater.discover_lutris_games(client_dir)

        self.assertEqual(
            result["matches"],
            [{
                "id": "4",
                "name": "Wrath",
                "runner": "wine",
                "directory": "/games/wrath",
            }],
        )
        run.assert_called_once()
        self.assertNotIn("--output-script", run.call_args.args[0])
        messages = self._messages(logger)
        self.assertEqual(
            messages,
            '[Lutris] Found one Wine entry: "Wrath" (ID 4).',
        )

    def test_accepts_arbitrarily_named_sibling_directory(self):
        with tempfile.TemporaryDirectory() as game_root:
            client_dir = os.path.join(game_root, "game-files")
            wine_dir = os.path.join(game_root, "runtime-data")
            os.makedirs(client_dir)
            os.makedirs(wine_dir)
            payload = json.dumps([{
                "id": 4,
                "name": "Wrath",
                "runner": "wine",
                "directory": wine_dir,
            }])
            completed = subprocess.CompletedProcess(
                ["lutris"], 0, payload, "")
            with mock.patch.object(
                    octo_updater.shutil, "which",
                    return_value="/usr/bin/lutris"), \
                    mock.patch.object(
                        octo_updater.subprocess, "run",
                        return_value=completed) as run, \
                    mock.patch.object(octo_updater, "log") as logger:
                result = octo_updater.discover_lutris_games(client_dir)

        self.assertEqual([game["id"] for game in result["matches"]], ["4"])
        self.assertNotIn("--output-script", run.call_args.args[0])
        self.assertEqual(
            self._messages(logger),
            '[Lutris] Found one Wine entry: "Wrath" (ID 4).',
        )

    def test_includes_missing_directory_entry_in_chooser_candidates(self):
        payload = json.dumps([
            {
                "id": 3,
                "name": "Battle.net",
                "runner": "wine",
                "directory": "/games/battlenet",
            },
            {
                "id": 4,
                "name": "Old WoW",
                "runner": "wine",
                "directory": "/games/old-wow",
            },
            {
                "id": 5,
                "name": "New WoW",
                "runner": "wine",
                "directory": None,
            },
        ])
        completed = subprocess.CompletedProcess(["lutris"], 0, payload, "")
        with mock.patch.object(
                octo_updater.shutil, "which",
                return_value="/usr/bin/lutris"), \
                mock.patch.object(
                    octo_updater.subprocess, "run",
                    return_value=completed), \
                mock.patch.object(octo_updater, "log") as logger:
            result = octo_updater.discover_lutris_games(
                "/games/new-wow/client")

        self.assertEqual(
            {game["id"] for game in result["matches"]},
            {"3", "4", "5"},
        )
        messages = self._messages(logger)
        self.assertIn(
            "[Lutris] Found 3 installed Wine entries; "
            "choose which one PLAY should use.",
            messages,
        )
        self.assertIn("name='Battle.net', id=3", messages)
        self.assertIn("name='Old WoW', id=4", messages)
        self.assertIn("name='New WoW', id=5", messages)
        self.assertIn(
            "directory unavailable; included for manual choice",
            messages,
        )

    def test_logs_unexpected_json_shape(self):
        completed = subprocess.CompletedProcess(["lutris"], 0, "{}", "")
        with mock.patch.object(
                octo_updater.shutil, "which",
                return_value="/usr/bin/lutris"), \
                mock.patch.object(
                    octo_updater.subprocess, "run", return_value=completed), \
                mock.patch.object(octo_updater, "log") as logger:
            result = octo_updater.discover_lutris_games("/games/OctoWoW")

        self.assertEqual(result["status"], "error")
        self.assertIn(
            "JSON that is not a game list",
            self._messages(logger),
        )


class LutrisProbeLifecycleTests(unittest.TestCase):
    @staticmethod
    def _ready_app(matches):
        app = object.__new__(octo_updater.OctoUpdaterApp)
        app._game_path = mock.Mock()
        app._game_path.get.return_value = "/games/OctoWoW"
        app._lutris_state = {
            "status": "ready",
            "path": "/games/OctoWoW",
            "matches": matches,
            "selected": None,
        }
        app._set_btn_action = mock.Mock()
        app._status_var = mock.Mock()
        return app

    def test_no_exact_match_selects_actionable_how_to_play_state(self):
        app = self._ready_app([])

        app._refresh_lutris_ready_state()

        app._set_btn_action.assert_called_once_with(
            "launch_help", "HOW TO PLAY")
        app._status_var.set.assert_called_once_with(
            "Add a Wine game to Lutris")

    def test_multiple_exact_matches_select_chooser_state(self):
        app = self._ready_app([
            {"id": "4", "name": "Wrath"},
            {"id": "5", "name": "Wrath alternate"},
        ])

        app._refresh_lutris_ready_state()

        app._set_btn_action.assert_called_once_with(
            "choose_launcher", "CHOOSE LAUNCHER")
        app._status_var.set.assert_called_once_with(
            "Multiple Lutris entries")

    def test_retry_starts_fresh_probe_without_routine_log_noise(self):
        app = object.__new__(octo_updater.OctoUpdaterApp)
        app._game_path = mock.Mock()
        app._game_path.get.return_value = "/games/OctoWoW"
        app._lutris_state = {
            "status": "ready",
            "path": "/games/OctoWoW",
            "matches": [],
            "selected": None,
        }
        app._lutris_probe_token = 4
        app._log_line = mock.Mock()
        app._set_btn_busy = mock.Mock()
        app._status_var = mock.Mock()

        with mock.patch.object(octo_updater.threading, "Thread") as thread:
            app._start_lutris_probe(force=True)

        app._log_line.assert_not_called()
        self.assertEqual(app._lutris_state["status"], "probing")
        self.assertEqual(app._lutris_probe_token, 5)
        thread.assert_called_once()
        thread.return_value.start.assert_called_once_with()

    def test_play_state_executable_info_does_not_offer_retry(self):
        app = object.__new__(octo_updater.OctoUpdaterApp)
        app._game_path = mock.Mock()
        app._game_path.get.return_value = "/games/wrath/game-files"
        app._lutris_state = {
            "selected": {"id": "4", "name": "Wrath"},
        }

        with mock.patch.object(
                octo_updater, "load_config", return_value={}), \
                mock.patch("tkinter.messagebox.showinfo") as showinfo:
            app._show_lutris_executable_info()

        showinfo.assert_called_once()
        title, text = showinfo.call_args.args
        self.assertEqual(title, "Verify Lutris executable")
        self.assertIn("PLAY will launch Wrath through Lutris", text)
        self.assertIn("No retry is needed", text)
        self.assertNotIn("Retry Lutris detection", text)


class LaunchBehaviorTests(unittest.TestCase):
    @staticmethod
    def _app(client_dir):
        app = object.__new__(octo_updater.OctoUpdaterApp)
        app._game_path = mock.Mock()
        app._game_path.get.return_value = client_dir
        app._cfg = {}
        app._lutris_state = {
            "lutris": "/usr/bin/lutris",
            "selected": {"id": "42", "name": "OctoWoW"},
        }
        app._log_line = mock.Mock()
        app._set_btn_busy = mock.Mock()
        app._status_var = mock.Mock()
        app.after = mock.Mock()
        app.iconify = mock.Mock()
        app._refresh_ready_state = mock.Mock()
        return app

    def test_windows_launch_behavior_remains_direct(self):
        with tempfile.TemporaryDirectory() as client_dir:
            wow = os.path.join(client_dir, "WoW.exe")
            Path(wow).touch()
            app = self._app(client_dir)
            with mock.patch.object(
                    octo_updater, "load_config", return_value={}), \
                    mock.patch.object(octo_updater.sys, "platform", "win32"), \
                    mock.patch.object(
                        octo_updater.subprocess, "Popen") as popen:
                app._launch_game()

        popen.assert_called_once_with(
            [wow], cwd=client_dir, creationflags=0, close_fds=True)
        app._log_line.assert_any_call("Launched WoW.exe!\n", "ok")

    def test_linux_launch_uses_fixed_lutris_argv_without_shell(self):
        with tempfile.TemporaryDirectory() as client_dir:
            Path(client_dir, "WoW.exe").touch()
            app = self._app(client_dir)
            with mock.patch.object(
                    octo_updater, "load_config", return_value={}), \
                    mock.patch.object(octo_updater.sys, "platform", "linux"), \
                    mock.patch.object(
                        octo_updater.subprocess, "Popen") as popen:
                app._launch_game()

        popen.assert_called_once_with(
            ["/usr/bin/lutris", "lutris:rungameid/42"],
            cwd=client_dir, close_fds=True)
        self.assertNotIn("shell", popen.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
