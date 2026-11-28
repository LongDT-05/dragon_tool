"""Geometry and visual evidence for the cards below the breeding dialog."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class ParentCard:
    left: int
    right: int
    top: int
    bottom: int
    viewport_left: int
    viewport_right: int

    @property
    def complete(self) -> bool:
        tolerance = max(2, round((self.right - self.left) * 0.035))
        return self.left >= self.viewport_left - tolerance and self.right <= self.viewport_right + tolerance

    @property
    def visible_width(self) -> int:
        return min(self.right, self.viewport_right) - max(self.left, self.viewport_left)

    def bounds(self) -> tuple[int, int, int, int]:
        return max(self.left, self.viewport_left), self.top, min(self.right, self.viewport_right), self.bottom

    def crop(self, frame: np.ndarray) -> np.ndarray:
        left, top, right, bottom = self.bounds()
        return frame[top:bottom, left:right]


def find_parent_cards(
    frame: np.ndarray, viewport: tuple[int, int, int, int], pitch: float
) -> list[ParentCard]:
    """Fit the repeated vertical card borders. Art and stars are above the border band."""
    left, top, right, bottom = viewport
    row = frame[top:bottom, left:right]
    if row.size == 0 or pitch < 24 or row.shape[0] < pitch * 0.55:
        return []

    # The lower corners remain in place when a dragon leaves an empty tile.
    band_top, band_bottom = round(row.shape[0] * 0.76), round(row.shape[0] * 0.96)
    border_band = row[band_top:band_bottom].astype(np.float32)
    edges = np.abs(np.diff(border_band, axis=1)).mean(axis=(0, 2))
    # A seam can have two nearby edges, one on either side of the dark divider.
    sigma = max(1.4, pitch * 0.048)
    edges = cv2.GaussianBlur(np.pad(edges, (1, 0))[None, :], (0, 0), sigmaX=sigma)[0]
    width = row.shape[1]
    min_edge = max(1.7, float(np.median(edges)) * 1.6)
    grid = None
    best_score = 0.0
    for interval in range(round(pitch * 0.96), round(pitch * 1.04) + 1):
        for phase in range(interval):
            seams = range(phase, width, interval)
            samples = [float(edges[pos]) for pos in seams if 4 <= pos < width - 4]
            supported = [score for score in samples if score >= min_edge]
            if len(supported) < 2:
                continue
            score = sum(min(value, 32.0) for value in supported)
            # Prefer the calibrated pitch when a rounded corner admits several nearby fits.
            score -= abs(interval - pitch) * 0.6
            if score > best_score:
                grid, best_score = (phase, interval), score
    if grid is None:
        return []

    phase, interval = grid
    starts = range(phase - interval, width, interval)
    return [
        ParentCard(left + start, left + start + interval, top, bottom, left, right)
        for start in starts
        if min(width, start + interval) - max(0, start) >= interval * 0.28
    ]


def _star_pixels(card: np.ndarray) -> int:
    height, width = card.shape[:2]
    badge = card[:round(height * 0.25), round(width * 0.66):round(width * 0.99)]
    if badge.size == 0:
        return 0
    hsv = cv2.cvtColor(badge, cv2.COLOR_BGR2HSV)
    return int(cv2.countNonZero(cv2.inRange(hsv, (15, 105, 155), (42, 255, 255))))


def has_parent_star(card: np.ndarray) -> bool:
    height, width = card.shape[:2]
    return min(height, width) >= 20 and _star_pixels(card) >= max(5, round(width * height * 0.0013))


def has_visible_parent_star(card: ParentCard, frame: np.ndarray) -> bool:
    """Test the visible part of a clipped card's original badge area."""
    left, top, right, bottom = card.bounds()
    width = card.right - card.left
    badge_left = max(left, card.left + round(width * 0.66))
    badge_right = min(right, card.left + round(width * 0.99))
    if badge_right - badge_left < 5:
        return False
    badge = frame[top:top + round((bottom - top) * 0.25), badge_left:badge_right]
    hsv = cv2.cvtColor(badge, cv2.COLOR_BGR2HSV)
    return cv2.countNonZero(cv2.inRange(hsv, (15, 105, 155), (42, 255, 255))) >= max(5, round(width * (bottom - top) * 0.0013))


def terminal_gap(frame: np.ndarray, viewport: tuple[int, int, int, int], pitch: int, direction: int) -> bool:
    """Find uninterrupted panel background at the end being revealed by a swipe."""
    left, top, right, bottom = viewport
    gap_width = round(pitch * 0.44)
    # Moving the contents right reveals the left end of the list and vice versa.
    edge = left if direction > 0 else right - gap_width
    neighbor = edge + pitch if direction > 0 else edge - pitch
    floor_top = top + round((bottom - top) * 0.83)
    gap = frame[floor_top:bottom, edge:edge + gap_width]
    border = frame[floor_top:bottom, neighbor:neighbor + gap_width]
    if gap.size == 0 or border.size == 0:
        return False
    gap_strength = float(np.abs(np.diff(gap.astype(np.float32), axis=0)).mean())
    border_strength = float(np.abs(np.diff(border.astype(np.float32), axis=0)).mean())
    return gap_strength < 4.0 and border_strength >= max(6.5, gap_strength * 2.0)


def parent_star_digits(card: np.ndarray) -> np.ndarray:
    """Crop the number immediately before the gold star on a complete lower card."""
    height, width = card.shape[:2]
    return card[round(height * 0.025):round(height * 0.24), round(width * 0.46):round(width * 0.76)]


def parent_tile_was_emptied(before: np.ndarray, after: np.ndarray) -> bool:
    """A selected tile retains its lower border and loses both badge and sprite."""
    if before.shape != after.shape or not has_parent_star(before) or has_parent_star(after):
        return False
    height, width = before.shape[:2]
    diff = cv2.absdiff(before, after)
    lower_border = diff[round(height * 0.87):round(height * 0.97)]
    art = np.s_[round(height * 0.24):round(height * 0.82), round(width * 0.15):round(width * 0.85)]
    sides = diff[round(height * 0.31):round(height * 0.82)]
    left_edge = sides[:, round(width * 0.04):round(width * 0.11)]
    right_edge = sides[:, round(width * 0.89):round(width * 0.96)]
    return bool(
        lower_border.mean() <= 7.0
        and max(left_edge.mean(), right_edge.mean()) <= 11.0
        and (diff[art].max(axis=2) > 35).mean() >= 0.06
        and (after[art].max(axis=2) >= 155).mean() <= 0.025
    )
