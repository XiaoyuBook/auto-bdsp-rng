"""测试 pokemon_info_ocr 的纯逻辑函数。"""

import numpy as np
import pytest

from auto_bdsp_rng.automation.auto_rng.pokemon_info_ocr import (
    _clean_characteristic,
    _clean_nature,
    _detect_page_type,
    _extract_nature_and_characteristic,
    _extract_stats,
    _is_pixel_red,
    _norm,
)


# ── _norm ──────────────────────────────────────────────────────────

def test_norm_removes_whitespace_and_punctuation():
    assert _norm("攻击 67") == "攻击67"
    assert _norm("HP 109 / 109") == "HP109109"
    assert _norm("自 大 的 性 格。") == "自大的性格"


# ── _clean_nature ─────────────────────────────────────────────────

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("自大的性格。", "自大"),
        ("自大的性格", "自大"),
        ("固执性格", "固执"),
        ("胆小", "胆小"),
        ("冷静的性格。", "冷静"),
        ("顽皮的性格。", "顽皮"),
        ("温顺的性格", "温顺"),
    ],
)
def test_clean_nature_strips_suffixes(raw, expected):
    assert _clean_nature(raw) == expected


# ── _clean_characteristic ─────────────────────────────────────────

def test_clean_characteristic_removes_trailing_punctuation():
    assert _clean_characteristic("经常睡午觉。") == "经常睡午觉"
    assert _clean_characteristic("经常睡午觉") == "经常睡午觉"
    assert _clean_characteristic("喜欢干味。") == "喜欢干味"


# ── _detect_page_type ─────────────────────────────────────────────

def _row(text: str) -> dict:
    return {"text": text, "bbox": [[0, 0], [10, 0], [10, 10], [0, 10]], "confidence": 0.99}


def _row_at(text: str, x: float, y: float, w: float = 100.0, h: float = 20.0) -> dict:
    return {"text": text, "bbox": [[x, y], [x + w, y], [x + w, y + h], [x, y + h]], "confidence": 0.99}


def test_detect_stats_page():
    rows = [_row("HP 109"), _row("攻击 67"), _row("防御 69"), _row("特攻 74"), _row("特防 81"), _row("速度 66")]
    assert _detect_page_type(rows) == "stats"


def test_detect_stats_page_with_fewer_keywords():
    rows = [_row("HP"), _row("攻击"), _row("特性 叶绿素")]
    assert _detect_page_type(rows) == "stats"


def test_detect_notes_page():
    rows = [_row("训练家笔记"), _row("自大的性格。"), _row("喜欢苦味。")]
    assert _detect_page_type(rows) == "notes"


def test_detect_notes_page_with_encounter():
    rows = [_row("命中注定般地遇见了"), _row("性格 胆小"), _row("喜欢辣味")]
    assert _detect_page_type(rows) == "notes"


def test_detect_unknown():
    rows = [_row("一些无关文本"), _row("没有关键词")]
    assert _detect_page_type(rows) == "unknown"


# ── _extract_stats（空间位置提取）────────────────────────────────

def test_extract_all_six_stats_spatial():
    """标签在数值上方，同列排列。"""
    rows = [
        _row_at("HP", 200, 20),
        _row_at("109/109", 200, 45),   # HP 下方
        _row_at("特攻", 100, 90),
        _row_at("攻击", 350, 90),
        _row_at("74", 100, 115),       # 特攻下方
        _row_at("67", 350, 115),       # 攻击下方
        _row_at("特防", 100, 210),
        _row_at("防御", 350, 210),
        _row_at("81", 100, 235),
        _row_at("69", 350, 235),
        _row_at("速度", 200, 270),
        _row_at("66", 200, 295),
    ]
    stats = _extract_stats(rows)
    assert stats == {"HP": 109, "攻击": 67, "防御": 69, "特攻": 74, "特防": 81, "速度": 66}


def test_extract_stats_hp_slash():
    """HP 109/109 格式提取最大 HP。"""
    rows = [
        _row_at("HP", 200, 20),
        _row_at("109/109", 200, 45),
        _row_at("攻击", 100, 90),
        _row_at("67", 100, 115),
        _row_at("防御", 100, 210),
        _row_at("69", 100, 235),
    ]
    stats = _extract_stats(rows)
    assert stats["HP"] == 109
    assert stats["攻击"] == 67


