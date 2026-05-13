from __future__ import annotations

from pathlib import Path

import pytest


pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from auto_bdsp_rng import __version__
from auto_bdsp_rng.ui.main_window import APP_DISPLAY_TITLE, MainWindow, configure_application_identity


def test_pyinstaller_spec_collects_project_xs_win32ui_dependency():
    root = Path(__file__).resolve().parents[1]
    spec = (root / "packaging" / "auto-bdsp-rng.spec").read_text(encoding="utf-8")

    assert '"win32ui"' in spec


def test_build_script_copies_project_xs_user_resources_next_to_exe():
    root = Path(__file__).resolve().parents[1]
    script = (root / "scripts" / "build_exe.py").read_text(encoding="utf-8")

    assert 'DIST_DIR / "third_party" / "Project_Xs_CHN" / "configs"' in script
    assert 'DIST_DIR / "third_party" / "Project_Xs_CHN" / "images"' in script
    assert "PROJECT_XS_OVERRIDES" in script
    assert "overlay_optional_tree" in script
    assert "verify_project_xs_assets" in script


def test_pyinstaller_spec_names_chinese_executable():
    root = Path(__file__).resolve().parents[1]
    spec = (root / "packaging" / "auto-bdsp-rng.spec").read_text(encoding="utf-8")

    assert 'name="珍钻复刻定点自动乱数"' in spec


def test_qt_application_and_window_use_project_identity(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    configure_application_identity(app)
    window = MainWindow()

    assert APP_DISPLAY_TITLE.endswith(f"v{__version__}")
    assert app.applicationName() == APP_DISPLAY_TITLE
    assert window.windowTitle() == APP_DISPLAY_TITLE
    assert not app.windowIcon().isNull()
    assert not window.windowIcon().isNull()
