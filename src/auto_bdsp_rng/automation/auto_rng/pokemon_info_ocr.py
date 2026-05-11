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


def _ocr_rows(
    image: np.ndarray,
    roi_bounds: tuple[float, float, float, float],
    *,
    debug_raw: list[object] | None = None,
) -> list[dict[str, object]]:
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

    if debug_raw is not None:
        debug_raw.append(raw)

    rows: list[dict[str, object]] = []
    # 新版 PaddleOCR predict() 返回 list[OCRResult]
    if isinstance(raw, list) and len(raw) >= 1 and isinstance(raw[0], dict):
        first = raw[0]
        # 检测是否为 OCRResult（有 rec_texts / rec_scores / dt_polys 等并行列表字段）
        if isinstance(first.get("rec_texts"), list) or isinstance(first.get("dt_polys"), list):
            rows = _parse_ocr_result(first)  # type: ignore[arg-type]
        else:
            for item in raw:
                parsed = _parse_ocr_item(item)
                if parsed is not None:
                    rows.append(parsed)
    elif isinstance(raw, list):
        for item in raw:
            parsed = _parse_ocr_item(item)
            if parsed is not None:
                rows.append(parsed)
    # 将 ROI 坐标还原为原图坐标
    for row in rows:
        bbox = row.get("bbox")
        if isinstance(bbox, list) and len(bbox) >= 4:
            adjusted: list[list[float]] = []
            for pt in bbox:  # type: ignore[assignment]
                if isinstance(pt, (list, tuple)) and len(pt) == 2:
                    adjusted.append([float(pt[0]) + x1, float(pt[1]) + y1])
            row["bbox"] = adjusted
    return rows


def _to_list_bbox(bbox: object) -> list[list[float]]:
    """将 numpy 数组或嵌套列表转为统一的 list[list[float]] 格式。"""
    if hasattr(bbox, "tolist"):
        bbox = bbox.tolist()  # type: ignore[union-attr]
    result: list[list[float]] = []
    if isinstance(bbox, (list, tuple)):
        for pt in bbox:  # type: ignore[assignment]
            if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                result.append([float(pt[0]), float(pt[1])])
    return result


def _parse_ocr_item(item: object) -> dict[str, object] | None:
    """解析单个 PaddleOCR 行结果为 {text, bbox, confidence}。"""
    if item is None:
        return None
    # 旧版格式: [[[x1,y1],[x2,y2],[x3,y3],[x4,y4]], (text, confidence)]
    if isinstance(item, (list, tuple)) and len(item) >= 2:
        bbox_raw, text_info = item[0], item[1]
        if isinstance(text_info, (list, tuple)) and len(text_info) >= 2:
            return {
                "text": str(text_info[0]).strip(),
                "bbox": list(bbox_raw) if isinstance(bbox_raw, (list, tuple)) else None,
                "confidence": float(text_info[1]),
            }
    return None


def _parse_ocr_result(item: dict[str, object]) -> list[dict[str, object]]:
    """解析新版 PaddleOCR predict() 返回的 OCRResult 字典。

    格式: {"rec_texts": [...], "rec_scores": [...], "dt_polys": [...], "rec_polys": [...]}
    每个列表的索引一一对应，转换为行级结果列表。
    """
    texts = item.get("rec_texts") or item.get("rec_text") or []
    if isinstance(texts, str):
        texts = [texts]
    scores = item.get("rec_scores") or item.get("rec_score") or []
    if isinstance(scores, (int, float)):
        scores = [float(scores)]
    polys = item.get("rec_polys") or item.get("dt_polys") or item.get("bbox") or []
    if isinstance(polys, dict):
        polys = [polys]

    rows: list[dict[str, object]] = []
    for i, text in enumerate(texts):
        text_str = str(text).strip() if text else ""
        if not text_str:
            continue
        confidence = float(scores[i]) if i < len(scores) else 0.0
        bbox = polys[i] if i < len(polys) else None
        rows.append({
            "text": text_str,
            "bbox": _to_list_bbox(bbox) if bbox is not None else None,
            "confidence": confidence,
        })
    return rows


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


def _row_center(bbox: object) -> tuple[float, float]:
    """返回 bbox 的中心坐标 (cx, cy)。"""
    if isinstance(bbox, list) and len(bbox) >= 4:
        xs = [float(pt[0]) for pt in bbox[:4]]  # type: ignore[index]
        ys = [float(pt[1]) for pt in bbox[:4]]  # type: ignore[index]
        return sum(xs) / len(xs), sum(ys) / len(ys)
    return (0.0, 0.0)


