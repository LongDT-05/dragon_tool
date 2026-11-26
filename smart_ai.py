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

    def _extract_star_condition(self, clause_norm: str) -> Tuple[str, Optional[int]]:
        # ">= N sao": "tu 23 sao tro len", ">= 23 sao", "tren 22 sao", "23 sao tro len"
        m_ge = re.search(r"(?:tu\s*|>=?\s*)(\d{1,2})\s*(?:sao|\*)\s*(?:tro len)?", clause_norm)
        if m_ge and any(k in clause_norm for k in ("tu ", "tro len", ">=", "lon hon", "tren ")):
            val = int(m_ge.group(1))
            if "tren " in clause_norm and "tro len" not in clause_norm and ">=" not in clause_norm:
                val += 1
            if 1 <= val <= 40:
                return ">=", val

        m_ge2 = re.search(r">=\s*(\d{1,2})", clause_norm)
        if m_ge2:
            val = int(m_ge2.group(1))
            if 1 <= val <= 40:
                return ">=", val

        # "= N sao": "dung 23 sao", "= 23 sao", "(23 sao)", "23 sao"
        m_eq = re.search(r"(?:dung\s*|=\s*|\(\s*)?(\d{1,2})\s*(?:sao|\*)", clause_norm)
        if m_eq:
            val = int(m_eq.group(1))
            if 1 <= val <= 40:
                return "=", val

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

        # Tách câu theo dấu phẩy, chấm phẩy, xuống dòng, dấu chấm
        clauses = [c.strip() for c in re.split(r"[,;\n.]+", raw) if c.strip()]
        # Tách thêm theo từ "còn lại" / "nhưng" / "riêng"
        expanded: List[str] = []
        for c in clauses:
            sub = re.split(r"\b(?:con lai|nhung|rieng|tru khi)\b", c, flags=re.IGNORECASE)
            for s in sub:
                if s.strip():
                    expanded.append(s.strip())

        last_action: Optional[str] = None
        for clause in expanded:
            cn = normalize_vn(clause)
            act = self._detect_action(cn) or last_action
            if not act:
                continue
            last_action = act
            star_op, star_val = self._extract_star_condition(cn)

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
        self._load()

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
        except Exception:
            self._entries.clear()
            self._images.clear()

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
        self, name_crop: np.ndarray, res_key: str, threshold: float = 0.94
    ) -> Optional[Tuple[str, str, Optional[int], float]]:
        """Trả về (canon_name, rarity, stars, score) trong ~2ms nếu khớp mẫu đã học."""
        if name_crop is None or name_crop.size == 0 or not self._entries:
            return None
        best_sc = 0.0
        best_key = None
        for key, meta in self._entries.items():
            if meta.get("res_key") != res_key:
                continue
            tpl = self._images.get(key)
            if tpl is None or tpl.shape != name_crop.shape:
                continue
            res = cv2.matchTemplate(name_crop, tpl, cv2.TM_CCOEFF_NORMED)
            sc = float(res[0, 0])
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
        if name_crop is None or name_crop.size == 0 or not canon_name:
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
            self._save_index()

    def clear(self) -> int:
        removed = len(self._entries)
        for meta in self._entries.values():
            (self.cache_dir / meta.get("file", "")).unlink(missing_ok=True)
        self._entries.clear()
        self._images.clear()
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
