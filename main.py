"""
main.py - DaoRong_tool v10.3 (Giao diện Studio 2026 — Gọn, Không Bóp Chữ, Dễ Dùng Tuyệt Đối)
- Thiết kế Top Navigation Bar hiện đại (loại bỏ sidebar trái cồng kềnh -> thêm +185px không gian ngang!).
- Khung gọn 920x620 (thu nhỏ tới 840x560 vẫn thoáng đẹp, không cắt bất kỳ nút hay chữ nào).
- Trang Luật Rồng dùng 5-tab chuyển đổi mượt mà + thanh AI Spotlight gõ tiếng Việt tự nhiên.
- Nút '?' hướng dẫn tại chỗ cho từng tính năng + Bảng Điều Khiển vừa khít 1 màn hình không cần cuộn.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog
from typing import Any, Dict, List, Optional

import customtkinter as ctk

try:
    import keyboard
except ImportError:
    keyboard = None

try:
    import winsound
except ImportError:
    winsound = None

from adb_controller import AdbController
from notifier import AlertNotifier
from yolo_vision import YoloLevel2Vision
from bot_engine import BotStats, DaoRongBot
from capture_tool import capture_region_from_bgr, capture_region_overlay, pick_point_overlay, select_box_overlay
from ocr_engine import DragonTextMatcher, format_rule_string, load_dragon_catalog, normalize_vn
from smart_ai import AnchorAutoCalibrator, NaturalRuleParser, PairStatsTracker, PersistentNameCache
from updater import (
    CURRENT_VERSION,
    DEFAULT_MANIFEST_URL,
    check_for_update,
    download_and_prepare_update,
    launch_updater_and_exit,
)
from vision import VisionEngine, enable_windows_dpi_awareness

BASE_DIR = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)
CONFIG_PATH = BASE_DIR / "config.json"
TEMPLATES_DIR = BASE_DIR / "templates"

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# Bảng màu Studio 2026 (Obsidian Dark — tương phản cao, tiết chế màu sắc, chuyên nghiệp)
C = {
    "bg": "#090B10",
    "topbar": "#0E121B",
    "card": "#121723",
    "card2": "#1A2233",
    "console": "#0A0D14",
    "border": "#232D42",
    "text": "#F1F5F9",
    "sub": "#94A3B8",
    "muted": "#64748B",
    "accent": "#38BDF8",
    "primary": "#2563EB",
    "primary_hv": "#1D4ED8",
    "green": "#10B981",
    "green_hv": "#059669",
    "amber": "#F59E0B",
    "red": "#F43F5E",
    "red_hv": "#E11D48",
    "purple": "#8B5CF6",
}

ACCURACY_PRESETS = {
    "Thoáng": {"button": 0.70, "nav_button": 0.68, "dragon": 0.70, "dragon_name": 0.80, "bag_full": 0.72, "nguyen_lieu": 0.66},
    "Chuẩn": {"button": 0.76, "nav_button": 0.72, "dragon": 0.75, "dragon_name": 0.85, "bag_full": 0.78, "nguyen_lieu": 0.70},
    "Chặt": {"button": 0.82, "nav_button": 0.78, "dragon": 0.82, "dragon_name": 0.90, "bag_full": 0.84, "nguyen_lieu": 0.76},
}

REQUIRED_UI_TEMPLATES = [
    ("btn_egg.png", "Quả trứng ở đảo lai", True),
    ("btn_start_breed.png", "Nút 'Lai Rồng'", True),
    ("btn_keep.png", "Nút 'Cất rồng'", True),
    ("btn_bless.png", "Nút 'Chúc phúc' (màu xanh lá)", True),
    ("btn_sell.png", "Nút 'Bán rồng'", True),
    ("btn_sell_confirm.png", "Nút xác nhận bán", True),
    ("msg_bag_full.png", "Thông báo 'Túi rồng đã đầy'", True),
    ("btn_close_x.png", "Dấu X đóng bảng Lai", True),
    ("btn_arrow_right.png", "Mũi tên chuyển đảo >", True),
    ("btn_arrow_left.png", "Mũi tên quay lại đảo <", True),
    ("btn_altar_center.png", "Tháp giữa đảo Tẩy Tủy", False),
    ("btn_dust_bottle.png", "Cái bình Hóa Bụi", True),
    ("dragon_nguyen_lieu.png", "Chữ 'Nguyên liệu' góc thẻ", True),
    ("btn_dust.png", "Nút 'Hóa Bụi'", True),
    ("btn_dust_confirm.png", "Nút 'Xác nhận' Hóa Bụi", True),
    ("btn_close_dust_x.png", "Dấu X đóng bảng Hóa Bụi", False),
    ("btn_keep_after_bless.png", "Nút 'Cất rồng' sau chúc phúc", False),
    ("icon_dragon_locked.png", "Ổ khóa trắng (né rồng khóa khi Hóa Bụi)", False),
    ("btn_home_island.png", "Nút Về Đảo màn phụ", False),
    ("btn_home_worldmap.png", "Nút Về Đảo World Map (nhà vàng)", False),
    ("slot_parent_empty.png", "Ô bố mẹ trống (kiểm chứng chống chọn nhầm)", False),
]


def ensure_directories() -> None:
    for sub in ["ui", "parents", "keep", "sell", "bless", "dust_targets", "protect_dust"]:
        (TEMPLATES_DIR / sub).mkdir(parents=True, exist_ok=True)


def load_config() -> Dict[str, Any]:
    cfg: Dict[str, Any] = {}
    if CONFIG_PATH.is_file():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    cfg = loaded
        except Exception:
            try:
                CONFIG_PATH.replace(CONFIG_PATH.with_suffix(".json.corrupt.bak"))
            except Exception:
                pass
            cfg = {}
    if not isinstance(cfg.get("dragon_text_rules"), dict):
        cfg["dragon_text_rules"] = {"bless": [], "keep": [], "sell": []}
    for k in ("bless", "keep", "sell"):
        if not isinstance(cfg["dragon_text_rules"].get(k), list):
            cfg["dragon_text_rules"][k] = []
    for dict_k in ("drag", "timing", "timings", "auto_stop", "confidence"):
        if dict_k in cfg and not isinstance(cfg.get(dict_k), dict):
            cfg[dict_k] = {}
    cfg.setdefault("accuracy_mode", "Chuẩn")
    cfg.setdefault("bot_mode", "breed_and_dust")
    cfg.setdefault("control_mode", "windows")
    cfg.setdefault("dust_rarities", ["Nguyên Liệu"])
    cfg.setdefault("dust_only_session_bred", True)
    cfg.setdefault("human_like_input", True)
    cfg.setdefault("human_break_enabled", True)
    cfg.setdefault("save_rare_snapshots", True)
    cfg.setdefault("sound_alert", True)
    cfg.setdefault("verbose_log", False)
    # Tự động nâng cấp thông số vuốt thanh rồng & khung quét cho người dùng nâng từ bản cũ (< v11.0)
    # mà vẫn giữ nguyên 100% luật rồng, cặp bố mẹ, ghim tọa độ và hồ sơ!
    try:
        _schema_v = int(cfg.get("config_schema_version", 0) or 0)
    except (TypeError, ValueError):
        _schema_v = 0
    if _schema_v < 12:
        drag = cfg.setdefault("drag", {})
        drag["bottom_bar_roi"] = [0.15, 0.62, 0.61, 0.89]
        drag["bottom_drag_start"] = [0.44, 0.75]
        drag["bottom_drag_end"] = [0.30, 0.75]
        drag["drag_duration"] = 0.62
        drag["drag_hold_end"] = 0.14
        drag["result_name_roi"] = [0.57, 0.285, 0.78, 0.348]
        timing = cfg.setdefault("timing", {})
        timing["scroll_settle_delay"] = 0.38
        cfg["config_schema_version"] = 12
    # v12.1: Khôi phục ui_reference_window_size về chuẩn [1330, 761] của bộ nút UI tích hợp sẵn
    # (tránh lỗi bấm 'Lưu cỡ giả lập' khi cửa sổ nhỏ làm phóng đại sai toàn bộ 22 nút UI!)
    if _schema_v < 13:
        cfg["ui_reference_window_size"] = [1330, 761]
        cfg["config_schema_version"] = 13
        try:
            save_config(cfg)
        except Exception:
            pass
    return cfg


def save_config(cfg: Dict[str, Any]) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


class DaoRongStudioV10(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        enable_windows_dpi_awareness()
        ensure_directories()

        self.title(f"DaoRong_tool v{CURRENT_VERSION}")
        self.geometry("940x630")
        self.minsize(840, 560)
        self.configure(fg_color=C["bg"])

        ico_file = BASE_DIR / "app_icon.ico"
        if ico_file.is_file():
            try:
                self.iconbitmap(str(ico_file))
            except Exception:
                pass

        self.config_data = load_config()
        self.catalog = load_dragon_catalog(BASE_DIR)
        self.bot: Optional[DaoRongBot] = None
        self.multi_bots: Dict[str, DaoRongBot] = {}
        self.adb_ctrl = AdbController(BASE_DIR, self.config_data.get("adb_path", ""))
        self.adb_device_vars: Dict[str, tk.BooleanVar] = {}
        self.adb_device_status_lbls: Dict[str, ctk.CTkLabel] = {}
        self.adb_device_btns: Dict[str, ctk.CTkButton] = {}

        self.shared_text_matcher = DragonTextMatcher(catalog=self.catalog)
        self.shared_name_cache = PersistentNameCache(TEMPLATES_DIR / "auto_learned")
        self.shared_calibrator = AnchorAutoCalibrator()
        self.shared_pair_stats = PairStatsTracker(BASE_DIR / "pair_stats.json")
        self.rule_parser = NaturalRuleParser(self.catalog)
        self.shared_yolo = YoloLevel2Vision(BASE_DIR / "models")

        self._mini_hud: Optional[ctk.CTkToplevel] = None
        self._hud_lbl_status: Optional[ctk.CTkLabel] = None
        self._hud_lbl_kpi: Optional[ctk.CTkLabel] = None
        self._pending_update_info = None
        self._last_notified_update_ver = ""
        self._log_line_count = 0

        self._pages: Dict[str, ctk.CTkFrame] = {}
        self._nav_btns: Dict[str, ctk.CTkButton] = {}
        self._ui_status_lbls: Dict[str, ctk.CTkLabel] = {}
        self._stat_lbls: Dict[str, ctk.CTkLabel] = {}
        self._active_rule_tab = "bless"

        self._build_layout()
        self._refresh_all_lists()
        self._show_page("dashboard")
        self._register_hotkeys()
        self._poll_emulator_status()
        if self.config_data.get("auto_check_update", True):
            self.after(3000, self._periodic_update_check)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # Helper UI: Nút '?' hướng dẫn tại chỗ
    # ------------------------------------------------------------------
    def _help_btn(self, parent, title: str, guide_text: str):
        return ctk.CTkButton(
            parent, text="?", width=20, height=20, corner_radius=10,
            fg_color=C["card2"], hover_color=C["primary"], text_color=C["accent"],
            font=ctk.CTkFont("Segoe UI", 10, "bold"),
            command=lambda t=title, g=guide_text: messagebox.showinfo(f"Hướng dẫn: {t}", g, parent=self),
        )

    # ------------------------------------------------------------------
    # KHUNG GIAO DIỆN STUDIO 2026: Top Navigation Bar + Full-Width Content
    # ------------------------------------------------------------------
    def _build_layout(self) -> None:
        # 1. TOP NAVIGATION BAR (54px — không chiếm chiều ngang như sidebar!)
        topbar = ctk.CTkFrame(self, height=54, corner_radius=0, fg_color=C["topbar"])
        topbar.pack(side="top", fill="x")
        topbar.pack_propagate(False)
        ctk.CTkFrame(self, height=1, fg_color=C["border"]).pack(side="top", fill="x")

        # Brand trái
        brand_box = ctk.CTkFrame(topbar, fg_color="transparent")
        brand_box.pack(side="left", padx=(16, 12))
        ctk.CTkLabel(
            brand_box, text="ĐẢO RỒNG", font=ctk.CTkFont("Segoe UI", 16, "bold"), text_color=C["accent"]
        ).pack(side="left")
        ctk.CTkLabel(
            brand_box, text=f"v{CURRENT_VERSION}", font=ctk.CTkFont("Consolas", 11, "bold"),
            fg_color=C["card2"], text_color=C["sub"], corner_radius=6, padx=6, pady=1
        ).pack(side="left", padx=(7, 0))

        # 3 Pill Navigation ở giữa-trái
        nav_pill = ctk.CTkFrame(topbar, corner_radius=10, fg_color=C["bg"], border_width=1, border_color=C["border"])
        nav_pill.pack(side="left", padx=8, pady=9)
        for key, label in [
            ("dashboard", "Điều Khiển"),
            ("dragons", "Luật Rồng (AI)"),
            ("settings", "Cài Đặt"),
        ]:
            b = ctk.CTkButton(
                nav_pill, text=label, width=108, height=30, corner_radius=8,
                fg_color="transparent", hover_color=C["card2"], text_color=C["sub"],
                font=ctk.CTkFont("Segoe UI", 12, "bold"),
                command=lambda k=key: self._show_page(k),
            )
            b.pack(side="left", padx=2, pady=2)
            self._nav_btns[key] = b

        # Cụm tiện ích bên phải Topbar: Trạng thái giả lập + Kiểm tra + Mini-HUD + Cập nhật
        right_tools = ctk.CTkFrame(topbar, fg_color="transparent")
        right_tools.pack(side="right", padx=14)

        self.btn_check_update = ctk.CTkButton(
            right_tools, text="Cập nhật", width=74, height=30, corner_radius=8,
            fg_color=C["card2"], hover_color=C["primary"], text_color=C["text"],
            font=ctk.CTkFont("Segoe UI", 11, "bold"),
            command=lambda: self._check_update_async(silent=False),
        )
        self.btn_check_update.pack(side="right", padx=(6, 0))

        ctk.CTkButton(
            right_tools, text="Mini-HUD", width=76, height=30, corner_radius=8,
            fg_color=C["card2"], hover_color=C["border"], text_color=C["accent"],
            font=ctk.CTkFont("Segoe UI", 11, "bold"), command=self._toggle_mini_hud,
        ).pack(side="right", padx=(6, 0))

        ctk.CTkButton(
            right_tools, text="Kiểm tra", width=72, height=30, corner_radius=8,
            fg_color=C["card2"], hover_color=C["border"], text_color=C["green"],
            font=ctk.CTkFont("Segoe UI", 11, "bold"), command=self._run_preflight_ui,
        ).pack(side="right", padx=(6, 0))

        self.lbl_ld_status = ctk.CTkLabel(
            right_tools, text="● Đang tìm giả lập...", font=ctk.CTkFont("Segoe UI", 11, "bold"),
            text_color=C["amber"],
        )
        self.lbl_ld_status.pack(side="right", padx=(0, 8))

        # 2. THANH THÔNG BÁO CẬP NHẬT (ẨN MẶC ĐỊNH)
        self.update_banner = ctk.CTkFrame(self, corner_radius=0, fg_color="#451A03")
        self.lbl_update_banner = ctk.CTkLabel(
            self.update_banner, text="", font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color="#FDE68A"
        )
        self.lbl_update_banner.pack(side="left", padx=16, pady=6)
        ctk.CTkButton(
            self.update_banner, text="✕", width=26, height=24, corner_radius=6,
            fg_color="transparent", hover_color="#78350F", text_color="#FDE68A",
            command=lambda: self.update_banner.pack_forget(),
        ).pack(side="right", padx=(4, 12), pady=5)
        ctk.CTkButton(
            self.update_banner, text="Cập nhật ngay", width=110, height=26, corner_radius=6,
            fg_color=C["amber"], hover_color="#D97706", text_color="#451A03",
            font=ctk.CTkFont("Segoe UI", 11, "bold"), command=self._apply_pending_update,
        ).pack(side="right", pady=5)

        # 3. KHU NỘI DUNG CHÍNH (TOÀN BỘ CHIỀU NGANG!)
        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.pack(fill="both", expand=True, padx=16, pady=12)

        self._build_page_dashboard()
        self._build_page_dragons()
        self._build_page_settings()

    def _show_page(self, key: str) -> None:
        for k, page in self._pages.items():
            if k == key:
                page.pack(fill="both", expand=True)
            else:
                page.pack_forget()
        for k, btn in self._nav_btns.items():
            if k == key:
                btn.configure(fg_color=C["primary"], text_color="#FFFFFF")
            else:
                btn.configure(fg_color="transparent", text_color=C["sub"])
        if key == "settings":
            self._refresh_pair_stats_ui()

    # ------------------------------------------------------------------
    # TRANG 1: BẢNG ĐIỀU KHIỂN (VỪA KHÍT 1 MÀN HÌNH, KHÔNG BÓP CHỮ)
    # ------------------------------------------------------------------
    def _build_page_dashboard(self) -> None:
        page = ctk.CTkFrame(self.content, fg_color="transparent")
        self._pages["dashboard"] = page

        # --- HÀNG 1: HERO ACTION BAR + KPI STRIP ---
        hero = ctk.CTkFrame(page, corner_radius=12, fg_color=C["card"], border_width=1, border_color=C["border"])
        hero.pack(fill="x", pady=(0, 10))

        hero_top = ctk.CTkFrame(hero, fg_color="transparent")
        hero_top.pack(fill="x", padx=14, pady=10)

        self.btn_play_pause = ctk.CTkButton(
            hero_top, text="▶  BẮT ĐẦU (Ctrl+Enter)", width=185, height=38, corner_radius=9,
            fg_color=C["green"], hover_color=C["green_hv"], text_color="#052E16",
            font=ctk.CTkFont("Segoe UI", 12, "bold"), command=self.on_toggle_start_stop,
        )
        self.btn_play_pause.pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            hero_top, text="↻ Chạy lại", width=95, height=38, corner_radius=9,
            fg_color=C["card2"], hover_color=C["primary"], text_color=C["text"],
            font=ctk.CTkFont("Segoe UI", 12, "bold"), command=self.on_restart_from_egg,
        ).pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            hero_top, text="■ Dừng (ESC)", width=102, height=38, corner_radius=9,
            fg_color="#3B0D16", hover_color=C["red"], text_color="#FDA4AF",
            font=ctk.CTkFont("Segoe UI", 12, "bold"), command=self.on_stop_all,
        ).pack(side="left", padx=(0, 12))

        self.lbl_badge = ctk.CTkLabel(
            hero_top, text=" ● Sẵn sàng ", corner_radius=12, fg_color="#064E3B", text_color="#6EE7B7",
            font=ctk.CTkFont("Segoe UI", 11, "bold"), height=28, padx=8,
        )
        self.lbl_badge.pack(side="right")

        self.lbl_state = ctk.CTkLabel(
            hero_top, text="Sẵn sàng", font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color=C["accent"]
        )
        self.lbl_state.pack(side="right", padx=(0, 10))

        # KPI Strip gọn nằm ngay đáy Hero Card (không chiếm thêm hàng card riêng)
        ctk.CTkFrame(hero, height=1, fg_color=C["border"]).pack(fill="x", padx=14)
        kpi_bar = ctk.CTkFrame(hero, fg_color="transparent")
        kpi_bar.pack(fill="x", padx=14, pady=7)
        for idx, (k, title, col) in enumerate([
            ("breeds", "ĐÃ LAI", C["accent"]),
            ("kept", "ĐÃ CẤT", C["green"]),
            ("blessed", "CHÚC PHÚC", C["amber"]),
            ("sold", "ĐÃ BÁN", C["red"]),
            ("dusted", "HÓA BỤI", C["purple"]),
            ("rare_total", "RỒNG QUÝ ★", "#FACC15"),
        ]):
            cell = ctk.CTkFrame(kpi_bar, fg_color="transparent")
            cell.grid(row=0, column=idx, sticky="ew")
            kpi_bar.columnconfigure(idx, weight=1)
            ctk.CTkLabel(cell, text=title, font=ctk.CTkFont("Segoe UI", 10, "bold"), text_color=C["muted"]).pack(side="left", padx=(0, 6))
            lbl = ctk.CTkLabel(cell, text="0", font=ctk.CTkFont("Segoe UI", 15, "bold"), text_color=col)
            lbl.pack(side="left")
            self._stat_lbls[k] = lbl

        # --- HÀNG 2: CHIA 2 CỘT CÂN ĐỐI (TRÁI 62% CẤU HÌNH CHẠY | PHẢI 38% RỒNG QUÝ) ---
        mid = ctk.CTkFrame(page, fg_color="transparent")
        mid.pack(fill="x", pady=(0, 10))
        mid.columnconfigure(0, weight=62)
        mid.columnconfigure(1, weight=38)

        # Cột trái: Thẻ Cấu hình chạy nhanh
        cfg_card = ctk.CTkFrame(mid, corner_radius=12, fg_color=C["card"], border_width=1, border_color=C["border"])
        cfg_card.grid(row=0, column=0, sticky="nsew", padx=(0, 10))

        # Dòng 1: Chế độ chạy + Điều khiển
        r1 = ctk.CTkFrame(cfg_card, fg_color="transparent")
        r1.pack(fill="x", padx=12, pady=(10, 6))
        self._mode_to_vn = {"breed_and_dust": "Lai + Hóa Bụi", "breed_only": "Chỉ Auto Lai"}
        self._mode_from_vn = {v: k for k, v in self._mode_to_vn.items()}
        self.var_bot_mode_vn = tk.StringVar(value=self._mode_to_vn.get(self.config_data.get("bot_mode", "breed_and_dust"), "Lai + Hóa Bụi"))
        ctk.CTkLabel(r1, text="Chế độ:", font=ctk.CTkFont("Segoe UI", 11, "bold"), text_color=C["sub"]).pack(side="left", padx=(0, 6))
        ctk.CTkSegmentedButton(
            r1, values=["Lai + Hóa Bụi", "Chỉ Auto Lai"], variable=self.var_bot_mode_vn,
            height=28, corner_radius=7, font=ctk.CTkFont("Segoe UI", 11, "bold"),
            command=lambda _v: self._on_mode_changed(),
        ).pack(side="left", padx=(0, 12))

        self._ctrl_to_vn = {"windows": "Chuột Thường", "adb": "Chạy Ngầm ADB"}
        self._ctrl_from_vn = {v: k for k, v in self._ctrl_to_vn.items()}
        self.var_ctrl_mode_vn = tk.StringVar(value=self._ctrl_to_vn.get(self.config_data.get("control_mode", "windows"), "Chuột Thường"))
        ctk.CTkSegmentedButton(
            r1, values=["Chuột Thường", "Chạy Ngầm ADB"], variable=self.var_ctrl_mode_vn,
            height=28, corner_radius=7, font=ctk.CTkFont("Segoe UI", 11, "bold"),
            command=lambda _v: self._on_ctrl_mode_changed(),
        ).pack(side="left")
        self._help_btn(
            r1, "Chế độ chạy & Chạy Ngầm ADB",
            "• Lai + Hóa Bụi: đầy túi tự sang đảo Tẩy Tủy hóa bụi (né rồng khóa 🔒).\n"
            "• Chỉ Auto Lai: săn rồng hiếm / làm nhiệm vụ, đầy túi tự dừng & báo chuông.\n"
            "• Chạy Ngầm ADB: bật Gỡ lỗi ADB 1 lần trong LDPlayer để treo ngầm không chiếm chuột và chạy nhiều acc cùng lúc!"
        ).pack(side="right")

        # Khung chọn nhiều thiết bị ADB (chỉ hiện khi chọn Chạy Ngầm ADB)
        self.adb_panel = ctk.CTkFrame(cfg_card, corner_radius=8, fg_color=C["console"], border_width=1, border_color=C["border"])
        adb_hdr = ctk.CTkFrame(self.adb_panel, fg_color="transparent")
        adb_hdr.pack(fill="x", padx=10, pady=(6, 3))
        ctk.CTkLabel(adb_hdr, text="Thiết bị ADB (chạy song song không chiếm chuột):", font=ctk.CTkFont("Segoe UI", 11, "bold"), text_color=C["green"]).pack(side="left")
        ctk.CTkButton(adb_hdr, text="Quét ADB", width=76, height=24, corner_radius=6, fg_color=C["primary"], font=ctk.CTkFont("Segoe UI", 10, "bold"), command=self._scan_adb_devices).pack(side="right")
        self.adb_list_frame = ctk.CTkFrame(self.adb_panel, fg_color="transparent")
        self.adb_list_frame.pack(fill="x", padx=10, pady=(0, 6))

        # Dòng 2: Chọn 2 rồng bố mẹ
        r2 = ctk.CTkFrame(cfg_card, fg_color="transparent")
        r2.pack(fill="x", padx=12, pady=4)
        ctk.CTkLabel(r2, text="Bố/Mẹ:", font=ctk.CTkFont("Segoe UI", 11, "bold"), text_color=C["sub"]).pack(side="left", padx=(0, 6))
        self.var_p1 = tk.StringVar(value=self.config_data.get("selected_parent_1", "") or "(Chưa có mẫu)")
        self.combo_p1 = ctk.CTkOptionMenu(r2, variable=self.var_p1, values=["(Chưa có mẫu)"], width=165, height=28, corner_radius=7, fg_color=C["console"], button_color=C["card2"], font=ctk.CTkFont("Segoe UI", 11))
        self.combo_p1.pack(side="left", padx=(0, 6))

        self.var_p2 = tk.StringVar(value=self.config_data.get("selected_parent_2", "") or "(Chưa có mẫu)")
        self.combo_p2 = ctk.CTkOptionMenu(r2, variable=self.var_p2, values=["(Chưa có mẫu)"], width=165, height=28, corner_radius=7, fg_color=C["console"], button_color=C["card2"], font=ctk.CTkFont("Segoe UI", 11))
        self.combo_p2.pack(side="left", padx=(0, 6))

        ctk.CTkButton(
            r2, text="+ Chụp", width=64, height=28, corner_radius=7, fg_color=C["primary"], hover_color=C["primary_hv"],
            font=ctk.CTkFont("Segoe UI", 11, "bold"),
            command=lambda: self._capture_dragon_template("parents", "Rồng Bố/Mẹ", ""),
        ).pack(side="left", padx=(0, 4))
        ctk.CTkButton(
            r2, text="↻", width=28, height=28, corner_radius=7, fg_color=C["card2"], text_color=C["amber"],
            command=self._reset_parent_selection,
        ).pack(side="left")

        # Dòng 3: Tốc độ + Rồng chưa có luật
        r3 = ctk.CTkFrame(cfg_card, fg_color="transparent")
        r3.pack(fill="x", padx=12, pady=(4, 10))
        self._spd_to_vn = {"fast": "Nhanh", "balanced": "Vừa", "safe": "Chậm"}
        self._spd_from_vn = {v: k for k, v in self._spd_to_vn.items()}
        self.var_speed_vn = tk.StringVar(value=self._spd_to_vn.get(self.config_data.get("speed_preset", "fast"), "Nhanh"))
        ctk.CTkLabel(r3, text="Tốc độ:", font=ctk.CTkFont("Segoe UI", 11, "bold"), text_color=C["sub"]).pack(side="left", padx=(0, 6))
        ctk.CTkSegmentedButton(r3, values=["Nhanh", "Vừa", "Chậm"], variable=self.var_speed_vn, height=26, corner_radius=7, font=ctk.CTkFont("Segoe UI", 11)).pack(side="left", padx=(0, 14))

        self._unk_map_to_vn = {"keep": "Cất", "pause": "Dừng", "sell": "Bán"}
        self._unk_map_from_vn = {v: k for k, v in self._unk_map_to_vn.items()}
        self.var_unknown_vn = tk.StringVar(value=self._unk_map_to_vn.get(self.config_data.get("unknown_dragon_action", "keep"), "Cất"))
        ctk.CTkLabel(r3, text="Rồng lạ:", font=ctk.CTkFont("Segoe UI", 11, "bold"), text_color=C["sub"]).pack(side="left", padx=(0, 6))
        ctk.CTkSegmentedButton(r3, values=["Cất", "Dừng", "Bán"], variable=self.var_unknown_vn, height=26, corner_radius=7, font=ctk.CTkFont("Segoe UI", 11)).pack(side="left")

        # Cột phải: Rồng Quý Vừa Nổ (+ 2 nút cài mục tiêu nhanh cho người mới)
        rare_card = ctk.CTkFrame(mid, corner_radius=12, fg_color=C["card"], border_width=1, border_color=C["border"])
        rare_card.grid(row=0, column=1, sticky="nsew")
        rhdr = ctk.CTkFrame(rare_card, fg_color="transparent")
        rhdr.pack(fill="x", padx=12, pady=(8, 4))
        ctk.CTkLabel(rhdr, text="★ RỒNG QUÝ VỪA NỔ", font=ctk.CTkFont("Segoe UI", 11, "bold"), text_color="#FACC15").pack(side="left")
        ctk.CTkButton(rhdr, text="Cày Bụi", width=62, height=22, corner_radius=6, fg_color="#064E3B", hover_color="#047857", font=ctk.CTkFont("Segoe UI", 10, "bold"), command=lambda: self._quick_goal_setup("dust")).pack(side="right", padx=(4, 0))
        ctk.CTkButton(rhdr, text="Săn Hiếm", width=66, height=22, corner_radius=6, fg_color="#78350F", hover_color="#92400E", font=ctk.CTkFont("Segoe UI", 10, "bold"), command=lambda: self._quick_goal_setup("hunt")).pack(side="right")

        self.lb_rare_drops = tk.Listbox(
            rare_card, bg=C["console"], fg="#FDE047", font=("Segoe UI", 9),
            relief=tk.FLAT, highlightthickness=0, height=5,
        )
        self.lb_rare_drops.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self._on_mode_changed()
        self._on_ctrl_mode_changed()

        # --- HÀNG 3: NHẬT KÝ HOẠT ĐỘNG (FULL WIDTH, SẠCH ĐẸP) ---
        log_card = ctk.CTkFrame(page, corner_radius=12, fg_color=C["card"], border_width=1, border_color=C["border"])
        log_card.pack(fill="both", expand=True)

        lhdr = ctk.CTkFrame(log_card, fg_color="transparent")
        lhdr.pack(fill="x", padx=12, pady=(8, 4))
        self.lbl_speed_rate = ctk.CTkLabel(
            lhdr, text="NHẬT KÝ HOẠT ĐỘNG  •  0.0 lượt/phút",
            font=ctk.CTkFont("Segoe UI", 11, "bold"), text_color=C["sub"],
        )
        self.lbl_speed_rate.pack(side="left")
        ctk.CTkButton(lhdr, text="Xóa", width=52, height=24, corner_radius=6, fg_color=C["card2"], command=self._clear_log).pack(side="right")
        ctk.CTkButton(lhdr, text="Sao chép", width=70, height=24, corner_radius=6, fg_color=C["card2"], command=self._copy_log).pack(side="right", padx=(0, 6))

        log_wrap = ctk.CTkFrame(log_card, corner_radius=8, fg_color=C["console"])
        log_wrap.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.txt_log = tk.Text(
            log_wrap, state=tk.DISABLED, bg=C["console"], fg=C["text"],
            font=("Consolas", 9), relief=tk.FLAT, wrap=tk.WORD, padx=10, pady=8, highlightthickness=0,
        )
        self.txt_log.tag_config("ok", foreground="#34D399")
        self.txt_log.tag_config("warn", foreground="#FBBF24")
        self.txt_log.tag_config("err", foreground="#FB7185")
        self.txt_log.tag_config("info", foreground="#38BDF8")
        sb = ctk.CTkScrollbar(log_wrap, command=self.txt_log.yview)
        self.txt_log.configure(yscrollcommand=sb.set)
        self.txt_log.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y", padx=2, pady=2)

    # ------------------------------------------------------------------
    # TRANG 2: LUẬT RỒNG (AI SPOTLIGHT + 5-TAB KHÔNG BAO GIỜ CẮT CHỮ)
    # ------------------------------------------------------------------
    def _build_page_dragons(self) -> None:
        page = ctk.CTkFrame(self.content, fg_color="transparent")
        self._pages["dragons"] = page

        # Thanh AI Spotlight ở trên cùng
        ai_card = ctk.CTkFrame(page, corner_radius=12, fg_color="#0F1B33", border_width=1, border_color=C["primary"])
        ai_card.pack(fill="x", pady=(0, 10))

        ai_top = ctk.CTkFrame(ai_card, fg_color="transparent")
        ai_top.pack(fill="x", padx=12, pady=(8, 4))
        ctk.CTkLabel(ai_top, text="AI CÀI LUẬT BẰNG TIẾNG VIỆT (100% offline)", font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color=C["accent"]).pack(side="left")
        self._help_btn(
            ai_top, "AI Cài Luật Tiếng Việt",
            "Gõ 1 câu tiếng Việt tự nhiên về hành động (Bán / Cất / Chúc phúc / Hóa bụi toàn bộ) + tên rồng hoặc độ hiếm + số sao.\n\n"
            "Ví dụ:\n• Bán hết phổ biến, cất nguyên liệu, giữ hiếm và cực hiếm\n• Cất và hóa bụi toàn bộ rồng lai ra, chỉ chúc phúc Rồng Lửa Xanh Biển (24 sao)"
        ).pack(side="left", padx=(6, 0))

        ctk.CTkButton(ai_top, text="Thử đọc rồng", width=95, height=24, corner_radius=6, fg_color=C["card2"], hover_color=C["primary"], font=ctk.CTkFont("Segoe UI", 10, "bold"), command=self._test_read_live_dragon).pack(side="right", padx=(6, 0))
        ctk.CTkButton(ai_top, text="Mẫu: Săn 24★", width=92, height=24, corner_radius=6, fg_color=C["card2"], font=ctk.CTkFont("Segoe UI", 10), command=lambda: self.var_ai_rule.set("Cất và hóa bụi toàn bộ rồng lai ra, chỉ chúc phúc và giữ Rồng Lửa Xanh Biển (24 sao)")).pack(side="right", padx=(6, 0))
        ctk.CTkButton(ai_top, text="Mẫu: Cày bụi", width=90, height=24, corner_radius=6, fg_color=C["card2"], font=ctk.CTkFont("Segoe UI", 10), command=lambda: self.var_ai_rule.set("Bán hết phổ biến, cất nguyên liệu, giữ toàn bộ hiếm, cực hiếm và event")).pack(side="right")

        ai_in = ctk.CTkFrame(ai_card, fg_color="transparent")
        ai_in.pack(fill="x", padx=12, pady=(2, 10))
        self.var_ai_rule = tk.StringVar(value="")
        ctk.CTkEntry(
            ai_in, textvariable=self.var_ai_rule, height=34, corner_radius=8, fg_color=C["console"],
            placeholder_text="Vd: 'Cất và hóa bụi toàn bộ rồng lai ra, chỉ chúc phúc Rồng Lửa Xanh Biển (24 sao)'...",
        ).pack(side="left", fill="x", expand=True, padx=(0, 8))
        ctk.CTkButton(
            ai_in, text="Phân tích & Áp dụng", width=145, height=34, corner_radius=8,
            fg_color=C["primary"], hover_color=C["primary_hv"], font=ctk.CTkFont("Segoe UI", 12, "bold"),
            command=self._apply_ai_natural_rule,
        ).pack(side="right")

        # Chia 2 cột đều 50/50
        split = ctk.CTkFrame(page, fg_color="transparent")
        split.pack(fill="both", expand=True)
        split.columnconfigure(0, weight=1)
        split.columnconfigure(1, weight=1)
        split.rowconfigure(0, weight=1)

        # ---- CỘT TRÁI: THƯ VIỆN 63 LOÀI RỒNG ----
        left_card = ctk.CTkFrame(split, corner_radius=12, fg_color=C["card"], border_width=1, border_color=C["border"])
        left_card.grid(row=0, column=0, sticky="nsew", padx=(0, 10))

        self.var_search_dragon = tk.StringVar(value="")
        self.var_search_dragon.trace_add("write", lambda *_a: self._filter_catalog_list())
        ctk.CTkEntry(
            left_card, textvariable=self.var_search_dragon, height=30, corner_radius=8,
            fg_color=C["console"], placeholder_text="Tìm trong 63 loài rồng (vd: lua xanh bien, ky lan)...",
        ).pack(fill="x", padx=10, pady=(10, 6))

        self.var_filter_rarity = tk.StringVar(value="Tất cả")
        ctk.CTkSegmentedButton(
            left_card, values=["Tất cả", "Phổ biến", "Nguyên Liệu", "Hiếm", "Cực hiếm", "Event"],
            variable=self.var_filter_rarity, command=lambda _v: self._filter_catalog_list(),
            font=ctk.CTkFont("Segoe UI", 10, "bold"), height=26, corner_radius=7,
        ).pack(fill="x", padx=10, pady=(0, 6))

        self.lb_catalog = tk.Listbox(
            left_card, bg=C["console"], fg=C["text"], selectbackground=C["primary"],
            selectmode=tk.EXTENDED, borderwidth=0, highlightthickness=1, highlightbackground=C["border"],
            font=("Segoe UI", 10),
        )
        self.lb_catalog.pack(fill="both", expand=True, padx=10, pady=(0, 8))

        add_bar = ctk.CTkFrame(left_card, corner_radius=8, fg_color=C["card2"])
        add_bar.pack(fill="x", padx=10, pady=(0, 10))

        ra = ctk.CTkFrame(add_bar, fg_color="transparent")
        ra.pack(fill="x", padx=8, pady=(6, 4))
        self.var_add_target = tk.StringVar(value="Chúc phúc")
        ctk.CTkSegmentedButton(ra, values=["Chúc phúc", "Cất", "Bán"], variable=self.var_add_target, height=26, corner_radius=6).pack(side="left", fill="x", expand=True)

        rs = ctk.CTkFrame(add_bar, fg_color="transparent")
        rs.pack(fill="x", padx=8, pady=(0, 6))
        self.var_star_op = tk.StringVar(value="Mọi sao")
        ctk.CTkSegmentedButton(rs, values=["Mọi sao", ">= sao", "= sao"], variable=self.var_star_op, width=160, height=26, corner_radius=6).pack(side="left", padx=(0, 6))
        self.var_star_num = tk.StringVar(value="23")
        ctk.CTkEntry(rs, textvariable=self.var_star_num, width=44, height=26, corner_radius=6, fg_color=C["console"]).pack(side="left", padx=(0, 6))
        ctk.CTkButton(rs, text="+ Thêm luật", height=26, corner_radius=6, fg_color=C["green"], hover_color=C["green_hv"], text_color="#052E16", font=ctk.CTkFont("Segoe UI", 11, "bold"), command=self._add_selected_catalog_dragons).pack(side="left", fill="x", expand=True)

        # ---- CỘT PHẢI: 5-TAB LUẬT ĐANG ÁP DỤNG (RỘNG RÃI, KHÔNG CẮT CHỮ) ----
        right_card = ctk.CTkFrame(split, corner_radius=12, fg_color=C["card"], border_width=1, border_color=C["border"])
        right_card.grid(row=0, column=1, sticky="nsew")

        ctk.CTkLabel(right_card, text="DANH SÁCH LUẬT ĐANG ÁP DỤNG", font=ctk.CTkFont("Segoe UI", 11, "bold"), text_color=C["sub"]).pack(anchor="w", padx=12, pady=(10, 6))

        self.var_rule_tab = tk.StringVar(value="Chúc phúc")
        self.seg_rule_tabs = ctk.CTkSegmentedButton(
            right_card, values=["Bố/Mẹ", "Chúc phúc", "Cất", "Bán", "Cấm Bụi"],
            variable=self.var_rule_tab, command=lambda _v: self._refresh_active_rule_view(),
            height=28, corner_radius=7, font=ctk.CTkFont("Segoe UI", 11, "bold"),
        )
        self.seg_rule_tabs.pack(fill="x", padx=10, pady=(0, 6))

        self.lbl_rule_summary = ctk.CTkLabel(right_card, text="", font=ctk.CTkFont("Segoe UI", 11), text_color=C["accent"], anchor="w")
        self.lbl_rule_summary.pack(fill="x", padx=12, pady=(0, 4))

        self.lb_active_rules = tk.Listbox(
            right_card, bg=C["console"], fg=C["text"], selectbackground=C["primary"],
            borderwidth=0, highlightthickness=1, highlightbackground=C["border"], font=("Segoe UI", 10),
        )
        self.lb_active_rules.pack(fill="both", expand=True, padx=10, pady=(0, 8))

        r_actions = ctk.CTkFrame(right_card, fg_color="transparent")
        r_actions.pack(fill="x", padx=10, pady=(0, 10))
        ctk.CTkButton(r_actions, text="+ Chụp mẫu", width=105, height=30, corner_radius=7, fg_color=C["primary"], hover_color=C["primary_hv"], font=ctk.CTkFont("Segoe UI", 11, "bold"), command=self._capture_for_active_rule_tab).pack(side="left", padx=(0, 6))
        ctk.CTkButton(r_actions, text="+ Tên mới", width=85, height=30, corner_radius=7, fg_color=C["card2"], hover_color=C["border"], font=ctk.CTkFont("Segoe UI", 11), command=self._add_custom_name_from_picker).pack(side="left", padx=(0, 6))
        ctk.CTkButton(r_actions, text="Xóa chọn", width=80, height=30, corner_radius=7, fg_color="#3B0D16", hover_color=C["red"], text_color="#FDA4AF", font=ctk.CTkFont("Segoe UI", 11, "bold"), command=self._delete_active_rule_item).pack(side="right")
        ctk.CTkButton(r_actions, text="Dọn nhóm", width=80, height=30, corner_radius=7, fg_color=C["card2"], hover_color="#7F1D1D", text_color=C["sub"], font=ctk.CTkFont("Segoe UI", 11), command=self._clear_active_rule_group).pack(side="right", padx=(0, 6))

        self._filter_catalog_list()

    def _rule_tab_to_key(self) -> str:
        return {"Bố/Mẹ": "parents", "Chúc phúc": "bless", "Cất": "keep", "Bán": "sell", "Cấm Bụi": "protect_dust"}.get(self.var_rule_tab.get(), "bless")

    def _refresh_active_rule_view(self) -> None:
        if not hasattr(self, "lb_active_rules"):
            return
        key = self._rule_tab_to_key()
        self.lb_active_rules.delete(0, tk.END)
        cnt = 0
        if key in ("bless", "keep", "sell"):
            for rule in self.config_data.get("dragon_text_rules", {}).get(key, []):
                self.lb_active_rules.insert(tk.END, f"[TÊN] {rule}")
                cnt += 1
        if key == "protect_dust":
            self.lb_active_rules.insert(tk.END, "[BẢO VỆ] Chụp ảnh thẻ rồng cũ quý vào đây -> Tool CẤM hóa bụi 100%!")
        for p in sorted((TEMPLATES_DIR / key).iterdir()):
            if p.suffix.lower() in {".png", ".jpg", ".jpeg"}:
                self.lb_active_rules.insert(tk.END, f"[ẢNH] {p.name}" if key != "parents" else p.name)
                cnt += 1
        rules_all = self.config_data.get("dragon_text_rules", {})
        self.lbl_rule_summary.configure(
            text=f"Tổng: Chúc phúc ({len(rules_all.get('bless', []))})  •  Cất ({len(rules_all.get('keep', []))})  •  Bán ({len(rules_all.get('sell', []))})"
        )

    def _capture_for_active_rule_tab(self) -> None:
        key = self._rule_tab_to_key()
        prefix = "ten_" if key in ("bless", "keep", "sell") else ""
        self._capture_dragon_template(key, self.var_rule_tab.get(), prefix)

    def _delete_active_rule_item(self) -> None:
        sel = self.lb_active_rules.curselection()
        if not sel:
            return
        item = self.lb_active_rules.get(sel[0])
        if item.startswith("[MẶC ĐỊNH]"):
            return
        key = self._rule_tab_to_key()
        if item.startswith("[TÊN] "):
            rule_txt = item[6:]
            rules = self.config_data.get("dragon_text_rules", {}).get(key, [])
            if rule_txt in rules:
                rules.remove(rule_txt)
                save_config(self.config_data)
        else:
            fname = item[6:] if item.startswith("[ẢNH] ") else item
            (TEMPLATES_DIR / key / fname).unlink(missing_ok=True)
        self._refresh_all_lists()

    def _clear_active_rule_group(self) -> None:
        key = self._rule_tab_to_key()
        if key not in ("bless", "keep", "sell"):
            return
        if not messagebox.askyesno("Dọn nhóm luật", f"Xóa toàn bộ quy tắc tên trong nhóm '{self.var_rule_tab.get()}'?"):
            return
        self.config_data.setdefault("dragon_text_rules", {}).setdefault(key, []).clear()
        save_config(self.config_data)
        self._refresh_all_lists()
        self.append_log(f"🧹 Đã dọn sạch nhóm '{self.var_rule_tab.get()}'.")

    # ------------------------------------------------------------------
    # TRANG 3: CÀI ĐẶT & TIỆN ÍCH (4 TAB + THANH LƯU CỐ ĐỊNH)
    # ------------------------------------------------------------------
    def _build_page_settings(self) -> None:
        page = ctk.CTkFrame(self.content, fg_color="transparent")
        self._pages["settings"] = page

        save_footer = ctk.CTkFrame(page, fg_color="transparent")
        save_footer.pack(side="bottom", fill="x", pady=(8, 0))
        ctk.CTkButton(
            save_footer, text="Lưu toàn bộ cài đặt", width=180, height=36, corner_radius=8,
            fg_color=C["green"], hover_color=C["green_hv"], text_color="#052E16",
            font=ctk.CTkFont("Segoe UI", 12, "bold"), command=self._save_all_settings,
        ).pack(side="left")
        ctk.CTkLabel(
            save_footer, text="Chế độ chạy ở trang Điều Khiển được tự động lưu ngầm.",
            font=ctk.CTkFont("Segoe UI", 11), text_color=C["muted"],
        ).pack(side="right")

        tabs = ctk.CTkTabview(page, corner_radius=12, fg_color=C["card"], segmented_button_fg_color=C["console"])
        tabs.pack(fill="both", expand=True)
        tab_basic = tabs.add("Cơ Bản")
        tab_adv = tabs.add("Treo 24/7 & Thông Báo")
        tab_stats = tabs.add("Thống Kê Cặp Lai")
        tab_guide = tabs.add("Hướng Dẫn")

        # --- TAB 1: CƠ BẢN ---
        sc_b = ctk.CTkScrollableFrame(tab_basic, fg_color="transparent")
        sc_b.pack(fill="both", expand=True)

        self.var_win_title = tk.StringVar(value=self.config_data.get("window_title_keyword", "AUTO"))
        self.var_hops = tk.StringVar(value=str(self.config_data.get("island_hops", 2)))
        self.var_accuracy = tk.StringVar(value=self.config_data.get("accuracy_mode", "Chuẩn"))
        self.var_dust_max_star = tk.StringVar(value=str(self.config_data.get("dust_max_star", 0)))
        self.var_watchdog_sec = tk.StringVar(value=str(self.config_data.get("watchdog_seconds", 180)))
        self.var_adb_path = tk.StringVar(value=self.config_data.get("adb_path", ""))

        self.var_color_check = tk.BooleanVar(value=bool(self.config_data.get("use_color_check", True)))
        self.var_auto_scale = tk.BooleanVar(value=bool(self.config_data.get("auto_scale_templates", True)))
        self.var_enable_ocr = tk.BooleanVar(value=bool(self.config_data.get("enable_text_ocr", True)))
        self.var_auto_update = tk.BooleanVar(value=bool(self.config_data.get("auto_check_update", True)))
        self.var_dust_only_session = tk.BooleanVar(value=bool(self.config_data.get("dust_only_session_bred", True)))
        self.var_human_like = tk.BooleanVar(value=bool(self.config_data.get("human_like_input", True)))
        self.var_human_break = tk.BooleanVar(value=bool(self.config_data.get("human_break_enabled", True)))
        self.var_save_rare = tk.BooleanVar(value=bool(self.config_data.get("save_rare_snapshots", True)))
        self.var_sound_alert = tk.BooleanVar(value=bool(self.config_data.get("sound_alert", True)))
        self.var_verbose_log = tk.BooleanVar(value=bool(self.config_data.get("verbose_log", False)))
        self.var_watchdog_relaunch = tk.BooleanVar(value=bool(self.config_data.get("watchdog_relaunch", True)))
        self.var_remote_notify = tk.BooleanVar(value=bool(self.config_data.get("enable_remote_notify", False)))
        self.var_dc_webhook = tk.StringVar(value=str(self.config_data.get("discord_webhook", "")))

        # Nhóm Hóa Bụi An Toàn
        c1 = ctk.CTkFrame(sc_b, corner_radius=10, fg_color=C["console"], border_width=1, border_color=C["border"])
        c1.pack(fill="x", pady=(0, 8))

        r_st = ctk.CTkFrame(c1, fg_color="transparent")
        r_st.pack(fill="x", padx=12, pady=(10, 6))
        ctk.CTkLabel(r_st, text="Lọc sao Hóa Bụi tối đa:", font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color=C["text"]).pack(side="left", padx=(0, 8))
        ctk.CTkEntry(r_st, textvariable=self.var_dust_max_star, width=70, fg_color=C["card"]).pack(side="left", padx=(0, 8))
        ctk.CTkLabel(r_st, text="0 = tất cả (trừ khóa 🔒) | Vd: 23 = chỉ hóa bụi <= 23★", text_color=C["sub"], font=ctk.CTkFont("Segoe UI", 11)).pack(side="left")
        self._help_btn(r_st, "Khiên Bảo Vệ Rồng Sao Cao", "Khi đặt số > 0 (vd 23), tool chỉ hóa bụi thẻ <= 23 sao. Nếu game mờ không đọc chắc số sao, tool tự BỎ QUA để bảo vệ rồng 24–25 sao an toàn 100%!").pack(side="right")

        r_dr = ctk.CTkFrame(c1, fg_color="transparent")
        r_dr.pack(fill="x", padx=12, pady=(0, 10))
        ctk.CTkLabel(r_dr, text="Độ hiếm Hóa Bụi:", font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color=C["text"]).pack(side="left", padx=(0, 10))
        saved_dr = set(self.config_data.get("dust_rarities", ["Nguyên Liệu"]))
        self.var_dust_rars: Dict[str, tk.BooleanVar] = {}
        for r_lbl in ("Phổ biến", "Nguyên Liệu", "Hiếm", "Cực hiếm", "Event"):
            v = tk.BooleanVar(value=(r_lbl in saved_dr))
            self.var_dust_rars[r_lbl] = v
            ctk.CTkCheckBox(r_dr, text=r_lbl, variable=v, width=90, font=ctk.CTkFont("Segoe UI", 11)).pack(side="left", padx=(0, 6))
        r_led = ctk.CTkFrame(c1, fg_color="transparent")
        r_led.pack(fill="x", padx=12, pady=(0, 10))
        ctk.CTkSwitch(r_led, text="Sổ Tay Rồng Vừa Lai: CHỈ hóa bụi đúng những con vừa lai ra trong phiên (né 100% rồng cũ có sẵn!)", variable=self.var_dust_only_session, font=ctk.CTkFont("Segoe UI", 11, "bold")).pack(side="left")

        # Nhóm Công tắc An Toàn & Tiện Ích
        c2 = ctk.CTkFrame(sc_b, corner_radius=10, fg_color=C["console"], border_width=1, border_color=C["border"])
        c2.pack(fill="x", pady=(0, 8))
        sw_wrap = ctk.CTkFrame(c2, fg_color="transparent")
        sw_wrap.pack(fill="x", padx=12, pady=8)
        for text_sw, var_sw in [
            ("Thao tác chuột tự nhiên như người thật (đường cong + lệch điểm ngẫu nhiên)", self.var_human_like),
            ("Nghỉ giải lao tự nhiên (tự nghỉ 1–2 phút sau mỗi 45 phút treo máy)", self.var_human_break),
            ("Tự chụp lưu ảnh khi nổ Cực hiếm / Event / Chúc phúc (giới hạn 30 ảnh)", self.var_save_rare),
            ("Phát tiếng chuông & hiện thông báo nổi khi đầy túi / nổ rồng mục tiêu", self.var_sound_alert),
            ("Tự động kiểm tra bản cập nhật mới", self.var_auto_update),
        ]:
            ctk.CTkSwitch(sw_wrap, text=text_sw, variable=var_sw, font=ctk.CTkFont("Segoe UI", 11)).pack(anchor="w", pady=3)

        # Hẹn giờ & Tự dừng
        c3 = ctk.CTkFrame(sc_b, corner_radius=10, fg_color=C["console"], border_width=1, border_color=C["border"])
        c3.pack(fill="x", pady=(0, 8))
        as_cfg = self.config_data.get("auto_stop", {})
        self.var_as_enabled = tk.BooleanVar(value=bool(as_cfg.get("enabled", False)))
        self.var_as_mins = tk.StringVar(value=str(as_cfg.get("max_minutes", 0)))
        self.var_as_breeds = tk.StringVar(value=str(as_cfg.get("max_breeds", 0)))
        self.var_as_rare = tk.StringVar(value=str(as_cfg.get("stop_on_rare_count", 0)))
        self.var_as_epic = tk.BooleanVar(value=bool(as_cfg.get("stop_on_epic_or_event", False)))

        as_h = ctk.CTkFrame(c3, fg_color="transparent")
        as_h.pack(fill="x", padx=12, pady=(8, 4))
        ctk.CTkLabel(as_h, text="HẸN GIỜ & TỰ DỪNG MỤC TIÊU", font=ctk.CTkFont("Segoe UI", 11, "bold"), text_color=C["amber"]).pack(side="left")
        ctk.CTkSwitch(as_h, text="Bật", variable=self.var_as_enabled).pack(side="right")

        as_r = ctk.CTkFrame(c3, fg_color="transparent")
        as_r.pack(fill="x", padx=12, pady=(2, 8))
        ctk.CTkLabel(as_r, text="Phút:", text_color=C["sub"]).pack(side="left", padx=(0, 4))
        ctk.CTkEntry(as_r, textvariable=self.var_as_mins, width=60, fg_color=C["card"]).pack(side="left", padx=(0, 12))
        ctk.CTkLabel(as_r, text="Lượt lai:", text_color=C["sub"]).pack(side="left", padx=(0, 4))
        ctk.CTkEntry(as_r, textvariable=self.var_as_breeds, width=60, fg_color=C["card"]).pack(side="left", padx=(0, 12))
        ctk.CTkLabel(as_r, text="Rồng Hiếm+:", text_color=C["sub"]).pack(side="left", padx=(0, 4))
        ctk.CTkEntry(as_r, textvariable=self.var_as_rare, width=60, fg_color=C["card"]).pack(side="left", padx=(0, 12))
        ctk.CTkCheckBox(as_r, text="Dừng ngay khi nổ Cực hiếm / Event", variable=self.var_as_epic, font=ctk.CTkFont("Segoe UI", 11)).pack(side="left")


        # --- TAB 2: TREO 24/7 & THÔNG BÁO (Gọn sạch, chỉ giữ tính năng cho người dùng) ---
        sc_a = ctk.CTkScrollableFrame(tab_adv, fg_color="transparent")
        sc_a.pack(fill="both", expand=True)

        adv = ctk.CTkFrame(sc_a, corner_radius=10, fg_color=C["console"], border_width=1, border_color=C["border"])
        adv.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(adv, text="GIẢ LẬP & TREO MÁY 24/7", font=ctk.CTkFont("Segoe UI", 11, "bold"), text_color=C["accent"]).pack(anchor="w", padx=12, pady=(8, 2))
        ag = ctk.CTkFrame(adv, fg_color="transparent")
        ag.pack(fill="x", padx=12, pady=6)
        ctk.CTkLabel(ag, text="Giả lập:", font=ctk.CTkFont("Segoe UI", 11, "bold")).grid(row=0, column=0, sticky="w", pady=4, padx=(0, 8))
        ctk.CTkComboBox(ag, variable=self.var_win_title, values=["AUTO", "LDPlayer", "BlueStacks", "MuMu", "Nox", "MEmu"], width=140, fg_color=C["card"]).grid(row=0, column=1, sticky="w", pady=4, padx=(0, 14))
        ctk.CTkLabel(ag, text="Số đảo Hóa Bụi:", font=ctk.CTkFont("Segoe UI", 11, "bold")).grid(row=0, column=2, sticky="w", pady=4, padx=(0, 8))
        ctk.CTkEntry(ag, textvariable=self.var_hops, width=65, fg_color=C["card"]).grid(row=0, column=3, sticky="w", pady=4)

        ctk.CTkLabel(ag, text="File adb.exe:", font=ctk.CTkFont("Segoe UI", 11, "bold")).grid(row=1, column=0, sticky="w", pady=4, padx=(0, 8))
        ar = ctk.CTkFrame(ag, fg_color="transparent")
        ar.grid(row=1, column=1, columnspan=3, sticky="w", pady=4)
        ctk.CTkEntry(ar, textvariable=self.var_adb_path, width=250, fg_color=C["card"], placeholder_text="Để trống = tự tìm").pack(side="left", padx=(0, 6))
        ctk.CTkButton(ar, text="Chọn file", width=78, height=28, fg_color=C["card2"], command=self._browse_adb_exe).pack(side="left")

        asw = ctk.CTkFrame(adv, fg_color="transparent")
        asw.pack(fill="x", padx=12, pady=(2, 6))
        ctk.CTkSwitch(asw, text="Watchdog 24/7: Tự mở lại game qua ADB nếu bị văng ra màn hình chính", variable=self.var_watchdog_relaunch, font=ctk.CTkFont("Segoe UI", 11)).pack(anchor="w", pady=2)

        amr = ctk.CTkFrame(adv, fg_color="transparent")
        amr.pack(fill="x", padx=12, pady=(4, 10))
        ai_lbl = "● AI Thị Giác Thông Minh: Đang hoạt động" if self.shared_yolo.ready else "● AI Thị Giác: Chế độ chuẩn"
        self.lbl_ai_cache_info = ctk.CTkLabel(amr, text=f"{ai_lbl}  •  Đã nhớ {self.shared_name_cache.count} rồng", font=ctk.CTkFont("Segoe UI", 11, "bold"), text_color=C["green"] if self.shared_yolo.ready else C["accent"])
        self.lbl_ai_cache_info.pack(side="left")
        ctk.CTkButton(amr, text="Dọn bộ nhớ tên", width=108, height=26, fg_color="#3B0D16", hover_color=C["red"], text_color="#FDA4AF", command=self._clear_ai_cache).pack(side="right", padx=(6, 0))
        ctk.CTkButton(amr, text="Ảnh Rồng Quý", width=104, height=26, fg_color="#064E3B", command=self._open_rare_shots_folder).pack(side="right")

        # Thẻ Thông Báo Qua Discord (tự lưu ngay khi dán link hoặc bật/tắt)
        ntf = ctk.CTkFrame(sc_a, corner_radius=10, fg_color=C["console"], border_width=1, border_color=C["border"])
        ntf.pack(fill="x", pady=(0, 8))
        nh = ctk.CTkFrame(ntf, fg_color="transparent")
        nh.pack(fill="x", padx=12, pady=(8, 4))
        ctk.CTkLabel(nh, text="THÔNG BÁO QUA DISCORD", font=ctk.CTkFont("Segoe UI", 11, "bold"), text_color=C["accent"]).pack(side="left")
        ctk.CTkButton(nh, text="Gửi thử", width=74, height=24, corner_radius=6, fg_color=C["primary"], font=ctk.CTkFont("Segoe UI", 11, "bold"), command=self._test_remote_notify).pack(side="right")
        ctk.CTkSwitch(
            ntf,
            text="Gửi ảnh chụp về Discord khi nổ rồng Chúc phúc / Cực hiếm / Event hoặc khi lỗi dừng",
            variable=self.var_remote_notify,
            command=lambda: self._save_all_settings(silent=True),
            font=ctk.CTkFont("Segoe UI", 11),
        ).pack(anchor="w", padx=12, pady=(0, 6))
        ng = ctk.CTkFrame(ntf, fg_color="transparent")
        ng.pack(fill="x", padx=12, pady=(0, 10))
        ctk.CTkLabel(ng, text="Discord Webhook:", font=ctk.CTkFont("Segoe UI", 11, "bold")).grid(row=0, column=0, sticky="w", pady=3, padx=(0, 8))
        dc_entry = ctk.CTkEntry(ng, textvariable=self.var_dc_webhook, width=460, fg_color=C["card"], placeholder_text="https://discord.com/api/webhooks/...")
        dc_entry.grid(row=0, column=1, sticky="w", pady=3)
        dc_entry.bind("<FocusOut>", lambda _e: self._save_all_settings(silent=True))

        # --- TAB 3: THỐNG KÊ CẶP LAI ---
        sh = ctk.CTkFrame(tab_stats, fg_color="transparent")
        sh.pack(fill="x", padx=6, pady=(4, 6))
        ctk.CTkLabel(sh, text="Hiệu suất nổ rồng theo từng cặp bố mẹ (file cố định ~10KB)", font=ctk.CTkFont("Segoe UI", 11, "bold"), text_color=C["accent"]).pack(side="left")
        ctk.CTkButton(sh, text="Xóa", width=65, height=26, fg_color="#3B0D16", hover_color=C["red"], text_color="#FDA4AF", command=self._clear_pair_stats).pack(side="right", padx=(6, 0))
        ctk.CTkButton(sh, text="Làm mới", width=80, height=26, fg_color=C["primary"], command=self._refresh_pair_stats_ui).pack(side="right")
        self.txt_pair_stats = tk.Text(tab_stats, state=tk.DISABLED, bg=C["console"], fg=C["text"], font=("Consolas", 10), relief=tk.FLAT, padx=12, pady=10, highlightthickness=0)
        self.txt_pair_stats.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        self._refresh_pair_stats_ui()

        # --- TAB 4: HƯỚNG DẪN ---
        sc_g = ctk.CTkScrollableFrame(tab_guide, fg_color="transparent")
        sc_g.pack(fill="both", expand=True)
        for title, body in [
            ("1. Bắt đầu nhanh trong 30 giây",
             "• Mở game Đảo Rồng trên LDPlayer → vào bảng Lai Rồng.\n"
             "• Ở trang Điều Khiển, bấm '+ Chụp' để chụp gọn 2 rồng bố mẹ ở thanh dưới (tool tự chấm điểm độ khớp ngay).\n"
             "• Bấm nhanh nút 'Cày Bụi' hoặc 'Săn Hiếm' (hoặc vào Luật Rồng gõ 1 câu tiếng Việt cho AI tự cài) → nhấn CTRL + ENTER để chạy!"),
            ("2. Chạy Ngầm & Treo Nhiều Acc không chiếm chuột",
             "• Trong LDPlayer: Cài đặt → Khác → Gỡ lỗi ADB → chọn 'Mở kết nối cục bộ' (chỉ bật 1 lần).\n"
             "• Chọn 'Chạy Ngầm ADB' trên trang Điều Khiển → bấm 'Quét ADB' → tick chọn các acc cần chạy rồi bấm chạy. Bạn có thể thu nhỏ hoặc che cửa sổ game và làm việc khác bình thường."),
            ("3. Tạm dừng, Chạy lại & Bảo vệ an toàn",
             "• CTRL+ENTER: Tạm dừng tại chỗ hoặc Tiếp tục đúng bước đang dở.\n"
             "• Nút 'Chạy lại': Tự đóng bảng, thoát Map (bằng nút Về Đảo) và quay về Đảo Trứng.\n"
             "• Phím ESC: Dừng khẩn cấp toàn bộ acc ngay lập tức."),
        ]:
            box = ctk.CTkFrame(sc_g, corner_radius=10, fg_color=C["console"], border_width=1, border_color=C["border"])
            box.pack(fill="x", padx=4, pady=4)
            ctk.CTkLabel(box, text=title, font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color=C["accent"]).pack(anchor="w", padx=12, pady=(8, 3))
            ctk.CTkLabel(box, text=body, font=ctk.CTkFont("Segoe UI", 11), text_color=C["text"], justify="left", wraplength=760).pack(anchor="w", padx=12, pady=(0, 8))

    # ------------------------------------------------------------------
    # LOGIC XỬ LÝ CHẾ ĐỘ, ADB ĐA TÀI KHOẢN, LUẬT RỒNG & AI
    # ------------------------------------------------------------------
    def _on_mode_changed(self) -> None:
        m = self._mode_from_vn.get(self.var_bot_mode_vn.get(), "breed_and_dust")
        self.config_data["bot_mode"] = m
        save_config(self.config_data)

    def _on_ctrl_mode_changed(self) -> None:
        c = self._ctrl_from_vn.get(self.var_ctrl_mode_vn.get(), "windows")
        self.config_data["control_mode"] = c
        save_config(self.config_data)
        if c == "adb":
            self.adb_panel.pack(fill="x", padx=12, pady=(0, 6))
            self._scan_adb_devices()
        else:
            self.adb_panel.pack_forget()

    def _scan_adb_devices(self) -> None:
        for w in self.adb_list_frame.winfo_children():
            w.destroy()
        self.adb_device_vars.clear()
        self.adb_device_status_lbls.clear()
        self.adb_device_btns.clear()
        self.adb_ctrl.custom_adb_path = self.config_data.get("adb_path", "")
        devices = self.adb_ctrl.list_devices(auto_connect_ports=True)
        if not devices:
            ctk.CTkLabel(
                self.adb_list_frame,
                text="Chưa thấy giả lập ADB. Bật Gỡ lỗi ADB trong LDPlayer rồi bấm 'Quét ADB'.",
                font=ctk.CTkFont("Segoe UI", 11), text_color=C["amber"],
            ).pack(anchor="w", pady=2)
            return
        for dev in devices:
            row = ctk.CTkFrame(self.adb_list_frame, fg_color=C["card"], corner_radius=6)
            row.pack(fill="x", pady=2)
            var = tk.BooleanVar(value=True)
            self.adb_device_vars[dev.serial] = var
            ctk.CTkCheckBox(row, text=dev.label, variable=var, font=ctk.CTkFont("Segoe UI", 11, "bold")).pack(side="left", padx=8, pady=3)
            btn_ind = ctk.CTkButton(row, text="▶", width=30, height=22, corner_radius=5, fg_color=C["green"], text_color="#052E16", font=ctk.CTkFont("Segoe UI", 10, "bold"), command=lambda s=dev.serial: self._toggle_single_adb_bot(s))
            btn_ind.pack(side="right", padx=(4, 6), pady=3)
            self.adb_device_btns[dev.serial] = btn_ind
            st = ctk.CTkLabel(row, text="Sẵn sàng", font=ctk.CTkFont("Segoe UI", 10), text_color=C["green"])
            st.pack(side="right", padx=4)
            self.adb_device_status_lbls[dev.serial] = st

    def _filter_catalog_list(self) -> None:
        if not hasattr(self, "lb_catalog"):
            return
        q = normalize_vn(self.var_search_dragon.get())
        rar = self.var_filter_rarity.get()
        self.lb_catalog.delete(0, tk.END)
        self._filtered_catalog = []
        for item in self.catalog:
            if rar != "Tất cả" and item.get("rarity") != rar:
                continue
            if q and q not in normalize_vn(item.get("name", "")) and q not in normalize_vn(item.get("rarity", "")):
                continue
            self._filtered_catalog.append(item)
            self.lb_catalog.insert(tk.END, f"[{item.get('rarity')}]  {item.get('name')}")

    def _add_selected_catalog_dragons(self) -> None:
        sels = self.lb_catalog.curselection()
        if not sels:
            messagebox.showinfo("Chưa chọn rồng", "Hãy bấm chọn ít nhất 1 con rồng trong danh sách bên trái trước nhé!")
            return
        target = {"Chúc phúc": "bless", "Cất": "keep", "Bán": "sell"}.get(self.var_add_target.get(), "keep")
        op_raw = self.var_star_op.get()
        op = ">=" if ">=" in op_raw else ("=" if "=" in op_raw else "any")
        star_val = None
        if op != "any":
            try:
                star_val = int(self.var_star_num.get().strip())
            except ValueError:
                messagebox.showerror("Sai số sao", "Số sao phải là số nguyên (ví dụ: 23).")
                return
        rules = self.config_data.setdefault("dragon_text_rules", {"bless": [], "keep": [], "sell": []}).setdefault(target, [])
        added = 0
        for i in sels:
            name = self._filtered_catalog[i]["name"]
            r_str = format_rule_string(name, op, star_val)
            if r_str not in rules:
                rules.append(r_str)
                added += 1
        save_config(self.config_data)
        self._refresh_all_lists()
        self.append_log(f"Đã thêm {added} rồng vào nhóm [{self.var_add_target.get()}].")

    def _add_custom_name_from_picker(self) -> None:
        key = self._rule_tab_to_key()
        if key not in ("bless", "keep", "sell"):
            key = "keep"
        txt = simpledialog.askstring("Thêm tên rồng mới", "Nhập tên rồng (vd: Rồng Lửa Đỏ hoặc Rồng Lửa Xanh Biển (24 sao)):", parent=self)
        if not txt or not txt.strip():
            return
        rules = self.config_data.setdefault("dragon_text_rules", {}).setdefault(key, [])
        if txt.strip() not in rules:
            rules.append(txt.strip())
            save_config(self.config_data)
            self._refresh_all_lists()

    def _bulk_add_rarity(self, rarity_name: str, target_key: str) -> None:
        rules = self.config_data.setdefault("dragon_text_rules", {}).setdefault(target_key, [])
        for item in self.catalog:
            if item.get("rarity") == rarity_name:
                nm = item["name"]
                if nm not in rules:
                    rules.append(nm)
        save_config(self.config_data)
        self._refresh_all_lists()

    def _quick_goal_setup(self, goal: str) -> None:
        if goal == "dust":
            self.var_bot_mode_vn.set("Lai + Hóa Bụi")
            self._bulk_add_rarity("Phổ biến", "sell")
            for r in ("Nguyên Liệu", "Hiếm", "Cực hiếm", "Event"):
                self._bulk_add_rarity(r, "keep")
            self._on_mode_changed()
            self.append_log("Đã cài nhanh mục tiêu 'Cày Bụi 24/7' (Bán Phổ biến, Cất Nguyên liệu & Rồng quý).")
        else:
            self.var_bot_mode_vn.set("Chỉ Auto Lai")
            self._bulk_add_rarity("Phổ biến", "sell")
            self._bulk_add_rarity("Nguyên Liệu", "sell")
            for r in ("Hiếm", "Cực hiếm", "Event"):
                self._bulk_add_rarity(r, "keep")
            self._on_mode_changed()
            self.append_log("Đã cài nhanh mục tiêu 'Săn Rồng Hiếm' (Bán PB & NL, chỉ giữ rồng quý, không hóa bụi).")

    def _apply_ai_natural_rule(self) -> None:
        prompt = self.var_ai_rule.get().strip()
        if not prompt:
            messagebox.showinfo("AI Cài Luật", "Hãy gõ 1 câu tiếng Việt (hoặc bấm nút Mẫu) trước nhé!")
            return
        parsed = self.rule_parser.parse(prompt)
        if parsed["matched_count"] == 0:
            messagebox.showwarning("Chưa hiểu câu lệnh", "Hãy ghi rõ hành động (Bán / Cất / Chúc phúc) + tên hoặc độ hiếm rồng.")
            return
        preview = "\n".join(parsed["summary"])
        if not messagebox.askyesno("Xác nhận bộ luật AI", f"AI phân tích được {parsed['matched_count']} quy tắc:\n\n{preview}\n\nÁp dụng ngay?"):
            return
        rules = self.config_data.setdefault("dragon_text_rules", {"bless": [], "keep": [], "sell": []})
        for cat in ("bless", "keep", "sell"):
            for r in parsed["rules"][cat]:
                if r not in rules[cat]:
                    rules[cat].append(r)
        if parsed.get("want_dust_all"):
            self.config_data["dust_rarities"] = ["Phổ biến", "Nguyên Liệu", "Hiếm", "Cực hiếm"]
            if hasattr(self, "var_dust_rars"):
                for rk, v in self.var_dust_rars.items():
                    v.set(rk != "Event")
            if parsed["rules"]["bless"] and int(self.config_data.get("dust_max_star", 0) or 0) == 0:
                stars_found = [int(m) for r_s in parsed["rules"]["bless"] for m in re.findall(r"(\d{1,2})\s*sao", r_s)]
                if stars_found:
                    safe_max = max(1, min(stars_found) - 1)
                    self.config_data["dust_max_star"] = safe_max
                    if hasattr(self, "var_dust_max_star"):
                        self.var_dust_max_star.set(str(safe_max))
                    self.append_log(f"🛡️ [AI An Toàn] Đã bật Hóa Bụi 4 độ hiếm (tự chừa Event) + Khiên bảo vệ chỉ hóa bụi <= {safe_max}★!")
        save_config(self.config_data)
        self._refresh_all_lists()
        self.append_log(f"✨ [AI Luật Rồng] Đã áp dụng {parsed['matched_count']} quy tắc!")

    def _test_read_live_dragon(self) -> None:
        self._save_all_settings(silent=True)
        try:
            ve = VisionEngine(
                self.config_data.get("window_title_keyword", "AUTO"),
                reference_size=tuple(self.config_data.get("ui_reference_window_size", [1330, 761])),
            )
            screen, rect = ve.capture_window()
            roi = self.config_data.get("drag_settings", {}).get("result_name_roi", [0.57, 0.292, 0.77, 0.342])
            crop, _, _ = ve.crop_roi(screen, rect, roi)
            raw = self.shared_text_matcher.read_name_crop(crop)
            if not raw:
                messagebox.showwarning("Thử đọc rồng", "Chưa thấy chữ ở vùng tên phải. Hãy mở bảng kết quả lai trên giả lập rồi thử lại!")
                return
            tm = self.shared_text_matcher.match_text_against_rules(raw, self.config_data.get("dragon_text_rules", {}))
            act_map = {"bless": "CHÚC PHÚC & CẤT", "keep": "CẤT VÀO TÚI", "sell": "BÁN NGAY"}
            act_str = act_map.get(tm.category, self.var_unknown_vn.get()) if tm.found else f"Chưa có luật -> {self.var_unknown_vn.get()}"
            st_str = f"{tm.stars} sao" if tm.stars else "Chưa rõ sao"
            messagebox.showinfo("Kết Quả Thử Đọc", f"• Đọc thô: {raw}\n• Nhận diện: {tm.identified_name or 'Chưa rõ'} [{tm.identified_rarity or '?'}] — {st_str}\n• Hành động: {act_str}")
        except Exception as exc:
            messagebox.showerror("Lỗi thử đọc", str(exc))

    # ------------------------------------------------------------------
    # CHỤP MẪU, CĂN CHỈNH VÙNG & KIỂM TRA NHANH
    # ------------------------------------------------------------------
    def _ensure_ref_size(self, is_ui: bool = False) -> None:
        key = "ui_reference_window_size" if is_ui else "dragon_reference_window_size"
        cur = self.config_data.get(key, [0, 0])
        if not cur or cur[0] < 200:
            rect = VisionEngine(self.config_data.get("window_title_keyword", "AUTO")).get_target_window()
            if rect.found_window:
                self.config_data[key] = [rect.width, rect.height]
                save_config(self.config_data)

    def _record_reference_size(self) -> None:
        rect = VisionEngine(self.config_data.get("window_title_keyword", "AUTO")).get_target_window()
        if rect.found_window:
            # Chỉ lưu cỡ chuẩn cho ảnh RỒNG người dùng tự chụp; GIỮ NGUYÊN [1330, 761] cho bộ nút UI tích hợp sẵn!
            self.config_data["dragon_reference_window_size"] = [rect.width, rect.height]
            save_config(self.config_data)
            self.append_log(f"Đã lưu kích thước giả lập chuẩn: {rect.width}×{rect.height}.")
        else:
            messagebox.showwarning("Chưa thấy giả lập", "Hãy mở cửa sổ LDPlayer/BlueStacks trước khi bấm lưu kích thước.")

    def _pick_active_adb_serial(self) -> str:
        for s, v in self.adb_device_vars.items():
            if v.get():
                return s
        devs = self.adb_ctrl.list_devices(auto_connect_ports=False)
        return devs[0].serial if devs else ""

    def _capture_dragon_template(self, folder_key: str, title: str, prefix: str = "") -> None:
        self._save_all_settings(silent=True)
        name = simpledialog.askstring("Đặt tên mẫu", f"Nhập tên cho mẫu [{title}]:", parent=self)
        if not name or not name.strip():
            return
        safe = "".join(ch for ch in name.strip() if ch.isalnum() or ch in (" ", "_", "-", "đ", "Đ")).strip()
        if not safe:
            return
        out_path = TEMPLATES_DIR / folder_key / f"{prefix}{safe}.png"

        # Nếu đang ở chế độ ADB chạy ngầm -> chụp thẳng từ khung hình ADB chuẩn 1:1 (khớp 99-100%, không lệch tỷ lệ Windows!)
        use_adb_cap = self.config_data.get("control_mode", "windows") == "adb"
        adb_serial = self._pick_active_adb_serial() if use_adb_cap else ""
        adb_bgr = self.adb_ctrl.capture_screen(adb_serial) if adb_serial else None

        if adb_bgr is not None and adb_bgr.size > 0:
            ah, aw = adb_bgr.shape[:2]

            def on_done_adb(saved_p: Path) -> None:
                if not saved_p or not Path(saved_p).is_file():
                    return
                self.config_data["dragon_reference_window_size"] = [aw, ah]
                self.config_data["dragon_capture_source"] = "adb"
                self.config_data.setdefault("parent_template_meta", {})[out_path.name] = {
                    "source": "adb", "w": aw, "h": ah
                }
                save_config(self.config_data)
                self._refresh_all_lists()
                if folder_key == "parents":
                    ve = VisionEngine(
                        self.config_data.get("window_title_keyword", "AUTO"),
                        dragon_reference_size=(aw, ah),
                        adb=self.adb_ctrl,
                        adb_serial=adb_serial,
                        dragon_capture_source="adb",
                        parent_template_meta=self.config_data.get("parent_template_meta", {}),
                    )
                    _sc, verdict = ve.test_template_quality(out_path)
                    self.append_log(f"Đã lưu bố/mẹ ADB 1:1 '{safe}' ({aw}×{ah}) — {verdict}")
                else:
                    self.append_log(f"Đã lưu mẫu ADB 1:1 '{safe}' vào [{folder_key}].")

            capture_region_from_bgr(
                self, adb_bgr, out_path,
                f"Kéo khung chụp {title}: {safe}",
                on_done_adb,
            )
            return

        # Chế độ Chuột chính (Windows)
        self._ensure_ref_size(is_ui=False)

        def on_done_win(saved_p: Path) -> None:
            if not saved_p or not Path(saved_p).is_file():
                return
            rect = VisionEngine(self.config_data.get("window_title_keyword", "AUTO")).get_target_window()
            if rect.found_window:
                self.config_data["dragon_reference_window_size"] = [rect.width, rect.height]
                self.config_data["dragon_capture_source"] = "windows"
                self.config_data.setdefault("parent_template_meta", {})[out_path.name] = {
                    "source": "windows", "w": rect.width, "h": rect.height
                }
                save_config(self.config_data)
            self._refresh_all_lists()
            if folder_key == "parents":
                ve = VisionEngine(
                    self.config_data.get("window_title_keyword", "AUTO"),
                    dragon_reference_size=tuple(self.config_data.get("dragon_reference_window_size", [0, 0])),
                    dragon_capture_source="windows",
                    parent_template_meta=self.config_data.get("parent_template_meta", {}),
                )
                _sc, verdict = ve.test_template_quality(out_path)
                self.append_log(f"Đã lưu bố/mẹ '{safe}' — {verdict}")
            else:
                self.append_log(f"Đã lưu mẫu '{safe}' vào [{folder_key}].")

        capture_region_overlay(self, out_path, f"Kéo khung chụp {title}: {safe}", on_done_win)

    def _capture_ui_template(self, filename: str, desc: str) -> None:
        self._ensure_ref_size(is_ui=True)
        out_path = TEMPLATES_DIR / "ui" / filename
        capture_region_overlay(self, out_path, f"Kéo khung chụp: {desc}", lambda ok: self._refresh_all_lists() if ok else None)

    def _pin_egg_point(self) -> None:
        def on_pick(ax: int, ay: int) -> None:
            rect = VisionEngine(self.config_data.get("window_title_keyword", "AUTO")).get_target_window()
            self.config_data.setdefault("pins", {})["egg_rel"] = list(rect.abs_to_rel(ax, ay))
            save_config(self.config_data)
            self.append_log("Đã ghim tâm Quả Trứng thành công.")
        pick_point_overlay(self, "Click đúng giữa Quả Trứng trên đảo lai", on_pick)

    def _pin_altar_point(self) -> None:
        def on_pick(ax: int, ay: int) -> None:
            rect = VisionEngine(self.config_data.get("window_title_keyword", "AUTO")).get_target_window()
            self.config_data.setdefault("pins", {})["altar_rel"] = list(rect.abs_to_rel(ax, ay))
            save_config(self.config_data)
            self.append_log("Đã ghim tâm Tháp Tẩy Tủy thành công.")
        pick_point_overlay(self, "Click đúng chân/giữa Tháp Tẩy Tủy", on_pick)

    def _save_rel_box(self, key: str, x1: int, y1: int, x2: int, y2: int) -> None:
        rect = VisionEngine(self.config_data.get("window_title_keyword", "AUTO")).get_target_window()
        rx1, ry1 = rect.abs_to_rel(x1, y1)
        rx2, ry2 = rect.abs_to_rel(x2, y2)
        self.config_data.setdefault("drag_settings", {})[key] = [rx1, ry1, rx2, ry2]
        save_config(self.config_data)
        self.append_log(f"Đã lưu vùng [{key}] thành công.")

    def _calibrate_bottom_bar(self) -> None:
        select_box_overlay(self, "Khoanh vùng thanh 4 rồng phía dưới", lambda a, b, c, d: self._save_rel_box("bottom_bar_roi", a, b, c, d))

    def _calibrate_result_zone(self) -> None:
        select_box_overlay(self, "Khoanh vùng hiển thị rồng con sau khi lai", lambda a, b, c, d: self._save_rel_box("result_roi", a, b, c, d))

    def _calibrate_name_bar(self) -> None:
        select_box_overlay(self, "Khoanh gọn dòng Tên + Sao rồng ở góc phải", lambda a, b, c, d: self._save_rel_box("result_name_roi", a, b, c, d))

    def _calibrate_dust_list(self) -> None:
        select_box_overlay(self, "Khoanh vùng 2 cột Túi Chứa Rồng Hóa Bụi", lambda a, b, c, d: self._save_rel_box("dust_list_roi", a, b, c, d))

    def _reset_parent_selection(self) -> None:
        self.config_data["selected_parent_1"] = ""
        self.config_data["selected_parent_2"] = ""
        save_config(self.config_data)
        self.var_p1.set("(Chưa có mẫu)")
        self.var_p2.set("(Chưa có mẫu)")
        self.append_log("Đã đặt lại cặp rồng bố mẹ.")

    def _run_preflight_ui(self) -> None:
        self._save_all_settings(silent=True)
        ctrl = self.config_data.get("control_mode", "windows")
        serial = next((s for s, v in self.adb_device_vars.items() if v.get()), "") if ctrl == "adb" else ""
        tmp = DaoRongBot(
            BASE_DIR, self.config_data, lambda _m: None, lambda _s: None,
            adb=self.adb_ctrl if ctrl == "adb" else None, adb_serial=serial,
            shared_text_matcher=self.shared_text_matcher, shared_name_cache=self.shared_name_cache,
            shared_calibrator=self.shared_calibrator, shared_pair_stats=self.shared_pair_stats,
                shared_yolo=self.shared_yolo,
        )
        checks = tmp.run_preflight_check()
        body = "\n".join(f"{'✅' if ok else '❌'} {t}: {d}" for ok, t, d in checks)
        hdr = "Mọi thứ đã SẴN SÀNG!" if all(ok for ok, _, _ in checks) else "Cần bổ sung mục đánh dấu đỏ:"
        messagebox.showinfo("Kiểm Tra Nhanh", f"{hdr}\n\n{body}", parent=self)

    # ------------------------------------------------------------------
    # PROFILE, MINI-HUD, THỐNG KÊ, DỌN AI & LƯU CÀI ĐẶT
    # ------------------------------------------------------------------
    def _browse_adb_exe(self) -> None:
        fp = filedialog.askopenfilename(title="Chọn file adb.exe", filetypes=[("ADB", "*.exe"), ("Tất cả", "*.*")])
        if fp:
            self.var_adb_path.set(fp)
            self._save_all_settings(silent=True)

    def _open_rare_shots_folder(self) -> None:
        d = BASE_DIR / "lich_su_rong_quy"
        d.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(str(d))

    def _clear_ai_cache(self) -> None:
        n = self.shared_name_cache.clear()
        self.shared_calibrator.clear()
        ai_lbl = "● AI Thị Giác Thông Minh: Đang hoạt động" if self.shared_yolo.ready else "● AI Thị Giác: Chế độ chuẩn"
        self.lbl_ai_cache_info.configure(text=f"{ai_lbl}  •  Đã nhớ 0 rồng")
        self.append_log(f"Đã dọn sạch {n} mẫu trong bộ nhớ AI tự học.")

    def _refresh_pair_stats_ui(self) -> None:
        if not hasattr(self, "txt_pair_stats"):
            return
        self.txt_pair_stats.configure(state=tk.NORMAL)
        self.txt_pair_stats.delete("1.0", tk.END)
        if not self.shared_pair_stats.data:
            self.txt_pair_stats.insert(tk.END, "Chưa có dữ liệu. Chạy vài lượt lai để xem tỷ lệ nổ theo từng cặp bố mẹ tại đây.")
        else:
            self.txt_pair_stats.insert(tk.END, f"{'CẶP RỒNG BỐ MẸ':<38} | {'LAI':>5} | {'NL%':>5} | {'HIẾM%':>6} | {'C.HIẾM%':>7} | {'EV%':>5}\n" + "-" * 78 + "\n")
            for pair, d in sorted(self.shared_pair_stats.data.items(), key=lambda x: x[1].get("breeds", 0), reverse=True):
                tot = max(1, d.get("breeds", 0))
                self.txt_pair_stats.insert(
                    tk.END,
                    f"{pair[:38]:<38} | {tot:>5} | {d.get('material',0)*100/tot:>4.1f}% | {d.get('rare',0)*100/tot:>5.1f}% | {d.get('epic',0)*100/tot:>6.1f}% | {d.get('event',0)*100/tot:>4.1f}%\n",
                )
        self.txt_pair_stats.configure(state=tk.DISABLED)

    def _clear_pair_stats(self) -> None:
        self.shared_pair_stats.clear()
        self._refresh_pair_stats_ui()

    def _toggle_mini_hud(self) -> None:
        if self._mini_hud is not None and self._mini_hud.winfo_exists():
            self._mini_hud.destroy()
            self._mini_hud = None
            self.deiconify()
            return
        self.withdraw()
        hud = ctk.CTkToplevel(self)
        hud.title("DaoRong HUD")
        hud.geometry("340x96+20+20")
        hud.attributes("-topmost", True)
        hud.resizable(False, False)
        hud.configure(fg_color=C["topbar"])
        hud.protocol("WM_DELETE_WINDOW", self._toggle_mini_hud)
        top = ctk.CTkFrame(hud, fg_color="transparent")
        top.pack(fill="x", padx=10, pady=(8, 4))
        self._hud_lbl_status = ctk.CTkLabel(top, text="Sẵn sàng", font=ctk.CTkFont("Segoe UI", 11, "bold"), text_color=C["accent"])
        self._hud_lbl_status.pack(side="left")
        self._hud_lbl_kpi = ctk.CTkLabel(top, text="Lai: 0 | Quý: 0", font=ctk.CTkFont("Segoe UI", 11, "bold"), text_color="#FDE047")
        self._hud_lbl_kpi.pack(side="right")
        btns = ctk.CTkFrame(hud, fg_color="transparent")
        btns.pack(fill="x", padx=10, pady=(2, 8))
        ctk.CTkButton(btns, text="▶ / ⏸", width=70, height=30, fg_color=C["green"], text_color="#052E16", font=ctk.CTkFont("Segoe UI", 11, "bold"), command=self.on_toggle_start_stop).pack(side="left", padx=(0, 6))
        ctk.CTkButton(btns, text="↻ Lại", width=64, height=30, fg_color=C["primary"], font=ctk.CTkFont("Segoe UI", 11, "bold"), command=self.on_restart_from_egg).pack(side="left", padx=(0, 6))
        ctk.CTkButton(btns, text="■ Dừng", width=66, height=30, fg_color=C["red"], font=ctk.CTkFont("Segoe UI", 11, "bold"), command=self.on_stop_all).pack(side="left", padx=(0, 6))
        ctk.CTkButton(btns, text="Mở rộng", width=82, height=30, fg_color=C["card2"], font=ctk.CTkFont("Segoe UI", 11), command=self._toggle_mini_hud).pack(side="right")
        self._mini_hud = hud

    def _refresh_all_lists(self) -> None:
        for fname, lbl in self._ui_status_lbls.items():
            lbl.configure(text="✓" if (TEMPLATES_DIR / "ui" / fname).is_file() else "✕",
                          text_color=C["green"] if (TEMPLATES_DIR / "ui" / fname).is_file() else C["red"])
        self._refresh_active_rule_view()
        parent_files = sorted(p.name for p in (TEMPLATES_DIR / "parents").iterdir() if p.suffix.lower() in {".png", ".jpg", ".jpeg"})
        opts = parent_files if parent_files else ["(Chưa có mẫu)"]
        self.combo_p1.configure(values=opts)
        self.combo_p2.configure(values=opts)
        if self.var_p1.get() not in parent_files:
            self.var_p1.set(parent_files[0] if parent_files else "(Chưa có mẫu)")
        if self.var_p2.get() not in parent_files or (len(parent_files) > 1 and self.var_p2.get() == self.var_p1.get()):
            other = [f for f in parent_files if f != self.var_p1.get()]
            self.var_p2.set(other[0] if other else (parent_files[0] if parent_files else "(Chưa có mẫu)"))

    def _save_all_settings(self, silent: bool = False) -> bool:
        try:
            hops_val = int(self.var_hops.get() or 2)
            dust_max_val = max(0, int(self.var_dust_max_star.get() or 0))
            wd_sec_val = max(0, int(self.var_watchdog_sec.get() or 0))
            as_mins_val = max(0, int(self.var_as_mins.get() or 0))
            as_breeds_val = max(0, int(self.var_as_breeds.get() or 0))
            as_rare_val = max(0, int(self.var_as_rare.get() or 0))
        except ValueError:
            if not silent:
                messagebox.showerror("Sai định dạng", "Các ô số (lọc sao, số đảo, hẹn giờ, watchdog) phải là số nguyên.")
            return False

        self.config_data["window_title_keyword"] = self.var_win_title.get().strip() or "AUTO"
        self.config_data["selected_parent_1"] = "" if self.var_p1.get() == "(Chưa có mẫu)" else self.var_p1.get()
        self.config_data["selected_parent_2"] = "" if self.var_p2.get() == "(Chưa có mẫu)" else self.var_p2.get()
        self.config_data["bot_mode"] = self._mode_from_vn.get(self.var_bot_mode_vn.get(), "breed_and_dust")
        self.config_data["control_mode"] = self._ctrl_from_vn.get(self.var_ctrl_mode_vn.get(), "windows")
        self.config_data["unknown_dragon_action"] = self._unk_map_from_vn.get(self.var_unknown_vn.get(), "keep")
        self.config_data["speed_preset"] = self._spd_from_vn.get(self.var_speed_vn.get(), "fast")
        self.config_data["island_hops"] = hops_val
        self.config_data["dust_max_star"] = dust_max_val
        self.config_data["dust_rarities"] = [k for k, v in self.var_dust_rars.items() if v.get()] or ["Nguyên Liệu"]
        self.config_data["dust_only_session_bred"] = bool(self.var_dust_only_session.get())
        self.config_data["watchdog_seconds"] = wd_sec_val
        self.config_data["adb_path"] = self.var_adb_path.get().strip()
        self.adb_ctrl.custom_adb_path = self.config_data["adb_path"]
        self.config_data["use_color_check"] = True
        self.config_data["auto_scale_templates"] = True
        self.config_data["enable_text_ocr"] = True
        self.config_data["auto_check_update"] = bool(self.var_auto_update.get())
        self.config_data["human_like_input"] = bool(self.var_human_like.get())
        self.config_data["human_break_enabled"] = bool(self.var_human_break.get())
        self.config_data["save_rare_snapshots"] = bool(self.var_save_rare.get())
        self.config_data["sound_alert"] = bool(self.var_sound_alert.get())
        self.config_data["verbose_log"] = bool(self.var_verbose_log.get())
        self.config_data["watchdog_relaunch"] = bool(self.var_watchdog_relaunch.get())
        self.config_data["enable_remote_notify"] = bool(self.var_remote_notify.get())
        self.config_data["discord_webhook"] = self.var_dc_webhook.get().strip()
        self.config_data["auto_stop"] = {
            "enabled": bool(self.var_as_enabled.get()),
            "max_minutes": as_mins_val,
            "max_breeds": as_breeds_val,
            "stop_on_rare_count": as_rare_val,
            "stop_on_epic_or_event": bool(self.var_as_epic.get()),
        }
        acc = self.var_accuracy.get()
        self.config_data["accuracy_mode"] = acc
        self.config_data["confidence"] = dict(ACCURACY_PRESETS.get(acc, ACCURACY_PRESETS["Chuẩn"]))
        save_config(self.config_data)
        if not silent:
            self.append_log("Đã lưu toàn bộ cài đặt.")
        return True

    # ------------------------------------------------------------------
    # ĐIỀU KHIỂN CHẠY / TẠM DỪNG / CHẠY LẠI / DỪNG KHẨN CẤP
    # ------------------------------------------------------------------
    def _active_bots(self) -> List[DaoRongBot]:
        bots: List[DaoRongBot] = []
        if self.bot is not None and self.bot.is_running:
            bots.append(self.bot)
        for b in self.multi_bots.values():
            if b.is_running:
                bots.append(b)
        return bots

    def on_toggle_start_stop(self) -> None:
        running = self._active_bots()
        if running:
            if any(not b.is_paused for b in running):
                for b in running:
                    b.pause()
                self.lbl_badge.configure(text=" ⏸ Tạm dừng ", fg_color="#78350F", text_color="#FDE68A")
                self.btn_play_pause.configure(text="▶  TIẾP TỤC (Ctrl+Enter)", fg_color=C["green"], text_color="#052E16")
            else:
                for b in running:
                    b.resume()
                self.lbl_badge.configure(text=" ● Đang chạy ", fg_color="#14532D", text_color="#4ADE80")
                self.btn_play_pause.configure(text="⏸  TẠM DỪNG (Ctrl+Enter)", fg_color=C["amber"], text_color="#451A03")
        else:
            self._start_bot(restart_from_egg=False)

    def on_restart_from_egg(self) -> None:
        running = self._active_bots()
        if running:
            for b in running:
                b.restart_from_beginning()
            self.lbl_badge.configure(text=" ● Đang chạy lại ", fg_color="#14532D", text_color="#4ADE80")
            self.btn_play_pause.configure(text="⏸  TẠM DỪNG (Ctrl+Enter)", fg_color=C["amber"], text_color="#451A03")
        else:
            self._start_bot(restart_from_egg=True)

    def _toggle_single_adb_bot(self, serial: str) -> None:
        self._save_all_settings(silent=True)
        existing = self.multi_bots.get(serial)
        btn = self.adb_device_btns.get(serial)
        if existing is not None and existing.is_running:
            if existing.is_paused:
                existing.resume()
                if btn:
                    btn.configure(text="⏸", fg_color=C["amber"], text_color="#451A03")
            else:
                existing.pause()
                if btn:
                    btn.configure(text="▶", fg_color=C["green"], text_color="#052E16")
            return
        self._save_all_settings(silent=True)
        acc_cfg = dict(self.config_data)
        if not acc_cfg.get("selected_parent_1") or not acc_cfg.get("selected_parent_2"):
            messagebox.showwarning("Thiếu rồng bố mẹ", "Bạn chưa chọn đủ 2 rồng bố mẹ!")
            return
        lbl = f"ADB {serial}"
        bot = DaoRongBot(
            BASE_DIR, acc_cfg,
            lambda m, l=lbl: self.append_log(f"[{l}] {m}"),
            lambda st, s=serial: self._update_multi_stats(s, st),
            alert_callback=self._on_bot_alert, adb=self.adb_ctrl, adb_serial=serial, account_label=lbl,
            shared_text_matcher=self.shared_text_matcher, shared_name_cache=self.shared_name_cache,
            shared_calibrator=self.shared_calibrator, shared_pair_stats=self.shared_pair_stats,
                shared_yolo=self.shared_yolo,
        )
        self.multi_bots[serial] = bot
        bot.start(restart_from_egg=False)
        if btn:
            btn.configure(text="⏸", fg_color=C["amber"], text_color="#451A03")

    def _start_bot(self, restart_from_egg: bool = False) -> None:
        if not self._save_all_settings(silent=False):
            return
        if self.config_data.get("control_mode", "windows") == "adb":
            sels = [s for s, v in self.adb_device_vars.items() if v.get()]
            if not sels:
                messagebox.showwarning("Chưa chọn thiết bị ADB", "Hãy bấm 'Quét ADB' và tick ít nhất 1 giả lập!")
                return
            acc_cfg_base = dict(self.config_data)
            ap1 = acc_cfg_base.get("selected_parent_1", "")
            ap2 = acc_cfg_base.get("selected_parent_2", "")
            if not ap1 or not ap2:
                messagebox.showwarning("Thiếu rồng bố mẹ", "Bạn chưa chọn đủ 2 rồng bố mẹ!")
                return
            if ap1 == ap2:
                messagebox.showwarning("Trùng cặp rồng bố mẹ", "Đang chọn trùng 2 rồng bố mẹ!")
                return
            self.multi_bots.clear()
            for idx, serial in enumerate(sels, 1):
                acc_cfg = dict(acc_cfg_base)
                lbl = f"Acc #{idx} ({serial})"
                bot = DaoRongBot(
                    BASE_DIR, acc_cfg,
                    lambda m, l=lbl: self.append_log(f"[{l}] {m}"),
                    lambda st, s=serial: self._update_multi_stats(s, st),
                    alert_callback=self._on_bot_alert, adb=self.adb_ctrl, adb_serial=serial, account_label=lbl,
                    shared_text_matcher=self.shared_text_matcher, shared_name_cache=self.shared_name_cache,
                    shared_calibrator=self.shared_calibrator, shared_pair_stats=self.shared_pair_stats,
                shared_yolo=self.shared_yolo,
                )
                self.multi_bots[serial] = bot
                bot.start(restart_from_egg=restart_from_egg)
            self.lbl_badge.configure(text=f" ● Chạy ngầm ({len(sels)} acc) ", fg_color="#14532D", text_color="#4ADE80")
            self.btn_play_pause.configure(text="⏸  TẠM DỪNG (Ctrl+Enter)", fg_color=C["amber"], text_color="#451A03")
            return

        p1 = self.config_data.get("selected_parent_1", "")
        p2 = self.config_data.get("selected_parent_2", "")
        if not p1 or not p2:
            messagebox.showwarning("Chưa chọn cặp rồng", "Hãy chụp và chọn đủ 2 rồng bố mẹ trước khi chạy!")
            return
        if p1 == p2:
            messagebox.showwarning("Trùng cặp rồng bố mẹ", "Rồng bố/mẹ 1 và Rồng bố/mẹ 2 đang chọn giống hệt nhau!\nHãy chọn 2 con khác nhau ở 2 ô Bố/Mẹ.")
            return
        self.bot = DaoRongBot(
            BASE_DIR, self.config_data, self.append_log, self.update_stats,
            alert_callback=self._on_bot_alert, account_label="Chuột chính",
            shared_text_matcher=self.shared_text_matcher, shared_name_cache=self.shared_name_cache,
            shared_calibrator=self.shared_calibrator, shared_pair_stats=self.shared_pair_stats,
                shared_yolo=self.shared_yolo,
        )
        self.lbl_badge.configure(text=" ● Đang chạy ", fg_color="#14532D", text_color="#4ADE80")
        self.btn_play_pause.configure(text="⏸  TẠM DỪNG (Ctrl+Enter)", fg_color=C["amber"], text_color="#451A03")
        self.bot.start(restart_from_egg=restart_from_egg)

    def on_stop_all(self) -> None:
        if self.bot is not None and self.bot.is_running:
            self.bot.stop()
        for b in self.multi_bots.values():
            if b.is_running:
                b.stop()
        try:
            self.adb_ctrl.close_all_shells()
        except Exception:
            pass
        self.lbl_badge.configure(text=" ● Đã dừng ", fg_color="#450A0A", text_color="#FCA5A5")
        self.btn_play_pause.configure(text="▶  BẮT ĐẦU (Ctrl+Enter)", fg_color=C["green"], text_color="#052E16")

    def _on_bot_alert(self, title: str, message: str) -> None:
        def _show() -> None:
            if self.config_data.get("sound_alert", True) and winsound is not None:
                try:
                    winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
                except Exception:
                    pass
            messagebox.showinfo(title, message, parent=self._mini_hud if (self._mini_hud and self._mini_hud.winfo_exists()) else self)
        self.after(0, _show)

    def append_log(self, msg: str) -> None:
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n"
        tag = "info"
        if any(k in msg for k in ("[LỖI", "[DỪNG]")):
            tag = "err"
        elif any(k in msg for k in ("[CẢNH BÁO]", "[PHỤC HỒI]", "[CHẨN ĐOÁN]", "Bỏ qua", "Nghỉ")):
            tag = "warn"
        elif any(k in msg for k in ("Lai ra:", "Chúc phúc", "HOÀN THÀNH", "Xong", "✨", "⚡")):
            tag = "ok"
        max_lines = int(self.config_data.get("max_log_lines", 250))

        def _ins() -> None:
            self.txt_log.configure(state=tk.NORMAL)
            self.txt_log.insert(tk.END, line, tag)
            self._log_line_count += 1
            if self._log_line_count > max_lines:
                self.txt_log.delete("1.0", f"{self._log_line_count - max_lines + 1}.0")
                self._log_line_count = max_lines
            self.txt_log.see(tk.END)
            self.txt_log.configure(state=tk.DISABLED)
        self.after(0, _ins)

    def _clear_log(self) -> None:
        self.txt_log.configure(state=tk.NORMAL)
        self.txt_log.delete("1.0", tk.END)
        self.txt_log.configure(state=tk.DISABLED)
        self._log_line_count = 0

    def _copy_log(self) -> None:
        c = self.txt_log.get("1.0", tk.END).strip()
        if c:
            self.clipboard_clear()
            self.clipboard_append(c)

    def _refresh_rare_drops(self, drops: List[Dict[str, Any]]) -> None:
        self.lb_rare_drops.delete(0, tk.END)
        for d in drops:
            st = f"{d['stars']}★" if d.get("stars") else ""
            self.lb_rare_drops.insert(tk.END, f"[{d['time']}] {d['name']} {st} ({d['rarity']})")

    def update_stats(self, stats: BotStats) -> None:
        def _upd() -> None:
            rare_tot = stats.rare_hits + stats.epic_hits + stats.event_hits
            self.lbl_state.configure(text=stats.current_state[:32])
            self._stat_lbls["breeds"].configure(text=str(stats.breeds))
            self._stat_lbls["kept"].configure(text=str(stats.kept))
            self._stat_lbls["blessed"].configure(text=str(stats.blessed))
            self._stat_lbls["sold"].configure(text=str(stats.sold))
            self._stat_lbls["dusted"].configure(text=str(stats.dusted))
            self._stat_lbls["rare_total"].configure(text=str(rare_tot))
            self.lbl_speed_rate.configure(text=f"NHẬT KÝ HOẠT ĐỘNG  •  {stats.breeds_per_min:.1f} lượt/phút")
            self._refresh_rare_drops(stats.recent_drops)
            if self._hud_lbl_status and self._hud_lbl_status.winfo_exists():
                self._hud_lbl_status.configure(text=stats.current_state[:22])
                self._hud_lbl_kpi.configure(text=f"Lai: {stats.breeds} | Quý: {rare_tot}")
            if stats.current_state == "Đã dừng":
                self.lbl_badge.configure(text=" ● Đã dừng ", fg_color="#450A0A", text_color="#FCA5A5")
                self.btn_play_pause.configure(text="▶  BẮT ĐẦU (Ctrl+Enter)", fg_color=C["green"], text_color="#052E16")
            elif stats.current_state.startswith("Tạm dừng"):
                self.lbl_badge.configure(text=" ⏸ Tạm dừng ", fg_color="#78350F", text_color="#FDE68A")
                self.btn_play_pause.configure(text="▶  TIẾP TỤC (Ctrl+Enter)", fg_color=C["green"], text_color="#052E16")
        self.after(0, _upd)

    def _update_multi_stats(self, serial: str, stats: BotStats) -> None:
        def _upd() -> None:
            st_lbl = self.adb_device_status_lbls.get(serial)
            if st_lbl is not None and st_lbl.winfo_exists():
                st_lbl.configure(text=f"Lai: {stats.breeds} | Bụi: {stats.dusted}")
            all_b = list(self.multi_bots.values())
            tot_b = sum(b.stats.breeds for b in all_b)
            tot_r = sum(b.stats.rare_hits + b.stats.epic_hits + b.stats.event_hits for b in all_b)
            self._stat_lbls["breeds"].configure(text=str(tot_b))
            self._stat_lbls["kept"].configure(text=str(sum(b.stats.kept for b in all_b)))
            self._stat_lbls["blessed"].configure(text=str(sum(b.stats.blessed for b in all_b)))
            self._stat_lbls["sold"].configure(text=str(sum(b.stats.sold for b in all_b)))
            self._stat_lbls["dusted"].configure(text=str(sum(b.stats.dusted for b in all_b)))
            self._stat_lbls["rare_total"].configure(text=str(tot_r))
            self.lbl_speed_rate.configure(text=f"NHẬT KÝ HOẠT ĐỘNG  •  {sum(b.stats.breeds_per_min for b in all_b):.1f} lượt/phút ({len(all_b)} acc)")
            self._refresh_rare_drops([d for b in all_b for d in b.stats.recent_drops][:30])
        self.after(0, _upd)

    def _poll_emulator_status(self) -> None:
        try:
            rect = VisionEngine(self.config_data.get("window_title_keyword", "AUTO")).get_target_window()
            if rect.found_window:
                self.lbl_ld_status.configure(text=f"● {(rect.title or 'Giả lập')[:14]} ({rect.width}×{rect.height})", text_color=C["green"])
            else:
                self.lbl_ld_status.configure(text="● Chưa thấy giả lập", text_color=C["amber"])
        except Exception:
            pass
        self.after(3000, self._poll_emulator_status)

    def _register_hotkeys(self) -> None:
        self.bind_all("<Control-Return>", lambda _e: self.on_toggle_start_stop())
        self.bind_all("<Escape>", lambda _e: self.on_stop_all())
        if keyboard is not None:
            try:
                keyboard.add_hotkey("ctrl+enter", lambda: self.after(0, self.on_toggle_start_stop), suppress=False)
                keyboard.add_hotkey("esc", lambda: self.after(0, self.on_stop_all), suppress=False)
            except Exception:
                pass
        self.append_log("Sẵn sàng • CTRL+ENTER: Chạy / Tạm dừng  |  ESC: Dừng khẩn cấp.")

    # ------------------------------------------------------------------
    # AUTO-UPDATE QUA GITHUB RELEASES
    # ------------------------------------------------------------------
    def _periodic_update_check(self) -> None:
        self._check_update_async(silent=True)
        # Tự động kiểm tra ngầm mỗi 3 phút (180s) để báo ngay khi đang treo máy mà có bản mới!
        self.after(180_000, self._periodic_update_check)

    def _check_update_async(self, silent: bool = False) -> None:
        url = self.config_data.get("update_manifest_url", "") or DEFAULT_MANIFEST_URL
        threading.Thread(target=lambda: self.after(0, lambda: self._handle_update_result(check_for_update(url), silent)), daemon=True).start()

    def _handle_update_result(self, info, silent: bool) -> None:
        if info.error:
            if not silent:
                messagebox.showwarning("Kiểm tra cập nhật", info.error, parent=self)
            return
        if info.has_update:
            self._pending_update_info = info
            note_short = (info.notes or "").strip()[:95]
            self.lbl_update_banner.configure(text=f"🔔 Có bản mới v{info.latest_version}! {note_short}")
            if not self.update_banner.winfo_ismapped():
                self.update_banner.pack(side="top", fill="x", before=self.content)
            # Đổi màu nút Cập nhật trên thanh tiêu đề để người đang mở app thấy ngay lập tức!
            self.btn_check_update.configure(
                text=f"● v{info.latest_version}",
                fg_color=C["amber"],
                hover_color="#D97706",
                text_color="#451A03",
            )
            if self._last_notified_update_ver != info.latest_version:
                self._last_notified_update_ver = info.latest_version
                self.append_log(f"🔔 [CẬP NHẬT] Đã có phiên bản mới v{info.latest_version}! Bấm 'Cập nhật ngay' trên thanh vàng.")
                if winsound is not None and self.config_data.get("sound_alert", True):
                    try:
                        winsound.MessageBeep(winsound.MB_ICONASTERISK)
                    except Exception:
                        pass
            if not silent:
                self._apply_pending_update()
        elif not silent:
            messagebox.showinfo("Cập nhật", f"Bạn đang dùng bản mới nhất (v{CURRENT_VERSION})!", parent=self)

    def _apply_pending_update(self) -> None:
        info = self._pending_update_info
        if not info:
            return
        if not messagebox.askyesno("Cập nhật bản mới", f"Tải và cập nhật lên v{info.latest_version} ngay?\n(Toàn bộ rồng bố mẹ, luật rồng và link Discord sẽ được giữ nguyên)", parent=self):
            return
        self._save_all_settings(silent=True)
        self.on_stop_all()
        win = ctk.CTkToplevel(self)
        win.title("Đang cập nhật...")
        win.geometry("380x120")
        win.attributes("-topmost", True)
        lbl = ctk.CTkLabel(win, text="Đang tải bản cập nhật...", font=ctk.CTkFont("Segoe UI", 12, "bold"))
        lbl.pack(pady=(18, 8))
        bar = ctk.CTkProgressBar(win, width=320)
        bar.set(0.05)
        bar.pack(pady=6)

        def worker() -> None:
            try:
                bat = download_and_prepare_update(
                    info.download_url, BASE_DIR,
                    progress_cb=lambda r, t: self.after(0, lambda: (bar.set(r), lbl.configure(text=t))),
                )
                self.after(0, lambda: launch_updater_and_exit(bat))
            except Exception as exc:
                self.after(0, lambda: (win.destroy(), messagebox.showerror("Lỗi cập nhật", str(exc))))

        threading.Thread(target=worker, daemon=True).start()

    def _open_models_folder(self) -> None:
        folder = BASE_DIR / "models"
        folder.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(folder))
        except Exception:
            messagebox.showinfo("Thư mục AI Cấp 2", str(folder), parent=self)

    def _test_remote_notify(self) -> None:
        self._save_all_settings(silent=True)
        ntf = AlertNotifier(
            discord_webhook=self.var_dc_webhook.get().strip(),
            enabled=True,
        )
        def _run() -> None:
            ok, msg = ntf.send_test()
            self.after(0, lambda: (
                messagebox.showinfo("Thông báo điện thoại", msg, parent=self)
                if ok else messagebox.showwarning("Lỗi gửi thông báo", msg, parent=self)
            ))
        threading.Thread(target=_run, daemon=True).start()

    def _on_close(self) -> None:
        try:
            self._save_all_settings(silent=True)
        except Exception:
            pass
        self.on_stop_all()
        try:
            self.adb_ctrl.close_all_shells()
        except Exception:
            pass
        self.destroy()


def show_splash_and_launch() -> None:
    app = DaoRongStudioV10()
    app.mainloop()


if __name__ == "__main__":
    show_splash_and_launch()