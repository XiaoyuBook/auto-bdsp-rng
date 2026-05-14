from __future__ import annotations

import os
import traceback
from pathlib import Path

from auto_bdsp_rng.__main__ import main


def _run_ocr_smoke(output_path: str) -> int:
    try:
        import numpy as np

        from auto_bdsp_rng.automation.auto_rng.dialog_timing import read_paddle_ocr_text

        frame = np.zeros((32, 96, 3), dtype=np.uint8)
        read_paddle_ocr_text(frame)
    except Exception:
        Path(output_path).write_text(traceback.format_exc(), encoding="utf-8")
        return 1
    Path(output_path).write_text("OCR smoke ok\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    smoke_output = os.environ.get("AUTO_BDSP_RNG_OCR_SMOKE")
    if smoke_output:
        raise SystemExit(_run_ocr_smoke(smoke_output))
    raise SystemExit(main(["gui"]))
