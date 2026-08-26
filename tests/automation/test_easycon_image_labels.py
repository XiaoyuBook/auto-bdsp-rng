from __future__ import annotations

import base64
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from auto_bdsp_rng.automation.easycon.native.image_labels import (
    ImageLabel,
    ImageLabelError,
    SearchMethod,
    load_image_labels,
    string_match_simple,
)


def _encoded_png(image: np.ndarray) -> str:
    ok, payload = cv2.imencode(".png", image)
    assert ok
    return base64.b64encode(payload.tobytes()).decode("ascii")


def _write_label(
    path: Path,
    *,
    method: SearchMethod,
    content: str,
    range_rect: tuple[int, int, int, int] = (2, 3, 12, 10),
    target_rect: tuple[int, int, int, int] = (6, 5, 3, 3),
) -> None:
    rx, ry, rw, rh = range_rect
    tx, ty, tw, th = target_rect
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "name": "ignored-json-name",
                "searchMethod": int(method),
                "ImgBase64": content,
                "RangeX": rx,
                "RangeY": ry,
                "RangeWidth": rw,
                "RangeHeight": rh,
                "TargetX": tx,
                "TargetY": ty,
                "TargetWidth": tw,
                "TargetHeight": th,
                "matchDegree": 101,
            }
        ),
        encoding="utf-8-sig",
    )


def test_template_label_uses_filename_and_easycon_score(tmp_path: Path) -> None:
    rng = np.random.default_rng(20260825)
    frame = rng.integers(0, 256, size=(18, 20, 3), dtype=np.uint8)
    template = frame[7:10, 8:11].copy()
    path = tmp_path / "ImgLabel" / "目标.IL"
    _write_label(
        path,
        method=SearchMethod.C_COEFF_NORMED,
        content=_encoded_png(template),
        range_rect=(2, 3, 12, 10),
        target_rect=(8, 7, 3, 3),
    )

    label = ImageLabel.load(path)
    result = label.search(frame)

    assert label.name == "目标"
    assert result.location == (6, 4)
    assert result.match_rect == (8, 7, 3, 3)
    assert result.score == pytest.approx(100.0, abs=1e-4)
    assert result.script_value == 100


def test_tesser_detect_multiplies_edit_similarity_and_confidence(tmp_path: Path) -> None:
    path = tmp_path / "ImgLabel" / "文字.IL"
    _write_label(
        path,
        method=SearchMethod.TESSER_DETECT,
        content="皮卡丘",
        range_rect=(0, 0, 12, 10),
        target_rect=(2, 3, 6, 4),
    )
    frame = np.zeros((12, 16, 3), dtype=np.uint8)

    result = ImageLabel.load(path).search(
        frame,
        ocr_reader=lambda image: ("皮卡", 0.75),
    )

    assert result.recognized_text == "皮卡"
    assert result.location == (2, 3)
    assert result.score == pytest.approx((2 / 3) * 0.75 * 100.0)
    assert result.script_value == 50


def test_collection_first_root_wins_and_getter_reads_current_frame(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    template = np.full((3, 3, 3), 77, dtype=np.uint8)
    _write_label(
        first / "ImgLabel" / "same.IL",
        method=SearchMethod.SQ_DIFF_NORMED,
        content=_encoded_png(template),
    )
    _write_label(
        second / "ImgLabel" / "same.IL",
        method=SearchMethod.C_CORR_NORMED,
        content=_encoded_png(template),
    )

    collection = load_image_labels((first, second))
    frames = [np.full((18, 20, 3), 77, dtype=np.uint8)]
    seen = []
    getters = collection.external_getters(lambda: frames[-1], result_callback=seen.append)

    assert collection.total_files == 2
    assert collection.duplicate_names == 1
    assert collection.labels["same"].path.parent.parent == first
    assert getters["same"]() == 100
    assert seen[-1].label_name == "same"


def test_label_rejects_out_of_bounds_range(tmp_path: Path) -> None:
    path = tmp_path / "ImgLabel" / "bad.IL"
    template = np.zeros((3, 3, 3), dtype=np.uint8)
    _write_label(
        path,
        method=SearchMethod.C_COEFF_NORMED,
        content=_encoded_png(template),
        range_rect=(10, 10, 12, 10),
    )

    with pytest.raises(ImageLabelError, match="outside frame"):
        ImageLabel.load(path).search(np.zeros((12, 16, 3), dtype=np.uint8))


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ("", "", 1.0),
        ("abc", "", 0.0),
        ("abc", "abc", 1.0),
        ("abc", "axc", 2 / 3),
    ],
)
def test_string_match_matches_easycon(left: str, right: str, expected: float) -> None:
    assert string_match_simple(left, right) == pytest.approx(expected)
