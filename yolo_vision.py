"""
yolo_vision.py - Lõi AI Cấp 2 (YOLOv8-nano ONNX Offline) cho DaoRong_tool v12.0
- Chạy 100% cục bộ trên CPU thông qua onnxruntime (đã có sẵn nhờ rapidocr_onnxruntime -> 0MB phụ thuộc mới!).
- Tự động nạp model tại `models/daorong_yolo_v2.onnx` + nhãn `models/classes.json` nếu người dùng đã train xong.
- Nếu chưa có file `.onnx`, tự động trả về trạng thái `ready = False` để bot chạy mượt bằng AI Cấp 1 + OpenCV.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

try:
    import onnxruntime as ort
except ImportError:
    ort = None


DEFAULT_CLASSES = [
    "egg",              # 0: Quả trứng trên Đảo Trứng (kể cả bị che một phần)
    "altar",            # 1: Tháp Tẩy Tủy trên Đảo Tẩy Tủy
    "popup_close_x",    # 2: Mọi loại dấu X đỏ/vàng đóng popup sự kiện/bảng lạ
    "worldmap_home",    # 3: Nút Về Đảo trên World Map / màn phụ
    "breed_button",     # 4: Nút Lai Rồng đang sáng
    "bag_full_popup",   # 5: Popup thông báo đầy túi rồng
    "disconnect_popup", # 6: Popup mất kết nối / đăng nhập lại
    "parent_dragon",    # 7: Thẻ rồng trên thanh chọn bố mẹ
]


@dataclass
class YoloDetection:
    cls_id: int
    label: str
    conf: float
    x1: int
    y1: int
    x2: int
    y2: int

    @property
    def cx(self) -> int:
        return (self.x1 + self.x2) // 2

    @property
    def cy(self) -> int:
        return (self.y1 + self.y2) // 2


class YoloLevel2Vision:
    """Bộ suy luận YOLOv8-nano ONNX siêu nhẹ (~15-25ms trên CPU)."""

    def __init__(self, models_dir: Path, input_size: int = 640):
        self.models_dir = Path(models_dir)
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self.model_path = self.models_dir / "daorong_yolo_v2.onnx"
        self.classes_path = self.models_dir / "classes.json"
        self.input_size = int(input_size)
        self.classes: List[str] = list(DEFAULT_CLASSES)
        self._session = None
        self._input_name: str = ""
        self.ready: bool = False
        self.status_text: str = "Chưa có model AI Cấp 2 (đang dùng AI Cấp 1)"
        self._load()

    def _load(self) -> None:
        if self.classes_path.is_file():
            try:
                loaded = json.loads(self.classes_path.read_text(encoding="utf-8"))
                if isinstance(loaded, list) and loaded:
                    self.classes = [str(x) for x in loaded]
            except Exception:
                pass

        if not self.model_path.is_file():
            self.ready = False
            self.status_text = "Chờ file models/daorong_yolo_v2.onnx (đang dùng AI Cấp 1)"
            return

        if ort is None:
            self.ready = False
            self.status_text = "Thiếu onnxruntime (đang dùng AI Cấp 1)"
            return

        try:
            sess_opt = ort.SessionOptions()
            sess_opt.intra_op_num_threads = 2
            self._session = ort.InferenceSession(
                str(self.model_path),
                sess_options=sess_opt,
                providers=["CPUExecutionProvider"],
            )
            self._input_name = self._session.get_inputs()[0].name
            self.ready = True
            self.status_text = f"AI Cấp 2 YOLOv8-nano SẴN SÀNG ({len(self.classes)} lớp)"
        except Exception as exc:
            self.ready = False
            self.status_text = f"Lỗi nạp ONNX: {exc}"

    def _letterbox(self, img_bgr: np.ndarray) -> Tuple[np.ndarray, float, int, int]:
        h, w = img_bgr.shape[:2]
        sz = self.input_size
        scale = min(sz / max(1, w), sz / max(1, h))
        nw, nh = int(round(w * scale)), int(round(h * scale))
        resized = cv2.resize(img_bgr, (nw, nh), interpolation=cv2.INTER_LINEAR)
        canvas = np.full((sz, sz, 3), 114, dtype=np.uint8)
        pad_x = (sz - nw) // 2
        pad_y = (sz - nh) // 2
        canvas[pad_y:pad_y + nh, pad_x:pad_x + nw] = resized
        rgb = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        blob = np.transpose(rgb, (2, 0, 1))[None, ...]
        return blob, scale, pad_x, pad_y

    def detect(
        self, img_bgr: np.ndarray, conf_thr: float = 0.45, iou_thr: float = 0.45
    ) -> List[YoloDetection]:
        if not self.ready or self._session is None or img_bgr is None or img_bgr.size == 0:
            return []
        blob, scale, pad_x, pad_y = self._letterbox(img_bgr)
        outputs = self._session.run(None, {self._input_name: blob})
        if not outputs:
            return []

        # YOLOv8 ONNX output shape: [1, 4 + num_classes, N] -> transpose to [N, 4 + num_classes]
        pred = outputs[0]
        if pred.ndim == 3:
            pred = pred[0]
        if pred.shape[0] < pred.shape[1]:
            pred = pred.T

        boxes: List[List[int]] = []
        scores: List[float] = []
        cls_ids: List[int] = []
        h_img, w_img = img_bgr.shape[:2]

        for row in pred:
            cls_scores = row[4:]
            cid = int(np.argmax(cls_scores))
            sc = float(cls_scores[cid])
            if sc < conf_thr:
                continue
            cx, cy, bw, bh = float(row[0]), float(row[1]), float(row[2]), float(row[3])
            x1 = int(max(0, min(w_img, (cx - bw / 2.0 - pad_x) / scale)))
            y1 = int(max(0, min(h_img, (cy - bh / 2.0 - pad_y) / scale)))
            x2 = int(max(0, min(w_img, (cx + bw / 2.0 - pad_x) / scale)))
            y2 = int(max(0, min(h_img, (cy + bh / 2.0 - pad_y) / scale)))
            boxes.append([x1, y1, max(1, x2 - x1), max(1, y2 - y1)])
            scores.append(sc)
            cls_ids.append(cid)

        if not boxes:
            return []

        idxs = cv2.dnn.NMSBoxes(boxes, scores, conf_thr, iou_thr)
        results: List[YoloDetection] = []
        if len(idxs) > 0:
            for i in np.array(idxs).flatten():
                x, y, bw, bh = boxes[i]
                cid = cls_ids[i]
                lbl = self.classes[cid] if cid < len(self.classes) else f"cls_{cid}"
                results.append(YoloDetection(cid, lbl, scores[i], x, y, x + bw, y + bh))
        results.sort(key=lambda d: d.conf, reverse=True)
        return results

    def find_best(
        self, img_bgr: np.ndarray, label: str, conf_thr: float = 0.48
    ) -> Optional[YoloDetection]:
        for det in self.detect(img_bgr, conf_thr=conf_thr):
            if det.label == label:
                return det
        return None
