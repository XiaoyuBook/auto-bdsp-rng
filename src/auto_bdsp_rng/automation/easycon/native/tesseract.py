"""Bundled Tesseract 5 runtime used by EasyCon ``TesserDetect`` labels."""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import sys
import threading
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from auto_bdsp_rng.resources import resource_path


class TesseractRuntimeError(RuntimeError):
    """Raised when the bundled EasyCon OCR runtime cannot be loaded or used."""


_DLL_NAME = "tesseract50.dll"
_LEPTONICA_DLL_NAME = "leptonica-1.82.0.dll"
_LANGUAGE = "chi_sim"
_PAGE_SEG_MODE_SINGLE_LINE = 7
_runtime_lock = threading.Lock()
_runtime: "TesseractRuntime | None" = None


def _source_runtime_root() -> Path:
    return resource_path("third_party", "EasyCon", "src", "EasyCon.Capture")


def bundled_runtime_root() -> Path:
    """Return the packaged runtime, falling back to EasyCon sources in development."""

    packaged = resource_path("easycon_native")
    if (packaged / "Tessdata" / f"{_LANGUAGE}.traineddata").is_file():
        return packaged
    vendored = resource_path("packaging", "easycon_native")
    if (vendored / "Tessdata" / f"{_LANGUAGE}.traineddata").is_file():
        return vendored
    return _source_runtime_root()


class TesseractRuntime:
    """Small ctypes binding for the Tesseract C API shipped by EasyCon.

    EasyCon creates one engine for each ``TesserDetect`` lookup. We preserve
    that behavior while caching only the loaded DLL. Frames are converted from
    the Broker's BGR24 format to contiguous RGB24 before entering Tesseract.
    """

    def __init__(self, root: str | Path | None = None, *, library: Any | None = None) -> None:
        self.root = Path(root) if root is not None else bundled_runtime_root()
        self.tessdata = self.root / "Tessdata"
        traineddata = self.tessdata / f"{_LANGUAGE}.traineddata"
        if not traineddata.is_file():
            raise TesseractRuntimeError(f"缺少 EasyCon OCR 语言数据: {traineddata}")
        self._dll_directory: Any | None = None
        self._leptonica: Any | None = None
        self._library = library if library is not None else self._load_library()
        self._bind_api()

    def _load_library(self) -> Any:
        if sys.platform == "win32":
            architecture = "x64" if ctypes.sizeof(ctypes.c_void_p) == 8 else "x86"
            dll_dir = self.root / architecture
            tesseract_dll = dll_dir / _DLL_NAME
            leptonica_dll = dll_dir / _LEPTONICA_DLL_NAME
            if not tesseract_dll.is_file() or not leptonica_dll.is_file():
                raise TesseractRuntimeError(f"缺少 EasyCon OCR DLL: {dll_dir}")
            add_directory = getattr(os, "add_dll_directory", None)
            if callable(add_directory):
                self._dll_directory = add_directory(str(dll_dir))
            try:
                # Load Leptonica first so Windows resolves Tesseract's sibling
                # dependency even on hosts with a restrictive DLL policy.
                self._leptonica = ctypes.WinDLL(str(leptonica_dll))
                return ctypes.WinDLL(str(tesseract_dll))
            except OSError as exc:
                raise TesseractRuntimeError(f"无法加载 EasyCon OCR DLL: {exc}") from exc

        library_name = ctypes.util.find_library("tesseract")
        if not library_name:
            raise TesseractRuntimeError("当前系统没有可用的 Tesseract 运行时")
        try:
            return ctypes.CDLL(library_name)
        except OSError as exc:
            raise TesseractRuntimeError(f"无法加载 Tesseract: {exc}") from exc

    def _bind_api(self) -> None:
        api = self._library
        try:
            api.TessBaseAPICreate.argtypes = []
            api.TessBaseAPICreate.restype = ctypes.c_void_p
            api.TessBaseAPIDelete.argtypes = [ctypes.c_void_p]
            api.TessBaseAPIDelete.restype = None
            api.TessBaseAPIEnd.argtypes = [ctypes.c_void_p]
            api.TessBaseAPIEnd.restype = None
            api.TessBaseAPIInit3.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
            api.TessBaseAPIInit3.restype = ctypes.c_int
            api.TessBaseAPISetPageSegMode.argtypes = [ctypes.c_void_p, ctypes.c_int]
            api.TessBaseAPISetPageSegMode.restype = None
            api.TessBaseAPISetImage.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_ubyte),
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
            ]
            api.TessBaseAPISetImage.restype = None
            api.TessBaseAPIGetUTF8Text.argtypes = [ctypes.c_void_p]
            api.TessBaseAPIGetUTF8Text.restype = ctypes.c_void_p
            api.TessBaseAPIMeanTextConf.argtypes = [ctypes.c_void_p]
            api.TessBaseAPIMeanTextConf.restype = ctypes.c_int
            api.TessDeleteText.argtypes = [ctypes.c_void_p]
            api.TessDeleteText.restype = None
        except AttributeError as exc:
            raise TesseractRuntimeError(f"EasyCon OCR DLL 缺少 C API: {exc}") from exc

    def read(self, frame: np.ndarray) -> tuple[str, float]:
        image = np.asarray(frame)
        if image.dtype != np.uint8 or image.ndim != 3 or image.shape[2] != 3:
            raise TesseractRuntimeError("TesserDetect 需要 uint8 BGR 图像")
        rgb = np.ascontiguousarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
        height, width = rgb.shape[:2]
        stride = int(rgb.strides[0])

        api = self._library
        handle = api.TessBaseAPICreate()
        if not handle:
            raise TesseractRuntimeError("无法创建 Tesseract 引擎")
        text_pointer: int | None = None
        try:
            init_result = api.TessBaseAPIInit3(
                handle,
                os.fsencode(self.tessdata),
                _LANGUAGE.encode("ascii"),
            )
            if int(init_result) != 0:
                raise TesseractRuntimeError(f"Tesseract 初始化失败 (code={init_result})")
            api.TessBaseAPISetPageSegMode(handle, _PAGE_SEG_MODE_SINGLE_LINE)
            pixels = rgb.ctypes.data_as(ctypes.POINTER(ctypes.c_ubyte))
            api.TessBaseAPISetImage(handle, pixels, width, height, 3, stride)
            text_pointer = api.TessBaseAPIGetUTF8Text(handle)
            if not text_pointer:
                raise TesseractRuntimeError("Tesseract 没有返回文本")
            raw = ctypes.string_at(text_pointer)
            confidence = max(0.0, min(1.0, int(api.TessBaseAPIMeanTextConf(handle)) / 100.0))
            return raw.decode("utf-8", errors="replace").strip(), confidence
        except TesseractRuntimeError:
            raise
        except Exception as exc:
            raise TesseractRuntimeError(f"TesserDetect 执行失败: {exc}") from exc
        finally:
            if text_pointer:
                api.TessDeleteText(text_pointer)
            api.TessBaseAPIEnd(handle)
            api.TessBaseAPIDelete(handle)


def read_tesseract(frame: np.ndarray) -> tuple[str, float]:
    global _runtime
    with _runtime_lock:
        if _runtime is None:
            _runtime = TesseractRuntime()
        runtime = _runtime
    return runtime.read(frame)


def reset_cached_runtime() -> None:
    """Clear the process cache; intended for tests and controlled shutdown."""

    global _runtime
    with _runtime_lock:
        _runtime = None


__all__ = [
    "TesseractRuntime",
    "TesseractRuntimeError",
    "bundled_runtime_root",
    "read_tesseract",
    "reset_cached_runtime",
]
