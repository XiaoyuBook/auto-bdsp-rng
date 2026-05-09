from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class DialogTimingResult:
    first_seen_at: float
    second_seen_at: float
    interval_seconds: float


def suggested_shiny_threshold(interval_seconds: float, *, multiplier: float = 1.2) -> float:
    return round(max(0.0, interval_seconds) * multiplier, 3)


def measure_dialog_interval(
    capture_frame: Callable[[], object],
    *,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    should_stop: Callable[[], bool] | None = None,
    timeout_seconds: float = 30.0,
    poll_interval_seconds: float = 0.03,
    detector: Callable[[object], bool] | None = None,
) -> DialogTimingResult:
    """Measure the gap between two BDSP bottom-dialog appearances.

    The default detector tracks the dialog box appearance instead of a Pokemon
    name, because the stable markers are the two dialog events while names vary
    between targets.
    """

    detect = detector or detect_bdsp_dialog_box
    started_at = monotonic()
    first_seen_at: float | None = None
    waiting_for_gap = False
    saw_gap = False
    previous_visible = False
    while monotonic() - started_at <= timeout_seconds:
        if should_stop is not None and should_stop():
            raise RuntimeError("Dialog timing calibration stopped")
        visible = detect(capture_frame())
        now = monotonic()
        if first_seen_at is None:
            if visible and not previous_visible:
                first_seen_at = now
                waiting_for_gap = True
        elif waiting_for_gap:
            if not visible:
                saw_gap = True
            elif saw_gap and not previous_visible:
                interval = now - first_seen_at
                return DialogTimingResult(first_seen_at, now, interval)
        previous_visible = visible
        sleep(poll_interval_seconds)
    raise TimeoutError("Timed out while waiting for the two dialog events")


def detect_bdsp_dialog_box(frame: object) -> bool:
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("OpenCV and NumPy are required for dialog timing detection") from exc

    image = np.asarray(frame)
    if image.ndim < 3 or image.shape[0] < 10 or image.shape[1] < 10:
        return False
    height, width = image.shape[:2]
    bottom = image[int(height * 0.70) :, :]
    hsv = cv2.cvtColor(bottom, cv2.COLOR_BGR2HSV)
    value = hsv[:, :, 2]
    saturation = hsv[:, :, 1]
    white = (value > 210) & (saturation < 65)
    ratio = float(np.count_nonzero(white)) / float(bottom.shape[0] * bottom.shape[1])
    return ratio > 0.18
