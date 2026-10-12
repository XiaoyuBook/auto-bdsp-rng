from __future__ import annotations

import importlib
import os
import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest

from auto_bdsp_rng.automation.auto_rng.ocr_runtime import (
    DEFAULT_OCR_CPU_THREADS,
    configure_ocr_runtime,
    configure_paddlex_cache,
    configure_packaged_paddlex_cache,
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
        (model_dir / "inference.yml").write_text("Global: {}", encoding="utf-8")
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


@pytest.mark.parametrize("install_name", ("ascii-install", "中文安装目录 with spaces"))
@pytest.mark.parametrize("cache_location", ("user", "install", None))
def test_frozen_runtime_initializes_ocr_with_a_readonly_install(monkeypatch, tmp_path, install_name, cache_location):
    install = tmp_path / install_name
    original_dirs = _create_bundled_model_files(install)
    user_cache = tmp_path / "user-cache"
    native_cache = tmp_path / "native-cache"
    monkeypatch.setattr(ocr_runtime.sys, "frozen", True, raising=False)
    monkeypatch.setattr(ocr_runtime.sys, "_MEIPASS", str(install), raising=False)
    monkeypatch.setattr(ocr_runtime, "_source_cache_home", None)
    monkeypatch.setattr(ocr_runtime, "_prepared_cache_home", None)
    monkeypatch.setattr(pokemon_info_ocr, "_PADDLE_OCR", None)
    monkeypatch.setenv("AUTO_BDSP_RNG_NATIVE_CACHE_HOME", str(native_cache))
    if cache_location is None:
        monkeypatch.delenv("PADDLE_PDX_CACHE_HOME", raising=False)
    else:
        configured = user_cache if cache_location == "user" else install / "paddlex_cache"
        monkeypatch.setenv("PADDLE_PDX_CACHE_HOME", str(configured))

    original_mkdir = os.mkdir
    original_open = os.open

    def readonly_mkdir(path, *args, **kwargs):
        if Path(path).is_relative_to(install):
            raise PermissionError("installation directory is read-only")
        return original_mkdir(path, *args, **kwargs)

    def readonly_open(path, flags, *args, **kwargs):
        if flags & (os.O_WRONLY | os.O_RDWR) and Path(path).is_relative_to(install):
            raise PermissionError("installation directory is read-only")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "mkdir", readonly_mkdir)
    monkeypatch.setattr(os, "open", readonly_open)
    calls = []
    instance = object()

    def factory(**kwargs):
        # PaddleX creates its temp directory before it can load the OCR models.
        (Path(os.environ["PADDLE_PDX_CACHE_HOME"]) / "temp").mkdir(parents=True, exist_ok=True)
        calls.append(kwargs)
        return instance

    monkeypatch.setitem(ocr_runtime.sys.modules, "paddleocr", SimpleNamespace(PaddleOCR=factory))
    hook = Path(__file__).resolve().parents[2] / "packaging" / "runtime_paddlex_cache.py"
    runpy.run_path(str(hook))
    runpy.run_path(str(hook))

    assert pokemon_info_ocr.get_paddle_ocr() is instance
    expected_cache = user_cache if cache_location == "user" else native_cache / "paddlex_cache"
    assert os.environ["PADDLE_PDX_CACHE_HOME"] == str(expected_cache)
    assert (expected_cache / "temp").is_dir()
    assert len(calls) == 1
    for key, original in zip(("text_detection_model_dir", "text_recognition_model_dir"), original_dirs):
        model_dir = Path(calls[0][key])
        assert str(model_dir).isascii()
        for filename in ("inference.json", "inference.pdiparams", "inference.yml"):
            assert (model_dir / filename).read_bytes() == (original / filename).read_bytes()


