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


class LutrisMatchingTests(unittest.TestCase):
    def test_matches_only_exact_directory_wine_entries_with_numeric_ids(self):
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
            ]

            matches = octo_updater.matching_lutris_games(
                json.dumps(games), os.path.join(client_dir, "."))

            self.assertEqual(
                matches,
                [{
                    "id": "42",
                    "name": "OctoWoW",
                    "runner": "wine",
                    "directory": client_dir,
                }],
            )

    def test_rejects_non_list_json(self):
        with self.assertRaisesRegex(ValueError, "not a game list"):
            octo_updater.matching_lutris_games("{}", "/tmp/game")


class LutrisScriptTests(unittest.TestCase):
    def test_launch_command_is_fixed_argv_and_requires_numeric_id(self):
        self.assertEqual(
            octo_updater.lutris_launch_command("/usr/bin/lutris", "42"),
            ["/usr/bin/lutris", "lutris:rungameid/42"],
        )
        with self.assertRaisesRegex(ValueError, "numeric"):
            octo_updater.lutris_launch_command(
                "/usr/bin/lutris", "42;touch /tmp/no")

    def test_detects_vanilla_fixes_from_quoted_command(self):
        script = """#!/bin/bash
# Command
'/games/Octo WoW/VanillaFixes.exe' --arg
"""
        self.assertEqual(
            octo_updater.lutris_script_executable(script), "vanillafixes")

    def test_detects_wow_with_windows_separators(self):
        script = """#!/bin/bash
# Command
wine 'Z:\\\\games\\\\OctoWoW\\\\WoW.exe'
"""
        self.assertEqual(octo_updater.lutris_script_executable(script), "wow")

    def test_returns_unknown_for_ambiguous_or_malformed_script(self):
        ambiguous = "# Command\nwine WoW.exe VanillaFixes.exe\n"
        malformed = "# Command\n'unterminated\n"
        self.assertIsNone(octo_updater.lutris_script_executable(ambiguous))
        self.assertIsNone(octo_updater.lutris_script_executable(malformed))
        self.assertIsNone(octo_updater.lutris_script_executable("#!/bin/bash"))

    def test_warning_only_for_verified_wow_when_vanilla_fixes_expected(self):
        warning, is_warning = octo_updater.lutris_launcher_note(
            "VanillaFixes.exe", "wow")
        self.assertIn("Lutris launches WoW.exe", warning)
        self.assertTrue(is_warning)

        self.assertEqual(
            octo_updater.lutris_launcher_note(
                "VanillaFixes.exe", "vanillafixes"),
            ("", False),
        )
        neutral, is_warning = octo_updater.lutris_launcher_note(
            "VanillaFixes.exe", None)
        self.assertIn("not verified", neutral)
        self.assertFalse(is_warning)
        self.assertEqual(
            octo_updater.lutris_launcher_note("WoW.exe", "wow"),
            ("", False),
        )

    def test_inspection_reads_generated_script_without_executing_it(self):
        def write_script(args, **kwargs):
            Path(kwargs["cwd"], "octowow.sh").write_text(
                "#!/bin/bash\n# Command\nwine VanillaFixes.exe\n",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(args, 0, "", "")

        with mock.patch.object(
                octo_updater.subprocess, "run", side_effect=write_script) as run:
            result = octo_updater.inspect_lutris_executable(
                "/usr/bin/lutris", "42")

        self.assertEqual(result, "vanillafixes")
        run.assert_called_once()
        self.assertEqual(
            run.call_args.args[0],
            ["/usr/bin/lutris", "--output-script", "42"],
        )
        self.assertNotIn("shell", run.call_args.kwargs)

    def test_inspection_rejects_non_numeric_id_and_multiple_files(self):
        self.assertIsNone(
            octo_updater.inspect_lutris_executable("/usr/bin/lutris", "x"))

        def write_files(args, **kwargs):
            Path(kwargs["cwd"], "one.sh").touch()
            Path(kwargs["cwd"], "two.sh").touch()
            return subprocess.CompletedProcess(args, 0, "", "")

        with mock.patch.object(
                octo_updater.subprocess, "run", side_effect=write_files):
            self.assertIsNone(
                octo_updater.inspect_lutris_executable(
                    "/usr/bin/lutris", "42"))

    def test_inspection_rejects_oversized_script(self):
        def write_oversized(args, **kwargs):
            Path(kwargs["cwd"], "large.sh").write_bytes(
                b"x" * (octo_updater.LUTRIS_SCRIPT_MAX_BYTES + 1))
            return subprocess.CompletedProcess(args, 0, "", "")

        with mock.patch.object(
                octo_updater.subprocess, "run", side_effect=write_oversized):
            self.assertIsNone(
                octo_updater.inspect_lutris_executable(
                    "/usr/bin/lutris", "42"))

    def test_inspection_timeout_is_inconclusive_not_an_error(self):
        with mock.patch.object(
                octo_updater.subprocess, "run",
                side_effect=subprocess.TimeoutExpired(["lutris"], 10)):
            self.assertIsNone(
                octo_updater.inspect_lutris_executable(
                    "/usr/bin/lutris", "42"))


class LutrisDiscoveryTests(unittest.TestCase):
    def test_reports_missing_lutris(self):
        with mock.patch.object(octo_updater.shutil, "which", return_value=None):
            result = octo_updater.discover_lutris_games("/games/OctoWoW")

        self.assertEqual(result["status"], "missing")
        self.assertEqual(result["matches"], [])

    def test_discovers_and_inspects_exact_matches(self):
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
                    mock.patch.object(
                        octo_updater, "inspect_lutris_executable",
                        return_value="wow") as inspect:
                result = octo_updater.discover_lutris_games(client_dir)

        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["matches"][0]["executable"], "wow")
        inspect.assert_called_once_with("/usr/bin/lutris", "42")
        self.assertEqual(
            run.call_args.args[0],
            ["/usr/bin/lutris", "--list-games", "--installed", "--json"],
        )
        self.assertNotIn("shell", run.call_args.kwargs)

    def test_reports_timeout_and_malformed_json_as_probe_errors(self):
        with mock.patch.object(
                octo_updater.shutil, "which",
                return_value="/usr/bin/lutris"), \
                mock.patch.object(
                    octo_updater.subprocess, "run",
                    side_effect=subprocess.TimeoutExpired(["lutris"], 10)):
            timeout = octo_updater.discover_lutris_games("/games/OctoWoW")
        self.assertEqual(timeout["status"], "error")

        completed = subprocess.CompletedProcess(["lutris"], 0, "{", "")
        with mock.patch.object(
                octo_updater.shutil, "which",
                return_value="/usr/bin/lutris"), \
                mock.patch.object(
                    octo_updater.subprocess, "run", return_value=completed):
            malformed = octo_updater.discover_lutris_games("/games/OctoWoW")
        self.assertEqual(malformed["status"], "error")


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
