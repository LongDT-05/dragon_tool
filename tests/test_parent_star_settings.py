import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.modules.setdefault("pyautogui", None)
if sys.platform != "win32":
    sys.modules.setdefault("pygetwindow", None)

import main


class ParentStarSettingsTests(unittest.TestCase):
    def test_star_fields_accept_only_blank_or_1_to_99(self):
        for value, expected in ((None, None), ("  ", None), ("23", 23), (24, 24), (1, 1), (99, 99)):
            with self.subTest(value=value):
                self.assertEqual(main._optional_parent_stars(value), expected)
        for value in (False, 0, 100, -1, "24.0", "23★", []):
            with self.subTest(value=value), self.assertRaises(ValueError):
                main._optional_parent_stars(value)

    def test_legacy_profile_does_not_inherit_global_stars(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "legacy.json").write_text('{"selected_parent_1":"dragon.png"}', encoding="utf-8")
            (root / "configured.json").write_text(
                '{"selected_parent_1":"dragon.png","selected_parent_1_stars":"23","selected_parent_2_stars":24}',
                encoding="utf-8",
            )
            app = SimpleNamespace(
                config_data={"selected_parent_1_stars": 21, "selected_parent_2_stars": 22},
                _list_profiles=lambda: ["Mặc định", "legacy", "configured"],
            )
            with patch.object(main, "PROFILES_DIR", root):
                default = main.DaoRongStudioV10._load_profile_dict(app, "Mặc định")
                old = main.DaoRongStudioV10._load_profile_dict(app, "legacy")
                configured = main.DaoRongStudioV10._load_profile_dict(app, "configured")
        self.assertEqual(tuple(default[key] for key in main.PARENT_STAR_KEYS), (21, 22))
        self.assertEqual(tuple(old[key] for key in main.PARENT_STAR_KEYS), (None, None))
        self.assertEqual(tuple(configured[key] for key in main.PARENT_STAR_KEYS), (23, 24))
        self.assertEqual(configured["selected_parent_1"], "dragon.png")

    def test_invalid_stars_in_named_profile_are_reported_and_block_start(self):
        config = main._normalize_config({"selected_parent_1_stars": 100})
        self.assertEqual(config["selected_parent_1_stars"], 100)
        with patch.object(main.messagebox, "showwarning") as warning:
            self.assertFalse(main.DaoRongStudioV10._validate_run_config(object(), config, "ADB test"))
        self.assertEqual(warning.call_args.args[0], "Số sao bố mẹ")


if __name__ == "__main__":
    unittest.main()
