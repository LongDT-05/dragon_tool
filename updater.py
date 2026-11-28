"""
updater.py - Hệ thống Tự Động Cập Nhật (Auto-Updater v9.0) qua GitHub Releases.
- Hỗ trợ cả bản Full Update lẫn bản Patch Update siêu nhẹ (~2-5 MB, chỉ cập nhật exe + dữ liệu,
  giữ nguyên _internal, config.json, profiles/ và toàn bộ templates/ của người dùng).
- Dùng hoàn toàn thư viện chuẩn Python (urllib, zipfile, subprocess).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Tuple

CURRENT_VERSION = "15.1"

DEFAULT_MANIFEST_URL = "https://github.com/LongDT-05/dragon_tool/releases/latest/download/version.json"


def parse_version(ver: str) -> Tuple[int, ...]:
    """Chuyển chuỗi phiên bản ('9.0', 'v9.1.2') thành tuple số nguyên để so sánh chính xác."""
    clean = (ver or "0").strip().lstrip("vV")
    parts = []
    for p in clean.split("."):
        digits = "".join(ch for ch in p if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts) or (0,)


def is_newer_version(remote_ver: str, local_ver: str = CURRENT_VERSION) -> bool:
    return parse_version(remote_ver) > parse_version(local_ver)


@dataclass
class UpdateInfo:
    has_update: bool
    current_version: str
    latest_version: str = ""
    download_url: str = ""
    notes: str = ""
    is_patch: bool = False
    error: str = ""


def check_for_update(manifest_url: str, timeout: float = 8.0) -> UpdateInfo:
    """Tải file version.json từ GitHub Releases để kiểm tra có bản mới hay không."""
    url = (manifest_url or DEFAULT_MANIFEST_URL).strip()
    if not url:
        return UpdateInfo(
            has_update=False,
            current_version=CURRENT_VERSION,
            error="Chưa cấu hình link file version.json.",
        )

    try:
        import time as _t
        sep = "&" if "?" in url else "?"
        bust_url = f"{url}{sep}t={int(_t.time())}"
        req = urllib.request.Request(
            bust_url,
            headers={
                "User-Agent": f"DaoRong_tool/{CURRENT_VERSION}",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8-sig")
        data = json.loads(raw)
        latest = str(data.get("version", "")).strip()
        dl_url = str(data.get("download_url", "")).strip()
        notes = str(data.get("notes", "")).strip()
        is_patch = bool(data.get("patch", False))
        if not latest or not dl_url:
            return UpdateInfo(
                has_update=False,
                current_version=CURRENT_VERSION,
                error="File version.json thiếu trường 'version' hoặc 'download_url'.",
            )
        return UpdateInfo(
            has_update=is_newer_version(latest, CURRENT_VERSION),
            current_version=CURRENT_VERSION,
            latest_version=latest,
            download_url=dl_url,
            notes=notes,
            is_patch=is_patch,
        )
    except Exception as exc:
        return UpdateInfo(
            has_update=False,
            current_version=CURRENT_VERSION,
            error=f"Không kiểm tra được cập nhật: {exc}",
        )


def download_and_prepare_update(
    download_url: str,
    app_dir: Path,
    progress_cb: Optional[Callable[[float, str], None]] = None,
) -> Path:
    """
    Tải gói cập nhật (Full hoặc Patch siêu nhẹ), giải nén, kiểm tra tính toàn vẹn
    và tạo script apply_update.bat (tuyệt đối KHÔNG đụng config.json, profiles/ & templates rồng).
    """
    tmp_root = Path(tempfile.gettempdir()) / "DaoRong_tool_update"
    tmp_root.mkdir(parents=True, exist_ok=True)
    zip_file = tmp_root / "update.zip"
    extract_dir = tmp_root / "extracted"

    if progress_cb:
        progress_cb(0.05, "Đang kết nối máy chủ tải bản cập nhật...")

    req = urllib.request.Request(
        download_url, headers={"User-Agent": f"DaoRong_tool/{CURRENT_VERSION}"}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        downloaded = 0
        chunk_size = 64 * 1024
        with open(zip_file, "wb") as out:
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                out.write(chunk)
                downloaded += len(chunk)
                if progress_cb and total > 0:
                    ratio = min(0.85, 0.05 + 0.80 * (downloaded / total))
                    mb_done = downloaded / (1024 * 1024)
                    mb_all = total / (1024 * 1024)
                    progress_cb(ratio, f"Đang tải... {mb_done:.1f} / {mb_all:.1f} MB")

    if progress_cb:
        progress_cb(0.90, "Đang giải nén và kiểm tra gói cập nhật...")

    if extract_dir.exists():
        import shutil
        shutil.rmtree(extract_dir, ignore_errors=True)
    extract_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_file, "r") as zf:
        zf.extractall(extract_dir)

    exe_matches = list(extract_dir.rglob("DaoRong_tool.exe"))
    if not exe_matches:
        raise RuntimeError("Gói cập nhật không hợp lệ (không tìm thấy DaoRong_tool.exe bên trong).")
    src_app_dir = exe_matches[0].parent

    bat_path = tmp_root / "apply_update.bat"
    app_dir_str = str(app_dir.resolve())
    src_str = str(src_app_dir.resolve())
    bat_lines = [
        "@echo off",
        "timeout /t 2 /nobreak >nul",
        # Chỉ chép _internal nếu gói update có chứa _internal (bản Patch nhẹ sẽ bỏ qua bước này!)
        f'if exist "{src_str}\\_internal" xcopy /E /I /Y "{src_str}\\_internal" "{app_dir_str}\\_internal" >nul',
        f'copy /Y "{src_str}\\DaoRong_tool.exe" "{app_dir_str}\\DaoRong_tool.exe" >nul',
        f'if exist "{src_str}\\app_icon.ico" copy /Y "{src_str}\\app_icon.ico" "{app_dir_str}\\app_icon.ico" >nul',
        f'if exist "{src_str}\\app_icon.png" copy /Y "{src_str}\\app_icon.png" "{app_dir_str}\\app_icon.png" >nul',
        f'if exist "{src_str}\\dragons_db.json" copy /Y "{src_str}\\dragons_db.json" "{app_dir_str}\\dragons_db.json" >nul',
        f'if exist "{src_str}\\templates\\ui" xcopy /E /I /Y "{src_str}\\templates\\ui" "{app_dir_str}\\templates\\ui" >nul',
        f'if exist "{src_str}\\models" xcopy /E /I /Y "{src_str}\\models" "{app_dir_str}\\models" >nul',
        f'start "" "{app_dir_str}\\DaoRong_tool.exe"',
        f'rmdir /s /q "{tmp_root}"',
        "exit",
    ]
    bat_path.write_bytes(("\r\n".join(bat_lines) + "\r\n").encode("utf-8"))

    if progress_cb:
        progress_cb(1.0, "Sẵn sàng áp dụng bản cập nhật!")
    return bat_path


def launch_updater_and_exit(bat_path: Path) -> None:
    """Chạy script cập nhật ở chế độ ẩn cửa sổ CMD rồi thoát app hiện tại."""
    flags = 0x08000000 if sys.platform == "win32" else 0
    subprocess.Popen(["cmd.exe", "/c", str(bat_path)], creationflags=flags)
    os._exit(0)
