from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path


def _configure_packaged_paddlex_cache() -> None:
    if getattr(sys, "frozen", False):
        base_dir = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    else:
        base_dir = Path(__file__).resolve().parent
    cache_home = base_dir / "paddlex_cache"
    if (cache_home / "official_models").exists():
        os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(cache_home))


_configure_packaged_paddlex_cache()

from auto_bdsp_rng.__main__ import main


def _run_ocr_smoke(output_path: str) -> int:
    try:
        mode = os.environ.get("AUTO_BDSP_RNG_OCR_SMOKE_MODE", "full").strip().lower()
        if mode == "import":
            import paddle  # noqa: F401
            import paddleocr  # noqa: F401
            import paddlex  # noqa: F401
        else:
            import numpy as np

            from auto_bdsp_rng.automation.auto_rng.dialog_timing import read_paddle_ocr_text

            frame = np.zeros((32, 96, 3), dtype=np.uint8)
            read_paddle_ocr_text(frame)
    except Exception:
        Path(output_path).write_text(traceback.format_exc(), encoding="utf-8")
        return 1
    Path(output_path).write_text("OCR smoke ok\n", encoding="utf-8")
    return 0


def _run_easycon_ocr_smoke(output_path: str) -> int:
    try:
        import numpy as np

        from auto_bdsp_rng.automation.easycon.native.tesseract import read_tesseract

        read_tesseract(np.zeros((32, 96, 3), dtype=np.uint8))
    except Exception:
        Path(output_path).write_text(traceback.format_exc(), encoding="utf-8")
        return 1
    Path(output_path).write_text("EasyCon OCR smoke ok\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    smoke_output = os.environ.get("AUTO_BDSP_RNG_OCR_SMOKE")
    if smoke_output:
        raise SystemExit(_run_ocr_smoke(smoke_output))
    easycon_ocr_smoke_output = os.environ.get("AUTO_BDSP_RNG_EASYCON_OCR_SMOKE")
    if easycon_ocr_smoke_output:
        raise SystemExit(_run_easycon_ocr_smoke(easycon_ocr_smoke_output))
    if len(sys.argv) > 1 and sys.argv[1] == "--capture-broker-child":
        raise SystemExit(main(["capture-broker", *sys.argv[2:]]))
    raise SystemExit(main(["gui"]))
