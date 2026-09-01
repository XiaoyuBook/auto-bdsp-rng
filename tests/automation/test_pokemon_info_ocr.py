"""测试 pokemon_info_ocr 的纯逻辑函数。"""

import numpy as np
import pytest

from auto_bdsp_rng.automation.auto_rng.ocr_regions import OcrRegion
from auto_bdsp_rng.automation.auto_rng.pokemon_info_ocr import (
    _bbox_is_red_text,
    _clean_characteristic,
    _clean_nature,
    _detect_page_type,
    _expanded_stat_region,
    _extract_nature_and_characteristic,
    _extract_region_number,
    _extract_stat_value_text,
    _extract_stats,
    _match_characteristic_text,
    _stats_have_obvious_digit_drop,
    _is_pixel_red,
    compute_characteristic,
    extract_pokemon_info,
    _norm,
    warm_up_pokemon_info_ocr,
)


# ── _norm ──────────────────────────────────────────────────────────

def test_norm_removes_whitespace_and_punctuation():
    assert _norm("攻击 67") == "攻击67"
    assert _norm("HP 109 / 109") == "HP109109"
    assert _norm("自 大 的 性 格。") == "自大的性格"
    assert _norm("【溫 順】_？！") == "温顺"


def test_warm_up_pokemon_info_ocr_primes_full_frame(monkeypatch):
    calls = []

    def fake_ocr_rows(image, bounds):
        calls.append((image.shape, bounds))
        return []

    monkeypatch.setattr(
        "auto_bdsp_rng.automation.auto_rng.pokemon_info_ocr._ocr_rows",
        fake_ocr_rows,
    )

    warm_up_pokemon_info_ocr()

    assert calls == [((32, 96, 3), (0.0, 1.0, 0.0, 1.0))]


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


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("怕寂", "怕寂寞"),
        ("怕寂寞的", "怕寂寞"),
        ("怕寂寞的性格。", "怕寂寞"),
    ],
)
def test_clean_nature_matches_partial_or_extra_ocr_text(raw, expected):
    assert _clean_nature(raw) == expected


@pytest.mark.parametrize(
    "traditional,simplified",
    [
        ("勤奮", "勤奋"),
        ("怕寂寞", "怕寂寞"),
        ("勇敢", "勇敢"),
        ("固執", "固执"),
        ("頑皮", "顽皮"),
        ("大膽", "大胆"),
        ("坦率", "坦率"),
        ("悠閒", "悠闲"),
        ("淘氣", "淘气"),
        ("樂天", "乐天"),
        ("膽小", "胆小"),
        ("急躁", "急躁"),
        ("認真", "认真"),
        ("爽朗", "爽朗"),
        ("天真", "天真"),
        ("內斂", "内敛"),
        ("慢吞吞", "慢吞吞"),
        ("冷靜", "冷静"),
        ("害羞", "害羞"),
        ("馬虎", "马虎"),
        ("溫和", "温和"),
        ("溫順", "温顺"),
        ("自大", "自大"),
        ("慎重", "慎重"),
        ("浮躁", "浮躁"),
    ],
)
def test_clean_nature_maps_every_traditional_name(traditional, simplified):
    assert _clean_nature(f"【{traditional}】的 性 格？！") == simplified


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("溫顺", "温顺"),
        ("內・斂性格", "内敛"),
        ("悠閑的性格", "悠闲"),
        ("勤奮的", "勤奋"),
    ],
)
def test_clean_nature_accepts_mixed_spacing_and_variant_forms(raw, expected):
    assert _clean_nature(raw) == expected


# ── _clean_characteristic ─────────────────────────────────────────

def test_clean_characteristic_removes_trailing_punctuation():
    assert _clean_characteristic("经常睡午觉。") == "经常睡午觉"
    assert _clean_characteristic("经常睡午觉") == "经常睡午觉"
    assert _clean_characteristic("喜欢干味。") == "喜欢干味"
    assert _clean_characteristic("【經 常 睡 午 覺】？！") == "经常睡午觉"


@pytest.mark.parametrize(
    "traditional,simplified",
    [
        ("非常喜歡吃東西", "非常喜欢吃东西"),
        ("經常睡午覺", "经常睡午觉"),
        ("常常打瞌睡", "常常打瞌睡"),
        ("經常亂扔東西", "经常乱扔东西"),
        ("喜歡悠然自在", "喜欢悠然自在"),
        ("以力氣大為傲", "以力气大为傲"),
        ("喜歡胡鬧", "喜欢胡闹"),
        ("有點容易生氣", "有点容易生气"),
        ("喜歡打架", "喜欢打架"),
        ("血氣方剛", "血气方刚"),
        ("身體強壯", "身体强壮"),
        ("抗打能力強", "抗打能力强"),
        ("頑強不屈", "顽强不屈"),
        ("能吃苦耐勞", "能吃苦耐劳"),
        ("善於忍耐", "善于忍耐"),
        ("好奇心強", "好奇心强"),
        ("喜歡惡作劇", "喜欢恶作剧"),
        ("做事萬無一失", "做事万无一失"),
        ("經常思考", "经常思考"),
        ("一絲不苟", "一丝不苟"),
        ("性格強勢", "性格强势"),
        ("有一點點愛慕虛榮", "有一点点爱慕虚荣"),
        ("爭強好勝", "争强好胜"),
        ("不服輸", "不服输"),
        ("有一點點固執", "有一点点固执"),
        ("喜歡比誰跑得快", "喜欢比谁跑得快"),
        ("對聲音敏感", "对声音敏感"),
        ("冒冒失失", "冒冒失失"),
        ("有點容易得意忘形", "有点容易得意忘形"),
        ("逃得快", "逃得快"),
    ],
)
def test_characteristic_maps_every_traditional_phrase(traditional, simplified):
    raw = f"《{traditional}》？！"
    assert _clean_characteristic(raw) == simplified
    assert _match_characteristic_text(raw) == simplified


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


