import hashlib
import io
import json
import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import octo_updater as updater


def pe_dll(version="1.2.5"):
    data = bytearray(512)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3c, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", data, 0x84,
                     0x14c, 1, 0, 0, 0, 0, 0x2000)
    key = "ProductVersion\0".encode("utf-16le")
    data[0x100:0x100 + len(key)] = key
    value_start = (0x100 + len(key) + 3) & ~3
    value = (version + "\0").encode("utf-16le")
    data[value_start:value_start + len(value)] = value
    return bytes(data)


def file_bytes(relative, version="1.2.5"):
    if relative == updater.OCTOWOW_HD_DLL:
        return pe_dll(version)
    if relative.endswith("HDSwitch.toc"):
        return (
            "## Interface: 11200\n"
            f"## Version: {version}\n"
            "## SavedVariables: HDSwitchDB\n"
            "HDSwitch.lua\nBindings.xml\n"
        ).encode()
    if relative.lower().endswith(".mpq"):
        return b"MPQ\x1a" + relative.encode() + version.encode()
    return relative.encode()


def normalized_manifest(version="1.2.5", mutate=None):
    files = []
    payloads = {}
    for relative in updater.OCTOWOW_HD_REQUIRED_FILES:
        data = file_bytes(relative, version)
        payloads[relative] = data
        files.append({
            "component": "packs" if relative.lower().endswith(".mpq")
            else "hdswitch",
            "asset": Path(relative).name,
            "asset_id": len(files) + 1,
            "dest": relative,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "url": f"https://github.com/example/release/{Path(relative).name}",
        })
    if mutate:
        mutate(files, payloads)
    return {
        "release": version,
        "release_id": 1,
        "files": files,
        "total_size": sum(item["size"] for item in files),
    }, payloads


