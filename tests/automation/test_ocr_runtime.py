from __future__ import annotations

from auto_bdsp_rng.automation.auto_rng.ocr_runtime import (
    DEFAULT_OCR_CPU_THREADS,
    configure_ocr_runtime,
    optimized_paddle_ocr_kwargs,
)
from auto_bdsp_rng.automation.auto_rng import dialog_timing, pokemon_info_ocr


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


def test_optimized_paddle_ocr_kwargs_use_mobile_models_and_limited_threads():
    kwargs = optimized_paddle_ocr_kwargs()

    assert kwargs["text_detection_model_name"] == "PP-OCRv5_mobile_det"
    assert kwargs["text_recognition_model_name"] == "PP-OCRv5_mobile_rec"
    assert kwargs["cpu_threads"] == DEFAULT_OCR_CPU_THREADS
    assert "lang" not in kwargs


def test_dialog_timing_paddle_ocr_prefers_optimized_runtime_kwargs():
    calls = []

    def factory(**kwargs):
        calls.append(kwargs)
        return object()

    dialog_timing._create_paddle_ocr(factory)

    assert calls[0]["text_detection_model_name"] == "PP-OCRv5_mobile_det"
    assert calls[0]["text_recognition_model_name"] == "PP-OCRv5_mobile_rec"
    assert calls[0]["cpu_threads"] == DEFAULT_OCR_CPU_THREADS


def test_pokemon_info_paddle_ocr_prefers_optimized_runtime_kwargs():
    calls = []

    def factory(**kwargs):
        calls.append(kwargs)
        return object()

    pokemon_info_ocr._create_paddle_ocr(factory)

    assert calls[0]["text_detection_model_name"] == "PP-OCRv5_mobile_det"
    assert calls[0]["text_recognition_model_name"] == "PP-OCRv5_mobile_rec"
    assert calls[0]["cpu_threads"] == DEFAULT_OCR_CPU_THREADS