def _match_stat_name(text: str) -> str | None:
    """匹配文本中的能力名，返回标准化名称。"""
    norm = _norm(text)
    for name in _STAT_NAMES:
        if _norm(name) in norm:
            return name
    return None


def _extract_stats(rows: list[dict[str, object]]) -> dict[str, int]:
    """从 OCR 行中提取六项能力值（基于空间位置关联标签与数值）。"""
    if not rows:
        return {}
    stats: dict[str, int] = {}
    # 分类行：标签行 vs 数值行
    label_rows: dict[str, dict[str, object]] = {}
    number_rows: list[dict[str, object]] = []
    for row in rows:
        text = str(row["text"]).strip()
        stat_name = _match_stat_name(text)
        if stat_name is not None:
            label_rows[stat_name] = row
        elif re.match(r"^\d{1,3}$", text) or re.match(r"^\d{1,3}/\d{1,3}$", text):
            number_rows.append(row)

    # 为每个数值行提取数值和坐标
    num_entries: list[tuple[float, float, int]] = []  # (cx, cy, value)
    for num_row in number_rows:
        num_text = str(num_row["text"]).strip()
        nx, ny = _row_center(num_row.get("bbox"))
        match = re.match(r"(\d{1,3})", num_text)
        if match:
            val = int(match.group(1))
            if 0 <= val <= 999:
                num_entries.append((nx, ny, val))

    # 贪心匹配：每个标签找最近的数值（上下双向），数值不重复分配
    used_num: set[int] = set()
    # 按标签的 x 坐标排序，左列优先匹配左列数值
    label_items = sorted(label_rows.items(), key=lambda kv: _row_center(kv[1].get("bbox"))[0])
    for name, label_row in label_items:
        lx, ly = _row_center(label_row.get("bbox"))
        best_idx: int | None = None
        best_dist = float("inf")
        for i, (nx, ny, val) in enumerate(num_entries):
            if i in used_num:
                continue
            if abs(nx - lx) > 150:
                continue
            dist = abs(ny - ly)  # 上下均可
            if dist > 60:  # 距离太远忽略
                continue
            if dist < best_dist:
                best_dist = dist
                best_idx = i
        if best_idx is not None:
            stats[name] = num_entries[best_idx][2]
            used_num.add(best_idx)
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

ImageInput = str | Path | np.ndarray


def extract_pokemon_info(
    stats_image: ImageInput | None = None,
    notes_image: ImageInput | None = None,
) -> dict[str, object]:
    """从宝可梦详情页截图中提取结构化信息。

    需要两张截图：
    - stats_image: 能力页截图，提取六项能力值
    - notes_image: 训练家笔记页截图，提取性格和个性

    任一图片为 None 时，对应字段返回 None。

    Args:
        stats_image: 能力页图片路径 或 numpy 数组
        notes_image: 笔记页图片路径 或 numpy 数组

    Returns:
        {"stats": {...} or None, "nature": str or None, "characteristic": str or None}
    """
    result: dict[str, object] = {"stats": None, "nature": None, "characteristic": None}
    # 能力页 → stats
    if stats_image is not None:
        try:
            img = _load_image(stats_image)
            stats_rows = _ocr_rows(img, STATS_ROI)
            if _detect_page_type(stats_rows) == "unknown":
                # 也可能放进错了，用笔记 ROI 再试
                alt_rows = _ocr_rows(img, NOTES_ROI)
                if _detect_page_type(alt_rows) == "stats":
                    stats_rows = alt_rows
            stats = _extract_stats(stats_rows)
            if len(stats) >= 3:
                result["stats"] = stats
        except Exception:
            pass
    # 笔记页 → nature + characteristic
    if notes_image is not None:
        try:
            img = _load_image(notes_image)
            notes_rows = _ocr_rows(img, NOTES_ROI)
            if _detect_page_type(notes_rows) == "unknown":
                alt_rows = _ocr_rows(img, STATS_ROI)
                if _detect_page_type(alt_rows) == "notes":
                    notes_rows = alt_rows
            nature, chara = _extract_nature_and_characteristic(img, notes_rows)
            result["nature"] = nature
            result["characteristic"] = chara
        except Exception:
            pass
    return result


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


# ── CLI 测试入口 ───────────────────────────────────────────────────

