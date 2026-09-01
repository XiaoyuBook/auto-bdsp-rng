from __future__ import annotations

import numpy as np

from auto_bdsp_rng.automation.auto_rng.ocr_regions import (
    SHINY_DIALOG_REGION_FIELD,
    STARTER_BATTLE_REGION_FIELD,
    OcrRegion,
    OcrRegionConfig,
    default_ocr_region,
)
from auto_bdsp_rng.automation.auto_rng import pokemon_info_ocr
from auto_bdsp_rng.automation.auto_rng.pokemon_info_ocr import extract_pokemon_info


def test_ocr_region_clips_and_converts_to_relative_bounds():
    region = OcrRegion(90, -10, 30, 50)

    assert region.clip(100, 80) == OcrRegion(90, 0, 10, 40)
    assert region.to_relative_bounds((80, 100, 3)) == (0.9, 1.0, 0.0, 0.5)


def test_shiny_dialog_region_resolves_dynamic_lower_half_default():
    config = OcrRegionConfig()

    assert default_ocr_region(SHINY_DIALOG_REGION_FIELD, (101, 201, 3)) == OcrRegion(0, 50, 201, 51)
    assert config.resolve(SHINY_DIALOG_REGION_FIELD, (720, 1280, 3)) == OcrRegion(0, 360, 1280, 360)


def test_shiny_dialog_region_prefers_absolute_custom_and_falls_back_when_invalid():
    config = OcrRegionConfig({SHINY_DIALOG_REGION_FIELD: OcrRegion(100, 300, 500, 250)})

    assert config.resolve(SHINY_DIALOG_REGION_FIELD, (720, 1280, 3)) == OcrRegion(100, 300, 500, 250)
    assert config.to_settings_dict()[SHINY_DIALOG_REGION_FIELD] == "[100, 300, 500, 250]"

    off_frame = OcrRegionConfig({SHINY_DIALOG_REGION_FIELD: OcrRegion(2000, 1000, 20, 20)})
    empty = OcrRegionConfig.from_settings_dict({SHINY_DIALOG_REGION_FIELD: ""})
    invalid = OcrRegionConfig.from_settings_dict({SHINY_DIALOG_REGION_FIELD: "[0, 0, 0, 0]"})

    expected_default = OcrRegion(0, 360, 1280, 360)
    assert off_frame.resolve(SHINY_DIALOG_REGION_FIELD, (720, 1280, 3)) == expected_default
    assert empty.resolve(SHINY_DIALOG_REGION_FIELD, (720, 1280, 3)) == expected_default
    assert invalid.resolve(SHINY_DIALOG_REGION_FIELD, (720, 1280, 3)) == expected_default
    assert not empty.has_invalid_custom(SHINY_DIALOG_REGION_FIELD)
    assert invalid.has_invalid_custom(SHINY_DIALOG_REGION_FIELD)


def test_starter_battle_region_scales_from_calibrated_1080p_default():
    config = OcrRegionConfig()

    assert default_ocr_region(STARTER_BATTLE_REGION_FIELD, (1080, 1920, 3)) == OcrRegion(1540, 620, 170, 95)
    assert config.resolve(STARTER_BATTLE_REGION_FIELD, (720, 1280, 3)) == OcrRegion(1027, 413, 113, 64)


def test_starter_battle_region_prefers_custom_and_falls_back_when_invalid():
    custom = OcrRegion(900, 400, 200, 100)
    config = OcrRegionConfig({STARTER_BATTLE_REGION_FIELD: custom})

    assert config.resolve(STARTER_BATTLE_REGION_FIELD, (720, 1280, 3)) == custom
    assert config.to_settings_dict()[STARTER_BATTLE_REGION_FIELD] == "[900, 400, 200, 100]"

    off_frame = OcrRegionConfig({STARTER_BATTLE_REGION_FIELD: OcrRegion(2000, 1000, 20, 20)})
    invalid = OcrRegionConfig.from_settings_dict({STARTER_BATTLE_REGION_FIELD: "not-a-region"})
    expected_default = OcrRegion(1027, 413, 113, 64)

    assert off_frame.resolve(STARTER_BATTLE_REGION_FIELD, (720, 1280, 3)) == expected_default
    assert invalid.resolve(STARTER_BATTLE_REGION_FIELD, (720, 1280, 3)) == expected_default
    assert invalid.has_invalid_custom(STARTER_BATTLE_REGION_FIELD)


