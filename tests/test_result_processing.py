import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np

sys.modules.setdefault("pyautogui", None)
if sys.platform != "win32":
    sys.modules.setdefault("pygetwindow", None)

from bot_engine import BotState, BotStats, DaoRongBot
from ocr_engine import DragonTextMatcher
from vision import MatchResult, WindowRect


ROOT = Path(__file__).resolve().parents[1]
DRAGON = "Rồng Lửa Đỏ"


class FixtureMatcher(DragonTextMatcher):
    @property
    def is_available(self):
        return self.available


class ResultProcessingTests(unittest.TestCase):
    def make_bot(self):
        bot = DaoRongBot.__new__(DaoRongBot)
        bot.config = {"dragon_text_rules": {}, "save_rare_snapshots": False}
        bot.stats = BotStats()
        bot.account_label = "test"
        bot._mode_key = "test"
        bot._spd = 1.0
        bot._cached_result_frame = (np.zeros((800, 1200, 3), np.uint8), WindowRect(0, 0, 1200, 800))
        bot._cached_result_btns = {}
        bot._pending_bless_keep = False
        bot._pause_event = Mock()
        bot._pause_event.is_set.return_value = False
        bot._keep_retry_count = 0
        bot._check_bag_full = Mock(return_value=False)
        bot._has_custom_result_templates = Mock(return_value=False)
        bot._check_auto_stop_conditions = Mock()
        bot._trigger_alert_and_pause = Mock()
        bot._click_ui = Mock(return_value=MatchResult(found=True))
        bot._ui = lambda name: ROOT / "templates" / "ui" / name
        bot.should_stop = Mock(return_value=False)
        bot._drag_cfg = bot._timing = bot._conf = lambda key, default: default
        bot.vision = Mock()
        bot.vision.capture_window.return_value = bot._cached_result_frame
        bot.vision.crop_roi.return_value = (np.ones((30, 150, 3), np.uint8), 0, 0)
        bot.vision.find_template.side_effect = lambda path, **opts: MatchResult(found=path.name == "btn_start_breed.png")
        bot.input = Mock()
        bot.input.sleep_interruptible.return_value = True
        bot.name_cache = SimpleNamespace(count=0, learn=Mock())
        bot.text_matcher = FixtureMatcher([{"name": DRAGON, "rarity": "Phổ biến"}])
        bot.text_matcher.available = True
        bot.text_matcher.read_name_crop = Mock(return_value="")
        bot.ocr_dict = Mock()
        bot.ocr_dict.lookup.return_value = None
        bot.pair_stats = Mock()
        bot.notifier = Mock()
        bot._read_resource_ratio = Mock(return_value=None)
        bot.log = bot.log_tech = bot.on_stats = Mock()
        return bot

    def test_unknown_sell_cannot_bypass_missing_stars(self):
        bot = self.make_bot()
        bot.config["unknown_dragon_action"] = "sell"
        bot.config["dragon_text_rules"] = {"sell": [f"{DRAGON} (>= 23 sao)"]}
        bot.text_matcher.read_name_crop.return_value = DRAGON

        self.assertEqual(bot._step_classify_and_act(), BotState.SELECT_PARENTS)
        self.assertEqual(bot.stats.sold, 0)
        self.assertEqual(bot.stats.kept, 1)
        self.assertEqual(bot._click_ui.call_args.args[0], "btn_keep.png")
        bot.text_matcher.read_name_crop.assert_called_once()

    def test_adapted_name_preserves_stars_for_matching(self):
        bot = self.make_bot()
        bot.config["dragon_text_rules"] = {"sell": [f"{DRAGON} (>= 23 sao)"]}
        bot.text_matcher.read_name_crop.return_value = f"{DRAGON} 24★"
        bot.ocr_dict.lookup.return_value = DRAGON

        self.assertEqual(bot._step_classify_and_act(), BotState.SELECT_PARENTS)
        self.assertEqual(bot.stats.sold, 1)
        self.assertEqual([c.args[0] for c in bot._click_ui.call_args_list], ["btn_sell.png", "btn_sell_confirm.png"])
        bot.text_matcher.read_name_crop.assert_called_once()

    def test_unknown_sell_does_not_override_a_possible_keep_rule(self):
        bot = self.make_bot()
        bot.config["unknown_dragon_action"] = "sell"
        bot.config["dragon_text_rules"] = {"keep": [f"{DRAGON} (>= 23 sao)"]}
        bot.text_matcher.read_name_crop.return_value = DRAGON

        self.assertEqual(bot._step_classify_and_act(), BotState.SELECT_PARENTS)
        self.assertEqual(bot.stats.sold, 0)
        self.assertEqual(bot.stats.kept, 1)
        self.assertEqual(bot._click_ui.call_args.args[0], "btn_keep.png")

    def test_confirmation_click_without_screen_change_is_not_success(self):
        bot = self.make_bot()
        bot.config["unknown_dragon_action"] = "sell"
        bot.vision.find_template.side_effect = lambda path, **opts: MatchResult(found=path.name == "btn_sell_confirm.png")

        self.assertEqual(bot._step_classify_and_act(), BotState.WAIT_BREED_RESULT)
        self.assertEqual(bot.stats.sold, 0)
        bot.pair_stats.record_breed.assert_not_called()
        bot._trigger_alert_and_pause.assert_called_once()

    def test_resume_after_bless_without_keep_does_not_bless_twice(self):
        bot = self.make_bot()
        bot.config["unknown_dragon_action"] = "bless"
        bot._click_ui.side_effect = lambda name, **opts: MatchResult(found=name == "btn_bless.png")

        self.assertEqual(bot._step_classify_and_act(), BotState.WAIT_BREED_RESULT)
        self.assertEqual(bot.stats.blessed, 0)
        bot.pair_stats.record_breed.assert_not_called()
        self.assertTrue(bot._pending_bless_keep)

        bot._click_ui.reset_mock()
        bot._click_ui.side_effect = lambda name, **opts: MatchResult(found=name == "btn_keep_after_bless.png")
        self.assertEqual(bot._step_classify_and_act(), BotState.SELECT_PARENTS)
        self.assertEqual(bot.stats.blessed, 1)
        self.assertFalse(bot._pending_bless_keep)
        self.assertEqual([c.args[0] for c in bot._click_ui.call_args_list], ["btn_keep_after_bless.png"])
        bot.pair_stats.record_breed.assert_called_once()


if __name__ == "__main__":
    unittest.main()
