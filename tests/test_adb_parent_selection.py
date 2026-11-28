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
    from bot_engine import BotState, DaoRongBot
    from carousel import ParentCarousel
    from parent_cards import parent_star_digits
    from vision import VisionEngine, WindowRect, imread_unicode
finally:
    for name, module in gui_modules.items():
        if module is missing:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


class AdbParentSelectionTest(unittest.TestCase):
    def _exercise_selection(self, mode, *, wrong_tap=False, change_upper=False, stars=(23, 24)):
        rect = WindowRect(0 if mode == "adb" else 321, 0 if mode == "adb" else 197, 1280, 720)
        viewport = (253, 475, 700, 588)
        pitch = 112
        shift = 0
        selected = []
        stale = []
        taps = []
        gestures = []
        assets = {}
        numeric_images = {}
        rng = np.random.default_rng(41)
        empty_tile = np.full((113, 112, 3), (63, 74, 57), np.uint8)
        cv2.rectangle(empty_tile, (1, 2), (110, 111), (12, 19, 23), 4)
        cv2.line(empty_tile, (5, 104), (106, 104), (102, 103, 88), 3)

        for index, star_count in enumerate((23, 24, 16, 16, 8)):
            tile = empty_tile.copy()
            # Keep sprites different but similar, as in the reported pair.
            sprite = (rng.integers(80, 245, size=(45, 48, 3), dtype=np.uint8)
                      if index != 1 else assets[0][33:78, 29:77].copy())
            if index == 1:
                sprite[:19, :, 2] //= 2
            tile[33:78, 29:77] = sprite
            cv2.putText(tile, str(star_count), (55, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (246, 246, 246), 2)
            cv2.circle(tile, (92, 13), 8, (0, 216, 255), -1)
            assets[index] = tile
            numeric_images[index] = parent_star_digits(tile).copy()

        bot = DaoRongBot.__new__(DaoRongBot)
        bot._verified_parent_slots = {}
        bot._spd = 1.0
        bot._mode_key = mode
        bot._drag_cfg = lambda key, default: 2 if key == "max_parent_scrolls" else default
        bot.input = Mock()
        bot.input.sleep_interruptible.return_value = True
        bot.log = Mock()
        bot.log_tech = Mock()
        bot.dir_memory = Mock()
        bot.should_stop = lambda: False
        bot.wait_if_paused = lambda: True
        bot._check_bag_full = lambda *args: False
        bot._trigger_alert_and_pause = Mock()
        bot._set_state = Mock()
        bot.calibrator = Mock()
        bot.calibrator.get_roi.return_value = None
        bot._parent_carousel = lambda *_: ParentCarousel(viewport, pitch)
        bot._count_empty_parent_slots = lambda frame, _: (not frame[0, 0, 0], not frame[0, 0, 1])
        bot.text_matcher = Mock()

        def recognize(crop):
            resized = cv2.resize(crop, (numeric_images[0].shape[1], numeric_images[0].shape[0]))
            best = min(numeric_images, key=lambda key: np.abs(numeric_images[key].astype(float) - resized).mean())
            return (23, 24, 16, 16, 8)[best]

        bot.text_matcher.read_parent_stars.side_effect = recognize
        bot.config = {
            "selected_parent_1": "p1.png", "selected_parent_2": "p2.png",
            "selected_parent_1_stars": stars[0], "selected_parent_2_stars": stars[1],
        }

        def frame():
            screen = np.zeros((720, 1280, 3), np.uint8)
            screen[475:588, 253:700] = (55, 48, 42)
            for index in assets:
                left = 223 + index * pitch + shift
                x1, x2 = max(left, viewport[0]), min(left + pitch, viewport[2])
                if x2 > x1:
                    tile = empty_tile if index in selected else assets[index]
                    screen[475:588, x1:x2] = tile[:, x1 - left:x2 - left]
            for slot, index in enumerate(selected, 1):
                screen[0, 0, slot - 1] = 1
                crop = bot._slot_crop_upper(screen, slot, mode == "adb")
                crop[39:109, 36:123] = cv2.resize(assets[index][33:78, 29:77], (87, 70))
            return screen

        def capture(*args, **kwargs):
            return (stale.pop(0) if stale else frame()), rect

        def click(x, y, **kwargs):
            x, y = x - rect.left, y - rect.top
            taps.append((x, y))
            if y < 475:
                return
            stale.append(frame())
            index = (x - 223 - shift) // pitch
            if wrong_tap and len(selected) == 1:
                index = 2
            selected.append(index)

        def drag(x1, y1, x2, y2, **kwargs):
            nonlocal shift
            gestures.append(x2 - x1)
            stale.extend((frame(), frame()))
            shift = max(-160, min(50, shift + x2 - x1))

        bot.input.click.side_effect = click
        bot.input.smooth_drag.side_effect = drag
        with tempfile.TemporaryDirectory() as tmp, patch.object(vision_module, "mss", None):
            bot.parents_dir = Path(tmp)
            bot._ui = lambda name: Path(tmp) / name
            for index in (0, 1):
                cv2.imwrite(str(bot.parents_dir / f"p{index + 1}.png"), assets[index][32:80, 26:78])
            bot._ui("btn_start_breed.png").touch()
            bot.vision = VisionEngine(adb=object() if mode == "adb" else None, adb_serial="test")
            bot.vision.capture_window = capture
            real_match = bot.vision.find_template

            def match(path, **kwargs):
                if path.name == "btn_start_breed.png":
                    return SimpleNamespace(found=True, x=626 + rect.left, y=415 + rect.top)
                return real_match(path, **kwargs)

            def wait_for_button(*args, **kwargs):
                if change_upper:
                    selected[0] = 4
                return SimpleNamespace(found=True)

            bot.vision.find_template = match
            bot.vision.wait_for_template = wait_for_button
            state = bot._step_select_parents()
        return state, selected, taps, gestures, bot

    def test_clipped_23_star_card_is_aligned_and_selected_after_24_in_both_modes(self):
        for mode in ("adb", "windows"):
            with self.subTest(mode=mode):
                state, selected, taps, gestures, bot = self._exercise_selection(mode)
                self.assertEqual(selected, [1, 0], bot.log.call_args_list)
                self.assertEqual(state, BotState.WAIT_BREED_RESULT, bot.log.call_args_list)
                self.assertEqual(len(taps), 3)
                self.assertEqual(len(gestures), 1)
                self.assertIn(gestures[0], range(25, 56))

    def test_neighbor_tap_fills_a_slot_but_does_not_authorize_breeding(self):
        state, selected, taps, _, bot = self._exercise_selection("adb", wrong_tap=True)
        self.assertEqual(selected, [1, 2], bot.log.call_args_list)
        self.assertEqual(len(taps), 2)
        self.assertEqual(state, BotState.SELECT_PARENTS)
        bot._trigger_alert_and_pause.assert_called_once()

    def test_stars_are_checked_before_tap_even_for_a_matching_sprite(self):
        state, selected, taps, _, bot = self._exercise_selection("adb", stars=(22, 24))
        self.assertEqual(selected, [1], bot.log.call_args_list)
        self.assertEqual(len(taps), 1)
        self.assertEqual(state, BotState.SELECT_PARENTS)

    def test_fresh_frame_must_still_show_verified_pair_before_breed(self):
        state, selected, taps, _, bot = self._exercise_selection("adb", change_upper=True)
        self.assertEqual(selected, [4, 0], bot.log.call_args_list)
        self.assertEqual(state, BotState.SELECT_PARENTS)
        self.assertEqual(len(taps), 2)
        bot._trigger_alert_and_pause.assert_called_once()

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
