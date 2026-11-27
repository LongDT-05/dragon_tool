"""
ocr_engine.py - Nhận diện tên rồng Đảo Rồng v8.0 (Closed-Catalog 63 Rồng + RapidOCR use_cls=False)
Độc lập 100% trong file .exe (không cần cài gói ngôn ngữ Windows):
1. Tắt hoàn toàn bộ xoay ngược 180 độ (use_cls=False) -> không bao giờ đọc ngược chuỗi.
2. So khớp ĐÓNG trên danh mục chuẩn 63 loài rồng Đảo Rồng (dragons_db.json) + từ điển chuẩn hóa
   lỗi font đặc thù -> xác định chính xác tên rồng & số sao rồi mới áp dụng luật Chúc phúc / Cất / Bán.
3. Hỗ trợ điều kiện số sao linh hoạt: 'any' (mọi sao), '>=' (từ X sao trở lên), '=' (đúng X sao).
"""

from __future__ import annotations

import difflib
import json
import re
import threading
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

try:
    from rapidocr_onnxruntime import RapidOCR
except ImportError:
    RapidOCR = None


CONFLICT_PAIRS = (
    ("lua", "cay"),
    ("lua", "bang"),
    ("cay", "bang"),
    ("do", "xanh"),
    ("vang", "xanh"),
    ("vang", "do"),
)

DAO_RONG_OCR_MAP = {
    "lia": "lua",
    "loa": "lua",
    "lva": "lua",
    "lwa": "lua",
    "lura": "lua",
    "bo": "do",
    "ba": "do",
    "d6": "do",
    "d0": "do",
    "b0": "do",
    "b6": "do",
    "bao": "dau",
    "dao": "dau",
    "da": "dau",
    "1a": "la",
    "blen": "bien",
    "dien": "bien",
    "sarn": "sam",
    "san": "sam",
    "nav": "nau",
    "bac": "bac",
    "bqc": "bac",
    "xarn": "xanh",
}

DRAGON_PREFIXES = {"rong", "ring", "rang", "r0ng", "r1ng", "rng"}


