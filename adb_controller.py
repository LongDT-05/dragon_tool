"""
adb_controller.py - Bộ điều khiển Chạy Ngầm & Đa Tài Khoản qua ADB (DaoRong_tool v9.0)
- Tự động tìm adb.exe từ LDPlayer, BlueStacks, MuMu, Nox, MEmu hoặc thư mục ./adb/.
- Chụp màn hình trực tiếp từ bên trong Android (che cửa sổ hay thu nhỏ vẫn chụp nét 100%).
- Gửi lệnh chạm/vuốt tự nhiên như tay người thật, hoàn toàn KHÔNG chiếm chuột Windows
  và cho phép treo song song nhiều giả lập trong cùng 1 cửa sổ tool.
"""

from __future__ import annotations

import os
import random
import shutil
import struct
import queue
import subprocess
import threading
import time
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Tuple, List, Optional, Tuple

import cv2
import numpy as np

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

COMMON_EMULATOR_ADB_PATHS = [
    r"C:\LDPlayer\LDPlayer9\adb.exe",
    r"D:\LDPlayer\LDPlayer9\adb.exe",
    r"E:\LDPlayer\LDPlayer9\adb.exe",
    r"C:\LDPlayer\LDPlayer4.0\adb.exe",
    r"D:\LDPlayer\LDPlayer4.0\adb.exe",
    r"C:\LDPlayer\LDPlayer3.0\adb.exe",
    r"C:\Program Files\LDPlayer\LDPlayer9\adb.exe",
    r"C:\Program Files\BlueStacks_nxt\HD-Adb.exe",
    r"C:\Program Files\BlueStacks\HD-Adb.exe",
    r"C:\Program Files\Netease\MuMuPlayerGlobal-12.0\shell\adb.exe",
    r"C:\Program Files\Netease\MuMuPlayer-12.0\shell\adb.exe",
    r"C:\Program Files (x86)\Nox\bin\nox_adb.exe",
    r"C:\Program Files\Microvirt\MEmu\adb.exe",
]


@dataclass
class AdbDevice:
    serial: str
    status: str
    label: str


