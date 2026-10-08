import hashlib
import io
import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import octo_updater as updater


def pe_dll(version=None):
    data = bytearray(512)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3c, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into(
        "<HHIIIHH", data, 0x84,
        0x14c, 1, 0, 0, 0, 0, 0x2000,
    )
    if version:
        key = "ProductVersion\0".encode("utf-16le")
        start = 0x100
        data[start:start + len(key)] = key
        value_start = (start + len(key) + 3) & ~3
        value = (version + "\0").encode("utf-16le")
        data[value_start:value_start + len(value)] = value
    return bytes(data)


def release(data, version="v1.1.0", release_id=1, prerelease=False,
            digest=None):
    digest = digest or hashlib.sha256(data).hexdigest()
    return {
        "id": release_id,
        "tag_name": version,
        "draft": False,
        "prerelease": prerelease,
        "published_at": "2026-10-06T18:10:15Z",
        "assets": [{
            "id": release_id * 10,
            "name": "OctoLogin.dll",
            "size": len(data),
            "content_type": "application/x-msdownload",
            "digest": f"sha256:{digest}",
            "browser_download_url": (
                f"https://github.com/fmustafayaman/OctoLogin/"
                f"releases/download/{version}/OctoLogin.dll"
            ),
        }],
    }


class OctoLoginReleaseTests(unittest.TestCase):
    def test_registry_uses_existing_root_mod_conventions(self):
        mod = next(
            mod for mod in updater.MODS_REGISTRY
            if mod["id"] == updater.OCTOLOGIN_ID
        )

        self.assertFalse(mod["essential"])
        self.assertEqual(mod["source"]["kind"], "github_release")
        self.assertEqual(mod["source"]["asset_pattern"], "OctoLogin.dll")
        self.assertEqual(mod["register_dll"], "OctoLogin.dll")
        self.assertEqual(mod["installed_files"], ["OctoLogin.dll"])
        updater._torrent_excluded_files.cache_clear()
        self.assertIn(
            "octologin.dll", updater._torrent_excluded_files()
        )

    def test_validates_stable_release_asset_and_digest(self):
        data = pe_dll("1.1.0")
        version, asset, digest = updater._validated_octologin_release(
            release(data)
        )

        self.assertEqual(version, "1.1.0")
        self.assertEqual(asset["name"], "OctoLogin.dll")
        self.assertEqual(digest, hashlib.sha256(data).hexdigest())

    def test_rejects_prerelease_missing_digest_and_wrong_asset(self):
        data = pe_dll("1.1.0")
        with self.assertRaisesRegex(RuntimeError, "draft or prerelease"):
            updater._validated_octologin_release(
                release(data, prerelease=True)
            )

        no_digest = release(data)
        no_digest["assets"][0]["digest"] = None
        with self.assertRaisesRegex(RuntimeError, "SHA-256"):
            updater._validated_octologin_release(no_digest)

        wrong_asset = release(data)
        wrong_asset["assets"][0]["name"] = "OctoLogin-v1.1.0.zip"
        with self.assertRaisesRegex(RuntimeError, "exactly one"):
            updater._validated_octologin_release(wrong_asset)

    def test_slim_release_keeps_verification_metadata(self):
        data = pe_dll("1.1.0")
        slim = updater._slim_release(release(data))

        self.assertEqual(slim["id"], 1)
        self.assertFalse(slim["prerelease"])
        self.assertEqual(
            slim["assets"][0]["digest"],
            f"sha256:{hashlib.sha256(data).hexdigest()}",
        )
        self.assertEqual(
            slim["assets"][0]["content_type"],
            "application/x-msdownload",
        )

    def test_release_history_follows_github_pagination(self):
        data = pe_dll("1.1.0")

        class Response(io.BytesIO):
            def __init__(self, payload, link=""):
                super().__init__(json.dumps(payload).encode())
                self.headers = {"Link": link}

        first = Response(
            [release(data, release_id=1)],
            '<https://api.github.com/page/2>; rel="next"',
        )
        second = Response([release(data, version="v1.0.2", release_id=2)])

        with mock.patch.object(
                updater, "secure_urlopen",
                side_effect=[first, second]) as urlopen:
            releases = updater._github_releases(
                "fmustafayaman", "OctoLogin", raise_errors=True
            )

        self.assertEqual([item["id"] for item in releases], [1, 2])
        self.assertEqual(
            urlopen.call_args_list[1].args[0].full_url,
            "https://api.github.com/page/2",
        )

    def test_stale_release_history_is_used_when_offline(self):
        data = pe_dll("1.1.0")
        cached = updater._slim_release(release(data))
        config = {
            "mod_release_history_cache": {
                "octologin_release_history": {
                    "timestamp": 0,
                    "releases": [cached],
                }
            }
        }

        with mock.patch.object(
                updater, "load_config", return_value=config), \
                mock.patch.object(
                    updater, "_github_releases", return_value=[]):
            history = updater._octologin_release_history()

        self.assertEqual(history, [cached])


class OctoLoginDetectionTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.client = Path(self.tempdir.name)
        (self.client / "VanillaFixes.exe").write_bytes(b"loader")
        (self.client / "VfPatcher.dll").write_bytes(b"loader")

    def tearDown(self):
        self.tempdir.cleanup()

    def register(self, line="OctoLogin.dll"):
        (self.client / "dlls.txt").write_text(line + "\n", encoding="utf-8")

    def test_reads_embedded_pe_version_without_executing_dll(self):
        data = pe_dll("1.1.0")

        self.assertEqual(updater.octologin_pe_version(data), "1.1.0")

    def test_detects_embedded_version_and_canonical_registration(self):
        data = pe_dll("1.1.0")
        (self.client / "OctoLogin.dll").write_bytes(data)
        self.register()

        state = updater.detect_octologin_state(
            str(self.client), releases=[release(data)]
        )

        self.assertEqual(state["status"], "installed")
        self.assertEqual(state["installed_version"], "1.1.0")
        self.assertEqual(state["detection_source"], "pe_version")
        self.assertIsNone(state["error"])

    def test_detects_legacy_version_by_release_digest(self):
        data = pe_dll()
        (self.client / "OctoLogin.dll").write_bytes(data)
        self.register()

        state = updater.detect_octologin_state(
            str(self.client),
            releases=[release(data, version="v1.0.2")],
        )

        self.assertEqual(state["status"], "installed")
        self.assertEqual(state["installed_version"], "1.0.2")
        self.assertEqual(state["detection_source"], "release_digest")

    def test_classifies_unknown_manual_and_partial_installs(self):
        data = pe_dll()
        (self.client / "OctoLogin.dll").write_bytes(data)
        self.register()

        unknown = updater.detect_octologin_state(str(self.client))
        self.assertEqual(unknown["status"], "installed_unknown")
        self.assertIsNone(unknown["installed_version"])

        (self.client / "dlls.txt").unlink()
        broken = updater.detect_octologin_state(str(self.client))
        self.assertEqual(broken["status"], "broken")
        self.assertIn("canonical", broken["error"])

        (self.client / "OctoLogin.dll").unlink()
        self.register()
        missing = updater.detect_octologin_state(str(self.client))
        self.assertEqual(missing["status"], "broken")
        self.assertIn("missing", missing["error"])

        (self.client / "dlls.txt").unlink()
        (self.client / "OctoLogin.ini").write_text("enabled=1\n")
        config_only = updater.detect_octologin_state(str(self.client))
        self.assertEqual(config_only["status"], "broken")
        self.assertEqual(
            config_only["installed_files"], ["OctoLogin.ini"]
        )

    def test_detects_invalid_pe_and_missing_loader(self):
        (self.client / "OctoLogin.dll").write_bytes(b"not a PE")
        self.register()

        invalid = updater.detect_octologin_state(str(self.client))
        self.assertEqual(invalid["status"], "broken")
        self.assertIn("Invalid OctoLogin.dll", invalid["error"])

        (self.client / "OctoLogin.dll").write_bytes(pe_dll("1.1.0"))
        (self.client / "VanillaFixes.exe").unlink()
        no_loader = updater.detect_octologin_state(str(self.client))
        self.assertEqual(no_loader["status"], "broken")
        self.assertIn("VanillaFixes", no_loader["error"])

    def test_rejects_noncanonical_duplicate_and_utf16_dlls_txt(self):
        data = pe_dll("1.1.0")
        (self.client / "OctoLogin.dll").write_bytes(data)
        (self.client / "dlls.txt").write_text(
            "mods/OctoLogin.dll\nOctoLogin.dll\n", encoding="utf-8"
        )

        duplicate = updater.detect_octologin_state(str(self.client))
        self.assertEqual(duplicate["status"], "broken")
        self.assertIn("non-canonical or duplicate", duplicate["error"])

        (self.client / "dlls.txt").write_bytes(
            "OctoLogin.dll\n".encode("utf-16")
        )
        encoded = updater.detect_octologin_state(str(self.client))
        self.assertEqual(encoded["status"], "broken")
        self.assertIn("UTF-16", encoded["error"])


class OctoLoginTransactionTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.client = Path(self.tempdir.name)
        self.data = pe_dll("1.1.0")
        self.release = release(self.data)

    def tearDown(self):
        self.tempdir.cleanup()

    def response(self, data=None):
        return io.BytesIO(self.data if data is None else data)

    def test_atomic_install_updates_dll_and_registration_preserving_config(self):
        old = pe_dll("1.0.2")
        (self.client / "OctoLogin.dll").write_bytes(old)
        (self.client / "OctoLogin.ini").write_text("enabled=1\n")
        (self.client / "OctoLogin.log").write_text("old log\n")
        (self.client / "dlls.txt").write_text(
            "ClassicAPI.dll\nmods\\OctoLogin.dll\n"
        )

        with mock.patch.object(
                updater, "secure_urlopen",
                return_value=self.response()):
            files, version, digest = updater.install_octologin(
                str(self.client), self.release
            )

        self.assertEqual(files, ["OctoLogin.dll"])
        self.assertEqual(version, "1.1.0")
        self.assertEqual(digest, hashlib.sha256(self.data).hexdigest())
        self.assertEqual(
            (self.client / "OctoLogin.dll").read_bytes(), self.data
        )
        self.assertEqual(
            (self.client / "dlls.txt").read_text(),
            "ClassicAPI.dll\nOctoLogin.dll\n",
        )
        self.assertEqual(
            (self.client / "OctoLogin.ini").read_text(), "enabled=1\n"
        )
        self.assertEqual(
            (self.client / "OctoLogin.log").read_text(), "old log\n"
        )

    def test_checksum_failure_keeps_existing_install(self):
        old = pe_dll("1.0.2")
        (self.client / "OctoLogin.dll").write_bytes(old)
        (self.client / "dlls.txt").write_text("OctoLogin.dll\n")
        corrupted = self.data[:-1] + bytes([self.data[-1] ^ 1])

        with mock.patch.object(
                updater, "secure_urlopen",
                return_value=self.response(corrupted)):
            with self.assertRaisesRegex(RuntimeError, "checksum"):
                updater.install_octologin(str(self.client), self.release)

        self.assertEqual(
            (self.client / "OctoLogin.dll").read_bytes(), old
        )
        self.assertEqual(
            (self.client / "dlls.txt").read_text(), "OctoLogin.dll\n"
        )

    def test_invalid_pe_is_rejected_before_mutation(self):
        invalid = b"x" * len(self.data)
        bad_release = release(invalid)
        (self.client / "dlls.txt").write_text("ClassicAPI.dll\n")

        with mock.patch.object(
                updater, "secure_urlopen",
                return_value=self.response(invalid)):
            with self.assertRaisesRegex(RuntimeError, "valid PE"):
                updater.install_octologin(str(self.client), bad_release)

        self.assertFalse((self.client / "OctoLogin.dll").exists())
        self.assertEqual(
            (self.client / "dlls.txt").read_text(), "ClassicAPI.dll\n"
        )

    def test_rolls_back_when_dlls_replacement_fails(self):
        old = pe_dll("1.0.2")
        (self.client / "OctoLogin.dll").write_bytes(old)
        (self.client / "dlls.txt").write_text("ClassicAPI.dll\n")
        real_replace = updater.os.replace

        def fail_dlls(source, destination):
            if (destination.endswith("dlls.txt")
                    and ".dlls.txt.octologin-" in source):
                raise PermissionError("locked")
            return real_replace(source, destination)

        with mock.patch.object(
                updater, "secure_urlopen",
                return_value=self.response()), \
                mock.patch.object(updater.os, "replace",
                                  side_effect=fail_dlls):
            with self.assertRaises(PermissionError):
                updater.install_octologin(str(self.client), self.release)

        self.assertEqual(
            (self.client / "OctoLogin.dll").read_bytes(), old
        )
        self.assertEqual(
            (self.client / "dlls.txt").read_text(), "ClassicAPI.dll\n"
        )

    def test_remove_deletes_dll_config_log_and_registration(self):
        for name in ("OctoLogin.dll", "OctoLogin.ini", "OctoLogin.log"):
            (self.client / name).write_text(name)
        (self.client / "dlls.txt").write_text(
            "ClassicAPI.dll\nOctoLogin.dll\n"
        )

        updater.uninstall_octologin(str(self.client))

        for name in ("OctoLogin.dll", "OctoLogin.ini", "OctoLogin.log"):
            self.assertFalse((self.client / name).exists())
        self.assertEqual(
            (self.client / "dlls.txt").read_text(), "ClassicAPI.dll\n"
        )

    def test_remove_rolls_back_staged_files_on_failure(self):
        for name in ("OctoLogin.dll", "OctoLogin.ini", "OctoLogin.log"):
            (self.client / name).write_text(name)
        (self.client / "dlls.txt").write_text("OctoLogin.dll\n")

        with mock.patch.object(
                updater, "_replace_with_backup",
                side_effect=PermissionError("locked")):
            with self.assertRaises(PermissionError):
                updater.uninstall_octologin(str(self.client))

        for name in ("OctoLogin.dll", "OctoLogin.ini", "OctoLogin.log"):
            self.assertEqual((self.client / name).read_text(), name)
        self.assertEqual(
            (self.client / "dlls.txt").read_text(), "OctoLogin.dll\n"
        )


