import unittest

import cv2
import numpy as np

from ocr_engine import DragonTextMatcher
from parent_cards import parent_star_digits, parent_tile_was_emptied, terminal_gap


class ParentCardEvidenceTest(unittest.TestCase):
    def test_ocr_reads_the_star_number_without_the_yellow_icon(self):
        matcher = DragonTextMatcher()
        self.assertTrue(matcher.is_available)
        card = np.full((112, 112, 3), (48, 65, 41), np.uint8)
        for stars in (23, 24):
            with self.subTest(stars=stars):
                sample = card.copy()
                cv2.circle(sample, (92, 14), 8, (5, 214, 248), -1)
                cv2.putText(sample, str(stars), (56, 22), cv2.FONT_HERSHEY_SIMPLEX,
                            0.56, (245, 245, 245), 2, cv2.LINE_AA)
                self.assertEqual(matcher.read_parent_stars(parent_star_digits(sample)), stars)
        self.assertIsNone(matcher.read_parent_stars(parent_star_digits(card)))

    def test_end_detection_ignores_a_selected_tile_with_a_frame(self):
        viewport = (28, 30, 476, 142)
        frame = np.full((160, 500, 3), (55, 48, 42), np.uint8)
        for left in range(28, 476, 112):
            frame[30:142, left:left + 112] = (63, 74, 57)
            cv2.rectangle(frame, (left + 2, 32), (left + 109, 137), (13, 18, 22), 4)
            cv2.line(frame, (left + 5, 133), (left + 106, 133), (132, 141, 124), 3)
        # All four spots retain their frame, even when one is the selected empty tile.
        self.assertFalse(terminal_gap(frame, viewport, 112, -1))
        self.assertFalse(terminal_gap(frame, viewport, 112, 1))
        with_right_gap = frame.copy()
        with_right_gap[30:142, 400:476] = (55, 48, 42)
        self.assertTrue(terminal_gap(with_right_gap, viewport, 112, -1))
        self.assertFalse(terminal_gap(with_right_gap, viewport, 112, 1))
        with_left_gap = frame.copy()
        with_left_gap[30:142, 28:105] = (55, 48, 42)
        self.assertTrue(terminal_gap(with_left_gap, viewport, 112, 1))
        self.assertFalse(terminal_gap(with_left_gap, viewport, 112, -1))

    def test_dark_overlay_is_not_an_empty_tile(self):
        empty = np.full((112, 112, 3), (48, 65, 41), np.uint8)
        cv2.rectangle(empty, (2, 2), (109, 109), (12, 19, 23), 4)
        filled = empty.copy()
        cv2.circle(filled, (92, 13), 8, (5, 214, 248), -1)
        cv2.ellipse(filled, (52, 57), (22, 21), 0, 0, 360, (20, 195, 220), -1)
        covered = filled.copy()
        covered[:92] = (12, 12, 12)
        self.assertTrue(parent_tile_was_emptied(filled, empty))
        self.assertFalse(parent_tile_was_emptied(filled, covered))


if __name__ == "__main__":
    unittest.main()