def test_extract_pokemon_info_prefers_configured_stat_regions(monkeypatch):
    image = np.zeros((100, 200, 3), dtype=np.uint8)
    config = OcrRegionConfig()
    for index, field in enumerate(("hp", "attack", "defense", "sp_attack", "sp_defense", "speed")):
        config.set(field, OcrRegion(index * 10, 0, 8, 8))
    values = ["108", "66", "65", "74", "74", "79"]
    calls: list[tuple[float, float, float, float]] = []

    def fake_ocr_rows(_image, roi_bounds, **_kwargs):
        calls.append(tuple(roi_bounds))
        return [{"text": values[len(calls) - 1], "bbox": None, "confidence": 0.99}]

    monkeypatch.setattr(pokemon_info_ocr, "_ocr_rows", fake_ocr_rows)

    result = extract_pokemon_info(stats_image=image, ocr_regions=config)

    assert result["stats"] == {"HP": 108, "攻击": 66, "防御": 65, "特攻": 74, "特防": 74, "速度": 79}
    assert calls == [
        (0.0, 0.04, 0.0, 0.08),
        (0.05, 0.09, 0.0, 0.08),
        (0.1, 0.14, 0.0, 0.08),
        (0.15, 0.19, 0.0, 0.08),
        (0.2, 0.24, 0.0, 0.08),
        (0.25, 0.29, 0.0, 0.08),
    ]


def test_extract_pokemon_info_falls_back_when_configured_stats_are_invalid(monkeypatch):
    image = np.zeros((100, 200, 3), dtype=np.uint8)
    config = OcrRegionConfig()
    for index, field in enumerate(("hp", "attack", "defense", "sp_attack", "sp_defense", "speed")):
        config.set(field, OcrRegion(index * 10, 0, 8, 8))
    field_values = ["108", "6", "65", "74", "74", "9"]
    broad_rows = [
        {"text": "HP", "bbox": [[20, 10], [30, 10], [30, 20], [20, 20]], "confidence": 0.99},
        {"text": "108", "bbox": [[20, 25], [30, 25], [30, 35], [20, 35]], "confidence": 0.99},
        {"text": "攻击", "bbox": [[60, 10], [70, 10], [70, 20], [60, 20]], "confidence": 0.99},
        {"text": "66", "bbox": [[60, 25], [70, 25], [70, 35], [60, 35]], "confidence": 0.99},
        {"text": "防御", "bbox": [[100, 10], [110, 10], [110, 20], [100, 20]], "confidence": 0.99},
        {"text": "65", "bbox": [[100, 25], [110, 25], [110, 35], [100, 35]], "confidence": 0.99},
        {"text": "特攻", "bbox": [[20, 50], [30, 50], [30, 60], [20, 60]], "confidence": 0.99},
        {"text": "74", "bbox": [[20, 65], [30, 65], [30, 75], [20, 75]], "confidence": 0.99},
        {"text": "特防", "bbox": [[60, 50], [70, 50], [70, 60], [60, 60]], "confidence": 0.99},
        {"text": "74", "bbox": [[60, 65], [70, 65], [70, 75], [60, 75]], "confidence": 0.99},
        {"text": "速度", "bbox": [[100, 50], [110, 50], [110, 60], [100, 60]], "confidence": 0.99},
        {"text": "79", "bbox": [[100, 65], [110, 65], [110, 75], [100, 75]], "confidence": 0.99},
    ]

    def fake_ocr_rows(_image, roi_bounds, **_kwargs):
        if len(calls) < 6:
            text = field_values[len(calls)]
            calls.append(tuple(roi_bounds))
            return [{"text": text, "bbox": None, "confidence": 0.99}]
        calls.append(tuple(roi_bounds))
        return broad_rows

    calls: list[tuple[float, float, float, float]] = []
    monkeypatch.setattr(pokemon_info_ocr, "_ocr_rows", fake_ocr_rows)

    result = extract_pokemon_info(stats_image=image, ocr_regions=config)

    assert result["stats"] == {"HP": 108, "攻击": 66, "防御": 65, "特攻": 74, "特防": 74, "速度": 79}
    assert len(calls) == 7


