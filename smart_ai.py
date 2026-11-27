"""
smart_ai.py - Lõi AI Cục Bộ 100% Offline (Miễn phí, <1MB, Nhẹ CPU) cho DaoRong_tool v10.0
Gồm 4 thành phần:
1. NaturalRuleParser: Đọc câu tiếng Việt tự nhiên (vd: "Bán phổ biến, cất nguyên liệu, chúc phúc lửa xanh biển từ 23 sao")
   và chuyển thành bộ luật chuẩn cho 63 rồng trong <10ms.
2. PersistentNameCache: Bộ nhớ tự học vĩnh viễn ảnh dòng tên rồng (trần cứng 150 mẫu LRU < 1MB),
   chỉ lưu định danh (tên + độ hiếm + sao) để luôn tuân theo luật mới nhất của người dùng.
3. AnchorAutoCalibrator: Tự học tọa độ tương đối của các nút game trên đúng máy người dùng
   để quét vùng hẹp trước (~giảm 70% CPU, tăng tốc phản hồi).
4. PairStatsTracker: Thống kê hiệu suất nổ rồng theo từng cặp bố mẹ (chỉ cộng số đếm, file cố định ~10KB).
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from ocr_engine import format_rule_string, normalize_vn
from vision import imread_unicode, imwrite_unicode


# ======================================================================
# 1. AI CÀI LUẬT BẰNG TIẾNG VIỆT TỰ NHIÊN (OFFLINE NLP)
# ======================================================================
class NaturalRuleParser:
    """Phân tích câu lệnh tiếng Việt tự nhiên thành bộ luật bless / keep / sell chuẩn xác."""

    ACTION_KEYWORDS = [
        ("bless", ("chuc phuc", "up sao", "len sao", "bless")),
        ("sell", ("ban het", "ban sach", "ban toan bo", "ban", "sell")),
        ("keep", ("cat het", "cat toan bo", "giu het", "giu toan bo", "cat", "giu", "luu", "keep")),
    ]

    RARITY_PATTERNS = [
        ("Cực hiếm", ("cuc hiem", "cuc quy")),
        ("Hiếm", ("hiem",)),
        ("Nguyên Liệu", ("nguyen lieu", "hai dau", "nl")),
        ("Phổ biến", ("pho bien", "thuong", "pb")),
        ("Event", ("event", "su kien", "ev")),
    ]

    def __init__(self, catalog: List[Dict[str, str]]):
        self.catalog = catalog or []
        self._norm_catalog: List[Tuple[str, str, str, str]] = []
        for item in self.catalog:
            name = item.get("name", "")
            rarity = item.get("rarity", "")
            norm_full = normalize_vn(name)
            norm_short = re.sub(r"^rong\s+", "", norm_full).strip()
            self._norm_catalog.append((name, rarity, norm_full, norm_short))
        # Sắp xếp tên dài trước để tránh khớp nhầm tên ngắn bên trong tên dài
        self._norm_catalog.sort(key=lambda x: len(x[2]), reverse=True)

    def _extract_star_condition(self, clause_raw: str) -> Tuple[str, Optional[int]]:
        raw_low = (clause_raw or "").lower()
        cn = normalize_vn(clause_raw)
        has_ge = (">=" in raw_low) or any(k in cn for k in ("tu ", "tro len", "lon hon", "tren "))
        # Kiểm tra trước nếu có số bất kỳ đi kèm điều kiện sao
        m_any_num = re.search(r"(?<!\d)(\d{1,3})(?!\d)", raw_low if ">=" in raw_low else cn)
        if m_any_num and has_ge:
            val = int(m_any_num.group(1))
            if ("tren " in cn or "lon hon" in cn) and "tro len" not in cn and ">=" not in raw_low and "tu " not in cn:
                val += 1
            if 1 <= val <= 40:
                return ">=", val
            return "invalid", None

        m_eq = re.search(r"(?:dung\s*|=\s*|\(\s*)?(?<!\d)(\d{1,3})(?!\d)\s*(?:sao|\*|★)", raw_low if "★" in raw_low else cn)
        if m_eq:
            val = int(m_eq.group(1))
            if 1 <= val <= 40:
                return "=", val
            return "invalid", None

        return "any", None

    def _detect_action(self, clause_norm: str) -> Optional[str]:
        best_pos = 10**9
        best_act = None
        for act, kws in self.ACTION_KEYWORDS:
            for kw in kws:
                m = re.search(r"(?:^|\s)" + re.escape(kw) + r"(?:\s|$)", clause_norm)
                if m and m.start() < best_pos:
                    best_pos = m.start()
                    best_act = act
        return best_act

    def parse(self, text: str) -> Dict[str, Any]:
        """
        Trả về:
        {
            "rules": {"bless": [...], "keep": [...], "sell": [...]},
            "summary": ["..."],
            "matched_count": int,
        }
        """
        result: Dict[str, List[str]] = {"bless": [], "keep": [], "sell": []}
        summary: List[str] = []
        raw = (text or "").strip()
        if not raw:
            return {"rules": result, "summary": [], "matched_count": 0}

        # Tách câu theo dấu phẩy, chấm phẩy, xuống dòng, dấu chấm TRƯỚC khi bỏ dấu!
        clauses = [c.strip() for c in re.split(r"[,;\n.]+", raw) if c.strip()]
        expanded: List[str] = []
        for c in clauses:
            c_norm = normalize_vn(c)
            sub = re.split(r"\b(?:con lai|nhung|rieng|tru khi)\b", c_norm, flags=re.IGNORECASE)
            for s in sub:
                if s.strip():
                    # Giữ lại ký hiệu '>=' hoặc '★' nếu có ở mệnh đề gốc
                    prefix = ">= " if ">=" in c and ">=" not in s else ""
                    expanded.append((prefix + s.strip()).strip())

        last_action: Optional[str] = None
        for clause in expanded:
            cn = normalize_vn(clause)
            act = self._detect_action(cn) or last_action
            if not act:
                continue
            last_action = act
            star_op, star_val = self._extract_star_condition(clause)
            if star_op == "invalid":
                continue

            matched_names: List[str] = []
            # 1. Tìm tên rồng cụ thể trong mệnh đề
            cn_search = f" {cn} "
            for name, _rar, nf, ns in self._norm_catalog:
                if f" {nf} " in cn_search or (len(ns) >= 5 and f" {ns} " in cn_search):
                    matched_names.append(name)
                    # Xóa cụm đã khớp để không khớp đè tên ngắn hơn
                    cn_search = cn_search.replace(f" {nf} ", " ").replace(f" {ns} ", " ")

            # 2. Tìm theo nhóm độ hiếm (Cực hiếm kiểm tra trước Hiếm!)
            matched_rarities: List[str] = []
            cn_rar = cn_search
            for rar_name, kws in self.RARITY_PATTERNS:
                for kw in kws:
                    if re.search(r"(?:^|\s)" + re.escape(kw) + r"(?:\s|$)", cn_rar):
                        matched_rarities.append(rar_name)
                        cn_rar = re.sub(r"(?:^|\s)" + re.escape(kw) + r"(?:\s|$)", " ", cn_rar)
                        break

            for rar_name in matched_rarities:
                for item in self.catalog:
                    if item.get("rarity") == rar_name and item["name"] not in matched_names:
                        matched_names.append(item["name"])

            # 3. Hỗ trợ cụm từ "toàn bộ / tất cả / mọi rồng" (khi người chơi muốn áp dụng cho cả 63 rồng!)
            if not matched_names and any(k in cn for k in ("toan bo", "tat ca", "moi rong", "tat ca rong", "het rong", "toan bo rong")):
                matched_names = [item["name"] for item in self.catalog]
                matched_rarities = ["Tất cả 63 loài"]

            if not matched_names:
                continue

            act_vn = {"bless": "Chúc phúc", "keep": "Cất", "sell": "Bán"}[act]
            star_vn = f" ({star_op} {star_val} sao)" if (star_op != "any" and star_val) else ""
            if matched_rarities and len(matched_names) > 3:
                summary.append(f"• {act_vn}: nhóm {', '.join(matched_rarities)}{star_vn} ({len(matched_names)} rồng)")
            else:
                summary.append(f"• {act_vn}: {', '.join(matched_names)}{star_vn}")

            for nm in matched_names:
                rule_str = format_rule_string(nm, star_op, star_val)
                if rule_str not in result[act]:
                    result[act].append(rule_str)

        total = sum(len(v) for v in result.values())
        want_dust_all = any(k in normalize_vn(raw) for k in ("hoa bui toan bo", "hoa bui tat ca", "hoa bui het"))
        return {"rules": result, "summary": summary, "matched_count": total, "want_dust_all": want_dust_all}


# ======================================================================
# 2. BỘ NHỚ TỰ HỌC VĨNH VIỄN (LRU TRẦN CỨNG 150 MẪU < 1MB)
# ======================================================================
class PersistentNameCache:
    """
    Lưu ảnh cắt dòng tên rồng đã nhận diện chắc chắn (mỗi Tên + Sao + Độ phân giải đúng 1 ảnh).
    Giới hạn cứng MAX_ENTRIES = 150 ảnh (~0.7MB) -> KHÔNG BAO GIỜ PHÌNH TO Ổ CỨNG.
    """

    MAX_ENTRIES = 150

    def __init__(self, cache_dir: Path):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.cache_dir / "index.json"
        self._entries: Dict[str, Dict[str, Any]] = {}
        self._images: Dict[str, np.ndarray] = {}
        self._sigs: Dict[str, np.ndarray] = {}
        self._load()

    @staticmethod
    def _has_text_signal(crop_bgr: np.ndarray) -> bool:
        if crop_bgr is None or crop_bgr.size == 0:
            return False
        gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY) if crop_bgr.ndim == 3 else crop_bgr
        # Phải có ít nhất 1.5% điểm ảnh chữ sáng (>=135) và độ tương phản đủ rõ
        return float(np.mean(gray >= 135)) >= 0.015 and float(gray.std()) >= 12.0

    @staticmethod
    def _make_sig(crop_bgr: np.ndarray, size: Tuple[int, int] = (200, 40)) -> np.ndarray:
        """Trích chữ sáng trên nền tối thành chữ ký chuẩn hóa (miễn nhiễm rung nền & sai lệch 1-3px)."""
        gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY) if crop_bgr.ndim == 3 else crop_bgr
        boosted = np.clip((gray.astype(np.float32) - 100.0) * 2.1, 0, 255).astype(np.uint8)
        return cv2.resize(boosted, size, interpolation=cv2.INTER_AREA)

    def _load(self) -> None:
        if not self.index_path.is_file():
            return
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for key, meta in data.items():
                    img_path = self.cache_dir / meta.get("file", "")
                    if img_path.is_file():
                        img = imread_unicode(img_path)
                        if img is not None and img.size > 0:
                            self._entries[key] = meta
                            self._images[key] = img
                            self._sigs[key] = self._make_sig(img, (200, 40))[3:37, 5:195]
        except Exception:
            self._entries.clear()
            self._images.clear()
            self._sigs.clear()

    def _save_index(self) -> None:
        try:
            self.index_path.write_text(
                json.dumps(self._entries, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass

    @property
    def count(self) -> int:
        return len(self._entries)

    def match(
        self, name_crop: np.ndarray, res_key: str, threshold: float = 0.89
    ) -> Optional[Tuple[str, str, Optional[int], float]]:
        """Trả về (canon_name, rarity, stars, score) trong ~1ms nhờ chữ ký chữ sáng có dung sai ±3px."""
        if not self._has_text_signal(name_crop) or not self._sigs:
            return None
        # Cùng tỷ lệ 200x40 so với lõi 190x34 -> tự trượt ±5px ngang & ±3px dọc chống lệch khung!
        q_sig = self._make_sig(name_crop, (200, 40))
        best_sc = 0.0
        best_key = None
        for key, tpl_sig in self._sigs.items():
            res = cv2.matchTemplate(q_sig, tpl_sig, cv2.TM_CCOEFF_NORMED)
            sc = float(res.max())
            if sc >= threshold and sc > best_sc:
                best_sc = sc
                best_key = key

        if best_key is not None:
            meta = self._entries[best_key]
            meta["last_used"] = int(time.time())
            return (
                str(meta.get("canon_name", "")),
                str(meta.get("rarity", "")),
                meta.get("stars"),
                best_sc,
            )
        return None

    def learn(
        self,
        name_crop: np.ndarray,
        res_key: str,
        canon_name: str,
        rarity: str,
        stars: Optional[int],
    ) -> None:
        if not self._has_text_signal(name_crop) or not canon_name:
            return
        safe_id = re.sub(r"[^a-z0-9_]+", "_", normalize_vn(canon_name)).strip("_")
        star_tag = str(stars) if stars is not None else "any"
        key = f"{res_key}__{safe_id}__{star_tag}"
        fname = f"{key}.png"
        fpath = self.cache_dir / fname

        # Nếu đã có key này -> chỉ cập nhật thời gian sử dụng, không tạo file trùng!
        if key in self._entries:
            self._entries[key]["last_used"] = int(time.time())
            return

        # Cắt trần cứng MAX_ENTRIES (xóa mẫu cũ nhất lâu không dùng)
        while len(self._entries) >= self.MAX_ENTRIES:
            oldest_k = min(self._entries.keys(), key=lambda k: self._entries[k].get("last_used", 0))
            old_f = self.cache_dir / self._entries[oldest_k].get("file", "")
            old_f.unlink(missing_ok=True)
            self._entries.pop(oldest_k, None)
            self._images.pop(oldest_k, None)
            self._sigs.pop(oldest_k, None)

        if imwrite_unicode(fpath, name_crop):
            self._entries[key] = {
                "file": fname,
                "res_key": res_key,
                "canon_name": canon_name,
                "rarity": rarity,
                "stars": stars,
                "last_used": int(time.time()),
            }
            self._images[key] = name_crop.copy()
            self._sigs[key] = self._make_sig(name_crop, (200, 40))[3:37, 5:195]
            self._save_index()

    def clear(self) -> int:
        removed = len(self._entries)
        for meta in self._entries.values():
            (self.cache_dir / meta.get("file", "")).unlink(missing_ok=True)
        self._entries.clear()
        self._images.clear()
        self._sigs.clear()
        if self.index_path.is_file():
            self.index_path.unlink(missing_ok=True)
        return removed


# ======================================================================
# 3. TỰ HỌC MỐC TỌA ĐỘ NÚT GAME (AUTO-CALIBRATION GIẢM ~70% CPU)
# ======================================================================
class AnchorAutoCalibrator:
    """
    Ghi nhớ tọa độ tương đối (rx, ry) của từng nút UI sau lần tìm thấy đầu tiên.
    Các lần sau chỉ quét vùng hẹp ±9% quanh mốc neo trước -> giảm mạnh CPU & tăng tốc!
    """

    def __init__(self, pad_rel: float = 0.09):
        self.pad_rel = pad_rel
        self._anchors: Dict[str, Tuple[float, float]] = {}

    def get_roi(self, mode_key: str, btn_name: str) -> Optional[List[float]]:
        pos = self._anchors.get(f"{mode_key}:{btn_name}")
        if pos is None:
            return None
        rx, ry = pos
        p = self.pad_rel
        return [max(0.0, rx - p), max(0.0, ry - p), min(1.0, rx + p), min(1.0, ry + p)]

    def record(self, mode_key: str, btn_name: str, rx: float, ry: float) -> None:
        self._anchors[f"{mode_key}:{btn_name}"] = (round(rx, 4), round(ry, 4))

    def clear(self) -> None:
        self._anchors.clear()


# ======================================================================
# 4. THỐNG KÊ HIỆU SUẤT THEO TỪNG CẶP BỐ MẸ (FILE CỐ ĐỊNH ~10KB)
# ======================================================================
class PairStatsTracker:
    """Cộng dồn số đếm theo từng cặp rồng bố mẹ — không lưu từng dòng lẻ nên file luôn siêu nhẹ."""

    def __init__(self, stats_path: Path):
        self.stats_path = Path(stats_path)
        self.data: Dict[str, Dict[str, int]] = {}
        self._load()

    def _load(self) -> None:
        if self.stats_path.is_file():
            try:
                loaded = json.loads(self.stats_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self.data = loaded
            except Exception:
                self.data = {}

    def _save(self) -> None:
        try:
            self.stats_path.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass

    @staticmethod
    def make_pair_key(p1: str, p2: str) -> str:
        n1 = Path(p1).stem if p1 else "?"
        n2 = Path(p2).stem if p2 else "?"
        return " × ".join(sorted([n1, n2]))

    def record_breed(self, p1: str, p2: str, rarity: str, action: str) -> None:
        key = self.make_pair_key(p1, p2)
        rec = self.data.setdefault(
            key,
            {
                "breeds": 0,
                "common": 0,
                "material": 0,
                "rare": 0,
                "epic": 0,
                "event": 0,
                "blessed": 0,
                "kept": 0,
                "sold": 0,
            },
        )
        rec["breeds"] += 1
        rar_map = {
            "Phổ biến": "common",
            "Nguyên Liệu": "material",
            "Hiếm": "rare",
            "Cực hiếm": "epic",
            "Event": "event",
        }
        rk = rar_map.get(rarity)
        if rk:
            rec[rk] += 1
        if action in ("blessed", "bless"):
            rec["blessed"] += 1
        elif action == "keep":
            rec["kept"] += 1
        elif action == "sell":
            rec["sold"] += 1
        self._save()

    def clear(self) -> None:
        self.data.clear()
        self._save()


# ======================================================================
# 5. AI TỰ HỌC LỖI ĐỌC CHỮ THEO MÁY (OCR ADAPTIVE DICTIONARY, ~5KB)
# ======================================================================
class OcrAdaptiveDictionary:
    """Ghi nhớ ánh xạ chuỗi OCR hay đọc lệch trên máy người dùng -> tên rồng chuẩn (trần 200 mục)."""

    MAX_ENTRIES = 200

    def __init__(self, map_path: Path):
        self.map_path = Path(map_path)
        self.entries: Dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if self.map_path.is_file():
            try:
                data = json.loads(self.map_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self.entries = {str(k): str(v) for k, v in data.items()}
            except Exception:
                self.entries = {}

    def _save(self) -> None:
        try:
            self.map_path.write_text(json.dumps(self.entries, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    @staticmethod
    def _clean_key(raw_text: str) -> str:
        # Bỏ phần (20 sao) chỉ lấy phần tên để khớp được mọi số sao
        base_part = re.split(r"[\(\[]", raw_text or "", maxsplit=1)[0]
        return normalize_vn(base_part)

    def lookup(self, raw_text: str) -> Optional[str]:
        k = self._clean_key(raw_text)
        if not k or len(k) < 5:
            return None
        return self.entries.get(k)

    def learn(self, raw_text: str, canonical_name: str) -> None:
        k = self._clean_key(raw_text)
        if not k or len(k) < 5 or not canonical_name:
            return
        if k == normalize_vn(canonical_name):
            return  # Đã khớp nguyên bản thì không cần lưu thừa
        if k in self.entries:
            return
        if len(self.entries) >= self.MAX_ENTRIES:
            oldest = next(iter(self.entries))
            self.entries.pop(oldest, None)
        self.entries[k] = canonical_name
        self._save()

    def clear(self) -> int:
        n = len(self.entries)
        self.entries.clear()
        if self.map_path.is_file():
            self.map_path.unlink(missing_ok=True)
        return n


# ======================================================================
# 6. TỰ GOM MẪU ẢNH TRAIN YOLO (TẮT MẶC ĐỊNH, TRẦN 300 ẢNH)
# ======================================================================
class TrainingDatasetCollector:
    """Tự động lưu ảnh tình huống khó (rồng che, popup lạ, OCR mờ) để sau này train YOLOv8-nano."""

    MAX_IMAGES = 300

    def __init__(self, out_dir: Path):
        self.out_dir = Path(out_dir)
        self._last_save_ts = 0.0

    def count(self) -> int:
        if not self.out_dir.is_dir():
            return 0
        return sum(1 for _ in self.out_dir.glob("*.jpg"))

    def save_sample(self, img_bgr: np.ndarray, tag: str) -> bool:
        if img_bgr is None or img_bgr.size == 0:
            return False
        now = time.monotonic()
        if now - self._last_save_ts < 4.0:
            return False  # Tránh lưu trùng nhiều khung hình liên tiếp
        self.out_dir.mkdir(parents=True, exist_ok=True)
        existing = sorted(self.out_dir.glob("*.jpg"), key=lambda p: p.stat().st_mtime)
        while len(existing) >= self.MAX_IMAGES:
            existing.pop(0).unlink(missing_ok=True)
        fname = f"{tag}_{time.strftime('%Y%m%d_%H%M%S')}.jpg"
        ok = imwrite_unicode(self.out_dir / fname, img_bgr)
        if ok:
            self._last_save_ts = now
        return ok


# ======================================================================
# 7. TỰ LƯU ẢNH KHOẢNH KHẮC RỒNG QUÝ (TRẦN 30 ẢNH MỚI NHẤT)
# ======================================================================
class RareMomentSaver:
    """Lưu ảnh chụp nhỏ khi nổ Cực hiếm / Event / Chúc phúc (giới hạn 30 ảnh gần nhất)."""

    MAX_SHOTS = 30

    def __init__(self, out_dir: Path):
        self.out_dir = Path(out_dir)

    def save_shot(self, img_bgr: np.ndarray, dragon_name: str, stars: Optional[int] = None) -> None:
        if img_bgr is None or img_bgr.size == 0:
            return
        self.out_dir.mkdir(parents=True, exist_ok=True)
        existing = sorted(self.out_dir.glob("*.jpg"), key=lambda p: p.stat().st_mtime)
        while len(existing) >= self.MAX_SHOTS:
            existing.pop(0).unlink(missing_ok=True)
        safe = re.sub(r"[^a-z0-9_]+", "_", normalize_vn(dragon_name or "rong_quy")).strip("_")[:30]
        st = f"_{stars}sao" if stars else ""
        fname = f"{time.strftime('%Y%m%d_%H%M%S')}_{safe}{st}.jpg"
        # Thu nhỏ nhẹ nếu quá to để mỗi ảnh chỉ ~40-60KB
        h, w = img_bgr.shape[:2]
        out_img = cv2.resize(img_bgr, (960, int(h * 960 / w))) if w > 960 else img_bgr
        imwrite_unicode(self.out_dir / fname, out_img)


# ======================================================================
# 7. GHI NHỚ HƯỚNG TÌM CẶP BỐ MẸ (TĂNG TỐC TÌM RỒNG LẦN SAU)
# ======================================================================
class PairDirectionMemory:
    """Ghi nhớ hướng vuốt thành công gần nhất ('right' hoặc 'left') cho từng cặp bố mẹ."""

    def __init__(self) -> None:
        self._mem: Dict[str, str] = {}

    @staticmethod
    def _key(p1: str, p2: str) -> str:
        return f"{p1}||{p2}"

    def preferred_direction(self, p1: str, p2: str) -> str:
        return self._mem.get(self._key(p1, p2), "right")

    def record(self, p1: str, p2: str, direction: str) -> None:
        if direction in ("right", "left"):
            self._mem[self._key(p1, p2)] = direction