class OctoLoginUiTests(unittest.TestCase):
    def test_linux_recommendation_is_presentation_only(self):
        self.assertIn(
            "Recommended on Linux",
            updater.octologin_platform_note("linux"),
        )
        self.assertEqual(updater.octologin_platform_note("win32"), "")

    def test_unknown_install_is_present_but_not_update_comparable(self):
        state = {
            "enabled": True,
            "status": "installed_unknown",
            "installed_version": None,
        }
        mod = next(
            mod for mod in updater.MODS_REGISTRY
            if mod["id"] == updater.OCTOLOGIN_ID
        )

        self.assertTrue(updater.mod_state_installed(state))
        self.assertEqual(
            updater.mod_display_version(
                state, {"latest_version": "v1.1.0"}
            ),
            "unknown",
        )
        self.assertFalse(
            updater.mod_update_available(
                mod, state, {"latest_version": "v1.1.0"}
            )
        )

    def test_mutated_asset_digest_is_an_update_even_with_same_tag(self):
        state = {
            "enabled": True,
            "status": "installed",
            "installed_version": "1.1.0",
            "installed_sha256": "a" * 64,
        }
        mod = next(
            mod for mod in updater.MODS_REGISTRY
            if mod["id"] == updater.OCTOLOGIN_ID
        )

        self.assertTrue(
            updater.mod_update_available(
                mod,
                state,
                {
                    "latest_version": "v1.1.0",
                    "latest_sha256": "b" * 64,
                },
            )
        )

    def test_detected_octologin_does_not_suppress_essential_mod_setup(self):
        app = object.__new__(updater.OctoUpdaterApp)
        app._default_mods_install_started = False
        app._game_path = mock.Mock()
        app._game_path.get.return_value = "/game"
        app._mod_pending_state = {}
        app._log_line = mock.Mock()
        app._set_btn_busy = mock.Mock()
        app._status_var = mock.Mock()
        config = {
            "auto_install_mods": True,
            "mods": {
                "OctoLogin": {
                    "enabled": True,
                    "status": "installed",
                    "installed_version": "1.1.0",
                }
            },
        }

        with mock.patch.object(
                updater, "load_config", return_value=config), \
                mock.patch.object(
                    updater.os.path, "exists", return_value=True), \
                mock.patch.object(updater.threading, "Thread") as thread:
            app._maybe_install_essential_mods()

        self.assertTrue(app._default_mods_install_started)
        self.assertIn("VanillaFixes", app._mod_pending_state)
        thread.assert_called_once()


if __name__ == "__main__":
    unittest.main()