def test_extract_pokemon_info_can_expand_stat_regions_without_broad_fallback(monkeypatch):
    image = np.zeros((100, 200, 3), dtype=np.uint8)
    config = OcrRegionConfig()
    for index, field in enumerate(("hp", "attack", "defense", "sp_attack", "sp_defense", "speed")):
        config.set(field, OcrRegion(20 + index * 20, 30, 10, 10))
    values = ["HP 18/20", "攻击11", "防御10", "特攻11", "特防9", "速度11"]
    calls: list[tuple[float, float, float, float]] = []

    def fake_ocr_rows(_image, roi_bounds, **_kwargs):
        calls.append(tuple(roi_bounds))
        return [{"text": values[len(calls) - 1], "bbox": None, "confidence": 0.99}]

    monkeypatch.setattr(pokemon_info_ocr, "_ocr_rows", fake_ocr_rows)

    result = extract_pokemon_info(
        stats_image=image,
        ocr_regions=config,
        stats_region_expansion_level=1,
        allow_stats_page_fallback=False,
    )

    assert result["stats"] == {"HP": 20, "攻击": 11, "防御": 10, "特攻": 11, "特防": 9, "速度": 11}
    assert len(calls) == 6
    assert calls[0] == (0.08, 0.17, 0.26, 0.44)


def test_extract_pokemon_info_skips_broad_fallback_when_auto_retry_requests_it(monkeypatch):
    image = np.zeros((100, 200, 3), dtype=np.uint8)
    config = OcrRegionConfig()
    for index, field in enumerate(("hp", "attack", "defense", "sp_attack", "sp_defense", "speed")):
        config.set(field, OcrRegion(index * 10, 0, 8, 8))
    values = ["108", "6", "65", "74", "74", "9"]
    calls: list[tuple[float, float, float, float]] = []

    def fake_ocr_rows(_image, roi_bounds, **_kwargs):
        calls.append(tuple(roi_bounds))
        return [{"text": values[len(calls) - 1], "bbox": None, "confidence": 0.99}]

    monkeypatch.setattr(pokemon_info_ocr, "_ocr_rows", fake_ocr_rows)

    result = extract_pokemon_info(
        stats_image=image,
        ocr_regions=config,
        allow_stats_page_fallback=False,
    )

    assert result["stats"] is None
    assert len(calls) == 6


def test_characteristic_region_uses_exact_roi_and_same_raw_fallback_as_manual_test(monkeypatch):
    image = np.zeros((100, 200, 3), dtype=np.uint8)
    region = OcrRegion(50, 40, 20, 10)
    config = OcrRegionConfig({"characteristic": region})
    calls: list[tuple[float, float, float, float]] = []

    def fake_ocr_rows(_image, roi_bounds, **_kwargs):
        calls.append(tuple(roi_bounds))
        return [{"text": "身休强状", "bbox": [[0, 0], [1, 0], [1, 1], [0, 1]], "confidence": 0.99}]

    monkeypatch.setattr(pokemon_info_ocr, "_ocr_rows", fake_ocr_rows)

    result = extract_pokemon_info(notes_image=image, ocr_regions=config)

    assert result["characteristic"] == "身休强状"
    assert calls == [(0.25, 0.35, 0.4, 0.5)]
    assert pokemon_info_ocr.recognize_ocr_field(image, "characteristic", region) == "身休强状"
    assert calls == [(0.25, 0.35, 0.4, 0.5)] * 2