def test_detect_traditional_stats_page():
    rows = [_row("HP 109"), _row("攻擊 67"), _row("防禦 69")]
    assert _detect_page_type(rows) == "stats"


def test_detect_notes_page():
    rows = [_row("训练家笔记"), _row("自大的性格。"), _row("喜欢苦味。")]
    assert _detect_page_type(rows) == "notes"


def test_detect_traditional_notes_page():
    rows = [_row("訓練家筆記"), _row("冷靜的性格。"), _row("喜歡苦味。")]
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
    """HP 当前值/最大值格式提取斜杠后的最大 HP。"""
    rows = [
        _row_at("HP", 200, 20),
        _row_at("73/109", 200, 45),
        _row_at("攻击", 100, 90),
        _row_at("67", 100, 115),
        _row_at("防御", 100, 210),
        _row_at("69", 100, 235),
    ]
    stats = _extract_stats(rows)
    assert stats["HP"] == 109
    assert stats["攻击"] == 67


@pytest.mark.parametrize(
    ("raw", "use_max_hp", "expected"),
    [
        ("攻击 11", False, 11),
        ("特攻11", False, 11),
        ("HP 18/20", True, 20),
        ("HP 18／20", True, 20),
        ("2026年09月01日", False, None),
        ("2026年09月", False, None),
        ("2026年9月", False, None),
        ("11 10", False, None),
    ],
)
def test_extract_stat_value_text_removes_labels_and_rejects_ambiguous_numbers(raw, use_max_hp, expected):
    assert _extract_stat_value_text(raw, use_max_hp=use_max_hp) == expected


def test_extract_region_number_prefers_value_nearest_original_roi():
    anchor = OcrRegion(100, 100, 20, 20)
    rows = [
        _row_at("99", 150, 110),
        _row_at("11", 110, 110),
        _row_at("防御10", 105, 105),
    ]

    assert _extract_region_number(rows, field="attack", anchor_region=anchor) == 11


def test_extract_region_number_does_not_guess_between_values_without_positions():
    rows = [
        {"text": "11", "bbox": None, "confidence": 0.99},
        {"text": "10", "bbox": None, "confidence": 0.99},
    ]

    assert _extract_region_number(rows, field="attack", anchor_region=OcrRegion(10, 10, 20, 20)) is None


def test_extract_region_number_prefers_target_label_even_when_its_bbox_is_missing():
    rows = [
        {"text": "攻击11", "bbox": None, "confidence": 0.99},
        _row_at("99", 100, 100, 10, 10),
    ]

    assert _extract_region_number(rows, field="attack", anchor_region=OcrRegion(100, 100, 20, 20)) == 11


def test_extract_region_number_rejects_mixed_positioned_and_unpositioned_unlabelled_values():
    rows = [
        {"text": "11", "bbox": None, "confidence": 0.99},
        _row_at("99", 100, 100, 10, 10),
    ]

    assert _extract_region_number(rows, field="attack", anchor_region=OcrRegion(100, 100, 20, 20)) is None


def test_extract_region_number_rejects_only_unlabelled_value_outside_original_roi():
    rows = [_row_at("99", 123, 105, 4, 10)]

    assert _extract_region_number(rows, field="attack", anchor_region=OcrRegion(100, 100, 20, 20)) is None


def test_expanded_stat_region_uses_three_predictable_levels_and_clips_to_frame():
    region = OcrRegion(20, 30, 50, 20)

    assert _expanded_stat_region(region, 100, 100, 0) == OcrRegion(20, 30, 50, 20)
    assert _expanded_stat_region(region, 100, 100, 1) == OcrRegion(14, 26, 62, 28)
    assert _expanded_stat_region(region, 100, 100, 2) == OcrRegion(8, 22, 74, 36)
    assert _expanded_stat_region(OcrRegion(0, 0, 10, 10), 100, 100, 2) == OcrRegion(0, 0, 18, 18)


