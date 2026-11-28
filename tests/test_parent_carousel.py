import unittest

import cv2
import numpy as np

from carousel import CarouselEdgeTracker, ParentCarousel


class ParentCarouselTest(unittest.TestCase):
    def setUp(self):
        self.row = ParentCarousel((253, 475, 699, 588), pitch=112)
        left, top, right, bottom = self.row.viewport
        self.height = bottom - top
        self.strip = np.full((self.height, 12 * self.row.pitch, 3), (26, 43, 30), np.uint8)
        rng = np.random.default_rng(5)
        for idx in range(10):
            x = idx * self.row.pitch
            # Card border and art change position together.
            card = self.strip[:, x:x + self.row.pitch]
            cv2.rectangle(card, (2, 2), (109, 110), (36, 185, 216), 3)
            cv2.ellipse(
                card, (int(rng.integers(34, 76)), 48), (24, 29), int(rng.integers(0, 160)),
                0, 360, tuple(map(int, rng.integers(50, 245, size=3))), -1,
            )
            for _ in range(9):
                cv2.circle(
                    card, tuple(map(int, rng.integers(16, 80, size=2))), int(rng.integers(3, 9)),
                    tuple(map(int, rng.integers(25, 245, size=3))), -1,
                )
            cv2.putText(card, str(16 + idx), (71, 18), cv2.FONT_HERSHEY_SIMPLEX, .45, (240, 220, 220), 1)
        # Tapping 24 stars leaves a vacancy between two occupied cells.
        self.strip[:, self.row.pitch:2 * self.row.pitch] = (26, 43, 30)

    def _frame(self, offset):
        left, top, right, bottom = self.row.viewport
        frame = np.zeros((720, 1280, 3), np.uint8)
        frame[top:bottom, left:right] = self.strip[:, offset:offset + self.row.width]
        return frame

    def test_page_keeps_overlap(self):
        for direction in (-1, 1):
            sx, sy, ex, ey = self.row.page_swipe(direction)
            self.assertEqual(ex - sx, 168 * direction)
            self.assertEqual(sy, ey)
            self.assertTrue(self.row.keeps_card_overlap(ex - sx))
            self.assertLessEqual(253, min(sx, ex))
            self.assertLess(max(sx, ex), 699)
        self.assertFalse(self.row.keeps_card_overlap(250))

    def test_nudge_moves_left_partial_inward(self):
        swipe = self.row.nudge_swipe(223, 335)
        sx, _, ex, _ = swipe
        shift = ex - sx
        self.assertGreaterEqual(223 + shift, 253)
        self.assertLessEqual(shift, 56)
        self.assertIsNone(self.row.nudge_swipe(335, 447))
        self.assertIsNone(self.row.nudge_swipe(120, 232))

    def test_nudge_moves_right_partial_inward(self):
        swipe = self.row.nudge_swipe(620, 732)
        sx, _, ex, _ = swipe
        shift = ex - sx
        self.assertLessEqual(732 + shift, 699)
        self.assertGreaterEqual(shift, -56)

    def test_internal_empty_cell_can_move(self):
        before = self._frame(30)
        leftward = self._frame(198)
        self.assertEqual(self.row.measure_shift(before, leftward), -168)
        self.assertEqual(self.row.measure_shift(leftward, before), 168)
        edge = CarouselEdgeTracker()
        self.assertFalse(edge.observe(-1, self.row.measure_shift(before, before)))
        self.assertFalse(edge.observe(-1, self.row.measure_shift(before, leftward)))

    def test_edge_requires_confirmed_still_frames(self):
        terminal = self._frame(820)
        no_change = self.row.measure_shift(terminal, terminal.copy())
        self.assertEqual(no_change, 0)
        edge = CarouselEdgeTracker()
        self.assertFalse(edge.observe(-1, no_change))
        self.assertTrue(edge.observe(-1, no_change))
        self.assertFalse(edge.observe(1, no_change))
        self.assertFalse(edge.observe(1, None))
        self.assertFalse(edge.observe(1, no_change))

    def test_blank_or_repeated_tiles_are_not_motion_evidence(self):
        blank = np.zeros((720, 1280, 3), np.uint8)
        self.assertIsNone(self.row.measure_shift(blank, blank))
        tile = self.strip[:, :self.row.pitch].copy()
        self.strip = np.tile(tile, (1, 12, 1))
        recurring = self._frame(30)
        self.assertIsNone(self.row.measure_shift(recurring, recurring))

    def test_motion_consensus_tolerates_single_changed_tile(self):
        before = self._frame(30)
        after = self._frame(150)
        after[488:575, 503:553] = (130, 150, 155)
        self.assertEqual(self.row.measure_shift(before, after), -120)


if __name__ == "__main__":
    unittest.main()
