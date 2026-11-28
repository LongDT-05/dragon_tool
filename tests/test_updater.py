import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

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
                self.assertIn(str(root / destination), contents)
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


if __name__ == "__main__":
    unittest.main()
