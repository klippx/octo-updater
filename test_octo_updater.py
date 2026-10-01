import unittest

import octo_updater


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
