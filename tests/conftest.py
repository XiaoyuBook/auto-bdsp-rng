from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def disable_automatic_ocr_warmup(monkeypatch):
    from auto_bdsp_rng.ui import MainWindow

    monkeypatch.setattr(MainWindow, "_start_ocr_warmup", lambda self: None)
