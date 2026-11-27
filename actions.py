"""
actions.py - Bộ điều khiển thao tác TỰ NHIÊN NHƯ NGƯỜI THẬT (DaoRong_tool v9.0)
Hỗ trợ 2 chế độ:
1. Chuột Windows tự nhiên: di chuyển theo đường cong Bezier nhẹ, lệch ngẫu nhiên ±3-5px,
   nhịp bấm & giữ chuột có biến thiên tự nhiên (không máy móc lặp từng mili-giây).
2. Chạy Ngầm qua ADB: gửi chạm/vuốt trực tiếp vào bên trong giả lập (không chiếm chuột thật,
   treo nhiều acc song song an toàn).
"""

from __future__ import annotations

import random
import time
from typing import Any, Callable, Optional

try:
    import pyautogui

    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.015
except ImportError:
    pyautogui = None


class InputController:
    """Thực hiện click & vuốt mượt tự nhiên (hỗ trợ cả Chuột Windows lẫn ADB chạy ngầm)."""

    def __init__(
        self,
        click_delay: float = 0.24,
        jitter_px: int = 4,
        human_like: bool = True,
        adb: Optional[Any] = None,
        adb_serial: str = "",
    ):
        self.click_delay = click_delay
        self.jitter_px = jitter_px
        self.human_like = human_like
        self.adb = adb
        self.adb_serial = adb_serial

    @property
    def is_adb_mode(self) -> bool:
        return self.adb is not None and bool(self.adb_serial)

    def _require_pyautogui(self) -> None:
        if pyautogui is None:
            raise RuntimeError("Thư viện 'pyautogui' chưa được cài đặt.")

    def _humanize_delay(self, seconds: float) -> float:
        if seconds <= 0:
            return 0.0
        if not self.human_like:
            return seconds
        return max(0.01, seconds * random.uniform(0.86, 1.18))

    def _human_offset(self) -> tuple[int, int]:
        if self.jitter_px <= 0:
            return 0, 0
        # Phân phối chuẩn tập trung quanh tâm nút để không bao giờ bấm trượt ra mép
        jx = int(round(random.gauss(0, self.jitter_px * 0.5)))
        jy = int(round(random.gauss(0, self.jitter_px * 0.5)))
        jx = max(-self.jitter_px, min(self.jitter_px, jx))
        jy = max(-self.jitter_px, min(self.jitter_px, jy))
        return jx, jy

    def _move_bezier(self, tx: int, ty: int, duration: float) -> None:
        """Di chuột theo đường cong Bezier bậc 2 tự nhiên như cổ tay người thật."""
        cx, cy = pyautogui.position()
        dist = ((tx - cx) ** 2 + (ty - cy) ** 2) ** 0.5
        if dist < 18 or not self.human_like:
            pyautogui.moveTo(tx, ty, duration=max(0.02, duration))
            return

        # Điểm uốn cong nhẹ ngẫu nhiên (lệch 5-12% quãng đường)
        mid_x = (cx + tx) / 2.0 + random.uniform(-0.10, 0.10) * dist
        mid_y = (cy + ty) / 2.0 + random.uniform(-0.10, 0.10) * dist
        steps = max(6, min(16, int(dist / 55)))
        step_sleep = max(0.003, duration / steps)
        for i in range(1, steps + 1):
            t = i / steps
            # Ease-in-out
            u = t * t * (3.0 - 2.0 * t)
            inv = 1.0 - u
            px = int(inv * inv * cx + 2 * inv * u * mid_x + u * u * tx)
            py = int(inv * inv * cy + 2 * inv * u * mid_y + u * u * ty)
            pyautogui.moveTo(px, py, duration=0.0)
            time.sleep(step_sleep)

    def sleep_interruptible(
        self, seconds: float, stop_check: Optional[Callable[[], bool]] = None
    ) -> bool:
        """Ngủ theo bước nhỏ để phản hồi Tạm dừng / Dừng khẩn cấp gần như tức thì."""
        end = time.monotonic() + max(0.0, seconds)
        while time.monotonic() < end:
            if stop_check and stop_check():
                return False
            time.sleep(min(0.05, max(0.0, end - time.monotonic())))
        return True

    def click(
        self,
        x: int,
        y: int,
        post_delay: Optional[float] = None,
        stop_check: Optional[Callable[[], bool]] = None,
    ) -> None:
        """Click tự nhiên tại (x, y) — tự chọn ADB chạy ngầm hoặc Chuột Windows."""
        delay = self._humanize_delay(self.click_delay if post_delay is None else post_delay)

        if self.is_adb_mode:
            if stop_check and stop_check():
                return
            self.adb.tap(self.adb_serial, int(x), int(y), jitter_px=self.jitter_px if self.human_like else 1)
            self.sleep_interruptible(delay, stop_check)
            return

        self._require_pyautogui()
        jx, jy = self._human_offset()
        tx, ty = int(x + jx), int(y + jy)
        move_dur = random.uniform(0.035, 0.085) if self.human_like else 0.025
        self._move_bezier(tx, ty, duration=move_dur)
        pyautogui.mouseDown(tx, ty, button="left")
        time.sleep(random.uniform(0.028, 0.062) if self.human_like else 0.025)
        pyautogui.mouseUp(tx, ty, button="left")
        self.sleep_interruptible(delay, stop_check)

    def smooth_drag(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration: float = 0.42,
        hold_end: float = 0.14,
        steps: int = 22,
        stop_check: Optional[Callable[[], bool]] = None,
    ) -> None:
        """
        Vuốt mượt chống trôi quán tính trên giả lập.
        Ở chế độ ADB sẽ gửi lệnh swipe có thời lượng & độ lệch tự nhiên.
        """
        if self.is_adb_mode:
            if stop_check and stop_check():
                return
            dur_ms = int((duration + hold_end) * 1000)
            self.adb.swipe(
                self.adb_serial,
                int(x1),
                int(y1),
                int(x2),
                int(y2),
                duration_ms=dur_ms,
                jitter_px=self.jitter_px if self.human_like else 1,
            )
            return

        self._require_pyautogui()
        jx1, jy1 = self._human_offset()
        jx2, jy2 = self._human_offset()
        sx, sy = int(x1 + jx1), int(y1 + jy1)
        ex, ey = int(x2 + jx2), int(y2 + jy2)

        self._move_bezier(sx, sy, duration=random.uniform(0.04, 0.08) if self.human_like else 0.03)
        pyautogui.mouseDown(sx, sy, button="left")
        time.sleep(random.uniform(0.045, 0.075) if self.human_like else 0.05)

        actual_dur = self._humanize_delay(duration)
        step_delay = max(0.005, actual_dur / max(1, steps))
        for i in range(1, steps + 1):
            if stop_check and stop_check():
                pyautogui.mouseUp(button="left")
                return
            t = i / steps
            eased = 1.0 - (1.0 - t) ** 3
            cx = int(sx + (ex - sx) * eased)
            cy = int(sy + (ey - sy) * eased)
            pyautogui.moveTo(cx, cy, duration=0.0)
            time.sleep(step_delay)

        time.sleep(max(0.05, self._humanize_delay(hold_end)))
        pyautogui.mouseUp(ex, ey, button="left")
