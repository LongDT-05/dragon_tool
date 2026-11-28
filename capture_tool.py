"""
capture_tool.py - Công cụ chụp ảnh mẫu, khoanh vùng & ghim tọa độ v3.0
Hỗ trợ ĐA MÀN HÌNH (2–3 màn hình, kể cả màn hình phụ có tọa độ âm) bằng virtual screen mss.monitors[0].
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from typing import Callable, Optional

import cv2
import numpy as np
from PIL import Image, ImageTk

from vision import enable_windows_dpi_awareness, imwrite_unicode

try:
    import mss
except ImportError:
    mss = None


def _grab_virtual_screen():
    if mss is None:
        raise RuntimeError("Cần cài thư viện 'mss'.")
    with mss.mss() as sct:
        mon = sct.monitors[0]  # Toàn bộ các màn hình nối lại
        raw = sct.grab(mon)
        bgra = np.array(raw, dtype=np.uint8)
        bgr = cv2.cvtColor(bgra, cv2.COLOR_BGRA2BGR)
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        return bgr, rgb, int(mon["left"]), int(mon["top"]), int(mon["width"]), int(mon["height"])


def _make_fullscreen_overlay(parent_root: tk.Tk, left: int, top: int, width: int, height: int) -> tk.Toplevel:
    overlay = tk.Toplevel(parent_root)
    overlay.overrideredirect(True)  # Trải đều qua cả 2–3 màn hình không bị bó vào màn hình chính!
    overlay.geometry(f"{width}x{height}+{left}+{top}")
    overlay.attributes("-topmost", True)
    overlay.configure(cursor="cross")
    overlay.focus_force()
    return overlay


def capture_region_overlay(
    parent_root: tk.Tk,
    save_path: Path,
    prompt_text: str = "Kéo chuột khoanh vùng cần chụp (Nhấn ESC để hủy)",
    on_saved: Optional[Callable[[Path], None]] = None,
) -> None:
    enable_windows_dpi_awareness()
    parent_root.withdraw()
    parent_root.update()

    def _open() -> None:
        bgr, rgb, m_left, m_top, m_w, m_h = _grab_virtual_screen()
        pil_img = Image.fromarray(rgb)
        dimmed = Image.eval(pil_img, lambda p: int(p * 0.65))

        overlay = _make_fullscreen_overlay(parent_root, m_left, m_top, m_w, m_h)
        tk_img = ImageTk.PhotoImage(dimmed)
        canvas = tk.Canvas(overlay, width=m_w, height=m_h, highlightthickness=0)
        canvas.pack(fill=tk.BOTH, expand=True)
        canvas.create_image(0, 0, anchor=tk.NW, image=tk_img)
        canvas.image = tk_img

        # Hiển thị thanh hướng dẫn ở màn hình chính (offset -m_left, -m_top)
        bx = max(20, -m_left + 24)
        by = max(16, -m_top + 16)
        canvas.create_rectangle(bx, by, bx + 780, by + 40, fill="#0f172a", outline="#38bdf8", width=2)
        canvas.create_text(bx + 390, by + 20, text=prompt_text, fill="#f8fafc", font=("Segoe UI", 11, "bold"))

        state = {"x0": 0, "y0": 0, "rect": None}

        def close() -> None:
            overlay.destroy()
            parent_root.deiconify()

        def on_press(event: tk.Event) -> None:
            state["x0"], state["y0"] = event.x, event.y
            if state["rect"] is not None:
                canvas.delete(state["rect"])
            state["rect"] = canvas.create_rectangle(
                event.x, event.y, event.x, event.y, outline="#10b981", width=2
            )

        def on_drag(event: tk.Event) -> None:
            if state["rect"] is not None:
                canvas.coords(state["rect"], state["x0"], state["y0"], event.x, event.y)

        def on_release(event: tk.Event) -> None:
            x1, x2 = sorted([state["x0"], event.x])
            y1, y2 = sorted([state["y0"], event.y])
            close()
            if (x2 - x1) >= 8 and (y2 - y1) >= 8:
                cropped = bgr[y1:y2, x1:x2]
                if imwrite_unicode(save_path, cropped) and on_saved:
                    on_saved(save_path)

        canvas.bind("<ButtonPress-1>", on_press)
        canvas.bind("<B1-Motion>", on_drag)
        canvas.bind("<ButtonRelease-1>", on_release)
        overlay.bind("<Escape>", lambda _e: close())

    parent_root.after(200, _open)


def select_box_overlay(
    parent_root: tk.Tk,
    prompt_text: str,
    on_selected: Callable[[int, int, int, int], None],
) -> None:
    """Khoanh khung chữ nhật trên bất kỳ màn hình nào, trả về tọa độ tuyệt đối (x1, y1, x2, y2)."""
    enable_windows_dpi_awareness()
    parent_root.withdraw()
    parent_root.update()

    def _open() -> None:
        _, rgb, m_left, m_top, m_w, m_h = _grab_virtual_screen()
        pil_img = Image.fromarray(rgb)
        dimmed = Image.eval(pil_img, lambda p: int(p * 0.65))

        overlay = _make_fullscreen_overlay(parent_root, m_left, m_top, m_w, m_h)
        tk_img = ImageTk.PhotoImage(dimmed)
        canvas = tk.Canvas(overlay, width=m_w, height=m_h, highlightthickness=0)
        canvas.pack(fill=tk.BOTH, expand=True)
        canvas.create_image(0, 0, anchor=tk.NW, image=tk_img)
        canvas.image = tk_img

        bx = max(20, -m_left + 24)
        by = max(16, -m_top + 16)
        canvas.create_rectangle(bx, by, bx + 820, by + 40, fill="#0f172a", outline="#f59e0b", width=2)
        canvas.create_text(bx + 410, by + 20, text=prompt_text, fill="#f8fafc", font=("Segoe UI", 11, "bold"))

        state = {"x0": 0, "y0": 0, "rect": None}

        def close() -> None:
            overlay.destroy()
            parent_root.deiconify()

        def on_press(event: tk.Event) -> None:
            state["x0"], state["y0"] = event.x, event.y
            if state["rect"] is not None:
                canvas.delete(state["rect"])
            state["rect"] = canvas.create_rectangle(
                event.x, event.y, event.x, event.y, outline="#f59e0b", width=3
            )

        def on_drag(event: tk.Event) -> None:
            if state["rect"] is not None:
                canvas.coords(state["rect"], state["x0"], state["y0"], event.x, event.y)

        def on_release(event: tk.Event) -> None:
            x1, x2 = sorted([state["x0"], event.x])
            y1, y2 = sorted([state["y0"], event.y])
            close()
            if (x2 - x1) >= 20 and (y2 - y1) >= 20:
                on_selected(int(m_left + x1), int(m_top + y1), int(m_left + x2), int(m_top + y2))

        canvas.bind("<ButtonPress-1>", on_press)
        canvas.bind("<B1-Motion>", on_drag)
        canvas.bind("<ButtonRelease-1>", on_release)
        overlay.bind("<Escape>", lambda _e: close())

    parent_root.after(200, _open)


def pick_point_overlay(
    parent_root: tk.Tk,
    prompt_text: str,
    on_picked: Callable[[int, int], None],
) -> None:
    """Click chọn 1 điểm (ví dụ ghim tâm Trứng hoặc Tháp Tẩy Tủy) trên bất kỳ màn hình nào."""
    enable_windows_dpi_awareness()
    parent_root.withdraw()
    parent_root.update()

    def _open() -> None:
        _, rgb, m_left, m_top, m_w, m_h = _grab_virtual_screen()
        pil_img = Image.fromarray(rgb)
        dimmed = Image.eval(pil_img, lambda p: int(p * 0.72))

        overlay = _make_fullscreen_overlay(parent_root, m_left, m_top, m_w, m_h)
        tk_img = ImageTk.PhotoImage(dimmed)
        canvas = tk.Canvas(overlay, width=m_w, height=m_h, highlightthickness=0)
        canvas.pack(fill=tk.BOTH, expand=True)
        canvas.create_image(0, 0, anchor=tk.NW, image=tk_img)
        canvas.image = tk_img

        bx = max(20, -m_left + 24)
        by = max(16, -m_top + 16)
        canvas.create_rectangle(bx, by, bx + 780, by + 40, fill="#0f172a", outline="#10b981", width=2)
        canvas.create_text(bx + 390, by + 20, text=prompt_text, fill="#f8fafc", font=("Segoe UI", 11, "bold"))

        def close() -> None:
            overlay.destroy()
            parent_root.deiconify()

        def on_click(event: tk.Event) -> None:
            ax, ay = int(m_left + event.x), int(m_top + event.y)
            close()
            on_picked(ax, ay)

        canvas.bind("<Button-1>", on_click)
        overlay.bind("<Escape>", lambda _e: close())

    parent_root.after(200, _open)


def capture_region_from_bgr(
    parent_root: tk.Tk,
    bgr_img: np.ndarray,
    save_path: Path,
    prompt_text: str = "Kéo chuột khoanh gọn thẻ rồng từ khung hình ADB (Nhấn ESC để hủy)",
    on_saved: Optional[Callable[[Path], None]] = None,
) -> None:
    """Hiển thị ảnh chụp trực tiếp từ ADB (1280x720) để khoanh mẫu chuẩn 1:1 không lệch tỷ lệ Windows."""
    if bgr_img is None or bgr_img.size == 0:
        return
    h, w = bgr_img.shape[:2]
    max_w, max_h = 960, 560
    disp_scale = min(1.0, max_w / max(1, w), max_h / max(1, h))
    dw, dh = max(200, int(round(w * disp_scale))), max(150, int(round(h * disp_scale)))

    rgb = cv2.cvtColor(bgr_img, cv2.COLOR_BGR2RGB)
    pil_disp = Image.fromarray(rgb).resize((dw, dh), Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS)

    win = tk.Toplevel(parent_root)
    win.title("Chụp mẫu trực tiếp từ ADB (Chuẩn 1:1)")
    win.attributes("-topmost", True)
    win.resizable(False, False)
    win.configure(bg="#090B10")

    hdr = tk.Label(
        win,
        text=f"{prompt_text}  ({w}×{h})",
        bg="#0f172a",
        fg="#38bdf8",
        font=("Segoe UI", 10, "bold"),
        pady=8,
    )
    hdr.pack(fill=tk.X)

    tk_img = ImageTk.PhotoImage(pil_disp)
    canvas = tk.Canvas(win, width=dw, height=dh, highlightthickness=0, cursor="cross", bg="#090B10")
    canvas.pack()
    canvas.create_image(0, 0, anchor=tk.NW, image=tk_img)
    canvas.image = tk_img

    state = {"x0": 0, "y0": 0, "rect": None}

    def close() -> None:
        win.destroy()

    def on_press(event: tk.Event) -> None:
        state["x0"], state["y0"] = event.x, event.y
        if state["rect"] is not None:
            canvas.delete(state["rect"])
        state["rect"] = canvas.create_rectangle(
            event.x, event.y, event.x, event.y, outline="#10b981", width=2
        )

    def on_drag(event: tk.Event) -> None:
        if state["rect"] is not None:
            canvas.coords(state["rect"], state["x0"], state["y0"], event.x, event.y)

    def on_release(event: tk.Event) -> None:
        x1, x2 = sorted([max(0, min(dw, state["x0"])), max(0, min(dw, event.x))])
        y1, y2 = sorted([max(0, min(dh, state["y0"])), max(0, min(dh, event.y))])
        close()
        if (x2 - x1) >= 8 and (y2 - y1) >= 8:
            ox1 = max(0, min(w - 1, int(round(x1 / disp_scale))))
            oy1 = max(0, min(h - 1, int(round(y1 / disp_scale))))
            ox2 = max(ox1 + 4, min(w, int(round(x2 / disp_scale))))
            oy2 = max(oy1 + 4, min(h, int(round(y2 / disp_scale))))
            cropped = bgr_img[oy1:oy2, ox1:ox2]
            if imwrite_unicode(save_path, cropped) and on_saved:
                on_saved(save_path)

    canvas.bind("<ButtonPress-1>", on_press)
    canvas.bind("<B1-Motion>", on_drag)
    canvas.bind("<ButtonRelease-1>", on_release)
    win.bind("<Escape>", lambda _e: close())
    win.focus_force()
