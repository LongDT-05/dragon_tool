"""
main.py - DaoRong_tool v7.0 (Giao diện Chuyên Nghiệp, Dễ Hiểu, Không Thuật Ngữ Khó Hiểu)
- Tự động nhận diện đa giả lập (AUTO: LDPlayer, BlueStacks, MuMu, Nox, MEmu).
- Tích hợp sẵn 17 ảnh nút game + tách riêng tỷ lệ co giãn Nút UI và Rồng.
- Chẩn đoán thông minh khi rồng bay che Trứng/Tháp + phản ứng tức thì ngay khi lai xong.
- Tích hợp Auto-Update ngầm qua GitHub Releases (LongDT-05/dragon_tool).
Phím tắt: CTRL + ENTER = Bắt đầu / Dừng | ESC = Dừng khẩn cấp.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog
from typing import Any, Dict, List, Optional

import customtkinter as ctk
from PIL import Image

try:
    import winsound
except ImportError:
    winsound = None

from adb_controller import AdbController
from bot_engine import BotStats, DaoRongBot
from smart_ai import AnchorAutoCalibrator, NaturalRuleParser, PairStatsTracker, PersistentNameCache
from ocr_engine import DragonTextMatcher, format_rule_string, load_dragon_catalog, normalize_vn
from capture_tool import capture_region_overlay, pick_point_overlay, select_box_overlay
from updater import (
    CURRENT_VERSION,
    DEFAULT_MANIFEST_URL,
    check_for_update,
    download_and_prepare_update,
    launch_updater_and_exit,
)
from vision import VisionEngine, enable_windows_dpi_awareness

try:
    import keyboard
except ImportError:
    keyboard = None


BASE_DIR = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)
CONFIG_PATH = BASE_DIR / "config.json"
TEMPLATES_DIR = BASE_DIR / "templates"

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

C = {
    "sidebar": "#090f1d",
    "bg": "#0f172a",
    "card": "#172033",
    "card2": "#1e293b",
    "border": "#2a384f",
    "text": "#f1f5f9",
    "muted": "#94a3b8",
    "cyan": "#38bdf8",
    "green": "#10b981",
    "green_hv": "#059669",
    "amber": "#f59e0b",
    "red": "#ef4444",
    "red_hv": "#dc2626",
    "purple": "#a855f7",
    "console": "#070c17",
}

# 3 mức độ chính xác dễ hiểu thay cho các con số kỹ thuật 0.76 / 0.85
ACCURACY_PRESETS = {
    "Thoáng": {"button": 0.70, "nav_button": 0.68, "dragon": 0.72, "dragon_name": 0.80, "bag_full": 0.74, "nguyen_lieu": 0.72},
    "Chuẩn": {"button": 0.76, "nav_button": 0.72, "dragon": 0.75, "dragon_name": 0.85, "bag_full": 0.78, "nguyen_lieu": 0.70},
    "Chặt": {"button": 0.82, "nav_button": 0.78, "dragon": 0.80, "dragon_name": 0.90, "bag_full": 0.82, "nguyen_lieu": 0.82},
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
    ("btn_close_dust_x.png", "Dấu X đóng bảng Hóa Bụi", False),
    ("btn_arrow_right.png", "Mũi tên chuyển đảo >", True),
    ("btn_arrow_left.png", "Mũi tên quay lại đảo <", True),
    ("btn_altar_center.png", "Tháp giữa đảo Tẩy Tủy", False),
    ("btn_dust_bottle.png", "Cái bình Hóa Bụi", True),
    ("dragon_nguyen_lieu.png", "Thẻ rồng 'Nguyên liệu'", True),
    ("btn_dust.png", "Nút 'Hóa Bụi'", True),
    ("btn_dust_confirm.png", "Nút 'Xác nhận' Hóa Bụi", True),
    ("btn_keep_after_bless.png", "Nút 'Cất rồng' sau chúc phúc", False),
    ("icon_dragon_locked.png", "Ổ khóa trắng (né rồng khóa khi Hóa Bụi)", False),
    ("btn_home_island.png", "Nút Về Đảo (tự thoát khi lạc vào Map)", False),
]


PROFILES_DIR = BASE_DIR / "profiles"


def ensure_directories() -> None:
    for sub in ["ui", "parents", "keep", "sell", "bless", "dust_targets"]:
        (TEMPLATES_DIR / sub).mkdir(parents=True, exist_ok=True)
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> Dict[str, Any]:
    if CONFIG_PATH.is_file():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    else:
        cfg = {}
    cfg.setdefault("dragon_text_rules", {"bless": [], "keep": [], "sell": []})
    for k in ("bless", "keep", "sell"):
        cfg["dragon_text_rules"].setdefault(k, [])
    cfg.setdefault("accuracy_mode", "Chuẩn")
    return cfg


def save_config(cfg: Dict[str, Any]) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


class DaoRongStudioV7(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        enable_windows_dpi_awareness()
        ensure_directories()

        self.title(f"DaoRong_tool v{CURRENT_VERSION}")
        self.geometry("900x610")
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
        self.bot: DaoRongBot | None = None
        self.multi_bots: Dict[str, DaoRongBot] = {}
        self.adb_ctrl = AdbController(BASE_DIR, self.config_data.get("adb_path", ""))
        self.adb_device_vars: Dict[str, tk.BooleanVar] = {}
        self.adb_device_status_lbls: Dict[str, ctk.CTkLabel] = {}
        self.shared_text_matcher = DragonTextMatcher(catalog=self.catalog)
        self.shared_name_cache = PersistentNameCache(TEMPLATES_DIR / 'auto_learned')
        self.shared_calibrator = AnchorAutoCalibrator()
        self.shared_pair_stats = PairStatsTracker(BASE_DIR / 'pair_stats.json')
        self.rule_parser = NaturalRuleParser(self.catalog)
        self.adb_device_profiles: Dict[str, tk.StringVar] = {}
        self.adb_device_btns: Dict[str, ctk.CTkButton] = {}
        self._mini_hud: Optional[ctk.CTkToplevel] = None
        self._hud_lbl_status: Optional[ctk.CTkLabel] = None
        self._hud_lbl_kpi: Optional[ctk.CTkLabel] = None
        self._log_line_count = 0

        self._pages: Dict[str, ctk.CTkFrame] = {}
        self._nav_btns: Dict[str, ctk.CTkButton] = {}
        self._ui_status_lbls: Dict[str, ctk.CTkLabel] = {}
        self._stat_lbls: Dict[str, ctk.CTkLabel] = {}
        self._dragon_listboxes: Dict[str, tk.Listbox] = {}

        self._build_layout()
        self._refresh_all_lists()
        self._show_page("dashboard")
        self._register_hotkeys()
        self._poll_emulator_status()
        if self.config_data.get("auto_check_update", True):
            self.after(3000, self._periodic_update_check)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # Khung giao diện chính: Sidebar trái + Khu nội dung phải
    # ------------------------------------------------------------------
    def _build_layout(self) -> None:
        sidebar = ctk.CTkFrame(self, width=185, corner_radius=0, fg_color=C["sidebar"])
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        brand = ctk.CTkFrame(sidebar, fg_color="transparent")
        brand.pack(fill="x", padx=18, pady=(20, 14))
        ctk.CTkLabel(
            brand, text="🐉 ĐẢO RỒNG", font=ctk.CTkFont("Segoe UI", 20, "bold"), text_color=C["cyan"]
        ).pack(anchor="w")
        ctk.CTkLabel(
            brand, text=f"DaoRong_tool v{CURRENT_VERSION}", font=ctk.CTkFont("Segoe UI", 11, "bold"), text_color=C["muted"]
        ).pack(anchor="w", pady=(2, 0))

        nav_specs = [
            ("dashboard", "🎮  Bảng Điều Khiển"),
            ("dragons", "🐉  Luật Rồng (AI)"),
            ("settings", "⚙️  Cài Đặt & Tiện Ích"),
        ]
        for key, label in nav_specs:
            btn = ctk.CTkButton(
                sidebar,
                text=label,
                command=lambda k=key: self._show_page(k),
                anchor="w",
                height=42,
                corner_radius=10,
                fg_color="transparent",
                hover_color=C["card2"],
                text_color=C["muted"],
                font=ctk.CTkFont("Segoe UI", 13, "bold"),
            )
            btn.pack(fill="x", padx=12, pady=3)
            self._nav_btns[key] = btn

        # Đáy Sidebar: Trạng thái giả lập + Phím tắt + Nút cập nhật
        status_card = ctk.CTkFrame(sidebar, corner_radius=12, fg_color=C["card"], border_width=1, border_color=C["border"])
        status_card.pack(side="bottom", fill="x", padx=14, pady=(6, 14))

        self.btn_check_update = ctk.CTkButton(
            sidebar,
            text=f"🔄 Cập nhật (v{CURRENT_VERSION})",
            command=lambda: self._check_update_async(silent=False),
            height=34,
            corner_radius=10,
            fg_color=C["card2"],
            hover_color="#0284c7",
            text_color=C["cyan"],
            font=ctk.CTkFont("Segoe UI", 12, "bold"),
        )
        self.btn_check_update.pack(side="bottom", fill="x", padx=14, pady=(0, 6))

        ctk.CTkLabel(
            status_card, text="GIẢ LẬP ĐANG KẾT NỐI", font=ctk.CTkFont("Segoe UI", 10, "bold"), text_color=C["muted"]
        ).pack(anchor="w", padx=12, pady=(10, 2))
        self.lbl_ld_status = ctk.CTkLabel(
            status_card,
            text="Đang tự tìm giả lập...",
            font=ctk.CTkFont("Segoe UI", 12, "bold"),
            text_color=C["amber"],
            wraplength=145,
            justify="left",
        )
        self.lbl_ld_status.pack(anchor="w", padx=12, pady=(0, 8))

        ctk.CTkFrame(status_card, height=1, fg_color=C["border"]).pack(fill="x", padx=12, pady=(0, 8))
        ctk.CTkLabel(
            status_card, text="CTRL+ENTER : Chạy / Dừng", font=ctk.CTkFont("Consolas", 11, "bold"), text_color=C["cyan"]
        ).pack(anchor="w", padx=12)
        ctk.CTkLabel(
            status_card, text="ESC        : Dừng khẩn cấp", font=ctk.CTkFont("Consolas", 11, "bold"), text_color=C["red"]
        ).pack(anchor="w", padx=12, pady=(2, 10))

        right_shell = ctk.CTkFrame(self, corner_radius=0, fg_color=C["bg"])
        right_shell.pack(side="right", fill="both", expand=True)

        # Thanh thông báo nổi bật khi có bản cập nhật mới (ẩn mặc định, tự hiện khi phát hiện bản mới)
        self._pending_update_info = None
        self.update_banner = ctk.CTkFrame(
            right_shell, corner_radius=10, fg_color="#451a03", border_width=1, border_color=C["amber"]
        )
        self.lbl_update_banner = ctk.CTkLabel(
            self.update_banner, text="", font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color="#fde68a"
        )
        self.lbl_update_banner.pack(side="left", padx=14, pady=8)
        ctk.CTkButton(
            self.update_banner, text="✕", width=28, height=26, corner_radius=6,
            fg_color="transparent", hover_color="#78350f", text_color="#fde68a",
            command=lambda: self.update_banner.pack_forget()
        ).pack(side="right", padx=(4, 8), pady=6)
        ctk.CTkButton(
            self.update_banner, text="⬇ Cập nhật ngay", width=125, height=28, corner_radius=8,
            fg_color=C["amber"], hover_color="#d97706", text_color="#1c1917",
            font=ctk.CTkFont("Segoe UI", 11, "bold"), command=self._apply_pending_update
        ).pack(side="right", padx=4, pady=6)

        self.content = ctk.CTkFrame(right_shell, corner_radius=0, fg_color=C["bg"])
        self.content.pack(fill="both", expand=True, padx=18, pady=16)

        self._build_page_dashboard()
        self._build_page_dragons()
        self._build_page_settings()

    def _show_page(self, key: str) -> None:
        for p in self._pages.values():
            p.pack_forget()
        self._pages[key].pack(fill="both", expand=True)
        for k, btn in self._nav_btns.items():
            btn.configure(
                fg_color=C["card2"] if k == key else "transparent",
                text_color=C["cyan"] if k == key else C["muted"],
            )

    def _help_btn(self, parent, title: str, guide_text: str):
        """Nút '?' nhỏ gọn đặt cạnh từng tính năng — bấm vào hiện ngay hướng dẫn dễ hiểu."""
        return ctk.CTkButton(
            parent, text="?", width=22, height=22, corner_radius=11,
            fg_color=C["card2"], hover_color="#0284c7", text_color=C["cyan"],
            font=ctk.CTkFont("Segoe UI", 11, "bold"),
            command=lambda t=title, g=guide_text: messagebox.showinfo(f"Hướng dẫn: {t}", g, parent=self),
        )

    def _tip_banner(self, parent, text: str) -> None:
        box = ctk.CTkFrame(parent, corner_radius=10, fg_color="#0c233a", border_width=1, border_color="#1d4ed8")
        box.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(
            box, text=f"💡 {text}", font=ctk.CTkFont("Segoe UI", 12), text_color="#bae6fd", justify="left", wraplength=790
        ).pack(anchor="w", padx=12, pady=8)

    # ------------------------------------------------------------------
    # TRANG 1: Bảng Điều Khiển
    # ------------------------------------------------------------------
    def _build_page_dashboard(self) -> None:
        page = ctk.CTkScrollableFrame(self.content, fg_color="transparent")
        self._pages["dashboard"] = page

        hdr = ctk.CTkFrame(page, fg_color="transparent")
        hdr.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(hdr, text="Bảng Điều Khiển", font=ctk.CTkFont("Segoe UI", 22, "bold"), text_color=C["text"]).pack(side="left")

        ctk.CTkButton(
            hdr, text="🗗 Mini-HUD", width=100, height=30, corner_radius=8,
            fg_color=C["card2"], hover_color=C["border"], text_color=C["cyan"],
            font=ctk.CTkFont("Segoe UI", 11, "bold"), command=self._toggle_mini_hud
        ).pack(side="right", padx=(6, 0))
        ctk.CTkButton(
            hdr, text="🩺 Kiểm tra nhanh", width=128, height=30, corner_radius=8,
            fg_color=C["card2"], hover_color=C["border"], text_color=C["green"],
            font=ctk.CTkFont("Segoe UI", 11, "bold"), command=self._run_preflight_ui
        ).pack(side="right", padx=(6, 0))

        self.lbl_badge = ctk.CTkLabel(
            hdr, text="  ● Sẵn sàng  ", corner_radius=14, fg_color="#064e3b", text_color="#6ee7b7",
            font=ctk.CTkFont("Segoe UI", 12, "bold"), height=30
        )
        self.lbl_badge.pack(side="right")

        # --- THANH DẪN ĐƯỜNG 3 BƯỚC CHO NGƯỜI MỚI (TỰ ẨN KHI ĐÃ CHỌN ĐỦ 2 BỐ MẸ) ---
        self.onboard_card = ctk.CTkFrame(page, corner_radius=12, fg_color="#0f2942", border_width=1, border_color="#0284c7")
        ob_top = ctk.CTkFrame(self.onboard_card, fg_color="transparent")
        ob_top.pack(fill="x", padx=14, pady=(8, 6))
        ctk.CTkLabel(
            ob_top, text="👋 NGƯỜI MỚI BẮT ĐẦU TRONG 30 GIÂY:",
            font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color="#7dd3fc"
        ).pack(side="left")
        ctk.CTkButton(
            ob_top, text="1. Chụp 2 Bố Mẹ", width=125, height=28, corner_radius=8,
            fg_color="#0284c7", hover_color="#0369a1", font=ctk.CTkFont("Segoe UI", 11, "bold"),
            command=lambda: self._capture_dragon_template("parents", "Rồng Bố/Mẹ", "")
        ).pack(side="left", padx=(10, 6))
        ctk.CTkButton(
            ob_top, text="2. Mục tiêu: Cày Bụi", width=138, height=28, corner_radius=8,
            fg_color="#065f46", hover_color="#047857", font=ctk.CTkFont("Segoe UI", 11, "bold"),
            command=lambda: self._quick_goal_setup("dust")
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            ob_top, text="2. Mục tiêu: Săn Hiếm", width=148, height=28, corner_radius=8,
            fg_color="#b45309", hover_color="#92400e", font=ctk.CTkFont("Segoe UI", 11, "bold"),
            command=lambda: self._quick_goal_setup("hunt")
        ).pack(side="left")
        if not self.config_data.get("selected_parent_1") or not self.config_data.get("selected_parent_2"):
            self.onboard_card.pack(fill="x", pady=(0, 8))

        # --- CARD 1: CHẾ ĐỘ HOẠT ĐỘNG & KẾT NỐI (CHẠY NGẦM / NHIỀU ACC) ---
        card_mode = ctk.CTkFrame(page, corner_radius=14, fg_color=C["card"], border_width=1, border_color=C["border"])
        card_mode.pack(fill="x", pady=(0, 10))

        m_grid = ctk.CTkFrame(card_mode, fg_color="transparent")
        m_grid.pack(fill="x", padx=16, pady=10)

        self._mode_to_vn = {"breed_and_dust": "Lai + Hóa Bụi", "breed_only": "Chỉ Auto Lai (Săn Rồng)"}
        self._mode_from_vn = {v: k for k, v in self._mode_to_vn.items()}
        self.var_bot_mode_vn = tk.StringVar(
            value=self._mode_to_vn.get(self.config_data.get("bot_mode", "breed_and_dust"), "Lai + Hóa Bụi")
        )
        ctk.CTkLabel(m_grid, text="Chế độ chạy:", font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color=C["cyan"]).grid(row=0, column=0, sticky="w", padx=(0, 8), pady=4)
        ctk.CTkSegmentedButton(
            m_grid, values=["Lai + Hóa Bụi", "Chỉ Auto Lai (Săn Rồng)"],
            variable=self.var_bot_mode_vn, width=240, corner_radius=8,
            command=lambda _v: self._on_mode_changed()
        ).grid(row=0, column=1, sticky="w", padx=(0, 16), pady=4)

        self._ctrl_to_vn = {"windows": "Chuột Tự Nhiên", "adb": "Chạy Ngầm / Nhiều Acc (ADB)"}
        self._ctrl_from_vn = {v: k for k, v in self._ctrl_to_vn.items()}
        self.var_ctrl_mode_vn = tk.StringVar(
            value=self._ctrl_to_vn.get(self.config_data.get("control_mode", "windows"), "Chuột Tự Nhiên")
        )
        ctk.CTkLabel(m_grid, text="Điều khiển:", font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color=C["cyan"]).grid(row=0, column=2, sticky="w", padx=(0, 8), pady=4)
        ctk.CTkSegmentedButton(
            m_grid, values=["Chuột Tự Nhiên", "Chạy Ngầm / Nhiều Acc (ADB)"],
            variable=self.var_ctrl_mode_vn, width=235, corner_radius=8,
            command=lambda _v: self._on_ctrl_mode_changed()
        ).grid(row=0, column=3, sticky="w", pady=4)

        self.lbl_mode_hint = ctk.CTkLabel(
            card_mode,
            text="💡 Chế độ Lai + Hóa Bụi: tự động lai liên tục, đầy túi tự sang đảo hóa bụi rồng Nguyên liệu (tự né rồng bị khóa 🔒) rồi quay lại.",
            font=ctk.CTkFont("Segoe UI", 11), text_color=C["muted"], anchor="w", justify="left"
        )
        m_hint_row = ctk.CTkFrame(card_mode, fg_color="transparent")
        m_hint_row.pack(fill="x", padx=16, pady=(0, 8))
        self.lbl_mode_hint.pack_forget()
        self.lbl_mode_hint = ctk.CTkLabel(m_hint_row, text="", font=ctk.CTkFont("Segoe UI", 11), text_color=C["muted"], anchor="w", justify="left", wraplength=590)
        self.lbl_mode_hint.pack(side="left", fill="x", expand=True)
        self._help_btn(m_hint_row, "Chế độ chạy & Chạy Ngầm ADB", "• Lai + Hóa Bụi: đầy túi tự sang đảo Tẩy Tủy hóa bụi (tự né rồng khóa 🔒).\n• Chỉ Auto Lai: chuyên săn rồng hiếm / làm nhiệm vụ, đầy túi tự dừng & báo chuông.\n• Chạy Ngầm ADB: bật Gỡ lỗi ADB 1 lần trong LDPlayer để treo ngầm không chiếm chuột và chạy nhiều acc cùng lúc!").pack(side="right", padx=(6, 0))

        self.adb_panel = ctk.CTkFrame(card_mode, corner_radius=10, fg_color=C["console"], border_width=1, border_color=C["border"])
        adb_hdr = ctk.CTkFrame(self.adb_panel, fg_color="transparent")
        adb_hdr.pack(fill="x", padx=12, pady=(8, 4))
        ctk.CTkLabel(
            adb_hdr,
            text="📱 DANH SÁCH GIẢ LẬP CHẠY NGẦM (Tick nhiều acc để chạy song song — KHÔNG chiếm chuột thật)",
            font=ctk.CTkFont("Segoe UI", 11, "bold"), text_color=C["green"]
        ).pack(side="left")
        ctk.CTkButton(
            adb_hdr, text="🔄 Quét giả lập ADB", width=140, height=26, corner_radius=8,
            fg_color="#0284c7", hover_color="#0369a1", font=ctk.CTkFont("Segoe UI", 11, "bold"),
            command=self._scan_adb_devices
        ).pack(side="right")
        self.adb_list_frame = ctk.CTkFrame(self.adb_panel, fg_color="transparent")
        self.adb_list_frame.pack(fill="x", padx=12, pady=(2, 8))
        self._on_mode_changed()
        self._on_ctrl_mode_changed()

        # --- CARD 2: CHỌN 2 RỒNG BỐ MẸ & TỐC ĐỘ ---
        card_pair = ctk.CTkFrame(page, corner_radius=14, fg_color=C["card"], border_width=1, border_color=C["border"])
        card_pair.pack(fill="x", pady=(0, 10))

        top_bar = ctk.CTkFrame(card_pair, fg_color="transparent")
        top_bar.pack(fill="x", padx=16, pady=(10, 4))
        ctk.CTkLabel(top_bar, text="CHỌN 2 RỒNG ĐỂ LAI CỐ ĐỊNH", font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color=C["cyan"]).pack(side="left")
        self._help_btn(top_bar, "Chọn 2 rồng bố mẹ", "1. Mở bảng Lai Rồng trong game.\n2. Bấm '+ Chụp rồng lai mới' và khoanh gọn từng con ở thanh dưới.\n3. Chọn Rồng 1 và Rồng 2 ở 2 ô bên dưới — tool sẽ tự cuộn tìm và lai cố định cặp này.").pack(side="left", padx=(6, 0))
        ctk.CTkButton(
            top_bar, text="🔄 Đặt lại cặp rồng", width=130, height=28, corner_radius=8,
            fg_color=C["card2"], hover_color=C["border"], text_color=C["amber"],
            font=ctk.CTkFont("Segoe UI", 11, "bold"), command=self._reset_parent_selection
        ).pack(side="right")
        ctk.CTkButton(
            top_bar, text="📷 + Chụp rồng lai mới", width=150, height=28, corner_radius=8,
            fg_color="#0284c7", hover_color="#0369a1", font=ctk.CTkFont("Segoe UI", 11, "bold"),
            command=lambda: self._capture_dragon_template("parents", "Rồng Bố/Mẹ", "")
        ).pack(side="right", padx=(0, 8))

        grid = ctk.CTkFrame(card_pair, fg_color="transparent")
        grid.pack(fill="x", padx=16, pady=(2, 10))

        ctk.CTkLabel(grid, text="Rồng bố/mẹ 1:", text_color=C["text"]).grid(row=0, column=0, sticky="w", padx=(0, 8), pady=4)
        self.var_p1 = tk.StringVar(value=self.config_data.get("selected_parent_1", ""))
        self.combo_p1 = ctk.CTkOptionMenu(grid, variable=self.var_p1, values=[""], width=195, corner_radius=8, fg_color=C["console"], button_color=C["card2"])
        self.combo_p1.grid(row=0, column=1, padx=(0, 18), pady=4)

        ctk.CTkLabel(grid, text="Rồng bố/mẹ 2:", text_color=C["text"]).grid(row=0, column=2, sticky="w", padx=(0, 8), pady=4)
        self.var_p2 = tk.StringVar(value=self.config_data.get("selected_parent_2", ""))
        self.combo_p2 = ctk.CTkOptionMenu(grid, variable=self.var_p2, values=[""], width=195, corner_radius=8, fg_color=C["console"], button_color=C["card2"])
        self.combo_p2.grid(row=0, column=3, pady=4)

        ctk.CTkLabel(grid, text="Rồng chưa có luật:", text_color=C["muted"]).grid(row=1, column=0, sticky="w", padx=(0, 8), pady=4)
        self._unk_map_to_vn = {"keep": "Cất an toàn", "pause": "Tạm dừng", "sell": "Bán luôn"}
        self._unk_map_from_vn = {v: k for k, v in self._unk_map_to_vn.items()}
        self.var_unknown_vn = tk.StringVar(value=self._unk_map_to_vn.get(self.config_data.get("unknown_dragon_action", "keep"), "Cất an toàn"))
        ctk.CTkSegmentedButton(
            grid, values=["Cất an toàn", "Tạm dừng", "Bán luôn"], variable=self.var_unknown_vn, width=195, corner_radius=8
        ).grid(row=1, column=1, sticky="w", pady=4)

        ctk.CTkLabel(grid, text="Tốc độ thao tác:", text_color=C["muted"]).grid(row=1, column=2, sticky="w", padx=(0, 8), pady=4)
        self._spd_to_vn = {"fast": "Nhanh", "balanced": "Cân bằng", "safe": "Chậm"}
        self._spd_from_vn = {v: k for k, v in self._spd_to_vn.items()}
        self.var_speed_vn = tk.StringVar(value=self._spd_to_vn.get(self.config_data.get("speed_preset", "fast"), "Nhanh"))
        ctk.CTkSegmentedButton(
            grid, values=["Nhanh", "Cân bằng", "Chậm"], variable=self.var_speed_vn, width=195, corner_radius=8
        ).grid(row=1, column=3, sticky="w", pady=4)

        # --- THANH ĐIỀU KHIỂN THÔNG MINH ---
        act_bar = ctk.CTkFrame(page, fg_color="transparent")
        act_bar.pack(fill="x", pady=(0, 10))

        self.btn_play_pause = ctk.CTkButton(
            act_bar, text="▶  CHẠY / TẠM DỪNG (CTRL+ENTER)", height=42, corner_radius=10,
            fg_color=C["green"], hover_color=C["green_hv"], text_color="#052e16",
            font=ctk.CTkFont("Segoe UI", 12, "bold"), command=self.on_toggle_start_stop
        )
        self.btn_play_pause.pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            act_bar, text="↻  CHẠY LẠI TỪ ĐẦU", height=42, corner_radius=10,
            fg_color="#0284c7", hover_color="#0369a1", text_color="#ffffff",
            font=ctk.CTkFont("Segoe UI", 12, "bold"), command=self.on_restart_from_egg
        ).pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            act_bar, text="■  DỪNG HẲN (ESC)", height=42, corner_radius=10,
            fg_color=C["red"], hover_color=C["red_hv"], text_color="#ffffff",
            font=ctk.CTkFont("Segoe UI", 12, "bold"), command=self.on_stop_all
        ).pack(side="left", padx=(0, 12))

        self.lbl_state = ctk.CTkLabel(
            act_bar, text="Trạng thái: Sẵn sàng", font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color=C["cyan"]
        )
        self.lbl_state.pack(side="left")

        # --- KPI CARDS ---
        kpi_row = ctk.CTkFrame(page, fg_color="transparent")
        kpi_row.pack(fill="x", pady=(0, 10))
        specs = [
            ("breeds", "ĐÃ LAI", C["cyan"]),
            ("kept", "ĐÃ CẤT", C["green"]),
            ("blessed", "CHÚC PHÚC", C["amber"]),
            ("sold", "ĐÃ BÁN", "#fb7185"),
            ("dusted", "HÓA BỤI", C["purple"]),
            ("rare_total", "RỒNG QUÝ ★", "#facc15"),
        ]
        for idx, (k, title, color) in enumerate(specs):
            box = ctk.CTkFrame(kpi_row, corner_radius=12, fg_color=C["card"], border_width=1, border_color=C["border"])
            box.grid(row=0, column=idx, sticky="nsew", padx=(0 if idx == 0 else 8, 0))
            kpi_row.columnconfigure(idx, weight=1)
            ctk.CTkLabel(box, text=title, font=ctk.CTkFont("Segoe UI", 10, "bold"), text_color=C["muted"]).pack(pady=(6, 0))
            lbl = ctk.CTkLabel(box, text="0", font=ctk.CTkFont("Segoe UI", 20, "bold"), text_color=color)
            lbl.pack(pady=(0, 6))
            self._stat_lbls[k] = lbl

        # --- KHU VỰC DƯỚI: NHẬT KÝ (TRÁI) + RỒNG QUÝ VỪA NỔ (PHẢI) ---
        bottom_split = ctk.CTkFrame(page, fg_color="transparent")
        bottom_split.pack(fill="both", expand=True)
        bottom_split.columnconfigure(0, weight=7)
        bottom_split.columnconfigure(1, weight=3)

        log_card = ctk.CTkFrame(bottom_split, corner_radius=14, fg_color=C["card"], border_width=1, border_color=C["border"])
        log_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        log_hdr = ctk.CTkFrame(log_card, fg_color="transparent")
        log_hdr.pack(fill="x", padx=12, pady=(8, 4))
        self.lbl_speed_rate = ctk.CTkLabel(
            log_hdr, text="NHẬT KÝ HOẠT ĐỘNG • 0.0 lượt/phút",
            font=ctk.CTkFont("Segoe UI", 11, "bold"), text_color=C["cyan"]
        )
        self.lbl_speed_rate.pack(side="left")
        ctk.CTkButton(log_hdr, text="Xóa", width=55, height=24, corner_radius=6, fg_color=C["card2"], hover_color=C["border"], command=self._clear_log).pack(side="right")
        ctk.CTkButton(log_hdr, text="Chép", width=60, height=24, corner_radius=6, fg_color=C["card2"], hover_color=C["border"], command=self._copy_log).pack(side="right", padx=(0, 6))

        log_wrap = ctk.CTkFrame(log_card, corner_radius=10, fg_color=C["console"])
        log_wrap.pack(fill="both", expand=True, padx=10, pady=(2, 10))

        self.txt_log = tk.Text(
            log_wrap, state=tk.DISABLED, bg=C["console"], fg=C["text"], height=12,
            font=("Consolas", 9), relief=tk.FLAT, wrap=tk.WORD, padx=8, pady=6, highlightthickness=0
        )
        self.txt_log.tag_config("ok", foreground="#34d399")
        self.txt_log.tag_config("warn", foreground="#fbbf24")
        self.txt_log.tag_config("err", foreground="#f87171")
        self.txt_log.tag_config("info", foreground="#38bdf8")

        sb = ctk.CTkScrollbar(log_wrap, command=self.txt_log.yview)
        self.txt_log.configure(yscrollcommand=sb.set)
        self.txt_log.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y", padx=2, pady=2)

        rare_card = ctk.CTkFrame(bottom_split, corner_radius=14, fg_color=C["card"], border_width=1, border_color=C["border"])
        rare_card.grid(row=0, column=1, sticky="nsew")
        ctk.CTkLabel(
            rare_card, text="★ RỒNG QUÝ VỪA NỔ",
            font=ctk.CTkFont("Segoe UI", 11, "bold"), text_color="#facc15"
        ).pack(anchor="w", padx=12, pady=(10, 4))
        self.lb_rare_drops = tk.Listbox(
            rare_card, bg=C["console"], fg="#fde047", font=("Segoe UI", 9),
            relief=tk.FLAT, highlightthickness=0, height=12
        )
        self.lb_rare_drops.pack(fill="both", expand=True, padx=10, pady=(2, 10))

    def _on_mode_changed(self) -> None:
        m = self._mode_from_vn.get(self.var_bot_mode_vn.get(), "breed_and_dust")
        self.config_data["bot_mode"] = m
        save_config(self.config_data)
        if m == "breed_only":
            self.lbl_mode_hint.configure(
                text="💡 Chế độ Chỉ Auto Lai: chuyên săn rồng Hiếm/Cực hiếm hoặc chạy nhiệm vụ. KHÔNG tự hóa bụi — đầy túi sẽ tự tạm dừng & báo chuông!",
                text_color=C["amber"],
            )
        else:
            self.lbl_mode_hint.configure(
                text="💡 Chế độ Lai + Hóa Bụi: lai liên tục, đầy túi tự sang đảo hóa bụi rồng Nguyên liệu (tự né rồng bị khóa 🔒) rồi quay về lai tiếp.",
                text_color=C["muted"],
            )

    def _on_ctrl_mode_changed(self) -> None:
        c = self._ctrl_from_vn.get(self.var_ctrl_mode_vn.get(), "windows")
        if c == "adb":
            self.adb_panel.pack(fill="x", padx=14, pady=(0, 10))
            self._scan_adb_devices()
        else:
            self.adb_panel.pack_forget()

    def _scan_adb_devices(self) -> None:
        for w in self.adb_list_frame.winfo_children():
            w.destroy()
        self.adb_device_vars.clear()
        self.adb_device_status_lbls.clear()
        self.adb_ctrl.custom_adb_path = self.config_data.get("adb_path", "")
        devices = self.adb_ctrl.list_devices(auto_connect_ports=True)
        if not devices:
            ctk.CTkLabel(
                self.adb_list_frame,
                text="⚠ Chưa tìm thấy giả lập ADB nào. Hãy mở LDPlayer → Cài đặt → Khác → Gỡ lỗi ADB → Mở kết nối cục bộ rồi bấm 'Quét giả lập ADB'.",
                font=ctk.CTkFont("Segoe UI", 11), text_color=C["amber"], wraplength=740, justify="left"
            ).pack(anchor="w", pady=4)
            return
        self.adb_device_profiles.clear()
        self.adb_device_btns.clear()
        profs = self._list_profiles()
        for dev in devices:
            row = ctk.CTkFrame(self.adb_list_frame, fg_color=C["card"], corner_radius=8)
            row.pack(fill="x", pady=2)
            var = tk.BooleanVar(value=True)
            self.adb_device_vars[dev.serial] = var
            ctk.CTkCheckBox(row, text=f"{dev.label}", variable=var, font=ctk.CTkFont("Segoe UI", 11, "bold")).pack(side="left", padx=8, pady=4)
            pvar = tk.StringVar(value=self.config_data.get("active_profile", "Mặc định"))
            self.adb_device_profiles[dev.serial] = pvar
            ctk.CTkOptionMenu(row, variable=pvar, values=profs, width=130, height=24, fg_color=C["console"], button_color=C["card2"], font=ctk.CTkFont("Segoe UI", 10)).pack(side="left", padx=6, pady=4)
            btn_ind = ctk.CTkButton(
                row, text="▶", width=34, height=24, corner_radius=6,
                fg_color=C["green"], text_color="#052e16", font=ctk.CTkFont("Segoe UI", 11, "bold"),
                command=lambda s=dev.serial: self._toggle_single_adb_bot(s)
            )
            btn_ind.pack(side="right", padx=(4, 8), pady=4)
            self.adb_device_btns[dev.serial] = btn_ind
            st = ctk.CTkLabel(row, text="Sẵn sàng", font=ctk.CTkFont("Segoe UI", 11), text_color=C["green"])
            st.pack(side="right", padx=6)
            self.adb_device_status_lbls[dev.serial] = st

    # ------------------------------------------------------------------
    # TRANG 2: Quản Lý Rồng (Thư viện 63 rồng + Tìm kiếm + Bộ lọc Độ Hiếm + Điều kiện Sao)
    # ------------------------------------------------------------------
    def _build_page_dragons(self) -> None:
        page = ctk.CTkFrame(self.content, fg_color="transparent")
        self._pages["dragons"] = page

        hdr = ctk.CTkFrame(page, fg_color="transparent")
        hdr.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(hdr, text="Quản Lý Rồng (Thư viện 63 loài)", font=ctk.CTkFont("Segoe UI", 21, "bold"), text_color=C["text"]).pack(side="left")

        # 2 nút cài nhanh 1 chạm cho người mới!
        ctk.CTkButton(
            hdr, text="⚡ + Bán hết Phổ biến (5)", width=165, height=30, corner_radius=8,
            fg_color="#881337", hover_color="#be123c", font=ctk.CTkFont("Segoe UI", 11, "bold"),
            command=lambda: self._bulk_add_rarity("Phổ biến", "sell")
        ).pack(side="right")
        ctk.CTkButton(
            hdr, text="⚡ + Cất hết Nguyên Liệu (10)", width=185, height=30, corner_radius=8,
            fg_color="#065f46", hover_color="#047857", font=ctk.CTkFont("Segoe UI", 11, "bold"),
            command=lambda: self._bulk_add_rarity("Nguyên Liệu", "keep")
        ).pack(side="right", padx=(0, 8))
        ctk.CTkButton(
            hdr, text="🧪 Thử đọc rồng", width=125, height=30, corner_radius=8,
            fg_color="#0284c7", hover_color="#0369a1", font=ctk.CTkFont("Segoe UI", 11, "bold"),
            command=self._test_read_live_dragon
        ).pack(side="right", padx=(0, 8))

        ai_card = ctk.CTkFrame(page, corner_radius=12, fg_color="#172554", border_width=1, border_color="#3b82f6")
        ai_card.pack(fill="x", pady=(0, 8))
        ai_top = ctk.CTkFrame(ai_card, fg_color="transparent")
        ai_top.pack(fill="x", padx=12, pady=(8, 4))
        ctk.CTkLabel(ai_top, text="✨ AI CÀI LUẬT BẰNG TIẾNG VIỆT (100% offline, miễn phí):", font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color="#93c5fd").pack(side="left")
        self._help_btn(ai_top, "AI Cài Luật Tiếng Việt", "Gõ 1 câu tiếng Việt bình thường về hành động (Bán / Cất / Chúc phúc / Hóa bụi toàn bộ) + tên rồng hoặc độ hiếm + số sao.\n\nVí dụ:\n• Bán hết phổ biến, cất nguyên liệu, giữ hiếm và cực hiếm\n• Cất và hóa bụi toàn bộ rồng lai ra, chỉ chúc phúc Rồng Lửa Xanh Biển (24 sao)").pack(side="left", padx=(6, 0))
        ctk.CTkButton(
            ai_top, text="Mẫu 1: Cày bụi", width=98, height=24, corner_radius=6, fg_color=C["card2"],
            font=ctk.CTkFont("Segoe UI", 10),
            command=lambda: self.var_ai_rule.set("Bán hết phổ biến, cất nguyên liệu, giữ toàn bộ hiếm, cực hiếm và event")
        ).pack(side="right", padx=(4, 0))
        ctk.CTkButton(
            ai_top, text="Mẫu 2: Săn 23★", width=102, height=24, corner_radius=6, fg_color=C["card2"],
            font=ctk.CTkFont("Segoe UI", 10),
            command=lambda: self.var_ai_rule.set("Bán hết phổ biến và nguyên liệu, chúc phúc rồng lửa xanh biển từ 23 sao trở lên, giữ cực hiếm và event")
        ).pack(side="right")

        ai_in = ctk.CTkFrame(ai_card, fg_color="transparent")
        ai_in.pack(fill="x", padx=12, pady=(2, 8))
        self.var_ai_rule = tk.StringVar(value="")
        ctk.CTkEntry(
            ai_in, textvariable=self.var_ai_rule, height=34, corner_radius=8, fg_color=C["console"],
            placeholder_text="Gõ tiếng Việt tự nhiên: vd 'Bán hết phổ biến, cất nguyên liệu, chúc phúc rồng lửa xanh biển từ 23 sao trở lên'..."
        ).pack(side="left", fill="x", expand=True, padx=(0, 8))
        ctk.CTkButton(
            ai_in, text="✨ Phân tích & Áp dụng", width=155, height=34, corner_radius=8,
            fg_color="#2563eb", hover_color="#1d4ed8", font=ctk.CTkFont("Segoe UI", 12, "bold"),
            command=self._apply_ai_natural_rule
        ).pack(side="right")

        split = ctk.CTkFrame(page, fg_color="transparent")
        split.pack(fill="both", expand=True)
        split.columnconfigure(0, weight=5)
        split.columnconfigure(1, weight=6)
        split.rowconfigure(0, weight=1)

        # ---- CỘT TRÁI: THƯ VIỆN TÌM KIẾM & BỘ LỌC 63 RỒNG ----
        lib_card = ctk.CTkFrame(split, corner_radius=14, fg_color=C["card"], border_width=1, border_color=C["border"])
        lib_card.grid(row=0, column=0, sticky="nsew", padx=(0, 10))

        ctk.CTkLabel(lib_card, text="📚 CHỌN RỒNG TỪ DANH MỤC (KHÔNG CẦN GÕ TAY)", font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color=C["cyan"]).pack(anchor="w", padx=12, pady=(10, 6))

        # Ô tìm kiếm nhanh
        self.var_search_dragon = tk.StringVar(value="")
        self.var_search_dragon.trace_add("write", lambda *_a: self._filter_catalog_list())
        ctk.CTkEntry(
            lib_card, textvariable=self.var_search_dragon, height=32, corner_radius=8,
            fg_color=C["console"], placeholder_text="🔍 Tìm tên rồng (vd: lua xanh bien, ky lan, hai dau)..."
        ).pack(fill="x", padx=12, pady=(0, 6))

        # Bộ lọc theo 5 độ hiếm
        self.var_filter_rarity = tk.StringVar(value="Tất cả")
        ctk.CTkSegmentedButton(
            lib_card,
            values=["Tất cả", "Phổ biến", "Nguyên Liệu", "Hiếm", "Cực hiếm", "Event"],
            variable=self.var_filter_rarity,
            command=lambda _v: self._filter_catalog_list(),
            font=ctk.CTkFont("Segoe UI", 10, "bold"),
            corner_radius=8,
        ).pack(fill="x", padx=12, pady=(0, 6))

        # Danh sách rồng khớp bộ lọc
        self.lb_catalog = tk.Listbox(
            lib_card, bg=C["console"], fg=C["text"], selectbackground="#0284c7",
            selectmode=tk.EXTENDED, borderwidth=0, highlightthickness=1,
            highlightbackground=C["border"], font=("Segoe UI", 10)
        )
        self.lb_catalog.pack(fill="both", expand=True, padx=12, pady=(0, 8))

        # Khung chọn Hành động + Điều kiện Sao + Nút Thêm
        add_box = ctk.CTkFrame(lib_card, corner_radius=10, fg_color=C["card2"])
        add_box.pack(fill="x", padx=12, pady=(0, 10))

        r_act = ctk.CTkFrame(add_box, fg_color="transparent")
        r_act.pack(fill="x", padx=8, pady=(8, 4))
        ctk.CTkLabel(r_act, text="Đưa vào:", text_color=C["muted"], font=ctk.CTkFont("Segoe UI", 11)).pack(side="left", padx=(0, 6))
        self.var_add_target = tk.StringVar(value="✨ Chúc phúc")
        ctk.CTkSegmentedButton(
            r_act, values=["✨ Chúc phúc", "📦 Cất", "💰 Bán"], variable=self.var_add_target, corner_radius=8
        ).pack(side="left", fill="x", expand=True)

        r_star = ctk.CTkFrame(add_box, fg_color="transparent")
        r_star.pack(fill="x", padx=8, pady=(2, 6))
        ctk.CTkLabel(r_star, text="Số sao:", text_color=C["muted"], font=ctk.CTkFont("Segoe UI", 11)).pack(side="left", padx=(0, 6))
        self.var_star_op = tk.StringVar(value="Mọi sao")
        ctk.CTkSegmentedButton(
            r_star, values=["Mọi sao", ">= sao", "= sao"], variable=self.var_star_op, width=165, corner_radius=8
        ).pack(side="left", padx=(0, 6))
        self.var_star_num = tk.StringVar(value="23")
        ctk.CTkEntry(r_star, textvariable=self.var_star_num, width=48, height=28, corner_radius=6, fg_color=C["console"]).pack(side="left")

        r_btn = ctk.CTkFrame(add_box, fg_color="transparent")
        r_btn.pack(fill="x", padx=8, pady=(0, 8))
        ctk.CTkButton(
            r_btn, text="➕ Thêm rồng đã chọn vào luật", height=34, corner_radius=8,
            fg_color=C["green"], hover_color=C["green_hv"], text_color="#052e16",
            font=ctk.CTkFont("Segoe UI", 12, "bold"), command=self._add_selected_catalog_dragons
        ).pack(side="left", fill="x", expand=True, padx=(0, 6))
        ctk.CTkButton(
            r_btn, text="+ Tên mới", width=78, height=34, corner_radius=8,
            fg_color=C["card"], hover_color=C["border"], font=ctk.CTkFont("Segoe UI", 11),
            command=self._add_custom_name_from_picker
        ).pack(side="right")

        r_pre = ctk.CTkFrame(add_box, fg_color="transparent")
        r_pre.pack(fill="x", padx=8, pady=(0, 8))
        ctk.CTkButton(
            r_pre, text="🎯 1-Bấm: Nhiệm vụ nhanh (Bán PB+NL, Cất Hiếm+)", height=28, corner_radius=8,
            fg_color="#b45309", hover_color="#92400e", font=ctk.CTkFont("Segoe UI", 11, "bold"),
            command=self._preset_fast_quest
        ).pack(fill="x")

        # ---- CỘT PHẢI: CÁC NHÓM LUẬT ĐANG ÁP DỤNG ----
        right_col = ctk.CTkScrollableFrame(split, fg_color="transparent")
        right_col.grid(row=0, column=1, sticky="nsew")

        rule_groups = [
            ("parents", "🧬 BỐ / MẸ", C["cyan"], False),
            ("bless", "✨ CHÚC PHÚC", C["amber"], True),
            ("keep", "📦 CẤT TÚI", C["green"], True),
            ("sell", "💰 BÁN NGAY", "#fb7185", True),
            ("dust_targets", "🌪️ HÓA BỤI", C["purple"], False),
        ]

        for key, title, color, allow_capture in rule_groups:
            card = ctk.CTkFrame(right_col, corner_radius=12, fg_color=C["card"], border_width=1, border_color=C["border"])
            card.pack(fill="x", pady=(0, 8))

            top = ctk.CTkFrame(card, fg_color="transparent")
            top.pack(fill="x", padx=10, pady=(8, 4))
            ctk.CTkLabel(top, text=title, font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color=color).pack(side="left")
            ctk.CTkButton(
                top, text="Xóa", width=44, height=24, corner_radius=6,
                fg_color="#450a0a", hover_color="#7f1d1d", text_color="#fca5a5",
                font=ctk.CTkFont("Segoe UI", 10, "bold"), command=lambda fk=key: self._delete_dragon_entry(fk)
            ).pack(side="right")

            if allow_capture:
                ctk.CTkButton(
                    top, text="Dọn", width=44, height=24, corner_radius=6,
                    fg_color=C["card2"], hover_color="#7f1d1d", text_color=C["muted"],
                    font=ctk.CTkFont("Segoe UI", 10, "bold"), command=lambda fk=key, t=title: self._clear_dragon_group(fk, t)
                ).pack(side="right", padx=(0, 4))
                ctk.CTkButton(
                    top, text="+ Chụp Tên", width=80, height=24, corner_radius=6,
                    fg_color="#0284c7", hover_color="#0369a1", font=ctk.CTkFont("Segoe UI", 10, "bold"),
                    command=lambda fk=key, t=title: self._capture_dragon_template(fk, t, "ten_")
                ).pack(side="right", padx=(0, 4))
            else:
                lbl_c = "+ Mẫu Bụi" if key == "dust_targets" else "+ Chụp Rồng"
                ctk.CTkButton(
                    top, text=lbl_c, width=90, height=24, corner_radius=6,
                    fg_color="#0284c7", hover_color="#0369a1", font=ctk.CTkFont("Segoe UI", 10, "bold"),
                    command=lambda fk=key, t=title: self._capture_dragon_template(fk, t, "")
                ).pack(side="right", padx=(0, 4))

            h_rows = 3 if key in ("parents", "dust_targets") else 4
            lb = tk.Listbox(
                card, height=h_rows, bg=C["console"], fg=C["text"], selectbackground="#0284c7",
                borderwidth=0, highlightthickness=1, highlightbackground=C["border"], font=("Segoe UI", 9)
            )
            lb.pack(fill="x", padx=10, pady=(0, 8))
            self._dragon_listboxes[key] = lb

        self._filter_catalog_list()

    def _filter_catalog_list(self) -> None:
        if not hasattr(self, "lb_catalog"):
            return
        q = normalize_vn(self.var_search_dragon.get())
        rar = self.var_filter_rarity.get()
        self.lb_catalog.delete(0, tk.END)
        self._filtered_catalog = []
        for item in self.catalog:
            name = item.get("name", "")
            r = item.get("rarity", "")
            if rar != "Tất cả" and r != rar:
                continue
            if q and q not in normalize_vn(name) and q not in normalize_vn(r):
                continue
            self._filtered_catalog.append(item)
            self.lb_catalog.insert(tk.END, f"[{r}]  {name}")

    def _get_selected_target_key(self) -> str:
        t = self.var_add_target.get()
        if "Chúc" in t:
            return "bless"
        if "Bán" in t:
            return "sell"
        return "keep"

    def _get_selected_star_cond(self):
        op_lbl = self.var_star_op.get()
        if op_lbl == "Mọi sao":
            return "any", None
        try:
            val = int(self.var_star_num.get().strip())
        except ValueError:
            val = 23
        return (">=" if ">=" in op_lbl else "="), val

    def _add_selected_catalog_dragons(self) -> None:
        sels = self.lb_catalog.curselection()
        if not sels:
            messagebox.showinfo("Chưa chọn rồng", "Hãy bấm chọn 1 hoặc nhiều rồng trong danh sách bên trái trước nhé.")
            return
        target_key = self._get_selected_target_key()
        star_op, star_val = self._get_selected_star_cond()
        rules = self.config_data.setdefault("dragon_text_rules", {}).setdefault(target_key, [])
        added = 0
        for idx in sels:
            name = self._filtered_catalog[idx]["name"]
            rule_str = format_rule_string(name, star_op, star_val)
            if rule_str not in rules:
                rules.append(rule_str)
                added += 1
        if added:
            save_config(self.config_data)
            self.append_log(f"[QUẢN LÝ] Đã thêm {added} rồng vào nhóm [{target_key}].")
            self._refresh_all_lists()

    def _add_custom_name_from_picker(self) -> None:
        target_key = self._get_selected_target_key()
        self._add_text_dragon_rule(target_key, self.var_add_target.get())

    def _preset_fast_quest(self) -> None:
        """Cài nhanh 1 bấm cho nhiệm vụ săn rồng: Bán sạch Phổ biến & Nguyên liệu, tự Cất Hiếm/Cực hiếm/Event."""
        self._bulk_add_rarity("Phổ biến", "sell")
        self._bulk_add_rarity("Nguyên Liệu", "sell")
        self._bulk_add_rarity("Hiếm", "keep")
        self._bulk_add_rarity("Cực hiếm", "keep")
        self._bulk_add_rarity("Event", "keep")
        self.append_log("[PRESET] Đã cài nhanh bộ luật 'Nhiệm vụ siêu tốc' (Bán Phổ biến+NL, Cất Hiếm/Cực hiếm/Event).")

    def _bulk_add_rarity(self, rarity_name: str, target_key: str) -> None:
        rules = self.config_data.setdefault("dragon_text_rules", {}).setdefault(target_key, [])
        added = 0
        for item in self.catalog:
            if item.get("rarity") == rarity_name:
                name = item["name"]
                if name not in rules:
                    rules.append(name)
                    added += 1
        save_config(self.config_data)
        self.append_log(f"[QUẢN LÝ] Đã thêm nhanh {added} rồng nhóm '{rarity_name}' vào [{target_key}].")
        self._refresh_all_lists()

    # ------------------------------------------------------------------
    # TRANG 3: Vùng & Nút Game (Lưới 2x3 không bị cắt chữ + Ghi chú rõ ràng)
    # ------------------------------------------------------------------
    def _build_page_setup(self) -> None:
        page = ctk.CTkFrame(self.content, fg_color="transparent")
        self._pages["setup"] = page

        ctk.CTkLabel(page, text="Vùng & Nút Game (Đã cài sẵn 17 nút)", font=ctk.CTkFont("Segoe UI", 22, "bold"), text_color=C["text"]).pack(anchor="w", pady=(0, 6))
        self._tip_banner(
            page,
            "Bộ 17 nút game bên dưới đã được cài sẵn. Bạn chỉ cần bấm Ghim Trứng / Ghim Tháp hoặc khoanh lại 1 vùng nào đó nếu máy bạn hiển thị lệch so với mặc định."
        )

        # Lưới 2 hàng x 3 cột: KHÔNG BAO GIỜ bị cắt chữ, mỗi nút có giải thích rõ!
        pin_card = ctk.CTkFrame(page, corner_radius=14, fg_color=C["card"], border_width=1, border_color=C["border"])
        pin_card.pack(fill="x", pady=(0, 12))
        pin_grid = ctk.CTkFrame(pin_card, fg_color="transparent")
        pin_grid.pack(fill="x", padx=10, pady=10)
        for col_i in range(3):
            pin_grid.columnconfigure(col_i, weight=1)

        cal_specs = [
            (0, 0, "📌 Ghim tâm Trứng", "Chống rồng bay che trứng ở đảo lai", C["green"], "#052e16", self._pin_egg_point),
            (0, 1, "📌 Ghim tâm Tháp", "Chống rồng bay che tháp tẩy tủy", C["amber"], "#1c1917", self._pin_altar_point),
            (0, 2, "📐 Vùng Thanh Rồng", "Khung 4 ô rồng bố mẹ phía dưới", "#0284c7", "#ffffff", self._calibrate_bottom_bar),
            (1, 0, "📐 Vùng Ảnh Kết Quả", "Khung rồng con + tên khi lai xong", "#0284c7", "#ffffff", self._calibrate_result_zone),
            (1, 1, "📐 Dòng Tên Rồng", "Đúng 1 dòng tên góc phải (để đọc chữ)", "#7c3aed", "#ffffff", self._calibrate_name_bar),
            (1, 2, "📐 Túi Hóa Bụi (2 Cột)", "Cả 2 cột rồng trong Túi Chứa Rồng", "#0284c7", "#ffffff", self._calibrate_dust_list),
        ]
        for r, c, title, desc, bg_col, fg_col, cmd in cal_specs:
            cell = ctk.CTkFrame(pin_grid, corner_radius=10, fg_color=C["console"], border_width=1, border_color=C["border"])
            cell.grid(row=r, column=c, sticky="nsew", padx=5, pady=4)
            ctk.CTkButton(
                cell, text=title, height=30, corner_radius=8, fg_color=bg_col, text_color=fg_col,
                font=ctk.CTkFont("Segoe UI", 12, "bold"), command=cmd
            ).pack(fill="x", padx=8, pady=(8, 3))
            ctk.CTkLabel(
                cell, text=desc, font=ctk.CTkFont("Segoe UI", 10), text_color=C["muted"], wraplength=230
            ).pack(anchor="w", padx=10, pady=(0, 7))

        scroll_ui = ctk.CTkScrollableFrame(
            page, corner_radius=14, fg_color=C["card"], border_width=1, border_color=C["border"],
            label_text="DANH SÁCH ẢNH NÚT GAME (Chụp lại nếu máy bạn có giao diện khác)",
            label_text_color=C["cyan"], label_font=ctk.CTkFont("Segoe UI", 12, "bold")
        )
        scroll_ui.pack(fill="both", expand=True)
        scroll_ui.columnconfigure(0, weight=1)
        scroll_ui.columnconfigure(1, weight=1)

        half = (len(REQUIRED_UI_TEMPLATES) + 1) // 2
        for idx, (fname, desc, req) in enumerate(REQUIRED_UI_TEMPLATES):
            col = 0 if idx < half else 1
            row = idx if idx < half else idx - half
            item = ctk.CTkFrame(scroll_ui, corner_radius=8, fg_color=C["console"], border_width=1, border_color=C["border"])
            item.grid(row=row, column=col, sticky="ew", padx=(4 if col == 0 else 8, 4), pady=3)

            st = ctk.CTkLabel(item, text="❌", width=28, font=ctk.CTkFont("Segoe UI", 12))
            st.pack(side="left", padx=(6, 2))
            self._ui_status_lbls[fname] = st

            prefix = "[*] " if req else ""
            ctk.CTkLabel(item, text=f"{prefix}{desc}", font=ctk.CTkFont("Segoe UI", 12), text_color=C["text"]).pack(side="left", padx=4)
            ctk.CTkButton(
                item, text="Chụp lại", width=68, height=26, corner_radius=6,
                fg_color=C["card2"], hover_color="#0284c7", text_color=C["cyan"],
                font=ctk.CTkFont("Segoe UI", 11, "bold"),
                command=lambda f=fname, d=desc: self._capture_ui_template(f, d)
            ).pack(side="right", padx=6, pady=4)

    # ------------------------------------------------------------------
    # TRANG 4: Cài Đặt (Gọn gàng, 100% tiếng Việt dễ hiểu, không số rối mắt)
    # ------------------------------------------------------------------
    def _build_page_settings(self) -> None:
        page = ctk.CTkFrame(self.content, fg_color="transparent")
        self._pages["settings"] = page

        ctk.CTkLabel(page, text="Cài Đặt & Tiện Ích", font=ctk.CTkFont("Segoe UI", 22, "bold"), text_color=C["text"]).pack(anchor="w", pady=(0, 6))

        save_footer = ctk.CTkFrame(page, fg_color="transparent")
        save_footer.pack(side="bottom", fill="x", pady=(8, 0))
        ctk.CTkButton(
            save_footer, text="💾 Lưu toàn bộ cài đặt (Cơ Bản + Nâng Cao)", height=38, corner_radius=8,
            fg_color=C["green"], hover_color=C["green_hv"], text_color="#052e16",
            font=ctk.CTkFont("Segoe UI", 12, "bold"), command=self._save_all_settings
        ).pack(side="left")
        ctk.CTkLabel(
            save_footer, text="💡 Thay đổi chế độ chạy ở Bảng Điều Khiển sẽ được tự động lưu ngầm.",
            font=ctk.CTkFont("Segoe UI", 11), text_color=C["muted"]
        ).pack(side="right")

        tabs = ctk.CTkTabview(page, corner_radius=12, fg_color=C["card"], segmented_button_fg_color=C["console"])
        tabs.pack(fill="both", expand=True)
        tab_basic = tabs.add("⚙️ Cơ Bản")
        tab_adv = tabs.add("🔧 Nâng Cao & Nút Game")
        tab_stats = tabs.add("📊 Thống Kê Cặp Lai")
        tab_guide = tabs.add("📖 Hướng Dẫn")

        # ==================== TAB 1: CƠ BẢN (GỌN CHO NGƯỜI MỚI) ====================
        sc_basic = ctk.CTkScrollableFrame(tab_basic, fg_color="transparent")
        sc_basic.pack(fill="both", expand=True)

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
        self.var_human_like = tk.BooleanVar(value=bool(self.config_data.get("human_like_input", True)))
        self.var_sound_alert = tk.BooleanVar(value=bool(self.config_data.get("sound_alert", True)))
        self.var_verbose_log = tk.BooleanVar(value=bool(self.config_data.get("verbose_log", False)))
        self.var_human_break = tk.BooleanVar(value=bool(self.config_data.get("human_break_enabled", True)))
        self.var_save_rare = tk.BooleanVar(value=bool(self.config_data.get("save_rare_snapshots", True)))
        self.var_collect_ai = tk.BooleanVar(value=bool(self.config_data.get("collect_ai_dataset", False)))

        c1 = ctk.CTkFrame(sc_basic, corner_radius=12, fg_color=C["console"], border_width=1, border_color=C["border"])
        c1.pack(fill="x", pady=(0, 8))
        g1 = ctk.CTkFrame(c1, fg_color="transparent")
        g1.pack(fill="x", padx=14, pady=10)
        ctk.CTkLabel(g1, text="Lọc sao khi Hóa Bụi (tối đa):", font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color=C["text"]).grid(row=0, column=0, sticky="w", pady=5, padx=(0, 10))
        ctk.CTkEntry(g1, textvariable=self.var_dust_max_star, width=100, fg_color=C["card"]).grid(row=0, column=1, sticky="w", pady=5, padx=(0, 12))
        ctk.CTkLabel(g1, text="0 = không giới hạn sao. Nhập 23 = chỉ hóa bụi <= 23★ (mờ sao tự chừa an toàn!)", text_color=C["muted"], font=ctk.CTkFont("Segoe UI", 11)).grid(row=0, column=2, sticky="w")

        rar_row = ctk.CTkFrame(c1, fg_color="transparent")
        rar_row.pack(fill="x", padx=14, pady=(0, 8))
        ctk.CTkLabel(rar_row, text="Độ hiếm được Hóa Bụi:", font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color=C["text"]).pack(side="left", padx=(0, 10))
        saved_dr = set(self.config_data.get("dust_rarities", ["Nguyên Liệu"]))
        self.var_dust_rars: Dict[str, tk.BooleanVar] = {}
        for r_lbl in ("Phổ biến", "Nguyên Liệu", "Hiếm", "Cực hiếm", "Event"):
            v = tk.BooleanVar(value=(r_lbl in saved_dr))
            self.var_dust_rars[r_lbl] = v
            ctk.CTkCheckBox(rar_row, text=r_lbl, variable=v, width=95, font=ctk.CTkFont("Segoe UI", 11)).pack(side="left", padx=(0, 8))

        sw = ctk.CTkFrame(c1, fg_color="transparent")
        sw.pack(fill="x", padx=14, pady=(0, 10))
        ctk.CTkSwitch(sw, text="Thao tác tự nhiên giống người thật (di chuột cong + lệch điểm & nhịp bấm ngẫu nhiên)", variable=self.var_human_like).pack(anchor="w", pady=3)
        ctk.CTkSwitch(sw, text="Phát tiếng chuông & hiện thông báo nổi trên màn hình khi đầy túi / nổ rồng mục tiêu", variable=self.var_sound_alert).pack(anchor="w", pady=3)
        ctk.CTkSwitch(sw, text="Tự động kiểm tra bản cập nhật mới", variable=self.var_auto_update).pack(anchor="w", pady=3)
        ctk.CTkSwitch(sw, text="Nghỉ giải lao tự nhiên (tự nghỉ 1–2 phút mỗi 45 phút treo máy như người thật)", variable=self.var_human_break).pack(anchor="w", pady=3)
        ctk.CTkSwitch(sw, text="Tự chụp lưu ảnh khi nổ Rồng Cực hiếm / Event / Chúc phúc (giới hạn 30 ảnh mới nhất)", variable=self.var_save_rare).pack(anchor="w", pady=3)
        ctk.CTkSwitch(sw, text="Hiện nhật ký kỹ thuật chi tiết (tọa độ, điểm khớp — chỉ bật khi cần kiểm tra lỗi)", variable=self.var_verbose_log).pack(anchor="w", pady=3)

        c_stop = ctk.CTkFrame(sc_basic, corner_radius=12, fg_color=C["console"], border_width=1, border_color=C["border"])
        c_stop.pack(fill="x", pady=(0, 8))
        as_cfg = self.config_data.get("auto_stop", {})
        self.var_as_enabled = tk.BooleanVar(value=bool(as_cfg.get("enabled", False)))
        self.var_as_mins = tk.StringVar(value=str(as_cfg.get("max_minutes", 0)))
        self.var_as_breeds = tk.StringVar(value=str(as_cfg.get("max_breeds", 0)))
        self.var_as_rare = tk.StringVar(value=str(as_cfg.get("stop_on_rare_count", 0)))
        self.var_as_epic = tk.BooleanVar(value=bool(as_cfg.get("stop_on_epic_or_event", False)))

        as_hdr = ctk.CTkFrame(c_stop, fg_color="transparent")
        as_hdr.pack(fill="x", padx=14, pady=(8, 4))
        ctk.CTkLabel(as_hdr, text="⏱️ HẸN GIỜ & TỰ DỪNG KHI ĐẠT MỤC TIÊU", font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color=C["amber"]).pack(side="left")
        ctk.CTkSwitch(as_hdr, text="Bật tự dừng", variable=self.var_as_enabled).pack(side="right")

        as_grid = ctk.CTkFrame(c_stop, fg_color="transparent")
        as_grid.pack(fill="x", padx=14, pady=(2, 10))
        ctk.CTkLabel(as_grid, text="Sau (phút):", text_color=C["text"]).grid(row=0, column=0, sticky="w", padx=(0, 6), pady=4)
        ctk.CTkEntry(as_grid, textvariable=self.var_as_mins, width=70, fg_color=C["card"]).grid(row=0, column=1, padx=(0, 14), pady=4)
        ctk.CTkLabel(as_grid, text="Sau (lượt lai):", text_color=C["text"]).grid(row=0, column=2, sticky="w", padx=(0, 6), pady=4)
        ctk.CTkEntry(as_grid, textvariable=self.var_as_breeds, width=70, fg_color=C["card"]).grid(row=0, column=3, padx=(0, 14), pady=4)
        ctk.CTkLabel(as_grid, text="Đủ (con Hiếm+):", text_color=C["text"]).grid(row=0, column=4, sticky="w", padx=(0, 6), pady=4)
        ctk.CTkEntry(as_grid, textvariable=self.var_as_rare, width=70, fg_color=C["card"]).grid(row=0, column=5, pady=4)
        ctk.CTkCheckBox(as_grid, text="Dừng & báo chuông ngay khi nổ Rồng Cực hiếm hoặc Event", variable=self.var_as_epic).grid(row=1, column=0, columnspan=6, sticky="w", pady=(6, 0))

        c_prof = ctk.CTkFrame(sc_basic, corner_radius=12, fg_color=C["console"], border_width=1, border_color=C["border"])
        c_prof.pack(fill="x", pady=(0, 8))
        pf_row = ctk.CTkFrame(c_prof, fg_color="transparent")
        pf_row.pack(fill="x", padx=14, pady=10)
        ctk.CTkLabel(pf_row, text="📁 PROFILE NHANH:", font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color=C["cyan"]).pack(side="left", padx=(0, 8))
        self.var_profile_sel = tk.StringVar(value=self.config_data.get("active_profile", "Mặc định"))
        self.combo_profile = ctk.CTkOptionMenu(pf_row, variable=self.var_profile_sel, values=self._list_profiles(), width=165, fg_color=C["card"], button_color=C["card2"])
        self.combo_profile.pack(side="left", padx=(0, 6))
        ctk.CTkButton(pf_row, text="📂 Tải", width=75, height=28, fg_color="#0284c7", command=self._load_selected_profile).pack(side="left", padx=(0, 6))
        ctk.CTkButton(pf_row, text="💾 Lưu Profile mới", width=130, height=28, fg_color=C["purple"], command=self._save_new_profile).pack(side="left", padx=(0, 6))
        ctk.CTkButton(pf_row, text="✕ Xóa", width=60, height=28, fg_color="#7f1d1d", command=self._delete_selected_profile).pack(side="left")



        # ==================== TAB 2: NÂNG CAO & VÙNG/NÚT GAME ====================
        sc_adv = ctk.CTkScrollableFrame(tab_adv, fg_color="transparent")
        sc_adv.pack(fill="both", expand=True)

        adv_box = ctk.CTkFrame(sc_adv, corner_radius=12, fg_color=C["console"], border_width=1, border_color=C["border"])
        adv_box.pack(fill="x", pady=(0, 8))
        ag = ctk.CTkFrame(adv_box, fg_color="transparent")
        ag.pack(fill="x", padx=14, pady=10)

        ctk.CTkLabel(ag, text="Phần mềm giả lập:", text_color=C["text"], font=ctk.CTkFont("Segoe UI", 12, "bold")).grid(row=0, column=0, sticky="w", pady=5, padx=(0, 10))
        ctk.CTkComboBox(ag, variable=self.var_win_title, values=["AUTO", "LDPlayer", "BlueStacks", "MuMu", "Nox", "MEmu"], width=165, fg_color=C["card"]).grid(row=0, column=1, sticky="w", pady=5, padx=(0, 12))
        ctk.CTkLabel(ag, text="Số đảo sang Hóa Bụi:", text_color=C["text"], font=ctk.CTkFont("Segoe UI", 12, "bold")).grid(row=0, column=2, sticky="w", pady=5, padx=(0, 8))
        ctk.CTkEntry(ag, textvariable=self.var_hops, width=70, fg_color=C["card"]).grid(row=0, column=3, sticky="w", pady=5)

        ctk.CTkLabel(ag, text="Độ nhạy nhận diện:", text_color=C["text"], font=ctk.CTkFont("Segoe UI", 12, "bold")).grid(row=1, column=0, sticky="w", pady=5, padx=(0, 10))
        ctk.CTkSegmentedButton(ag, values=["Thoáng", "Chuẩn", "Chặt"], variable=self.var_accuracy, width=165).grid(row=1, column=1, sticky="w", pady=5, padx=(0, 12))
        ctk.CTkLabel(ag, text="Cảnh báo đơ (giây):", text_color=C["text"], font=ctk.CTkFont("Segoe UI", 12, "bold")).grid(row=1, column=2, sticky="w", pady=5, padx=(0, 8))
        ctk.CTkEntry(ag, textvariable=self.var_watchdog_sec, width=70, fg_color=C["card"]).grid(row=1, column=3, sticky="w", pady=5)

        ctk.CTkLabel(ag, text="Đường dẫn adb.exe:", text_color=C["text"], font=ctk.CTkFont("Segoe UI", 12, "bold")).grid(row=2, column=0, sticky="w", pady=5, padx=(0, 10))
        adb_pick_row = ctk.CTkFrame(ag, fg_color="transparent")
        adb_pick_row.grid(row=2, column=1, columnspan=3, sticky="w", pady=5)
        ctk.CTkEntry(adb_pick_row, textvariable=self.var_adb_path, width=250, fg_color=C["card"], placeholder_text="Để trống = tự tìm").pack(side="left", padx=(0, 6))
        ctk.CTkButton(adb_pick_row, text="📂 Chọn file", width=88, height=28, fg_color=C["card2"], hover_color=C["border"], command=self._browse_adb_exe).pack(side="left")

        adv_sw = ctk.CTkFrame(adv_box, fg_color="transparent")
        adv_sw.pack(fill="x", padx=14, pady=(0, 10))
        ctk.CTkSwitch(adv_sw, text="Phân biệt rồng theo màu sắc", variable=self.var_color_check).pack(anchor="w", pady=2)
        ctk.CTkSwitch(adv_sw, text="Tự động co giãn ảnh mẫu theo kích thước giả lập", variable=self.var_auto_scale).pack(anchor="w", pady=2)
        ctk.CTkSwitch(adv_sw, text="Bật nhận diện bằng tên (OCR 63 rồng + bộ nhớ AI tự học)", variable=self.var_enable_ocr).pack(anchor="w", pady=2)
        ctk.CTkSwitch(adv_sw, text="Thu thập mẫu ảnh tự động để train AI YOLOv8-nano (trần 300 ảnh vào thư mục ai_dataset/)", variable=self.var_collect_ai).pack(anchor="w", pady=2)

        ai_mem_row = ctk.CTkFrame(adv_box, fg_color="transparent")
        ai_mem_row.pack(fill="x", padx=14, pady=(4, 10))
        self.lbl_ai_cache_info = ctk.CTkLabel(
            ai_mem_row, text=f"🧠 Bộ nhớ AI tự học: {self.shared_name_cache.count} / {PersistentNameCache.MAX_ENTRIES} mẫu (<1MB)",
            font=ctk.CTkFont("Segoe UI", 11, "bold"), text_color=C["cyan"]
        )
        self.lbl_ai_cache_info.pack(side="left")
        ctk.CTkButton(ai_mem_row, text="🧹 Dọn bộ nhớ AI", width=130, height=28, fg_color="#7f1d1d", hover_color="#991b1b", command=self._clear_ai_cache).pack(side="right", padx=(6, 0))
        ctk.CTkButton(ai_mem_row, text="🖥️ Lưu kích thước giả lập", width=165, height=28, fg_color="#0284c7", command=self._record_reference_size).pack(side="right")
        ctk.CTkButton(ai_mem_row, text="📸 Ảnh Rồng Quý", width=120, height=28, fg_color="#065f46", command=self._open_rare_shots_folder).pack(side="right", padx=(0, 6))

        cal_box = ctk.CTkFrame(sc_adv, corner_radius=12, fg_color=C["console"], border_width=1, border_color=C["border"])
        cal_box.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(cal_box, text="🎯 CĂN CHỈNH VÙNG & GHIM ĐIỂM (Chỉ dùng nếu máy bạn bị lệch nút)", font=ctk.CTkFont("Segoe UI", 11, "bold"), text_color=C["amber"]).pack(anchor="w", padx=12, pady=(8, 6))
        cg = ctk.CTkFrame(cal_box, fg_color="transparent")
        cg.pack(fill="x", padx=12, pady=(0, 10))
        cal_specs = [
            ("📌 Ghim tâm Trứng", "#059669", self._pin_egg_point),
            ("📌 Ghim tâm Tháp", "#d97706", self._pin_altar_point),
            ("📐 Khung Thanh Rồng", "#0284c7", self._calibrate_bottom_bar),
            ("📐 Khung Ảnh Rồng Lai", "#0284c7", self._calibrate_result_zone),
            ("📐 Khung Dòng Tên Rồng", "#7c3aed", self._calibrate_name_bar),
            ("📐 Khung Túi Hóa Bụi", "#0284c7", self._calibrate_dust_list),
        ]
        for idx, (label, col, cmd) in enumerate(cal_specs):
            r, c_i = divmod(idx, 3)
            ctk.CTkButton(cg, text=label, height=32, corner_radius=8, fg_color=col, font=ctk.CTkFont("Segoe UI", 11, "bold"), command=cmd).grid(row=r, column=c_i, sticky="ew", padx=4, pady=3)
            cg.columnconfigure(c_i, weight=1)

        ui_box = ctk.CTkFrame(sc_adv, corner_radius=12, fg_color=C["console"], border_width=1, border_color=C["border"])
        ui_box.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(ui_box, text="🖼️ 18 ẢNH NÚT MẪU (Đã setup sẵn — chỉ chụp lại nếu bị lệch)", font=ctk.CTkFont("Segoe UI", 11, "bold"), text_color=C["cyan"]).pack(anchor="w", padx=12, pady=(8, 6))
        ug = ctk.CTkFrame(ui_box, fg_color="transparent")
        ug.pack(fill="x", padx=10, pady=(0, 10))
        ug.columnconfigure(0, weight=1)
        ug.columnconfigure(1, weight=1)
        for idx, (fname, desc, required) in enumerate(REQUIRED_UI_TEMPLATES):
            r, c_i = divmod(idx, 2)
            row = ctk.CTkFrame(ug, corner_radius=8, fg_color=C["card"])
            row.grid(row=r, column=c_i, sticky="ew", padx=4, pady=2)
            st = ctk.CTkLabel(row, text="...", width=24, font=ctk.CTkFont("Segoe UI", 12, "bold"))
            st.pack(side="left", padx=(8, 4), pady=4)
            self._ui_status_lbls[fname] = st
            ctk.CTkLabel(row, text=f"{'[*] ' if required else ''}{desc}", text_color=C["text"], font=ctk.CTkFont("Segoe UI", 11)).pack(side="left", fill="x", expand=True)
            ctk.CTkButton(row, text="Chụp", width=58, height=24, corner_radius=6, fg_color=C["card2"], command=lambda f=fname, d=desc: self._capture_ui_template(f, d)).pack(side="right", padx=6, pady=4)

        # ==================== TAB 3: THỐNG KÊ HIỆU SUẤT CẶP BỐ MẸ ====================
        st_hdr = ctk.CTkFrame(tab_stats, fg_color="transparent")
        st_hdr.pack(fill="x", padx=8, pady=(6, 6))
        ctk.CTkLabel(st_hdr, text="📊 Tỷ lệ nổ rồng theo từng cặp bố mẹ (lưu gọn ~10KB, không phình ổ cứng)", font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color=C["cyan"]).pack(side="left")
        ctk.CTkButton(st_hdr, text="🗑️ Xóa thống kê", width=115, height=28, fg_color="#7f1d1d", command=self._clear_pair_stats).pack(side="right", padx=(6, 0))
        ctk.CTkButton(st_hdr, text="🔄 Làm mới", width=90, height=28, fg_color="#0284c7", command=self._refresh_pair_stats_ui).pack(side="right")

        self.txt_pair_stats = tk.Text(
            tab_stats, state=tk.DISABLED, bg=C["console"], fg=C["text"],
            font=("Consolas", 10), relief=tk.FLAT, padx=12, pady=10, highlightthickness=0
        )
        self.txt_pair_stats.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self._refresh_pair_stats_ui()

        # ==================== TAB 4: HƯỚNG DẪN NHANH ====================
        sc_guide = ctk.CTkScrollableFrame(tab_guide, fg_color="transparent")
        sc_guide.pack(fill="both", expand=True)
        sections = [
            ("1. Bắt đầu nhanh trong 30 giây cho người mới",
             "• Bước 1: Mở game Đảo Rồng trên LDPlayer (hoặc BlueStacks/MuMu).\n"
             "• Bước 2: Bấm 'Chụp 2 Bố Mẹ' và khoanh gọn 2 con rồng ở thanh dưới (tool tự kiểm tra độ chuẩn ngay khi chụp xong!).\n"
             "• Bước 3: Bấm 1 nút chọn mục tiêu (Cày Bụi hoặc Săn Hiếm), hoặc vào mục Luật Rồng gõ 1 câu tiếng Việt cho AI tự cài luật → bấm CHẠY!"),
            ("2. Chạy Ngầm & Treo Nhiều Acc không chiếm chuột",
             "• Trong LDPlayer → Cài đặt → Khác → Gỡ lỗi ADB → chọn 'Mở kết nối cục bộ' (chỉ bật 1 lần).\n"
             "• Trên Bảng Điều Khiển, chọn 'Chạy Ngầm / Nhiều Acc (ADB)' → bấm 'Quét giả lập ADB'. Mỗi acc có thể chọn Profile riêng và bấm Chạy/Tạm dừng độc lập!"),
            ("3. Tạm dừng / Tiếp tục / Chạy lại & AI Tự Học",
             "• CTRL+ENTER: Chạy hoặc Tạm dừng tại chỗ (bấm lại sẽ tự quét màn hình chạy tiếp đúng bước).\n"
             "• Nút '↻ Chạy lại từ đầu': Tự đóng các bảng đang mở và quay về Đảo Trứng.\n"
             "• AI tự học vĩnh viễn giới hạn trần 150 mẫu (<1MB) giúp lần sau nhận diện rồng trong 2ms và giảm ~70% CPU."),
        ]
        for title, body in sections:
            box = ctk.CTkFrame(sc_guide, corner_radius=10, fg_color=C["console"], border_width=1, border_color=C["border"])
            box.pack(fill="x", padx=6, pady=5)
            ctk.CTkLabel(box, text=title, font=ctk.CTkFont("Segoe UI", 13, "bold"), text_color=C["cyan"]).pack(anchor="w", padx=12, pady=(8, 3))
            ctk.CTkLabel(box, text=body, font=ctk.CTkFont("Segoe UI", 12), text_color=C["text"], justify="left", wraplength=740).pack(anchor="w", padx=12, pady=(0, 8))

    # ------------------------------------------------------------------
    # Các hàm AI v10.0: Cài luật tiếng Việt, Kiểm tra nhanh, Thống kê cặp, Chạy từng acc ADB
    # ------------------------------------------------------------------
    def _open_rare_shots_folder(self) -> None:
        d = BASE_DIR / "lich_su_rong_quy"
        d.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform == "win32":
                os.startfile(str(d))
            else:
                messagebox.showinfo("Thư mục ảnh Rồng Quý", f"Đường dẫn: {d}")
        except Exception as exc:
            messagebox.showerror("Không mở được thư mục", str(exc))

    def _browse_adb_exe(self) -> None:
        fp = filedialog.askopenfilename(
            title="Chọn file adb.exe (hoặc HD-Adb.exe)",
            filetypes=[("ADB Executable", "*.exe"), ("Tất cả file", "*.*")],
        )
        if fp:
            self.var_adb_path.set(fp)
            self._save_all_settings(silent=True)
            self.append_log(f"📂 Đã chọn đường dẫn ADB: {fp}")

    def _clear_dragon_group(self, folder_key: str, title: str) -> None:
        if not messagebox.askyesno("Xác nhận dọn nhóm", f"Xóa toàn bộ quy tắc tên trong nhóm {title}?"):
            return
        self.config_data.setdefault("dragon_text_rules", {}).setdefault(folder_key, []).clear()
        save_config(self.config_data)
        self._refresh_all_lists()
        self.append_log(f"🧹 Đã dọn sạch toàn bộ luật tên trong nhóm {title}.")

    def _test_read_live_dragon(self) -> None:
        """Đọc thử dòng tên rồng đang hiện trên bảng kết quả lai để người chơi kiểm chứng độ chuẩn trong 1 giây."""
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
                messagebox.showwarning(
                    "Thử đọc tên rồng",
                    "Chưa đọc thấy chữ nào ở vùng dòng tên bên phải.\nHãy mở bảng kết quả lai trên giả lập rồi bấm thử lại!",
                )
                return
            tm = self.shared_text_matcher.match_text_against_rules(raw, self.config_data.get("dragon_text_rules", {}))
            act_map = {"bless": "✨ CHÚC PHÚC & CẤT", "keep": "📦 CẤT VÀO TÚI", "sell": "💰 BÁN NGAY"}
            act_str = act_map.get(tm.category, f"Mặc định ({self.var_unknown_vn.get()})") if tm.found else f"Chưa có luật -> {self.var_unknown_vn.get()}"
            st_str = f"{tm.stars} sao" if tm.stars else "Chưa rõ sao"
            msg = (
                f"• Chữ đọc thô: {raw}\n"
                f"• Nhận diện: {tm.identified_name or 'Chưa rõ'} [{tm.identified_rarity or '?'}] — {st_str}\n"
                f"• Hành động theo luật hiện tại: {act_str}"
            )
            self.append_log(f"🧪 [Thử đọc] {tm.identified_name or raw} ({st_str}) -> {act_str}")
            messagebox.showinfo("Kết Quả Thử Đọc Rồng", msg)
        except Exception as exc:
            messagebox.showerror("Lỗi thử đọc", str(exc))

    def _quick_goal_setup(self, goal: str) -> None:
        if goal == "dust":
            self.var_bot_mode_vn.set("Lai + Hóa Bụi")
            self._bulk_add_rarity("Phổ biến", "sell")
            self._bulk_add_rarity("Nguyên Liệu", "keep")
            self._bulk_add_rarity("Hiếm", "keep")
            self._bulk_add_rarity("Cực hiếm", "keep")
            self._bulk_add_rarity("Event", "keep")
            self._on_mode_changed()
            self.append_log("🎯 Đã cài nhanh mục tiêu 'Cày Bụi 24/7' (Bán Phổ biến, Cất Nguyên liệu & Rồng quý, tự Hóa Bụi khi đầy túi).")
        else:
            self.var_bot_mode_vn.set("Chỉ Auto Lai (Săn Rồng)")
            self._preset_fast_quest()
            self._on_mode_changed()
            self.append_log("🎯 Đã cài nhanh mục tiêu 'Săn Rồng Hiếm / Cực Hiếm' (Bán Phổ biến & Nguyên liệu, chỉ giữ rồng quý, không hóa bụi).")

    def _apply_ai_natural_rule(self) -> None:
        prompt = self.var_ai_rule.get().strip()
        if not prompt:
            messagebox.showinfo("AI Cài Luật", "Hãy gõ 1 câu tiếng Việt (hoặc bấm nút Mẫu 1 / Mẫu 2) trước nhé!")
            return
        parsed = self.rule_parser.parse(prompt)
        if parsed["matched_count"] == 0:
            messagebox.showwarning(
                "Chưa hiểu câu lệnh",
                "AI chưa nhận diện được hành động (Bán / Cất / Chúc phúc) hoặc tên/nhóm rồng.\nVí dụ: 'Bán hết phổ biến, cất nguyên liệu, chúc phúc rồng lửa xanh biển từ 23 sao trở lên'."
            )
            return
        preview = "\n".join(parsed["summary"])
        if not messagebox.askyesno("Xác nhận bộ luật AI vừa phân tích", f"AI sẽ áp dụng {parsed['matched_count']} quy tắc sau:\n\n{preview}\n\nBạn đồng ý áp dụng không?"):
            return
        rules = self.config_data.setdefault("dragon_text_rules", {"bless": [], "keep": [], "sell": []})
        for cat in ("bless", "keep", "sell"):
            for r in parsed["rules"][cat]:
                if r not in rules[cat]:
                    rules[cat].append(r)
        if parsed.get("want_dust_all"):
            self.config_data["dust_rarities"] = ["Phổ biến", "Nguyên Liệu", "Hiếm", "Cực hiếm", "Event"]
            if hasattr(self, "var_dust_rars"):
                for k, v in self.var_dust_rars.items():
                    v.set(True)
            # Nếu người chơi có đặt luật Chúc phúc theo sao (vd 24 sao), tự bật Khiên bảo vệ sao = 23 để không hóa bụi nhầm!
            if parsed["rules"]["bless"] and int(self.config_data.get("dust_max_star", 0) or 0) == 0:
                import re as _re
                stars_found = [int(m) for r_s in parsed["rules"]["bless"] for m in _re.findall(r"(\d{1,2})\s*sao", r_s)]
                if stars_found:
                    safe_max = max(1, min(stars_found) - 1)
                    self.config_data["dust_max_star"] = safe_max
                    if hasattr(self, "var_dust_max_star"):
                        self.var_dust_max_star.set(str(safe_max))
                    self.append_log(f"🛡️ [AI An Toàn] Đã tự bật Hóa Bụi đa độ hiếm + Khiên bảo vệ chỉ hóa bụi <= {safe_max}★ để bảo vệ rồng Chúc phúc!")
        save_config(self.config_data)
        self._refresh_all_lists()
        self.append_log(f"✨ [AI Luật Rồng] Đã thêm {parsed['matched_count']} quy tắc từ câu lệnh tiếng Việt!")

    def _run_preflight_ui(self) -> None:
        self._save_all_settings(silent=True)
        ctrl_mode = self.config_data.get("control_mode", "windows")
        adb_serial = ""
        if ctrl_mode == "adb":
            sel = [s for s, v in self.adb_device_vars.items() if v.get()]
            adb_serial = sel[0] if sel else ""
        tmp_bot = DaoRongBot(
            BASE_DIR, self.config_data, lambda _m: None, lambda _s: None,
            adb=self.adb_ctrl if ctrl_mode == "adb" else None,
            adb_serial=adb_serial,
            shared_text_matcher=self.shared_text_matcher,
            shared_name_cache=self.shared_name_cache,
            shared_calibrator=self.shared_calibrator,
            shared_pair_stats=self.shared_pair_stats,
        )
        checks = tmp_bot.run_preflight_check()
        lines = [f"{'✅' if ok else '❌'} {title}: {detail}" for ok, title, detail in checks]
        all_ok = all(ok for ok, _, _ in checks)
        header = "Mọi thứ đã SẴN SÀNG để chạy mượt!" if all_ok else "Có mục cần bổ sung trước khi chạy:"
        messagebox.showinfo("Kiểm Tra Nhanh (Pre-Flight Check)", f"{header}\n\n" + "\n".join(lines))

    def _clear_ai_cache(self) -> None:
        n = self.shared_name_cache.clear()
        self.shared_calibrator.clear()
        self.lbl_ai_cache_info.configure(text=f"🧠 Bộ nhớ AI tự học: 0 / {PersistentNameCache.MAX_ENTRIES} mẫu (<1MB)")
        self.append_log(f"🧹 Đã dọn sạch {n} mẫu trong bộ nhớ AI tự học.")

    def _refresh_pair_stats_ui(self) -> None:
        self.txt_pair_stats.configure(state=tk.NORMAL)
        self.txt_pair_stats.delete("1.0", tk.END)
        if not self.shared_pair_stats.data:
            self.txt_pair_stats.insert(tk.END, "Chưa có dữ liệu thống kê. Hãy chạy vài lượt lai, tool sẽ tự động tính tỷ lệ nổ theo từng cặp bố mẹ ở đây!")
        else:
            hdr = f"{'CẶP RỒNG BỐ MẸ':<42} | {'LAI':>5} | {'NL%':>5} | {'HIẾM%':>6} | {'C.HIẾM%':>7} | {'EV%':>5}\n"
            self.txt_pair_stats.insert(tk.END, hdr + "-" * 82 + "\n")
            for pair, d in sorted(self.shared_pair_stats.data.items(), key=lambda x: x[1].get("breeds", 0), reverse=True):
                tot = max(1, d.get("breeds", 0))
                nl_p = d.get("material", 0) * 100.0 / tot
                r_p = d.get("rare", 0) * 100.0 / tot
                e_p = d.get("epic", 0) * 100.0 / tot
                ev_p = d.get("event", 0) * 100.0 / tot
                self.txt_pair_stats.insert(tk.END, f"{pair[:42]:<42} | {tot:>5} | {nl_p:>4.1f}% | {r_p:>5.1f}% | {e_p:>6.1f}% | {ev_p:>4.1f}%\n")
        self.txt_pair_stats.configure(state=tk.DISABLED)

    def _clear_pair_stats(self) -> None:
        self.shared_pair_stats.clear()
        self._refresh_pair_stats_ui()
        self.append_log("🗑️ Đã xóa lịch sử thống kê cặp bố mẹ.")

    def _load_profile_dict(self, profile_name: str) -> Dict[str, Any]:
        cfg = dict(self.config_data)
        if profile_name and profile_name != "Mặc định":
            p = PROFILES_DIR / f"{profile_name}.json"
            if p.is_file():
                try:
                    cfg.update(json.loads(p.read_text(encoding="utf-8")))
                except Exception:
                    pass
        return cfg

    def _toggle_single_adb_bot(self, serial: str) -> None:
        self._save_all_settings(silent=True)
        existing = self.multi_bots.get(serial)
        btn = self.adb_device_btns.get(serial)
        if existing is not None and existing.is_running:
            if existing.is_paused:
                existing.resume()
                if btn:
                    btn.configure(text="⏸", fg_color=C["amber"], text_color="#451a03")
            else:
                existing.pause()
                if btn:
                    btn.configure(text="▶", fg_color=C["green"], text_color="#052e16")
            return
        prof_name = self.adb_device_profiles.get(serial).get() if serial in self.adb_device_profiles else "Mặc định"
        acc_cfg = self._load_profile_dict(prof_name)
        if not acc_cfg.get("selected_parent_1") or not acc_cfg.get("selected_parent_2"):
            messagebox.showwarning("Chưa chọn cặp rồng", f"Profile '{prof_name}' chưa có đủ 2 rồng bố mẹ!")
            return
        lbl = f"ADB {serial}"
        bot = DaoRongBot(
            BASE_DIR, acc_cfg,
            lambda m, l=lbl: self.append_log(f"[{l}] {m}"),
            lambda st, s=serial: self._update_multi_stats(s, st),
            alert_callback=self._on_bot_alert,
            adb=self.adb_ctrl, adb_serial=serial, account_label=lbl,
            shared_text_matcher=self.shared_text_matcher,
            shared_name_cache=self.shared_name_cache,
            shared_calibrator=self.shared_calibrator,
            shared_pair_stats=self.shared_pair_stats,
        )
        self.multi_bots[serial] = bot
        bot.start(restart_from_egg=False)
        if btn:
            btn.configure(text="⏸", fg_color=C["amber"], text_color="#451a03")

    # ------------------------------------------------------------------
    # Quản lý Profile nhanh & Mini-HUD nổi góc màn hình
    # ------------------------------------------------------------------
    def _list_profiles(self) -> List[str]:
        names = ["Mặc định"]
        if PROFILES_DIR.is_dir():
            for p in sorted(PROFILES_DIR.glob("*.json")):
                if p.stem not in names:
                    names.append(p.stem)
        return names

    def _save_new_profile(self) -> None:
        self._save_all_settings(silent=True)
        name = simpledialog.askstring("Lưu Profile", "Nhập tên Profile (vd: Cày Bụi 24/7, Săn Rồng Hiếm):", parent=self)
        if not name or not name.strip():
            return
        safe = "".join(ch for ch in name.strip() if ch.isalnum() or ch in (" ", "_", "-", "đ", "Đ")).strip()
        if not safe:
            return
        prof_path = PROFILES_DIR / f"{safe}.json"
        self.config_data["active_profile"] = safe
        prof_path.write_text(json.dumps(self.config_data, ensure_ascii=False, indent=2), encoding="utf-8")
        vals = self._list_profiles()
        self.combo_profile.configure(values=vals)
        self.var_profile_sel.set(safe)
        self.append_log(f"[PROFILE] Đã lưu cấu hình vào Profile '{safe}'.")

    def _load_selected_profile(self) -> None:
        sel = self.var_profile_sel.get()
        if sel == "Mặc định":
            return
        prof_path = PROFILES_DIR / f"{sel}.json"
        if not prof_path.is_file():
            return
        try:
            loaded = json.loads(prof_path.read_text(encoding="utf-8"))
            self.config_data.update(loaded)
            self.config_data["active_profile"] = sel
            save_config(self.config_data)
            self.var_bot_mode_vn.set(self._mode_to_vn.get(self.config_data.get("bot_mode", "breed_and_dust"), "Lai + Hóa Bụi"))
            self.var_unknown_vn.set(self._unk_map_to_vn.get(self.config_data.get("unknown_dragon_action", "keep"), "Cất an toàn"))
            self.var_speed_vn.set(self._spd_to_vn.get(self.config_data.get("speed_preset", "fast"), "Nhanh"))
            self.var_dust_max_star.set(str(self.config_data.get("dust_max_star", 0)))
            self._on_mode_changed()
            self._refresh_all_lists()
            self.append_log(f"[PROFILE] Đã tải Profile '{sel}' thành công!")
        except Exception as exc:
            messagebox.showerror("Lỗi tải Profile", str(exc))

    def _delete_selected_profile(self) -> None:
        sel = self.var_profile_sel.get()
        if sel == "Mặc định":
            return
        prof_path = PROFILES_DIR / f"{sel}.json"
        if prof_path.is_file():
            prof_path.unlink(missing_ok=True)
        vals = self._list_profiles()
        self.combo_profile.configure(values=vals)
        self.var_profile_sel.set("Mặc định")
        self.append_log(f"[PROFILE] Đã xóa Profile '{sel}'.")

    def _toggle_mini_hud(self) -> None:
        if self._mini_hud is not None and self._mini_hud.winfo_exists():
            self._mini_hud.destroy()
            self._mini_hud = None
            self.deiconify()
            return
        self.withdraw()
        hud = ctk.CTkToplevel(self)
        hud.title("DaoRong HUD")
        hud.geometry("360x108+24+24")
        hud.attributes("-topmost", True)
        hud.resizable(False, False)
        hud.configure(fg_color=C["sidebar"])
        hud.protocol("WM_DELETE_WINDOW", self._toggle_mini_hud)

        top = ctk.CTkFrame(hud, fg_color="transparent")
        top.pack(fill="x", padx=10, pady=(8, 4))
        self._hud_lbl_status = ctk.CTkLabel(top, text="🐉 Sẵn sàng", font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color=C["cyan"])
        self._hud_lbl_status.pack(side="left")
        self._hud_lbl_kpi = ctk.CTkLabel(top, text="Lai: 0 | Quý: 0", font=ctk.CTkFont("Segoe UI", 11, "bold"), text_color="#fde047")
        self._hud_lbl_kpi.pack(side="right")

        btns = ctk.CTkFrame(hud, fg_color="transparent")
        btns.pack(fill="x", padx=10, pady=(4, 8))
        ctk.CTkButton(btns, text="▶ / ⏸", width=75, height=32, fg_color=C["green"], text_color="#052e16", font=ctk.CTkFont("Segoe UI", 11, "bold"), command=self.on_toggle_start_stop).pack(side="left", padx=(0, 6))
        ctk.CTkButton(btns, text="↻ Lại", width=70, height=32, fg_color="#0284c7", font=ctk.CTkFont("Segoe UI", 11, "bold"), command=self.on_restart_from_egg).pack(side="left", padx=(0, 6))
        ctk.CTkButton(btns, text="■ Dừng", width=70, height=32, fg_color=C["red"], font=ctk.CTkFont("Segoe UI", 11, "bold"), command=self.on_stop_all).pack(side="left", padx=(0, 6))
        ctk.CTkButton(btns, text="⬜ Mở rộng", width=95, height=32, fg_color=C["card2"], font=ctk.CTkFont("Segoe UI", 11, "bold"), command=self._toggle_mini_hud).pack(side="right")
        self._mini_hud = hud

    def _on_bot_alert(self, title: str, message: str) -> None:
        def _show() -> None:
            if self.config_data.get("sound_alert", True) and winsound is not None:
                try:
                    winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
                except Exception:
                    pass
            messagebox.showinfo(title, message, parent=self._mini_hud if (self._mini_hud and self._mini_hud.winfo_exists()) else self)
        self.after(0, _show)

    # ------------------------------------------------------------------
    # Thêm / xóa mẫu rồng & chụp vùng
    # ------------------------------------------------------------------
    def _add_text_dragon_rule(self, folder_key: str, title: str) -> None:
        text = simpledialog.askstring(
            "Nhập tên rồng (không cần có sẵn hình)",
            f"Gõ tên rồng cho nhóm {title}:\n"
            "• Ghi kèm số sao nếu muốn khớp đúng sao (vd: Rồng Lửa Xanh Biển (23 sao))\n"
            "• Hoặc chỉ ghi tên rồng để áp dụng mọi sao (vd: Rồng Lửa Đỏ):",
            parent=self,
        )
        if not text or not text.strip():
            return
        rule = text.strip()
        rules = self.config_data.setdefault("dragon_text_rules", {}).setdefault(folder_key, [])
        if rule not in rules:
            rules.append(rule)
            save_config(self.config_data)
            self.append_log(f"[QUẢN LÝ] Đã thêm tên '{rule}' vào nhóm [{folder_key}].")
        self._refresh_all_lists()

    def _delete_dragon_entry(self, folder_key: str) -> None:
        lb = self._dragon_listboxes[folder_key]
        sel = lb.curselection()
        if not sel:
            return
        item = lb.get(sel[0])
        if item.startswith("[TÊN] "):
            rule = item[6:]
            rules = self.config_data.get("dragon_text_rules", {}).get(folder_key, [])
            if rule in rules:
                rules.remove(rule)
                save_config(self.config_data)
                self.append_log(f"[QUẢN LÝ] Đã xóa quy tắc tên '{rule}'.")
        elif item.startswith("[MẶC ĐỊNH] "):
            p = TEMPLATES_DIR / "ui" / "dragon_nguyen_lieu.png"
            if p.is_file() and messagebox.askyesno("Xóa mẫu mặc định", "Xóa ảnh mẫu dragon_nguyen_lieu.png?"):
                p.unlink()
                self.append_log("[QUẢN LÝ] Đã xóa mẫu mặc định dragon_nguyen_lieu.png.")
        else:
            fname = item[6:] if item.startswith("[ẢNH] ") else item
            p = TEMPLATES_DIR / folder_key / fname
            if p.is_file():
                p.unlink()
                self.append_log(f"[QUẢN LÝ] Đã xóa ảnh mẫu '{fname}'.")
        self._refresh_all_lists()

    def _capture_dragon_template(self, folder_key: str, title: str, prefix: str = "") -> None:
        self._ensure_ref_size(is_ui=False)
        hint = "dòng TÊN + SAO góc phải (vd: Rồng Lửa Đỏ(23sao))" if prefix == "ten_" else "hình con RỒNG"
        name = simpledialog.askstring("Đặt tên mẫu rồng", f"Nhập tên ngắn cho mẫu ({title}):", parent=self)
        if not name or not name.strip():
            return
        safe = "".join(c for c in name.strip() if c.isalnum() or c in " _-").strip()
        if not safe:
            return
        if prefix and not safe.lower().startswith(prefix):
            safe = f"{prefix}{safe}"
        target = TEMPLATES_DIR / folder_key / f"{safe}.png"
        capture_region_overlay(
            self, target, prompt_text=f"Khoanh gọn {hint} cho '{safe}' (ESC để hủy)",
            on_saved=lambda p: (self.append_log(f"[SETUP] Đã lưu mẫu '{p.stem}' vào [{folder_key}]."), self._refresh_all_lists()),
        )

    def _capture_ui_template(self, filename: str, desc: str) -> None:
        self._ensure_ref_size(is_ui=True)
        target = TEMPLATES_DIR / "ui" / filename
        capture_region_overlay(
            self, target, prompt_text=f"Khoanh vùng: {desc} (ESC để hủy)",
            on_saved=lambda p: (self.append_log(f"[SETUP] Đã lưu nút UI: {p.name}"), self._refresh_all_lists()),
        )

    def _record_reference_size(self, silent: bool = False) -> None:
        rect = VisionEngine(self.config_data.get("window_title_keyword", "AUTO")).get_target_window()
        if rect.found_window:
            self.config_data["dragon_reference_window_size"] = [rect.width, rect.height]
            save_config(self.config_data)
            if not silent:
                self.append_log(f"[MÀN HÌNH] Đã lưu kích thước chuẩn giả lập: {rect.width}x{rect.height}.")
        elif not silent:
            messagebox.showwarning("Chưa thấy giả lập", "Hãy mở cửa sổ giả lập (không thu nhỏ) rồi bấm lại.")

    def _ensure_ref_size(self, is_ui: bool = False) -> None:
        key = "ui_reference_window_size" if is_ui else "dragon_reference_window_size"
        ref = self.config_data.get(key, [0, 0])
        if not ref or ref[0] == 0:
            rect = VisionEngine(self.config_data.get("window_title_keyword", "AUTO")).get_target_window()
            if rect.found_window:
                self.config_data[key] = [rect.width, rect.height]
                save_config(self.config_data)

    def _pin_egg_point(self) -> None:
        def on_pick(ax: int, ay: int) -> None:
            rect = VisionEngine(self.config_data.get("window_title_keyword", "AUTO")).get_target_window()
            rel = list(rect.abs_to_rel(ax, ay))
            self.config_data.setdefault("pins", {})["egg_rel"] = rel
            save_config(self.config_data)
            self.append_log(f"[GHIM] Đã ghim tâm Trứng tại {rel}.")
        pick_point_overlay(self, "Click đúng vào TÂM QUẢ TRỨNG trên đảo lai (ESC để hủy)", on_pick)

    def _pin_altar_point(self) -> None:
        def on_pick(ax: int, ay: int) -> None:
            rect = VisionEngine(self.config_data.get("window_title_keyword", "AUTO")).get_target_window()
            rel = list(rect.abs_to_rel(ax, ay))
            self.config_data.setdefault("pins", {})["altar_rel"] = rel
            save_config(self.config_data)
            self.append_log(f"[GHIM] Đã ghim tâm Tháp Tẩy Tủy tại {rel}.")
        pick_point_overlay(self, "Click đúng vào TÂM THÁP TẨY TỦY trên đảo hóa bụi (ESC để hủy)", on_pick)

    def _save_rel_box(self, key: str, x1: int, y1: int, x2: int, y2: int) -> List[float]:
        rect = VisionEngine(self.config_data.get("window_title_keyword", "AUTO")).get_target_window()
        rx1, ry1 = rect.abs_to_rel(x1, y1)
        rx2, ry2 = rect.abs_to_rel(x2, y2)
        self.config_data.setdefault("drag_settings", {})[key] = [rx1, ry1, rx2, ry2]
        return [rx1, ry1, rx2, ry2]

    def _calibrate_bottom_bar(self) -> None:
        def on_box(x1: int, y1: int, x2: int, y2: int) -> None:
            rx1, ry1, rx2, ry2 = self._save_rel_box("bottom_bar_roi", x1, y1, x2, y2)
            mid_y = round((ry1 + ry2) / 2.0, 4)
            self.config_data["drag_settings"]["bottom_drag_start"] = [round(rx1 + (rx2 - rx1) * 0.82, 4), mid_y]
            self.config_data["drag_settings"]["bottom_drag_end"] = [round(rx1 + (rx2 - rx1) * 0.18, 4), mid_y]
            save_config(self.config_data)
            self.append_log(f"[CĂN CHỈNH] Đã lưu vùng Thanh Rồng: {[rx1, ry1, rx2, ry2]}")
        select_box_overlay(self, "Khoanh bao trọn 4 ô THANH RỒNG bố mẹ phía dưới (ESC để hủy)", on_box)

    def _calibrate_result_zone(self) -> None:
        def on_box(x1: int, y1: int, x2: int, y2: int) -> None:
            box = self._save_rel_box("result_roi", x1, y1, x2, y2)
            save_config(self.config_data)
            self.append_log(f"[CĂN CHỈNH] Đã lưu vùng Ảnh Kết Quả: {box}")
        select_box_overlay(self, "Khoanh bao gồm CẢ hình rồng con ở giữa VÀ dòng tên bên phải (ESC để hủy)", on_box)

    def _calibrate_name_bar(self) -> None:
        def on_box(x1: int, y1: int, x2: int, y2: int) -> None:
            box = self._save_rel_box("result_name_roi", x1, y1, x2, y2)
            save_config(self.config_data)
            self.append_log(f"[CĂN CHỈNH] Đã lưu vùng Dòng Tên Rồng: {box}")
        select_box_overlay(self, "Khoanh gọn ĐÚNG 1 DÒNG TÊN + SAO góc phải bảng lai (không dính dòng % bên dưới)", on_box)

    def _calibrate_dust_list(self) -> None:
        def on_box(x1: int, y1: int, x2: int, y2: int) -> None:
            rx1, ry1, rx2, ry2 = self._save_rel_box("dust_list_roi", x1, y1, x2, y2)
            mid_x = round((rx1 + rx2) / 2.0, 4)
            self.config_data["drag_settings"]["dust_drag_start"] = [mid_x, round(ry1 + (ry2 - ry1) * 0.80, 4)]
            self.config_data["drag_settings"]["dust_drag_end"] = [mid_x, round(ry1 + (ry2 - ry1) * 0.22, 4)]
            save_config(self.config_data)
            self.append_log(f"[CĂN CHỈNH] Đã lưu vùng Túi Hóa Bụi (2 cột): {[rx1, ry1, rx2, ry2]}")
        select_box_overlay(self, "Khoanh bao trọn CẢ 2 CỘT trong Túi Chứa Rồng (chừa hàng bị cắt ở đáy)", on_box)

    def _reset_parent_selection(self) -> None:
        self.var_p1.set("")
        self.var_p2.set("")
        self.config_data["selected_parent_1"] = ""
        self.config_data["selected_parent_2"] = ""
        save_config(self.config_data)
        self.append_log("[RỒNG LAI] Đã đặt lại cặp rồng lai — hãy chọn 2 rồng mới.")

    def _refresh_all_lists(self) -> None:
        for fname, lbl in self._ui_status_lbls.items():
            lbl.configure(text="✅" if (TEMPLATES_DIR / "ui" / fname).is_file() else "❌")

        for key, lb in self._dragon_listboxes.items():
            lb.delete(0, tk.END)
            if key in ("bless", "keep", "sell"):
                for rule in self.config_data.get("dragon_text_rules", {}).get(key, []):
                    lb.insert(tk.END, f"[TÊN] {rule}")
            if key == "dust_targets" and (TEMPLATES_DIR / "ui" / "dragon_nguyen_lieu.png").is_file():
                lb.insert(tk.END, "[MẶC ĐỊNH] dragon_nguyen_lieu.png (Rồng Nguyên Liệu)")
            files = sorted(p.name for p in (TEMPLATES_DIR / key).iterdir() if p.suffix.lower() in {".png", ".jpg", ".jpeg"})
            for f in files:
                lb.insert(tk.END, f"[ẢNH] {f}" if key != "parents" else f)

        parent_files = sorted(
            p.name for p in (TEMPLATES_DIR / "parents").iterdir() if p.suffix.lower() in {".png", ".jpg", ".jpeg"}
        )
        opts = parent_files if parent_files else ["(Chưa có mẫu)"]
        self.combo_p1.configure(values=opts)
        self.combo_p2.configure(values=opts)
        if self.var_p1.get() not in parent_files:
            self.var_p1.set(parent_files[0] if parent_files else "(Chưa có mẫu)")
        if self.var_p2.get() not in parent_files:
            self.var_p2.set(parent_files[1] if len(parent_files) >= 2 else (parent_files[0] if parent_files else ""))

    # ------------------------------------------------------------------
    # Lưu cài đặt, Điều khiển Bot & Auto-Update
    # ------------------------------------------------------------------
    def _save_all_settings(self, silent: bool = False) -> None:
        try:
            self.config_data["window_title_keyword"] = self.var_win_title.get().strip() or "AUTO"
            self.config_data["selected_parent_1"] = self.var_p1.get() if self.var_p1.get() != "(Chưa có mẫu)" else ""
            self.config_data["selected_parent_2"] = self.var_p2.get() if self.var_p2.get() != "(Chưa có mẫu)" else ""
            self.config_data["bot_mode"] = self._mode_from_vn.get(self.var_bot_mode_vn.get(), "breed_and_dust")
            self.config_data["control_mode"] = self._ctrl_from_vn.get(self.var_ctrl_mode_vn.get(), "windows")
            self.config_data["unknown_dragon_action"] = self._unk_map_from_vn.get(self.var_unknown_vn.get(), "keep")
            self.config_data["speed_preset"] = self._spd_from_vn.get(self.var_speed_vn.get(), "fast")
            self.config_data["island_hops"] = int(self.var_hops.get() or 2)
            self.config_data["dust_max_star"] = max(0, int(self.var_dust_max_star.get() or 0))
            if hasattr(self, "var_dust_rars"):
                picked_r = [k for k, v in self.var_dust_rars.items() if v.get()]
                self.config_data["dust_rarities"] = picked_r or ["Nguyên Liệu"]
            self.config_data["watchdog_seconds"] = max(0, int(self.var_watchdog_sec.get() or 0))
            self.config_data["adb_path"] = self.var_adb_path.get().strip()
            self.config_data["use_color_check"] = bool(self.var_color_check.get())
            self.config_data["auto_scale_templates"] = bool(self.var_auto_scale.get())
            self.config_data["enable_text_ocr"] = bool(self.var_enable_ocr.get())
            self.config_data["auto_check_update"] = bool(self.var_auto_update.get())
            self.config_data["human_like_input"] = bool(self.var_human_like.get())
            self.config_data["sound_alert"] = bool(self.var_sound_alert.get())
            self.config_data["verbose_log"] = bool(self.var_verbose_log.get())
            self.config_data["human_break_enabled"] = bool(self.var_human_break.get())
            self.config_data["save_rare_snapshots"] = bool(self.var_save_rare.get())
            self.config_data["collect_ai_dataset"] = bool(self.var_collect_ai.get())
            self.config_data["auto_stop"] = {
                "enabled": bool(self.var_as_enabled.get()),
                "max_minutes": max(0, int(self.var_as_mins.get() or 0)),
                "max_breeds": max(0, int(self.var_as_breeds.get() or 0)),
                "stop_on_rare_count": max(0, int(self.var_as_rare.get() or 0)),
                "stop_on_epic_or_event": bool(self.var_as_epic.get()),
            }

            acc_mode = self.var_accuracy.get()
            self.config_data["accuracy_mode"] = acc_mode
            self.config_data["confidence"] = dict(ACCURACY_PRESETS.get(acc_mode, ACCURACY_PRESETS["Chuẩn"]))

            save_config(self.config_data)
            if not silent:
                self.append_log(f"[CÀI ĐẶT] Đã lưu toàn bộ cài đặt (Chế độ: {self.var_bot_mode_vn.get()} | Nhận diện: {acc_mode}).")
        except ValueError:
            if not silent:
                messagebox.showerror("Sai định dạng", "Các ô số (khoảng cách đảo, lọc sao, hẹn giờ, watchdog) phải là số nguyên.")

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
            any_unpaused = any(not b.is_paused for b in running)
            if any_unpaused:
                for b in running:
                    b.pause()
                self.lbl_badge.configure(text="  ⏸ Tạm dừng  ", fg_color="#78350f", text_color="#fde68a")
                self.btn_play_pause.configure(text="▶  TIẾP TỤC (CTRL+ENTER)", fg_color=C["green"], text_color="#052e16")
            else:
                for b in running:
                    b.resume()
                self.lbl_badge.configure(text="  ● Đang chạy  ", fg_color="#14532d", text_color="#4ade80")
                self.btn_play_pause.configure(text="⏸  TẠM DỪNG (CTRL+ENTER)", fg_color=C["amber"], text_color="#451a03")
        else:
            self._start_bot(restart_from_egg=False)

    def on_restart_from_egg(self) -> None:
        running = self._active_bots()
        if running:
            for b in running:
                b.restart_from_beginning()
            self.lbl_badge.configure(text="  ● Đang chạy lại  ", fg_color="#14532d", text_color="#4ade80")
            self.btn_play_pause.configure(text="⏸  TẠM DỪNG (CTRL+ENTER)", fg_color=C["amber"], text_color="#451a03")
        else:
            self._start_bot(restart_from_egg=True)

    def _start_bot(self, restart_from_egg: bool = False) -> None:
        self._save_all_settings(silent=True)
        if not self.config_data.get("selected_parent_1") or not self.config_data.get("selected_parent_2"):
            messagebox.showwarning("Chưa chọn cặp rồng", "Hãy chọn đủ Rồng 1 và Rồng 2 ở Bảng Điều Khiển trước khi chạy!")
            return

        ctrl_mode = self.config_data.get("control_mode", "windows")
        if ctrl_mode == "adb":
            selected_serials = [s for s, var in self.adb_device_vars.items() if var.get()]
            if not selected_serials:
                messagebox.showwarning("Chưa chọn giả lập ADB", "Hãy bấm 'Quét giả lập ADB' và tick ít nhất 1 thiết bị trước khi chạy ngầm!")
                return
            self.multi_bots.clear()
            for idx, serial in enumerate(selected_serials, 1):
                lbl = f"Acc #{idx} ({serial})"
                prof_name = self.adb_device_profiles.get(serial).get() if serial in self.adb_device_profiles else "Mặc định"
                acc_cfg = self._load_profile_dict(prof_name)
                bot = DaoRongBot(
                    BASE_DIR,
                    acc_cfg,
                    lambda m, l=lbl: self.append_log(f"[{l}] {m}"),
                    lambda st, s=serial: self._update_multi_stats(s, st),
                    alert_callback=self._on_bot_alert,
                    adb=self.adb_ctrl,
                    adb_serial=serial,
                    account_label=lbl,
                    shared_text_matcher=self.shared_text_matcher,
                    shared_name_cache=self.shared_name_cache,
                    shared_calibrator=self.shared_calibrator,
                    shared_pair_stats=self.shared_pair_stats,
                )
                self.multi_bots[serial] = bot
                bot.start(restart_from_egg=restart_from_egg)
            self.lbl_badge.configure(text=f"  ● Chạy ngầm ({len(selected_serials)} acc)  ", fg_color="#14532d", text_color="#4ade80")
            self.btn_play_pause.configure(text="⏸  TẠM DỪNG (CTRL+ENTER)", fg_color=C["amber"], text_color="#451a03")
            return

        self.bot = DaoRongBot(
            BASE_DIR,
            self.config_data,
            self.append_log,
            self.update_stats,
            alert_callback=self._on_bot_alert,
            account_label="Chuột chính",
            shared_text_matcher=self.shared_text_matcher,
            shared_name_cache=self.shared_name_cache,
            shared_calibrator=self.shared_calibrator,
            shared_pair_stats=self.shared_pair_stats,
        )
        self.lbl_badge.configure(text="  ● Đang chạy  ", fg_color="#14532d", text_color="#4ade80")
        self.btn_play_pause.configure(text="⏸  TẠM DỪNG (CTRL+ENTER)", fg_color=C["amber"], text_color="#451a03")
        self.bot.start(restart_from_egg=restart_from_egg)

    def on_stop_all(self) -> None:
        if self.bot is not None and self.bot.is_running:
            self.bot.stop()
        for b in self.multi_bots.values():
            if b.is_running:
                b.stop()
        self.lbl_badge.configure(text="  ● Đã dừng  ", fg_color="#450a0a", text_color="#fca5a5")
        self.btn_play_pause.configure(text="▶  CHẠY / TẠM DỪNG (CTRL+ENTER)", fg_color=C["green"], text_color="#052e16")

    def _update_multi_stats(self, serial: str, stats: BotStats) -> None:
        def _upd() -> None:
            st_lbl = self.adb_device_status_lbls.get(serial)
            if st_lbl is not None and st_lbl.winfo_exists():
                st_lbl.configure(text=f"{stats.current_state} | Lai: {stats.breeds} | Bụi: {stats.dusted}")
            all_b = list(self.multi_bots.values())
            tot_breeds = sum(b.stats.breeds for b in all_b)
            tot_kept = sum(b.stats.kept for b in all_b)
            tot_blessed = sum(b.stats.blessed for b in all_b)
            tot_sold = sum(b.stats.sold for b in all_b)
            tot_dusted = sum(b.stats.dusted for b in all_b)
            tot_rare = sum(b.stats.rare_hits + b.stats.epic_hits + b.stats.event_hits for b in all_b)
            tot_rate = sum(b.stats.breeds_per_min for b in all_b)
            self._stat_lbls["breeds"].configure(text=str(tot_breeds))
            self._stat_lbls["kept"].configure(text=str(tot_kept))
            self._stat_lbls["blessed"].configure(text=str(tot_blessed))
            self._stat_lbls["sold"].configure(text=str(tot_sold))
            self._stat_lbls["dusted"].configure(text=str(tot_dusted))
            self._stat_lbls["rare_total"].configure(text=str(tot_rare))
            self.lbl_speed_rate.configure(text=f"NHẬT KÝ HOẠT ĐỘNG • {tot_rate:.1f} lượt/phút ({len(all_b)} acc)")
            self.lbl_state.configure(text=f"Trạng thái: {stats.current_state}")
            self._refresh_rare_drops([d for b in all_b for d in b.stats.recent_drops][:30])
            if self._hud_lbl_status and self._hud_lbl_status.winfo_exists():
                self._hud_lbl_status.configure(text=f"🐉 {len(all_b)} acc: {stats.current_state[:22]}")
                self._hud_lbl_kpi.configure(text=f"Lai: {tot_breeds} | Quý: {tot_rare}")
        self.after(0, _upd)

    def _refresh_rare_drops(self, drops: List[Dict[str, Any]]) -> None:
        self.lb_rare_drops.delete(0, tk.END)
        for d in drops:
            st = f"{d['stars']}★" if d.get("stars") else ""
            self.lb_rare_drops.insert(tk.END, f"[{d['time']}] {d['name']} {st} ({d['rarity']})")

    def append_log(self, msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}\n"
        tag = "info"
        if any(k in msg for k in ["[LỖI", "[DỪNG]"]):
            tag = "err"
        elif any(k in msg for k in ["[CẢNH BÁO]", "[THỬ LẠI]", "[RỒNG LẠ]", "[PHỤC HỒI]", "[CHẨN ĐOÁN]"]):
            tag = "warn"
        elif any(k in msg for k in ["[CHỌN RỒNG]", "[CHÚC PHÚC]", "[QUYẾT ĐỊNH", "HOÀN THÀNH", "Xong (tổng:", "ĐÃ XÁC NHẬN"]):
            tag = "ok"

        max_lines = int(self.config_data.get("max_log_lines", 500))

        def _insert() -> None:
            self.txt_log.configure(state=tk.NORMAL)
            self.txt_log.insert(tk.END, line, tag)
            self._log_line_count += 1
            if self._log_line_count > max_lines:
                excess = self._log_line_count - max_lines
                self.txt_log.delete("1.0", f"{excess + 1}.0")
                self._log_line_count = max_lines
            self.txt_log.see(tk.END)
            self.txt_log.configure(state=tk.DISABLED)

        self.after(0, _insert)

    def _clear_log(self) -> None:
        self.txt_log.configure(state=tk.NORMAL)
        self.txt_log.delete("1.0", tk.END)
        self.txt_log.configure(state=tk.DISABLED)
        self._log_line_count = 0

    def _copy_log(self) -> None:
        content = self.txt_log.get("1.0", tk.END).strip()
        if content:
            self.clipboard_clear()
            self.clipboard_append(content)
            self.append_log("[OK] Đã sao chép log vào Clipboard.")

    def update_stats(self, stats: BotStats) -> None:
        def _upd() -> None:
            rare_tot = stats.rare_hits + stats.epic_hits + stats.event_hits
            self.lbl_state.configure(text=f"Trạng thái: {stats.current_state}")
            self._stat_lbls["breeds"].configure(text=str(stats.breeds))
            self._stat_lbls["kept"].configure(text=str(stats.kept))
            self._stat_lbls["blessed"].configure(text=str(stats.blessed))
            self._stat_lbls["sold"].configure(text=str(stats.sold))
            self._stat_lbls["dusted"].configure(text=str(stats.dusted))
            self._stat_lbls["rare_total"].configure(text=str(rare_tot))
            self.lbl_speed_rate.configure(text=f"NHẬT KÝ HOẠT ĐỘNG • {stats.breeds_per_min:.1f} lượt/phút")
            self._refresh_rare_drops(stats.recent_drops)
            if self._hud_lbl_status and self._hud_lbl_status.winfo_exists():
                self._hud_lbl_status.configure(text=f"🐉 {stats.current_state[:24]}")
                self._hud_lbl_kpi.configure(text=f"Lai: {stats.breeds} | Quý: {rare_tot}")
            if stats.current_state == "Đã dừng":
                self.lbl_badge.configure(text="  ● Đã dừng  ", fg_color="#450a0a", text_color="#fca5a5")
                self.btn_play_pause.configure(text="▶  CHẠY / TẠM DỪNG (CTRL+ENTER)", fg_color=C["green"], text_color="#052e16")
            elif stats.current_state.startswith("Tạm dừng"):
                self.lbl_badge.configure(text="  ⏸ Tạm dừng  ", fg_color="#78350f", text_color="#fde68a")
                self.btn_play_pause.configure(text="▶  TIẾP TỤC (CTRL+ENTER)", fg_color=C["green"], text_color="#052e16")
        self.after(0, _upd)

    def _poll_emulator_status(self) -> None:
        try:
            rect = VisionEngine(self.config_data.get("window_title_keyword", "AUTO")).get_target_window()
            if rect.found_window:
                short_name = (rect.title or "Giả lập")[:16]
                self.lbl_ld_status.configure(text=f"✓ {short_name}\n{rect.width}×{rect.height} ({rect.left},{rect.top})", text_color=C["green"])
            else:
                self.lbl_ld_status.configure(text="⚠ Chưa thấy giả lập\n(Mở LDPlayer/BlueStacks...)", text_color=C["amber"])
        except Exception:
            pass
        self.after(3000, self._poll_emulator_status)

    def _register_hotkeys(self) -> None:
        # Gắn cả phím tắt nội bộ cửa sổ app (luôn ăn ngay cả khi không có hook hệ thống)
        self.bind_all("<Control-Return>", lambda _e: self.on_toggle_start_stop())
        self.bind_all("<Escape>", lambda _e: self.on_stop_all())
        if keyboard is None:
            return
        try:
            keyboard.add_hotkey("ctrl+enter", lambda: self.after(0, self.on_toggle_start_stop), suppress=False)
            keyboard.add_hotkey("esc", lambda: self.after(0, self.on_stop_all), suppress=False)
            self.append_log("[PHÍM TẮT] CTRL + ENTER = Bắt đầu / Dừng | ESC = Dừng khẩn cấp.")
        except Exception:
            self.append_log("[LƯU Ý] Chạy bằng quyền Administrator để dùng phím tắt CTRL + ENTER khi đang bấm trong giả lập.")

    def _check_update_async(self, silent: bool = False) -> None:
        url = (self.config_data.get("update_manifest_url", "") or DEFAULT_MANIFEST_URL).strip()
        if silent and not url:
            return
        if not silent:
            self.btn_check_update.configure(text="⏳ Đang kiểm tra...", state="disabled")

        def worker() -> None:
            info = check_for_update(url)
            self.after(0, lambda: self._handle_update_result(info, silent))

        threading.Thread(target=worker, daemon=True).start()

    def _periodic_update_check(self) -> None:
        if self.config_data.get("auto_check_update", True):
            self._check_update_async(silent=True)
        # Tự kiểm tra ngầm lại sau mỗi 15 phút khi đang mở tool
        self.after(900000, self._periodic_update_check)

    def _show_update_banner(self, info) -> None:
        self._pending_update_info = info
        note_short = (info.notes or "Cải thiện tốc độ và sửa lỗi.")[:75]
        self.lbl_update_banner.configure(
            text=f"🔔 Có bản cập nhật mới v{info.latest_version}! — {note_short}"
        )
        self.btn_check_update.configure(
            text=f"🔔 Có bản mới v{info.latest_version}!",
            fg_color="#78350f",
            text_color="#fde68a",
        )
        if not self.update_banner.winfo_ismapped():
            self.update_banner.pack(side="top", fill="x", padx=18, pady=(12, 0), before=self.content)

    def _apply_pending_update(self) -> None:
        info = self._pending_update_info
        if info is None:
            return
        if not getattr(sys, "frozen", False):
            messagebox.showinfo(
                f"Có bản mới v{info.latest_version}",
                f"Phiên bản mới: v{info.latest_version}\nGhi chú: {info.notes}\n\n"
                "Tính năng tự cập nhật trực tiếp hoạt động khi chạy từ file DaoRong_tool.exe.",
            )
            return
        self._start_download_update(info.download_url, info.latest_version)

    def _handle_update_result(self, info, silent: bool) -> None:
        self.btn_check_update.configure(text=f"🔄 Cập nhật (v{CURRENT_VERSION})", state="normal")
        if info.error:
            if not silent:
                messagebox.showwarning("Kiểm tra cập nhật", info.error)
            return
        if not info.has_update:
            if not silent:
                messagebox.showinfo("Đã là bản mới nhất", f"Bạn đang dùng phiên bản mới nhất (v{info.current_version}).")
            return
        # Luôn hiện thanh thông báo nổi bật trên đầu app khi phát hiện bản mới (kể cả đang treo game!)
        self._show_update_banner(info)
        if silent:
            self.append_log(f"[CẬP NHẬT] Phát hiện phiên bản mới v{info.latest_version}! Bấm nút 'Cập nhật ngay' trên thanh thông báo.")
            return
        if not getattr(sys, "frozen", False):
            messagebox.showinfo(
                f"Có bản mới v{info.latest_version}",
                f"Phiên bản mới: v{info.latest_version}\nGhi chú: {info.notes}\n\n"
                "Tính năng tự cập nhật trực tiếp hoạt động khi chạy từ file DaoRong_tool.exe.",
            )
            return
        ok = messagebox.askyesno(
            f"Có bản cập nhật mới v{info.latest_version}!",
            f"Hiện tại: v{info.current_version} → Bản mới: v{info.latest_version}\n\n"
            f"Thay đổi:\n{info.notes or 'Cải thiện hiệu năng và giao diện.'}\n\n"
            "Cập nhật ngay? (Giữ nguyên 100% ảnh mẫu & cài đặt của bạn)",
        )
        if ok:
            self._start_download_update(info.download_url, info.latest_version)

    def _start_download_update(self, download_url: str, latest_ver: str) -> None:
        self.on_stop_all()
        win = ctk.CTkToplevel(self)
        win.title(f"Đang cập nhật lên v{latest_ver}")
        win.geometry("420x150")
        win.attributes("-topmost", True)
        win.configure(fg_color=C["card"])

        lbl = ctk.CTkLabel(win, text="Đang chuẩn bị tải bản cập nhật...", font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color=C["text"])
        lbl.pack(pady=(22, 12), padx=16)
        pbar = ctk.CTkProgressBar(win, width=360, height=12, progress_color=C["cyan"])
        pbar.pack(pady=(0, 12))
        pbar.set(0.0)

        def on_prog(ratio: float, text: str) -> None:
            self.after(0, lambda: (pbar.set(ratio), lbl.configure(text=text)))

        def worker() -> None:
            try:
                bat = download_and_prepare_update(download_url, BASE_DIR, progress_cb=on_prog)
                self.after(400, lambda: launch_updater_and_exit(bat))
            except Exception as exc:
                self.after(0, lambda: (win.destroy(), messagebox.showerror("Lỗi cập nhật", str(exc))))

        threading.Thread(target=worker, daemon=True).start()

    def _on_close(self) -> None:
        self.on_stop_all()
        if keyboard is not None:
            try:
                keyboard.unhook_all()
            except Exception:
                pass
        self.destroy()


def show_splash_and_launch() -> None:
    app = DaoRongStudioV7()
    app.withdraw()

    splash = ctk.CTkToplevel(app)
    splash.overrideredirect(True)
    splash.attributes("-topmost", True)
    sw, sh = 480, 300
    x = (splash.winfo_screenwidth() - sw) // 2
    y = (splash.winfo_screenheight() - sh) // 2
    splash.geometry(f"{sw}x{sh}+{x}+{y}")
    splash.configure(fg_color=C["sidebar"])

    frame = ctk.CTkFrame(splash, corner_radius=16, fg_color=C["card"], border_width=2, border_color=C["cyan"])
    frame.pack(fill="both", expand=True, padx=6, pady=6)

    png_icon = BASE_DIR / "app_icon.png"
    if png_icon.is_file():
        try:
            sp_img = ctk.CTkImage(Image.open(png_icon), size=(76, 76))
            ctk.CTkLabel(frame, image=sp_img, text="").pack(pady=(16, 4))
        except Exception:
            pass

    ctk.CTkLabel(frame, text=f"DaoRong_tool v{CURRENT_VERSION}", font=ctk.CTkFont("Segoe UI", 22, "bold"), text_color=C["cyan"]).pack(pady=(4, 4))
    ctk.CTkLabel(
        frame,
        text="Tự động Lai Rồng • Hóa Bụi 2 Cột Thông Minh\nChẩn đoán lỗi chống rồng che • Hỗ trợ mọi giả lập & màn hình",
        font=ctk.CTkFont("Segoe UI", 12), text_color=C["muted"], justify="center"
    ).pack(pady=(0, 18))

    bar = ctk.CTkProgressBar(frame, width=360, height=10, corner_radius=5, progress_color=C["cyan"])
    bar.pack(pady=(0, 10))
    bar.set(0.0)

    lbl_load = ctk.CTkLabel(frame, text="Đang khởi tạo DaoRong_tool...", font=ctk.CTkFont("Segoe UI", 11), text_color=C["text"])
    lbl_load.pack()

    state = {"p": 0.0}

    def animate() -> None:
        state["p"] += 0.08
        if state["p"] >= 1.0:
            bar.set(1.0)
            splash.destroy()
            app.deiconify()
            app.lift()
            return
        bar.set(state["p"])
        if state["p"] > 0.55:
            lbl_load.configure(text="Sẵn sàng! Nhấn CTRL + ENTER để chạy / ESC để dừng.")
        splash.after(55, animate)

    splash.after(50, animate)
    app.mainloop()


if __name__ == "__main__":
    show_splash_and_launch()