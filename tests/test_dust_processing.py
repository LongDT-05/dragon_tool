from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

sys.modules.setdefault("pyautogui", None)
if sys.platform != "win32":
    sys.modules.setdefault("pygetwindow", None)

from bot_engine import BotStats, DaoRongBot, PendingDust
from vision import MatchResult, VisionEngine, WindowRect


UI = Path(__file__).resolve().parents[1] / "templates" / "ui"


class DustProcessingTest(unittest.TestCase):
    def setUp(self):
        with patch("vision.mss", None):
            vision = VisionEngine(reference_size=(1330, 761), adb=Mock(), adb_serial="fixture")
        self.rect = WindowRect(0, 0, 1280, 720)
        self.frame = np.zeros((self.rect.height, self.rect.width, 3), np.uint8)
        self.bot = DaoRongBot.__new__(DaoRongBot)
        self.bot.vision = vision
        self.bot._ui = lambda name: UI / name
        self.bot.config = {"dust_rarities": ["Nguyên Liệu"]}
        self.bot.dust_targets_dir = UI.parent / "dust_targets"
        self.bot._pause_event = threading.Event()
        self.bot._stop_event = threading.Event()
        self.bot._need_rescan = self.bot._need_restart = False
        self.bot.stats = BotStats()
        self.bot.on_stats = Mock()
        self.bot.log = Mock()
        self.bot.input = Mock()
        self.bot.input.sleep_interruptible.return_value = True

    def test_scan_keeps_both_original_and_scaled_rarity_badges(self):
        badge = self.bot.vision._load_template(UI / "dragon_nguyen_lieu.png")
        small_badge = cv2.resize(badge, None, fx=0.8, fy=0.8, interpolation=cv2.INTER_AREA)
        cards = ((badge, 850, 230), (small_badge, 1040, 420))
        for image, x, y in cards:
            h, w = image.shape[:2]
            self.frame[y:y + h, x:x + w] = image
        found = self.bot._find_all_dust_candidates(self.frame, self.rect)
        self.assertEqual(len(found), 2)
        for card, (image, x, y) in zip(found, cards):
            self.assertLessEqual(abs(card.x - x - image.shape[1] // 2), 1)
            self.assertLessEqual(abs(card.y - y - image.shape[0] // 2), 1)
        self.bot.config["dust_rarities"] = []
        self.assertEqual(self.bot._find_all_dust_candidates(self.frame, self.rect), [])

    def test_sent_confirmation_preserves_ledger_until_card_is_gone(self):
        card = MatchResult(found=True, x=900, y=320, w=81, h=57, name="Nguyên Liệu")
        patch_image = self.bot._dust_card_image(self.frame, self.rect, card)
        patch_image[:, :patch_image.shape[1] // 2] = (30, 90, 230)
        patch_image[:, patch_image.shape[1] // 2:] = (90, 180, 30)
        key = ("Nguyên Liệu", 23)
        self.bot._pending_dust = PendingDust(card, self.rect, patch_image.copy(), 23, 0)
        self.bot._session_dust_ledger = {key: 1}
        self.bot._session_dust_sprites = {key: [self.bot._dragon_hsv_hist(patch_image)]}
        self.bot.vision.find_template = Mock(
            side_effect=lambda path, **_: MatchResult(found=path.name == "btn_close_dust_x.png")
        )
        self.bot.vision.capture_window = Mock(return_value=(self.frame, self.rect))
        self.assertFalse(self.bot._verify_dust_completion())
        self.assertEqual(self.bot.stats.dusted, 0)
        self.assertEqual(self.bot._session_dust_ledger[key], 1)

        self.bot.vision.capture_window.return_value = (np.zeros_like(self.frame), self.rect)
        self.assertTrue(self.bot._verify_dust_completion())
        self.assertTrue(self.bot._verify_dust_completion())
        self.assertEqual(self.bot.stats.dusted, 1)
        self.assertEqual(self.bot._session_dust_ledger[key], 0)
        self.assertIsNone(self.bot._pending_dust)
