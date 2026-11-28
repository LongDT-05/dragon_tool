"""
notifier.py - Hệ thống Thông Báo Thời Gian Thực qua Discord Webhook cho DaoRong_tool
- 100% thư viện chuẩn Python (urllib, threading, uuid) -> không cần cài thêm thư viện ngoài.
- Gửi bất đồng bộ trên luồng nền -> KHÔNG BAO GIỜ làm chậm vòng lặp lai rồng.
- Hỗ trợ gửi cả tin nhắn văn bản lẫn ảnh chụp màn hình rồng quý / lỗi treo máy.
"""

from __future__ import annotations

import json
import threading
import urllib.request
import uuid
from typing import Any, Optional, Tuple

import cv2
import numpy as np


class AlertNotifier:
    """Gửi thông báo về điện thoại/máy tính qua Discord Webhook."""

    def __init__(
        self,
        discord_webhook: str = "",
        enabled: bool = False,
        **_legacy_kwargs: Any,
    ):
        self.discord_webhook = (discord_webhook or "").strip()
        self.enabled = bool(enabled)

    @property
    def is_configured(self) -> bool:
        return self.enabled and bool(self.discord_webhook)

    def send_async(self, text: str, image_bgr: Optional[np.ndarray] = None) -> None:
        """Gửi thông báo ngầm (không chặn luồng bot)."""
        if not self.is_configured:
            return
        img_copy = image_bgr.copy() if (image_bgr is not None and image_bgr.size > 0) else None
        threading.Thread(
            target=self._send_sync,
            args=(text, img_copy),
            daemon=True,
        ).start()

    def send_test(self) -> Tuple[bool, str]:
        """Gửi tin nhắn thử đồng bộ để báo kết quả ngay trên giao diện Cài Đặt."""
        if not self.discord_webhook:
            return False, "Bạn chưa nhập Discord Webhook URL."
        ok, msg = self._send_discord("✅ **[DaoRong_tool]** Kết nối thông báo Discord thành công!", None)
        return ok, f"Discord: {msg}"

    def _send_sync(self, text: str, image_bgr: Optional[np.ndarray]) -> None:
        jpg_bytes: Optional[bytes] = None
        if image_bgr is not None:
            try:
                ok, buf = cv2.imencode(".jpg", image_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
                if ok:
                    jpg_bytes = buf.tobytes()
            except Exception:
                jpg_bytes = None
        if self.discord_webhook:
            self._send_discord(text, jpg_bytes)

    def _send_discord(self, text: str, jpg_bytes: Optional[bytes]) -> Tuple[bool, str]:
        try:
            if jpg_bytes:
                boundary = uuid.uuid4().hex
                payload_json = json.dumps({"content": text[:1900]}, ensure_ascii=False)
                body = (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="payload_json"\r\n\r\n'
                    f"{payload_json}\r\n"
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="files[0]"; filename="dragon.jpg"\r\n'
                    f"Content-Type: image/jpeg\r\n\r\n"
                ).encode("utf-8") + jpg_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")
                req = urllib.request.Request(
                    self.discord_webhook,
                    data=body,
                    headers={
                        "Content-Type": f"multipart/form-data; boundary={boundary}",
                        "User-Agent": "DaoRong_tool/15.12",
                    },
                )
            else:
                payload = json.dumps({"content": text[:1900]}, ensure_ascii=False).encode("utf-8")
                req = urllib.request.Request(
                    self.discord_webhook,
                    data=payload,
                    headers={"Content-Type": "application/json", "User-Agent": "DaoRong_tool/15.12"},
                )
            with urllib.request.urlopen(req, timeout=8.0) as resp:
                if 200 <= resp.status < 300:
                    return True, "OK"
                return False, f"HTTP {resp.status}"
        except Exception as exc:
            return False, str(exc)
