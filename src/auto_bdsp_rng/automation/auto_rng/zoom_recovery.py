"""Detect and safely leave the Nintendo Switch zoom accessibility overlay."""

from __future__ import annotations

import time
from collections.abc import Callable

from auto_bdsp_rng.automation.auto_rng.dialog_timing import read_paddle_ocr_text


ZOOM_EXIT_SCRIPT_NAME = "自动退出放大模式"
ZOOM_EXIT_SCRIPT = """WAIT 2000
HOME DOWN
WAIT 100
HOME UP
WAIT 50
HOME DOWN
WAIT 100
HOME UP
WAIT 1000
"""
ZOOM_OVERLAY_KEYWORDS = ("缩放", "縮放")
ZOOM_CONFIRMATION_DELAY_SECONDS = 0.1


def contains_zoom_overlay_text(text: object) -> bool:
    normalized = "".join(str(text).split())
    return any(keyword in normalized for keyword in ZOOM_OVERLAY_KEYWORDS)


def detect_zoom_overlay(
    frame: object,
    *,
    read_text: Callable[[object], str] = read_paddle_ocr_text,
) -> bool:
    """OCR the complete capture frame; the overlay may be at any position."""
    return contains_zoom_overlay_text(read_text(frame))


def recover_zoom_overlay(
    capture_frame: Callable[[], object],
    run_script_text: Callable[[str, str], object],
    *,
    detect_overlay: Callable[[object], bool] = detect_zoom_overlay,
    sleep: Callable[[float], None] = time.sleep,
    should_stop: Callable[[], bool] | None = None,
) -> bool:
    """Exit Zoom only while the enclosing automation remains active.

    Stop checks bracket every capture, confirmation wait, and HOME dispatch so
    a late stop cannot start a recovery script or overwrite the stopped state.
    """
    is_stopped = should_stop or (lambda: False)
    if is_stopped():
        return False
    first_frame = capture_frame()
    if is_stopped() or not detect_overlay(first_frame):
        return False
    sleep(ZOOM_CONFIRMATION_DELAY_SECONDS)
    if is_stopped():
        return False
    second_frame = capture_frame()
    if is_stopped() or not detect_overlay(second_frame):
        return False
    if is_stopped():
        return False
    run_script_text(ZOOM_EXIT_SCRIPT, ZOOM_EXIT_SCRIPT_NAME)
    if is_stopped():
        return False
    clear_frame = capture_frame()
    if is_stopped():
        return False
    if detect_overlay(clear_frame):
        raise RuntimeError("双 HOME 后仍检测到缩放模式")
    sleep(ZOOM_CONFIRMATION_DELAY_SECONDS)
    if is_stopped():
        return False
    final_frame = capture_frame()
    if is_stopped():
        return False
    if detect_overlay(final_frame):
        raise RuntimeError("双 HOME 后仍检测到缩放模式")
    return True
