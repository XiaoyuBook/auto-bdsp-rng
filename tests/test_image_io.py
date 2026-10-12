from pathlib import Path

import numpy as np
import pytest

from auto_bdsp_rng import image_io
from auto_bdsp_rng.automation.auto_rng.pokemon_info_ocr import _load_image


@pytest.mark.parametrize("shape", [(7, 9), (7, 9, 3)])
def test_images_round_trip_through_chinese_paths(tmp_path: Path, shape: tuple[int, ...]) -> None:
    cv2 = pytest.importorskip("cv2")
    image = np.arange(np.prod(shape), dtype=np.uint8).reshape(shape)
    path = tmp_path / "中文安装目录 with spaces" / "眼睛模板.png"

    assert image_io.write_image(path, image) == path
    loaded = image_io.read_image(path, cv2.IMREAD_UNCHANGED)

    assert np.array_equal(loaded, image)
    assert not list(path.parent.glob(".image-*.tmp"))
    if image.ndim == 3:
        assert np.array_equal(_load_image(path), image)


def test_failed_image_replace_preserves_existing_template(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "眼睛.png"
    original = np.zeros((3, 4), dtype=np.uint8)
    image_io.write_image(path, original)
    original_bytes = path.read_bytes()

    def fail_replace(*_args):
        raise PermissionError("destination is locked")

    monkeypatch.setattr(image_io.os, "replace", fail_replace)
    with pytest.raises(PermissionError, match="locked"):
        image_io.write_image(path, np.full((3, 4), 255, dtype=np.uint8))

    assert path.read_bytes() == original_bytes
    assert not list(tmp_path.glob(".image-*.tmp"))


@pytest.mark.parametrize("payload", [b"", b"not an image"])
def test_invalid_image_reports_decode_failure(tmp_path: Path, payload: bytes) -> None:
    path = tmp_path / "损坏.png"
    path.write_bytes(payload)
    with pytest.raises(OSError, match="Cannot decode"):
        image_io.read_image(path)