def test_extract_stats_accepts_labels_and_values_in_the_same_rows():
    rows = [
        _row_at("HP 73/109", 200, 20),
        _row_at("特攻 74", 100, 90),
        _row_at("攻击 67", 350, 90),
        _row_at("特防 81", 100, 210),
        _row_at("防御 69", 350, 210),
        _row_at("速度 66", 200, 270),
    ]

    assert _extract_stats(rows) == {"HP": 109, "攻击": 67, "防御": 69, "特攻": 74, "特防": 81, "速度": 66}


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


def test_stats_digit_drop_detection_rejects_single_digit_among_large_stats():
    assert _stats_have_obvious_digit_drop({"HP": 108, "攻击": 2, "防御": 65, "特攻": 74, "特防": 74, "速度": 5})
    assert not _stats_have_obvious_digit_drop({"HP": 108, "攻击": 66, "防御": 65, "特攻": 74, "特防": 74, "速度": 79})


def test_extract_pokemon_info_discards_obvious_digit_drop(monkeypatch):
    rows = [
        _row_at("HP", 200, 20),
        _row_at("108/108", 200, 45),
        _row_at("特攻", 100, 90),
        _row_at("攻击", 350, 90),
        _row_at("74", 100, 115),
        _row_at("2", 350, 115),
        _row_at("特防", 100, 210),
        _row_at("防御", 350, 210),
        _row_at("74", 100, 235),
        _row_at("65", 350, 235),
        _row_at("速度", 200, 270),
        _row_at("5", 200, 295),
    ]

    monkeypatch.setattr(
        "auto_bdsp_rng.automation.auto_rng.pokemon_info_ocr._ocr_rows",
        lambda *_args, **_kwargs: rows,
    )

    result = extract_pokemon_info(stats_image=np.zeros((720, 1280, 3), dtype=np.uint8))

    assert result["stats"] is None


def test_extract_pokemon_info_returns_unknown_when_ocr_has_no_match(monkeypatch):
    monkeypatch.setattr(
        "auto_bdsp_rng.automation.auto_rng.pokemon_info_ocr._ocr_rows",
        lambda *_args, **_kwargs: [],
    )

    result = extract_pokemon_info(
        stats_image=np.zeros((720, 1280, 3), dtype=np.uint8),
        notes_image=np.zeros((720, 1280, 3), dtype=np.uint8),
    )

    assert result == {"stats": None, "nature": None, "characteristic": None}


@pytest.mark.parametrize("image_field", ["stats_image", "notes_image"])
def test_extract_pokemon_info_propagates_ocr_runtime_errors(monkeypatch, image_field):
    def fail_ocr(*_args, **_kwargs):
        raise RuntimeError("inference failed")

    monkeypatch.setattr(
        "auto_bdsp_rng.automation.auto_rng.pokemon_info_ocr._ocr_rows",
        fail_ocr,
    )

    with pytest.raises(RuntimeError, match="inference failed"):
        extract_pokemon_info(**{image_field: np.zeros((720, 1280, 3), dtype=np.uint8)})


@pytest.mark.parametrize("image_field", ["stats_image", "notes_image"])
def test_extract_pokemon_info_propagates_image_loading_errors(tmp_path, image_field):
    missing = tmp_path / "missing.png"

    with pytest.raises(FileNotFoundError, match="Cannot load image"):
        extract_pokemon_info(**{image_field: missing})


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

    def test_fallback_normalizes_traditional_nature_and_characteristic(self):
        rows = [
            _make_row("【冷 靜】的性格？！", 10),
            _make_row("2026年05月12日", 30),
            _make_row("在花之樂園，", 50),
            _make_row("命中注定般地遇見了它。", 70),
            _make_row("《經常亂扔東西》？！", 90),
            _make_row("喜歡苦味。", 110),
        ]
        img = np.full((200, 100, 3), 255, dtype=np.uint8)

        nature, characteristic = _extract_nature_and_characteristic(img, rows)

        assert nature == "冷静"
        assert characteristic == "经常乱扔东西"

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


@pytest.mark.parametrize("bgr", [(0, 0, 255), (10, 0, 255)])
def test_bbox_is_red_text_detects_both_hsv_red_ranges(bgr):
    image = np.full((20, 40, 3), 255, dtype=np.uint8)
    image[4:16, 8:32] = bgr
    bbox = [[0, 0], [40, 0], [40, 20], [0, 20]]

    assert _bbox_is_red_text(image, bbox) is True


@pytest.mark.parametrize("bgr", [(255, 0, 0), (0, 255, 0), (160, 160, 160)])
def test_bbox_is_red_text_rejects_non_red_colors(bgr):
    image = np.full((20, 40, 3), 255, dtype=np.uint8)
    image[4:16, 8:32] = bgr
    bbox = [[0, 0], [40, 0], [40, 20], [0, 20]]

    assert _bbox_is_red_text(image, bbox) is False


def test_compute_characteristic_uses_ec_tie_break_and_bdsp_translation():
    assert compute_characteristic(
        0x38458EDC,
        [31, 31, 31, 9, 23, 31],
    ) == "经常睡午觉"