BUILTIN_DRAGON_CATALOG: List[Dict[str, str]] = [
  {
    "name": "Rồng Đất Nâu",
    "rarity": "Phổ biến"
  },
  {
    "name": "Rồng Cây Xanh Lá",
    "rarity": "Phổ biến"
  },
  {
    "name": "Rồng Sấm Bạc",
    "rarity": "Phổ biến"
  },
  {
    "name": "Rồng Băng Xanh Biển",
    "rarity": "Phổ biến"
  },
  {
    "name": "Rồng Lửa Đỏ",
    "rarity": "Phổ biến"
  },
  {
    "name": "Rồng Băng Sấm Hai Đầu",
    "rarity": "Nguyên Liệu"
  },
  {
    "name": "Rồng Băng Đất Hai Đầu",
    "rarity": "Nguyên Liệu"
  },
  {
    "name": "Rồng Cây Băng Hai Đầu",
    "rarity": "Nguyên Liệu"
  },
  {
    "name": "Rồng Cây Lửa Hai Đầu",
    "rarity": "Nguyên Liệu"
  },
  {
    "name": "Rồng Đất Cây Hai Đầu",
    "rarity": "Nguyên Liệu"
  },
  {
    "name": "Rồng Đất Lửa Hai Đầu",
    "rarity": "Nguyên Liệu"
  },
  {
    "name": "Rồng Lửa Băng Hai Đầu",
    "rarity": "Nguyên Liệu"
  },
  {
    "name": "Rồng Lửa Sấm Hai Đầu",
    "rarity": "Nguyên Liệu"
  },
  {
    "name": "Rồng Sấm Cây Hai Đầu",
    "rarity": "Nguyên Liệu"
  },
  {
    "name": "Rồng Sấm Đất Hai Đầu",
    "rarity": "Nguyên Liệu"
  },
  {
    "name": "Rồng Băng Nâu",
    "rarity": "Hiếm"
  },
  {
    "name": "Rồng Băng Đỏ",
    "rarity": "Hiếm"
  },
  {
    "name": "Rồng Băng Bạc",
    "rarity": "Hiếm"
  },
  {
    "name": "Rồng Sấm Nâu",
    "rarity": "Hiếm"
  },
  {
    "name": "Rồng Sấm Xanh Lá",
    "rarity": "Hiếm"
  },
  {
    "name": "Rồng Sấm Xanh Biển",
    "rarity": "Hiếm"
  },
  {
    "name": "Rồng Lửa Nâu",
    "rarity": "Hiếm"
  },
  {
    "name": "Rồng Lửa Xanh Lá",
    "rarity": "Hiếm"
  },
  {
    "name": "Rồng Đất Xanh Lá",
    "rarity": "Hiếm"
  },
  {
    "name": "Rồng Đất Xanh Biển",
    "rarity": "Hiếm"
  },
  {
    "name": "Rồng Đất Bạc",
    "rarity": "Hiếm"
  },
  {
    "name": "Rồng Cây Đỏ",
    "rarity": "Hiếm"
  },
  {
    "name": "Rồng Cây Xanh Biển",
    "rarity": "Cực hiếm"
  },
  {
    "name": "Rồng Cây Nâu",
    "rarity": "Cực hiếm"
  },
  {
    "name": "Rồng Cây Bạc",
    "rarity": "Cực hiếm"
  },
  {
    "name": "Rồng Băng Xanh Lá",
    "rarity": "Cực hiếm"
  },
  {
    "name": "Rồng Lửa Xanh Biển",
    "rarity": "Cực hiếm"
  },
  {
    "name": "Rồng Lửa Bạc",
    "rarity": "Cực hiếm"
  },
  {
    "name": "Rồng Sấm Đỏ",
    "rarity": "Cực hiếm"
  },
  {
    "name": "Rồng Đất Đỏ",
    "rarity": "Cực hiếm"
  },
  {
    "name": "Rồng Vàng",
    "rarity": "Event"
  },
  {
    "name": "Rồng Bạc",
    "rarity": "Event"
  },
  {
    "name": "Rồng Lửa Mắt Xanh",
    "rarity": "Event"
  },
  {
    "name": "Rồng Xương",
    "rarity": "Event"
  },
  {
    "name": "Rồng Ma Trơi",
    "rarity": "Event"
  },
  {
    "name": "Rồng Nguyệt Long",
    "rarity": "Event"
  },
  {
    "name": "Rồng Mặt Xệ",
    "rarity": "Event"
  },
  {
    "name": "Rồng Ngũ Sắc",
    "rarity": "Event"
  },
  {
    "name": "Rồng Rùa",
    "rarity": "Event"
  },
  {
    "name": "Rồng Ma Thạch I",
    "rarity": "Event"
  },
  {
    "name": "Rồng Ma Thạch II",
    "rarity": "Event"
  },
  {
    "name": "Rồng Ma Thạch III",
    "rarity": "Event"
  },
  {
    "name": "Rồng Nụ Tầm Xuân",
    "rarity": "Event"
  },
  {
    "name": "Rồng Sơ Vơ",
    "rarity": "Event"
  },
  {
    "name": "Rồng Phi Tuyết",
    "rarity": "Event"
  },
  {
    "name": "Mắt Xanh Sapphire",
    "rarity": "Event"
  },
  {
    "name": "Khổng Tước",
    "rarity": "Event"
  },
  {
    "name": "Kỳ Lân",
    "rarity": "Event"
  },
  {
    "name": "Kỳ Lân Tím",
    "rarity": "Event"
  },
  {
    "name": "Tuần Long",
    "rarity": "Event"
  },
  {
    "name": "Phượng Hoàng Băng",
    "rarity": "Event"
  },
  {
    "name": "Phượng Hoàng Lửa",
    "rarity": "Event"
  },
  {
    "name": "Phượng Hoàng Dung Nham",
    "rarity": "Event"
  },
  {
    "name": "Huyết Nguyệt Long",
    "rarity": "Event"
  },
  {
    "name": "Việt Hải Long Vương",
    "rarity": "Event"
  },
  {
    "name": "Bất Tử Điểu",
    "rarity": "Event"
  },
  {
    "name": "Hồng Ngọc Long",
    "rarity": "Event"
  },
  {
    "name": "Tử Xà Long",
    "rarity": "Event"
  }
]


