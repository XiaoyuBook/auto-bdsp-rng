"""宝可梦详情页 OCR 信息提取。

从能力页或训练家笔记页截图中提取能力值、性格、个性。
支持文件路径和 numpy 数组输入。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import numpy as np

# ── 全局 PaddleOCR 单例 ────────────────────────────────────────────
_PADDLE_OCR: object | None = None


def _get_paddle_ocr() -> object:
    global _PADDLE_OCR
    if _PADDLE_OCR is not None:
        return _PADDLE_OCR
    try:
        from paddleocr import PaddleOCR
    except ImportError as exc:
        raise RuntimeError("PaddleOCR is not installed") from exc
    _PADDLE_OCR = _create_paddle_ocr(PaddleOCR)
    return _PADDLE_OCR


def _create_paddle_ocr(factory: Any) -> object:
    attempts = (
        {"lang": "ch", "use_doc_orientation_classify": False, "use_doc_unwarping": False, "use_textline_orientation": False},
        {"lang": "ch", "use_angle_cls": False},
        {"lang": "ch"},
    )
    last_error: Exception | None = None
    for kwargs in attempts:
        try:
            return factory(**kwargs)
        except Exception as exc:
            last_error = exc
    raise RuntimeError("Cannot initialize PaddleOCR") from last_error


def _ocr_rows(image: np.ndarray, roi_bounds: tuple[float, float, float, float]) -> list[dict[str, object]]:
    """对 ROI 区域做 OCR，返回行级结果列表。

    每行: {"text": str, "bbox": [[x1,y1],[x2,y2],[x3,y3],[x4,y4]], "confidence": float}
    """
    h, w = image.shape[:2]
    x1 = int(w * roi_bounds[0])
    x2 = int(w * roi_bounds[1])
    y1 = int(h * roi_bounds[2])
    y2 = int(h * roi_bounds[3])

    if x2 <= x1 or y2 <= y1:
        return []

    roi = image[y1:y2, x1:x2]
    if roi.size == 0:
        return []

    ocr = _get_paddle_ocr()
    predict = getattr(ocr, "predict", None)
    if callable(predict):
        raw = predict(roi)
    else:
        legacy = getattr(ocr, "ocr", None)
        if not callable(legacy):
            raise RuntimeError("PaddleOCR does not expose a supported OCR method")
        try:
            raw = legacy(roi, cls=False)
        except TypeError:
            raw = legacy(roi)

    rows: list[dict[str, object]] = []
    if isinstance(raw, list):
        for item in raw:
            parsed = _parse_ocr_item(item)
            if parsed is not None:
                # 将 ROI 坐标还原为原图坐标
                bbox = parsed.get("bbox")
                if isinstance(bbox, list) and len(bbox) == 4:
                    adjusted: list[list[float]] = []
                    for pt in bbox:  # type: ignore[assignment]
                        if isinstance(pt, (list, tuple)) and len(pt) == 2:
                            adjusted.append([float(pt[0]) + x1, float(pt[1]) + y1])
                    parsed["bbox"] = adjusted
                rows.append(parsed)
    return rows


def _parse_ocr_item(item: object) -> dict[str, object] | None:
    """解析单个 PaddleOCR 行结果为 {text, bbox, confidence}。"""
    if item is None:
        return None
    # 格式: [[[x1,y1],[x2,y2],[x3,y3],[x4,y4]], (text, confidence)]
    # 或 PaddleOCR 新版 predict 格式: {"rec_text": ..., "dt_polys": ..., "rec_score": ...}
    if isinstance(item, dict):
        text = item.get("rec_text") or item.get("text")
        if not text or not isinstance(text, str):
            return None
        bbox = item.get("dt_polys") or item.get("bbox")
        confidence = item.get("rec_score") or item.get("confidence") or 0.0
        return {"text": str(text).strip(), "bbox": bbox, "confidence": float(confidence)}  # type: ignore[arg-type]
    if isinstance(item, (list, tuple)) and len(item) >= 2:
        bbox_raw, text_info = item[0], item[1]
        if isinstance(text_info, (list, tuple)) and len(text_info) >= 2:
            return {
                "text": str(text_info[0]).strip(),
                "bbox": list(bbox_raw) if isinstance(bbox_raw, (list, tuple)) else None,
                "confidence": float(text_info[1]),
            }
    return None


def _norm(text: str) -> str:
    """文本规范化：去空格、换行、标点。"""
    return re.sub(r"[\s\W_]+", "", text, flags=re.UNICODE)


# ── 页面类型判断 ──────────────────────────────────────────────────

# 能力页关键词（出现在同一图中）
_STATS_KEYWORDS = {"HP", "攻击", "防御", "特攻", "特防", "速度", "特性"}

# 笔记页关键词
_NOTES_KEYWORDS = {"性格", "喜欢", "遇见", "训练家笔记", "命中注定"}


def _detect_page_type(rows: list[dict[str, object]]) -> str:
    """根据 OCR 行文本判断页面类型。"""
    all_text = " ".join(str(r["text"]) for r in rows)
    norm_all = _norm(all_text)
    stats_hits = sum(1 for kw in _STATS_KEYWORDS if _norm(kw) in norm_all)
    notes_hits = sum(1 for kw in _NOTES_KEYWORDS if _norm(kw) in norm_all)
    if stats_hits >= 3:
        return "stats"
    if notes_hits >= 2:
        return "notes"
    if stats_hits > notes_hits:
        return "stats"
    if notes_hits > 0:
        return "notes"
    return "unknown"


# ── 能力页提取 ────────────────────────────────────────────────────

_STAT_NAMES = ["HP", "攻击", "防御", "特攻", "特防", "速度"]
_STAT_PATTERNS: dict[str, str] = {
    "HP": r"HP\D*(\d{1,3})",
    "攻击": r"攻击\D*(\d{1,3})",
    "防御": r"防御\D*(\d{1,3})",
    "特攻": r"特攻\D*(\d{1,3})",
    "特防": r"特防\D*(\d{1,3})",
    "速度": r"速度\D*(\d{1,3})",
}
# HP 特殊格式：HP 109/109 → 取 109
_HP_SLASH = re.compile(r"HP\D*(\d{1,3})\s*/\s*\d{1,3}")
# 容错：OCR 把 HP 识别成 HP
_HP_ALT = re.compile(r"HP\D*(\d{1,3})")


def _extract_stats(rows: list[dict[str, object]]) -> dict[str, int]:
    """从 OCR 行中提取六项能力值。"""
    stats: dict[str, int] = {}
    # 拼接所有文本行，保留换行以辅助 HP 特殊匹配
    full = "\n".join(str(r["text"]) for r in rows)
    norm = re.sub(r"\s+", "", full)

    for name in _STAT_NAMES:
        pattern = _STAT_PATTERNS[name]
        match = re.search(pattern, norm)
        if match:
            val = int(match.group(1))
            if 0 <= val <= 999:
                stats[name] = val
        # HP 特殊处理：HP 109/109 格式
        if name == "HP" and "HP" not in stats:
            hp_match = _HP_SLASH.search(norm)
            if hp_match:
                stats["HP"] = int(hp_match.group(1))
            else:
                hp_alt = _HP_ALT.search(norm)
                if hp_alt:
                    val = int(hp_alt.group(1))
                    if 0 <= val <= 999:
                        stats["HP"] = val
    return stats


# ── 笔记页提取 ────────────────────────────────────────────────────

# 性格清洗：去掉 "的性格" "性格" "。" 等后缀
_NATURE_CLEAN = re.compile(r"的性格[。.]?$|性格[。.]?$|[。.]+$")


def _clean_nature(text: str) -> str:
    return _NATURE_CLEAN.sub("", text).strip()


def _clean_characteristic(text: str) -> str:
    return text.rstrip("。.").strip()


def _is_pixel_red(r: int, g: int, b: int) -> bool:
    """判断单个像素是否为红色文字。
    红色文字特点：R 通道明显高于 G 和 B 通道，且饱和度较高。
    """
    if r < 100:
        return False
    return r > g * 1.3 and r > b * 1.3


def _bbox_is_red_text(image: np.ndarray, bbox: object) -> bool:
    """检测 bbox 区域的文字是否为红色。"""
    if bbox is None:
        return False
    if not isinstance(bbox, (list, tuple)) or len(bbox) < 4:
        return False
    pts = bbox  # type: ignore[assignment]
    h, w = image.shape[:2]
    x_vals = [min(max(int(p[0]), 0), w - 1) for p in pts[:4]]  # type: ignore[index]
    y_vals = [min(max(int(p[1]), 0), h - 1) for p in pts[:4]]  # type: ignore[index]
    x1, x2 = max(0, min(x_vals)), min(w, max(x_vals))
    y1, y2 = max(0, min(y_vals)), min(h, max(y_vals))
    if x2 <= x1 or y2 <= y1:
        return False

    region = image[y1:y2, x1:x2]
    if region.size == 0:
        return False
    # 转为 RGB
    if region.ndim == 3 and region.shape[2] >= 3:
        rgb = region[:, :, :3]
    else:
        return False

    # 过滤接近白色/黑色的像素
    mask = (rgb[:, :, 1] > 40) | (rgb[:, :, 2] > 40)
    if not np.any(mask):
        return False

    r_chan = rgb[:, :, 0][mask]
    g_chan = rgb[:, :, 1][mask]
    b_chan = rgb[:, :, 2][mask]
    if len(r_chan) < 5:
        return False

    red_mask = (r_chan.astype(float) > g_chan.astype(float) * 1.3) & (
        r_chan.astype(float) > b_chan.astype(float) * 1.3
    ) & (r_chan > 100)
    return float(np.count_nonzero(red_mask)) / len(r_chan) > 0.25


def _extract_nature_and_characteristic(
    image: np.ndarray, rows: list[dict[str, object]]
) -> tuple[str | None, str | None]:
    """从笔记页 OCR 行中提取性格和个性。

    返回 (nature, characteristic)。
    """
    if not rows:
        return None, None

    # 按 y 坐标排序行（从上到下）
    def _row_y(row: dict[str, object]) -> float:
        bbox = row.get("bbox")
        if isinstance(bbox, (list, tuple)) and len(bbox) >= 1:
            pt0 = bbox[0]  # type: ignore[index]
            if isinstance(pt0, (list, tuple)) and len(pt0) >= 2:
                return float(pt0[1])
        return 0.0

    sorted_rows = sorted(rows, key=_row_y)

    # 检测每行是否为红字
    red_rows: list[dict[str, object]] = []
    for row in sorted_rows:
        if _bbox_is_red_text(image, row.get("bbox")):
            red_rows.append(row)

    nature: str | None = None
    characteristic: str | None = None

    if red_rows:
        # 性格 = 第一行红字
        nature = _clean_nature(str(red_rows[0]["text"]))

        # 个性 = 最后一行红字的上一行（在全部行中找）
        if len(red_rows) >= 1:
            last_red = red_rows[-1]
            last_red_idx = None
            for i, row in enumerate(sorted_rows):
                if row is last_red:
                    last_red_idx = i
                    break
            if last_red_idx is not None and last_red_idx > 0:
                prev_row = sorted_rows[last_red_idx - 1]
                characteristic = _clean_characteristic(str(prev_row["text"]))
    else:
        # 退化：无红字信息时按行位置规则
        texts = [str(r["text"]).strip() for r in sorted_rows if str(r["text"]).strip()]
        if texts:
            # 性格 = 第一行
            nature = _clean_nature(texts[0])
            # 个性 = 倒数第二行（最后一行通常是口味偏好）
            if len(texts) >= 2:
                characteristic = _clean_characteristic(texts[-2])

    # 验证：性格应该是2-4个中文字符
    if nature and not re.match(r"^[一-鿿]{2,4}$", nature):
        nature = None
    # 个性应该在3-20个中文字符之间
    if characteristic and not re.match(r"^[一-鿿]{3,20}$", characteristic):
        pass  # 保留，OCR 可能不完美

    return nature, characteristic


# ── 主入口 ─────────────────────────────────────────────────────────

# 能力页 ROI：左侧面板 (左2%, 右52%, 上15%, 下80%)
STATS_ROI = (0.02, 0.52, 0.15, 0.80)
# 笔记页 ROI：左侧笔记区 (左2%, 右52%, 上15%, 下75%)
NOTES_ROI = (0.02, 0.52, 0.15, 0.75)


def extract_pokemon_info(image_input: str | Path | np.ndarray) -> dict[str, object]:
    """从宝可梦详情页截图中提取结构化信息。

    Args:
        image_input: 图片文件路径 或 numpy 数组 (H, W, 3)

    Returns:
        {"stats": {...} or None, "nature": str or None, "characteristic": str or None}
    """
    try:
        image = _load_image(image_input)
        return _extract_pokemon_info_impl(image)
    except Exception:
        return {"stats": None, "nature": None, "characteristic": None}


def _load_image(image_input: str | Path | np.ndarray) -> np.ndarray:
    if isinstance(image_input, np.ndarray):
        return image_input
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("OpenCV is required for image loading") from exc
    img = cv2.imread(str(image_input))
    if img is None:
        raise FileNotFoundError(f"Cannot load image: {image_input}")
    return img


def _extract_pokemon_info_impl(image: np.ndarray) -> dict[str, object]:
    # 对两种 ROI 分别 OCR
    stats_rows = _ocr_rows(image, STATS_ROI)
    notes_rows = _ocr_rows(image, NOTES_ROI)

    # 判断页面类型
    page_type = _detect_page_type(stats_rows)
    if page_type == "unknown":
        page_type = _detect_page_type(notes_rows)

    if page_type == "stats":
        stats = _extract_stats(stats_rows)
        result: dict[str, object] = {"stats": stats if len(stats) >= 3 else None, "nature": None, "characteristic": None}
        if result["stats"] is None:
            # 尝试也从笔记行提取
            nature, chara = _extract_nature_and_characteristic(image, notes_rows)
            result["nature"] = nature
            result["characteristic"] = chara
        return result

    if page_type == "notes":
        nature, chara = _extract_nature_and_characteristic(image, notes_rows)
        return {"stats": None, "nature": nature, "characteristic": chara}

    # unknown: 两种都试
    stats = _extract_stats(stats_rows)
    nature, chara = _extract_nature_and_characteristic(image, notes_rows)
    return {
        "stats": stats if len(stats) >= 3 else None,
        "nature": nature,
        "characteristic": chara,
    }