def test_extract_stats_partial():
    """部分能力未识别时不抛异常。"""
    rows = [
        _row_at("攻击", 100, 90),
        _row_at("67", 100, 115),
        _row_at("防御", 100, 210),
        _row_at("69", 100, 235),
    ]
    stats = _extract_stats(rows)
    assert stats == {"攻击": 67, "防御": 69}


def test_extract_stats_empty():
    assert _extract_stats([]) == {}


# ── _extract_nature_and_characteristic ────────────────────────────

def _make_bbox(y: float, height: float = 12.0) -> list:
    return [[0, y], [100, y], [100, y + height], [0, y + height]]


def _make_row(text: str, y: float) -> dict:
    return {"text": text, "bbox": _make_bbox(y), "confidence": 0.99}


class TestNatureAndCharacteristic:
    def test_extract_with_red_text(self, monkeypatch):
        """模拟红字检测：第一行和最后一行是红字。"""
        rows = [
            _make_row("自大的性格。", 10),
            _make_row("2026年05月12日", 30),
            _make_row("在花之乐园，", 50),
            _make_row("命中注定般地遇见了当时Lv.30的它。", 70),
            _make_row("经常睡午觉。", 90),
            _make_row("喜欢苦味。", 110),
        ]
        red_ys = {10, 110}  # 第一行红字 + 最后一行红字

        def mock_is_red(image, bbox):
            y = bbox[0][1]
            return y in red_ys

        monkeypatch.setattr(
            "auto_bdsp_rng.automation.auto_rng.pokemon_info_ocr._bbox_is_red_text",
            mock_is_red,
        )
        # 创建假的 100x200 图片
        img = np.zeros((200, 100, 3), dtype=np.uint8)
        nature, characteristic = _extract_nature_and_characteristic(img, rows)
        assert nature == "自大"
        assert characteristic == "经常睡午觉"

    def test_fallback_without_red_text(self):
        """无红字时退化到位置规则。"""
        rows = [
            _make_row("自大的性格。", 10),
            _make_row("2026年05月12日", 30),
            _make_row("在花之乐园，", 50),
            _make_row("命中注定般地遇见了它。", 70),
            _make_row("经常睡午觉。", 90),
            _make_row("喜欢苦味。", 110),
        ]
        # 无红字，走退化逻辑：性格=第一行，个性=倒数第二行
        img = np.full((200, 100, 3), 255, dtype=np.uint8)
        nature, characteristic = _extract_nature_and_characteristic(img, rows)
        assert nature == "自大"
        assert characteristic == "经常睡午觉"

    def test_single_red_row(self, monkeypatch):
        """只有一行红字时，个性无法提取。"""
        rows = [
            _make_row("胆小的性格。", 10),
            _make_row("遇见了它。", 30),
        ]
        red_ys = {10}

        def mock_is_red(image, bbox):
            return bbox[0][1] in red_ys

        monkeypatch.setattr(
            "auto_bdsp_rng.automation.auto_rng.pokemon_info_ocr._bbox_is_red_text",
            mock_is_red,
        )
        img = np.zeros((100, 100, 3), dtype=np.uint8)
        nature, characteristic = _extract_nature_and_characteristic(img, rows)
        assert nature == "胆小"
        assert characteristic is None

    def test_empty_rows(self):
        img = np.zeros((100, 100, 3), dtype=np.uint8)
        nature, characteristic = _extract_nature_and_characteristic(img, [])
        assert nature is None
        assert characteristic is None


# ── _is_pixel_red ──────────────────────────────────────────────────

def test_is_pixel_red_true():
    assert _is_pixel_red(200, 50, 50) is True
    assert _is_pixel_red(150, 100, 80) is True


def test_is_pixel_red_false():
    assert _is_pixel_red(50, 50, 50) is False   # dark
    assert _is_pixel_red(200, 200, 200) is False  # white/gray
    assert _is_pixel_red(100, 150, 100) is False  # green > red
    assert _is_pixel_red(80, 60, 200) is False    # blue > red