def load_dragon_catalog(base_dir: Path) -> List[Dict[str, str]]:
    """Tải danh mục 63 rồng từ dragons_db.json (luôn có bản dự phòng 63 rồng tích hợp sẵn trong code)."""
    for candidate in (Path(base_dir) / "dragons_db.json", Path(__file__).resolve().parent / "dragons_db.json"):
        if candidate.is_file():
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
                if isinstance(data, list) and len(data) >= 50:
                    return data
            except Exception:
                pass
    return [dict(item) for item in BUILTIN_DRAGON_CATALOG]

def normalize_vn(text: str) -> str:
    if not text:
        return ""
    text = text.replace("đ", "d").replace("Đ", "D")
    nfd = unicodedata.normalize("NFD", text)
    no_accent = "".join(ch for ch in nfd if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-zA-Z0-9]+", " ", no_accent.lower()).strip()


def fix_ocr_word(word: str) -> str:
    if word.isdigit():
        return word
    w = word
    if re.search(r"[a-z]", w):
        w = w.replace("0", "o").replace("6", "o").replace("1", "l")
    return DAO_RONG_OCR_MAP.get(w, w)


def extract_stars(text: str) -> Optional[int]:
    """Trích số sao (1-40) từ các dạng: '(23 sao)', '(23)', '23★', '23*', '23 sao', '23s'."""
    if not text:
        return None
    norm = normalize_vn(text)
    for pat in (
        r"(\d{1,2})\s*(?:sao|\*|s\b)",
        r"\(\s*(\d{1,2})\s*\)",
    ):
        m = re.search(pat, text if "(" in pat else norm)
        if m:
            val = int(m.group(1))
            if 1 <= val <= 40:
                return val
    m_star = re.search(r"(\d{1,2})\s*[★⭐\*]", text)
    if m_star:
        val = int(m_star.group(1))
        if 1 <= val <= 40:
            return val
    return None

def parse_rule_string(rule_str: str) -> Tuple[str, str, Optional[int]]:
    """
    Phân tích chuỗi quy tắc như:
      - 'Rồng Lửa Xanh Biển (>= 23 sao)' -> ('Rồng Lửa Xanh Biển', '>=', 23)
      - 'Rồng Lửa Xanh Biển (23 sao)'    -> ('Rồng Lửa Xanh Biển', '=', 23)
      - 'Rồng Lửa Đỏ'                    -> ('Rồng Lửa Đỏ', 'any', None)
    """
    s = rule_str.strip()
    m_ge = re.search(r"\(\s*>=\s*(\d{1,2})\s*sao\s*\)", s, re.IGNORECASE)
    if m_ge:
        name = s[: m_ge.start()].strip()
        return name, ">=", int(m_ge.group(1))
    m_eq = re.search(r"\(\s*=?\s*(\d{1,2})\s*sao\s*\)", s, re.IGNORECASE)
    if m_eq:
        name = s[: m_eq.start()].strip()
        return name, "=", int(m_eq.group(1))
    return s, "any", None


def format_rule_string(name: str, star_op: str = "any", star_val: Optional[int] = None) -> str:
    if star_op == ">=" and star_val is not None:
        return f"{name} (>= {star_val} sao)"
    if star_op == "=" and star_val is not None:
        return f"{name} ({star_val} sao)"
    return name


