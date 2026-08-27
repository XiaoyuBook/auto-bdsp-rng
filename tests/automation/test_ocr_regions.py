from __future__ import annotations

import numpy as np

from auto_bdsp_rng.automation.auto_rng.ocr_regions import (
    SHINY_DIALOG_REGION_FIELD,
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


def test_extract_pokemon_info_prefers_configured_stat_regions(monkeypatch):
    image = np.zeros((100, 200, 3), dtype=np.uint8)
    config = OcrRegionConfig()
    for index, field in enumerate(("hp", "attack", "defense", "sp_attack", "sp_defense", "speed")):
        config.set(field, OcrRegion(index * 10, 0, 8, 8))
    values = ["108", "66", "65", "74", "74", "79"]
    calls: list[tuple[float, float, float, float]] = []

    def fake_ocr_rows(_image, roi_bounds, **_kwargs):
        calls.append(tuple(roi_bounds))
        return [{"text": values[len(calls) - 1], "bbox": [[0, 0], [1, 0], [1, 1], [0, 1]], "confidence": 0.99}]

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
            return [{"text": text, "bbox": [[0, 0], [1, 0], [1, 1], [0, 1]], "confidence": 0.99}]
        calls.append(tuple(roi_bounds))
        return broad_rows

    calls: list[tuple[float, float, float, float]] = []
    monkeypatch.setattr(pokemon_info_ocr, "_ocr_rows", fake_ocr_rows)

    result = extract_pokemon_info(stats_image=image, ocr_regions=config)

    assert result["stats"] == {"HP": 108, "攻击": 66, "防御": 65, "特攻": 74, "特防": 74, "速度": 79}
    assert len(calls) == 7


def test_characteristic_region_retries_with_expanded_region(monkeypatch):
    image = np.zeros((100, 200, 3), dtype=np.uint8)
    config = OcrRegionConfig({"characteristic": OcrRegion(50, 40, 20, 10)})
    calls: list[tuple[float, float, float, float]] = []

    def fake_ocr_rows(_image, roi_bounds, **_kwargs):
        calls.append(tuple(roi_bounds))
        text = "欢胡" if len(calls) == 1 else "喜欢胡闹"
        return [{"text": text, "bbox": [[0, 0], [1, 0], [1, 1], [0, 1]], "confidence": 0.99}]

    monkeypatch.setattr(pokemon_info_ocr, "_ocr_rows", fake_ocr_rows)

    result = extract_pokemon_info(notes_image=image, ocr_regions=config)

    assert result["characteristic"] == "喜欢胡闹"
    assert calls[0] == (0.25, 0.35, 0.4, 0.5)
    assert calls[1][0] < calls[0][0]
    assert calls[1][1] > calls[0][1]


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
