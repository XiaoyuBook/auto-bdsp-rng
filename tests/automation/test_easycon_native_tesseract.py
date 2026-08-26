from __future__ import annotations

import ctypes
from pathlib import Path

import numpy as np
import pytest

from auto_bdsp_rng.automation.easycon.native.tesseract import (
    TesseractRuntime,
    TesseractRuntimeError,
)


class _Function:
    def __init__(self, callback):  # type: ignore[no-untyped-def]
        self.callback = callback
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):  # type: ignore[no-untyped-def]
        return self.callback(*args)


class _FakeTesseractLibrary:
    def __init__(self) -> None:
        self.image_bytes = b""
        self.init_path = b""
        self.init_language = b""
        self.page_seg_mode = -1
        self.ended = False
        self.deleted = False
        self.text_deleted = False
        self._text = ctypes.create_string_buffer("皮卡丘\n".encode("utf-8"))

        self.TessBaseAPICreate = _Function(lambda: 42)
        self.TessBaseAPIDelete = _Function(self._delete)
        self.TessBaseAPIEnd = _Function(self._end)
        self.TessBaseAPIInit3 = _Function(self._init)
        self.TessBaseAPISetPageSegMode = _Function(self._set_page_seg_mode)
        self.TessBaseAPISetImage = _Function(self._set_image)
        self.TessBaseAPIGetUTF8Text = _Function(lambda _handle: ctypes.addressof(self._text))
        self.TessBaseAPIMeanTextConf = _Function(lambda _handle: 87)
        self.TessDeleteText = _Function(self._delete_text)

    def _init(self, _handle, path, language):  # type: ignore[no-untyped-def]
        self.init_path = path
        self.init_language = language
        return 0

    def _set_page_seg_mode(self, _handle, mode):  # type: ignore[no-untyped-def]
        self.page_seg_mode = mode

    def _set_image(self, _handle, pixels, width, height, bytes_per_pixel, stride):  # type: ignore[no-untyped-def]
        assert bytes_per_pixel == 3
        assert stride == width * bytes_per_pixel
        self.image_bytes = ctypes.string_at(pixels, height * stride)

    def _end(self, _handle):  # type: ignore[no-untyped-def]
        self.ended = True

    def _delete(self, _handle):  # type: ignore[no-untyped-def]
        self.deleted = True

    def _delete_text(self, _pointer):  # type: ignore[no-untyped-def]
        self.text_deleted = True


def _runtime_root(tmp_path: Path) -> Path:
    root = tmp_path / "runtime"
    tessdata = root / "Tessdata"
    tessdata.mkdir(parents=True)
    (tessdata / "chi_sim.traineddata").write_bytes(b"test")
    return root


def test_ctypes_runtime_uses_single_line_rgb_and_returns_mean_confidence(tmp_path: Path) -> None:
    library = _FakeTesseractLibrary()
    runtime = TesseractRuntime(_runtime_root(tmp_path), library=library)

    text, confidence = runtime.read(np.array([[[10, 20, 30], [40, 50, 60]]], dtype=np.uint8))

    assert text == "皮卡丘"
    assert confidence == pytest.approx(0.87)
    assert library.image_bytes == bytes((30, 20, 10, 60, 50, 40))
    assert library.init_language == b"chi_sim"
    assert library.page_seg_mode == 7
    assert library.ended and library.deleted and library.text_deleted


def test_runtime_rejects_missing_easycon_language_data(tmp_path: Path) -> None:
    with pytest.raises(TesseractRuntimeError, match="语言数据"):
        TesseractRuntime(tmp_path, library=_FakeTesseractLibrary())
