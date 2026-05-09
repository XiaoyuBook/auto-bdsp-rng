from __future__ import annotations

import numpy as np
import pytest

from auto_bdsp_rng.automation.auto_rng.dialog_timing import (
    detect_bdsp_dialog_box,
    measure_dialog_interval,
    measure_keyword_interval,
    normalize_ocr_text,
    _extract_paddle_text,
)


def _blank_frame() -> np.ndarray:
    return np.zeros((728, 1040, 3), dtype=np.uint8)


def _bdsp_dialog_frame() -> np.ndarray:
    frame = _blank_frame()
    frame[520:615, 16:1016] = (245, 245, 245)
    frame[515:520, 16:1016] = (45, 45, 45)
    frame[615:620, 16:1016] = (45, 45, 45)
    frame[520:615, 12:16] = (45, 45, 45)
    frame[520:615, 1016:1020] = (45, 45, 45)
    return frame


def _non_battle_menu_frame() -> np.ndarray:
    frame = _blank_frame()
    frame[500:690, 60:350] = (245, 245, 245)
    frame[500:690, 390:680] = (245, 245, 245)
    frame[500:690, 720:1000] = (245, 245, 245)
    return frame


def test_dialog_detector_rejects_non_battle_white_menu_panels():
    assert detect_bdsp_dialog_box(_non_battle_menu_frame()) is False


def test_dialog_detector_accepts_bdsp_bottom_dialog_box():
    assert detect_bdsp_dialog_box(_bdsp_dialog_frame()) is True


def test_measure_dialog_interval_ignores_dialog_visible_at_start_until_clear():
    frames = iter(
        [
            _bdsp_dialog_frame(),
            _bdsp_dialog_frame(),
            _blank_frame(),
            _blank_frame(),
            _bdsp_dialog_frame(),
            _blank_frame(),
            _bdsp_dialog_frame(),
        ]
    )
    times = iter([0.0, 0.1, 0.2, 0.4, 1.0, 1.5, 3.0, 3.1])

    result = measure_dialog_interval(
        lambda: next(frames),
        monotonic=lambda: next(times),
        sleep=lambda _seconds: None,
        timeout_seconds=5.0,
        stable_clear_seconds=0.2,
    )

    assert result.interval_seconds == pytest.approx(1.6)


def test_normalize_ocr_text_removes_spaces_and_punctuation():
    assert normalize_ocr_text("谢 米 出 现 了 !") == "谢米出现了"
    assert normalize_ocr_text("去吧！ 图图犬！") == "去吧图图犬"


def test_measure_keyword_interval_uses_ocr_keywords_in_order():
    frames = iter([object(), object(), object(), object(), object()])
    texts = iter(["菜单", "谢米出现了！", "谢米出现了！", "空白", "去吧！图图犬！"])
    times = iter([0.0, 0.1, 0.2, 0.3, 2.6, 2.7])

    result = measure_keyword_interval(
        lambda: next(frames),
        lambda _frame: next(texts),
        monotonic=lambda: next(times),
        sleep=lambda _seconds: None,
        timeout_seconds=5.0,
        poll_interval_seconds=0.1,
    )

    assert result.interval_seconds == pytest.approx(2.5)


def test_measure_keyword_interval_ignores_second_keyword_before_first_keyword():
    frames = iter([object(), object(), object(), object()])
    texts = iter(["去吧！", "菜单", "谢米出现了！", "去吧！图图犬！"])
    times = iter([0.0, 0.1, 0.2, 0.3, 1.5])

    result = measure_keyword_interval(
        lambda: next(frames),
        lambda _frame: next(texts),
        monotonic=lambda: next(times),
        sleep=lambda _seconds: None,
        timeout_seconds=5.0,
        poll_interval_seconds=0.1,
    )

    assert result.interval_seconds == pytest.approx(1.2)


def test_extract_paddle_text_supports_legacy_and_v3_shapes():
    legacy = [[[[0, 0], [1, 0], [1, 1], [0, 1]], ("谢米出现了！", 0.99)]]
    v3 = [{"rec_texts": ["去吧！", "图图犬！"]}]

    assert "谢米出现了" in _extract_paddle_text(legacy)
    assert "去吧" in _extract_paddle_text(v3)
