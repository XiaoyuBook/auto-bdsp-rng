from __future__ import annotations

import os


DEFAULT_OCR_CPU_THREADS = 2


def configure_ocr_runtime(*, cpu_threads: int = DEFAULT_OCR_CPU_THREADS) -> None:
    value = str(max(1, int(cpu_threads)))
    for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ.setdefault(name, value)
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")


def optimized_paddle_ocr_kwargs(*, cpu_threads: int = DEFAULT_OCR_CPU_THREADS) -> dict[str, object]:
    return {
        "text_detection_model_name": "PP-OCRv5_mobile_det",
        "text_recognition_model_name": "PP-OCRv5_mobile_rec",
        "use_doc_orientation_classify": False,
        "use_doc_unwarping": False,
        "use_textline_orientation": False,
        "cpu_threads": max(1, int(cpu_threads)),
        "enable_mkldnn": True,
    }
