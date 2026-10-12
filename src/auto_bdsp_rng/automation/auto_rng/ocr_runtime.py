from __future__ import annotations

import os
import sys
from pathlib import Path

from auto_bdsp_rng.native_paths import NativePathError, ascii_resource_directory, native_cache_dir, needs_ascii_path


DEFAULT_OCR_CPU_THREADS = 2
OCR_DETECTION_MODEL_NAME = "PP-OCRv5_mobile_det"
OCR_RECOGNITION_MODEL_NAME = "PP-OCRv5_mobile_rec"
_MODEL_NAMES = (OCR_DETECTION_MODEL_NAME, OCR_RECOGNITION_MODEL_NAME)
_REQUIRED_MODEL_FILES = ("inference.json", "inference.pdiparams", "inference.yml")
_source_cache_home: Path | None = None
_prepared_cache_home: Path | None = None


def configure_ocr_runtime(*, cpu_threads: int = DEFAULT_OCR_CPU_THREADS) -> None:
    value = str(max(1, int(cpu_threads)))
    for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ.setdefault(name, value)
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")


# Apply thread limits before NumPy/Paddle can observe them during import.
configure_ocr_runtime()


def _local_source_cache_home() -> Path | None:
    if getattr(sys, "frozen", False):
        base_dir = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
        return base_dir / "paddlex_cache"
    configured = os.environ.get("PADDLE_PDX_CACHE_HOME", "").strip()
    if _prepared_cache_home is not None and configured == str(_prepared_cache_home):
        return _source_cache_home
    return Path(configured) if configured else None


def configure_paddlex_cache() -> None:
    """Set PaddleX's writable cache before importing it, retaining the original model source."""
    global _source_cache_home, _prepared_cache_home
    source = _local_source_cache_home()
    cache_home = source or Path.home() / ".paddlex"
    if needs_ascii_path(cache_home):
        cache_home = native_cache_dir("paddlex_cache")
    _source_cache_home = source
    _prepared_cache_home = cache_home
    os.environ["PADDLE_PDX_CACHE_HOME"] = str(cache_home)


def configure_packaged_paddlex_cache() -> None:
    if not getattr(sys, "frozen", False):
        return
    try:
        configure_paddlex_cache()
    except NativePathError:
        # Keep the GUI available; OCR initialization will report the cache error
        # in its normal failure dialog instead of aborting the whole application.
        pass


def resolve_local_ocr_model_dirs() -> tuple[Path, Path] | None:
    """Resolve validated local model directories without allowing a frozen app to fall back online."""
    frozen = bool(getattr(sys, "frozen", False))
    cache_home = _local_source_cache_home()
    if cache_home is None:
        return None
    model_root = cache_home / "official_models"
    detection_dir = model_root / OCR_DETECTION_MODEL_NAME
    recognition_dir = model_root / OCR_RECOGNITION_MODEL_NAME
    missing = [
        path
        for model_dir in (detection_dir, recognition_dir)
        for filename in _REQUIRED_MODEL_FILES
        if not (path := model_dir / filename).is_file() or path.stat().st_size <= 0
    ]
    if missing:
        if frozen:
            missing_text = ", ".join(str(path.relative_to(cache_home.parent)) for path in missing)
            raise RuntimeError(f"安装包内置 OCR 模型不完整，请重新安装软件: {missing_text}")
        return None
    native_home = ascii_resource_directory(
        cache_home,
        "paddle_models",
        subdirectories=tuple(f"official_models/{name}" for name in _MODEL_NAMES),
    )
    return (
        native_home / "official_models" / OCR_DETECTION_MODEL_NAME,
        native_home / "official_models" / OCR_RECOGNITION_MODEL_NAME,
    )


def optimized_paddle_ocr_kwargs(*, cpu_threads: int = DEFAULT_OCR_CPU_THREADS) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "text_detection_model_name": OCR_DETECTION_MODEL_NAME,
        "text_recognition_model_name": OCR_RECOGNITION_MODEL_NAME,
        "use_doc_orientation_classify": False,
        "use_doc_unwarping": False,
        "use_textline_orientation": False,
        "cpu_threads": max(1, int(cpu_threads)),
        "enable_mkldnn": True,
        "mkldnn_cache_capacity": 1,
    }
    local_model_dirs = resolve_local_ocr_model_dirs()
    if local_model_dirs is not None:
        detection_dir, recognition_dir = local_model_dirs
        kwargs["text_detection_model_dir"] = str(detection_dir)
        kwargs["text_recognition_model_dir"] = str(recognition_dir)
    return kwargs
