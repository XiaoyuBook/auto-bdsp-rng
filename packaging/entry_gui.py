from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path


from auto_bdsp_rng.automation.auto_rng.ocr_runtime import configure_packaged_paddlex_cache


configure_packaged_paddlex_cache()

from auto_bdsp_rng.__main__ import main


def _run_ocr_smoke(output_path: str) -> int:
    try:
        mode = os.environ.get("AUTO_BDSP_RNG_OCR_SMOKE_MODE", "full").strip().lower()
        if mode == "import":
            import paddle  # noqa: F401
            import paddleocr  # noqa: F401
            import paddlex  # noqa: F401
        else:
            import cv2
            import numpy as np

            from auto_bdsp_rng.automation.auto_rng.dialog_timing import read_paddle_ocr_text

            frame = np.full((96, 320, 3), 255, dtype=np.uint8)
            cv2.putText(frame, "123456", (12, 72), cv2.FONT_HERSHEY_SIMPLEX, 2, (0, 0, 0), 3)
            if not read_paddle_ocr_text(frame).strip():
                raise RuntimeError("Packaged OCR did not recognize the smoke-test image")
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


def _run_image_path_smoke(output_path: str) -> int:
    try:
        import numpy as np

        from auto_bdsp_rng.automation.auto_rng.pokemon_info_ocr import _load_image
        from auto_bdsp_rng.blink_detection.models import BlinkCaptureConfig
        from auto_bdsp_rng.blink_detection.project_xs import _load_eye_template
        from auto_bdsp_rng.image_io import write_image

        directory = Path(output_path).parent / "中文图片 with spaces"
        eye_path = directory / "眼睛模板.png"
        eye = np.full((5, 7), 40, dtype=np.uint8)
        write_image(eye_path, eye)
        config = BlinkCaptureConfig(eye_path, (0, 0, 7, 5))
        if not np.array_equal(_load_eye_template(config), eye):
            raise RuntimeError("Eye template did not round-trip through a Unicode path")
        write_image(eye_path, np.full_like(eye, 80))
        if not np.all(_load_eye_template(config) == 80):
            raise RuntimeError("Eye template could not be updated in a Unicode path")
        image_path = directory / "原始截图.png"
        frame = np.arange(5 * 7 * 3, dtype=np.uint8).reshape((5, 7, 3))
        write_image(image_path, frame)
        if not np.array_equal(_load_image(image_path), frame):
            raise RuntimeError("OCR image did not round-trip through a Unicode path")
    except Exception:
        Path(output_path).write_text(traceback.format_exc(), encoding="utf-8")
        return 1
    Path(output_path).write_text("Image path smoke ok\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    smoke_output = os.environ.get("AUTO_BDSP_RNG_OCR_SMOKE")
    if smoke_output:
        raise SystemExit(_run_ocr_smoke(smoke_output))
    easycon_ocr_smoke_output = os.environ.get("AUTO_BDSP_RNG_EASYCON_OCR_SMOKE")
    if easycon_ocr_smoke_output:
        raise SystemExit(_run_easycon_ocr_smoke(easycon_ocr_smoke_output))
    image_path_smoke_output = os.environ.get("AUTO_BDSP_RNG_IMAGE_PATH_SMOKE")
    if image_path_smoke_output:
        raise SystemExit(_run_image_path_smoke(image_path_smoke_output))
    if len(sys.argv) > 1 and sys.argv[1] == "--capture-broker-child":
        raise SystemExit(main(["capture-broker", *sys.argv[2:]]))
    raise SystemExit(main(["gui"]))
