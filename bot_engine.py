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
from carousel import CarouselEdgeTracker, ParentCarousel
from ocr_engine import DragonTextMatcher, extract_stars, load_dragon_catalog, normalize_vn, parse_rule_string
import random
from notifier import AlertNotifier
from parent_cards import (
    ParentCard, find_parent_cards, has_parent_star, has_visible_parent_star,
    parent_star_digits, parent_tile_was_emptied, terminal_gap,
)
from smart_ai import (
    AnchorAutoCalibrator,
    OcrAdaptiveDictionary,
    PairDirectionMemory,
    PairStatsTracker,
    PersistentNameCache,
    RareMomentSaver,
    TrainingDatasetCollector,
)
from yolo_vision import YoloLevel2Vision
from updater import CURRENT_VERSION
from vision import MatchResult, VisionEngine, WindowRect, compute_frame_diff, compute_frame_similarity, imread_unicode


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


@dataclass
class PendingDust:
    card: MatchResult
    rect: WindowRect
    card_image: np.ndarray
    stars: Optional[int]
    session_sample_index: Optional[int]


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
        shared_yolo: Optional[YoloLevel2Vision] = None,
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
        self._pre_identified = None
        self._breed_counted_for_cycle = False
        self._pair_stats_recorded_for_cycle = False
        self._drop_recorded_for_cycle = False
        self._keep_retry_count = 0
        self._pending_bless_keep = False
        self._session_dust_sprites: Dict[Tuple[str, int], List[np.ndarray]] = {}
        self._pending_dust: Optional[PendingDust] = None
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
        self.dir_memory = PairDirectionMemory()
        self._verified_parent_slots: Dict[int, np.ndarray] = {}
        self.yolo = shared_yolo if shared_yolo is not None else YoloLevel2Vision(self.base_dir / "models")
        self.notifier = AlertNotifier(
            telegram_token=config.get("telegram_token", ""),
            telegram_chat_id=config.get("telegram_chat_id", ""),
            discord_webhook=config.get("discord_webhook", ""),
            enabled=bool(config.get("enable_remote_notify", False)),
        )
        self._game_package = str(config.get("adb_game_package", "") or "").strip()
        self._watchdog_relaunch_count = 0

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
        if self._pending_dust is not None:
            return BotState.DUST_MATERIALS
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
        result_buttons = ["btn_keep.png", "btn_sell.png", "btn_bless.png"]
        if getattr(self, "_pending_bless_keep", False):
            result_buttons.append("btn_keep_after_bless.png")
        for bname in result_buttons:
            bp = self._ui(bname)
            if bp.is_file():
                m = self.vision.find_template(bp, threshold=btn_thr, screen_bgr=screen, rect=rect)
                if m.found:
                    found_btns[bname] = (m.x, m.y)
        if len(found_btns) >= 2 or "btn_keep.png" in found_btns or "btn_keep_after_bless.png" in found_btns:
            self._cached_result_btns = found_btns
            self._cached_result_frame = (screen, rect)
            self._pre_identified = None
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
        *,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> Optional[MatchResult]:
        path = self._ui(filename)
        if not path.is_file():
            self.log(f"[CẢNH BÁO] Chưa có ảnh mẫu UI: templates/ui/{filename}")
            return None

        thr = self._conf("button", 0.76) if threshold is None else threshold
        wait_sec = self._timing("ui_wait_timeout", 8.0) if timeout is None else timeout
        should_cancel = cancel_check or self.should_stop
        search_options = {}
        if filename in {"btn_dust.png", "btn_dust_confirm.png"} and self.vision.is_adb_mode:
            search_options["compare_scales"] = True

        for attempt in range(1, retries + 1):
            if not self.wait_if_paused() or should_cancel():
                return None
            res = self.vision.wait_for_template(
                path, timeout=wait_sec, threshold=thr, stop_check=should_cancel, **search_options
            )
            if res.found:
                if should_cancel():
                    return None
                self.input.click(res.x, res.y, post_delay=post_delay, stop_check=should_cancel)
                return None if should_cancel() else res
            if attempt < retries:
                self.log(
                    f"[THỬ LẠI] Chưa thấy '{filename}' (max={res.score:.2f} / {thr:.2f}), thử lần {attempt + 1}..."
                )
        return None

    # ------------------------------------------------------------------
    # Bước 1: Mở bảng Lai Rồng (Chống rồng bay che trứng)
    # ------------------------------------------------------------------
    def _read_resource_ratio(
        self, screen: np.ndarray, rect: WindowRect, roi_rel: List[float]
    ) -> Optional[Tuple[int, int]]:
        """Đọc cặp số 'A/B' trên thanh 'Cần: A/B' (Ngọc lai hoặc Đá chúc phúc) bằng OCR."""
        import re as _re
        if not self.text_matcher.is_available:
            return None
        try:
            crop, _, _ = self.vision.crop_roi(screen, rect, roi_rel)
            if crop is None or crop.size == 0:
                return None
            txt = self.text_matcher.read_name_crop(crop) or ""
            m = _re.search(r"(\d[\d.,]*)\s*/\s*(\d[\d.,]*)", txt)
            if m:
                a = int(_re.sub(r"[^\d]", "", m.group(1)) or "0")
                b = int(_re.sub(r"[^\d]", "", m.group(2)) or "0")
                return a, b
        except Exception:
            pass
        return None

    def _watchdog_record_game_pkg(self) -> None:
        """Tự ghi nhớ tên package Đảo Rồng trên Android lần đầu chạy ADB."""
        if self.adb is None or not self.adb_serial or self._game_package:
            return
        try:
            pkg = self.adb.get_foreground_package(self.adb_serial)
            if pkg and not any(k in pkg.lower() for k in ("launcher", "systemui", "settings", "android.desk")):
                self._game_package = pkg
                self.config["adb_game_package"] = pkg
                self.log_tech(f"[WATCHDOG 24/7] Đã ghi nhận package game: {pkg}")
        except Exception:
            pass

    def _watchdog_check_and_relaunch(self) -> bool:
        """Watchdog 24/7: phát hiện game văng ra màn hình chính Android -> tự mở lại game & chạy tiếp!"""
        if self.adb is None or not self.adb_serial or not self.config.get("watchdog_relaunch", True):
            return False
        if not self._game_package:
            self._watchdog_record_game_pkg()
            if not self._game_package:
                return False
        try:
            cur = self.adb.get_foreground_package(self.adb_serial)
            if cur and cur != self._game_package and any(
                k in cur.lower() for k in ("launcher", "home", "systemui", "android.desk")
            ):
                self._watchdog_relaunch_count += 1
                self.log(f"🔄 [WATCHDOG 24/7] Phát hiện game bị văng ({cur}) -> Tự mở lại {self._game_package}!")
                self.notifier.send_async(
                    f"🔄 [{self.account_label}] Game bị văng ra màn hình chính -> Watchdog đang tự mở lại game (lần #{self._watchdog_relaunch_count})."
                )
                if self.adb.relaunch_package(self.adb_serial, self._game_package):
                    self.input.sleep_interruptible(12.0, self.should_stop)
                    self._dismiss_stray_popups(max_clicks=3)
                    return True
        except Exception:
            pass
        return False

    def _is_on_dust_island(self, screen: np.ndarray, rect: WindowRect) -> bool:
        if self.yolo.ready and self.yolo.find_best(screen, "altar", conf_thr=0.48) is not None:
            return True
        altar_p = self._ui("btn_altar_center.png")
        if altar_p.is_file():
            return self.vision.find_template(altar_p, threshold=0.68, screen_bgr=screen, rect=rect).found
        return False

    def _scan_islands_for_egg(self) -> bool:
        """Tự động bấm mũi tên chuyển đảo (< rồi >) để tìm về đúng Đảo Trứng khi người dùng bật tool ở đảo khác."""
        egg_p = self._ui("btn_egg.png")
        if not egg_p.is_file():
            return False
        nav_thr = self._conf("nav_button", 0.72)
        for arrow_name, label, max_hops in [
            ("btn_arrow_left.png", "sang trái (<)", 4),
            ("btn_arrow_right.png", "sang phải (>)", 6),
        ]:
            arr_p = self._ui(arrow_name)
            if not arr_p.is_file():
                continue
            for hop in range(1, max_hops + 1):
                if not self.wait_if_paused():
                    return False
                scr, rect = self.vision.capture_window()
                if self._is_breed_ui_open(scr, rect):
                    return True
                y_egg = self.yolo.find_best(scr, "egg", conf_thr=0.52) if self.yolo.ready else None
                m_egg = self.vision.find_template(egg_p, threshold=0.68, screen_bgr=scr, rect=rect)
                if y_egg is not None or m_egg.found:
                    self.log(f"🏝️ Đã tìm thấy Đảo Trứng sau khi chuyển đảo {label}!")
                    return True
                m_arr = self.vision.find_template(arr_p, threshold=nav_thr, screen_bgr=scr, rect=rect)
                if not m_arr.found:
                    break
                self.log(f"🏝️ Chưa thấy Trứng trên đảo này -> Chuyển đảo {label} ({hop}/{max_hops})...")
                self.input.click(m_arr.x, m_arr.y, post_delay=0.70 * self._spd, stop_check=self.should_stop)
                # Kiểm tra ngay sau khi chuyển đảo
                scr2, rect2 = self.vision.capture_window()
                if (self.yolo.ready and self.yolo.find_best(scr2, "egg", conf_thr=0.52) is not None) or self.vision.find_template(egg_p, threshold=0.68, screen_bgr=scr2, rect=rect2).found:
                    self.log("🏝️ Đã về đúng Đảo Trứng!")
                    return True
        return False

    def _step_open_breed(self) -> BotState:
        self._set_state(BotState.OPEN_BREED)
        self._watchdog_record_game_pkg()
        screen, rect = self.vision.capture_window()

        if self._check_bag_full(screen, rect):
            return BotState.EXIT_BREED_TO_DUST
        if self._is_breed_ui_open(screen, rect):
            return BotState.SELECT_PARENTS
        if self._is_dust_ui_open(screen, rect):
            self.log("[PHỤC HỒI] Đang ở giao diện Hóa Bụi -> Chuyển sang dọn nguyên liệu.")
            return BotState.DUST_MATERIALS

        # Nếu đang lạc ở World Map / màn hình phụ -> bấm Về Đảo trước!
        if not self._has_island_arrows(screen, rect):
            if self._recover_from_map_or_subscreen():
                screen, rect = self.vision.capture_window()

        # Nhận diện ngay nếu người dùng đang đứng ở Đảo Tẩy Tủy -> tự dò về Đảo Trứng, không click nhầm vào Tháp!
        egg_path = self._ui("btn_egg.png")
        egg_visible = egg_path.is_file() and self.vision.find_template(egg_path, threshold=0.68, screen_bgr=screen, rect=rect).found
        if not egg_visible and self._is_on_dust_island(screen, rect):
            self.log("🏝️ Phát hiện đang ở Đảo Tẩy Tủy -> Tự chuyển đảo tìm về Đảo Trứng!")
            self._scan_islands_for_egg()
            screen, rect = self.vision.capture_window()

        # Thử mở trứng tối đa 4 lần (nếu sau 2 lần vẫn không thấy mở bảng Lai -> dò đảo khác!)
        egg_rel = self._pin("egg_rel", [0.49, 0.34])
        spiral_offsets = [(0, 0), (0, 14), (-14, 0), (14, 0)]

        for attempt, (ox, oy) in enumerate(spiral_offsets, 1):
            if not self.wait_if_paused():
                return BotState.OPEN_BREED

            screen, rect = self.vision.capture_window()
            if self._is_breed_ui_open(screen, rect):
                return BotState.SELECT_PARENTS

            clicked_pos = None
            # AI Cấp 2 (YOLOv8-nano): nhìn xuyên rồng che tìm chuẩn tâm Trứng trên Đảo Trứng!
            if self.yolo.ready:
                y_egg = self.yolo.find_best(screen, "egg", conf_thr=0.52)
                if y_egg is not None:
                    clicked_pos = (rect.left + y_egg.cx, rect.top + y_egg.cy)
                    self.config.setdefault("pins", {})["egg_rel"] = list(rect.abs_to_rel(*clicked_pos))
                    self.log_tech(f"[AI CẤP 2] Phát hiện Trứng ({y_egg.conf:.2f}) tại {clicked_pos}.")
            if clicked_pos is None and egg_path.is_file():
                res = self.vision.find_template(egg_path, threshold=0.68, screen_bgr=screen, rect=rect)
                if res.found:
                    clicked_pos = (res.x, res.y)
                    self.config.setdefault("pins", {})["egg_rel"] = list(rect.abs_to_rel(res.x, res.y))

            if clicked_pos is None:
                # Nếu thử ghim 2 lần rồi mà chưa mở được bảng Lai -> có thể đang đứng sai đảo -> tự quét chuyển đảo!
                if attempt == 3:
                    self.log("🏝️ Không thấy trứng trên đảo hiện tại -> Tự dò các đảo lân cận tìm Đảo Trứng...")
                    if self._scan_islands_for_egg():
                        return BotState.OPEN_BREED
                px, py = rect.rel_to_abs(egg_rel[0], egg_rel[1])
                clicked_pos = (px + ox, py + oy)
                self.log(
                    f"[TRỨNG] Rồng có thể đang che trứng -> Click điểm ghim lần {attempt}/4 tại {clicked_pos}."
                )
            else:
                self.log(f"[TRỨNG] Click trứng tại {clicked_pos}.")

            self.input.click(clicked_pos[0], clicked_pos[1], post_delay=0.30 * self._spd, stop_check=self.should_stop)

            chk_scr, chk_rect = self.vision.capture_window()
            if self._is_breed_ui_open(chk_scr, chk_rect):
                return BotState.SELECT_PARENTS

            self._dismiss_stray_popups(max_clicks=1)
            self.input.sleep_interruptible(0.30, self.should_stop)

        self.log("[PHỤC HỒI] Không mở được trứng -> Kiểm tra Watchdog & tự dò lại đảo...")
        self._consecutive_failures += 1
        if self._consecutive_failures >= 2 and self._watchdog_check_and_relaunch():
            self._consecutive_failures = 0
            return BotState.OPEN_BREED
        self._scan_islands_for_egg()
        self.stats.recoveries += 1
        self.on_stats(self.stats)
        return BotState.OPEN_BREED

    # ------------------------------------------------------------------
    # Bước 2: Chọn hai rồng bố mẹ và kiểm chứng từng thẻ sau khi bấm.
    # ------------------------------------------------------------------
    def _count_empty_parent_slots(self, screen: np.ndarray, rect: WindowRect) -> Tuple[bool, bool]:
        """Trả về (left_empty, right_empty), hỗ trợ cả co giãn theo khung lẫn giữ nguyên cỡ nội dung."""
        emp_p = self._ui("slot_parent_empty.png")
        if not emp_p.is_file():
            return True, True
        roi_l = [0.14, 0.22, 0.36, 0.56]
        roi_r = [0.38, 0.22, 0.60, 0.56]
        m_l = self.vision.find_template(emp_p, threshold=0.60, roi_rel=roi_l, screen_bgr=screen, rect=rect)
        m_r = self.vision.find_template(emp_p, threshold=0.60, roi_rel=roi_r, screen_bgr=screen, rect=rect)
        if m_l.found and m_r.found:
            return True, True
        tpl = imread_unicode(emp_p)
        if tpl is not None and tpl.size > 0:
            crop_l, _, _ = self.vision.crop_roi(screen, rect, roi_l)
            crop_r, _, _ = self.vision.crop_roi(screen, rect, roi_r)
            for sc in (1.0, 992.0 / 1294.0):
                tw, th = max(12, int(tpl.shape[1] * sc)), max(12, int(tpl.shape[0] * sc))
                t_s = cv2.resize(tpl, (tw, th), interpolation=cv2.INTER_AREA)
                if not m_l.found and crop_l.shape[0] >= th and crop_l.shape[1] >= tw:
                    if float(cv2.matchTemplate(crop_l, t_s, cv2.TM_CCOEFF_NORMED).max()) >= 0.62:
                        m_l.found = True
                if not m_r.found and crop_r.shape[0] >= th and crop_r.shape[1] >= tw:
                    if float(cv2.matchTemplate(crop_r, t_s, cv2.TM_CCOEFF_NORMED).max()) >= 0.62:
                        m_r.found = True
        return m_l.found, m_r.found

    def _click_upper_slot(self, rect: WindowRect, slot_idx: int) -> None:
        """Click đúng tâm ô bố mẹ phía trên (1=Trái, 2=Phải) để trả rồng chọn nhầm xuống thanh dưới."""
        # Ở chế độ cửa sổ Windows có thanh tiêu đề LDPlayer (~34px) -> bù nhẹ ry xuống +0.035
        ry = 0.385 if self.vision.is_adb_mode else 0.420
        rx = 0.245 if slot_idx == 1 else 0.485
        cx, cy = rect.rel_to_abs(rx, ry)
        self.input.click(cx, cy, post_delay=0.22 * self._spd, stop_check=self.should_stop)

    @staticmethod
    def _slot_crop_upper(screen: np.ndarray, slot_idx: int, is_adb: bool) -> np.ndarray:
        h, w = screen.shape[:2]
        y1 = int(h * (0.26 if is_adb else 0.29))
        y2 = int(h * (0.50 if is_adb else 0.53))
        if slot_idx == 1:
            return screen[y1:y2, int(w * 0.18):int(w * 0.31)]
        return screen[y1:y2, int(w * 0.42):int(w * 0.55)]

    @staticmethod
    def _dragon_hsv_hist(slot_bgr: np.ndarray) -> Optional[np.ndarray]:
        if slot_bgr is None or slot_bgr.size == 0:
            return None
        h, w = slot_bgr.shape[:2]
        inner = slot_bgr[int(h * 0.20):int(h * 0.82), int(w * 0.20):int(w * 0.80)]
        if inner.size == 0:
            return None
        # Từ chối ảnh đơn sắc (đỏ tối đồng nhất, đen) bằng độ lệch chuẩn từng kênh màu -> vẫn nhận ảnh nhiều màu đồng độ sáng!
        max_ch_std = max(float(inner[:, :, c].std()) for c in range(3)) if inner.ndim == 3 else float(inner.std())
        if max_ch_std < 10.0:
            return None
        hsv = cv2.cvtColor(inner, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, (0, 0, 85), (180, 255, 255))
        if int(cv2.countNonZero(mask)) < 25:
            return None
        hist = cv2.calcHist([hsv], [0, 1], mask, [18, 8], [0, 180, 0, 256])
        if float(hist.sum()) <= 0.0:
            return None
        cv2.normalize(hist, hist, 0, 1, cv2.NORM_MINMAX)
        return hist

    @staticmethod
    def _parent_slot_shape(slot: np.ndarray) -> Optional[np.ndarray]:
        """Lấy phần lõi có hình rồng, giữ chi tiết màu kể cả khi ảnh xám tối."""
        h, w = slot.shape[:2]
        core = slot[int(h * 0.18):int(h * 0.85), int(w * 0.18):int(w * 0.82)]
        if core.size == 0:
            return None
        normalized = cv2.resize(core, (48, 64), interpolation=cv2.INTER_AREA)
        normalized = cv2.GaussianBlur(normalized, (3, 3), 0)
        _, channel_std = cv2.meanStdDev(normalized)
        return normalized if float(channel_std.max()) >= 3.0 else None

    def _parent_carousel(self, screen: np.ndarray, rect: WindowRect) -> Optional[ParentCarousel]:
        button = self.vision.find_template(
            self._ui("btn_start_breed.png"), threshold=0.66, roi_rel=[0.38, 0.47, 0.59, 0.65],
            screen_bgr=screen, rect=rect, compare_scales=True,
        )
        if button.found and button.w > 0:
            scale = button.w / 144.0
            x, y = button.x - rect.left, button.y - rect.top
            viewport = (
                round(x - 374 * scale), round(y + 61 * scale),
                round(x + 73 * scale), round(y + 174 * scale),
            )
        else:
            configured = self._drag_cfg("bottom_bar_roi", [0.15, 0.62, 0.61, 0.89])
            x1, y1, x2, y2 = rect.roi_rel_to_local_px(configured)
            if 3.55 <= (x2 - x1) / max(1, y2 - y1) <= 4.45:
                viewport = (x1, y1, x2, y2)
            elif self.vision.is_adb_mode and 1.72 <= rect.width / max(1, rect.height) <= 1.84:
                # ADB frames contain the game alone, without window chrome.
                viewport = rect.roi_rel_to_local_px([253 / 1280, 475 / 720, 700 / 1280, 588 / 720])
            elif rect.found_window and "ldplayer" in rect.title.lower():
                title_height = round(rect.height * 0.045)
                game_height = rect.height - title_height
                game_width = min(rect.width, round(game_height * 16 / 9))
                viewport = (
                    round(game_width * 253 / 1280), title_height + round(game_height * 475 / 720),
                    round(game_width * 700 / 1280), title_height + round(game_height * 588 / 720),
                )
            else:
                return None
        left, top, right, bottom = viewport
        if left < 0 or top < 0 or right > screen.shape[1] or bottom > screen.shape[0] or right - left < 96:
            return None
        return ParentCarousel(viewport, round((right - left) / 4))

    def _parent_slot_matches(self, screen: np.ndarray, slot: int, previous: np.ndarray) -> bool:
        current = self._parent_slot_shape(self._slot_crop_upper(screen, slot, self.vision.is_adb_mode))
        if current is None or compute_frame_similarity(previous, current) < 0.83:
            return False
        sprite = (previous.max(axis=2) >= 105) | (current.max(axis=2) >= 105)
        difference = cv2.absdiff(previous, current)
        return float(difference[sprite].mean() if sprite.any() else difference.mean()) <= 18.0

    def _selected_pair_still_present(self, screen: np.ndarray, rect: WindowRect) -> bool:
        return (
            len(self._verified_parent_slots) == 2
            and self._count_empty_parent_slots(screen, rect) == (False, False)
            and all(self._parent_slot_matches(screen, slot, shape) for slot, shape in self._verified_parent_slots.items())
        )

    def _select_parents_dual_fast(self, p1_path: Path, p2_path: Path) -> bool:
        """Select from complete cards and verify each source tile before authorizing breeding."""
        self._verified_parent_slots.clear()
        if self.should_stop():
            return False
        frame, rect = self.vision.capture_window()
        carousel = self._parent_carousel(frame, rect)
        if carousel is None:
            self.log("[THANH RỒNG] Chưa xác định được vị trí thanh thẻ trên màn hình.")
            return False

        is_adb = self.vision.is_adb_mode
        settle = 0.23 * self._spd
        view_left, view_top, view_right, view_bottom = carousel.viewport
        row = np.s_[view_top:view_bottom, view_left:view_right]
        empty = self._count_empty_parent_slots(frame, rect)

        # A pre-filled upper slot has no star label and no source tile tied to this run.
        for slot, is_empty in enumerate(empty, 1):
            if not is_empty:
                self._click_upper_slot(rect, slot)
        if empty != (True, True):
            for _ in range(4):
                if not self.input.sleep_interruptible(0.10, self.should_stop):
                    return False
                frame, current_rect = self.vision.capture_window()
                if current_rect != rect:
                    return False
                if self._count_empty_parent_slots(frame, rect) == (True, True):
                    break
            else:
                self.log("[CHỌN RỒNG] Không xác nhận được hai ô bố mẹ đã trống.")
                return False

        remaining = {}
        for index, path in enumerate((p1_path, p2_path), 1):
            stars = self.config.get(f"selected_parent_{index}_stars")
            if stars is not None and (type(stars) is not int or not 1 <= stars <= 99):
                self.log(f"[CHỌN RỒNG] Số sao rồng {index} không hợp lệ.")
                return False
            if stars is None:
                sample = imread_unicode(path)
                if sample is not None and 0.87 <= sample.shape[1] / sample.shape[0] <= 1.14 and has_parent_star(sample):
                    stars = self.text_matcher.read_parent_stars(parent_star_digits(sample))
            remaining[index] = (path, stars)

        best_seen = {1: 0.0, 2: 0.0}

        def select_in_frame() -> Optional[list[ParentCard]]:
            nonlocal frame
            while remaining and not self.should_stop():
                cards = find_parent_cards(frame, carousel.viewport, carousel.pitch)
                ranked = {idx: [] for idx in remaining}
                for card in cards:
                    if not card.complete:
                        continue
                    before = card.crop(frame)
                    if not has_parent_star(before):
                        continue
                    x1, y1, x2, y2 = card.bounds()
                    relative = [(x1 + 0.1) / rect.width, (y1 + 0.1) / rect.height,
                                (x2 + 0.1) / rect.width, (y2 + 0.1) / rect.height]
                    read_stars = False
                    observed_stars = None
                    for index, (path, stars) in remaining.items():
                        threshold = (0.79 if is_adb else 0.82) if stars is not None else 0.87
                        match = self.vision.find_template(
                            path, threshold=threshold, roi_rel=relative, screen_bgr=frame,
                            rect=rect, compare_scales=True,
                        )
                        best_seen[index] = max(best_seen[index], match.score)
                        if not match.found:
                            continue
                        if stars is not None and not read_stars:
                            observed_stars = self.text_matcher.read_parent_stars(parent_star_digits(before))
                            read_stars = True
                        if stars is None or stars == observed_stars:
                            ranked[index].append((match.score, card))

                choices = []
                for index, matches in ranked.items():
                    matches.sort(key=lambda item: item[0], reverse=True)
                    if not matches or (len(matches) > 1 and matches[0][0] - matches[1][0] < 0.04):
                        continue
                    score, card = matches[0]
                    # Require a clear matching margin when both targets resemble the same tile.
                    if any(other != index and any(tile == card and abs(other_score - score) < 0.04
                                                 for other_score, tile in other_matches)
                           for other, other_matches in ranked.items()):
                        continue
                    choices.append((remaining[index][1] is not None, score, index, card))
                if not choices:
                    return cards
                _, score, index, card = max(choices, key=lambda item: item[:2])
                path, stars = remaining[index]
                empty_before = self._count_empty_parent_slots(frame, rect)
                if empty_before.count(False) != len(self._verified_parent_slots):
                    return None
                before = card.crop(frame)
                x = rect.left + (card.left + card.right) // 2
                y = rect.top + card.top + round((card.bottom - card.top) * 0.57)
                stars_label = f", {stars} sao" if stars is not None else ""
                self.log(f"[CHỌN RỒNG] Thử rồng {index} ({path.stem}{stars_label}, khớp {score:.2f}).")
                self.input.click(x, y, post_delay=settle, stop_check=self.should_stop)

                confirmed_slot = None
                for attempt in range(5):
                    if self.should_stop():
                        return None
                    after, after_rect = self.vision.capture_window()
                    if after_rect != rect:
                        return None
                    empty_after = self._count_empty_parent_slots(after, rect)
                    filled = [slot for slot, (was_empty, is_empty) in enumerate(zip(empty_before, empty_after), 1)
                              if was_empty and not is_empty]
                    verified_now = None
                    if len(filled) == 1 and parent_tile_was_emptied(before, card.crop(after)):
                        slot = filled[0]
                        shape = self._parent_slot_shape(self._slot_crop_upper(after, slot, is_adb))
                        unchanged = all(not empty_after[old_slot - 1] and self._parent_slot_matches(after, old_slot, old_shape)
                                        for old_slot, old_shape in self._verified_parent_slots.items())
                        if shape is not None and unchanged:
                            verified_now = (slot, shape)
                            if (confirmed_slot is not None and confirmed_slot[0] == slot
                                    and self._parent_slot_matches(after, slot, confirmed_slot[1])):
                                self._verified_parent_slots[slot] = shape
                                del remaining[index]
                                frame = after
                                self.log(f"[CHỌN RỒNG] Đã xác minh rồng {index}{stars_label} vào ô {slot}.")
                                break
                    confirmed_slot = verified_now
                    if attempt == 4 or not self.input.sleep_interruptible(0.10, self.should_stop):
                        self.log(f"[CHỌN RỒNG] Thẻ rồng {index} chưa trống đúng vị trí sau cú bấm. Dừng kiểm chứng.")
                        return None
            return [] if not remaining else None

        def swipe(gesture: Tuple[int, int, int, int]) -> Tuple[bool, Optional[int]]:
            nonlocal frame
            x1, y1, x2, y2 = gesture
            before = frame
            self.input.smooth_drag(
                rect.left + x1, rect.top + y1, rect.left + x2, rect.top + y2,
                duration=0.42 * self._spd, hold_end=0.12, stop_check=self.should_stop,
            )
            if not self.input.sleep_interruptible(settle, self.should_stop):
                return False, None
            previous = None
            for attempt in range(5):
                next_frame, current_rect = self.vision.capture_window()
                if current_rect != rect or self.should_stop():
                    return False, None
                # ADB can deliver an old screenshot immediately after a gesture.
                moved = compute_frame_diff(before[row], next_frame[row]) > 1.2
                settled = previous is not None and compute_frame_diff(previous[row], next_frame[row]) < 1.2
                if settled and (moved or attempt >= 2):
                    frame = next_frame
                    return True, carousel.measure_shift(before, frame)
                previous = next_frame
                if attempt == 4 or not self.input.sleep_interruptible(0.10, self.should_stop):
                    break
            self.log("[THANH RỒNG] Chưa có ảnh ổn định sau khi vuốt.")
            return False, None

        def scan_with_nudge(*, require_geometry: bool = False) -> bool:
            nonlocal frame
            cards = select_in_frame()
            if cards is None or not remaining:
                return cards is not None
            if not cards and require_geometry:
                if not self.input.sleep_interruptible(settle, self.should_stop):
                    return False
                frame, current_rect = self.vision.capture_window()
                if current_rect != rect:
                    return False
                cards = select_in_frame()
                if cards is None or not remaining:
                    return cards is not None
                if not cards:
                    self.log("[THANH RỒNG] Chưa xác định được các đường phân cách giữa thẻ.")
                    return False
            clipped = [
                card for card in cards
                if not card.complete and has_visible_parent_star(card, frame)
            ]
            if not clipped:
                return True
            card = max(clipped, key=lambda item: item.visible_width)
            gesture = carousel.nudge_swipe(card.left, card.right)
            if gesture is None:
                return True
            self.log_tech("[THANH RỒNG] Căn thẻ bị cắt ở mép trước khi quét tiếp.")
            settled, _ = swipe(gesture)
            return settled and select_in_frame() is not None

        if not scan_with_nudge(require_geometry=True):
            return False
        if not remaining:
            return self._selected_pair_still_present(frame, rect)
        edge = CarouselEdgeTracker()
        # A smaller swipe needs more steps to cover the same number of cards.
        limit = max(1, int(self._drag_cfg("max_parent_scrolls", 24))) * 2
        first = 1 if self.dir_memory.preferred_direction(p1_path.name, p2_path.name) == "left" else -1
        for direction in (first, -first):
            for _ in range(limit):
                if not self.wait_if_paused() or self._check_bag_full(frame, rect):
                    return False
                if terminal_gap(frame, carousel.viewport, carousel.pitch, direction):
                    break
                gesture = carousel.page_swipe(direction)
                if gesture is None:
                    break
                before_swipe = frame
                settled, shift = swipe(gesture)
                if not settled:
                    return False
                if shift is not None and not carousel.keeps_card_overlap(shift):
                    # Undo the excess with a measured correction before scanning this page.
                    x1, y1, _, _ = gesture
                    dx = min(carousel.pitch, abs(shift) - carousel.pitch)
                    corrected, correction = swipe((x1, y1, x1 - (dx if shift > 0 else -dx), y1))
                    if not corrected or correction is None:
                        self.log("[THANH RỒNG] Cú vuốt dài, chưa thể xác nhận phần thẻ chồng nhau.")
                        return False
                    shift = carousel.measure_shift(before_swipe, frame)
                    if shift is None or not carousel.keeps_card_overlap(shift):
                        return False
                reached_edge = terminal_gap(frame, carousel.viewport, carousel.pitch, direction) or edge.observe(direction, shift)
                # Keep the endpoint frame as the basis for the next direction.
                scanned = select_in_frame() is not None if reached_edge else scan_with_nudge()
                if not scanned:
                    return False
                if not remaining:
                    self.dir_memory.record(p1_path.name, p2_path.name, "left" if direction > 0 else "right")
                    return self._selected_pair_still_present(frame, rect)
                if reached_edge:
                    break

        for index, (path, stars) in remaining.items():
            detail = f" {stars} sao" if stars is not None else ""
            self.log(f"[CHỌN RỒNG] Chưa xác minh được rồng {index} ({path.stem}{detail}, khớp cao nhất {best_seen[index]:.2f}).")
        return False

    def _step_select_parents(self) -> BotState:
        self._set_state(BotState.SELECT_PARENTS)
        self._breed_counted_for_cycle = False
        self._pair_stats_recorded_for_cycle = False
        self._drop_recorded_for_cycle = False
        self._pending_bless_keep = False
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

        if not self._select_parents_dual_fast(p1_path, p2_path):
            if self._check_bag_full():
                return BotState.EXIT_BREED_TO_DUST
            if not self.should_stop():
                self._trigger_alert_and_pause("Chưa xác minh đủ rồng bố mẹ", "Xem nhật ký chọn rồng và kiểm tra ảnh mẫu, số sao của từng con.")
            return BotState.SELECT_PARENTS

        sb_path = self._ui("btn_start_breed.png")
        if self.should_stop():
            return BotState.OPEN_BREED
        if not sb_path.is_file():
            self._trigger_alert_and_pause("Thiếu ảnh nút Lai Rồng", "Cần bổ sung templates/ui/btn_start_breed.png.")
            return BotState.SELECT_PARENTS
        sb_roi = self.calibrator.get_roi(self._mode_key, "btn_start_breed.png") or [0.38, 0.48, 0.58, 0.65]
        threshold = self._conf("button", 0.76)
        scale_options = {"compare_scales": True} if self.vision.is_adb_mode else {}
        self.vision.wait_for_template(
            sb_path, timeout=2.0, threshold=threshold, roi_rel=sb_roi, stop_check=self.should_stop, **scale_options
        )
        if self.should_stop():
            return BotState.OPEN_BREED
        screen, rect = self.vision.capture_window()
        if not self._selected_pair_still_present(screen, rect):
            self._trigger_alert_and_pause("Ô bố mẹ đã thay đổi", "Cần xác minh lại cả hai rồng trước khi lai.")
            return BotState.SELECT_PARENTS
        res = self.vision.find_template(
            sb_path, threshold=threshold, roi_rel=sb_roi,
            screen_bgr=screen, rect=rect, **scale_options,
        )
        if not res.found:
            self._trigger_alert_and_pause("Chưa thấy nút Lai Rồng", "Hãy kiểm tra lại hai ô bố mẹ và giao diện lai.")
            return BotState.SELECT_PARENTS
        if self.should_stop():
            return BotState.OPEN_BREED
        self.log_tech(f"[LAI RỒNG] Click 'Lai Rồng' ({res.x}, {res.y}).")
        self.input.click(res.x, res.y, post_delay=0.10, stop_check=self.should_stop)
        if self.should_stop():
            return BotState.OPEN_BREED

        self._consecutive_failures = 0
        self._breed_counted_for_cycle = False
        self._keep_retry_count = 0
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
        if getattr(self, "_pending_bless_keep", False):
            result_buttons.append("btn_keep_after_bless.png")
        # Quét đúng dải nút kết quả thay vì quét toàn màn hình -> nhanh gấp ~12 lần!
        btns_band_roi = self._drag_cfg("result_buttons_roi", [0.16, 0.42, 0.64, 0.72])

        self._pre_identified = None
        name_roi = self._drag_cfg("result_name_roi", [0.57, 0.285, 0.78, 0.348])
        text_rules = self.config.get("dragon_text_rules", {})
        has_rules = any(bool(text_rules.get(k)) for k in ("bless", "keep", "sell"))

        while time.monotonic() < deadline:
            if not self.wait_if_paused():
                return BotState.OPEN_BREED

            screen, rect = self.vision.capture_window()
            if self._check_bag_full(screen, rect):
                return BotState.EXIT_BREED_TO_DUST

            if self._pre_identified is None and has_rules and self.name_cache.count > 0:
                nc, _, _ = self.vision.crop_roi(screen, rect, name_roi)
                if PersistentNameCache._has_text_signal(nc):
                    res_key = f"{self._mode_key}_{rect.width}x{rect.height}"
                    mem_hit = self.name_cache.match(nc, res_key, threshold=0.89)
                    if mem_hit is not None:
                        c_name, c_rar, c_stars, c_sc = mem_hit
                        synth_txt = f"{c_name} ({c_stars} sao)" if c_stars else c_name
                        tm_mem = self.text_matcher.match_text_against_rules(synth_txt, text_rules)
                        if tm_mem.found:
                            self._pre_identified = (tm_mem.category, c_name, c_rar, c_stars, c_sc, nc.copy())

            found_btns: Dict[str, Tuple[int, int]] = {}
            for bname in result_buttons:
                bp = self._ui(bname)
                if bp.is_file():
                    b_roi = self.calibrator.get_roi(self._mode_key, bname)
                    m_btn = self.vision.find_template(
                        bp, threshold=btn_thr, roi_rel=b_roi or btns_band_roi, screen_bgr=screen, rect=rect
                    )
                    if not m_btn.found and b_roi and b_roi != btns_band_roi:
                        m_btn = self.vision.find_template(
                            bp, threshold=btn_thr, roi_rel=btns_band_roi, screen_bgr=screen, rect=rect
                        )
                    if m_btn.found:
                        found_btns[bname] = (m_btn.x, m_btn.y)
                        self.calibrator.record(self._mode_key, bname, *rect.abs_to_rel(m_btn.x, m_btn.y))

            if len(found_btns) >= 2 or "btn_keep.png" in found_btns or "btn_keep_after_bless.png" in found_btns:
                self._cached_result_btns = found_btns
                self._cached_result_frame = (screen, rect)
                if not self._breed_counted_for_cycle:
                    self.stats.breeds += 1
                    self._breed_counted_for_cycle = True
                    self.on_stats(self.stats)
                return BotState.CLASSIFY_AND_ACT

            time.sleep(0.035)

        self.log("[PHỤC HỒI] Chờ kết quả quá lâu -> Kiểm tra lại nút 'Lai Rồng' hoặc trạng thái bảng.")
        self.stats.recoveries += 1
        self.on_stats(self.stats)
        return BotState.OPEN_BREED

    def _click_result_button(self, filename: str, **options: Any) -> bool:
        coords = self._cached_result_btns.pop(filename, None)
        if coords is None:
            result = self._click_ui(filename, **options)
            return bool(result and result.found)
        if self.should_stop():
            return False
        self.input.click(*coords, post_delay=options.get("post_delay"), stop_check=self.should_stop)
        return not self.should_stop()

    def _verify_result_completed(self) -> bool:
        """Chỉ ghi thành công khi game trở về bảng lai hoặc Đảo Trứng."""
        threshold = self._conf("button", 0.76)
        result_roi = self._drag_cfg("result_buttons_roi", [0.16, 0.42, 0.64, 0.72])
        next_screen_buttons = ("btn_start_breed.png", "btn_egg.png")
        result_buttons = ("btn_keep.png", "btn_keep_after_bless.png")
        for attempt in range(4):
            if self.should_stop():
                return False
            if attempt and not self.input.sleep_interruptible(0.18, self.should_stop):
                return False
            screen, rect = self.vision.capture_window()
            if self._check_bag_full(screen, rect):
                return False

            def visible(filename: str, roi=None) -> bool:
                path = self._ui(filename)
                return path.is_file() and self.vision.find_template(
                    path, threshold=threshold, roi_rel=roi, screen_bgr=screen, rect=rect
                ).found

            if (
                any(visible(name) for name in next_screen_buttons)
                and not any(visible(name, result_roi) for name in result_buttons)
                and not visible("btn_sell_confirm.png")
            ):
                return True
        return False

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

        pending_bless_keep = getattr(self, "_pending_bless_keep", False)
        action = "bless" if pending_bless_keep else None
        if not pending_bless_keep:
            self._last_identified = ("", "", None)
        name_roi = self._drag_cfg("result_name_roi", [0.57, 0.285, 0.78, 0.348])
        res_key = f"{self._mode_key}_{rect.width}x{rect.height}"
        text_rules = self.config.get("dragon_text_rules", {})
        has_rules = any(bool(text_rules.get(k)) for k in ("bless", "keep", "sell"))
        name_crop = None

        # Đọc OCR đúng 1 lần duy nhất cho khung hiện tại và dùng chung cho mọi nhánh (tránh gọi OCR 3 lần!)
        ocr_once_done = False
        ocr_once_raw: Optional[str] = None
        ocr_once_stars: Optional[int] = None

        def _get_ocr_once(crop_img: np.ndarray) -> Tuple[Optional[str], Optional[int]]:
            nonlocal ocr_once_done, ocr_once_raw, ocr_once_stars
            if not ocr_once_done:
                ocr_once_done = True
                if self.text_matcher.is_available and crop_img is not None and crop_img.size > 0:
                    ocr_once_raw = self.text_matcher.read_name_crop(crop_img)
                    ocr_once_stars = extract_stars(ocr_once_raw or "")
            return ocr_once_raw, ocr_once_stars

        if action is None and getattr(self, "_pre_identified", None) is not None:
            pre_act, c_name, c_rar, c_stars, c_sc, nc_pre = self._pre_identified
            self._pre_identified = None
            nc_now, _, _ = self.vision.crop_roi(screen, rect, name_roi)
            name_crop = nc_now
            star_ok = True
            if nc_now.shape == nc_pre.shape and nc_now.shape[1] >= 30:
                sw_cut = max(12, int(nc_now.shape[1] * 0.60))
                if float(cv2.matchTemplate(nc_pre[:, sw_cut:], nc_now[:, sw_cut:], cv2.TM_CCOEFF_NORMED)[0, 0]) < 0.96:
                    star_ok = False
            if star_ok and c_stars is not None:
                if not self.text_matcher.is_available:
                    star_ok = False
                else:
                    _, st_now = _get_ocr_once(nc_now)
                    if st_now is None or st_now != c_stars:
                        star_ok = False
            if (
                star_ok
                and PersistentNameCache._has_text_signal(nc_now)
                and nc_now.shape == nc_pre.shape
                and float(cv2.matchTemplate(nc_pre, nc_now, cv2.TM_CCOEFF_NORMED)[0, 0]) >= 0.94
            ):
                action = pre_act
                self._last_identified = (c_name, c_rar, c_stars)
                act_vn = {"bless": "Chúc phúc & Cất", "keep": "Cất túi", "sell": "Bán ngay"}[action]
                st_str = f" ({c_stars}★)" if c_stars else ""
                self.log(f"⚡ Lai ra: {c_name}{st_str} [{c_rar}] → {act_vn} (nhớ trước 0ms)")
            else:
                self.log_tech("[NHẬN DIỆN SỚM] Vùng tên/sao đổi hoặc chưa đọc rõ sao -> Đọc lại trực tiếp!")

        # ƯU TIÊN 1: AI Bộ Nhớ Tự Học — nếu mẫu nhớ có số sao, BẮT BUỘC khớp với số sao OCR vừa đọc trên khung hiện tại!
        if action is None and has_rules and self.name_cache.count > 0:
            if name_crop is None:
                name_crop, _, _ = self.vision.crop_roi(screen, rect, name_roi)
            mem_hit = self.name_cache.match(name_crop, res_key, threshold=0.89)
            if mem_hit is not None:
                c_name, c_rar, c_stars, c_sc = mem_hit
                star_verified = True
                if c_stars is not None:
                    if not self.text_matcher.is_available:
                        star_verified = False
                        self.log_tech(f"[AI CACHE] Từ chối mẫu nhớ {c_stars}★ vì OCR không khả dụng để xác minh sao!")
                    else:
                        _, st_chk = _get_ocr_once(name_crop)
                        if st_chk is None or st_chk != c_stars:
                            star_verified = False
                            self.log_tech(f"[AI CACHE] Từ chối mẫu nhớ {c_stars}★ (OCR hiện tại: {st_chk}★)!")
                if star_verified:
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

        # ƯU TIÊN 2B: Đọc dòng Tên+Sao bằng OCR (tái sử dụng kết quả ocr_once nếu đã đọc!)
        if action is None and has_rules and self.config.get("enable_text_ocr", True):
            if name_crop is None:
                name_crop, _, _ = self.vision.crop_roi(screen, rect, name_roi)
            ocr_raw, ocr_stars = _get_ocr_once(name_crop)
            if ocr_raw:
                adapted = self.ocr_dict.lookup(ocr_raw)
                effective_ocr = (
                    f"{adapted} ({ocr_stars} sao)" if adapted and ocr_stars is not None else (adapted or ocr_raw)
                )
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

        # Bán mặc định cũng không được ghi đè luật bảo vệ rồng theo số sao.
        if action == "sell":
            parsed_rules = (
                parse_rule_string(rule)
                for category in ("sell", "keep", "bless")
                for rule in text_rules.get(category, [])
            )
            star_rule_names = [dragon for dragon, operator, _ in parsed_rules if operator != "any"]
            if star_rule_names:
                if name_crop is None:
                    name_crop, _, _ = self.vision.crop_roi(screen, rect, name_roi)
                current_text, _ = _get_ocr_once(name_crop)
                current_rule = self.text_matcher.match_text_against_rules(current_text or "", text_rules)
                if current_rule.found:
                    action = current_rule.category
                    self._last_identified = (current_rule.identified_name, current_rule.identified_rarity, current_rule.stars)
                else:
                    star_rule_name = self.text_matcher.match_text_against_rules(
                        current_text or "", {"sell": star_rule_names}
                    )
                    if not current_rule.identified_name or star_rule_name.found:
                        action = "keep"
                        self.log("[BẢO VỆ] Chưa xác minh được điều kiện sao để Bán trên khung hiện tại -> Cất rồng.")

        if action == "bless" and not pending_bless_keep:
            bless_ratio = self._read_resource_ratio(screen, rect, [0.23, 0.54, 0.44, 0.64])
            if bless_ratio is not None:
                need_st, have_st = bless_ratio
                if need_st > 0 and have_st < need_st:
                    self.log(f"⚠️ [HẾT ĐÁ CHÚC PHÚC] Cần {need_st}, chỉ còn {have_st} -> Chuyển sang Cất rồng.")
                    self.notifier.send_async(
                        f"⚠️ [{self.account_label}] Hết Đá Chúc Phúc ({have_st}/{need_st})! Tool sẽ thử Cất rồng thay thế.",
                        screen,
                    )
                    action = "keep"

        keep_succeeded = False
        sell_succeeded = False
        bless_succeeded = False
        if action == "keep":
            clicked = self._click_result_button("btn_keep.png", timeout=4.0, post_delay=0.16 * self._spd)
            keep_succeeded = clicked and self._verify_result_completed()
            if keep_succeeded:
                self.stats.kept += 1
        elif action == "sell":
            if self._click_result_button("btn_sell.png", timeout=4.0, post_delay=0.14 * self._spd):
                result = self._click_ui("btn_sell_confirm.png", timeout=4.5, retries=2, post_delay=0.12 * self._spd)
                sell_succeeded = bool(result and result.found) and self._verify_result_completed()
                if sell_succeeded:
                    self.stats.sold += 1
        elif action == "bless":
            if not pending_bless_keep:
                bless_thr = min(0.72, self._conf("button", 0.76))
                if self._click_result_button(
                    "btn_bless.png", timeout=5.0, threshold=bless_thr, retries=3, post_delay=0.25
                ):
                    self._pending_bless_keep = True
                    self._cached_result_btns.clear()
                    self.log("[CHÚC PHÚC] Đã bấm Chúc phúc -> Chờ hiệu ứng rồi Cất...")
                    self.input.sleep_interruptible(2.0, self.should_stop)
            if getattr(self, "_pending_bless_keep", False) and not self.should_stop():
                after_btn = (
                    "btn_keep_after_bless.png"
                    if self._ui("btn_keep_after_bless.png").is_file()
                    else "btn_keep.png"
                )
                clicked_keep = self._click_result_button(
                    after_btn,
                    timeout=self._timing("bless_wait_timeout", 15.0),
                    retries=1,
                    post_delay=0.45 * self._spd,
                )
                if not clicked_keep and after_btn != "btn_keep.png" and not self.should_stop():
                    clicked_keep = self._click_result_button("btn_keep.png", timeout=2.0, retries=1, post_delay=0.45 * self._spd)
                bless_succeeded = clicked_keep and self._verify_result_completed()
                if bless_succeeded:
                    self._pending_bless_keep = False
                    self.stats.blessed += 1
                    self.log("[CHÚC PHÚC] Chúc phúc (+1 sao) & Đã cất rồng thành công!")

        # Ghi nhận vào Sổ Tay Rồng Vừa Lai (chỉ ghi con Cất thuộc độ hiếm cho phép Hóa Bụi; TUYỆT ĐỐI không ghi con Chúc phúc!)
        id_name, id_rarity, id_stars = getattr(self, "_last_identified", ("", "", None))
        if keep_succeeded and id_rarity and id_stars is not None:
            enabled_dr = set(self.config.get("dust_rarities", ["Nguyên Liệu"]))
            max_st = int(self.config.get("dust_max_star", 0) or 0)
            if id_rarity in enabled_dr and (max_st == 0 or id_stars <= max_st):
                lkey = (id_rarity, int(id_stars))
                self._session_dust_ledger[lkey] = self._session_dust_ledger.get(lkey, 0) + 1
                # Lưu thêm bảng màu sprite rồng vừa lai để đối chiếu khi Hóa Bụi
                try:
                    sh_i, sw_i = screen.shape[:2]
                    spr = screen[int(sh_i * 0.25):int(sh_i * 0.45), int(sw_i * 0.30):int(sw_i * 0.43)]
                    shist = self._dragon_hsv_hist(spr)
                    if shist is not None:
                        self._session_dust_sprites.setdefault(lkey, []).append(shist)
                except Exception:
                    pass
                self.log_tech(f"[SỔ HÓA BỤI] +1 ({id_rarity}, {id_stars}★) -> tổng chờ bụi: {sum(self._session_dust_ledger.values())}")
        action_ok = keep_succeeded or sell_succeeded or bless_succeeded
        if action_ok and not getattr(self, "_pair_stats_recorded_for_cycle", False):
            self._pair_stats_recorded_for_cycle = True
            try:
                self.pair_stats.record_breed(
                    self.config.get("selected_parent_1", ""),
                    self.config.get("selected_parent_2", ""),
                    id_rarity,
                    action,
                )
            except Exception:
                pass
        if (id_rarity in ("Hiếm", "Cực hiếm", "Event") or bless_succeeded) and not getattr(self, "_drop_recorded_for_cycle", False):
            self._drop_recorded_for_cycle = True
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
                "action": action if action_ok else None,
                "time": time.strftime("%H:%M:%S"),
            })
            if len(self.stats.recent_drops) > 30:
                self.stats.recent_drops.pop()
            if bless_succeeded or id_rarity in ("Cực hiếm", "Event"):
                st_lbl = f" ({id_stars}★)" if id_stars else ""
                act_lbl = "Chúc phúc & Cất" if bless_succeeded else (action.upper() if action_ok else "chưa xác minh xử lý")
                self.notifier.send_async(
                    f"🐉 [{self.account_label}] Nổ rồng quý: {id_name or 'Rồng Chúc Phúc'}{st_lbl} [{id_rarity or 'Quý'}] → {act_lbl}!",
                    screen,
                )
            if self.config.get("save_rare_snapshots", True) and (id_rarity in ("Cực hiếm", "Event") or bless_succeeded):
                self.rare_saver.save_shot(screen, id_name or "Rong_Quy", id_stars)

        self.on_stats(self.stats)
        if not action_ok and action in {"sell", "bless"} and not self.should_stop():
            if self._check_bag_full():
                return BotState.EXIT_BREED_TO_DUST
            self._trigger_alert_and_pause(
                "Chưa xác minh được xử lý rồng",
                "Hãy kiểm tra bảng kết quả hoặc xác nhận trong game trước khi chạy tiếp.",
            )
            return BotState.WAIT_BREED_RESULT

        self._check_auto_stop_conditions(id_name, id_rarity, id_stars)
        if self.is_paused or self.should_stop():
            return BotState.SELECT_PARENTS

        self.input.sleep_interruptible(0.12 * self._spd, self.should_stop)
        if self._check_bag_full():
            return BotState.EXIT_BREED_TO_DUST
        if keep_succeeded:
            self._keep_retry_count = 0
        elif action == "keep" and not self.should_stop():
            self._keep_retry_count += 1
            if self._keep_retry_count >= 3:
                self._trigger_alert_and_pause(
                    "Không thấy nút Cất rồng!",
                    "Thử bấm Cất rồng 3 lần không được — tool đã tạm dừng để tránh mất rồng.",
                )
                return BotState.WAIT_BREED_RESULT
            self.log(f"[PHỤC HỒI] Chưa bấm được nút Cất rồng ({self._keep_retry_count}/3) -> Thử lại.")
            return BotState.WAIT_BREED_RESULT

        self._breed_counted_for_cycle = False
        self._pair_stats_recorded_for_cycle = False
        self._drop_recorded_for_cycle = False
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
    DUST_PREVIEW_ROI = [0.27, 0.25, 0.41, 0.45]

    def _find_all_dust_candidates(self, screen, rect) -> List[MatchResult]:
        """Quét TẤT CẢ thẻ thuộc các độ hiếm được phép Hóa Bụi trên cả 2 cột trái & phải (có khử trùng NMS)."""
        roi = self._get_padded_dust_roi()
        thr = self._conf("nguyen_lieu", 0.70)
        enabled_rarities = self.config.get("dust_rarities", ["Nguyên Liệu"])
        if not enabled_rarities:
            return []
        crop, ox, oy = self.vision.crop_roi(screen, rect, roi)

        matches: List[MatchResult] = []
        neighborhood = np.ones((3, 3), dtype=np.uint8)
        for rar_name in enabled_rarities:
            fname = self.DUST_RARITY_TEMPLATES.get(rar_name)
            if not fname:
                continue
            badge_thr = thr if rar_name == "Nguyên Liệu" else max(0.78, thr)
            for _, template in self.vision.iter_template_variants(self._ui(fname), rect, compare_scales=True):
                th, tw = template.shape[:2]
                if crop.shape[0] < th or crop.shape[1] < tw:
                    continue
                scores = cv2.matchTemplate(crop, template, cv2.TM_CCOEFF_NORMED)
                ys, xs = np.where((scores >= badge_thr) & (scores == cv2.dilate(scores, neighborhood)))
                for py, px in zip(ys, xs):
                    matches.append(MatchResult(
                        found=True, name=rar_name, score=float(scores[py, px]),
                        x=ox + int(px) + tw // 2, y=oy + int(py) + th // 2, w=tw, h=th,
                    ))

        # Mỗi thẻ có thể khớp ở nhiều tỷ lệ. Giữ nhận diện mạnh nhất của mỗi vị trí.
        results: List[MatchResult] = []
        merge_x = max(8, round(42 * rect.width / 1330))
        merge_y = max(8, round(42 * rect.height / 761))
        for match in sorted(matches, key=lambda candidate: candidate.score, reverse=True):
            if any(abs(match.x - card.x) < merge_x and abs(match.y - card.y) < merge_y for card in results):
                continue
            results.append(match)
        results.sort(key=lambda card: (card.y // merge_y, card.x))

        if not results and self.dust_targets_dir.is_dir():
            best = self.vision.find_best_across_folders(
                {"dust": self.dust_targets_dir}, threshold=thr, roi_rel=roi, screen_bgr=screen, rect=rect
            )
            if best.found:
                results.append(best)
        return results

    def _dust_interrupted(self) -> bool:
        return self.should_stop() or self.is_paused or self._need_rescan or self._need_restart

    @staticmethod
    def _dust_card_image(screen: np.ndarray, rect: WindowRect, card: MatchResult) -> np.ndarray:
        sx, sy = rect.width / 1330.0, rect.height / 761.0
        left = max(0, card.x - rect.left - int(28 * sx))
        top = max(0, card.y - rect.top - int(18 * sy))
        return screen[top:top + int(115 * sy), left:left + int(145 * sx)]

    @staticmethod
    def _dust_sprite_similarity(card: np.ndarray, preview: np.ndarray) -> float:
        thumbnails = []
        for image in (card, preview):
            h, w = image.shape[:2]
            inner = image[int(h * 0.20):int(h * 0.82), int(w * 0.20):int(w * 0.80)]
            thumbnails.append(cv2.resize(inner, (32, 32), interpolation=cv2.INTER_AREA))
        # Dung sai một dịch chuyển nhỏ giữa ảnh trong túi và ô Xem Trước.
        template = thumbnails[0][4:-4, 4:-4]
        if float(template.std(axis=(0, 1)).max()) < 5.0:
            template = thumbnails[0]
        scores = cv2.matchTemplate(thumbnails[1], template, cv2.TM_CCOEFF_NORMED)
        return float(scores.max())

    def _wait_for_dust_preview(
        self, selected: PendingDust, card_hist: np.ndarray, previous_preview: np.ndarray,
    ) -> bool:
        reason = "ô Xem Trước chưa tải hoặc thiếu chi tiết"
        for attempt in range(5 if self.vision.is_adb_mode else 3):
            if self._dust_interrupted():
                return False
            if attempt and not self.input.sleep_interruptible(0.18, self._dust_interrupted):
                return False
            screen, rect = self.vision.capture_window()
            if rect != selected.rect:
                reason = "vùng cửa sổ đã đổi sau khi chọn"
                break
            preview, _, _ = self.vision.crop_roi(screen, rect, self.DUST_PREVIEW_ROI)
            preview_hist = self._dragon_hsv_hist(preview)
            if preview_hist is None:
                continue
            color = float(cv2.compareHist(card_hist, preview_hist, cv2.HISTCMP_CORREL))
            shape = self._dust_sprite_similarity(selected.card_image, preview)
            # Khi ảnh xem trước đã có sẵn, chỉ chấp nhận nếu hình dạng khớp chặt với thẻ.
            changed = compute_frame_diff(previous_preview, preview) >= 2.0
            if color < 0.65 or shape < (0.38 if changed else 0.85):
                reason = f"ô Xem Trước không khớp thẻ (màu {color:.2f}, hình {shape:.2f})"
                continue
            card = selected.card
            image_now = self._dust_card_image(screen, rect, card)
            card_visible = (
                image_now.shape == selected.card_image.shape
                and self._dragon_hsv_hist(image_now) is not None
                and self._dust_sprite_similarity(selected.card_image, image_now) >= 0.75
            )
            # Ô xem trước có thể che thẻ hoặc số sao. Khi đó cần ảnh mới khớp đặc trưng chặt hơn.
            if not card_visible and (not changed or shape < 0.85):
                reason = "thẻ không còn đọc được trong danh sách và ô Xem Trước chưa đủ rõ"
                continue
            if card_visible:
                if self._is_card_locked(screen, rect, card.x, card.y, card.w, card.h) or self._is_card_protected(screen, rect, card.x, card.y, card.w, card.h):
                    reason = "thẻ có dấu khóa hoặc thuộc danh sách bảo vệ"
                    break
                if selected.stars is not None:
                    stars_now = self._read_card_star(screen, rect, card.x, card.y, card.w, card.h)
                    if stars_now is not None and stars_now != selected.stars:
                        reason = f"số sao sau khi chọn khác thẻ dự kiến ({stars_now}★)"
                        continue
            return not self._dust_interrupted()

        self.log(f"🛡️ [HÓA BỤI] Bỏ qua thẻ vì {reason}.")
        return False

    def _verify_dust_completion(self) -> bool:
        pending = self._pending_dust
        if pending is None:
            return True
        dust_button = self._ui("btn_dust.png")
        close_dust = self._ui("btn_close_dust_x.png")
        confirm_button = self._ui("btn_dust_confirm.png")
        threshold = self._conf("button", 0.76)
        compare_scales = self.vision.is_adb_mode
        verified_frames = 0
        for attempt in range(4):
            if attempt and not self.input.sleep_interruptible(0.18, self._dust_interrupted):
                break
            screen, rect = self.vision.capture_window()
            options = {"screen_bgr": screen, "rect": rect, "threshold": threshold, "compare_scales": compare_scales}
            if rect != pending.rect or self.vision.find_template(confirm_button, **options).found:
                verified_frames = 0
                continue
            if not any(self.vision.find_template(path, **options).found for path in (dust_button, close_dust)):
                verified_frames = 0
                continue
            image_now = self._dust_card_image(screen, rect, pending.card)
            if image_now.shape != pending.card_image.shape or compute_frame_diff(pending.card_image, image_now) < 8.0:
                verified_frames = 0
                continue
            if self._dragon_hsv_hist(image_now) is not None and self._dust_sprite_similarity(pending.card_image, image_now) >= 0.82:
                verified_frames = 0
                continue
            preview, _, _ = self.vision.crop_roi(screen, rect, self.DUST_PREVIEW_ROI)
            if self._dragon_hsv_hist(preview) is not None and self._dust_sprite_similarity(pending.card_image, preview) >= 0.82:
                verified_frames = 0
                continue
            verified_frames += 1
            if verified_frames == 2:
                break
        if verified_frames < 2:
            return False

        self._pending_dust = None
        self.stats.dusted += 1
        if pending.session_sample_index is not None:
            key = (pending.card.name, pending.stars)
            count = self._session_dust_ledger.get(key, 0)
            sprites = self._session_dust_sprites.get(key, [])
            if count > 0 and pending.session_sample_index < len(sprites):
                self._session_dust_ledger[key] = count - 1
                sprites.pop(pending.session_sample_index)
        self.on_stats(self.stats)
        self.log(f"[HÓA BỤI] Đã kiểm tra thẻ rời danh sách [{pending.card.name}] (tổng: {self.stats.dusted}).")
        return True

    def _step_dust_materials(self) -> BotState:
        self._set_state(BotState.DUST_MATERIALS)
        resuming_confirmation = self._pending_dust is not None
        if resuming_confirmation and not self._verify_dust_completion():
            if not self._dust_interrupted():
                self._trigger_alert_and_pause(
                    "Chưa xác minh Hóa Bụi",
                    "Hãy kiểm tra rồng và hộp Xác nhận trong game. Tool chưa cập nhật sổ và sẽ không bấm lại.",
                )
            return BotState.DUST_MATERIALS
        if not self.config.get("dust_rarities", ["Nguyên Liệu"]):
            self._trigger_alert_and_pause("Chưa chọn độ hiếm Hóa Bụi", "Chọn nhóm rồng được phép Hóa Bụi trước khi chạy lại.")
            return BotState.DUST_MATERIALS
        roi = self._get_padded_dust_roi()
        start_rel = self._drag_cfg("dust_drag_start", [0.77, 0.74])
        end_rel = self._drag_cfg("dust_drag_end", [0.77, 0.28])
        max_scrolls = int(self._drag_cfg("max_dust_scrolls", 14))
        drag_dur = float(self._drag_cfg("drag_duration", 0.38)) * self._spd
        hold_end = float(self._drag_cfg("drag_hold_end", 0.12))
        settle = self._timing("scroll_settle_delay", 0.50)
        dust_max_star = int(self.config.get("dust_max_star", 0) or 0)
        only_session = bool(self.config.get("dust_only_session_bred", True))
        use_ledger = only_session and (str(self.config.get("bot_mode", "breed_and_dust")) != "dust_only")
        if use_ledger:
            tot_l = sum(self._session_dust_ledger.values())
            if tot_l <= 0:
                if resuming_confirmation:
                    return BotState.RETURN_TO_EGG_ISLAND
                self.log("🛡️ [SỔ HÓA BỤI] Chưa ghi nhận rồng mới lai nào trong phiên này. Đã dừng Hóa Bụi theo phiên.")
                self.notifier.send_async(f"🛡️ [{self.account_label}] Túi đầy sẵn từ trước, Sổ Hóa Bụi đã tự dừng để bảo vệ rồng cũ trong túi!")
                self.stop()
                return BotState.OPEN_BREED
            self.log(f"📒 [SỔ HÓA BỤI] Có {tot_l} lượt hóa bụi trong sổ phiên. Đang đối chiếu từng thẻ với mẫu đã ghi.")

        empty_scrolls = 0
        bottom_hit_count = 0
        dust_fail_count = 0
        tab_fix_count = 0
        skipped_pts: List[Tuple[int, int]] = []
        check_open_confirmation = True
        confirm_path = self._ui("btn_dust_confirm.png")
        compare_scales = self.vision.is_adb_mode
        confirm_threshold = self._conf("button", 0.76)

        while not self.should_stop():
            if not self.wait_if_paused():
                return BotState.OPEN_BREED
            if self._need_restart or self._need_rescan:
                return BotState.DUST_MATERIALS
            if use_ledger and sum(self._session_dust_ledger.values()) <= 0:
                self.log("📒 [SỔ HÓA BỤI] Đã hóa bụi sạch toàn bộ rồng vừa lai trong đợt -> Quay về Đảo Trứng ngay!")
                break

            screen, rect = self.vision.capture_window()
            if check_open_confirmation:
                check_open_confirmation = False
                if self.vision.find_template(
                    confirm_path, threshold=confirm_threshold, screen_bgr=screen, rect=rect,
                    compare_scales=compare_scales,
                ).found:
                    self._trigger_alert_and_pause(
                        "Hộp Xác nhận đã mở",
                        "Hãy đóng hộp Xác nhận Hóa Bụi đang mở trong game trước khi tiếp tục.",
                    )
                    return BotState.DUST_MATERIALS
            candidates = self._find_all_dust_candidates(screen=screen, rect=rect)

            chosen: Optional[MatchResult] = None
            chosen_star: Optional[int] = None
            chosen_spr_idx: Optional[int] = None
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
                        spr_list = self._session_dust_sprites.get(lkey, [])
                        if not spr_list:
                            self.log(f"🛡️ [SỔ HÓA BỤI] Thiếu bằng chứng mẫu màu phiên cho {cand.name} {c_star}★ -> Bỏ qua an toàn!")
                            skipped_pts.append((cand.x, cand.y))
                            continue
                        c_hist = self._dragon_hsv_hist(self._dust_card_image(screen, rect, cand))
                        if c_hist is None:
                            skipped_pts.append((cand.x, cand.y))
                            continue
                        sims = [float(cv2.compareHist(c_hist, sh, cv2.HISTCMP_CORREL)) for sh in spr_list]
                        best_idx = int(np.argmax(sims))
                        best_sp = sims[best_idx]
                        if best_sp < 0.72:
                            self.log(f"🛡️ [SỔ HÓA BỤI] Bỏ qua thẻ trùng {cand.name} {c_star}★ nhưng khác màu rồng vừa lai (khớp={best_sp:.2f})!")
                            skipped_pts.append((cand.x, cand.y))
                            continue
                        chosen_spr_idx = best_idx

                chosen = cand
                chosen_star = c_star
                break

            if chosen is not None:
                empty_scrolls = 0
                bottom_hit_count = 0
                is_left_col = chosen.x < (rect.left + rect.width * 0.77)
                col_side = "Cột TRÁI" if is_left_col else "Cột PHẢI"
                # Tính điểm bấm trực tiếp từ khung thẻ vừa phát hiện (tôn trọng dust_click_offset_rel & kẹp trong ROI!)
                off_rel = self._drag_cfg("dust_click_offset_rel", [0.025, 0.038])
                is_badge_crop = (chosen.w < rect.width * 0.09) or (chosen.h < rect.height * 0.09)
                dx = int(min(chosen.w * 0.45, rect.width * float(off_rel[0]))) if is_badge_crop else 0
                dy = int(min(chosen.h * 0.65, rect.height * float(off_rel[1]))) if is_badge_crop else 0
                roi_px = rect.roi_rel_to_local_px(roi)
                click_x = max(rect.left + roi_px[0] + 6, min(rect.left + roi_px[2] - 6, chosen.x + dx))
                click_y = max(rect.top + roi_px[1] + 6, min(rect.top + roi_px[3] - 6, chosen.y + dy))

                card_image = self._dust_card_image(screen, rect, chosen).copy()
                pre_card_hist = self._dragon_hsv_hist(card_image)
                if pre_card_hist is None:
                    self.log("🛡️ [HÓA BỤI] Thẻ dự kiến thiếu chi tiết để xác minh.")
                    skipped_pts.append((chosen.x, chosen.y))
                    continue

                selection = PendingDust(chosen, rect, card_image, chosen_star, chosen_spr_idx)
                previous_preview, _, _ = self.vision.crop_roi(screen, rect, self.DUST_PREVIEW_ROI)
                if self._dust_interrupted():
                    return BotState.DUST_MATERIALS
                self.log(f"[HÓA BỤI] {col_side} ({chosen.score:.2f}) -> Click giữa rồng ({click_x}, {click_y}).")
                self.input.click(click_x, click_y, post_delay=0.45 * self._spd, stop_check=self._dust_interrupted)

                if not self._wait_for_dust_preview(selection, pre_card_hist, previous_preview):
                    skipped_pts.append((chosen.x, chosen.y))
                    continue

                res_dust = self._click_ui(
                    "btn_dust.png", timeout=3.0, retries=1, post_delay=0.4 * self._spd,
                    cancel_check=self._dust_interrupted,
                )
                if self._dust_interrupted():
                    return BotState.DUST_MATERIALS
                if not (res_dust and res_dust.found):
                    rb_fix = self.vision.find_template(self._ui("btn_dust_bottle.png"), threshold=0.70)
                    if rb_fix.found:
                        tab_fix_count += 1
                        if tab_fix_count >= 3:
                            self.log("[HÓA BỤI] Bấm chuyển tab Hóa Bụi 3 lần không thành công -> Thoát an toàn.")
                            break
                        self.log(f"[CHẨN ĐOÁN] Phát hiện đang ở nhầm tab Tẩy Tủy -> Bấm lại bình Hóa Bụi ({tab_fix_count}/3)!")
                        self.input.click(rb_fix.x, rb_fix.y, post_delay=0.6 * self._spd, stop_check=self._dust_interrupted)
                        continue
                    dust_fail_count += 1
                    skipped_pts.append((chosen.x, chosen.y))
                    self.log(f"[HÓA BỤI] Không thấy nút 'Hóa Bụi' ({dust_fail_count}/3) -> Bỏ qua thẻ này.")
                    if dust_fail_count >= 3:
                        self.log("[HÓA BỤI] Lỗi không thấy nút Hóa Bụi liên tiếp 3 lần -> Đóng bảng Hóa Bụi an toàn.")
                        break
                    continue
                confirm = self.vision.wait_for_template(
                    confirm_path, timeout=2.6, threshold=confirm_threshold,
                    stop_check=self._dust_interrupted, compare_scales=compare_scales,
                )
                if self._dust_interrupted():
                    return BotState.DUST_MATERIALS
                if not confirm.found:
                    self.log("[HÓA BỤI] Không hiện bảng Xác nhận (rồng có thể đang khóa) -> Đánh dấu bỏ qua thẻ này.")
                    skipped_pts.append((chosen.x, chosen.y))
                    continue

                self._pending_dust = selection
                self.input.click(confirm.x, confirm.y, post_delay=0.45 * self._spd, stop_check=self._dust_interrupted)
                if not self._verify_dust_completion():
                    if not self._dust_interrupted():
                        self._trigger_alert_and_pause(
                            "Chưa xác minh Hóa Bụi",
                            "Hãy kiểm tra rồng và hộp Xác nhận trong game. Tool chưa cập nhật sổ và sẽ không bấm lại.",
                        )
                    return BotState.DUST_MATERIALS
                dust_fail_count = 0
                skipped_pts.clear()
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
            if self._dust_interrupted():
                return BotState.DUST_MATERIALS
            if self.input.is_adb_mode:
                # Thu hẹp quãng vuốt ADB & tăng thời gian để triệt tiêu quán tính trôi lố trên LDPlayer
                mid_x = (x1 + x2) // 2
                ax1 = int(mid_x + (x1 - mid_x) * 0.62)
                ax2 = int(mid_x + (x2 - mid_x) * 0.62)
                self.input.smooth_drag(ax1, y1, ax2, y2, duration=max(0.55, drag_dur * 1.45), hold_end=0.18, stop_check=self._dust_interrupted)
            else:
                self.input.smooth_drag(
                    x1, y1, x2, y2, duration=drag_dur, hold_end=hold_end, stop_check=self._dust_interrupted
                )
            if not self.input.sleep_interruptible(settle, self._dust_interrupted):
                return BotState.DUST_MATERIALS

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
        self.log(f"=== BẮT ĐẦU DaoRong_tool v{CURRENT_VERSION} [{self.account_label}] | {mode_lbl} | {ctrl_lbl} ===")
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
            self.log(f"=== ĐÃ DỪNG DaoRong_tool v{CURRENT_VERSION} [{self.account_label}] ===")
