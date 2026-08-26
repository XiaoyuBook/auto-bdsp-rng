from __future__ import annotations

import base64
import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import Any

import cv2
import numpy as np


class ImageLabelError(RuntimeError):
    """Raised when an EasyCon image label cannot be loaded or evaluated."""


class SearchMethod(IntEnum):
    SQ_DIFF = 0
    SQ_DIFF_NORMED = 1
    C_CORR = 2
    C_CORR_NORMED = 3
    C_COEFF = 4
    C_COEFF_NORMED = 5
    STRICT_MATCH = 6
    STRICT_MATCH_RANDOM = 7
    OPACITY_DIFF = 8
    SIMILAR_MATCH = 9
    EDGE_DETECT_XY = 11
    EDGE_DETECT_LAPLACIAN = 12
    EDGE_DETECT_CANNY = 13
    TESSER_DETECT = 107

    @property
    def is_image_method(self) -> bool:
        return int(self) <= int(SearchMethod.EDGE_DETECT_LAPLACIAN)


@dataclass(frozen=True, slots=True)
class ImageSearchResult:
    label_name: str
    score: float
    location: tuple[int, int]
    range_rect: tuple[int, int, int, int]
    match_rect: tuple[int, int, int, int]
    recognized_text: str | None = None

    @property
    def script_value(self) -> int:
        # C# casts double to int by truncating towards zero.
        return int(self.score)


OcrReader = Callable[[np.ndarray], tuple[str, float]]


def string_match_simple(left: str, right: str) -> float:
    """Port of EasyCon Capture's Levenshtein similarity helper."""

    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0

    previous = list(range(len(right) + 1))
    for left_index, left_char in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_char in enumerate(right, start=1):
            current.append(
                min(
                    previous[right_index] + 1,
                    current[right_index - 1] + 1,
                    previous[right_index - 1] + (left_char != right_char),
                )
            )
        previous = current
    return max(0.0, 1.0 - previous[-1] / max(len(left), len(right)))


def _required_int(raw: Mapping[str, Any], name: str) -> int:
    try:
        return int(raw[name])
    except (KeyError, TypeError, ValueError) as exc:
        raise ImageLabelError(f"Invalid EasyCon .IL field: {name}") from exc


def _validate_rect(
    rect: tuple[int, int, int, int],
    frame: np.ndarray,
    *,
    field_name: str,
) -> tuple[slice, slice]:
    x, y, width, height = rect
    frame_height, frame_width = frame.shape[:2]
    if width <= 0 or height <= 0:
        raise ImageLabelError(f"{field_name} width and height must be positive")
    if x < 0 or y < 0 or x + width > frame_width or y + height > frame_height:
        raise ImageLabelError(
            f"{field_name} {rect} is outside frame {frame_width}x{frame_height}"
        )
    return slice(y, y + height), slice(x, x + width)


def _decode_template(encoded: str, label_name: str) -> np.ndarray:
    try:
        payload = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise ImageLabelError(f"Image label {label_name!r} has invalid base64 data") from exc
    image = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None or image.size == 0:
        raise ImageLabelError(f"Image label {label_name!r} contains an invalid image")
    return image


def _edge_detect(frame: np.ndarray, method: SearchMethod) -> np.ndarray:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    if method is SearchMethod.EDGE_DETECT_XY:
        grad_x = cv2.Sobel(gray, cv2.CV_16S, 1, 0, ksize=-1)
        grad_y = cv2.Sobel(gray, cv2.CV_16S, 0, 1, ksize=-1)
        # OpenCvSharp Mat.ConvertTo(CV_8U) saturates negative values instead of
        # taking their absolute value. Keep that quirk for score compatibility.
        combined = cv2.addWeighted(grad_x, 0.5, grad_y, 0.5, 0)
        return np.clip(combined, 0, 255).astype(np.uint8)
    if method is SearchMethod.EDGE_DETECT_LAPLACIAN:
        blurred = cv2.GaussianBlur(gray, (5, 5), 1.5)
        laplacian = cv2.Laplacian(blurred, cv2.CV_16S, ksize=3)
        absolute = cv2.convertScaleAbs(laplacian)
        _, edges = cv2.threshold(absolute, 30, 255, cv2.THRESH_BINARY)
        return edges
    if method is SearchMethod.EDGE_DETECT_CANNY:
        return cv2.Canny(gray, 50, 200)
    raise ImageLabelError(f"Unsupported EasyCon edge method: {int(method)}")


