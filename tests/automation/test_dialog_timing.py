from __future__ import annotations

import threading

import numpy as np
import pytest

from auto_bdsp_rng.automation.auto_rng import dialog_timing
from auto_bdsp_rng.automation.auto_rng.dialog_timing import (
    DialogFrameCaptureError,
    DialogKeywordTimeoutError,
    DialogOcrError,
    DialogScriptTimeoutError,
    DialogTimingCancelledError,
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


class _FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _timed_reader(
    clock: _FakeClock,
    texts: list[str],
    durations: list[float] | None = None,
):
    text_iter = iter(texts)
    duration_iter = iter(durations or [0.0] * len(texts))

    def read_text(_frame: object) -> str:
        clock.advance(next(duration_iter))
        return next(text_iter)

    return read_text


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


def test_normalize_ocr_text_canonicalizes_exclamation_for_shiny_keyword():
    assert normalize_ocr_text("谢 米 出 现 了 !") == "谢米出现了！"
    assert normalize_ocr_text("谢 米 出 现 了 ！") == "谢米出现了！"
    assert normalize_ocr_text("謝 米 出 現 了 !") == "謝米出现了！"
    assert normalize_ocr_text("謝 米 出 現 了 ！") == "謝米出现了！"
    assert normalize_ocr_text("去吧！ 图图犬！") == "去吧！图图犬！"


def test_measure_keyword_interval_uses_ocr_keywords_in_order():
    clock = _FakeClock(10.0)
    events: list[tuple[str, float, float | None]] = []
    structured_events = []

    result = measure_keyword_interval(
        object,
        _timed_reader(clock, ["菜单", "谢米出现了！", "去吧！图图犬！"], [0.02, 0.02, 0.02]),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        timeout_seconds=5.0,
        poll_interval_seconds=0.1,
        debug_callback=lambda event, elapsed, interval: events.append((event, elapsed, interval)),
        event_callback=structured_events.append,
    )

    assert result.first_seen_at == pytest.approx(10.12)
    assert result.second_seen_at == pytest.approx(10.22)
    assert result.interval_seconds == pytest.approx(0.1)
    assert events == [
        ("first_seen", pytest.approx(0.12), None),
        ("second_seen", pytest.approx(0.22), pytest.approx(0.1)),
    ]
    assert tuple(structured_events) == result.events
    assert [event.event for event in result.events] == ["monitor_started", "first_seen", "second_seen"]
    assert result.events[0].observed_at == pytest.approx(10.0)
    assert result.events[0].elapsed_seconds == pytest.approx(0.0)
    assert result.events[1].keyword == "出现了！"
    assert result.events[2].keyword == "去吧"


def test_measure_keyword_interval_ignores_second_keyword_before_first_keyword():
    clock = _FakeClock()

    result = measure_keyword_interval(
        object,
        _timed_reader(clock, ["去吧！", "菜单", "谢米出现了！", "去吧！图图犬！"]),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        timeout_seconds=5.0,
        poll_interval_seconds=0.1,
    )

    assert result.interval_seconds == pytest.approx(0.1)
    assert result.events[-1].keyword == "去吧"


def test_measure_keyword_interval_accepts_shangba_as_second_keyword():
    clock = _FakeClock()

    result = measure_keyword_interval(
        object,
        _timed_reader(clock, ["菜单", "谢米出现了！", "上吧！图图犬！"]),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        timeout_seconds=5.0,
        poll_interval_seconds=0.1,
    )

    assert result.interval_seconds == pytest.approx(0.1)
    assert result.events[-1].keyword == "上吧"


@pytest.mark.parametrize("battle_text", ["战斗", "戰鬥"])
def test_measure_keyword_interval_switches_callbacks_after_shangba(battle_text):
    clock = _FakeClock()
    first_frames = iter(["dialog-before", "dialog-shangba"])
    second_frames = iter(["battle-hud"])
    calls: list[tuple[str, object]] = []

    def capture_first():
        frame = next(first_frames)
        calls.append(("capture:first", frame))
        return frame

    def read_first(frame):
        calls.append(("read:first", frame))
        return "战斗" if frame == "dialog-before" else "上吧！草苗龟！"

    def capture_second():
        frame = next(second_frames)
        calls.append(("capture:second", frame))
        return frame

    def read_second(frame):
        calls.append(("read:second", frame))
        return battle_text

    result = measure_keyword_interval(
        capture_first,
        read_first,
        first_keyword="上吧",
        second_keyword=("战斗", "戰鬥"),
        second_capture_frame=capture_second,
        second_read_text=read_second,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        timeout_seconds=1.0,
        poll_interval_seconds=0.1,
    )

    assert result.interval_seconds == pytest.approx(0.1)
    assert result.events[1].keyword == "上吧"
    assert result.events[2].keyword == battle_text
    assert calls == [
        ("capture:first", "dialog-before"),
        ("read:first", "dialog-before"),
        ("capture:first", "dialog-shangba"),
        ("read:first", "dialog-shangba"),
        ("capture:second", "battle-hud"),
        ("read:second", "battle-hud"),
    ]


def test_measure_keyword_interval_defaults_to_primary_callbacks_for_both_stages():
    clock = _FakeClock()
    frames = iter(["appeared-frame", "go-frame"])
    texts = {
        "appeared-frame": "谢米出现了！",
        "go-frame": "去吧！图图犬！",
    }
    calls: list[tuple[str, object]] = []

    def capture():
        frame = next(frames)
        calls.append(("capture", frame))
        return frame

    def read(frame):
        calls.append(("read", frame))
        return texts[frame]

    result = measure_keyword_interval(
        capture,
        read,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        timeout_seconds=1.0,
        poll_interval_seconds=0.1,
    )

    assert result.interval_seconds == pytest.approx(0.1)
    assert result.events[-1].keyword == "去吧"
    assert calls == [
        ("capture", "appeared-frame"),
        ("read", "appeared-frame"),
        ("capture", "go-frame"),
        ("read", "go-frame"),
    ]


@pytest.mark.parametrize("first_text", ["谢米出现了!", "谢米出现了！"])
def test_measure_keyword_interval_accepts_halfwidth_and_fullwidth_exclamation(first_text):
    clock = _FakeClock()

    result = measure_keyword_interval(
        object,
        _timed_reader(clock, [first_text, "去吧！图图犬！"]),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        timeout_seconds=1.0,
    )

    assert result.interval_seconds == pytest.approx(0.1)


def test_measure_keyword_interval_accepts_traditional_first_keyword_in_order():
    clock = _FakeClock()

    result = measure_keyword_interval(
        object,
        _timed_reader(clock, ["選單", "謝米出現了！", "去吧！圖圖犬！"]),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        timeout_seconds=1.0,
    )

    assert result.interval_seconds == pytest.approx(0.1)
    assert result.events[1].keyword == "出现了！"
    assert result.events[2].keyword == "去吧"


def test_measure_keyword_interval_gives_second_keyword_an_independent_window():
    done = threading.Event()
    done.set()
    clock = _FakeClock()

    result = measure_keyword_interval(
        object,
        _timed_reader(clock, ["谢米出现了！", "去吧！图图犬！"], [29.9, 29.9]),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        timeout_seconds=30.0,
        poll_interval_seconds=0.1,
        script_done=done,
        grace_seconds=30.0,
    )

    assert result.first_seen_at == pytest.approx(29.9)
    assert result.second_seen_at == pytest.approx(59.8)
    assert result.interval_seconds == pytest.approx(29.9)


@pytest.mark.parametrize("first_text", ["谢米出现了", "謝米出現了"])
def test_measure_keyword_interval_requires_exclamation_for_first_keyword(first_text):
    clock = _FakeClock()

    with pytest.raises(DialogKeywordTimeoutError) as exc_info:
        measure_keyword_interval(
            object,
            lambda _frame: first_text,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
            timeout_seconds=0.3,
            poll_interval_seconds=0.1,
        )

    assert exc_info.value.stage == "before_first"
    assert exc_info.value.event.event == "timeout_before_first"
    assert exc_info.value.elapsed_seconds == pytest.approx(0.3)
    assert exc_info.value.events[-1] == exc_info.value.event


def test_measure_keyword_interval_distinguishes_script_running_hard_timeout():
    clock = _FakeClock()
    done = threading.Event()

    with pytest.raises(DialogScriptTimeoutError) as exc_info:
        measure_keyword_interval(
            object,
            lambda _frame: "菜单",
            monotonic=clock.monotonic,
            sleep=clock.sleep,
            script_done=done,
            hard_timeout_seconds=0.3,
        )

    assert exc_info.value.stage == "script_running"
    assert exc_info.value.event.event == "script_timeout"
    assert exc_info.value.elapsed_seconds == pytest.approx(0.3)
    assert [event.event for event in exc_info.value.events] == ["monitor_started", "script_timeout"]


def test_measure_keyword_interval_uses_before_first_after_script_finishes():
    clock = _FakeClock()
    done = threading.Event()
    done.set()

    with pytest.raises(DialogKeywordTimeoutError) as exc_info:
        measure_keyword_interval(
            object,
            lambda _frame: "菜单",
            monotonic=clock.monotonic,
            sleep=clock.sleep,
            script_done=done,
            grace_seconds=0.3,
        )

    assert exc_info.value.stage == "before_first"
    assert exc_info.value.event.event == "timeout_before_first"


def test_measure_keyword_interval_reports_independent_timeout_after_first_keyword():
    done = threading.Event()
    done.set()
    clock = _FakeClock()
    events: list[tuple[str, float, float | None]] = []

    with pytest.raises(DialogKeywordTimeoutError) as exc_info:
        measure_keyword_interval(
            object,
            _timed_reader(clock, ["谢米出现了！", "去吧！图图犬！"], [29.9, 30.0]),
            monotonic=clock.monotonic,
            sleep=clock.sleep,
            timeout_seconds=30.0,
            poll_interval_seconds=0.1,
            script_done=done,
            grace_seconds=30.0,
            debug_callback=lambda event, elapsed, interval: events.append((event, elapsed, interval)),
        )

    assert exc_info.value.stage == "after_first"
    assert exc_info.value.event.event == "timeout_after_first"
    assert exc_info.value.interval_seconds == pytest.approx(30.0)
    assert exc_info.value.events[-1] == exc_info.value.event
    assert events == [
        ("first_seen", pytest.approx(29.9), None),
        ("timeout_after_first", pytest.approx(59.9), pytest.approx(30.0)),
    ]


def test_measure_keyword_interval_deducts_capture_and_ocr_time_from_poll_period():
    clock = _FakeClock()

    result = measure_keyword_interval(
        object,
        _timed_reader(clock, ["菜单", "谢米出现了！", "去吧！"], [0.04, 0.15, 0.02]),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        timeout_seconds=2.0,
        poll_interval_seconds=0.1,
    )

    assert clock.sleeps == [pytest.approx(0.06)]
    assert result.first_seen_at == pytest.approx(0.25)
    assert result.second_seen_at == pytest.approx(0.27)


def test_measure_keyword_interval_distinguishes_cancellation():
    captured = []

    with pytest.raises(DialogTimingCancelledError):
        measure_keyword_interval(
            lambda: captured.append(True),
            lambda _frame: "",
            should_stop=lambda: True,
        )

    assert captured == []


def test_measure_keyword_interval_wraps_capture_errors():
    source_error = OSError("camera unavailable")

    with pytest.raises(DialogFrameCaptureError, match="camera unavailable") as exc_info:
        measure_keyword_interval(
            lambda: (_ for _ in ()).throw(source_error),
            lambda _frame: "",
        )

    assert exc_info.value.__cause__ is source_error


def test_measure_keyword_interval_wraps_ocr_errors():
    source_error = RuntimeError("paddle failed")

    with pytest.raises(DialogOcrError, match="paddle failed") as exc_info:
        measure_keyword_interval(
            object,
            lambda _frame: (_ for _ in ()).throw(source_error),
        )

    assert exc_info.value.__cause__ is source_error


def test_read_ocr_text_propagates_paddle_failure_without_tesseract_fallback(monkeypatch):
    source_error = RuntimeError("paddle failed")
    fallback_calls = []
    monkeypatch.setattr(dialog_timing, "read_paddle_ocr_text", lambda _frame: (_ for _ in ()).throw(source_error))
    monkeypatch.setattr(
        dialog_timing,
        "read_tesseract_ocr_text",
        lambda _frame: fallback_calls.append(True) or "fallback",
    )

    with pytest.raises(RuntimeError) as exc_info:
        dialog_timing.read_ocr_text(object())

    assert exc_info.value is source_error
    assert fallback_calls == []


def test_extract_paddle_text_supports_legacy_and_v3_shapes():
    legacy = [[[[0, 0], [1, 0], [1, 1], [0, 1]], ("谢米出现了！", 0.99)]]
    v3 = [{"rec_texts": ["去吧！", "图图犬！"]}]

    assert "谢米出现了" in _extract_paddle_text(legacy)
    assert "去吧" in _extract_paddle_text(v3)
