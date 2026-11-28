import json
from pathlib import Path
import tempfile
import unittest

from prepare_share import clean_share_copy


class ShareCopyTest(unittest.TestCase):
    def test_removes_personal_data_but_keeps_ui_and_model(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.json"
            config.write_text(json.dumps({
                "telegram_token": "example", "discord_webhook": "example",
                "selected_parent_1": "private.png", "active_profile": "mine",
            }), encoding="utf-8")
            for name in (
                "profiles/mine.json", "templates/protect_dust/private.png",
                "templates/auto_learned/index.json", "templates/ui/btn_keep.png",
                "models/model.onnx",
            ):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            clean_share_copy(root)
            cleaned = json.loads(config.read_text(encoding="utf-8"))
            self.assertNotIn("telegram_token", cleaned)
            self.assertNotIn("discord_webhook", cleaned)
            self.assertEqual(cleaned["selected_parent_1"], "")
            self.assertFalse((root / "profiles").exists())
            self.assertFalse((root / "templates/protect_dust").exists())
            self.assertFalse((root / "templates/auto_learned").exists())
            self.assertTrue((root / "templates/ui/btn_keep.png").is_file())
            self.assertTrue((root / "models/model.onnx").is_file())