_CV_TEMPLATE_METHODS = {
    SearchMethod.SQ_DIFF: cv2.TM_SQDIFF,
    SearchMethod.SQ_DIFF_NORMED: cv2.TM_SQDIFF_NORMED,
    SearchMethod.C_CORR: cv2.TM_CCORR,
    SearchMethod.C_CORR_NORMED: cv2.TM_CCORR_NORMED,
    SearchMethod.C_COEFF: cv2.TM_CCOEFF,
    SearchMethod.C_COEFF_NORMED: cv2.TM_CCOEFF_NORMED,
}


def _match_template(
    search_image: np.ndarray,
    template: np.ndarray,
    method: SearchMethod,
) -> tuple[tuple[int, int], float]:
    effective = method
    if method in {SearchMethod.EDGE_DETECT_XY, SearchMethod.EDGE_DETECT_LAPLACIAN}:
        search_image = _edge_detect(search_image, method)
        template = _edge_detect(template, method)
        # EasyCon passes the edge SearchMethod to MatchTemplate, whose default
        # mapping is CCOEFF_NORMED and whose score branch is also CCOEFF.
        cv_method = cv2.TM_CCOEFF_NORMED
    else:
        cv_method = _CV_TEMPLATE_METHODS.get(method)
        if cv_method is None:
            # EasyCon currently exposes only methods 0-5 and 11-12. Its other
            # historical enum values return a zero score rather than running.
            return (-1, -1), 0.0

    if template.shape[0] > search_image.shape[0] or template.shape[1] > search_image.shape[1]:
        raise ImageLabelError("Search image is smaller than the EasyCon template")
    result = cv2.matchTemplate(search_image, template, cv_method)
    min_value, max_value, min_location, max_location = cv2.minMaxLoc(result)
    if effective in {SearchMethod.SQ_DIFF, SearchMethod.SQ_DIFF_NORMED}:
        return tuple(map(int, min_location)), float(1.0 - min_value)
    if effective in {SearchMethod.C_CORR, SearchMethod.C_CORR_NORMED}:
        return tuple(map(int, max_location)), float(max_value)
    return tuple(map(int, max_location)), float((max_value + 1.0) / 2.0)


def _default_tesseract_reader(frame: np.ndarray) -> tuple[str, float]:
    """Run the same bundled Tesseract 5 runtime used by EasyCon."""

    from auto_bdsp_rng.automation.easycon.native.tesseract import (
        TesseractRuntimeError,
        read_tesseract,
    )

    try:
        return read_tesseract(frame)
    except TesseractRuntimeError as exc:
        raise ImageLabelError(str(exc)) from exc


