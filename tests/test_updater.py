import io
from pathlib import Path
from queue import Queue
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import zipfile

from main import DaoRongStudioV10
import updater


class UpdateResponse(io.BytesIO):
    def __init__(self, archive):
        super().__init__(archive)
        self.headers = {"Content-Length": str(len(archive))}


def make_archive(exe):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("bundle/config.json", "{}")
        if exe is not None:
            archive.writestr("bundle/DaoRong_tool.exe", exe)
    return stream.getvalue()


class StageUpdateTest(unittest.TestCase):
    def test_two_updates_keep_their_own_payload_and_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            responses = [UpdateResponse(make_archive(exe)) for exe in (b"first", b"second")]
            with (
                patch("updater.tempfile.tempdir", directory),
                patch("updater.urllib.request.urlopen", side_effect=responses),
            ):
                first = updater.download_and_prepare_update("https://example.invalid/one", root / "app1")
                second = updater.download_and_prepare_update("https://example.invalid/two", root / "app2")
            self.assertNotEqual(first.parent, second.parent)
            for script, destination, exe in ((first, "app1", b"first"), (second, "app2", b"second")):
                contents = script.read_text(encoding="utf-8")
                resolved_app_dir = (root / destination).resolve()
                self.assertIn(f'"{resolved_app_dir}\\DaoRong_tool.exe"', contents)
                self.assertNotIn("config.json", contents)
                self.assertEqual((script.parent / "extracted/bundle/DaoRong_tool.exe").read_bytes(), exe)

    def test_invalid_update_is_discarded(self):
        with tempfile.TemporaryDirectory() as directory:
            response = UpdateResponse(make_archive(None))
            with (
                patch("updater.tempfile.tempdir", directory),
                patch("updater.urllib.request.urlopen", return_value=response),
            ):
                with self.assertRaises(RuntimeError):
                    updater.download_and_prepare_update("https://example.invalid/bad", Path(directory))
            self.assertEqual(list(Path(directory).iterdir()), [])


class UpdateUiTest(unittest.TestCase):
    def setUp(self):
        self.callbacks = Queue()
        self.app = Mock()
        self.app.after.side_effect = lambda delay, callback, *args: self.callbacks.put((callback, args))
        self.app._update_in_progress = False
        self.app._pending_update_info = updater.UpdateInfo(True, "14.10", "14.11", "https://example.invalid/update.zip")

    def run_pending_ui_callback(self):
        callback, args = self.callbacks.get(timeout=5)
        callback(*args)

    def test_manifest_request_runs_outside_ui_thread(self):
        self.app.config_data = {}
        reader_threads = Queue()
        info = self.app._pending_update_info

        def read_manifest(url):
            reader_threads.put(threading.get_ident())
            return info

        with patch("main.check_for_update", side_effect=read_manifest) as check_for_update:
            DaoRongStudioV10._check_update_async(self.app, silent=True)
            self.run_pending_ui_callback()
        self.assertNotEqual(reader_threads.get_nowait(), threading.get_ident())
        check_for_update.assert_called_once_with(updater.DEFAULT_MANIFEST_URL)
        self.app._handle_update_result.assert_called_once_with(info, True)

    def test_download_error_appears_and_duplicate_click_is_ignored(self):
        with (
            patch("main.ctk") as widgets,
            patch("main.messagebox") as dialog,
            patch("main.download_and_prepare_update", side_effect=OSError("Download failed")) as download,
        ):
            DaoRongStudioV10._apply_pending_update(self.app)
            DaoRongStudioV10._apply_pending_update(self.app)
            self.run_pending_ui_callback()
        dialog.askyesno.assert_called_once()
        download.assert_called_once()
        widgets.CTkToplevel.return_value.destroy.assert_called_once()
        dialog.showerror.assert_called_once_with("Lỗi cập nhật", "Download failed", parent=self.app)
        self.assertFalse(self.app._update_in_progress)

    def test_launch_error_appears_and_clears_update_in_progress(self):
        bat = Path("apply_update.bat")
        with (
            patch("main.ctk"),
            patch("main.messagebox") as dialog,
            patch("main.download_and_prepare_update", return_value=bat),
            patch("main.launch_updater_and_exit", side_effect=OSError("Launch failed")) as launch,
        ):
            DaoRongStudioV10._apply_pending_update(self.app)
            self.run_pending_ui_callback()
        launch.assert_called_once_with(bat)
        dialog.showerror.assert_called_once_with("Lỗi cập nhật", "Launch failed", parent=self.app)
        self.assertFalse(self.app._update_in_progress)


if __name__ == "__main__":
    unittest.main()
