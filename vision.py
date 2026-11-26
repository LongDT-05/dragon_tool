"""
vision.py - Engine thị giác OpenCV v3.0 (Đa màn hình + Tự động Co Giãn Ảnh Mẫu)
Nâng cấp v3.0:
- Hỗ trợ mọi kích thước cửa sổ LDPlayer (nhỏ, lớn, fullscreen) nhờ tự động co giãn
  (auto-scale) ảnh mẫu theo tỷ lệ so với kích thước lúc chụp mẫu (reference_window_size).
- Hỗ trợ đa màn hình (2–3 màn hình, kể cả tọa độ âm) thông qua virtual screen mss.monitors[0].
- Xếp hạng Top-N ảnh mẫu và tính frame_diff phát hiện chạm đáy danh sách.
"""

from __future__ import annotations

import ctypes
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

try:
    import mss
except ImportError:
    mss = None

try:
    import pygetwindow as gw
except ImportError:
    gw = None


def enable_windows_dpi_awareness() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def imread_unicode(path: str | Path) -> Optional[np.ndarray]:
    p = Path(path)
    if not p.is_file():
        return None
    try:
        data = np.fromfile(str(p), dtype=np.uint8)
        return cv2.imdecode(data, cv2.IMREAD_COLOR)
    except Exception:
        return None


def imwrite_unicode(path: str | Path, img: np.ndarray) -> bool:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    ext = p.suffix or ".png"
    try:
        ok, buf = cv2.imencode(ext, img)
        if not ok:
            return False
        buf.tofile(str(p))
        return True
    except Exception:
        return False