if __name__ == "__main__":
    import json
    import sys
    import traceback

    stats_path: str | None = None
    notes_path: str | None = None
    debug = "--debug" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--debug"]

    i = 0
    while i < len(args):
        if args[i] == "--stats" and i + 1 < len(args):
            stats_path = args[i + 1]
            i += 2
        elif args[i] == "--notes" and i + 1 < len(args):
            notes_path = args[i + 1]
            i += 2
        elif stats_path is None:
            stats_path = args[i]
            i += 1
        elif notes_path is None:
            notes_path = args[i]
            i += 1
        else:
            i += 1

    if stats_path is None and notes_path is None:
        print("用法: python -m auto_bdsp_rng.automation.auto_rng.pokemon_info_ocr [--debug] [--stats 能力页.png] [--notes 笔记页.png]")
        print("      也可直接传位置参数: python ... 能力页.png 笔记页.png")
        sys.exit(1)

    print(f"能力页: {stats_path or '(未提供)'}")
    print(f"笔记页: {notes_path or '(未提供)'}")
    print("正在 OCR 识别...")

    if not debug:
        result = extract_pokemon_info(stats_image=stats_path, notes_image=notes_path)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        # 诊断模式：逐步执行并输出中间结果
        for label, path, roi_bounds in (
            ("能力页", stats_path, STATS_ROI),
            ("笔记页", notes_path, NOTES_ROI),
        ):
            if not path:
                continue
            print(f"\n--- 加载{label}: {path} ---")
            try:
                img = _load_image(path)
                h, w = img.shape[:2]
                print(f"图片尺寸: {img.shape} (宽={w}, 高={h})")
                x1 = int(w * roi_bounds[0]); x2 = int(w * roi_bounds[1])
                y1 = int(h * roi_bounds[2]); y2 = int(h * roi_bounds[3])
                print(f"ROI 裁剪: x=[{x1}:{x2}], y=[{y1}:{y2}] ({x2-x1}x{y2-y1})")

                # 1) 先跑 ROI OCR
                raw_holder: list[object] = []
                rows = _ocr_rows(img, roi_bounds, debug_raw=raw_holder)
                print(f"ROI OCR 行数: {len(rows)}")
                for r in rows:
                    bbox = r.get("bbox")
                    print(f"  [{r['confidence']:.2f}] bbox={bbox} \"{r['text']}\"")
                if raw_holder:
                    raw = raw_holder[0]
                    print(f"PaddleOCR 原始输出类型: {type(raw).__name__}, "
                          f"list={isinstance(raw, list)}, "
                          f"len={len(raw) if isinstance(raw, (list, tuple)) else 'N/A'}")
                    if isinstance(raw, list) and len(raw) > 0:
                        first = raw[0]
                        print(f"第一条类型: {type(first).__name__}")
                        if isinstance(first, dict):
                            print(f"第一条 keys: {list(first.keys())}")
                            print(f"第一条: {first}")
                        elif isinstance(first, (list, tuple)) and len(first) >= 2:
                            print(f"第一条[0]类型: {type(first[0]).__name__}")
                            print(f"第一条[1]类型: {type(first[1]).__name__}")
                            print(f"第一条: {first}")
                        else:
                            print(f"第一条: {first}")
                else:
                    print("PaddleOCR 原始输出为空或 None")

                # 2) 页面判断
                page = _detect_page_type(rows)
                print(f"页面类型判断: {page}")

                # 3) 提取
                if "stats" in label.lower() or page == "stats":
                    stats = _extract_stats(rows)
                    print(f"提取能力: {stats}")
                if "笔记" in label or page == "notes":
                    nature, chara = _extract_nature_and_characteristic(img, rows)
                    print(f"性格: {nature}, 个性: {chara}")

                # 4) 全图 OCR 验证
                print(f"\n全图 OCR 测试...")
                full_raw = _ocr_rows(img, (0.0, 1.0, 0.0, 1.0))
                print(f"全图 OCR 行数: {len(full_raw)}")
                for r in full_raw[:20]:
                    print(f"  [{r['confidence']:.2f}] \"{r['text']}\"")
                if len(full_raw) > 20:
                    print(f"  ... 共 {len(full_raw)} 行，仅显示前20")

            except Exception:
                traceback.print_exc()
        print(f"\n--- 最终合并结果 ---")
        result = extract_pokemon_info(stats_image=stats_path, notes_image=notes_path)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