def test_configured_note_regions_never_fall_back_to_full_notes_page(monkeypatch):
    image = np.zeros((100, 200, 3), dtype=np.uint8)
    config = OcrRegionConfig(
        {
            "nature": OcrRegion(10, 10, 20, 10),
            "characteristic": OcrRegion(50, 40, 20, 10),
        }
    )
    calls: list[tuple[float, float, float, float]] = []

    def fake_ocr_rows(_image, roi_bounds, **_kwargs):
        calls.append(tuple(roi_bounds))
        text = "浮躁" if len(calls) == 1 else ""
        return [] if not text else [{"text": text, "bbox": None, "confidence": 0.99}]

    monkeypatch.setattr(pokemon_info_ocr, "_ocr_rows", fake_ocr_rows)

    result = extract_pokemon_info(notes_image=image, ocr_regions=config)

    assert result["nature"] == "浮躁"
    assert result["characteristic"] is None
    assert calls == [
        (0.05, 0.15, 0.1, 0.2),
        (0.25, 0.35, 0.4, 0.5),
    ]


def test_configured_characteristic_roi_recognizes_body_is_strong(monkeypatch):
    image = np.zeros((100, 200, 3), dtype=np.uint8)
    region = OcrRegion(50, 40, 20, 10)
    config = OcrRegionConfig({"characteristic": region})
    monkeypatch.setattr(
        pokemon_info_ocr,
        "_ocr_rows",
        lambda *_args, **_kwargs: [{"text": "身体强壮", "bbox": None, "confidence": 0.99}],
    )

    automatic = extract_pokemon_info(notes_image=image, ocr_regions=config)
    manual = pokemon_info_ocr.recognize_ocr_field(image, "characteristic", region)

    assert automatic["characteristic"] == "身体强壮"
    assert automatic["characteristic"] == manual


def test_unconfigured_notes_keep_legacy_full_page_extraction(monkeypatch):
    image = np.zeros((120, 200, 3), dtype=np.uint8)
    rows = [
        {"text": "浮躁的性格。", "bbox": [[0, 10], [100, 10], [100, 20], [0, 20]], "confidence": 0.99},
        {"text": "2026年09月01日", "bbox": [[0, 30], [100, 30], [100, 40], [0, 40]], "confidence": 0.99},
        {"text": "命中注定般地遇见了它。", "bbox": [[0, 50], [100, 50], [100, 60], [0, 60]], "confidence": 0.99},
        {"text": "身体强壮。", "bbox": [[0, 70], [100, 70], [100, 80], [0, 80]], "confidence": 0.99},
        {"text": "喜欢甜味。", "bbox": [[0, 90], [100, 90], [100, 100], [0, 100]], "confidence": 0.99},
    ]
    monkeypatch.setattr(pokemon_info_ocr, "_ocr_rows", lambda *_args, **_kwargs: rows)

    result = extract_pokemon_info(notes_image=image)

    assert result["nature"] == "浮躁"
    assert result["characteristic"] == "身体强壮"


def test_recognize_ocr_field_formats_result_for_preview(monkeypatch):
    image = np.zeros((100, 200, 3), dtype=np.uint8)

    monkeypatch.setattr(
        pokemon_info_ocr,
        "_ocr_rows",
        lambda *_args, **_kwargs: [{"text": "【膽 小】的性格？！", "bbox": [[0, 0], [1, 0], [1, 1], [0, 1]], "confidence": 0.99}],
    )

    assert pokemon_info_ocr.recognize_ocr_field(image, "nature", OcrRegion(10, 20, 30, 40)) == "胆小"


def test_recognize_characteristic_field_normalizes_traditional_text(monkeypatch):
    image = np.zeros((100, 200, 3), dtype=np.uint8)

    monkeypatch.setattr(
        pokemon_info_ocr,
        "_ocr_rows",
        lambda *_args, **_kwargs: [{"text": "《喜歡惡作劇》？！", "bbox": [[0, 0], [1, 0], [1, 1], [0, 1]], "confidence": 0.99}],
    )

    assert pokemon_info_ocr.recognize_ocr_field(image, "characteristic", OcrRegion(10, 20, 30, 40)) == "喜欢恶作剧"
