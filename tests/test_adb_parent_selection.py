import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np

missing = object()
gui_modules = {name: sys.modules.get(name, missing) for name in ("pyautogui", "pygetwindow")}
try:
    for name in gui_modules:
        sys.modules[name] = None
    import vision as vision_module
    from adb_controller import AdbController
    from bot_engine import DaoRongBot
    from vision import VisionEngine, WindowRect, imread_unicode
finally:
    for name, module in gui_modules.items():
        if module is missing:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


class AdbParentSelectionTest(unittest.TestCase):
    def _select_parents(self, *, delay=False, duplicate=False):
        rect = WindowRect(0, 0, 1280, 720)
        initial = np.zeros((rect.height, rect.width, 3), np.uint8)
        card_1 = np.s_[505:620, 350:450]
        card_2 = np.s_[505:620, 620:720]
        initial[card_1] = 255
        initial[card_2] = 255
        bot = DaoRongBot.__new__(DaoRongBot)
        bot.vision = SimpleNamespace(is_adb_mode=True)
        bot.input = Mock()
        bot.log = Mock()
        bot.dir_memory = Mock()
        bot._click_upper_slot = Mock()
        bot._parent_slot_hists = {}
        bot._spd = 1.0
        bot._drag_cfg = lambda _, default: default
        bot.should_stop = lambda: False
        bot.wait_if_paused = lambda: False

        pending = initial.copy()
        pending[card_1] = 0
        first = pending.copy()
        left = bot._slot_crop_upper(first, 1, True)
        left[:, :left.shape[1] // 2] = (0, 0, 60)
        left[:, left.shape[1] // 2:] = (60, 10, 0)
        complete = first.copy()
        complete[card_2] = 0
        right = bot._slot_crop_upper(complete, 2, True)
        # At 1280 px the two actual ROI widths differ by one pixel.
        right[:] = cv2.resize(left, (right.shape[1], right.shape[0])) if duplicate else (0, 0, 60)
        if not duplicate:
            right[right.shape[0] // 2:] = (60, 10, 0)
        states = {
            id(initial): (True, True), id(pending): (False, True),
            id(first): (False, True), id(complete): (False, False),
        }
        bot._count_empty_parent_slots = lambda frame, _: states[id(frame)]
        sequence = [initial, initial, pending, first, first, complete] if delay else [initial, first, complete]
        bot.vision.capture_window = Mock(side_effect=[(frame, rect) for frame in sequence])
        bot.vision.find_template = Mock(side_effect=lambda path, **kwargs: SimpleNamespace(
            found=True, x=400 if path.name == "p1.png" else 670, y=560,
            score=1.0 if path.name == "p1.png" else 0.99,
        ))

        result = bot._select_parents_dual_fast(Path("p1.png"), Path("p2.png"))
        self.assertFalse(bot._parent_slot_hists, "The deliberately dark fixture must not produce HSV evidence")
        self.assertEqual([call.args[:2] for call in bot.input.click.call_args_list], [(400, 560), (670, 560)])
        self.assertTrue(all(call.kwargs["compare_scales"] for call in bot.vision.find_template.call_args_list))
        self.assertEqual(bot.vision.capture_window.call_count, len(sequence))
        return result, bot._click_upper_slot

    def test_distinct_dark_parents_survive_delayed_adb_frames(self):
        accepted, release = self._select_parents(delay=True)
        self.assertTrue(accepted)
        release.assert_not_called()

    def test_duplicate_dark_parents_are_rejected_without_hsv(self):
        accepted, release = self._select_parents(duplicate=True)
        self.assertFalse(accepted)
        self.assertEqual(release.call_args.args[1], 2)

    def test_adb_scroll_checks_delayed_motion_before_another_swipe(self):
        rect = WindowRect(0, 0, 1280, 720)
        initial = np.zeros((rect.height, rect.width, 3), np.uint8)
        scrolled = initial.copy()
        scrolled[505:620, 350:450] = 255
        bot = DaoRongBot.__new__(DaoRongBot)
        bot._spd = 1.0
        bot._drag_cfg = lambda _, default: default
        bot.input = Mock()
        bot.log = Mock()
        bot.config = {}
        bot.dir_memory = Mock()
        bot.should_stop = lambda: False
        bot.wait_if_paused = Mock(side_effect=[True, False])
        bot._check_bag_full = lambda *args: False
        bot._count_empty_parent_slots = lambda *args: (True, True)
        bot._parent_slot_hists = {}
        bot.vision = SimpleNamespace(
            is_adb_mode=True,
            capture_window=Mock(side_effect=[(frame, rect) for frame in [initial, initial] + [scrolled] * 4]),
            crop_roi=lambda frame, *_: (frame, 0, 0),
            find_template=Mock(side_effect=lambda path, **kwargs: SimpleNamespace(
                found=kwargs["screen_bgr"] is scrolled and path.name == "p1.png",
                score=1.0, x=400, y=560,
            )),
        )

        self.assertFalse(bot._select_parents_dual_fast(Path("p1.png"), Path("p2.png")))
        bot.input.smooth_drag.assert_called_once()
        self.assertEqual(bot.input.click.call_args.args[:2], (400, 560))

    def test_adb_parent_search_compares_scale_after_primary_match(self):
        asset = Path(__file__).resolve().parents[1] / "templates" / "ui" / "dragon_nguyen_lieu.png"
        template = imread_unicode(asset)
        distractor = template.copy()
        h, w = template.shape[:2]
        cv2.circle(distractor, (int(w * 0.73), int(h * 0.72)), 4, (100, 50, 240), -1)
        exact = cv2.resize(template, None, fx=0.75, fy=0.75, interpolation=cv2.INTER_AREA)
        rect = WindowRect(0, 0, 1280, 720)
        frame = np.zeros((rect.height, rect.width, 3), np.uint8)
        frame[510:510 + h, 670:670 + w] = distractor
        frame[510:510 + exact.shape[0], 500:500 + exact.shape[1]] = exact
        roi = [0.10, 0.60, 0.65, 0.90]
        with tempfile.TemporaryDirectory() as tmp, patch.object(vision_module, "mss", None):
            path = Path(tmp) / "sample.png"
            cv2.imwrite(str(path), template)
            vision = VisionEngine(adb=object(), adb_serial="test")
            primary = vision.find_template(path, 0.79, roi, frame, rect)
            winner = vision.find_template(path, 0.79, roi, frame, rect, compare_scales=True)
        self.assertGreaterEqual(primary.score, 0.96)
        self.assertGreater(primary.x, 650)
        self.assertGreater(winner.score, primary.score)
        self.assertEqual((winner.x, winner.y), (530, 531))

    def test_clear_custom_adb_path_returns_to_automatic_discovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            packaged = folder / "adb.exe"
            custom = folder / "custom-adb.exe"
            packaged.touch()
            custom.touch()
            controller = AdbController(folder, str(custom))
            self.assertEqual(controller.find_adb_executable(), str(custom))
            controller.custom_adb_path = ""
            self.assertEqual(controller.find_adb_executable(), str(packaged))


if __name__ == "__main__":
    unittest.main()
