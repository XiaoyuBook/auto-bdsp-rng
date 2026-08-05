from __future__ import annotations

import pytest

from auto_bdsp_rng.automation.auto_rng.zoom_recovery import (
    ZOOM_EXIT_SCRIPT,
    ZOOM_EXIT_SCRIPT_NAME,
    contains_zoom_overlay_text,
    detect_zoom_overlay,
    recover_zoom_overlay,
)


@pytest.mark.parametrize(
    "text",
    ("缩 放\n退出", "縮放", "锁定", "鎖定", "重新调整", "重新調整"),
)
def test_contains_zoom_overlay_text_accepts_every_zoom_overlay_label(text):
    assert contains_zoom_overlay_text(text)


def test_contains_zoom_overlay_text_ignores_normal_game_text():
    assert not contains_zoom_overlay_text("背包をとじます")


def test_detect_zoom_overlay_reads_the_full_frame():
    frame = object()
    seen_frames = []

    assert detect_zoom_overlay(
        frame,
        read_text=lambda captured: seen_frames.append(captured) or "缩放",
    )

    assert seen_frames == [frame]


def test_recover_zoom_overlay_only_runs_after_two_positive_checks():
    frames = iter(("zoom-1", "zoom-2", "clear-1", "clear-2"))
    scripts = []
    sleeps = []

    recovered = recover_zoom_overlay(
        lambda: next(frames),
        lambda text, name: scripts.append((text, name)),
        detect_overlay=lambda frame: str(frame).startswith("zoom"),
        sleep=sleeps.append,
    )

    assert recovered is True
    assert scripts == [(ZOOM_EXIT_SCRIPT, ZOOM_EXIT_SCRIPT_NAME)]
    assert sleeps == [0.1, 0.1]


def test_recover_zoom_overlay_does_not_send_home_without_the_overlay():
    scripts = []

    recovered = recover_zoom_overlay(
        lambda: "normal",
        lambda text, name: scripts.append((text, name)),
        detect_overlay=lambda _frame: False,
    )

    assert recovered is False
    assert scripts == []


def test_recover_zoom_overlay_does_not_send_home_when_stopped_during_confirmation_wait():
    stopped = False
    scripts = []

    def stop_during_wait(_seconds: float) -> None:
        nonlocal stopped
        stopped = True

    recovered = recover_zoom_overlay(
        lambda: "zoom",
        lambda text, name: scripts.append((text, name)),
        detect_overlay=lambda _frame: True,
        sleep=stop_during_wait,
        should_stop=lambda: stopped,
    )

    assert recovered is False
    assert scripts == []


def test_recover_zoom_overlay_fails_closed_when_overlay_remains():
    with pytest.raises(RuntimeError, match="仍检测到缩放模式"):
        recover_zoom_overlay(
            lambda: "zoom",
            lambda _text, _name: None,
            detect_overlay=lambda _frame: True,
            sleep=lambda _seconds: None,
        )
