from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QRect, QSettings, QSize, Qt
from PySide6.QtWidgets import QApplication, QScrollArea

from auto_bdsp_rng.ui import MainWindow
from auto_bdsp_rng.ui.main_window import (
    MAIN_WINDOW_SCREEN_MARGIN,
    _clamp_window_rect,
    _fit_window_rect,
)


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    application = QApplication.instance() or QApplication([])
    yield application
    for widget in application.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    application.processEvents()


def _settings(tmp_path: Path) -> QSettings:
    settings = QSettings(str(tmp_path / "responsive.ini"), QSettings.Format.IniFormat)
    settings.clear()
    return settings


@pytest.mark.parametrize(
    "available",
    (
        QRect(0, 0, 1920, 1080),
        QRect(0, 0, 1366, 728),
        QRect(0, 0, 1280, 704),
        QRect(0, 0, 1024, 568),
    ),
)
def test_fit_window_rect_stays_inside_available_geometry(available: QRect) -> None:
    rect = _fit_window_rect(available)
    work = available.adjusted(
        MAIN_WINDOW_SCREEN_MARGIN,
        MAIN_WINDOW_SCREEN_MARGIN,
        -MAIN_WINDOW_SCREEN_MARGIN,
        -MAIN_WINDOW_SCREEN_MARGIN,
    )

    assert rect.width() <= max(1, work.width())
    assert rect.height() <= max(1, work.height())
    assert available.contains(rect.topLeft())
    assert available.contains(rect.bottomRight())


def test_fit_window_rect_compresses_compact_floor_on_tiny_work_area() -> None:
    available = QRect(100, 40, 800, 500)
    rect = _fit_window_rect(available)

    assert rect.size() == QSize(
        available.width() - 2 * MAIN_WINDOW_SCREEN_MARGIN,
        available.height() - 2 * MAIN_WINDOW_SCREEN_MARGIN,
    )
    assert rect.left() == available.left() + MAIN_WINDOW_SCREEN_MARGIN
    assert rect.top() == available.top() + MAIN_WINDOW_SCREEN_MARGIN


def test_clamp_window_rect_repositions_saved_window_without_losing_size() -> None:
    available = QRect(0, 0, 1366, 728)
    saved = QRect(-900, 500, 1000, 900)
    rect = _clamp_window_rect(saved, available)

    assert rect.width() == 1000
    assert rect.height() == available.height() - 2 * MAIN_WINDOW_SCREEN_MARGIN
    assert rect.left() >= available.left() + MAIN_WINDOW_SCREEN_MARGIN
    assert rect.top() >= available.top() + MAIN_WINDOW_SCREEN_MARGIN
    assert rect.right() <= available.right() - MAIN_WINDOW_SCREEN_MARGIN
    assert rect.bottom() <= available.bottom() - MAIN_WINDOW_SCREEN_MARGIN


def test_main_window_uses_compact_geometry_and_vertical_project_xs_on_short_screen(
    app,
    monkeypatch,
    tmp_path: Path,
) -> None:
    available = QRect(0, 0, 1024, 640)
    monkeypatch.setattr(MainWindow, "_screen_available_geometry", lambda _self: QRect(available))

    window = MainWindow(profile_settings=_settings(tmp_path))
    window.show()
    app.processEvents()

    assert window.width() <= available.width() - 2 * MAIN_WINDOW_SCREEN_MARGIN
    assert window.height() <= available.height() - 2 * MAIN_WINDOW_SCREEN_MARGIN
    assert window.minimumSize().width() <= available.width() - 2 * MAIN_WINDOW_SCREEN_MARGIN
    assert window.minimumSize().height() <= available.height() - 2 * MAIN_WINDOW_SCREEN_MARGIN
    assert window.project_xs_tab.orientation() == Qt.Orientation.Vertical
    assert window._project_xs_vertical is True
    window.tabs.setCurrentWidget(window.project_xs_tab)
    app.processEvents()
    capture_top = window.capture_group.mapTo(window.project_xs_tab, window.capture_group.rect().topLeft()).y()
    assert capture_top <= 5
    assert window.project_xs_tab.sizes()[0] >= 150
    assert isinstance(window.project_xs_controls_scroll, QScrollArea)
    assert window.project_xs_controls_scroll.verticalScrollBar().maximum() > 0
    assert isinstance(window.auto_rng_tab.content_scroll, QScrollArea)
    assert isinstance(window.auto_tid_rng_tab.content_scroll, QScrollArea)

    window.tabs.setCurrentWidget(window.bdsp_tab)
    app.processEvents()
    assert isinstance(window.bdsp_content_scroll, QScrollArea)
    assert window.bdsp_content_scroll.verticalScrollBar().maximum() > 0
    assert window.bdsp_content_scroll.horizontalScrollBar().maximum() > 0


def test_main_window_fits_common_1366x768_work_area(app, monkeypatch, tmp_path: Path) -> None:
    available = QRect(0, 0, 1366, 728)
    monkeypatch.setattr(MainWindow, "_screen_available_geometry", lambda _self: QRect(available))

    window = MainWindow(profile_settings=_settings(tmp_path))
    window.show()
    app.processEvents()

    assert window.geometry().width() == 1150
    assert window.geometry().height() == available.height() - 2 * MAIN_WINDOW_SCREEN_MARGIN
    assert window.geometry().bottom() <= available.bottom() - MAIN_WINDOW_SCREEN_MARGIN
    assert window.project_xs_tab.orientation() == Qt.Orientation.Horizontal
    assert window.project_xs_controls_scroll.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded


def test_project_xs_returns_to_horizontal_layout_when_width_is_restored(app, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        MainWindow,
        "_screen_available_geometry",
        lambda _self: QRect(0, 0, 1920, 1080),
    )
    window = MainWindow(profile_settings=_settings(tmp_path))
    window.show()
    window.resize(1024, 640)
    app.processEvents()
    assert window.project_xs_tab.orientation() == Qt.Orientation.Vertical

    window.resize(1280, 760)
    app.processEvents()

    assert window.project_xs_tab.orientation() == Qt.Orientation.Horizontal
    assert window._project_xs_vertical is False
