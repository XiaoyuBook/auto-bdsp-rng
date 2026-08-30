from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
import re

from auto_bdsp_rng.automation.auto_rng.ocr_runtime import optimized_paddle_ocr_kwargs
from auto_bdsp_rng.automation.auto_rng.pokemon_info_ocr import run_paddle_ocr


@dataclass(frozen=True)
class DialogTimingEvent:
    event: str
    observed_at: float
    elapsed_seconds: float
    interval_seconds: float | None = None
    keyword: str | None = None


@dataclass(frozen=True)
class DialogTimingResult:
    first_seen_at: float
    second_seen_at: float
    interval_seconds: float
    events: tuple[DialogTimingEvent, ...] = ()


class DialogTimingCancelledError(RuntimeError):
    """Raised when dialog monitoring is cancelled cooperatively."""


class DialogFrameCaptureError(RuntimeError):
    """Raised when the capture source cannot provide the next frame."""


class DialogOcrError(RuntimeError):
    """Raised when PaddleOCR cannot process a captured frame."""


class DialogTimingTimeoutError(TimeoutError):
    """Base timeout carrying the structured event history for diagnostics."""

    def __init__(
        self,
        message: str,
        *,
        stage: str,
        event: DialogTimingEvent,
        events: tuple[DialogTimingEvent, ...],
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.event = event
        self.events = events
        self.elapsed_seconds = event.elapsed_seconds
        self.interval_seconds = event.interval_seconds


class DialogKeywordTimeoutError(DialogTimingTimeoutError):
    """Raised when either keyword monitoring phase reaches its deadline."""


class DialogScriptTimeoutError(DialogTimingTimeoutError):
    """Raised when the controller script exceeds its hard runtime deadline."""


def suggested_shiny_threshold(interval_seconds: float, *, multiplier: float = 1.2) -> float:
    return round(max(0.0, interval_seconds) * multiplier, 3)


def normalize_ocr_text(text: str) -> str:
    """去掉空格和噪声，并归一化判闪关键词使用的字符。"""
    cleaned = re.sub(r"[^\w！!]+", "", text, flags=re.UNICODE)
    cleaned = cleaned.replace("!", "！")
    return cleaned.replace("出現了！", "出现了！")


def measure_keyword_interval(
    capture_frame: Callable[[], object],
    read_text: Callable[[object], str],
    *,
    first_keyword: str = "出现了！",
    second_keyword: str | Sequence[str] = ("去吧", "上吧"),
    second_capture_frame: Callable[[], object] | None = None,
    second_read_text: Callable[[object], str] | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    should_stop: Callable[[], bool] | None = None,
    timeout_seconds: float = 30.0,
    poll_interval_seconds: float = 0.1,
    script_done: threading.Event | None = None,
    grace_seconds: float = 30.0,
    hard_timeout_seconds: float = 120.0,
    debug_callback: Callable[[str, float, float | None], None] | None = None,
    event_callback: Callable[[DialogTimingEvent], None] | None = None,
) -> DialogTimingResult:
    """Measure ordered text events, optionally switching sources after the first.

    The second-stage callbacks take effect on the iteration after the first
    keyword is observed. Omitting either callback keeps using its first-stage
    counterpart.
    """
    first = normalize_ocr_text(first_keyword)
    second_keywords = (second_keyword,) if isinstance(second_keyword, str) else tuple(second_keyword)
    seconds = tuple(normalize_ocr_text(keyword) for keyword in second_keywords)
    started_at = monotonic()
    first_seen_at: float | None = None
    script_ended_at: float | None = None
    events: list[DialogTimingEvent] = []

    def record_event(
        event: str,
        observed_at: float,
        interval: float | None = None,
        *,
        keyword: str | None = None,
        emit_debug: bool = True,
    ) -> DialogTimingEvent:
        item = DialogTimingEvent(event, observed_at, observed_at - started_at, interval, keyword)
        events.append(item)
        if event_callback is not None:
            event_callback(item)
        if emit_debug and debug_callback is not None:
            debug_callback(event, item.elapsed_seconds, interval)
        return item

    record_event("monitor_started", started_at, emit_debug=False)

    def check_stopped(now: float) -> None:
        if should_stop is not None and should_stop():
            record_event("cancelled", now, emit_debug=False)
            raise DialogTimingCancelledError("Dialog timing calibration stopped")

    def update_script_deadline(now: float) -> None:
        nonlocal script_ended_at
        if script_done is not None and script_done.is_set() and script_ended_at is None:
            script_ended_at = now

    def check_timeout(now: float) -> None:
        if first_seen_at is not None:
            interval = now - first_seen_at
            if interval >= timeout_seconds:
                timeout_event = record_event("timeout_after_first", now, interval)
                second_label = "/".join(second_keywords)
                raise DialogKeywordTimeoutError(
                    f"Timed out while waiting for second OCR keyword: {second_label}",
                    stage="after_first",
                    event=timeout_event,
                    events=tuple(events),
                )
            return
        if script_done is None:
            timed_out = now - started_at >= timeout_seconds
        elif script_ended_at is not None:
            timed_out = now - script_ended_at >= grace_seconds
        else:
            if now - started_at >= hard_timeout_seconds:
                timeout_event = record_event("script_timeout", now, emit_debug=False)
                raise DialogScriptTimeoutError(
                    "Controller script did not finish before the dialog monitoring hard timeout",
                    stage="script_running",
                    event=timeout_event,
                    events=tuple(events),
                )
            return
        if timed_out:
            timeout_event = record_event("timeout_before_first", now)
            raise DialogKeywordTimeoutError(
                f"Timed out while waiting for first OCR keyword: {first_keyword}",
                stage="before_first",
                event=timeout_event,
                events=tuple(events),
            )

    while True:
        iteration_started_at = monotonic()
        check_stopped(iteration_started_at)
        update_script_deadline(iteration_started_at)
        check_timeout(iteration_started_at)
        capture_current = (
            second_capture_frame
            if first_seen_at is not None and second_capture_frame is not None
            else capture_frame
        )
        read_current = (
            second_read_text
            if first_seen_at is not None and second_read_text is not None
            else read_text
        )
        try:
            frame = capture_current()
        except Exception as exc:
            failed_at = monotonic()
            record_event("capture_error", failed_at, emit_debug=False)
            raise DialogFrameCaptureError(f"Dialog frame capture failed: {exc}") from exc
        check_stopped(monotonic())
        try:
            raw_text = read_current(frame)
        except Exception as exc:
            failed_at = monotonic()
            record_event("ocr_error", failed_at, emit_debug=False)
            raise DialogOcrError(f"Dialog OCR inference failed: {exc}") from exc
        ocr_completed_at = monotonic()
        check_stopped(ocr_completed_at)
        update_script_deadline(ocr_completed_at)
        check_timeout(ocr_completed_at)
        text = normalize_ocr_text(raw_text)
        if first_seen_at is None:
            if first in text:
                first_seen_at = ocr_completed_at
                record_event("first_seen", ocr_completed_at, keyword=first_keyword)
        else:
            matched_keyword = next(
                (keyword for keyword, normalized in zip(second_keywords, seconds) if normalized in text),
                None,
            )
            if matched_keyword is not None:
                interval = ocr_completed_at - first_seen_at
                record_event("second_seen", ocr_completed_at, interval, keyword=matched_keyword)
                return DialogTimingResult(first_seen_at, ocr_completed_at, interval, tuple(events))
        sleep_seconds = poll_interval_seconds - (ocr_completed_at - iteration_started_at)
        if sleep_seconds > 0:
            sleep(sleep_seconds)


def read_ocr_text(frame: object) -> str:
    """Read text with the shared PaddleOCR runtime and propagate its failures."""
    return read_paddle_ocr_text(frame)


def read_paddle_ocr_text(frame: object) -> str:
    try:
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("NumPy is not installed") from exc

    image = np.asarray(frame)
    return _extract_paddle_text(run_paddle_ocr(image))


def _create_paddle_ocr(factory: Callable[..., object]) -> object:
    preferred = optimized_paddle_ocr_kwargs()
    attempts = (preferred,)
    if "text_detection_model_dir" not in preferred:
        attempts += (
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


def _extract_paddle_text(result: object) -> str:
    parts: list[str] = []

    def visit(value: object) -> None:
        if value is None:
            return
        if isinstance(value, str):
            parts.append(value)
            return
        if isinstance(value, dict):
            for key in ("rec_text", "text", "transcription"):
                text = value.get(key)
                if isinstance(text, str):
                    parts.append(text)
            for key in ("rec_texts", "texts"):
                texts = value.get(key)
                if isinstance(texts, list | tuple):
                    for text in texts:
                        if isinstance(text, str):
                            parts.append(text)
            for item in value.values():
                if isinstance(item, list | tuple | dict):
                    visit(item)
            return
        if isinstance(value, list | tuple):
            if len(value) >= 2 and isinstance(value[1], tuple | list) and value[1] and isinstance(value[1][0], str):
                parts.append(value[1][0])
            else:
                for item in value:
                    visit(item)

    visit(result)
    return "\n".join(parts)


def read_tesseract_ocr_text(frame: object) -> str:
    """Explicit opt-in compatibility helper; automatic OCR never falls back to it."""
    try:
        import pytesseract
        from PIL import Image
        import cv2
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("OCR requires pytesseract, Pillow, OpenCV, and NumPy") from exc

    image = np.asarray(frame)
    if image.ndim == 3:
        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    pil_image = Image.fromarray(image)
    return str(pytesseract.image_to_string(pil_image, lang="chi_sim"))


def measure_dialog_interval(
    capture_frame: Callable[[], object],
    *,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    should_stop: Callable[[], bool] | None = None,
    timeout_seconds: float = 30.0,
    poll_interval_seconds: float = 0.03,
    stable_clear_seconds: float = 0.35,
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
    clear_since: float | None = None
    armed = False
    saw_gap = False
    previous_visible = False
    while True:
        now = monotonic()
        if now - started_at > timeout_seconds:
            break
        if should_stop is not None and should_stop():
            raise RuntimeError("Dialog timing calibration stopped")
        visible = detect(capture_frame())
        if not armed:
            if visible:
                clear_since = None
                previous_visible = visible
                sleep(poll_interval_seconds)
                continue
            if clear_since is None:
                clear_since = now
                previous_visible = visible
                sleep(poll_interval_seconds)
                continue
            if now - clear_since >= stable_clear_seconds:
                armed = True
            previous_visible = visible
            sleep(poll_interval_seconds)
            continue
        if first_seen_at is None:
            if visible and not previous_visible:
                first_seen_at = now
        else:
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
    bottom_top = int(height * 0.66)
    bottom = image[bottom_top:, :]
    hsv = cv2.cvtColor(bottom, cv2.COLOR_BGR2HSV)
    value = hsv[:, :, 2]
    saturation = hsv[:, :, 1]
    white = ((value > 210) & (saturation < 70)).astype("uint8") * 255
    kernel = np.ones((5, 15), dtype=np.uint8)
    white = cv2.morphologyEx(white, cv2.MORPH_CLOSE, kernel)
    contours, _hierarchy = cv2.findContours(white, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for contour in contours:
        x, y, box_width, box_height = cv2.boundingRect(contour)
        area = cv2.contourArea(contour)
        if box_width < width * 0.55:
            continue
        if box_height < height * 0.055 or box_height > height * 0.20:
            continue
        if area < width * height * 0.045:
            continue
        center_y = bottom_top + y + box_height / 2
        if not (height * 0.68 <= center_y <= height * 0.90):
            continue
        margin = max(2, int(height * 0.006))
        global_y = bottom_top + y
        top_line = image[max(0, global_y - margin) : global_y, x : x + box_width]
        bottom_line = image[
            min(height, global_y + box_height) : min(height, global_y + box_height + margin),
            x : x + box_width,
        ]
        if top_line.size == 0 or bottom_line.size == 0:
            continue
        top_dark = float(np.mean(np.all(top_line < 90, axis=2)))
        bottom_dark = float(np.mean(np.all(bottom_line < 90, axis=2)))
        if max(top_dark, bottom_dark) < 0.20:
            continue
        return True
    return False
