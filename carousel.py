"""Geometry and motion checks for the horizontal parent-card list."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median

import cv2
import numpy as np


@dataclass(frozen=True)
class ParentCarousel:
    """Viewport and card pitch in capture-local pixels."""

    viewport: tuple[int, int, int, int]
    pitch: int

    def __post_init__(self) -> None:
        left, top, right, bottom = self.viewport
        if left < 0 or top < 0 or right <= left or bottom <= top or self.pitch <= 0:
            raise ValueError("Carousel viewport and pitch must be positive.")

    @property
    def width(self) -> int:
        return self.viewport[2] - self.viewport[0]

    def _swipe(self, dx: int) -> tuple[int, int, int, int] | None:
        left, top, right, bottom = self.viewport
        if abs(dx) < 12 or abs(dx) > self.width - 12:
            return None
        center_x = (left + right) // 2
        center_y = (top + bottom) // 2
        start = center_x - dx // 2
        return start, center_y, start + dx, center_y

    def page_swipe(self, direction: int) -> tuple[int, int, int, int] | None:
        """Plan an overlapping swipe. Direction +1 moves the finger to the right."""
        if direction not in (-1, 1):
            raise ValueError("Swipe direction must be -1 or +1.")
        # Leave two pitches of view shared even when the first card is clipped.
        distance = min(round(self.pitch * 1.5), self.width - 2 * self.pitch)
        return self._swipe(direction * distance) if distance > 0 else None

    def nudge_swipe(self, card_left: int, card_right: int) -> tuple[int, int, int, int] | None:
        """Move a clipped tile inward by at most half a pitch."""
        left, _, right, _ = self.viewport
        if card_right <= card_left or card_right <= left or card_left >= right:
            return None
        missing_left = left - card_left
        missing_right = card_right - right
        if max(missing_left, missing_right) <= 2 or min(missing_left, missing_right) > 2:
            return None
        direction = 1 if missing_left > missing_right else -1
        distance = min(self.pitch // 2, max(12, max(missing_left, missing_right) + round(self.pitch * 0.10)))
        return self._swipe(direction * distance)

    def keeps_card_overlap(self, shift: int) -> bool:
        """Check if an observed page still shares a full card at any lattice phase."""
        return self.width - abs(shift) >= 2 * self.pitch

    def measure_shift(self, before: np.ndarray, after: np.ndarray) -> int | None:
        """Estimate horizontal motion from distinctive, shared patches of the row."""
        left, top, right, bottom = self.viewport
        if before.shape != after.shape or before.shape[0] < bottom or before.shape[1] < right:
            return None

        # Ignore the fixed top and bottom frame of the viewport.
        margin = (bottom - top) // 6
        row_before = before[top + margin:bottom - margin, left:right]
        row_after = after[top + margin:bottom - margin, left:right]
        sampled_height = min(row_before.shape[0], 48)
        if sampled_height < 8:
            return None
        row_before = cv2.resize(row_before, (self.width, sampled_height), interpolation=cv2.INTER_AREA)
        row_after = cv2.resize(row_after, (self.width, sampled_height), interpolation=cv2.INTER_AREA)

        anchor_width = max(10, round(self.pitch * 0.32))
        if self.width < 3 * anchor_width:
            return None
        offsets = []
        stride = max(anchor_width, self.width // 12)
        peak_radius = max(5, round(self.pitch * 0.06))
        for anchor_x in range(0, self.width - anchor_width + 1, stride):
            anchor = row_before[:, anchor_x:anchor_x + anchor_width]
            if float(cv2.meanStdDev(anchor)[1].max()) < 6.0:
                continue
            scores = cv2.matchTemplate(row_after, anchor, cv2.TM_CCOEFF_NORMED).ravel()
            peak = int(np.argmax(scores))
            score = float(scores[peak])
            if score < 0.87:
                continue
            alternatives = np.concatenate((scores[:max(0, peak - peak_radius)], scores[peak + peak_radius + 1:]))
            if alternatives.size and score - float(alternatives.max()) < 0.025:
                continue
            offsets.append(peak - anchor_x)

        # An empty tile or a repeated card is weak evidence on its own.
        groups = [[other for other in offsets if abs(other - shift) <= 3] for shift in offsets]
        best = max(groups, key=len, default=[])
        if len(best) < 2 or len(best) * 2 <= len(offsets):
            return None
        return round(median(best))


@dataclass
class CarouselEdgeTracker:
    """Require repeated measured zero motion in one direction to infer an edge."""

    still_limit: int = 2
    direction: int = 0
    still_frames: int = 0

    def observe(self, direction: int, shift: int | None) -> bool:
        if direction != self.direction:
            self.direction = direction
            self.still_frames = 0
        if shift is None or abs(shift) > 3:
            self.still_frames = 0
            return False
        self.still_frames += 1
        return self.still_frames >= self.still_limit