def clean_name_words(text: str, is_ocr: bool = False) -> List[str]:
    norm = normalize_vn(text)
    norm = re.sub(r"\b\d{1,2}\s*sa[oc]?\b", "", norm)
    norm = re.sub(r"\d{1,2}sa[oc]?\b", "", norm)
    norm = re.sub(r"\bsa[oc]?\b", "", norm)
    raw = [w for w in norm.split() if not w.isdigit()]
    if raw and raw[0] in DRAGON_PREFIXES:
        raw = raw[1:]
    if is_ocr:
        fixed = [fix_ocr_word(w) for w in raw]
        # Chỉ đổi 'bat'/'bot' -> 'dat' (Đất) khi không phải 'Bất Tử Điểu'
        if ("bat" in fixed or "bot" in fixed) and "tu" not in fixed and "dieu" not in fixed:
            fixed = ["dat" if w in ("bat", "bot") else w for w in fixed]
        # Nếu 'la' đứng trước 'bang' hoặc 'sam' trong rồng Hai Đầu -> đó là 'lua' (Rồng Lửa Băng Hai Đầu)
        for i in range(len(fixed) - 1):
            if fixed[i] == "la" and fixed[i + 1] in {"bang", "sam", "cay", "dat"} and "hai" in fixed:
                fixed[i] = "lua"
        for i in range(1, len(fixed)):
            if fixed[i - 1] == "hai" and fixed[i] in {"do", "ba", "bo", "da", "bao", "dao", "ao", "au"}:
                fixed[i] = "dau"
        return fixed
    return raw


def _score_pair(target_words: List[str], ocr_words: List[str]) -> Tuple[float, bool]:
    """Tính điểm tương đồng giữa tên chuẩn và chuỗi OCR đã chuẩn hóa."""
    if not target_words or not ocr_words:
        return 0.0, False

    t_set, o_set = set(target_words), set(ocr_words)
    for a, b in CONFLICT_PAIRS:
        if (a in t_set and b in o_set and a not in o_set) or (
            b in t_set and a in o_set and b not in o_set
        ):
            return 0.0, False

    max_len_diff = 0 if len(target_words) <= 2 else 1
    if abs(len(target_words) - len(ocr_words)) > max_len_diff:
        return 0.0, False

    t_str, o_str = " ".join(target_words), " ".join(ocr_words)
    if t_str == o_str:
        return 1.0, True

    char_sim = difflib.SequenceMatcher(None, t_str, o_str).ratio()
    word_sims = []
    word_ok = True
    for tw in target_words:
        bw = max((difflib.SequenceMatcher(None, tw, ow).ratio() for ow in ocr_words), default=0.0)
        word_sims.append(bw)
        if bw < (0.50 if len(tw) <= 2 else 0.72):
            word_ok = False

    avg_w = sum(word_sims) / max(1, len(word_sims))
    return round(0.45 * char_sim + 0.55 * avg_w, 3), word_ok


def preprocess_name_bar_color(crop_bgr: np.ndarray) -> np.ndarray:
    up = cv2.resize(crop_bgr, None, fx=2.5, fy=2.5, interpolation=cv2.INTER_CUBIC)
    return cv2.copyMakeBorder(up, 16, 16, 16, 16, cv2.BORDER_REPLICATE)


def preprocess_name_bar_binary(crop_bgr: np.ndarray) -> np.ndarray:
    up = cv2.resize(crop_bgr, None, fx=2.5, fy=2.5, interpolation=cv2.INTER_CUBIC)
    hsv = cv2.cvtColor(up, cv2.COLOR_BGR2HSV)
    _, mask = cv2.threshold(hsv[:, :, 2], 165, 255, cv2.THRESH_BINARY)
    inv = cv2.bitwise_not(mask)
    padded = cv2.copyMakeBorder(inv, 16, 16, 16, 16, cv2.BORDER_CONSTANT, value=255)
    return cv2.cvtColor(padded, cv2.COLOR_GRAY2BGR)


@dataclass
class TextMatchResult:
    found: bool
    category: str = ""
    rule_text: str = ""
    identified_name: str = ""
    identified_rarity: str = ""
    stars: Optional[int] = None
    ocr_raw: str = ""
    score: float = 0.0


