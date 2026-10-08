import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import octo_updater as updater


class VerifyShieldTransactionTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.client = self.root / "client"
        self.client.mkdir()
        self.pointer = self.root / "active-verify.json"
        self.logs = []
        self.files = [
            (["d3d9.dll"], 10),
            (["nampower.dll"], 10),
            (["WoW.exe"], 10),
        ]

    def tearDown(self):
        self.tempdir.cleanup()

    def transaction(self):
        return updater.VerifyShieldTransaction(
            str(self.client),
            log_fn=lambda message, tag="": self.logs.append((message, tag)),
            pointer_path=str(self.pointer),
        )

    def test_rollback_restores_protected_files_and_wow(self):
        (self.client / "d3d9.dll").write_bytes(b"installed dxvk")
        (self.client / "WoW.exe").write_bytes(b"patched wow")
        pristine = self.root / "base-WoW.exe"
        pristine.write_bytes(b"pristine wow")
        legacy = self.client / "d3d9.dll.octobak"
        legacy.write_bytes(b"user backup")

        tx = self.transaction()
        tx.begin(self.files, ignore_speech=False, pristine_wow=str(pristine))
        self.assertFalse((self.client / "d3d9.dll").exists())
        self.assertEqual((self.client / "WoW.exe").read_bytes(), b"pristine wow")

        (self.client / "d3d9.dll").write_bytes(b"")
        (self.client / "nampower.dll").write_bytes(b"")
        tx.rollback()

        self.assertEqual(
            (self.client / "d3d9.dll").read_bytes(), b"installed dxvk")
        self.assertFalse((self.client / "nampower.dll").exists())
        self.assertEqual((self.client / "WoW.exe").read_bytes(), b"patched wow")
        self.assertEqual(legacy.read_bytes(), b"user backup")
        self.assertFalse(self.pointer.exists())
        self.assertFalse(Path(tx.transaction_dir).exists())

    def test_commit_keeps_repaired_wow_and_restores_mod(self):
        (self.client / "d3d9.dll").write_bytes(b"installed dxvk")
        (self.client / "WoW.exe").write_bytes(b"patched wow")
        pristine = self.root / "base-WoW.exe"
        pristine.write_bytes(b"pristine wow")

        tx = self.transaction()
        tx.begin(self.files, ignore_speech=False, pristine_wow=str(pristine))
        (self.client / "d3d9.dll").write_bytes(b"torrent stub")
        (self.client / "WoW.exe").write_bytes(b"new patched wow")
        tx.commit()

        self.assertEqual(
            (self.client / "d3d9.dll").read_bytes(), b"installed dxvk")
        self.assertEqual(
            (self.client / "WoW.exe").read_bytes(), b"new patched wow")
        self.assertFalse(self.pointer.exists())

    def test_startup_recovery_is_scoped_and_idempotent(self):
        (self.client / "d3d9.dll").write_bytes(b"installed dxvk")
        (self.client / "WoW.exe").write_bytes(b"patched wow")
        unrelated = self.client / "VfPatcher.dll.octobak"
        unrelated.write_bytes(b"untouched")

        tx = self.transaction()
        tx.begin(self.files, ignore_speech=False)
        (self.client / "d3d9.dll").write_bytes(b"")
        (self.client / "nampower.dll").write_bytes(b"")

        recovered = updater.recover_interrupted_verify(
            str(self.pointer),
            log_fn=lambda message, tag="": self.logs.append((message, tag)),
        )

        self.assertTrue(recovered)
        self.assertEqual(
            (self.client / "d3d9.dll").read_bytes(), b"installed dxvk")
        self.assertFalse((self.client / "nampower.dll").exists())
        self.assertEqual(unrelated.read_bytes(), b"untouched")
        self.assertFalse(updater.recover_interrupted_verify(
            str(self.pointer), log_fn=lambda *_args: None))

    def test_recovery_rejects_path_traversal(self):
        (self.client / "d3d9.dll").write_bytes(b"installed dxvk")
        tx = self.transaction()
        tx.begin(self.files, ignore_speech=False)

        journal_path = Path(tx.journal_path)
        journal = json.loads(journal_path.read_text())
        journal["entries"][0]["path"] = os.path.join("..", "outside.dll")
        journal_path.write_text(json.dumps(journal))

        with self.assertRaisesRegex(RuntimeError, "unsafe Verify transaction path"):
            updater.recover_interrupted_verify(
                str(self.pointer), log_fn=lambda *_args: None)

        self.assertTrue(self.pointer.exists())
        self.assertTrue(Path(tx.transaction_dir).exists())

    def test_begin_rejects_symlinked_protected_file(self):
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks are not supported")
        target = self.root / "outside.dll"
        target.write_bytes(b"outside")
        try:
            os.symlink(target, self.client / "d3d9.dll")
        except OSError as error:
            self.skipTest(f"symlink creation unavailable: {error}")

        tx = self.transaction()
        with self.assertRaisesRegex(RuntimeError, "refusing to shield symlink"):
            tx.begin(self.files, ignore_speech=False)

        self.assertEqual(target.read_bytes(), b"outside")

    @unittest.skipIf(os.name == "nt", "POSIX process-group behavior")
    def test_quiet_aria_process_cancels_without_stdout(self):
        fake_aria = self.root / "fake-aria"
        fake_aria.write_text(
            "#!/usr/bin/env python3\n"
            "import time\n"
            "time.sleep(30)\n"
        )
        fake_aria.chmod(0o755)
        cancelled = threading.Event()
        timer = threading.Timer(0.2, cancelled.set)
        started = time.monotonic()
        timer.start()
        try:
            with mock.patch.object(updater, "ARIA2C_PATH", str(fake_aria)), \
                    mock.patch.object(
                        updater, "_ensure_torrent_junction",
                        return_value=str(self.root)):
                with self.assertRaisesRegex(RuntimeError, "Cancelled"):
                    updater.run_aria2c(
                        str(self.client),
                        select_files=[1],
                        should_cancel=cancelled.is_set,
                        log_fn=lambda *_args: None,
                    )
        finally:
            timer.cancel()
        self.assertLess(time.monotonic() - started, 5)
        self.assertIsNone(updater._active_aria2)

    def test_worker_rolls_back_when_post_download_validation_fails(self):
        (self.client / "d3d9.dll").write_bytes(b"installed dxvk")
        (self.client / "WoW.exe").write_bytes(b"patched wow")
        (self.client / "WTF").mkdir()
        (self.client / "WTF" / "Config.wtf").write_text("existing")
        pristine = self.root / "base-WoW.exe"
        pristine.write_bytes(b"pristine wow")
        files = [(["d3d9.dll"], 10), (["WoW.exe"], 10)]

        def fake_aria(*_args, **_kwargs):
            self.assertFalse((self.client / "d3d9.dll").exists())
            (self.client / "d3d9.dll").write_bytes(b"")
            (self.client / "WoW.exe").write_bytes(b"downloaded wow")

        config = {
            "active_torrent_hash": "torrent-version",
            "active_client_dir": str(self.client.resolve()),
            "ignore_speech": False,
        }
        log_q = updater.queue.Queue()
        worker = updater.UpdateWorker(
            str(self.client), log_q, updater.queue.Queue(),
            check_integrity=True)

        with mock.patch.object(updater, "VERIFY_TRANSACTION_POINTER",
                               str(self.pointer)), \
                mock.patch.object(updater, "PRISTINE_WOW_PATH", str(pristine)), \
                mock.patch.object(updater, "fetch_torrent",
                                  return_value=(b"torrent", files)), \
                mock.patch.object(updater, "torrent_version",
                                  return_value="torrent-version"), \
                mock.patch.object(updater, "load_config", return_value=config), \
                mock.patch.object(updater, "run_aria2c", side_effect=fake_aria), \
                mock.patch.object(updater, "torrent_tree_intact",
                                  return_value=False):
            worker.run()

        self.assertTrue(worker.completed.is_set())
        self.assertTrue(worker.safe_to_exit)
        self.assertEqual(
            (self.client / "d3d9.dll").read_bytes(), b"installed dxvk")
        self.assertEqual((self.client / "WoW.exe").read_bytes(), b"patched wow")
        self.assertFalse(self.pointer.exists())
        messages = [log_q.get_nowait()[0] for _ in range(log_q.qsize())]
        self.assertIn("__ERROR__", messages)

    def test_windows_process_timeout_escalates_to_kill(self):
        class FakeProcess:
            pid = 1234

            def __init__(self):
                self.wait_calls = 0
                self.killed = False

            def poll(self):
                return None

            def wait(self, timeout):
                self.wait_calls += 1
                if self.wait_calls == 1:
                    raise updater.subprocess.TimeoutExpired("aria2c", timeout)
                return -9

            def kill(self):
                self.killed = True

        process = FakeProcess()
        logs = []
        with mock.patch.object(updater.sys, "platform", "win32"), \
                mock.patch.object(updater, "stop_aria2c"):
            updater._stop_aria2c_and_wait(
                process,
                lambda message, tag="": logs.append((message, tag)),
                grace_seconds=0.01,
            )

        self.assertTrue(process.killed)
        self.assertEqual(process.wait_calls, 2)
        self.assertTrue(any("forcing termination" in message
                            for message, _tag in logs))

    def test_close_waits_for_worker_before_destroying_window(self):
        class Variable:
            def __init__(self):
                self.value = None

            def set(self, value):
                self.value = value

        class Worker:
            def __init__(self):
                self.completed = threading.Event()
                self.safe_to_exit = True
                self.cancelled = False

            def cancel(self):
                self.cancelled = True

        class App:
            _on_close = updater.OctoUpdaterApp._on_close
            _finish_close = updater.OctoUpdaterApp._finish_close

            def __init__(self):
                self._closing = False
                self._worker = Worker()
                self._status_var = Variable()
                self._prog_label_var = Variable()
                self.after_calls = []
                self.destroyed = False

            def _set_btn_busy(self, value):
                self.button = value

            def _log_line(self, *_args):
                pass

            def after(self, delay, callback):
                self.after_calls.append((delay, callback))

            def destroy(self):
                self.destroyed = True

        app = App()
        with mock.patch.object(updater, "stop_aria2c"):
            app._on_close()
            app._on_close()

        self.assertTrue(app._worker.cancelled)
        self.assertEqual(app._status_var.value, "Restoring game files…")
        self.assertEqual(len(app.after_calls), 1)
        self.assertFalse(app.destroyed)

        app._worker.completed.set()
        app._finish_close()
        self.assertTrue(app.destroyed)


if __name__ == "__main__":
    unittest.main()