def api_release_and_manifest(version="1.2.5"):
    components = {
        "hdswitch": [],
        "packs": [],
        "female-full": [],
    }
    assets = []
    for relative in updater.OCTOWOW_HD_REQUIRED_FILES:
        data = file_bytes(relative, version)
        source_dest = ("mods/HDToggle.dll"
                       if relative == updater.OCTOWOW_HD_DLL else relative)
        component = ("female-full" if relative.endswith("Patch-X.mpq")
                     else "packs" if relative.lower().endswith(".mpq")
                     else "hdswitch")
        asset_name = Path(relative).name
        item = {
            "asset": asset_name,
            "dest": source_dest,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        if relative.startswith("Data/patch-") and Path(relative).name[6] in \
                "ABCDEGIMPST":
            item["url"] = (
                "https://"
                f"{updater.OCTOWOW_HD_THIRD_PARTY_HOST}/patches/{asset_name}"
            )
            item["source"] = "Project Reforged"
        else:
            assets.append({
                "id": len(assets) + 10,
                "name": asset_name,
                "size": len(data),
                "content_type": (
                    "application/x-msdownload"
                    if relative == updater.OCTOWOW_HD_DLL
                    else "application/xml"
                    if relative.endswith("Bindings.xml")
                    else "application/octet-stream"
                ),
                "digest": f"sha256:{hashlib.sha256(data).hexdigest()}",
                "browser_download_url":
                    f"https://github.com/example/release/{asset_name}",
            })
        components[component].append(item)
    manifest = {
        "release": f"v{version}",
        "notes": "fixture",
        "components": [
            {"id": key, "name": key, "version": version, "description": "",
             "files": value}
            for key, value in components.items()
        ],
    }
    manifest_data = json.dumps(manifest).encode()
    assets.append({
        "id": 999,
        "name": updater.OCTOWOW_HD_MANIFEST,
        "size": len(manifest_data),
        "content_type": "application/json",
        "digest": f"sha256:{hashlib.sha256(manifest_data).hexdigest()}",
        "browser_download_url":
            f"https://github.com/example/release/{updater.OCTOWOW_HD_MANIFEST}",
    })
    release = {
        "id": 1,
        "tag_name": f"v{version}",
        "draft": False,
        "prerelease": False,
        "assets": assets,
    }
    return release, manifest, manifest_data


class OctoWowReleaseTests(unittest.TestCase):
    def test_registry_and_ownership_are_composite(self):
        mod = next(mod for mod in updater.MODS_REGISTRY
                   if mod["id"] == updater.OCTOWOW_HD_ID)
        self.assertEqual(mod["transaction_hook"], "octowow_hd")
        self.assertIn("Data/Patch-X.mpq", mod["installed_files"])
        self.assertEqual(len(updater.OCTOWOW_HD_REQUIRED_MPQS), 16)
        updater._torrent_excluded_paths.cache_clear()
        self.assertTrue(updater._is_torrent_excluded(["Data", "Patch-X.mpq"]))
        self.assertTrue(updater.package_owned_addon("hdswitch"))
        self.assertTrue(updater.package_owned_mpq("PATCH-A.MPQ"))

    def test_validates_manifest_and_remaps_root_dll(self):
        release, manifest, _data = api_release_and_manifest()
        parsed = updater._validated_octowow_hd_manifest(release, manifest)
        destinations = {item["dest"] for item in parsed["files"]}
        self.assertIn("HDToggle.dll", destinations)
        self.assertNotIn("mods/HDToggle.dll", destinations)
        self.assertIn("Data/Patch-X.mpq", destinations)
        self.assertEqual(len(parsed["files"]), 20)

    def test_rejects_prerelease_and_missing_patch_x(self):
        release, manifest, _data = api_release_and_manifest()
        release["prerelease"] = True
        with self.assertRaisesRegex(RuntimeError, "draft or prerelease"):
            updater._validated_octowow_hd_release(release)

        release["prerelease"] = False
        female = next(c for c in manifest["components"]
                      if c["id"] == "female-full")
        female["files"] = []
        with self.assertRaisesRegex(RuntimeError, "missing required files"):
            updater._validated_octowow_hd_manifest(release, manifest)

    def test_fetches_and_verifies_manifest_asset(self):
        release, _manifest, data = api_release_and_manifest()
        with mock.patch.object(
                updater, "secure_urlopen", return_value=io.BytesIO(data)):
            parsed = updater._fetch_octowow_hd_manifest(release)
        self.assertEqual(parsed["release"], "1.2.5")

    def test_same_tag_fingerprint_drift_is_an_update(self):
        mod = next(mod for mod in updater.MODS_REGISTRY
                   if mod["id"] == updater.OCTOWOW_HD_ID)
        state = {
            "enabled": True,
            "health": "complete_current",
            "installed_version": "1.2.5",
            "installed_sha256": "a" * 64,
        }
        self.assertTrue(updater.mod_update_available(
            mod, state, {
                "latest_version": "1.2.5",
                "latest_sha256": "b" * 64,
            }))


class OctoWowDetectionTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.client = Path(self.tempdir.name)
        (self.client / "VanillaFixes.exe").write_bytes(b"loader")
        (self.client / "VfPatcher.dll").write_bytes(b"loader")

    def tearDown(self):
        self.tempdir.cleanup()

    def write_manifest(self, manifest, payloads):
        for item in manifest["files"]:
            path = self.client / item["dest"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payloads[item["dest"]])
        (self.client / "dlls.txt").write_text("HDToggle.dll\n")

    def test_detects_current_partial_and_disabled_registration(self):
        manifest, payloads = normalized_manifest()
        release = {"id": 1, "tag_name": "v1.2.5",
                   "hd_manifest": manifest}
        self.write_manifest(manifest, payloads)

        current = updater.detect_octowow_hd_state(
            str(self.client), releases=[release])
        self.assertEqual(current["health"], "complete_current")
        self.assertEqual(current["installed_version"], "1.2.5")

        (self.client / "Data" / "Patch-X.mpq").unlink()
        partial = updater.detect_octowow_hd_state(
            str(self.client), releases=[release])
        self.assertEqual(partial["health"], "partial")

        (self.client / "Data" / "Patch-X.mpq").write_bytes(
            payloads["Data/Patch-X.mpq"])
        (self.client / "dlls.txt").write_text("mods/HDToggle.dll\n")
        disabled = updater.detect_octowow_hd_state(
            str(self.client), releases=[release])
        self.assertEqual(disabled["health"], "registration_disabled")

    def test_pack_files_alone_are_not_an_installed_switch(self):
        path = self.client / "Data" / "Patch-X.mpq"
        path.parent.mkdir()
        path.write_bytes(file_bytes("Data/Patch-X.mpq"))
        state = updater.detect_octowow_hd_state(str(self.client))
        self.assertEqual(state["health"], "absent")
        self.assertFalse(state["enabled"])

    def test_detects_outdated_and_mixed_release_files(self):
        current, current_payloads = normalized_manifest("1.2.5")
        old, old_payloads = normalized_manifest("1.2.4")
        releases = [
            {"id": 2, "tag_name": "v1.2.5", "hd_manifest": current},
            {"id": 1, "tag_name": "v1.2.4", "hd_manifest": old},
        ]
        self.write_manifest(old, old_payloads)
        outdated = updater.detect_octowow_hd_state(
            str(self.client), releases=releases)
        self.assertEqual(outdated["health"], "complete_outdated")
        self.assertEqual(outdated["installed_version"], "1.2.4")

        mixed_path = self.client / "Data" / "Patch-X.mpq"
        mixed_path.write_bytes(current_payloads["Data/Patch-X.mpq"])
        mixed = updater.detect_octowow_hd_state(
            str(self.client), releases=releases)
        self.assertEqual(mixed["health"], "mixed_version")


class OctoWowTransactionTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.client = self.root / "client"
        self.client.mkdir()
        self.app_data = self.root / "app"
        self.app_data.mkdir()
        (self.client / "VanillaFixes.exe").write_bytes(b"loader")
        (self.client / "VfPatcher.dll").write_bytes(b"loader")

    def tearDown(self):
        self.tempdir.cleanup()

    def test_install_and_remove_preserve_packs_and_unrelated_registration(self):
        manifest, payloads = normalized_manifest()
        release = {"id": 1, "tag_name": "v1.2.5",
                   "assets": [{"id": 999,
                               "name": updater.OCTOWOW_HD_MANIFEST}],
                   "hd_manifest": manifest}
        (self.client / "dlls.txt").write_text("ClassicAPI.dll\n")

        def response(request, **_kwargs):
            url = request.full_url
            item = next(item for item in manifest["files"]
                        if item["url"] == url)
            return io.BytesIO(payloads[item["dest"]])

        usage = mock.Mock(free=1024 * 1024 * 1024)
        with mock.patch.object(updater, "APP_DATA_DIR", str(self.app_data)), \
                mock.patch.object(updater, "PACKAGE_TRANSACTION_POINTER",
                                  str(self.root / "active-package.json")), \
                mock.patch.object(updater.shutil, "disk_usage",
                                  return_value=usage), \
                mock.patch.object(updater, "get_client_version",
                                  return_value="1.12.1 (5875)"), \
                mock.patch.object(updater, "secure_urlopen",
                                  side_effect=response):
            files, version, _fingerprint = updater.install_octowow_hd(
                str(self.client), release)
            updater.uninstall_octowow_hd(str(self.client))

        self.assertEqual(version, "1.2.5")
        self.assertEqual(len(files), 20)
        self.assertFalse((self.client / "HDToggle.dll").exists())
        self.assertFalse(
            (self.client / "Interface" / "AddOns" / "HDSwitch").exists())
        self.assertTrue((self.client / "Data" / "Patch-X.mpq").exists())
        self.assertEqual(
            (self.client / "dlls.txt").read_text(), "ClassicAPI.dll\n")

    def test_file_set_transaction_rolls_back_all_files(self):
        first = self.client / "first.bin"
        second = self.client / "second.bin"
        first.write_bytes(b"old-one")
        second.write_bytes(b"old-two")
        new_one = self.root / "new-one"
        new_two = self.root / "new-two"
        new_one.write_bytes(b"new-one")
        new_two.write_bytes(b"new-two")
        pointer = self.root / "active-package.json"
        real_copy = updater.shutil.copyfile

        def fail_second(source, destination):
            if source == str(new_two):
                raise PermissionError("locked")
            return real_copy(source, destination)

        tx = updater.FileSetTransaction(
            str(self.client), pointer_path=str(pointer))
        with mock.patch.object(
                updater.shutil, "copyfile", side_effect=fail_second):
            with self.assertRaises(PermissionError):
                tx.apply({
                    "first.bin": str(new_one),
                    "second.bin": str(new_two),
                })

        self.assertEqual(first.read_bytes(), b"old-one")
        self.assertEqual(second.read_bytes(), b"old-two")
        self.assertFalse(pointer.exists())

    def test_checksum_failure_does_not_mutate_existing_install(self):
        manifest, payloads = normalized_manifest()
        release = {"id": 1, "tag_name": "v1.2.5",
                   "assets": [{"id": 999,
                               "name": updater.OCTOWOW_HD_MANIFEST}],
                   "hd_manifest": manifest}
        existing = self.client / "HDToggle.dll"
        existing.write_bytes(b"existing")
        (self.client / "dlls.txt").write_text("ClassicAPI.dll\n")

        def response(request, **_kwargs):
            item = next(item for item in manifest["files"]
                        if item["url"] == request.full_url)
            data = payloads[item["dest"]]
            if item["dest"] == "Data/Patch-X.mpq":
                data += b"corrupt"
            return io.BytesIO(data)

        usage = mock.Mock(free=1024 * 1024 * 1024)
        with mock.patch.object(updater, "APP_DATA_DIR", str(self.app_data)), \
                mock.patch.object(updater.shutil, "disk_usage",
                                  return_value=usage), \
                mock.patch.object(updater, "get_client_version",
                                  return_value="1.12.1 (5875)"), \
                mock.patch.object(updater, "secure_urlopen",
                                  side_effect=response):
            with self.assertRaisesRegex(RuntimeError, "declared size"):
                updater.install_octowow_hd(str(self.client), release)

        self.assertEqual(existing.read_bytes(), b"existing")
        self.assertEqual(
            (self.client / "dlls.txt").read_text(), "ClassicAPI.dll\n")

    def test_package_recovery_rejects_backup_path_traversal(self):
        target = self.client / "owned.bin"
        target.write_bytes(b"old")
        replacement = self.root / "replacement"
        replacement.write_bytes(b"new")
        pointer = self.root / "active-package.json"
        tx = updater.FileSetTransaction(
            str(self.client), pointer_path=str(pointer))
        tx.apply({"owned.bin": str(replacement)})
        journal_path = Path(tx.journal_path)
        journal = json.loads(journal_path.read_text())
        journal["entries"][0]["backup"] = os.path.join("..", "outside")
        journal_path.write_text(json.dumps(journal))

        with self.assertRaisesRegex(RuntimeError, "unsafe Verify transaction path"):
            updater.recover_interrupted_package_transaction(str(pointer))

        self.assertTrue(pointer.exists())
        self.assertEqual(target.read_bytes(), b"new")


if __name__ == "__main__":
    unittest.main()
