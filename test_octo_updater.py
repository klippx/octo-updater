import json
import os
import tempfile
import unittest

import octo_updater


class ConfigMigrationTests(unittest.TestCase):
    def _write(self, path, value, mtime):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(value, f)
        os.utime(path, (mtime, mtime))

    def test_newer_legacy_snapshot_replaces_stale_persistent_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            legacy = os.path.join(tmp, "app-config.json")
            persistent = os.path.join(tmp, "xdg-config.json")
            self._write(persistent, {
                "out_dir": "/game",
                "addons": {"pfUI": {"sha": "old"}},
            }, 100)
            self._write(legacy, {
                "out_dir": "/game",
                "addons": {"pfUI": {"sha": "current"}},
            }, 200)

            result = octo_updater._reconcile_config_file(legacy, persistent)

            self.assertEqual(result, "legacy-newer")
            self.assertFalse(os.path.exists(legacy))
            self.assertEqual(
                octo_updater._read_config_file(persistent)["addons"]["pfUI"]["sha"],
                "current")
            status, reason = octo_updater.compare_addon_shas(
                octo_updater._read_config_file(
                    persistent)["addons"]["pfUI"]["sha"],
                "current")
            self.assertEqual((status, reason), ("upToDate", None))

    def test_newer_persistent_snapshot_wins_and_removes_stale_legacy_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            legacy = os.path.join(tmp, "app-config.json")
            persistent = os.path.join(tmp, "xdg-config.json")
            self._write(legacy, {"addons": {"pfUI": {"sha": "old"}}}, 100)
            self._write(
                persistent, {"addons": {"pfUI": {"sha": "current"}}}, 200)

            result = octo_updater._reconcile_config_file(legacy, persistent)

            self.assertEqual(result, "persistent-newer")
            self.assertFalse(os.path.exists(legacy))
            self.assertEqual(
                octo_updater._read_config_file(persistent)["addons"]["pfUI"]["sha"],
                "current")

    def test_invalid_legacy_snapshot_cannot_replace_persistent_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            legacy = os.path.join(tmp, "app-config.json")
            persistent = os.path.join(tmp, "xdg-config.json")
            with open(legacy, "w", encoding="utf-8") as f:
                f.write("{")
            self._write(persistent, {"addons": {"pfUI": {"sha": "current"}}}, 100)
            os.utime(legacy, (200, 200))

            result = octo_updater._reconcile_config_file(legacy, persistent)

            self.assertEqual(result, "legacy-invalid")
            self.assertTrue(os.path.exists(legacy))
            self.assertEqual(
                octo_updater._read_config_file(persistent)["addons"]["pfUI"]["sha"],
                "current")


class AddonStatusTests(unittest.TestCase):
    def test_stale_persisted_sha_is_reported_as_update_with_diagnostics(self):
        status, reason = octo_updater.compare_addon_shas(
            "1" * 40, "2" * 40)

        self.assertEqual(status, "outOfDate")
        self.assertEqual(
            reason,
            "installed commit 1111111111 != remote 2222222222")

    def test_missing_remote_sha_is_not_reported_as_an_update(self):
        status, reason = octo_updater.compare_addon_shas("1" * 40, None)

        self.assertEqual(status, "invalid")
        self.assertEqual(reason, "remote commit could not be resolved")


if __name__ == "__main__":
    unittest.main()
