from __future__ import annotations

import os
import sys
from pathlib import Path


DEFAULT_OCR_CPU_THREADS = 2
OCR_DETECTION_MODEL_NAME = "PP-OCRv5_mobile_det"
OCR_RECOGNITION_MODEL_NAME = "PP-OCRv5_mobile_rec"
_REQUIRED_MODEL_FILES = ("inference.json", "inference.pdiparams")


def configure_ocr_runtime(*, cpu_threads: int = DEFAULT_OCR_CPU_THREADS) -> None:
    value = str(max(1, int(cpu_threads)))
    for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ.setdefault(name, value)
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")


def resolve_local_ocr_model_dirs() -> tuple[Path, Path] | None:
    """Resolve validated local model directories without allowing a frozen app to fall back online."""
    frozen = bool(getattr(sys, "frozen", False))
    if frozen:
        base_dir = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
        cache_home = base_dir / "paddlex_cache"
    else:
        configured_home = os.environ.get("PADDLE_PDX_CACHE_HOME", "").strip()
        if not configured_home:
            return None
        cache_home = Path(configured_home)
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
            missing_text = ", ".join(str(path.relative_to(base_dir)) for path in missing)
            raise RuntimeError(f"安装包内置 OCR 模型不完整，请重新安装软件: {missing_text}")
        return None
    return detection_dir, recognition_dir


def optimized_paddle_ocr_kwargs(*, cpu_threads: int = DEFAULT_OCR_CPU_THREADS) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "text_detection_model_name": OCR_DETECTION_MODEL_NAME,
        "text_recognition_model_name": OCR_RECOGNITION_MODEL_NAME,
        "use_doc_orientation_classify": False,
        "use_doc_unwarping": False,
        "use_textline_orientation": False,
        "cpu_threads": max(1, int(cpu_threads)),
        "enable_mkldnn": True,
    }
    local_model_dirs = resolve_local_ocr_model_dirs()
    if local_model_dirs is not None:
        detection_dir, recognition_dir = local_model_dirs
        kwargs["text_detection_model_dir"] = str(detection_dir)
        kwargs["text_recognition_model_dir"] = str(recognition_dir)
    return kwargs