def test_chinese_bundled_models_and_paddlex_cache_use_ascii_paths(monkeypatch, tmp_path):
    source = tmp_path / "中文安装目录 with spaces"
    original_dirs = _create_bundled_model_files(source)
    monkeypatch.setattr(ocr_runtime.sys, "frozen", True, raising=False)
    monkeypatch.setattr(ocr_runtime.sys, "_MEIPASS", str(source), raising=False)
    monkeypatch.setenv("AUTO_BDSP_RNG_NATIVE_CACHE_HOME", str(tmp_path / "native"))
    monkeypatch.setenv("PADDLE_PDX_CACHE_HOME", str(tmp_path / "用户缓存"))

    configure_paddlex_cache()
    kwargs = optimized_paddle_ocr_kwargs()

    assert os.environ["PADDLE_PDX_CACHE_HOME"].isascii()
    for key, original in zip(("text_detection_model_dir", "text_recognition_model_dir"), original_dirs):
        prepared = Path(kwargs[key])
        assert str(prepared).isascii()
        assert prepared != original
        for filename in ("inference.json", "inference.pdiparams", "inference.yml"):
            assert (prepared / filename).read_bytes() == (original / filename).read_bytes()

    (original_dirs[0] / "inference.pdiparams").unlink()
    with pytest.raises(RuntimeError, match="模型不完整"):
        optimized_paddle_ocr_kwargs()


def test_development_cache_remembers_original_chinese_model_source(monkeypatch, tmp_path):
    original_dirs = _create_bundled_model_files(tmp_path / "中文开发路径")
    monkeypatch.setenv("PADDLE_PDX_CACHE_HOME", str(original_dirs[0].parent.parent))
    monkeypatch.setenv("AUTO_BDSP_RNG_NATIVE_CACHE_HOME", str(tmp_path / "native"))

    configure_paddlex_cache()
    first = resolve_local_ocr_model_dirs()
    configure_paddlex_cache()
    assert resolve_local_ocr_model_dirs() == first
    assert first is not None
    assert all(str(path).isascii() for path in first)


def test_runtime_hook_prepares_unicode_cache_before_library_import(monkeypatch, tmp_path):
    source = tmp_path / "中文安装目录"
    _create_bundled_model_files(source)
    monkeypatch.setattr(ocr_runtime.sys, "frozen", True, raising=False)
    monkeypatch.setattr(ocr_runtime.sys, "_MEIPASS", str(source), raising=False)
    monkeypatch.setenv("AUTO_BDSP_RNG_NATIVE_CACHE_HOME", str(tmp_path / "native"))
    monkeypatch.delenv("PADDLE_PDX_CACHE_HOME", raising=False)

    hook = Path(__file__).resolve().parents[2] / "packaging" / "runtime_paddlex_cache.py"
    runpy.run_path(str(hook))

    assert os.environ["PADDLE_PDX_CACHE_HOME"].isascii()
    assert all(str(path).isascii() for path in resolve_local_ocr_model_dirs())


def test_bad_cache_location_does_not_prevent_gui_startup(monkeypatch, tmp_path):
    source = tmp_path / "中文安装目录"
    _create_bundled_model_files(source)
    monkeypatch.setattr(ocr_runtime.sys, "frozen", True, raising=False)
    monkeypatch.setattr(ocr_runtime.sys, "_MEIPASS", str(source), raising=False)
    monkeypatch.setenv("AUTO_BDSP_RNG_NATIVE_CACHE_HOME", str(tmp_path / "中文缓存目录"))

    configure_packaged_paddlex_cache()

    with pytest.raises(RuntimeError, match="AUTO_BDSP_RNG_NATIVE_CACHE_HOME"):
        optimized_paddle_ocr_kwargs()


def test_ocr_initialization_error_keeps_the_native_failure(monkeypatch):
    def factory(**_kwargs):
        raise OSError("Cannot open model at C:/中文目录/inference.json")

    monkeypatch.setattr(pokemon_info_ocr, "optimized_paddle_ocr_kwargs", lambda: {"text_detection_model_dir": "local"})
    with pytest.raises(RuntimeError, match="Cannot open model.*inference.json") as failure:
        pokemon_info_ocr._create_paddle_ocr(factory)
    assert isinstance(failure.value.__cause__, OSError)


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
