from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest

from auto_bdsp_rng.automation.auto_rng.ocr_runtime import (
    DEFAULT_OCR_CPU_THREADS,
    configure_ocr_runtime,
    optimized_paddle_ocr_kwargs,
    resolve_local_ocr_model_dirs,
)
from auto_bdsp_rng.automation.auto_rng import dialog_timing, ocr_runtime, pokemon_info_ocr


def _create_bundled_model_files(root: Path) -> tuple[Path, Path]:
    model_root = root / "paddlex_cache" / "official_models"
    detection_dir = model_root / "PP-OCRv5_mobile_det"
    recognition_dir = model_root / "PP-OCRv5_mobile_rec"
    for model_dir in (detection_dir, recognition_dir):
        model_dir.mkdir(parents=True)
        (model_dir / "inference.json").write_text("{}", encoding="utf-8")
        (model_dir / "inference.pdiparams").write_bytes(b"model")
    return detection_dir, recognition_dir


def test_configure_ocr_runtime_limits_thread_env(monkeypatch):
    for name in (
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK",
    ):
        monkeypatch.delenv(name, raising=False)

    configure_ocr_runtime()

    assert "OMP_NUM_THREADS" in __import__("os").environ
    assert __import__("os").environ["OMP_NUM_THREADS"] == str(DEFAULT_OCR_CPU_THREADS)
    assert __import__("os").environ["MKL_NUM_THREADS"] == str(DEFAULT_OCR_CPU_THREADS)
    assert __import__("os").environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] == "True"


def test_ocr_runtime_import_applies_thread_env(monkeypatch):
    for name in (
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK",
    ):
        monkeypatch.delenv(name, raising=False)

    importlib.reload(ocr_runtime)

    assert os.environ["OMP_NUM_THREADS"] == str(DEFAULT_OCR_CPU_THREADS)
    assert os.environ["MKL_NUM_THREADS"] == str(DEFAULT_OCR_CPU_THREADS)
    assert os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] == "True"


def test_configure_ocr_runtime_preserves_explicit_thread_env(monkeypatch):
    monkeypatch.setenv("OMP_NUM_THREADS", "6")

    configure_ocr_runtime()

    assert os.environ["OMP_NUM_THREADS"] == "6"


def test_optimized_paddle_ocr_kwargs_use_mobile_models_and_limited_threads():
    kwargs = optimized_paddle_ocr_kwargs()

    assert kwargs["text_detection_model_name"] == "PP-OCRv5_mobile_det"
    assert kwargs["text_recognition_model_name"] == "PP-OCRv5_mobile_rec"
    assert kwargs["cpu_threads"] == DEFAULT_OCR_CPU_THREADS
    assert kwargs["mkldnn_cache_capacity"] == 1
    assert "lang" not in kwargs


def test_frozen_runtime_binds_validated_bundled_model_dirs(monkeypatch, tmp_path):
    detection_dir, recognition_dir = _create_bundled_model_files(tmp_path)
    monkeypatch.setattr(ocr_runtime.sys, "frozen", True, raising=False)
    monkeypatch.setattr(ocr_runtime.sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setenv("PADDLE_PDX_CACHE_HOME", str(tmp_path / "untrusted-user-cache"))

    resolved = resolve_local_ocr_model_dirs()
    kwargs = optimized_paddle_ocr_kwargs()

    assert resolved == (detection_dir, recognition_dir)
    assert kwargs["text_detection_model_dir"] == str(detection_dir)
    assert kwargs["text_recognition_model_dir"] == str(recognition_dir)


def test_frozen_runtime_fails_fast_when_bundled_model_is_incomplete(monkeypatch, tmp_path):
    monkeypatch.setattr(ocr_runtime.sys, "frozen", True, raising=False)
    monkeypatch.setattr(ocr_runtime.sys, "_MEIPASS", str(tmp_path), raising=False)

    with pytest.raises(RuntimeError, match="安装包内置 OCR 模型不完整"):
        optimized_paddle_ocr_kwargs()


def test_dialog_timing_paddle_ocr_prefers_optimized_runtime_kwargs():
    calls = []

    def factory(**kwargs):
        calls.append(kwargs)
        return object()

    dialog_timing._create_paddle_ocr(factory)

    assert calls[0]["text_detection_model_name"] == "PP-OCRv5_mobile_det"
    assert calls[0]["text_recognition_model_name"] == "PP-OCRv5_mobile_rec"
    assert calls[0]["cpu_threads"] == DEFAULT_OCR_CPU_THREADS
    assert calls[0]["mkldnn_cache_capacity"] == 1


def test_pokemon_info_paddle_ocr_prefers_optimized_runtime_kwargs():
    calls = []

    def factory(**kwargs):
        calls.append(kwargs)
        return object()

    pokemon_info_ocr._create_paddle_ocr(factory)

    assert calls[0]["text_detection_model_name"] == "PP-OCRv5_mobile_det"
    assert calls[0]["text_recognition_model_name"] == "PP-OCRv5_mobile_rec"
    assert calls[0]["cpu_threads"] == DEFAULT_OCR_CPU_THREADS
    assert calls[0]["mkldnn_cache_capacity"] == 1
