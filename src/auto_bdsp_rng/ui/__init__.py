"""Desktop UI package."""

from auto_bdsp_rng.automation.auto_rng.ocr_runtime import configure_ocr_runtime

configure_ocr_runtime()

from auto_bdsp_rng.ui.main_window import MainWindow, create_window, run

__all__ = ["MainWindow", "create_window", "run"]