@dataclass(frozen=True, slots=True)
class ImageLabel:
    name: str
    path: Path
    search_method: SearchMethod
    image_base64: str
    range_rect: tuple[int, int, int, int]
    target_rect: tuple[int, int, int, int]

    @classmethod
    def load(cls, path: str | Path) -> "ImageLabel":
        label_path = Path(path)
        try:
            raw = json.loads(label_path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ImageLabelError(f"Cannot load EasyCon image label {label_path}: {exc}") from exc
        if not isinstance(raw, Mapping):
            raise ImageLabelError(f"EasyCon image label must contain a JSON object: {label_path}")
        try:
            method = SearchMethod(_required_int(raw, "searchMethod"))
        except ValueError as exc:
            raise ImageLabelError(f"Unsupported EasyCon search method in {label_path}") from exc
        image_base64 = raw.get("ImgBase64", "")
        if not isinstance(image_base64, str):
            raise ImageLabelError(f"Invalid EasyCon .IL field: ImgBase64")
        if method.is_image_method and not image_base64:
            raise ImageLabelError(f"EasyCon image label has no template: {label_path}")
        return cls(
            name=label_path.stem,
            path=label_path,
            search_method=method,
            image_base64=image_base64,
            range_rect=(
                _required_int(raw, "RangeX"),
                _required_int(raw, "RangeY"),
                _required_int(raw, "RangeWidth"),
                _required_int(raw, "RangeHeight"),
            ),
            target_rect=(
                _required_int(raw, "TargetX"),
                _required_int(raw, "TargetY"),
                _required_int(raw, "TargetWidth"),
                _required_int(raw, "TargetHeight"),
            ),
        )

    def search(
        self,
        frame: np.ndarray,
        *,
        ocr_reader: OcrReader | None = None,
    ) -> ImageSearchResult:
        image = np.asarray(frame)
        if image.dtype != np.uint8 or image.ndim != 3 or image.shape[2] != 3:
            raise ImageLabelError("EasyCon image search requires a uint8 BGR frame")
        if self.target_rect[2] > self.range_rect[2] or self.target_rect[3] > self.range_rect[3]:
            raise ImageLabelError("EasyCon target image is larger than its search range")

        if self.search_method is SearchMethod.TESSER_DETECT:
            target_slices = _validate_rect(self.target_rect, image, field_name="Target")
            target = image[target_slices].copy()
            recognized, confidence = (ocr_reader or _default_tesseract_reader)(target)
            expected = self.image_base64
            score = string_match_simple(recognized.strip(), expected) * float(confidence) * 100.0
            relative = (
                self.target_rect[0] - self.range_rect[0],
                self.target_rect[1] - self.range_rect[1],
            )
            return ImageSearchResult(
                label_name=self.name,
                score=score,
                location=relative,
                range_rect=self.range_rect,
                match_rect=self.target_rect,
                recognized_text=recognized.strip(),
            )

        range_slices = _validate_rect(self.range_rect, image, field_name="Range")
        search_image = image[range_slices]
        template = _decode_template(self.image_base64, self.name)
        location, fraction = _match_template(search_image, template, self.search_method)
        match_rect = (
            self.range_rect[0] + location[0],
            self.range_rect[1] + location[1],
            int(template.shape[1]),
            int(template.shape[0]),
        )
        return ImageSearchResult(
            label_name=self.name,
            score=fraction * 100.0,
            location=location,
            range_rect=self.range_rect,
            match_rect=match_rect,
        )


@dataclass(frozen=True, slots=True)
class ImageLabelCollection:
    labels: Mapping[str, ImageLabel]
    total_files: int
    duplicate_names: int
    failed_files: tuple[Path, ...]

    def external_getters(
        self,
        frame_reader: Callable[[], np.ndarray],
        *,
        ocr_reader: OcrReader | None = None,
        result_callback: Callable[[ImageSearchResult], None] | None = None,
    ) -> dict[str, Callable[[], int]]:
        getters: dict[str, Callable[[], int]] = {}
        for name, label in self.labels.items():
            def read(current: ImageLabel = label) -> int:
                # The broker client returns an owned copy; image-label search
                # never annotates the shared-memory backing frame.
                result = current.search(frame_reader(), ocr_reader=ocr_reader)
                if result_callback is not None:
                    result_callback(result)
                return result.script_value

            getters[name] = read
        return getters


def load_image_labels(roots: Iterable[str | Path]) -> ImageLabelCollection:
    """Load ``ImgLabel/*.IL`` exactly once, with the first root taking priority."""

    labels: dict[str, ImageLabel] = {}
    total = 0
    duplicates = 0
    failures: list[Path] = []
    for root in roots:
        directory = Path(root) / "ImgLabel"
        if not directory.is_dir():
            continue
        try:
            files = sorted(directory.glob("*.IL"), key=lambda item: item.name.casefold())
        except OSError:
            continue
        for path in files:
            total += 1
            try:
                label = ImageLabel.load(path)
            except ImageLabelError:
                failures.append(path)
                continue
            if label.name in labels:
                duplicates += 1
                continue
            labels[label.name] = label
    return ImageLabelCollection(labels, total, duplicates, tuple(failures))


__all__ = [
    "ImageLabel",
    "ImageLabelCollection",
    "ImageLabelError",
    "ImageSearchResult",
    "OcrReader",
    "SearchMethod",
    "load_image_labels",
    "string_match_simple",
]
