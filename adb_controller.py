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
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

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

    def find_adb_executable(self) -> Optional[str]:
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
        Thử định dạng raw RGBA trước (rất nhanh), dự phòng PNG nếu định dạng header khác.
        """
        try:
            proc = self._run(["exec-out", "screencap"], serial=serial, timeout=4.0)
            raw = proc.stdout
            if len(raw) >= 16:
                w, h, fmt = struct.unpack("<III", raw[:12])
                if 200 <= w <= 4096 and 200 <= h <= 4096 and fmt == 1:
                    expected = w * h * 4
                    # Android 8+ header có thể là 12 hoặc 16 bytes
                    for hdr in (16, 12):
                        if len(raw) >= hdr + expected:
                            arr = np.frombuffer(raw, dtype=np.uint8, count=expected, offset=hdr)
                            rgba = arr.reshape((h, w, 4))
                            return cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGR)
        except Exception:
            pass

        # Dự phòng: screencap -p (PNG)
        try:
            proc = self._run(["exec-out", "screencap", "-p"], serial=serial, timeout=5.0)
            buf = proc.stdout
            if buf:
                arr = np.frombuffer(buf, dtype=np.uint8)
                img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if img is not None:
                    return img
        except Exception:
            pass
        return None

    def tap(self, serial: str, x: int, y: int, jitter_px: int = 4) -> None:
        jx = int(round(random.gauss(0, jitter_px * 0.5))) if jitter_px > 0 else 0
        jy = int(round(random.gauss(0, jitter_px * 0.5))) if jitter_px > 0 else 0
        jx = max(-jitter_px, min(jitter_px, jx))
        jy = max(-jitter_px, min(jitter_px, jy))
        tx, ty = max(1, int(x + jx)), max(1, int(y + jy))
        self._run(["shell", "input", "tap", str(tx), str(ty)], serial=serial, timeout=3.0)

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

        dur = max(120, int(duration_ms * random.uniform(0.90, 1.12)))
        self._run(
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
            timeout=4.0,
        )