def compute_frame_similarity(img_a: np.ndarray, img_b: np.ndarray) -> float:
    """Độ tương đồng cấu trúc (0.0 -> 1.0) sau khi làm mờ nhẹ để bỏ qua hiệu ứng rồng thở/nhấp nháy."""
    if img_a is None or img_b is None or img_a.shape != img_b.shape:
        return 0.0
    ga = cv2.GaussianBlur(cv2.cvtColor(img_a, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    gb = cv2.GaussianBlur(cv2.cvtColor(img_b, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    res = cv2.matchTemplate(ga, gb, cv2.TM_CCOEFF_NORMED)
    return float(res[0, 0])


def compute_frame_diff(img_a: np.ndarray, img_b: np.ndarray) -> float:
    if img_a is None or img_b is None or img_a.shape != img_b.shape:
        return 999.0
    diff = cv2.absdiff(img_a, img_b)
    return float(np.mean(diff))


@dataclass
class WindowRect:
    left: int
    top: int
    width: int
    height: int
    found_window: bool = False
    title: str = ""

    def rel_to_abs(self, rx: float, ry: float) -> Tuple[int, int]:
        return (
            int(self.left + self.width * rx),
            int(self.top + self.height * ry),
        )

    def abs_to_rel(self, x: int, y: int) -> Tuple[float, float]:
        rx = (x - self.left) / max(1, self.width)
        ry = (y - self.top) / max(1, self.height)
        return (
            round(max(0.0, min(1.0, rx)), 4),
            round(max(0.0, min(1.0, ry)), 4),
        )

    def roi_rel_to_local_px(self, roi_rel: List[float]) -> Tuple[int, int, int, int]:
        x1 = max(0, int(self.width * roi_rel[0]))
        y1 = max(0, int(self.height * roi_rel[1]))
        x2 = min(self.width, int(self.width * roi_rel[2]))
        y2 = min(self.height, int(self.height * roi_rel[3]))
        return x1, y1, x2, y2


@dataclass
class MatchResult:
    found: bool
    name: str = ""
    score: float = 0.0
    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0
    category: str = ""


class VisionEngine:
    def __init__(
        self,
        window_title_keyword: str = "AUTO",
        use_color_check: bool = True,
        reference_size: Optional[Tuple[int, int]] = None,
        dragon_reference_size: Optional[Tuple[int, int]] = None,
        auto_scale: bool = True,
        adb: Optional[object] = None,
        adb_serial: str = "",
    ):
        enable_windows_dpi_awareness()
        self.window_title_keyword = window_title_keyword
        self.use_color_check = use_color_check
        self.reference_size = reference_size or (0, 0)
        self.dragon_reference_size = dragon_reference_size or (0, 0)
        self.auto_scale = auto_scale
        self.adb = adb
        self.adb_serial = adb_serial
        self._template_cache: Dict[str, Tuple[float, np.ndarray]] = {}
        self._scaled_cache: Dict[Tuple[str, int, int], np.ndarray] = {}
        self._sct = mss.mss() if mss is not None else None

    @property
    def is_adb_mode(self) -> bool:
        return self.adb is not None and bool(self.adb_serial)

    def adjust_rel_point(self, rx: float, ry: float) -> Tuple[float, float]:
        """Tự động bù trừ thanh tiêu đề & thanh bên LDPlayer (~36px) khi chuyển sang chạy ngầm ADB."""
        if not self.is_adb_mode:
            return rx, ry
        ref_w, ref_h = self.reference_size
        if ref_w < 600 or ref_h < 400:
            return rx, ry
        inner_w = max(200.0, float(ref_w) - 36.0)
        inner_h = max(200.0, float(ref_h) - 36.0)
        adj_x = max(0.01, min(0.99, (rx * ref_w) / inner_w))
        adj_y = max(0.01, min(0.99, (ry * ref_h - 36.0) / inner_h))
        return round(adj_x, 4), round(adj_y, 4)

    def adjust_roi(self, roi_rel: Optional[List[float]]) -> Optional[List[float]]:
        if roi_rel is None or not self.is_adb_mode or len(roi_rel) != 4:
            return roi_rel
        x1, y1 = self.adjust_rel_point(float(roi_rel[0]), float(roi_rel[1]))
        x2, y2 = self.adjust_rel_point(float(roi_rel[2]), float(roi_rel[3]))
        return [x1, y1, max(x1 + 0.02, x2), max(y1 + 0.02, y2)]

    def clear_cache(self) -> None:
        self._template_cache.clear()
        self._scaled_cache.clear()

    def get_virtual_screen(self) -> WindowRect:
        """Lấy khung toàn bộ các màn hình đang kết nối (hỗ trợ 2–3 màn hình & tọa độ âm)."""
        if self._sct is not None:
            mon = self._sct.monitors[0]
            return WindowRect(
                left=int(mon["left"]),
                top=int(mon["top"]),
                width=int(mon["width"]),
                height=int(mon["height"]),
                found_window=False,
                title="All Monitors",
            )
        return WindowRect(0, 0, 1920, 1080)

    KNOWN_EMULATORS = ("LDPlayer", "BlueStacks", "MuMu", "Nox", "MEmu", "MSI App Player")

    def get_target_window(self) -> WindowRect:
        """Tự động nhận diện giả lập (AUTO) hoặc tìm theo từ khóa cửa sổ trên bất kỳ màn hình nào."""
        if gw is not None:
            kw = (self.window_title_keyword or "AUTO").strip()
            keywords = self.KNOWN_EMULATORS if kw.upper() in ("", "AUTO") else (kw,)
            try:
                for k in keywords:
                    wins = gw.getWindowsWithTitle(k)
                    for w in wins:
                        title_low = (w.title or "").lower()
                        if any(skip in title_low for skip in ("đảo rồng auto", "dao rong auto", "daorong_tool")):
                            continue
                        if w.width > 120 and w.height > 120 and not w.isMinimized:
                            return WindowRect(
                                left=int(w.left),
                                top=int(w.top),
                                width=int(w.width),
                                height=int(w.height),
                                found_window=True,
                                title=str(w.title),
                            )
            except Exception:
                pass

        if self._sct is not None:
            mon = self._sct.monitors[1]
            return WindowRect(
                left=int(mon["left"]),
                top=int(mon["top"]),
                width=int(mon["width"]),
                height=int(mon["height"]),
                found_window=False,
                title="Primary Monitor",
            )
        return WindowRect(0, 0, 1920, 1080)

    def capture_window(self, rect: Optional[WindowRect] = None) -> Tuple[np.ndarray, WindowRect]:
        if self.adb is not None and self.adb_serial:
            bgr = self.adb.capture_screen(self.adb_serial)
            if bgr is None:
                time.sleep(0.15)
                bgr = self.adb.capture_screen(self.adb_serial)
            if bgr is None:
                raise RuntimeError(f"Không chụp được màn hình từ ADB ({self.adb_serial}). Kiểm tra giả lập đã bật Gỡ lỗi ADB chưa.")
            h, w = bgr.shape[:2]
            return bgr, WindowRect(
                left=0,
                top=0,
                width=w,
                height=h,
                found_window=True,
                title=f"ADB:{self.adb_serial}",
            )
        if rect is None:
            rect = self.get_target_window()
        if self._sct is None:
            raise RuntimeError("Thư viện 'mss' chưa được cài đặt.")
        monitor = {
            "left": rect.left,
            "top": rect.top,
            "width": rect.width,
            "height": rect.height,
        }
        raw = self._sct.grab(monitor)
        img = np.array(raw, dtype=np.uint8)
        bgr = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
        return bgr, rect

    def crop_roi(
        self,
        screen_bgr: np.ndarray,
        rect: WindowRect,
        roi_rel: Optional[List[float]],
    ) -> Tuple[np.ndarray, int, int]:
        ox, oy = rect.left, rect.top
        if roi_rel is None:
            return screen_bgr, ox, oy
        x1, y1, x2, y2 = rect.roi_rel_to_local_px(roi_rel)
        if x2 > x1 and y2 > y1:
            return screen_bgr[y1:y2, x1:x2], ox + x1, oy + y1
        return screen_bgr, ox, oy

    def _load_template(
        self, path: str | Path, target_rect: Optional[WindowRect] = None
    ) -> Optional[np.ndarray]:
        p = Path(path)
        if not p.is_file():
            return None
        key = str(p.resolve())
        mtime = p.stat().st_mtime
        cached = self._template_cache.get(key)
        if cached is not None and cached[0] == mtime:
            base_img = cached[1]
        else:
            base_img = imread_unicode(p)
            if base_img is None:
                return None
            self._template_cache[key] = (mtime, base_img)
            # Xóa cache scaled cũ của file này
            for sk in [k for k in self._scaled_cache if k[0] == key]:
                del self._scaled_cache[sk]

        # Dùng kích thước chuẩn riêng cho nút UI (setup sẵn) vs ảnh Rồng (người dùng tự chụp)
        is_ui_tmpl = p.parent.name.lower() == "ui"
        ref_w, ref_h = self.reference_size if is_ui_tmpl else self.dragon_reference_size
        # Nếu đang chụp ngầm ADB (không có viền tiêu đề & thanh bên LDPlayer ~36px) -> tự trừ viền
        if self.is_adb_mode and ref_w > 600 and ref_h > 400:
            ref_w = ref_w - 36
            ref_h = ref_h - 36
        if (
            self.auto_scale
            and target_rect is not None
            and ref_w > 200
            and ref_h > 200
            and target_rect.width > 200
            and target_rect.height > 200
        ):
            sx = target_rect.width / float(ref_w)
            sy = target_rect.height / float(ref_h)
            if abs(sx - 1.0) > 0.025 or abs(sy - 1.0) > 0.025:
                bh, bw = base_img.shape[:2]
                nw = max(6, int(round(bw * sx)))
                nh = max(6, int(round(bh * sy)))
                skey = (key, nw, nh)
                scaled = self._scaled_cache.get(skey)
                if scaled is None:
                    interp = cv2.INTER_AREA if (sx < 1.0 or sy < 1.0) else cv2.INTER_CUBIC
                    scaled = cv2.resize(base_img, (nw, nh), interpolation=interp)
                    self._scaled_cache[skey] = scaled
                return scaled

        return base_img

    def match_image(
        self,
        haystack_bgr: np.ndarray,
        template_bgr: np.ndarray,
        threshold: float = 0.78,
        offset_x: int = 0,
        offset_y: int = 0,
        name: str = "",
        category: str = "",
    ) -> MatchResult:
        th, tw = template_bgr.shape[:2]
        hh, hw = haystack_bgr.shape[:2]
        if th > hh or tw > hw:
            return MatchResult(found=False, name=name, score=0.0, category=category)

        if self.use_color_check:
            res = cv2.matchTemplate(haystack_bgr, template_bgr, cv2.TM_CCOEFF_NORMED)
        else:
            h_gray = cv2.cvtColor(haystack_bgr, cv2.COLOR_BGR2GRAY)
            t_gray = cv2.cvtColor(template_bgr, cv2.COLOR_BGR2GRAY)
            res = cv2.matchTemplate(h_gray, t_gray, cv2.TM_CCOEFF_NORMED)

        _, max_val, _, max_loc = cv2.minMaxLoc(res)
        score = float(max_val)
        if score >= threshold:
            return MatchResult(
                found=True,
                name=name,
                score=score,
                x=offset_x + max_loc[0] + tw // 2,
                y=offset_y + max_loc[1] + th // 2,
                w=tw,
                h=th,
                category=category,
            )
        return MatchResult(found=False, name=name, score=score, category=category)

    def find_template(
        self,
        template_path: str | Path,
        threshold: float = 0.78,
        roi_rel: Optional[List[float]] = None,
        screen_bgr: Optional[np.ndarray] = None,
        rect: Optional[WindowRect] = None,
    ) -> MatchResult:
        if screen_bgr is None or rect is None:
            screen_bgr, rect = self.capture_window(rect)
        tmpl = self._load_template(template_path, target_rect=rect)
        name = Path(template_path).stem
        if tmpl is None:
            return MatchResult(found=False, name=name, score=0.0)
        search_img, ox, oy = self.crop_roi(screen_bgr, rect, roi_rel)
        best = self.match_image(
            search_img, tmpl, threshold=threshold, offset_x=ox, offset_y=oy, name=name
        )
        # Multi-scale fallback khi Fullscreen (do mất thanh tiêu đề/toolbar làm lệch nhẹ tỷ lệ 3-6%)
        is_ui_tmpl = Path(template_path).parent.name.lower() == "ui"
        eff_ref = self.reference_size if is_ui_tmpl else self.dragon_reference_size
        if not best.found and self.auto_scale and eff_ref[0] > 200:
            sx = rect.width / float(eff_ref[0])
            sy = rect.height / float(eff_ref[1])
            if abs(sx - 1.0) > 0.03 or abs(sy - 1.0) > 0.03:
                base_img = self._load_template(template_path, target_rect=None)
                if base_img is not None:
                    bh, bw = base_img.shape[:2]
                    s_avg = (sx + sy) / 2.0
                    for mult in (1.0, 0.95, 1.05):
                        nw = max(6, int(round(bw * s_avg * mult)))
                        nh = max(6, int(round(bh * s_avg * mult)))
                        interp = cv2.INTER_AREA if (s_avg * mult) < 1.0 else cv2.INTER_CUBIC
                        cand = cv2.resize(base_img, (nw, nh), interpolation=interp)
                        m = self.match_image(
                            search_img, cand, threshold=threshold, offset_x=ox, offset_y=oy, name=name
                        )
                        if m.score > best.score:
                            best = m
                        if best.found:
                            break
        return best

    def rank_across_folders(
        self,
        category_folders: Dict[str, Path],
        roi_rel: Optional[List[float]] = None,
        screen_bgr: Optional[np.ndarray] = None,
        rect: Optional[WindowRect] = None,
    ) -> List[MatchResult]:
        if screen_bgr is None or rect is None:
            screen_bgr, rect = self.capture_window(rect)
        search_img, ox, oy = self.crop_roi(screen_bgr, rect, roi_rel)

        results: List[MatchResult] = []
        for category, folder in category_folders.items():
            if not folder.is_dir():
                continue
            for img_file in sorted(folder.iterdir()):
                if img_file.suffix.lower() not in {".png", ".jpg", ".jpeg", ".bmp"}:
                    continue
                tmpl = self._load_template(img_file, target_rect=rect)
                if tmpl is None:
                    continue
                m = self.match_image(
                    search_img,
                    tmpl,
                    threshold=0.0,
                    offset_x=ox,
                    offset_y=oy,
                    name=img_file.stem,
                    category=category,
                )
                results.append(m)

        results.sort(key=lambda r: r.score, reverse=True)
        return results

    def find_best_across_folders(
        self,
        category_folders: Dict[str, Path],
        threshold: float = 0.76,
        roi_rel: Optional[List[float]] = None,
        screen_bgr: Optional[np.ndarray] = None,
        rect: Optional[WindowRect] = None,
    ) -> MatchResult:
        ranked = self.rank_across_folders(
            category_folders, roi_rel=roi_rel, screen_bgr=screen_bgr, rect=rect
        )
        if not ranked:
            return MatchResult(found=False, score=0.0)
        best = ranked[0]
        best.found = best.score >= threshold
        return best

    def wait_for_template(
        self,
        template_path: str | Path,
        timeout: float = 8.0,
        threshold: float = 0.78,
        poll_interval: float = 0.12,
        roi_rel: Optional[List[float]] = None,
        stop_check=None,
    ) -> MatchResult:
        deadline = time.monotonic() + timeout
        last = MatchResult(found=False, name=Path(template_path).stem)
        while time.monotonic() < deadline:
            if stop_check and stop_check():
                return MatchResult(found=False, name=last.name)
            res = self.find_template(template_path, threshold=threshold, roi_rel=roi_rel)
            if res.found:
                return res
            last = res
            time.sleep(poll_interval)
        return last

    def find_template_smart(
        self,
        template_path: str | Path,
        calibrator: Optional[object] = None,
        mode_key: str = "win",
        threshold: float = 0.78,
        roi_rel: Optional[List[float]] = None,
        screen_bgr: Optional[np.ndarray] = None,
        rect: Optional[WindowRect] = None,
    ) -> MatchResult:
        """
        Quét mốc tọa độ đã học trước (±9% ROI -> giảm ~70% CPU & nhanh gấp 5x).
        Nếu không thấy ở vùng hẹp sẽ tự động quét toàn màn hình và cập nhật lại mốc mới.
        """
        p = Path(template_path)
        if screen_bgr is None or rect is None:
            screen_bgr, rect = self.capture_window()

        if calibrator is not None and roi_rel is None:
            fast_roi = calibrator.get_roi(mode_key, p.name)
            if fast_roi is not None:
                res_fast = self.find_template(
                    p, threshold=threshold, roi_rel=fast_roi, screen_bgr=screen_bgr, rect=rect
                )
                if res_fast.found:
                    return res_fast

        res_full = self.find_template(
            p, threshold=threshold, roi_rel=roi_rel, screen_bgr=screen_bgr, rect=rect
        )
        if res_full.found and calibrator is not None:
            rx, ry = rect.abs_to_rel(res_full.x, res_full.y)
            calibrator.record(mode_key, p.name, rx, ry)
        return res_full

    def test_template_quality(self, template_path: str | Path) -> Tuple[float, str]:
        """Kiểm tra ngay độ khớp của ảnh rồng vừa chụp trên màn hình giả lập để người mới yên tâm."""
        try:
            screen, rect = self.capture_window()
            res = self.find_template(template_path, threshold=0.40, screen_bgr=screen, rect=rect)
            sc = res.score
            pct = int(round(sc * 100))
            if sc >= 0.88:
                return sc, f"✅ Chuẩn lắm! (khớp {pct}% — tool nhận diện mượt 100%)"
            if sc >= 0.72:
                return sc, f"🟡 Tạm ổn (khớp {pct}%) — vẫn dùng được, nhưng chụp gọn hơn sẽ nhanh hơn"
            return sc, f"⚠ Độ khớp thấp ({pct}%) — hãy mở đúng màn hình có rồng đó và chụp gọn phần hình rồng"
        except Exception as exc:
            return 0.0, f"⚠ Không kiểm tra được ngay: {exc}"