class DragonTextMatcher:
    def __init__(self, catalog: Optional[List[Dict[str, str]]] = None) -> None:
        self._ocr = None
        self._init_failed = False
        self._lock = threading.Lock()
        self.catalog = catalog or []

    @property
    def is_available(self) -> bool:
        return RapidOCR is not None and not self._init_failed

    def _ensure_ocr(self) -> bool:
        if self._ocr is not None:
            return True
        if RapidOCR is None or self._init_failed:
            return False
        with self._lock:
            if self._ocr is not None:
                return True
            try:
                self._ocr = RapidOCR(use_cls=False)
                return True
            except Exception:
                self._init_failed = True
                return False

    def warmup_async(self) -> None:
        if self._ocr is None and self.is_available:
            threading.Thread(target=self._ensure_ocr, daemon=True).start()

    def read_name_crop(self, crop_bgr: np.ndarray) -> str:
        if not self._ensure_ocr() or crop_bgr is None or crop_bgr.size == 0:
            return ""
        try:
            res, _ = self._ocr(preprocess_name_bar_color(crop_bgr), use_cls=False)
            if not res:
                res, _ = self._ocr(preprocess_name_bar_binary(crop_bgr), use_cls=False)
            if not res:
                return ""
            res_sorted = sorted(res, key=lambda item: item[0][0][1])
            top_y = res_sorted[0][0][0][1]
            top_parts = [str(item[1]) for item in res_sorted if abs(item[0][0][1] - top_y) < 35]
            return " ".join(top_parts).strip()
        except Exception:
            return ""

    def identify_from_catalog(self, ocr_raw: str) -> Tuple[str, str, float]:
        """So khớp đóng chuỗi OCR vào danh mục 63 loài rồng chuẩn của Đảo Rồng."""
        if not ocr_raw or not self.catalog:
            return "", "", 0.0
        ocr_words = clean_name_words(ocr_raw, is_ocr=True)
        best_name, best_rarity, best_score = "", "", 0.0
        for item in self.catalog:
            cname = item.get("name", "")
            cwords = clean_name_words(cname, is_ocr=False)
            sc, ok = _score_pair(cwords, ocr_words)
            if ok and sc > best_score:
                best_name, best_rarity, best_score = cname, item.get("rarity", ""), sc
        return best_name, best_rarity, best_score

    def match_text_against_rules(
        self,
        ocr_raw: str,
        rules_by_category: Dict[str, List[str]],
        min_similarity: float = 0.78,
    ) -> TextMatchResult:
        if not ocr_raw or not ocr_raw.strip():
            return TextMatchResult(found=False, ocr_raw=ocr_raw)

        ocr_stars = extract_stars(ocr_raw)
        cat_name, cat_rarity, cat_score = self.identify_from_catalog(ocr_raw)
        ocr_words = clean_name_words(cat_name if cat_score >= 0.80 else ocr_raw, is_ocr=(cat_score < 0.80))

        best = TextMatchResult(
            found=False,
            identified_name=cat_name,
            identified_rarity=cat_rarity,
            stars=ocr_stars,
            ocr_raw=ocr_raw,
            score=0.0,
        )

        for cat in ("bless", "keep", "sell"):
            for rule in rules_by_category.get(cat, []):
                rule = rule.strip()
                if not rule:
                    continue
                r_name, star_op, star_val = parse_rule_string(rule)

                # Kiểm tra điều kiện số sao (any / >= / =)
                if star_op == ">=" and star_val is not None:
                    if ocr_stars is None or ocr_stars < star_val:
                        continue
                elif star_op == "=" and star_val is not None:
                    if ocr_stars is None or ocr_stars != star_val:
                        continue

                r_words = clean_name_words(r_name, is_ocr=False)
                sc, ok = _score_pair(r_words, ocr_words)
                is_match = ok and sc >= min_similarity

                if is_match and sc > best.score:
                    best = TextMatchResult(
                        found=True,
                        category=cat,
                        rule_text=rule,
                        identified_name=cat_name or r_name,
                        identified_rarity=cat_rarity,
                        stars=ocr_stars,
                        ocr_raw=ocr_raw,
                        score=sc,
                    )
                elif not best.found and sc > best.score:
                    best = TextMatchResult(
                        found=False,
                        category=cat,
                        rule_text=rule,
                        identified_name=cat_name,
                        identified_rarity=cat_rarity,
                        stars=ocr_stars,
                        ocr_raw=ocr_raw,
                        score=sc,
                    )

        return best
