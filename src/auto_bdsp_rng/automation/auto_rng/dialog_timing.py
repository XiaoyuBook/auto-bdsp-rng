from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
import re


_PADDLE_OCR: object | None = None


@dataclass(frozen=True)
class DialogTimingResult:
    first_seen_at: float
    second_seen_at: float
    interval_seconds: float


def suggested_shiny_threshold(interval_seconds: float, *, multiplier: float = 1.2) -> float:
    return round(max(0.0, interval_seconds) * multiplier, 3)


def normalize_ocr_text(text: str) -> str:
    """去掉空格和大部分标点，但保留中文叹号 ！ 用于区分关键文本。"""
    # 先保留 ！(U+FF01) 和 !(U+0021)，去掉其余标点
    cleaned = re.sub(r"[^\w！!]+", "", text, flags=re.UNICODE)
    return cleaned


def measure_keyword_interval(
    capture_frame: Callable[[], object],
    read_text: Callable[[object], str],
    *,
    first_keyword: str = "出现了！",
    second_keyword: str = "去吧",
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    should_stop: Callable[[], bool] | None = None,
    timeout_seconds: float = 30.0,
    poll_interval_seconds: float = 0.1,
    script_done: threading.Event | None = None,
    grace_seconds: float = 30.0,
    hard_timeout_seconds: float = 120.0,
) -> DialogTimingResult:
    first = normalize_ocr_text(first_keyword)
    second = normalize_ocr_text(second_keyword)
    started_at = monotonic()
    first_seen_at: float | None = None
    script_ended_at: float | None = None
    while True:
        now = monotonic()
        if script_done is not None:
            # 脚本结束后再给 grace_seconds 检测对话框
            if script_done.is_set():
                if script_ended_at is None:
                    script_ended_at = now
                if now - script_ended_at > grace_seconds:
                    break
            elif now - started_at > hard_timeout_seconds:
                # 脚本运行过久，兜底保护
                break
        elif now - started_at > timeout_seconds:
            break
        if should_stop is not None and should_stop():
            raise RuntimeError("Dialog timing calibration stopped")
        frame = capture_frame()
        text = normalize_ocr_text(read_text(frame))
        if first_seen_at is None:
            if first in text:
                first_seen_at = now
        elif second in text:
            return DialogTimingResult(first_seen_at, now, now - first_seen_at)
        sleep(poll_interval_seconds)
    raise TimeoutError(f"Timed out while waiting for OCR keywords: {first_keyword} -> {second_keyword}")


def read_ocr_text(frame: object) -> str:
    try:
        return read_paddle_ocr_text(frame)
    except RuntimeError as paddle_error:
        try:
            return read_tesseract_ocr_text(frame)
        except RuntimeError as tesseract_error:
            raise RuntimeError(
                "OCR engine is unavailable. Install PaddleOCR with "
                "`python -m pip install .[ocr]`, or install pytesseract plus Tesseract chi_sim. "
                f"PaddleOCR error: {paddle_error}; Tesseract error: {tesseract_error}"
            ) from tesseract_error


def read_paddle_ocr_text(frame: object) -> str:
    global _PADDLE_OCR
    try:
        from paddleocr import PaddleOCR
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("PaddleOCR is not installed") from exc

    image = np.asarray(frame)
    if _PADDLE_OCR is None:
        _PADDLE_OCR = _create_paddle_ocr(PaddleOCR)
    ocr = _PADDLE_OCR
    predict = getattr(ocr, "predict", None)
    if callable(predict):
        return _extract_paddle_text(predict(image))
    legacy_ocr = getattr(ocr, "ocr", None)
    if callable(legacy_ocr):
        try:
            return _extract_paddle_text(legacy_ocr(image, cls=False))
        except TypeError:
            return _extract_paddle_text(legacy_ocr(image))
    raise RuntimeError("PaddleOCR object does not expose a supported OCR method")


def _create_paddle_ocr(factory: Callable[..., object]) -> object:
    attempts = (
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