class AdbController:
    """Quản lý kết nối ADB, chụp màn hình ngầm tốc độ cao và gửi chạm/vuốt an toàn."""

    def __init__(self, base_dir: Optional[Path] = None, custom_adb_path: str = ""):
        self.base_dir = Path(base_dir) if base_dir else Path.cwd()
        self.custom_adb_path = custom_adb_path.strip()
        self._adb_path: Optional[str] = None
        self._capture_mode: Dict[str, str] = {}
        self._shell_pipes: Dict[str, Tuple[subprocess.Popen, queue.Queue, threading.Lock]] = {}

    def find_adb_executable(self) -> Optional[str]:
        custom = (self.custom_adb_path or "").strip()
        if custom:
            if Path(custom).is_file():
                self._adb_path = custom
                return custom
            self._adb_path = None
        if self._adb_path and Path(self._adb_path).is_file():
            return self._adb_path

        candidates: List[str] = []
        if self.custom_adb_path:
            candidates.append(self.custom_adb_path)

        # Kiểm tra thư mục ./adb/ cạnh tool trước
        local_adb = self.base_dir / "adb" / "adb.exe"
        if local_adb.is_file():
            candidates.append(str(local_adb))
        local_adb_root = self.base_dir / "adb.exe"
        if local_adb_root.is_file():
            candidates.append(str(local_adb_root))

        # Kiểm tra PATH hệ thống
        which_adb = shutil.which("adb")
        if which_adb:
            candidates.append(which_adb)

        # Kiểm tra các đường dẫn cài đặt giả lập phổ biến trên Windows
        candidates.extend(COMMON_EMULATOR_ADB_PATHS)

        for c in candidates:
            if c and Path(c).is_file():
                self._adb_path = c
                return c
        return None

    def _run(
        self, args: List[str], serial: Optional[str] = None, timeout: float = 6.0
    ) -> subprocess.CompletedProcess:
        adb = self.find_adb_executable()
        if not adb:
            raise RuntimeError(
                "Không tìm thấy adb.exe. Hãy cài LDPlayer/BlueStacks hoặc chọn đường dẫn adb.exe trong mục Cài Đặt."
            )
        cmd = [adb]
        if serial:
            cmd.extend(["-s", serial])
        cmd.extend(args)
        return subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )

    def list_devices(self, auto_connect_ports: bool = False) -> List[AdbDevice]:
        adb = self.find_adb_executable()
        if not adb:
            return []

        try:
            proc = self._run(["devices"], timeout=5.0)
            lines = proc.stdout.decode("utf-8", errors="ignore").splitlines()
        except Exception:
            lines = []

        online_serials: List[str] = []
        for line in lines[1:]:
            parts = line.strip().split()
            if len(parts) >= 2 and parts[1] == "device":
                online_serials.append(parts[0])

        # Chỉ tự connect cổng ngoài (BlueStacks/MuMu/Nox) nếu chưa có thiết bị nào,
        # TUYỆT ĐỐI không connect 127.0.0.1:5555 khi emulator-5554 đã có (vì là CÙNG 1 cửa sổ LDPlayer!)
        if auto_connect_ports and not online_serials:
            for port in (5555, 5557, 5559, 7555, 16384):
                try:
                    self._run(["connect", f"127.0.0.1:{port}"], timeout=1.0)
                except Exception:
                    pass
            try:
                proc = self._run(["devices"], timeout=5.0)
                lines = proc.stdout.decode("utf-8", errors="ignore").splitlines()
                for line in lines[1:]:
                    parts = line.strip().split()
                    if len(parts) >= 2 and parts[1] == "device" and parts[0] not in online_serials:
                        online_serials.append(parts[0])
            except Exception:
                pass

        # Khử trùng lặp cặp LDPlayer: emulator-(5554+2k) và 127.0.0.1:(5555+2k) là CÙNG 1 giả lập!
        emu_ports = set()
        for s in online_serials:
            if s.startswith("emulator-"):
                try:
                    emu_ports.add(int(s.split("-")[1]))
                except ValueError:
                    pass

        deduped: List[str] = []
        for s in online_serials:
            if s.startswith("127.0.0.1:"):
                try:
                    p = int(s.split(":")[1])
                    if (p - 1) in emu_ports:
                        continue  # Bỏ qua bản sao trùng của emulator-(p-1)!
                except ValueError:
                    pass
            if s not in deduped:
                deduped.append(s)

        devices: List[AdbDevice] = []
        for idx, serial in enumerate(deduped, 1):
            devices.append(AdbDevice(serial=serial, status="online", label=f"Giả lập #{idx} ({serial})"))
        return devices

    def capture_screen(self, serial: str) -> Optional[np.ndarray]:
        """
        Chụp màn hình trực tiếp từ framebuffer Android (nhanh ~60-120ms, không cần hiện cửa sổ).
        Tự ghi nhớ định dạng chụp nhanh nhất theo từng thiết bị để tránh thử lỗi 2 lần mỗi khung.
        """
        mode = self._capture_mode.get(serial, "raw")
        order = ("raw", "png") if mode == "raw" else ("png", "raw")
        for m in order:
            if m == "raw":
                try:
                    proc = self._run(["exec-out", "screencap"], serial=serial, timeout=4.0)
                    raw = proc.stdout
                    if len(raw) >= 16:
                        w, h, fmt = struct.unpack("<III", raw[:12])
                        if 200 <= w <= 4096 and 200 <= h <= 4096 and fmt == 1:
                            expected = w * h * 4
                            for hdr in (16, 12):
                                if len(raw) >= hdr + expected:
                                    arr = np.frombuffer(raw, dtype=np.uint8, count=expected, offset=hdr)
                                    rgba = arr.reshape((h, w, 4))
                                    self._capture_mode[serial] = "raw"
                                    return cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGR)
                except Exception:
                    pass
            else:
                try:
                    proc = self._run(["exec-out", "screencap", "-p"], serial=serial, timeout=5.0)
                    buf = proc.stdout
                    if buf:
                        arr = np.frombuffer(buf, dtype=np.uint8)
                        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                        if img is not None:
                            self._capture_mode[serial] = "png"
                            return img
                except Exception:
                    pass
        return None

    def _close_shell_pipe(self, serial: str) -> None:
        item = self._shell_pipes.pop(serial, None)
        if item is not None:
            proc = item[0]
            try:
                proc.kill()
            except Exception:
                pass

    def close_all_shells(self) -> None:
        """Đóng sạch toàn bộ phiên adb shell đang mở để không sót tiến trình nền."""
        for serial in list(self._shell_pipes.keys()):
            self._close_shell_pipe(serial)

    def _run_fast_shell_cmd(self, serial: str, cmd: str, timeout: float = 3.5) -> bool:
        """Gửi lệnh qua phiên adb shell mở sẵn (bỏ thời gian khởi tạo adb.exe trên Windows). Tự động lùi về _run nếu lỗi."""
        adb = self.find_adb_executable()
        if not adb:
            return False
        item = self._shell_pipes.get(serial)
        if item is None or item[0].poll() is not None:
            self._close_shell_pipe(serial)
            try:
                flags = 0x08000000 if os.name == "nt" else 0
                proc = subprocess.Popen(
                    [adb, "-s", serial, "shell"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    creationflags=flags,
                )
                q: queue.Queue = queue.Queue()
                def _reader(p=proc, out_q=q):
                    try:
                        while True:
                            line = p.stdout.readline()
                            if not line:
                                break
                            out_q.put(line)
                    except Exception:
                        pass
                threading.Thread(target=_reader, daemon=True).start()
                item = (proc, q, threading.Lock())
                self._shell_pipes[serial] = item
            except Exception:
                return False

        proc, q, lock = item
        with lock:
            if proc.poll() is not None or proc.stdin is None:
                self._close_shell_pipe(serial)
                return False
            try:
                while not q.empty():
                    q.get_nowait()
                marker = b"__DR_OK__"
                proc.stdin.write(f"{cmd}; echo __DR_OK__\n".encode("utf-8"))
                proc.stdin.flush()
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    rem = max(0.05, deadline - time.monotonic())
                    line = q.get(timeout=rem)
                    if marker in line:
                        return True
            except Exception:
                pass
        self._close_shell_pipe(serial)
        return False

    def tap(self, serial: str, x: int, y: int, jitter_px: int = 4) -> None:
        jx = int(round(random.gauss(0, jitter_px * 0.5))) if jitter_px > 0 else 0
        jy = int(round(random.gauss(0, jitter_px * 0.5))) if jitter_px > 0 else 0
        jx = max(-jitter_px, min(jitter_px, jx))
        jy = max(-jitter_px, min(jitter_px, jy))
        tx, ty = max(1, int(x + jx)), max(1, int(y + jy))
        if self._run_fast_shell_cmd(serial, f"input tap {tx} {ty}", timeout=3.2):
            return
        proc = self._run(["shell", "input", "tap", str(tx), str(ty)], serial=serial, timeout=3.0)
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", errors="ignore").strip() or proc.stdout.decode("utf-8", errors="ignore").strip()
            raise RuntimeError(f"ADB tap thất bại (code={proc.returncode}): {err or 'device offline'}")

    def swipe(
        self,
        serial: str,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration_ms: int = 280,
        jitter_px: int = 4,
    ) -> None:
        def _j(v: int) -> int:
            return max(1, int(v + random.randint(-jitter_px, jitter_px))) if jitter_px > 0 else max(1, int(v))

        j_lo = 0.98 if duration_ms >= 1000 else 0.90
        dur = max(120, int(duration_ms * random.uniform(j_lo, 1.08)))
        swipe_timeout = max(8.0, (dur / 1000.0) + 6.0)
        proc = self._run(
            [
                "shell",
                "input",
                "swipe",
                str(_j(x1)),
                str(_j(y1)),
                str(_j(x2)),
                str(_j(y2)),
                str(dur),
            ],
            serial=serial,
            timeout=swipe_timeout,
        )
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", errors="ignore").strip() or proc.stdout.decode("utf-8", errors="ignore").strip()
            raise RuntimeError(f"ADB swipe thất bại (code={proc.returncode}): {err or 'device offline'}")

    def get_foreground_package(self, serial: str) -> str:
        """Lấy tên package ứng dụng đang mở trên màn hình Android (dùng cho Watchdog 24/7)."""
        import re as _re
        try:
            proc = self._run(["shell", "dumpsys", "window", "windows"], serial=serial, timeout=3.5)
            out = proc.stdout.decode("utf-8", errors="ignore")
            for pat in (r"mCurrentFocus=Window\{[^\}]+\s+([a-zA-Z0-9_\.]+)/", r"mFocusedApp=.*?([a-zA-Z0-9_\.]+)/"):
                m = _re.search(pat, out)
                if m:
                    return m.group(1)
        except Exception:
            pass
        return ""

    def relaunch_package(self, serial: str, package_name: str) -> bool:
        """Tự động mở lại game nếu bị văng ra màn hình chính Android."""
        pkg = (package_name or "").strip()
        if not pkg:
            return False
        try:
            self._run(
                ["shell", "monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1"],
                serial=serial,
                timeout=6.0,
            )
            return True
        except Exception:
            return False
