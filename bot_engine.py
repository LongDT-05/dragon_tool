"""
bot_engine.py - Lõi State Machine Thông Minh Đảo Rồng Auto v3.0
Nâng cấp v3.0:
1. Chống rồng bay che Trứng / Tháp Tẩy Tủy: dùng tọa độ ghim tương đối + click xoắn ốc nhẹ;
   nếu lỡ click trúng con rồng bay ngang làm mở nhầm bảng rồng thì tự bấm X đóng rồi click lại!
2. Đóng bảng Hóa Bụi CHẮC CHẮN trước khi chuyển đảo <: hỗ trợ cả btn_close_dust_x.png lẫn
   btn_close_x.png (ngưỡng 0.66) và kiểm tra xác nhận nút Hóa Bụi đã biến mất rồi mới bấm <.
3. Tự phục hồi lỗi (Smart Recovery): không đứng chờ rồi skip; tự xác định màn hình hiện tại
   đang mở bảng nào để xử lý tiếp hoặc mở lại đúng bảng.
4. Nhận diện & ra tay gần như tức thì ngay khi Baby + Tên xuất hiện sau khi lai.
5. Tự động co giãn ảnh mẫu theo kích thước cửa sổ LDPlayer (nhỏ, lớn, fullscreen, 2 màn hình).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
import cv2
import numpy as np
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from actions import InputController
from ocr_engine import DragonTextMatcher, load_dragon_catalog, normalize_vn
import random
from smart_ai import (
    AnchorAutoCalibrator,
    OcrAdaptiveDictionary,
    PairStatsTracker,
    PersistentNameCache,
    RareMomentSaver,
    TrainingDatasetCollector,
)
from vision import MatchResult, VisionEngine, compute_frame_diff, compute_frame_similarity


class BotState(str, Enum):
    OPEN_BREED = "Mở giao diện lai (Chống rồng che trứng)"
    SELECT_PARENTS = "Chọn 2 rồng bố mẹ"
    WAIT_BREED_RESULT = "Đợi lai & bắt nhanh Baby + Tên"
    CLASSIFY_AND_ACT = "Xử lý Cất / Bán / Chúc phúc"
    EXIT_BREED_TO_DUST = "Đầy túi -> Đóng bảng lai"
    SWITCH_TO_DUST_ISLAND = "Chuyển sang đảo tẩy tủy (> x2)"
    OPEN_DUST_UI = "Mở Tháp Tẩy Tủy -> Bình Hóa Bụi"
    DUST_MATERIALS = "Quét 2 cột & hóa bụi nguyên liệu"
    RETURN_TO_EGG_ISLAND = "Đóng hẳn Hóa Bụi -> Về đảo trứng (< x2)"


@dataclass
class BotStats:
    breeds: int = 0
    kept: int = 0
    sold: int = 0
    blessed: int = 0
    dusted: int = 0
    cycles: int = 0
    recoveries: int = 0
    rare_hits: int = 0
    epic_hits: int = 0
    event_hits: int = 0
    started_at: float = 0.0
    recent_drops: List[Dict[str, Any]] = field(default_factory=list)
    current_state: str = "Sẵn sàng"

    @property
    def breeds_per_min(self) -> float:
        if self.started_at <= 0 or self.breeds <= 0:
            return 0.0
        mins = max(0.05, (time.monotonic() - self.started_at) / 60.0)
        return self.breeds / mins


SPEED_MULTIPLIERS = {
    "fast": 0.72,
    "balanced": 1.0,
    "safe": 1.35,
}


class DaoRongBot:
    def __init__(
        self,
        base_dir: Path,
        config: Dict[str, Any],
        log_callback: Callable[[str], None],
        stats_callback: Callable[[BotStats], None],
        alert_callback: Optional[Callable[[str, str], None]] = None,
        adb: Optional[Any] = None,
        adb_serial: str = "",
        account_label: str = "Mặc định",
        shared_text_matcher: Optional[DragonTextMatcher] = None,
        shared_name_cache: Optional[PersistentNameCache] = None,
        shared_calibrator: Optional[AnchorAutoCalibrator] = None,
        shared_pair_stats: Optional[PairStatsTracker] = None,
    ):
        self.base_dir = Path(base_dir)
        self.templates_dir = self.base_dir / "templates"
        self.ui_dir = self.templates_dir / "ui"
        self.parents_dir = self.templates_dir / "parents"
        self.keep_dir = self.templates_dir / "keep"
        self.sell_dir = self.templates_dir / "sell"
        self.bless_dir = self.templates_dir / "bless"
        self.dust_targets_dir = self.templates_dir / "dust_targets"
        self.protect_dust_dir = self.templates_dir / "protect_dust"
        self.protect_dust_dir.mkdir(parents=True, exist_ok=True)

        self.config = config
        self.log = log_callback
        self.on_stats = stats_callback
        self.on_alert = alert_callback
        self.adb = adb
        self.adb_serial = adb_serial
        self.account_label = account_label

        preset = config.get("speed_preset", "fast")
        self._spd = float(SPEED_MULTIPLIERS.get(preset, 1.0))

        ui_ref = config.get("ui_reference_window_size", config.get("reference_window_size", [1330, 761]))
        drg_ref = config.get("dragon_reference_window_size", [0, 0])
        ui_ref_t = (int(ui_ref[0]), int(ui_ref[1])) if isinstance(ui_ref, list) and len(ui_ref) == 2 else (1330, 761)
        drg_ref_t = (int(drg_ref[0]), int(drg_ref[1])) if isinstance(drg_ref, list) and len(drg_ref) == 2 else (0, 0)

        self.vision = VisionEngine(
            window_title_keyword=config.get("window_title_keyword", "AUTO"),
            use_color_check=bool(config.get("use_color_check", True)),
            reference_size=ui_ref_t,
            dragon_reference_size=drg_ref_t,
            auto_scale=bool(config.get("auto_scale_templates", True)),
            adb=adb,
            adb_serial=adb_serial,
        )
        self.input = InputController(
            click_delay=float(config.get("timings", {}).get("click_delay", 0.24)) * self._spd,
            jitter_px=4 if bool(config.get("human_like_input", True)) else 1,
            human_like=bool(config.get("human_like_input", True)),
            adb=adb,
            adb_serial=adb_serial,
        )

        self.stats = BotStats()
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._need_rescan = False
        self._need_restart = False
        self._thread: Optional[threading.Thread] = None
        self.text_matcher = shared_text_matcher or DragonTextMatcher(catalog=load_dragon_catalog(self.base_dir))
        self.name_cache = shared_name_cache or PersistentNameCache(self.templates_dir / "auto_learned")
        self.calibrator = shared_calibrator or AnchorAutoCalibrator()
        self.pair_stats = shared_pair_stats or PairStatsTracker(self.base_dir / "pair_stats.json")
        self._mode_key = f"adb_{adb_serial}" if adb_serial else "win"
        self._cached_result_btns: Dict[str, Tuple[int, int]] = {}
        self._cached_result_frame = None
        self._learned_name_cache: List[Tuple[Any, str, str]] = []
        self._consecutive_failures = 0
        self.ocr_dict = OcrAdaptiveDictionary(self.base_dir / "ocr_learned_map.json")
        self.rare_saver = RareMomentSaver(self.base_dir / "lich_su_rong_quy")
        self.dataset_collector = TrainingDatasetCollector(self.base_dir / "ai_dataset")
        self._next_break_ts = 0.0
        # Sổ Tay Rồng Vừa Lai: lưu {(Độ hiếm, Số sao): số con vừa cất chờ hóa bụi} -> 0% hóa bụi nhầm rồng cũ!
        self._session_dust_ledger: Dict[Tuple[str, int], int] = {}
        self._wd_last_frame = None
        self._wd_last_change_ts = 0.0

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def is_paused(self) -> bool:
        return self._pause_event.is_set()

    def should_stop(self) -> bool:
        return self._stop_event.is_set()

    def wait_if_paused(self) -> bool:
        while self._pause_event.is_set() and not self._stop_event.is_set():
            time.sleep(0.08)
        return not self._stop_event.is_set()

    @property
    def is_breed_only(self) -> bool:
        return str(self.config.get("bot_mode", "breed_and_dust")) == "breed_only"

    def start(self, restart_from_egg: bool = False) -> None:
        if self.is_running:
            if self.is_paused:
                if restart_from_egg:
                    self._need_restart = True
                else:
                    self._need_rescan = True
                self.resume()
            elif restart_from_egg:
                self._need_restart = True
            return
        self._stop_event.clear()
        self._pause_event.clear()
        self._need_restart = restart_from_egg
        self._need_rescan = not restart_from_egg
        self._consecutive_failures = 0
        self.stats.started_at = time.monotonic()
        self._wd_last_frame = None
        self._wd_last_change_ts = time.monotonic()
        self.vision.clear_cache()
        self._learned_name_cache.clear()
        self.text_matcher.warmup_async()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def pause(self) -> None:
        if self.is_running and not self._pause_event.is_set():
            self._pause_event.set()
            self.stats.current_state = "Tạm dừng"
            self.on_stats(self.stats)
            self.log("[TẠM DỪNG] Đã tạm dừng tại chỗ. Bấm 'Tiếp tục' (Ctrl+Enter) để chạy tiếp đúng bước đang dở.")

    def resume(self) -> None:
        if self.is_running and self._pause_event.is_set():
            self._need_rescan = True
            self._wd_last_change_ts = time.monotonic()
            self._pause_event.clear()
            self.log("[TIẾP TỤC] Đang tự quét màn hình game để xác định vị trí và chạy tiếp...")

    def restart_from_beginning(self) -> None:
        if self.is_running:
            self._need_restart = True
            self._pause_event.clear()
            self.log("[CHẠY LẠI] Đang tự kiểm tra màn hình để quay về Đảo Trứng và bắt đầu lại...")
        else:
            self.start(restart_from_egg=True)

    def stop(self) -> None:
        self._stop_event.set()
        self._pause_event.clear()
        self.log("[DỪNG] Đang dừng khẩn cấp...")

    def _trigger_alert_and_pause(self, title: str, message: str) -> None:
        self.log(f"[THÔNG BÁO] {title} — {message}")
        self._pause_event.set()
        self.stats.current_state = f"Tạm dừng: {title}"
        self.on_stats(self.stats)
        if self.on_alert:
            try:
                self.on_alert(f"[{self.account_label}] {title}", message)
            except Exception:
                pass

    def detect_current_screen(self, for_restart: bool = False) -> BotState:
        """
        Bộ não nhận diện ngữ cảnh màn hình (~50ms):
        Tự biết game đang mở bảng nào (Kết quả lai / Chọn bố mẹ / Đầy túi / Hóa Bụi / Tẩy Tủy /
        Bảng phụ chạm nhầm rồng / Ngoài đảo) để Tiếp tục đúng bước hoặc Chạy lại từ đầu mượt mà.
        """
        screen, rect = self.vision.capture_window()

        # 1. Thông báo đầy túi
        if self._check_bag_full(screen, rect):
            if self.is_breed_only:
                self._trigger_alert_and_pause(
                    "Túi rồng đã đầy!",
                    "Chế độ Chỉ Auto Lai đã tự tạm dừng an toàn để không hóa bụi nhầm rồng của bạn.",
                )
                return BotState.OPEN_BREED
            return BotState.EXIT_BREED_TO_DUST

        # 2. Đang hiện bảng kết quả lai (có nút Cất / Bán / Chúc phúc) -> Xử lý ngay, không bỏ lỡ con vừa lai!
        btn_thr = min(0.72, self._conf("button", 0.76))
        found_btns: Dict[str, Tuple[int, int]] = {}
        for bname in ("btn_keep.png", "btn_sell.png", "btn_bless.png"):
            bp = self._ui(bname)
            if bp.is_file():
                m = self.vision.find_template(bp, threshold=btn_thr, screen_bgr=screen, rect=rect)
                if m.found:
                    found_btns[bname] = (m.x, m.y)
        if len(found_btns) >= 2 or "btn_keep.png" in found_btns:
            self._cached_result_btns = found_btns
            self._cached_result_frame = (screen, rect)
            self.log("[NHẬN DIỆN MÀN HÌNH] Đang ở bảng Kết Quả Lai -> Nhận diện & xử lý rồng ngay!")
            return BotState.CLASSIFY_AND_ACT

        # 3. Đang ở bảng chọn bố mẹ (thấy nút Lai Rồng)
        bp_start = self._ui("btn_start_breed.png")
        if bp_start.is_file() and self.vision.find_template(bp_start, threshold=0.72, screen_bgr=screen, rect=rect).found:
            self.log("[NHẬN DIỆN MÀN HÌNH] Đang ở bảng Lai Rồng -> Chọn bố mẹ & lai tiếp.")
            return BotState.SELECT_PARENTS

        # 4. Đang ở tab Hóa Bụi (thấy nút Hóa Bụi)
        if self._is_dust_ui_open(screen, rect):
            if for_restart or self.is_breed_only:
                self.log("[NHẬN DIỆN MÀN HÌNH] Đang ở bảng Hóa Bụi -> Đóng bảng & quay về Đảo Trứng.")
                return BotState.RETURN_TO_EGG_ISLAND
            self.log("[NHẬN DIỆN MÀN HÌNH] Đang ở tab Hóa Bụi -> Tiếp tục quét & hóa bụi.")
            return BotState.DUST_MATERIALS

        # 5. Đang ở tab Tẩy Tủy (thấy bình Hóa Bụi)
        bp_bottle = self._ui("btn_dust_bottle.png")
        if bp_bottle.is_file() and self.vision.find_template(bp_bottle, threshold=0.70, screen_bgr=screen, rect=rect).found:
            if for_restart or self.is_breed_only:
                self.log("[NHẬN DIỆN MÀN HÌNH] Đang ở bảng Tẩy Tủy -> Đóng bảng & quay về Đảo Trứng.")
                return BotState.RETURN_TO_EGG_ISLAND
            self.log("[NHẬN DIỆN MÀN HÌNH] Đang ở bảng Tẩy Tủy -> Chuyển sang bình Hóa Bụi.")
            return BotState.OPEN_DUST_UI

        # 6. Có bảng lạ / chạm nhầm rồng bay ngang (thấy dấu X) -> Đóng ngay rồi quét lại
        rx = self._find_any_close_x(screen, rect)
        if rx.found:
            self.log("[NHẬN DIỆN MÀN HÌNH] Phát hiện bảng phụ / chạm nhầm rồng -> Tự bấm X đóng.")
            self.input.click(rx.x, rx.y, post_delay=0.45 * self._spd, stop_check=self.should_stop)
            screen, rect = self.vision.capture_window()

        # 6B. Nếu lỡ bấm nhầm vào Map / màn hình phụ -> bấm nút Về Đảo đến khi hiện mũi tên > <
        if self._recover_from_map_or_subscreen(max_clicks=4):
            screen, rect = self.vision.capture_window()

        # 7. Đang ở ngoài đảo: kiểm tra Trứng hay Tháp Tẩy Tủy
        egg_p = self._ui("btn_egg.png")
        if egg_p.is_file() and self.vision.find_template(egg_p, threshold=0.68, screen_bgr=screen, rect=rect).found:
            self.log("[NHẬN DIỆN MÀN HÌNH] Đang ở Đảo Trứng -> Mở bảng lai.")
            return BotState.OPEN_BREED

        altar_p = self._ui("btn_altar_center.png")
        if altar_p.is_file() and self.vision.find_template(altar_p, threshold=0.68, screen_bgr=screen, rect=rect).found:
            if for_restart or self.is_breed_only:
                self.log("[NHẬN DIỆN MÀN HÌNH] Đang ở Đảo Tẩy Tủy -> Quay về Đảo Trứng.")
                return BotState.RETURN_TO_EGG_ISLAND
            self.log("[NHẬN DIỆN MÀN HÌNH] Đang ở Đảo Tẩy Tủy -> Mở tháp Hóa Bụi.")
            return BotState.OPEN_DUST_UI

        # Nếu bấm Chạy lại mà đang ở đảo khác -> dò bấm < tối đa 3 lần để tìm Đảo Trứng
        if for_restart:
            self.log("[NHẬN DIỆN MÀN HÌNH] Chưa thấy Trứng -> Tự dò quay về Đảo Trứng...")
            nav_thr = self._conf("nav_button", 0.72)
            for _ in range(3):
                if self.should_stop():
                    break
                self._click_ui("btn_arrow_left.png", timeout=3.0, threshold=nav_thr, retries=1, post_delay=0.75 * self._spd)
                scr2, r2 = self.vision.capture_window()
                if egg_p.is_file() and self.vision.find_template(egg_p, threshold=0.68, screen_bgr=scr2, rect=r2).found:
                    self.log("[NHẬN DIỆN MÀN HÌNH] Đã về đúng Đảo Trứng!")
                    break
        return BotState.OPEN_BREED

    def _ui(self, filename: str) -> Path:
        return self.ui_dir / filename

    def _conf(self, key: str, default: float = 0.76) -> float:
        return float(self.config.get("confidence", {}).get(key, default))

    def _timing(self, key: str, default: float = 0.5) -> float:
        base = float(self.config.get("timings", {}).get(key, default))
        if key in {"breed_wait_timeout", "bless_wait_timeout", "ui_wait_timeout"}:
            return base
        return max(0.06, base * self._spd)

    def log_tech(self, msg: str) -> None:
        """Chỉ ghi log kỹ thuật khi người dùng bật 'Log chi tiết kỹ thuật' trong Cài Đặt."""
        if self.config.get("verbose_log", False):
            self.log(msg)

    def _drag_cfg(self, key: str, default: Any) -> Any:
        val = self.config.get("drag_settings", {}).get(key, default)
        if self.vision.is_adb_mode and isinstance(val, list):
            if len(val) == 4 and key.endswith("_roi"):
                return self.vision.adjust_roi(val)
            if len(val) == 2 and ("drag_start" in key or "drag_end" in key):
                ax, ay = self.vision.adjust_rel_point(float(val[0]), float(val[1]))
                return [ax, ay]
        return val

    def _pin(self, key: str, default: List[float]) -> List[float]:
        val = self.config.get("pins", {}).get(key, default)
        if self.vision.is_adb_mode and isinstance(val, list) and len(val) == 2:
            ax, ay = self.vision.adjust_rel_point(float(val[0]), float(val[1]))
            return [ax, ay]
        return val

    def _click_and_verify_gone(
        self,
        btn_filename: str,
        x: int,
        y: int,
        max_wait: float = 0.75,
        retries: int = 2,
    ) -> bool:
        """
        Cơ chế Action-Verify (v10.0): bấm nút -> kiểm tra nút biến mất ngay để chuyển bước tức thì
        (nhanh hơn ~35%), còn nếu game lag khiến nút vẫn còn -> tự bấm lại ngay không chờ timeout!
        """
        bp = self._ui(btn_filename)
        thr = min(0.74, self._conf("button", 0.76))
        for attempt in range(1, retries + 1):
            self.input.click(x, y, post_delay=0.08 * self._spd, stop_check=self.should_stop)
            if not bp.is_file():
                return True
            deadline = time.monotonic() + max_wait
            while time.monotonic() < deadline:
                if self.should_stop():
                    return False
                m = self.vision.find_template_smart(bp, self.calibrator, self._mode_key, threshold=thr)
                if not m.found:
                    return True
                time.sleep(0.06)
            if attempt < retries:
                self.log_tech(f"[XÁC NHẬN] Game hơi lag, '{btn_filename}' chưa tắt -> bấm lại lần {attempt + 1}.")
        return False

    def _diagnose_stray_popup(self, screen, rect) -> str:
        """
        AI Bác Sĩ Đọc Lỗi (v10.0): đọc nhanh chữ trên popup bất ngờ để phân biệt
        Hết vàng / Mất kết nối / Popup quảng cáo sự kiện.
        """
        if not self.text_matcher.is_available:
            return "unknown"
        try:
            crop, _, _ = self.vision.crop_roi(screen, rect, [0.24, 0.28, 0.76, 0.68])
            txt_p = normalize_vn(self.text_matcher.read_name_crop(crop))
            if not txt_p:
                return "unknown"
            if any(k in txt_p for k in ("het vang", "khong du vang", "thieu vang")):
                self._trigger_alert_and_pause(
                    "Hết vàng trong game!",
                    "Game thông báo không đủ vàng để lai tiếp. Hãy thu hoạch vàng rồi bấm Tiếp tục.",
                )
                return "out_of_gold"
            if any(k in txt_p for k in ("mat ket noi", "ket noi lai", "thu lai", "mang yeu")):
                self.log("🩺 [Bác sĩ AI] Phát hiện thông báo mất kết nối mạng -> Chờ 3s rồi thử lại...")
                self.input.sleep_interruptible(3.0, self.should_stop)
                return "network"
            if any(k in txt_p for k in ("su kien", "uu dai", "khuyen mai", "thong bao")):
                return "event_popup"
        except Exception:
            pass
        return "unknown"

    def run_preflight_check(self) -> List[Tuple[bool, str, str]]:
        """Kiểm tra nhanh 1 giây trước khi chạy để người mới biết ngay thiếu gì."""
        checks: List[Tuple[bool, str, str]] = []
        if self.vision.is_adb_mode:
            ok_adb = self.adb is not None and self.adb.find_adb_executable() is not None
            checks.append((ok_adb, "Kết nối Chạy Ngầm (ADB)", f"Thiết bị: {self.adb_serial}" if ok_adb else "Chưa tìm thấy adb.exe"))
        else:
            rect = self.vision.get_target_window()
            checks.append((
                rect.found_window,
                "Cửa sổ giả lập",
                f"Đã thấy {rect.title} ({rect.width}×{rect.height})" if rect.found_window else "Chưa mở LDPlayer / BlueStacks / MuMu",
            ))
        p1 = self.config.get("selected_parent_1", "")
        p2 = self.config.get("selected_parent_2", "")
        ok_p = bool(p1 and p2 and (self.parents_dir / p1).is_file() and (self.parents_dir / p2).is_file())
        checks.append((ok_p, "2 rồng bố mẹ", f"{Path(p1).stem} × {Path(p2).stem}" if ok_p else "Chưa chụp/chọn đủ 2 rồng bố mẹ"))
        req_ui = ["btn_egg.png", "btn_start_breed.png", "btn_keep.png", "btn_sell.png"]
        missing_ui = [f for f in req_ui if not self._ui(f).is_file()]
        checks.append((len(missing_ui) == 0, "Bộ nút giao diện game", "Đầy đủ 18 nút mẫu" if not missing_ui else f"Thiếu: {', '.join(missing_ui)}"))
        checks.append((
            self.text_matcher.is_available,
            "Bộ đọc tên 63 rồng & AI tự học",
            f"Sẵn sàng (đã học {self.name_cache.count}/150 mẫu siêu tốc)" if self.text_matcher.is_available else "Chưa cài rapidocr-onnxruntime",
        ))
        return checks

    def _set_state(self, state: BotState) -> None:
        self.stats.current_state = state.value
        self.on_stats(self.stats)

    def _check_bag_full(self, screen=None, rect=None) -> bool:
        bag_path = self._ui("msg_bag_full.png")
        if not bag_path.is_file():
            return False
        res = self.vision.find_template(
            bag_path, threshold=self._conf("bag_full", 0.78), screen_bgr=screen, rect=rect
        )
        if res.found:
            self.log(f"[TÚI ĐẦY] Phát hiện thông báo đầy túi (score={res.score:.2f}).")
            return True
        return False

    def _is_breed_ui_open(self, screen=None, rect=None) -> bool:
        """Kiểm tra bảng Lai Rồng có đang mở không."""
        for fname in ("btn_start_breed.png", "btn_keep.png", "btn_sell.png"):
            p = self._ui(fname)
            if p.is_file() and self.vision.find_template(
                p, threshold=0.72, screen_bgr=screen, rect=rect
            ).found:
                return True
        return False

    def _is_dust_ui_open(self, screen=None, rect=None) -> bool:
        """Kiểm tra bảng Hóa Bụi có đang mở không."""
        p = self._ui("btn_dust.png")
        if p.is_file():
            return self.vision.find_template(p, threshold=0.70, screen_bgr=screen, rect=rect).found
        return False

    def _find_any_close_x(self, screen=None, rect=None) -> MatchResult:
        """Tìm dấu X đóng bảng (hỗ trợ cả mẫu X hóa bụi riêng lẫn mẫu X chung với ngưỡng 0.66)."""
        for fname in ("btn_close_dust_x.png", "btn_close_x.png"):
            p = self._ui(fname)
            if p.is_file():
                res = self.vision.find_template(p, threshold=0.66, screen_bgr=screen, rect=rect)
                if res.found:
                    return res
        return MatchResult(found=False, name="btn_close_x")

    def _has_island_arrows(self, screen=None, rect=None) -> bool:
        """Kiểm tra có đang ở màn hình đảo (thấy mũi tên chuyển đảo > hoặc <) không."""
        nav_thr = self._conf("nav_button", 0.72)
        for fname in ("btn_arrow_left.png", "btn_arrow_right.png"):
            p = self._ui(fname)
            if p.is_file() and self.vision.find_template(p, threshold=nav_thr, screen_bgr=screen, rect=rect).found:
                return True
        return False

    def _recover_from_map_or_subscreen(self, max_clicks: int = 4) -> bool:
        """
        Nếu lỡ bấm nhầm vào Bản Đồ (World Map) hoặc màn hình phụ (mất mũi tên > <):
        tự bấm nút Về Đảo (btn_home_worldmap.png / btn_home_island.png) cho đến khi thấy lại mũi tên > <.
        """
        home_candidates = [
            p for p in (self._ui("btn_home_worldmap.png"), self._ui("btn_home_island.png")) if p.is_file()
        ]
        if not home_candidates:
            return False
        recovered = False
        for attempt in range(1, max_clicks + 1):
            if self.should_stop():
                return recovered
            screen, rect = self.vision.capture_window()
            if self._has_island_arrows(screen, rect):
                if recovered:
                    self.log("🏠 Đã thoát Map và thấy lại mũi tên chuyển đảo > < -> Dò về Đảo Trứng!")
                return recovered
            mh = None
            for hp in home_candidates:
                res_h = self.vision.find_template(hp, threshold=0.72, screen_bgr=screen, rect=rect)
                if res_h.found:
                    mh = res_h
                    break
            if mh is not None and mh.found:
                self.log(f"🏠 Phát hiện đang lạc vào Map / màn hình phụ -> Bấm nút Về Đảo lần {attempt}/{max_clicks}.")
                self.input.click(mh.x, mh.y, post_delay=0.65 * self._spd, stop_check=self.should_stop)
                recovered = True
                self.stats.recoveries += 1
                self.on_stats(self.stats)
            else:
                break
        return recovered

    def _dismiss_stray_popups(self, max_clicks: int = 2) -> None:
        """Đóng các bảng phụ/bảng thông tin rồng lỡ bấm trúng trên đảo."""
        for _ in range(max_clicks):
            if self.should_stop():
                return
            screen, rect = self.vision.capture_window()
            if self._is_breed_ui_open(screen, rect) or self._is_dust_ui_open(screen, rect):
                return
            rx = self._find_any_close_x(screen, rect)
            if rx.found:
                if self.config.get('collect_ai_dataset', False):
                    self.dataset_collector.save_sample(screen, 'stray_popup')
                diag = self._diagnose_stray_popup(screen, rect)
                if diag == "out_of_gold":
                    return
                self.log("🔧 Đóng bảng phụ / thông báo lạ để tiếp tục chạy.")
                self.input.click(rx.x, rx.y, post_delay=0.4 * self._spd, stop_check=self.should_stop)
                self.stats.recoveries += 1
                self.on_stats(self.stats)
            else:
                self._recover_from_map_or_subscreen(max_clicks=4)
                return

    def _click_ui(
        self,
        filename: str,
        timeout: Optional[float] = None,
        threshold: Optional[float] = None,
        retries: int = 2,
        post_delay: Optional[float] = None,
    ) -> Optional[MatchResult]:
        path = self._ui(filename)
        if not path.is_file():
            self.log(f"[CẢNH BÁO] Chưa có ảnh mẫu UI: templates/ui/{filename}")
            return None

        thr = self._conf("button", 0.76) if threshold is None else threshold
        wait_sec = self._timing("ui_wait_timeout", 8.0) if timeout is None else timeout

        for attempt in range(1, retries + 1):
            if not self.wait_if_paused():
                return None
            res = self.vision.wait_for_template(
                path, timeout=wait_sec, threshold=thr, stop_check=self.should_stop
            )
            if res.found:
                self.input.click(res.x, res.y, post_delay=post_delay, stop_check=self.should_stop)
                return res
            if attempt < retries:
                self.log(
                    f"[THỬ LẠI] Chưa thấy '{filename}' (max={res.score:.2f} / {thr:.2f}), thử lần {attempt + 1}..."
                )
        return None

    # ------------------------------------------------------------------
    # Bước 1: Mở bảng Lai Rồng (Chống rồng bay che trứng)
    # ------------------------------------------------------------------
    def _step_open_breed(self) -> BotState:
        self._set_state(BotState.OPEN_BREED)
        screen, rect = self.vision.capture_window()

        if self._check_bag_full(screen, rect):
            return BotState.EXIT_BREED_TO_DUST
        if self._is_breed_ui_open(screen, rect):
            return BotState.SELECT_PARENTS
        if self._is_dust_ui_open(screen, rect):
            self.log("[PHỤC HỒI] Đang ở giao diện Hóa Bụi -> Chuyển sang dọn nguyên liệu.")
            return BotState.DUST_MATERIALS

        # Thử mở trứng tối đa 6 lần (chống rồng trên đảo bay che kín trứng)
        egg_rel = self._pin("egg_rel", [0.49, 0.34])
        spiral_offsets = [(0, 0), (0, 14), (-14, 0), (14, 0), (0, -14), (10, 10)]

        for attempt, (ox, oy) in enumerate(spiral_offsets, 1):
            if not self.wait_if_paused():
                return BotState.OPEN_BREED

            screen, rect = self.vision.capture_window()
            if self._is_breed_ui_open(screen, rect):
                return BotState.SELECT_PARENTS

            # Ưu tiên tìm ảnh mẫu trứng ở lần 1; nếu rồng bay che -> click thẳng tọa độ ghim + xoay nhẹ
            egg_path = self._ui("btn_egg.png")
            clicked_pos = None
            if attempt == 1 and egg_path.is_file():
                res = self.vision.find_template(egg_path, threshold=0.68, screen_bgr=screen, rect=rect)
                if res.found:
                    clicked_pos = (res.x, res.y)
                    # Tự học lại tọa độ ghim tương đối mới nhất
                    self.config.setdefault("pins", {})["egg_rel"] = list(rect.abs_to_rel(res.x, res.y))

            if clicked_pos is None:
                px, py = rect.rel_to_abs(egg_rel[0], egg_rel[1])
                clicked_pos = (px + ox, py + oy)
                self.log(
                    f"[TRỨNG] Rồng có thể đang che trứng -> Click điểm ghim lần {attempt}/6 tại {clicked_pos}."
                )
            else:
                self.log(f"[TRỨNG] Click trứng tại {clicked_pos}.")

            self.input.click(clicked_pos[0], clicked_pos[1], post_delay=0.30 * self._spd, stop_check=self.should_stop)

            # Kiểm tra đã mở đúng bảng Lai Rồng chưa
            chk_scr, chk_rect = self.vision.capture_window()
            if self._is_breed_ui_open(chk_scr, chk_rect):
                return BotState.SELECT_PARENTS

            # Nếu lỡ click trúng con rồng bay ngang làm mở bảng thông tin rồng -> đóng bảng đó rồi thử lại!
            self._dismiss_stray_popups(max_clicks=1)
            self.input.sleep_interruptible(0.35, self.should_stop)

        self.log("[PHỤC HỒI] Không mở được trứng sau 6 lần thử -> Kiểm tra xem có đang lệch đảo không.")
        self.stats.recoveries += 1
        self.on_stats(self.stats)
        return BotState.OPEN_BREED

    # ------------------------------------------------------------------
    # Bước 2: Chọn 2 rồng bố mẹ THÔNG MINH (Quét song song 2 rồng + Kiểm chứng 2 ô trên)
    # ------------------------------------------------------------------
    def _slot_is_empty(self, screen: np.ndarray, rect: WindowRect, slot_roi: List[float]) -> bool:
        emp_p = self._ui("slot_parent_empty.png")
        if emp_p.is_file():
            m = self.vision.find_template(emp_p, threshold=0.68, roi_rel=slot_roi, screen_bgr=screen, rect=rect)
            return m.found
        return False

    def _slot_matches_any_parent(
        self, screen: np.ndarray, rect: WindowRect, slot_roi: List[float], parent_paths: List[Path]
    ) -> float:
        """So sánh hình rồng lõi (cắt bỏ viền/sao) của mẫu bố mẹ với rồng đang nằm trong ô bố mẹ phía trên."""
        slot_crop, _, _ = self.vision.crop_roi(screen, rect, slot_roi)
        if slot_crop is None or slot_crop.size == 0:
            return 1.0
        sh, sw = slot_crop.shape[:2]
        best_sc = 0.0
        for pp in parent_paths:
            tpl = imread_unicode(pp)
            if tpl is None or tpl.size == 0:
                continue
            th, tw = tpl.shape[:2]
            # Lấy vùng lõi chứa thân rồng (bỏ viền & chữ độ hiếm/sao ở góc trên)
            core = tpl[int(th * 0.22):int(th * 0.88), int(tw * 0.14):int(tw * 0.86)]
            if core.shape[0] < 14 or core.shape[1] < 14:
                continue
            for sc in (0.85, 0.95, 1.05, 1.15, 1.25):
                nw, nh = int(core.shape[1] * sc), int(core.shape[0] * sc)
                if nw < 10 or nh < 10 or nw >= sw or nh >= sh:
                    continue
                r = cv2.matchTemplate(slot_crop, cv2.resize(core, (nw, nh)), cv2.TM_CCOEFF_NORMED)
                val = float(r.max())
                if val > best_sc:
                    best_sc = val
        return best_sc

    def _verify_and_fix_parent_slots(
        self, p1_path: Path, p2_path: Path
    ) -> Tuple[bool, bool]:
        """
        Kiểm chứng 2 ô bố mẹ phía trên sau khi bấm chọn:
        - Nếu ô nào chọn NHẦM rồng lạ -> tự bấm vào ô đó để gỡ ra!
        - Trả về (both_valid, had_wrong_dragon).
        """
        slot_left = self._drag_cfg("parent_slot_left_roi", [0.17, 0.25, 0.32, 0.52])
        slot_right = self._drag_cfg("parent_slot_right_roi", [0.41, 0.25, 0.56, 0.52])
        screen, rect = self.vision.capture_window()

        left_empty = self._slot_is_empty(screen, rect, slot_left)
        right_empty = self._slot_is_empty(screen, rect, slot_right)
        if left_empty or right_empty:
            return False, False

        had_wrong = False
        parents = [p1_path, p2_path]
        for idx, (s_roi, center_rel) in enumerate(
            [(slot_left, (0.245, 0.385)), (slot_right, (0.485, 0.385))], 1
        ):
            sc = self._slot_matches_any_parent(screen, rect, s_roi, parents)
            self.log_tech(f"[KIỂM CHỨNG Ô {idx}] Độ khớp lõi rồng = {sc:.2f}")
            if sc < 0.46:
                had_wrong = True
                cx, cy = rect.rel_to_abs(*self.vision.adjust_rel_point(*center_rel))
                self.log(f"⚠️ [KIỂM CHỨNG] Ô bố mẹ {idx} bị chọn nhầm (khớp={sc:.2f}) -> Tự bấm gỡ ra chọn lại!")
                self.input.click(cx, cy, post_delay=0.18 * self._spd, stop_check=self.should_stop)

        return (not had_wrong), had_wrong

    def _select_parents_dual_fast(self, p1_path: Path, p2_path: Path) -> bool:
        """
        Quét ĐỒNG THỜI cả 2 rồng bố mẹ trên từng khung hình:
        - Mở rộng khung quét phủ trọn 4 thẻ rồng phía dưới (không còn cắt rồng bên phải).
        - Kéo nhẹ từng nhịp ngắn (~1.5 thẻ/lần) nên không bao giờ lướt quá rồng.
        - Thấy con nào (hoặc cả 2 con) trên cùng màn hình -> bấm chọn ngay lập tức!
        """
        roi = self._drag_cfg("bottom_bar_roi", [0.15, 0.62, 0.61, 0.89])
        # Kéo ngắn ~18% chiều ngang (vừa qua ~1.5 ô rồng) thay vì kéo dài dễ trượt quá
        start_rel = self._drag_cfg("bottom_drag_start", [0.47, 0.75])
        end_rel = self._drag_cfg("bottom_drag_end", [0.29, 0.75])
        max_scrolls = int(self._drag_cfg("max_parent_scrolls", 12))
        drag_dur = min(0.32, float(self._drag_cfg("drag_duration", 0.30))) * self._spd
        hold_end = float(self._drag_cfg("drag_hold_end", 0.08))
        settle = min(0.28, self._timing("scroll_settle_delay", 0.24)) * self._spd
        # Ngưỡng chặt >= 0.84 chống chọn nhầm rồng cùng màu!
        thr = max(0.84, self._conf("dragon", 0.75))

        need: List[Tuple[str, Path]] = [("Rồng 1", p1_path), ("Rồng 2", p2_path)]
        best_seen: Dict[str, float] = {"Rồng 1": 0.0, "Rồng 2": 0.0}

        def _try_pick_visible(scr: np.ndarray, rct: WindowRect) -> None:
            nonlocal need
            still_need: List[Tuple[str, Path]] = []
            for label, pp in need:
                res = self.vision.find_template(pp, threshold=thr, roi_rel=roi, screen_bgr=scr, rect=rct)
                if res.score > best_seen[label]:
                    best_seen[label] = res.score
                if res.found:
                    self.log(f"⚡ [CHỌN RỒNG] Thấy {label} ({pp.stem}, {res.score:.2f}) -> Click ngay.")
                    self.input.click(res.x, res.y, post_delay=0.14 * self._spd, stop_check=self.should_stop)
                else:
                    still_need.append((label, pp))
            need = still_need

        # Kiểm tra ngay trên màn hình hiện tại (0ms kéo!)
        scr0, rect0 = self.vision.capture_window()
        _try_pick_visible(scr0, rect0)
        if not need:
            return True

        half = max(3, max_scrolls // 2)
        directions = [
            ("kéo sang phải", start_rel, end_rel, half),
            ("kéo ngược sang trái", end_rel, start_rel, max_scrolls),
        ]

        prev_scr, prev_rect = scr0, rect0
        for dir_name, p_from, p_to, limit in directions:
            stuck_count = 0
            for step in range(1, limit + 1):
                if not need:
                    return True
                if not self.wait_if_paused():
                    return False
                if self._check_bag_full(prev_scr, prev_rect):
                    return False

                before_roi, _, _ = self.vision.crop_roi(prev_scr, prev_rect, roi)
                x1, y1 = prev_rect.rel_to_abs(*self.vision.adjust_rel_point(p_from[0], p_from[1]))
                x2, y2 = prev_rect.rel_to_abs(*self.vision.adjust_rel_point(p_to[0], p_to[1]))
                rem_lbl = " & ".join(lb for lb, _ in need)
                self.log_tech(f"[THANH RỒNG] {dir_name} ({step}/{limit}) tìm {rem_lbl}...")
                self.input.smooth_drag(
                    x1, y1, x2, y2, duration=drag_dur, hold_end=hold_end, stop_check=self.should_stop
                )
                self.input.sleep_interruptible(settle, self.should_stop)

                after_scr, after_rect = self.vision.capture_window()
                after_roi, _, _ = self.vision.crop_roi(after_scr, after_rect, roi)
                _try_pick_visible(after_scr, after_rect)
                prev_scr, prev_rect = after_scr, after_rect
                if not need:
                    return True

                if compute_frame_diff(before_roi, after_roi) < 2.2:
                    stuck_count += 1
                    if stuck_count >= 2:
                        break
                else:
                    stuck_count = 0

        for label, pp in need:
            self.log(f"[CẢNH BÁO] Chưa thấy {label} ({pp.stem}) (max={best_seen[label]:.2f} / {thr:.2f}).")
        return False

    def _step_select_parents(self) -> BotState:
        self._set_state(BotState.SELECT_PARENTS)
        if self._check_bag_full():
            return BotState.EXIT_BREED_TO_DUST

        p1 = self.config.get("selected_parent_1", "")
        p2 = self.config.get("selected_parent_2", "")
        if not p1 or not p2:
            self.log("[LỖI] Bạn chưa chọn đủ 2 rồng bố mẹ ở Bảng Điều Khiển!")
            self.stop()
            return BotState.OPEN_BREED

        p1_path = self.parents_dir / p1
        p2_path = self.parents_dir / p2
        if not p1_path.is_file() or not p2_path.is_file():
            self.log(f"[LỖI] Thiếu ảnh mẫu rồng bố mẹ trong templates/parents/ ({p1}, {p2})")
            self.stop()
            return BotState.OPEN_BREED

        # Thử tối đa 2 vòng nếu vòng 1 phát hiện chọn nhầm và tự gỡ ra
        for attempt in range(1, 3):
            if not self._select_parents_dual_fast(p1_path, p2_path):
                return BotState.EXIT_BREED_TO_DUST if self._check_bag_full() else BotState.OPEN_BREED
            ok_slots, had_wrong = self._verify_and_fix_parent_slots(p1_path, p2_path)
            if ok_slots:
                break
            if attempt == 2:
                self.log("[PHỤC HỒI] Không xác minh được đủ 2 ô bố mẹ -> Mở lại bảng Lai.")
                return BotState.OPEN_BREED

        sb_path = self._ui("btn_start_breed.png")
        if sb_path.is_file():
            sb_roi = self.calibrator.get_roi(self._mode_key, "btn_start_breed.png") or [0.38, 0.48, 0.58, 0.65]
            res = self.vision.wait_for_template(
                sb_path, timeout=2.0, threshold=self._conf("button", 0.76), roi_rel=sb_roi, stop_check=self.should_stop
            )
            if not res.found:
                res = self.vision.find_template(sb_path, threshold=self._conf("button", 0.76))
            if res.found:
                self.log_tech(f"[LAI RỒNG] Click 'Lai Rồng' ({res.x}, {res.y}).")
                self.input.click(res.x, res.y, post_delay=0.10, stop_check=self.should_stop)

        self._consecutive_failures = 0
        return BotState.WAIT_BREED_RESULT

    # ------------------------------------------------------------------
    # Bước 3 & 4: Đợi lai & BẮT TỨC THÌ Baby + Tên -> Cất / Bán / Chúc phúc
    # ------------------------------------------------------------------
    def _classify_result_dragon(
        self, screen, rect
    ) -> Tuple[Optional[MatchResult], Optional[MatchResult], List[MatchResult]]:
        """
        Tách riêng 2 nhóm ảnh mẫu để KHÔNG BAO GIỜ để ảnh Baby (dính vòng tròn vàng)
        cướp quyền của ảnh 'ten_' hoặc danh sách TÊN RỒNG nhập tay (OCR):
        - Trả về (best_name_img, best_baby_img, top3_all)
        """
        result_roi = self._drag_cfg("result_roi", [0.28, 0.22, 0.78, 0.55])
        ranked = self.vision.rank_across_folders(
            category_folders={
                "bless": self.bless_dir,
                "keep": self.keep_dir,
                "sell": self.sell_dir,
            },
            roi_rel=result_roi,
            screen_bgr=screen,
            rect=rect,
        )

        thr_name = self._conf("dragon_name", 0.85)
        # Ảnh Baby chụp dính vòng tròn vàng thường đạt 0.84-0.88 trên rồng khác -> siết ngưỡng >= 0.90
        thr_baby = max(0.90, self._conf("dragon", 0.75))

        best_name_img: Optional[MatchResult] = None
        best_baby_img: Optional[MatchResult] = None

        for m in ranked:
            is_name_tmpl = m.name.lower().startswith("ten_")
            if is_name_tmpl:
                if best_name_img is None and m.score >= thr_name:
                    m.found = True
                    best_name_img = m
            else:
                if best_baby_img is None and m.score >= thr_baby:
                    m.found = True
                    best_baby_img = m

        return best_name_img, best_baby_img, ranked[:3]

    def _step_wait_breed_result(self) -> BotState:
        self._set_state(BotState.WAIT_BREED_RESULT)
        timeout = self._timing("breed_wait_timeout", 45.0)
        btn_thr = self._conf("button", 0.76)
        deadline = time.monotonic() + timeout
        result_buttons = ["btn_keep.png", "btn_bless.png", "btn_sell.png"]
        # Quét đúng dải nút kết quả thay vì quét toàn màn hình -> nhanh gấp ~12 lần!
        btns_band_roi = self._drag_cfg("result_buttons_roi", [0.18, 0.44, 0.60, 0.68])

        while time.monotonic() < deadline:
            if not self.wait_if_paused():
                return BotState.OPEN_BREED

            screen, rect = self.vision.capture_window()
            if self._check_bag_full(screen, rect):
                return BotState.EXIT_BREED_TO_DUST

            found_btns: Dict[str, Tuple[int, int]] = {}
            for bname in result_buttons:
                bp = self._ui(bname)
                if bp.is_file():
                    b_roi = self.calibrator.get_roi(self._mode_key, bname) or btns_band_roi
                    m_btn = self.vision.find_template(
                        bp, threshold=btn_thr, roi_rel=b_roi, screen_bgr=screen, rect=rect
                    )
                    if m_btn.found:
                        found_btns[bname] = (m_btn.x, m_btn.y)
                        self.calibrator.record(self._mode_key, bname, m_btn.x, m_btn.y, rect)

            if len(found_btns) >= 2 or "btn_keep.png" in found_btns:
                self._cached_result_btns = found_btns
                self._cached_result_frame = (screen, rect)
                self.stats.breeds += 1
                self.on_stats(self.stats)
                return BotState.CLASSIFY_AND_ACT

            time.sleep(0.035)

        self.log("[PHỤC HỒI] Chờ kết quả quá lâu -> Kiểm tra lại nút 'Lai Rồng' hoặc trạng thái bảng.")
        self.stats.recoveries += 1
        self.on_stats(self.stats)
        return BotState.OPEN_BREED

    def _has_custom_result_templates(self) -> bool:
        for d in (self.bless_dir, self.keep_dir, self.sell_dir):
            if d.is_dir() and any(d.glob("*.png")):
                return True
        return False

    def _step_classify_and_act(self) -> BotState:
        self._set_state(BotState.CLASSIFY_AND_ACT)
        t_start = time.perf_counter()
        if self._cached_result_frame is not None:
            screen, rect = self._cached_result_frame
            self._cached_result_frame = None
        else:
            screen, rect = self.vision.capture_window()
        if self._check_bag_full(screen, rect):
            return BotState.EXIT_BREED_TO_DUST

        action = None
        self._last_identified = ("", "", None)
        name_roi = self._drag_cfg("result_name_roi", [0.57, 0.285, 0.78, 0.348])
        res_key = f"{self._mode_key}_{rect.width}x{rect.height}"
        text_rules = self.config.get("dragon_text_rules", {})
        has_rules = any(bool(text_rules.get(k)) for k in ("bless", "keep", "sell"))
        name_crop = None

        # ƯU TIÊN 1 (SIÊU TỐC < 1.5ms): AI Bộ Nhớ Tự Học Vĩnh Viễn — KIỂM TRA TRƯỚC TIÊN, KHÔNG CHỜ QUÉT THƯ MỤC!
        if has_rules and self.name_cache.count > 0:
            name_crop, _, _ = self.vision.crop_roi(screen, rect, name_roi)
            mem_hit = self.name_cache.match(name_crop, res_key, threshold=0.89)
            if mem_hit is not None:
                c_name, c_rar, c_stars, c_sc = mem_hit
                self._last_identified = (c_name, c_rar, c_stars)
                synth_txt = f"{c_name} ({c_stars} sao)" if c_stars else c_name
                tm_mem = self.text_matcher.match_text_against_rules(synth_txt, text_rules)
                if tm_mem.found:
                    action = tm_mem.category
                    dt_ms = (time.perf_counter() - t_start) * 1000.0
                    act_vn = {"bless": "Chúc phúc & Cất", "keep": "Cất túi", "sell": "Bán ngay"}[action]
                    st_str = f" ({c_stars}★)" if c_stars else ""
                    self.log(f"⚡ Lai ra: {c_name}{st_str} [{c_rar}] → {act_vn} ({dt_ms:.0f}ms)")
                    self.log_tech(f"[AI CACHE HIT] '{c_name}' score={c_sc:.2f} ({dt_ms:.1f}ms) -> {action}")

        # ƯU TIÊN 2: Ảnh mẫu tự chụp (chỉ quét nếu thư mục thực sự có ảnh .png!)
        best_name_img, best_baby_img = None, None
        if action is None and self._has_custom_result_templates():
            best_name_img, best_baby_img, top3 = self._classify_result_dragon(screen, rect)
            if top3:
                top_str = " | ".join(f"{m.name}[{m.category}]={m.score:.2f}" for m in top3)
                self.log_tech(f"[NHẬN DIỆN] Top ảnh: {top_str}")
            if best_name_img is not None:
                action = best_name_img.category
                self.log(
                    f"[QUYẾT ĐỊNH - ẢNH TÊN] '{best_name_img.name}' -> [{action.upper()}] ({best_name_img.score:.2f})."
                )

        # ƯU TIÊN 2B: Đọc dòng Tên+Sao bằng OCR -> tự học vĩnh viễn vào PersistentNameCache!
        if action is None and has_rules and self.config.get("enable_text_ocr", True):
            if name_crop is None:
                name_crop, _, _ = self.vision.crop_roi(screen, rect, name_roi)
            ocr_raw = self.text_matcher.read_name_crop(name_crop)
            if ocr_raw:
                adapted = self.ocr_dict.lookup(ocr_raw)
                effective_ocr = f"{adapted} ({ocr_raw.split('(', 1)[1]}" if (adapted and "(" in ocr_raw) else (adapted or ocr_raw)
                tm = self.text_matcher.match_text_against_rules(effective_ocr, text_rules)
                self._last_identified = (tm.identified_name, tm.identified_rarity, tm.stars)
                self.log_tech(f"[ĐỌC TÊN] '{ocr_raw}' -> {tm.identified_name} [{tm.identified_rarity}, {tm.stars}★] | Luật: '{tm.rule_text}' ({tm.score:.2f})")
                if tm.identified_name and tm.score >= 0.86:
                    self.ocr_dict.learn(ocr_raw, tm.identified_name)
                if tm.identified_name and tm.score >= 0.88 and name_crop is not None and name_crop.size > 0:
                    self.name_cache.learn(name_crop, res_key, tm.identified_name, tm.identified_rarity, tm.stars)
                if self.config.get("collect_ai_dataset", False) and tm.score < 0.85:
                    self.dataset_collector.save_sample(screen, "low_ocr")
                if tm.found:
                    action = tm.category
                    act_vn = {"bless": "Chúc phúc & Cất", "keep": "Cất túi", "sell": "Bán ngay"}[action]
                    st_str = f" ({tm.stars}★)" if tm.stars else ""
                    self.log(f"🐉 Lai ra: {tm.identified_name or tm.rule_text}{st_str} [{tm.identified_rarity or '?'}] → {act_vn}")
            elif not self.text_matcher.is_available:
                self.log("[LỖI OCR] Chưa cài thư viện rapidocr-onnxruntime! Hãy chạy install_and_run.bat.")
            else:
                self.log_tech("[ĐỌC TÊN] Chưa đọc thấy chữ ở vùng tên phải.")

        # ƯU TIÊN 3: Ảnh Baby dự phòng (ngưỡng chặt >= 0.90)
        if action is None and best_baby_img is not None:
            action = best_baby_img.category
            self.log(
                f"[QUYẾT ĐỊNH - BABY] '{best_baby_img.name}' -> [{action.upper()}] ({best_baby_img.score:.2f})."
            )

        if action is None:
            action = self.config.get("unknown_dragon_action", "keep")
            self.log(f"[RỒNG LẠ] Không có trong danh sách -> Mặc định: [{action.upper()}].")
            if action == "pause":
                self.log("[TẠM DỪNG] Đã dừng để bạn chụp thêm mẫu Tên+Sao/Baby cho rồng mới.")
                self.stop()
                return BotState.CLASSIFY_AND_ACT

        if action == "keep":
            cached_k = self._cached_result_btns.get("btn_keep.png")
            if cached_k is not None:
                self.input.click(cached_k[0], cached_k[1], post_delay=0.14 * self._spd, stop_check=self.should_stop)
                self.stats.kept += 1
            else:
                res_k = self._click_ui("btn_keep.png", timeout=4.0, post_delay=0.16 * self._spd)
                if res_k and res_k.found:
                    self.stats.kept += 1

        elif action == "sell":
            cached_s = self._cached_result_btns.get("btn_sell.png")
            clicked_s = False
            if cached_s is not None:
                self.input.click(cached_s[0], cached_s[1], post_delay=0.14 * self._spd, stop_check=self.should_stop)
                clicked_s = True
            else:
                res_s = self._click_ui("btn_sell.png", timeout=4.0, post_delay=0.14 * self._spd)
                clicked_s = bool(res_s and res_s.found)
            if clicked_s:
                res_cf = self._click_ui("btn_sell_confirm.png", timeout=4.5, retries=2, post_delay=0.12 * self._spd)
                if res_cf and res_cf.found:
                    self.stats.sold += 1

        elif action == "bless":
            cached_b = self._cached_result_btns.get("btn_bless.png")
            clicked_b = False
            if cached_b is not None:
                self.input.click(cached_b[0], cached_b[1], post_delay=0.25, stop_check=self.should_stop)
                clicked_b = True
                self.log(f"[CHÚC PHÚC] Click ngay Chúc phúc tại {cached_b} -> Chờ hiệu ứng 2-3s xong rồi Cất...")
            else:
                bless_thr = min(0.72, self._conf("button", 0.76))
                res_b = self._click_ui(
                    "btn_bless.png", timeout=5.0, threshold=bless_thr, retries=3, post_delay=0.25
                )
                clicked_b = bool(res_b and res_b.found)
                if clicked_b:
                    self.log(f"[CHÚC PHÚC] Đã bấm Chúc phúc ({res_b.score:.2f}) -> Chờ hiệu ứng 2-3s xong rồi Cất...")
            if clicked_b:
                bless_timeout = self._timing("bless_wait_timeout", 15.0)
                # Trong lúc chúc phúc (2-3s), toàn bộ nút ẩn đi -> chờ 2.0s rồi đợi nút Cất rồng hiện lại
                self.input.sleep_interruptible(2.0, self.should_stop)
                after_btn = (
                    "btn_keep_after_bless.png"
                    if self._ui("btn_keep_after_bless.png").is_file()
                    else "btn_keep.png"
                )
                res_k2 = self._click_ui(after_btn, timeout=bless_timeout, retries=3, post_delay=0.45 * self._spd)
                if res_k2 and res_k2.found:
                    self.log("[CHÚC PHÚC] Chúc phúc (+1 sao) & Đã cất rồng thành công!")
                self.stats.blessed += 1
            else:
                self.log("[LỖI] Không thấy nút Chúc phúc (btn_bless.png)! Hãy chụp lại ở mục Cài Đặt Vùng & Nút.")

        # Ghi nhận vào Sổ Tay Rồng Vừa Lai (chỉ ghi con Cất thuộc độ hiếm cho phép Hóa Bụi; TUYỆT ĐỐI không ghi con Chúc phúc!)
        id_name, id_rarity, id_stars = getattr(self, "_last_identified", ("", "", None))
        if action == "keep" and id_rarity and id_stars is not None:
            enabled_dr = set(self.config.get("dust_rarities", ["Nguyên Liệu"]))
            max_st = int(self.config.get("dust_max_star", 0) or 0)
            if id_rarity in enabled_dr and (max_st == 0 or id_stars <= max_st):
                lkey = (id_rarity, int(id_stars))
                self._session_dust_ledger[lkey] = self._session_dust_ledger.get(lkey, 0) + 1
                self.log_tech(f"[SỔ HÓA BỤI] +1 ({id_rarity}, {id_stars}★) -> tổng chờ bụi: {sum(self._session_dust_ledger.values())}")
        try:
            self.pair_stats.record_breed(
                self.config.get("selected_parent_1", ""),
                self.config.get("selected_parent_2", ""),
                id_rarity,
                action,
            )
        except Exception:
            pass
        if id_rarity in ("Hiếm", "Cực hiếm", "Event") or action == "bless":
            if id_rarity == "Hiếm":
                self.stats.rare_hits += 1
            elif id_rarity == "Cực hiếm":
                self.stats.epic_hits += 1
            elif id_rarity == "Event":
                self.stats.event_hits += 1
            self.stats.recent_drops.insert(0, {
                "name": id_name or "Rồng Chúc Phúc",
                "rarity": id_rarity or "Quý",
                "stars": id_stars,
                "action": action,
                "time": time.strftime("%H:%M:%S"),
            })
            if len(self.stats.recent_drops) > 30:
                self.stats.recent_drops.pop()
            if self.config.get("save_rare_snapshots", True) and (id_rarity in ("Cực hiếm", "Event") or action == "bless"):
                self.rare_saver.save_shot(screen, id_name or "Rong_Quy", id_stars)

        self.on_stats(self.stats)
        self._check_auto_stop_conditions(id_name, id_rarity, id_stars)
        if self.is_paused or self.should_stop():
            return BotState.SELECT_PARENTS

        self.input.sleep_interruptible(0.12 * self._spd, self.should_stop)
        if self._check_bag_full():
            return BotState.EXIT_BREED_TO_DUST

        return BotState.SELECT_PARENTS

    def _check_auto_stop_conditions(self, id_name: str, id_rarity: str, id_stars: Optional[int]) -> None:
        cfg_stop = self.config.get("auto_stop", {})
        if not cfg_stop.get("enabled", False):
            return
        max_mins = float(cfg_stop.get("max_minutes", 0) or 0)
        if max_mins > 0 and self.stats.started_at > 0:
            elapsed = (time.monotonic() - self.stats.started_at) / 60.0
            if elapsed >= max_mins:
                self._trigger_alert_and_pause("Đã hết giờ hẹn!", f"Tool đã chạy đủ {int(max_mins)} phút ({self.stats.breeds} lượt lai).")
                return
        max_breeds = int(cfg_stop.get("max_breeds", 0) or 0)
        if max_breeds > 0 and self.stats.breeds >= max_breeds:
            self._trigger_alert_and_pause("Đã đạt giới hạn lượt lai!", f"Tool đã hoàn thành {self.stats.breeds}/{max_breeds} lượt lai.")
            return
        rare_target = int(cfg_stop.get("stop_on_rare_count", 0) or 0)
        total_rare_plus = self.stats.rare_hits + self.stats.epic_hits + self.stats.event_hits
        if rare_target > 0 and total_rare_plus >= rare_target:
            self._trigger_alert_and_pause(
                "Đã nổ đủ rồng mục tiêu!",
                f"Đã lai ra {total_rare_plus} rồng Hiếm/Cực hiếm/Event (mới nhất: {id_name} {id_stars or ''} sao).",
            )
            return
        if cfg_stop.get("stop_on_epic_or_event", False) and id_rarity in ("Cực hiếm", "Event"):
            self._trigger_alert_and_pause(
                f"Nổ rồng {id_rarity}!",
                f"Vừa lai ra {id_name} ({id_stars or '?'} sao) — tool đã tự tạm dừng để bạn kiểm tra!",
            )

    # ------------------------------------------------------------------
    # Bước 5, 6, 7: Đóng bảng lai -> Chuyển 2 đảo -> Mở Tháp Tẩy Tủy (Chống rồng che!)
    # ------------------------------------------------------------------
    def _step_exit_breed_to_dust(self) -> BotState:
        if self.is_breed_only:
            self._trigger_alert_and_pause(
                "Túi rồng đã đầy!",
                "Chế độ Chỉ Auto Lai đã tự tạm dừng an toàn để không hóa bụi nhầm rồng của bạn.",
            )
            return BotState.OPEN_BREED
        self._set_state(BotState.EXIT_BREED_TO_DUST)


        # Bấm dấu X và xác nhận bảng Lai đã đóng hẳn
        for attempt in range(1, 5):
            screen, rect = self.vision.capture_window()
            rx = self._find_any_close_x(screen, rect)
            if rx.found:
                self.log(f"[ĐÓNG LAI] Click dấu X lần {attempt} tại ({rx.x}, {rx.y}).")
                self.input.click(rx.x, rx.y, post_delay=0.45 * self._spd, stop_check=self.should_stop)
            if not self._is_breed_ui_open():
                break

        return BotState.SWITCH_TO_DUST_ISLAND

    def _step_switch_to_dust_island(self) -> BotState:
        self._set_state(BotState.SWITCH_TO_DUST_ISLAND)
        hops = int(self.config.get("island_hops", 2))
        nav_thr = self._conf("nav_button", 0.72)
        switch_delay = self._timing("after_island_switch_delay", 0.80)
        for i in range(1, hops + 1):
            if not self.wait_if_paused():
                return BotState.OPEN_BREED
            self.log(f"[CHUYỂN ĐẢO] Bấm > lần {i}/{hops}...")
            self._click_ui("btn_arrow_right.png", timeout=6.0, threshold=nav_thr, retries=3, post_delay=switch_delay)
        return BotState.OPEN_DUST_UI

    def _step_open_dust_ui(self) -> BotState:
        self._set_state(BotState.OPEN_DUST_UI)
        altar_rel = self._pin("altar_rel", self._pin("egg_rel", [0.49, 0.34]))
        # Ưu tiên chân tháp (+22px, +36px) vì rồng thường bay che phần giữa/trên tháp!
        smart_offsets = [
            (0, 0), (0, 24), (0, 38), (-24, 14), (24, 14),
            (0, -20), (-18, 30), (18, 30), (-30, 0), (30, 0),
        ]

        bottle_path = self._ui("btn_dust_bottle.png")
        altar_path = self._ui("btn_altar_center.png")

        for attempt, (ox, oy) in enumerate(smart_offsets, 1):
            if not self.wait_if_paused():
                return BotState.OPEN_BREED

            screen, rect = self.vision.capture_window()
            if self._is_dust_ui_open(screen, rect):
                return BotState.DUST_MATERIALS

            # Nếu bảng Tẩy Tủy đã mở sẵn -> click ngay bình Hóa Bụi
            if bottle_path.is_file():
                rb = self.vision.find_template(bottle_path, threshold=0.70, screen_bgr=screen, rect=rect)
                if rb.found:
                    self.log(f"[HÓA BỤI] Thấy bình Hóa Bụi ({rb.score:.2f}) -> Click mở.")
                    self.input.click(rb.x, rb.y, post_delay=0.45 * self._spd, stop_check=self.should_stop)
                    return BotState.DUST_MATERIALS

            # Tìm ảnh tháp nếu không bị rồng che; nếu bị rồng che -> click điểm ghim + dịch xuống chân tháp
            clicked_pos = None
            if attempt in (1, 5) and altar_path.is_file():
                ra = self.vision.find_template(altar_path, threshold=0.68, screen_bgr=screen, rect=rect)
                if ra.found:
                    clicked_pos = (ra.x, ra.y)
                    self.config.setdefault("pins", {})["altar_rel"] = list(rect.abs_to_rel(ra.x, ra.y))
                    self.log(f"[TẨY TỦY] Thấy tháp tẩy tủy ({ra.score:.2f}) -> Click tại {clicked_pos}.")

            if clicked_pos is None:
                px, py = rect.rel_to_abs(altar_rel[0], altar_rel[1])
                clicked_pos = (px + ox, py + oy)
                self.log(
                    f"[CHẨN ĐOÁN] Tháp đang bị rồng che -> Thử chạm chân/mép tháp lần {attempt}/{len(smart_offsets)} tại {clicked_pos}."
                )

            self.input.click(clicked_pos[0], clicked_pos[1], post_delay=0.45 * self._spd, stop_check=self.should_stop)

            # Kiểm tra nhanh trong 1.0s (không đứng chờ 2.5s vô ích!)
            if bottle_path.is_file():
                rb2 = self.vision.wait_for_template(
                    bottle_path, timeout=1.0, threshold=0.70, stop_check=self.should_stop
                )
                if rb2.found:
                    self.log(f"[HÓA BỤI] Đã mở bảng Tẩy Tủy -> Click bình Hóa Bụi ({rb2.x}, {rb2.y}).")
                    for b_try in range(1, 4):
                        self.input.click(rb2.x, rb2.y, post_delay=0.55 * self._spd, stop_check=self.should_stop)
                        if self.vision.wait_for_template(self._ui("btn_dust.png"), timeout=2.0, threshold=0.70, stop_check=self.should_stop).found:
                            self.log("[HÓA BỤI] Đã xác nhận vào đúng tab Hóa Bụi.")
                            return BotState.DUST_MATERIALS
                        self.log(f"[CHẨN ĐOÁN] Game lag chưa chuyển sang tab Hóa Bụi -> Bấm lại bình lần {b_try + 1}/3...")

            # Chẩn đoán nguyên nhân: có bật nhầm bảng thông tin rồng (có dấu X) không?
            chk_scr, chk_rect = self.vision.capture_window()
            rx = self._find_any_close_x(chk_scr, chk_rect)
            if rx.found and not self._is_dust_ui_open(chk_scr, chk_rect):
                self.log("[CHẨN ĐOÁN] Click trúng con rồng bay ngang làm bật bảng thông tin -> Đóng bảng & bấm lại tháp!")
                self.input.click(rx.x, rx.y, post_delay=0.35 * self._spd, stop_check=self.should_stop)
                self.stats.recoveries += 1
                self.on_stats(self.stats)
            else:
                # Rồng đang đậu đúng điểm chạm -> chờ ngắn 0.8s cho rồng bay lệch ra rồi thử góc khác
                self.log("[CHẨN ĐOÁN] Chưa mở được tháp (rồng đang che) -> Đợi 0.8s cho rồng bay lệch rồi thử điểm tiếp theo.")
                self.input.sleep_interruptible(0.8, self.should_stop)

        self.log("[PHỤC HỒI] Thử 10 điểm quanh tháp vẫn bị che kín -> Đợi 2s rồi thử mở lại tháp (không quay về đảo lai khi túi còn đầy).")
        self.input.sleep_interruptible(2.0, self.should_stop)
        self.stats.recoveries += 1
        self.on_stats(self.stats)
        return BotState.OPEN_DUST_UI

    # ------------------------------------------------------------------
    # Bước 8: Quét đủ 2 cột Hóa Bụi & tự phát hiện chạm đáy
    # ------------------------------------------------------------------
    def _get_padded_dust_roi(self) -> List[float]:
        raw = self._drag_cfg("dust_list_roi", [0.62, 0.18, 0.92, 0.86])
        return [
            max(0.0, float(raw[0]) - 0.02),
            max(0.0, float(raw[1]) - 0.01),
            min(1.0, float(raw[2]) + 0.015),
            min(1.0, float(raw[3])),
        ]

    def _is_card_locked(self, screen, rect, badge_x: int, badge_y: int, badge_w: int = 0, badge_h: int = 0) -> bool:
        """Kiểm tra góc dưới-trái của đúng thẻ rồng này có biểu tượng ổ khóa trắng không."""
        lock_tpl = self._ui("icon_dragon_locked.png")
        if not lock_tpl.is_file():
            return False
        sx = rect.width / 1330.0
        sy = rect.height / 761.0
        lx = badge_x - rect.left
        ly = badge_y - rect.top
        half_w = max(int(badge_w * 0.62), int(48 * sx))
        x1 = max(0, int(lx - half_w))
        x2 = min(rect.width, int(lx + max(int(badge_w * 0.15), int(45 * sx))))
        y1 = max(0, int(ly - int(badge_h * 0.10)))
        y2 = min(rect.height, int(ly + max(int(badge_h * 0.85), int(105 * sy))))
        if x2 <= x1 + 10 or y2 <= y1 + 10:
            return False
        sub = screen[y1:y2, x1:x2]
        tpl_img = self.vision._load_template(lock_tpl, target_rect=rect)
        if tpl_img is None or sub.shape[0] < tpl_img.shape[0] or sub.shape[1] < tpl_img.shape[1]:
            return False
        res = cv2.matchTemplate(sub, tpl_img, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, _ = cv2.minMaxLoc(res)
        return float(max_val) >= 0.72

    def _read_card_star(self, screen, rect, badge_x: int, badge_y: int, badge_w: int = 60, badge_h: int = 40) -> Optional[int]:
        """Đọc chính xác số sao góc trên-phải thẻ rồng (tính từ góc trên-trái thẻ -> chuẩn cho cả 5 độ hiếm!)."""
        if not self.text_matcher.is_available:
            return None
        sx = rect.width / 1330.0
        sy = rect.height / 761.0
        # Quy về góc trên-trái của thẻ rồng bất kể mẫu cắt rộng hay hẹp
        card_left = (badge_x - rect.left) - max(badge_w // 2, int(28 * sx))
        card_top = (badge_y - rect.top) - max(badge_h // 2, int(18 * sy))
        x1 = max(0, int(card_left + 88 * sx))
        x2 = min(rect.width, int(card_left + 148 * sx))
        y1 = max(0, int(card_top - 4 * sy))
        y2 = min(rect.height, int(card_top + 42 * sy))
        if x2 <= x1 + 10 or y2 <= y1 + 10:
            return None
        sub = screen[y1:y2, x1:x2]
        raw = self.text_matcher.read_name_crop(sub)
        if not raw:
            return None
        digits = "".join(ch for ch in raw if ch.isdigit())
        if digits:
            val = int(digits[:2])
            if 1 <= val <= 40:
                return val
        return None

    def _is_card_protected(self, screen, rect, badge_x: int, badge_y: int, badge_w: int = 60, badge_h: int = 40) -> bool:
        """Kiểm tra thẻ rồng có khớp với ảnh nào trong thư mục templates/protect_dust (Cấm Hóa Bụi) không."""
        if not self.protect_dust_dir.is_dir():
            return False
        prot_files = [p for p in self.protect_dust_dir.iterdir() if p.suffix.lower() in {".png", ".jpg", ".jpeg"}]
        if not prot_files:
            return False
        sx = rect.width / 1330.0
        sy = rect.height / 761.0
        card_left = max(0, (badge_x - rect.left) - max(badge_w // 2, int(28 * sx)))
        card_top = max(0, (badge_y - rect.top) - max(badge_h // 2, int(18 * sy)))
        x2 = min(rect.width, int(card_left + 175 * sx))
        y2 = min(rect.height, int(card_top + 105 * sy))
        sub = screen[card_top:y2, card_left:x2]
        if sub.size == 0:
            return False
        for pf in prot_files:
            tpl = self.vision._load_template(pf, target_rect=rect)
            if tpl is None or sub.shape[0] < tpl.shape[0] or sub.shape[1] < tpl.shape[1]:
                continue
            res = cv2.matchTemplate(sub, tpl, cv2.TM_CCOEFF_NORMED)
            _, mv, _, _ = cv2.minMaxLoc(res)
            if float(mv) >= 0.88:
                return True
        return False

    DUST_RARITY_TEMPLATES = {
        "Phổ biến": "dust_badge_pho_bien.png",
        "Nguyên Liệu": "dragon_nguyen_lieu.png",
        "Hiếm": "dust_badge_hiem.png",
        "Cực hiếm": "dust_badge_cuc_hiem.png",
        "Event": "dust_badge_event.png",
    }

    def _find_all_dust_candidates(self, screen, rect) -> List[MatchResult]:
        """Quét TẤT CẢ thẻ thuộc các độ hiếm được phép Hóa Bụi trên cả 2 cột trái & phải (có khử trùng NMS)."""
        roi = self._get_padded_dust_roi()
        thr = self._conf("nguyen_lieu", 0.70)
        enabled_rarities = self.config.get("dust_rarities", ["Nguyên Liệu"]) or ["Nguyên Liệu"]
        results: List[MatchResult] = []
        picked: List[Tuple[int, int]] = []
        crop, ox, oy = self.vision.crop_roi(screen, rect, roi)

        for rar_name in enabled_rarities:
            fname = self.DUST_RARITY_TEMPLATES.get(rar_name)
            if not fname:
                continue
            target_p = self._ui(fname)
            if not target_p.is_file():
                continue
            tpl_img = self.vision._load_template(target_p, target_rect=rect)
            if tpl_img is None or crop.shape[0] < tpl_img.shape[0] or crop.shape[1] < tpl_img.shape[1]:
                continue
            th, tw = tpl_img.shape[:2]
            badge_thr = thr if rar_name == "Nguyên Liệu" else max(0.78, thr)
            res_map = cv2.matchTemplate(crop, tpl_img, cv2.TM_CCOEFF_NORMED)
            ys, xs = np.where(res_map >= badge_thr)
            cands = sorted(
                [(float(res_map[y, x]), int(x), int(y)) for y, x in zip(ys, xs)],
                reverse=True,
            )
            for sc, px, py in cands:
                cx, cy = ox + px + tw // 2, oy + py + th // 2
                if any(abs(cx - ex) < 42 and abs(cy - ey) < 42 for ex, ey in picked):
                    continue
                picked.append((cx, cy))
                results.append(MatchResult(found=True, name=rar_name, score=sc, x=cx, y=cy, w=tw, h=th))

        results.sort(key=lambda m: (m.y // 40, m.x))

        if not results and self.dust_targets_dir.is_dir():
            best = self.vision.find_best_across_folders(
                {"dust": self.dust_targets_dir}, threshold=thr, roi_rel=roi, screen_bgr=screen, rect=rect
            )
            if best.found:
                results.append(best)
        return results

    def _step_dust_materials(self) -> BotState:
        self._set_state(BotState.DUST_MATERIALS)
        roi = self._get_padded_dust_roi()
        start_rel = self._drag_cfg("dust_drag_start", [0.77, 0.74])
        end_rel = self._drag_cfg("dust_drag_end", [0.77, 0.28])
        max_scrolls = int(self._drag_cfg("max_dust_scrolls", 14))
        drag_dur = float(self._drag_cfg("drag_duration", 0.38)) * self._spd
        hold_end = float(self._drag_cfg("drag_hold_end", 0.12))
        settle = self._timing("scroll_settle_delay", 0.50)
        dust_max_star = int(self.config.get("dust_max_star", 0) or 0)
        only_session = bool(self.config.get("dust_only_session_bred", True))
        # Chỉ dùng Sổ Tay khi trong phiên đã thực sự lai ra ít nhất 1 con chờ hóa bụi (nếu mở app khi túi đã đầy sẵn -> dùng lọc sao + độ hiếm an toàn)
        use_ledger = only_session and sum(self._session_dust_ledger.values()) > 0
        if use_ledger:
            self.log(f"📒 [SỔ HÓA BỤI] Chỉ hóa bụi đúng {sum(self._session_dust_ledger.values())} rồng vừa lai trong đợt này (tự né 100% rồng cũ trong túi)!")

        empty_scrolls = 0
        bottom_hit_count = 0
        skipped_pts: List[Tuple[int, int]] = []

        while not self.should_stop():
            if not self.wait_if_paused():
                return BotState.OPEN_BREED
            if self._need_restart or self._need_rescan:
                return BotState.DUST_MATERIALS
            if use_ledger and sum(self._session_dust_ledger.values()) <= 0:
                self.log("📒 [SỔ HÓA BỤI] Đã hóa bụi sạch toàn bộ rồng vừa lai trong đợt -> Quay về Đảo Trứng ngay!")
                break

            screen, rect = self.vision.capture_window()
            candidates = self._find_all_dust_candidates(screen=screen, rect=rect)

            chosen: Optional[MatchResult] = None
            chosen_star: Optional[int] = None
            for cand in candidates:
                if any(abs(cand.x - sx) < 30 and abs(cand.y - sy) < 30 for sx, sy in skipped_pts):
                    continue
                if self._is_card_locked(screen, rect, cand.x, cand.y, cand.w, cand.h):
                    self.log(f"[HÓA BỤI] Bỏ qua rồng đang bị khóa (🔒) tại ({cand.x}, {cand.y}).")
                    skipped_pts.append((cand.x, cand.y))
                    continue
                if self._is_card_protected(screen, rect, cand.x, cand.y, cand.w, cand.h):
                    self.log(f"🛡️ [CẤM HÓA BỤI] Bỏ qua rồng cũ được bảo vệ tại ({cand.x}, {cand.y}).")
                    skipped_pts.append((cand.x, cand.y))
                    continue

                c_star = None
                if use_ledger or dust_max_star > 0:
                    c_star = self._read_card_star(screen, rect, cand.x, cand.y, cand.w, cand.h)
                    if c_star is None:
                        self.log(f"🛡️ [HÓA BỤI - AN TOÀN] Không đọc rõ số sao của thẻ [{cand.name}] -> Bỏ qua an toàn!")
                        skipped_pts.append((cand.x, cand.y))
                        continue
                    if dust_max_star > 0 and c_star > dust_max_star:
                        self.log(f"🛡️ [HÓA BỤI] Chừa lại rồng [{cand.name}] {c_star}★ (> mức tối đa {dust_max_star}★).")
                        skipped_pts.append((cand.x, cand.y))
                        continue
                    if use_ledger:
                        lkey = (cand.name, int(c_star))
                        if self._session_dust_ledger.get(lkey, 0) <= 0:
                            self.log_tech(f"[SỔ HÓA BỤI] Bỏ qua rồng cũ [{cand.name} {c_star}★] (không nằm trong sổ vừa lai).")
                            skipped_pts.append((cand.x, cand.y))
                            continue

                chosen = cand
                chosen_star = c_star
                break

            if chosen is not None:
                empty_scrolls = 0
                bottom_hit_count = 0
                off_rel = self._drag_cfg("dust_click_offset_rel", [0.038, 0.040])
                is_badge_crop = (chosen.w < rect.width * 0.09) or (chosen.h < rect.height * 0.09)
                dx = int(rect.width * float(off_rel[0])) if is_badge_crop else 0
                dy = int(rect.height * float(off_rel[1])) if is_badge_crop else 0
                click_x = chosen.x + dx
                click_y = chosen.y + dy
                col_side = "Cột TRÁI" if click_x < (rect.left + rect.width * 0.77) else "Cột PHẢI"

                self.log(f"[HÓA BỤI] {col_side} ({chosen.score:.2f}) -> Click giữa rồng ({click_x}, {click_y}).")
                self.input.click(click_x, click_y, post_delay=0.45 * self._spd, stop_check=self.should_stop)

                res_dust = self._click_ui("btn_dust.png", timeout=3.0, retries=1, post_delay=0.4 * self._spd)
                if not (res_dust and res_dust.found):
                    rb_fix = self.vision.find_template(self._ui("btn_dust_bottle.png"), threshold=0.70)
                    if rb_fix.found:
                        self.log("[CHẨN ĐOÁN] Phát hiện đang ở nhầm tab Tẩy Tủy -> Bấm lại bình Hóa Bụi!")
                        self.input.click(rb_fix.x, rb_fix.y, post_delay=0.6 * self._spd, stop_check=self.should_stop)
                        continue
                if res_dust and res_dust.found:
                    res_cf = self._click_ui(
                        "btn_dust_confirm.png", timeout=2.6, retries=1, post_delay=0.45 * self._spd
                    )
                    if res_cf and res_cf.found:
                        self.stats.dusted += 1
                        if use_ledger and chosen_star is not None:
                            lkey = (chosen.name, int(chosen_star))
                            if self._session_dust_ledger.get(lkey, 0) > 0:
                                self._session_dust_ledger[lkey] -= 1
                        self.on_stats(self.stats)
                        skipped_pts.clear()
                        rem = sum(self._session_dust_ledger.values()) if use_ledger else -1
                        rem_s = f" | Còn lại trong sổ: {rem}" if rem >= 0 else ""
                        self.log(f"[HÓA BỤI] Xong [{chosen.name}{f' {chosen_star}★' if chosen_star else ''}] (tổng: {self.stats.dusted}{rem_s}).")
                    else:
                        self.log("[HÓA BỤI] Không hiện bảng Xác nhận (rồng có thể đang khóa) -> Đánh dấu bỏ qua thẻ này.")
                        skipped_pts.append((chosen.x, chosen.y))
                continue

            # Chuẩn bị cuộn xuống -> xóa danh sách tọa độ bỏ qua của khung hình cũ
            skipped_pts.clear()

            empty_scrolls += 1
            if empty_scrolls > max_scrolls:
                self.log("[HÓA BỤI] Hết lượt kéo, không còn rồng 'nguyên liệu'.")
                break

            before_roi, _, _ = self.vision.crop_roi(screen, rect, roi)
            x1, y1 = rect.rel_to_abs(start_rel[0], start_rel[1])
            x2, y2 = rect.rel_to_abs(end_rel[0], end_rel[1])
            self.log(f"[HÓA BỤI] Kéo danh sách 2 cột xuống ({empty_scrolls}/{max_scrolls})...")
            if self.input.is_adb_mode:
                # Thu hẹp quãng vuốt ADB & tăng thời gian để triệt tiêu quán tính trôi lố trên LDPlayer
                mid_x = (x1 + x2) // 2
                ax1 = int(mid_x + (x1 - mid_x) * 0.62)
                ax2 = int(mid_x + (x2 - mid_x) * 0.62)
                self.input.smooth_drag(ax1, y1, ax2, y2, duration=max(0.55, drag_dur * 1.45), hold_end=0.18, stop_check=self.should_stop)
            else:
                self.input.smooth_drag(
                    x1, y1, x2, y2, duration=drag_dur, hold_end=hold_end, stop_check=self.should_stop
                )
            self.input.sleep_interruptible(settle, self.should_stop)

            after_scr, after_rect = self.vision.capture_window()
            after_roi, _, _ = self.vision.crop_roi(after_scr, after_rect, roi)
            diff_val = compute_frame_diff(before_roi, after_roi)
            sim_val = compute_frame_similarity(before_roi, after_roi)
            if sim_val >= 0.93 or diff_val < 3.2:
                bottom_hit_count += 1
                self.log(f"[HÓA BỤI] Đã chạm đáy ({bottom_hit_count}/2, sim={sim_val:.2f}, diff={diff_val:.2f}).")
                if bottom_hit_count >= 2:
                    self.log("[HÓA BỤI] Hết rồng nguyên liệu ở đáy túi -> Đóng bảng Hóa Bụi ngay!")
                    break
            else:
                bottom_hit_count = 0

        return BotState.RETURN_TO_EGG_ISLAND

    # ------------------------------------------------------------------
    # Bước 9: XÁC NHẬN ĐÓNG HẲN BẢNG HÓA BỤI (X) rồi mới bấm < x2!
    # ------------------------------------------------------------------
    def _step_return_to_egg_island(self) -> BotState:
        self._set_state(BotState.RETURN_TO_EGG_ISLAND)

        # Đảm bảo bấm X cho đến khi bảng Hóa Bụi / Tẩy Tủy THẬT SỰ ĐÓNG HẲN (tránh chuyển đảo khi còn mở bảng!)
        for close_try in range(1, 6):
            if not self.wait_if_paused():
                return BotState.OPEN_BREED
            screen, rect = self.vision.capture_window()
            rx = self._find_any_close_x(screen, rect)
            if rx.found:
                self.log(f"[ĐÓNG HÓA BỤI] Click dấu X lần {close_try} tại ({rx.x}, {rx.y}) (score={rx.score:.2f}).")
                self.input.click(rx.x, rx.y, post_delay=0.55 * self._spd, stop_check=self.should_stop)
            else:
                # Nếu bảng Hóa Bụi VẪN đang mở nhưng không khớp ảnh mẫu X -> dùng vị trí X góc phải bảng Hóa Bụi
                if self._is_dust_ui_open(screen, rect):
                    fx, fy = rect.rel_to_abs(0.605, 0.21)
                    self.log(f"[PHỤC HỒI] Bảng Hóa Bụi vẫn mở -> Click dấu X dự phòng tại ({fx}, {fy}).")
                    self.input.click(fx, fy, post_delay=0.55 * self._spd, stop_check=self.should_stop)
                    self.stats.recoveries += 1
                    self.on_stats(self.stats)
                else:
                    break

            # Kiểm tra lại sau khi click X
            if not self._is_dust_ui_open():
                # Kiểm tra thêm 1 lần nữa xem có còn dấu X bảng Tẩy Tủy phía sau không
                chk_x = self._find_any_close_x()
                if not chk_x.found:
                    self.log("[ĐÓNG HÓA BỤI] Đã xác nhận đóng hoàn toàn bảng Hóa Bụi.")
                    break

        hops = int(self.config.get("island_hops", 2))
        nav_thr = self._conf("nav_button", 0.72)
        switch_delay = self._timing("after_island_switch_delay", 0.80)
        for i in range(1, hops + 1):
            if not self.wait_if_paused():
                return BotState.OPEN_BREED
            self.log(f"[VỀ ĐẢO TRỨNG] Bấm < lần {i}/{hops}...")
            self._click_ui("btn_arrow_left.png", timeout=6.0, threshold=nav_thr, retries=3, post_delay=switch_delay)

        self.stats.cycles += 1
        self.on_stats(self.stats)
        self.log(f"=== HOÀN THÀNH VÒNG LẶP #{self.stats.cycles} - QUAY LẠI LAI RỒNG ===")
        return BotState.OPEN_BREED

    def _check_human_break(self) -> None:
        """Nghỉ giải lao tự nhiên (Human Break): nghỉ ngẫu nhiên 1-2 phút sau mỗi 40-60 phút treo máy."""
        if not self.config.get("human_break_enabled", True):
            return
        interval_min = max(10, int(self.config.get("human_break_interval_min", 45) or 45))
        dur_sec = max(15, int(self.config.get("human_break_duration_sec", 90) or 90))
        now = time.monotonic()
        if self._next_break_ts <= 0:
            self._next_break_ts = now + interval_min * 60.0 * random.uniform(0.88, 1.15)
            return
        if now >= self._next_break_ts:
            rest = int(dur_sec * random.uniform(0.78, 1.25))
            self.log(f"☕ [Nghỉ tự nhiên] Đang nghỉ giải lao {rest} giây giống người chơi thật (nhấn ESC để dừng ngay)...")
            self.stats.current_state = f"Nghỉ tự nhiên ({rest}s)"
            self.on_stats(self.stats)
            self.input.sleep_interruptible(float(rest), self.should_stop)
            self._wd_last_change_ts = time.monotonic()
            self._next_break_ts = time.monotonic() + interval_min * 60.0 * random.uniform(0.88, 1.15)

    def _check_watchdog(self) -> None:
        wd_sec = float(self.config.get("watchdog_seconds", 180) or 0)
        if wd_sec <= 0:
            return
        now = time.monotonic()
        if now - getattr(self, "_wd_last_check", 0.0) < 15.0:
            return
        self._wd_last_check = now
        try:
            scr, _ = self.vision.capture_window()
            small = cv2.resize(scr, (160, 90))
            if self._wd_last_frame is None or compute_frame_similarity(self._wd_last_frame, small) < 0.996:
                self._wd_last_frame = small
                self._wd_last_change_ts = now
            elif now - self._wd_last_change_ts >= wd_sec:
                self._wd_last_change_ts = now
                self._trigger_alert_and_pause(
                    "Màn hình game đứng yên quá lâu",
                    f"Màn hình không thay đổi trong {int(wd_sec)} giây (có thể mất mạng hoặc đơ giả lập). Hãy kiểm tra lại game!",
                )
        except Exception:
            pass

    def _run_loop(self) -> None:
        mode_lbl = "Chỉ Auto Lai" if self.is_breed_only else "Lai + Hóa Bụi"
        ctrl_lbl = f"Chạy Ngầm ADB ({self.adb_serial})" if self.input.is_adb_mode else "Chuột Tự Nhiên"
        self.log(f"=== BẮT ĐẦU DaoRong_tool v10.4 [{self.account_label}] | {mode_lbl} | {ctrl_lbl} ===")
        state = BotState.OPEN_BREED
        idle = self._timing("loop_idle_delay", 0.10)

        handlers = {
            BotState.OPEN_BREED: self._step_open_breed,
            BotState.SELECT_PARENTS: self._step_select_parents,
            BotState.WAIT_BREED_RESULT: self._step_wait_breed_result,
            BotState.CLASSIFY_AND_ACT: self._step_classify_and_act,
            BotState.EXIT_BREED_TO_DUST: self._step_exit_breed_to_dust,
            BotState.SWITCH_TO_DUST_ISLAND: self._step_switch_to_dust_island,
            BotState.OPEN_DUST_UI: self._step_open_dust_ui,
            BotState.DUST_MATERIALS: self._step_dust_materials,
            BotState.RETURN_TO_EGG_ISLAND: self._step_return_to_egg_island,
        }

        try:
            while not self.should_stop():
                if not self.wait_if_paused():
                    break
                if self._need_restart:
                    self._need_restart = False
                    self._need_rescan = False
                    state = self.detect_current_screen(for_restart=True)
                    continue
                if self._need_rescan:
                    self._need_rescan = False
                    state = self.detect_current_screen(for_restart=False)
                    continue

                state = handlers[state]()
                self._check_watchdog()
                self._check_human_break()
                self.input.sleep_interruptible(idle, self.should_stop)
        except Exception as exc:
            self.log(f"[LỖI NGOẠI LỆ] {exc}")
        finally:
            self.stats.current_state = "Đã dừng"
            self.on_stats(self.stats)
            self.log(f"=== ĐÃ DỪNG DaoRong_tool v10.4 [{self.account_label}] ===")
