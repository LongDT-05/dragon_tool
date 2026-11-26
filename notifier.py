"""
notifier.py - Hệ thống Thông Báo Thời Gian Thực (Telegram & Discord) cho DaoRong_tool v12.0
- 100% thư viện chuẩn Python (urllib, threading, uuid) -> không cần cài thêm thư viện ngoài.
- Gửi bất đồng bộ trên luồng nền -> KHÔNG BAO GIỜ làm chậm vòng lặp lai rồng.
- Hỗ trợ gửi cả tin nhắn văn bản lẫn ảnh chụp màn hình rồng quý / lỗi treo máy.
"""

from __future__ import annotations

import json
import threading
import urllib.request
import uuid
from typing import Optional

import cv2
import numpy as np


class AlertNotifier:
    """Gửi thông báo về điện thoại qua Telegram Bot và/hoặc Discord Webhook."""

    def __init__(
        self,
        telegram_token: str = "",
        telegram_chat_id: str = "",
        discord_webhook: str = "",
        enabled: bool = False,
    ):
        self.telegram_token = (telegram_token or "").strip()
        self.telegram_chat_id = (telegram_chat_id or "").strip()
        self.discord_webhook = (discord_webhook or "").strip()
        self.enabled = bool(enabled)

    @property
    def is_configured(self) -> bool:
        has_tg = bool(self.telegram_token and self.telegram_chat_id)
        has_dc = bool(self.discord_webhook)
        return self.enabled and (has_tg or has_dc)

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

    def send_test(self) -> tuple[bool, str]:
        """Gửi tin nhắn thử đồng bộ để báo kết quả ngay trên giao diện Cài Đặt."""
        has_tg = bool(self.telegram_token and self.telegram_chat_id)
        has_dc = bool(self.discord_webhook)
        if not (has_tg or has_dc):
            return False, "Bạn chưa nhập Telegram Token + Chat ID hoặc Discord Webhook URL."
        ok_msgs = []
        err_msgs = []
        if has_tg:
            ok, msg = self._send_telegram("✅ [DaoRong_tool v12.0] Kết nối thông báo Telegram thành công!", None)
            (ok_msgs if ok else err_msgs).append(f"Telegram: {msg}")
        if has_dc:
            ok, msg = self._send_discord("✅ **[DaoRong_tool v12.0]** Kết nối thông báo Discord thành công!", None)
            (ok_msgs if ok else err_msgs).append(f"Discord: {msg}")
        if err_msgs and not ok_msgs:
            return False, " | ".join(err_msgs)
        return True, " | ".join(ok_msgs + err_msgs)

    def _send_sync(self, text: str, image_bgr: Optional[np.ndarray]) -> None:
        jpg_bytes: Optional[bytes] = None
        if image_bgr is not None:
            try:
                ok, buf = cv2.imencode(".jpg", image_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
                if ok:
                    jpg_bytes = buf.tobytes()
            except Exception:
                jpg_bytes = None

        if self.telegram_token and self.telegram_chat_id:
            self._send_telegram(text, jpg_bytes)
        if self.discord_webhook:
            self._send_discord(text, jpg_bytes)

    def _send_telegram(self, text: str, jpg_bytes: Optional[bytes]) -> tuple[bool, str]:
        try:
            if jpg_bytes:
                url = f"https://api.telegram.org/bot{self.telegram_token}/sendPhoto"
                boundary = uuid.uuid4().hex
                body = (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="chat_id"\r\n\r\n'
                    f"{self.telegram_chat_id}\r\n"
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="caption"\r\n\r\n'
                    f"{text[:1000]}\r\n"
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="photo"; filename="dragon.jpg"\r\n'
                    f"Content-Type: image/jpeg\r\n\r\n"
                ).encode("utf-8") + jpg_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")
                req = urllib.request.Request(
                    url,
                    data=body,
                    headers={
                        "Content-Type": f"multipart/form-data; boundary={boundary}",
                        "User-Agent": "DaoRong_tool/12.0",
                    },
                )
            else:
                url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
                payload = json.dumps({"chat_id": self.telegram_chat_id, "text": text[:4000]}).encode("utf-8")
                req = urllib.request.Request(
                    url,
                    data=payload,
                    headers={"Content-Type": "application/json", "User-Agent": "DaoRong_tool/12.0"},
                )
            with urllib.request.urlopen(req, timeout=8.0) as resp:
                if 200 <= resp.status < 300:
                    return True, "OK"
                return False, f"HTTP {resp.status}"
        except Exception as exc:
            return False, str(exc)

    def _send_discord(self, text: str, jpg_bytes: Optional[bytes]) -> tuple[bool, str]:
        try:
            if jpg_bytes:
                boundary = uuid.uuid4().hex
                payload_json = json.dumps({"content": text[:1900]}, ensure_ascii=False)
                body = (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="payload_json"\r\n'
                    f"Content-Type: application/json\r\n\r\n"
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
                        "User-Agent": "DaoRong_tool/12.0",
                    },
                )
            else:
                payload = json.dumps({"content": text[:1900]}, ensure_ascii=False).encode("utf-8")
                req = urllib.request.Request(
                    self.discord_webhook,
                    data=payload,
                    headers={"Content-Type": "application/json", "User-Agent": "DaoRong_tool/12.0"},
                )
            with urllib.request.urlopen(req, timeout=8.0) as resp:
                if 200 <= resp.status < 300:
                    return True, "OK"
                return False, f"HTTP {resp.status}"
        except Exception as exc:
            return False, str(exc)
