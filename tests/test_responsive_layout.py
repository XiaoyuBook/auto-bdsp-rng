from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QRect, QSettings, QSize, Qt
from PySide6.QtWidgets import QApplication, QSplitter, QTabWidget

from auto_bdsp_rng.ui import MainWindow
from auto_bdsp_rng.ui.main_window import (
    MAIN_WINDOW_CURRENT_TAB_KEY,
    MAIN_WINDOW_MIN_SIZE,
    MAIN_WINDOW_SCREEN_MARGIN,
    MAIN_WINDOW_UI_SCALE_KEY,
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
    rect = _clamp_window_rect(saved, available, minimum=QSize(900, 600))

    assert rect.width() == 1000
    assert rect.height() == available.height() - 2 * MAIN_WINDOW_SCREEN_MARGIN
    assert rect.left() >= available.left() + MAIN_WINDOW_SCREEN_MARGIN
    assert rect.top() >= available.top() + MAIN_WINDOW_SCREEN_MARGIN
    assert rect.right() <= available.right() - MAIN_WINDOW_SCREEN_MARGIN
    assert rect.bottom() <= available.bottom() - MAIN_WINDOW_SCREEN_MARGIN


def test_main_window_keeps_fixed_layout_on_short_screen(
    app,
    monkeypatch,
    tmp_path: Path,
) -> None:
    available = QRect(0, 0, 1024, 640)
    monkeypatch.setattr(MainWindow, "_screen_available_geometry", lambda _self: QRect(available))

    window = MainWindow(profile_settings=_settings(tmp_path))
    window.show()
    app.processEvents()

    assert window.minimumSize() == MAIN_WINDOW_MIN_SIZE
    assert window.width() >= MAIN_WINDOW_MIN_SIZE.width()
    assert window.height() >= MAIN_WINDOW_MIN_SIZE.height()
    assert isinstance(window.tabs, QTabWidget)
    assert type(window.tabs) is QTabWidget
    assert isinstance(window.project_xs_tab, QSplitter)
    assert type(window.project_xs_tab) is QSplitter
    assert window.project_xs_tab.orientation() == Qt.Orientation.Horizontal
    assert window.project_xs_tab.widget(0).isAncestorOf(window.capture_group)
    assert window.project_xs_tab.widget(1).isAncestorOf(window.status_group)
    assert not hasattr(window, "project_xs_controls_scroll")
    assert not hasattr(window, "bdsp_content_scroll")


def test_main_window_uses_design_geometry_when_screen_can_fit_it(app, monkeypatch, tmp_path: Path) -> None:
    available = QRect(0, 0, 1600, 1000)
    monkeypatch.setattr(MainWindow, "_screen_available_geometry", lambda _self: QRect(available))

    window = MainWindow(profile_settings=_settings(tmp_path))
    window.show()
    app.processEvents()

    assert window.geometry().width() == 1150
    assert window.geometry().height() == 900
    assert window.geometry().bottom() <= available.bottom() - MAIN_WINDOW_SCREEN_MARGIN
    assert window.project_xs_tab.orientation() == Qt.Orientation.Horizontal


def test_main_header_connection_controls_do_not_overlap_at_minimum_width(
    app,
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        MainWindow,
        "_screen_available_geometry",
        lambda _self: QRect(0, 0, 1920, 1080),
    )
    window = MainWindow(profile_settings=_settings(tmp_path))
    window.resize(MAIN_WINDOW_MIN_SIZE)
    window._update_auto_rng_header(
        loop_index=9999,
        phase_text="搜索目标 Display TID",
        advances=1_000_000_000,
    )
    window._update_easycon_header(
        "伊机控 COM123456789",
        "connected",
        "已连接 · COM123456789",
        True,
    )
    window.show()
    app.processEvents()

    controls = (
        window.title_label,
        window.auto_loop_badge,
        window.auto_phase_badge,
        window.auto_advance_badge,
        window.video_source_header_button,
        window.easycon_header_button,
        window.help_button,
    )
    for left, right in zip(controls, controls[1:], strict=False):
        assert left.geometry().right() < right.geometry().left()
    assert controls[0].geometry().left() >= window.header.contentsRect().left()
    assert controls[-1].geometry().right() <= window.header.contentsRect().right()
    assert window.header_layout.minimumSize().width() <= window.header.width()
    assert window.video_source_header_button.size() == QSize(150, 30)
    assert window.easycon_header_button.size() == QSize(150, 30)
    assert window.auto_phase_badge.toolTip() == "阶段 搜索目标 Display TID"
    assert window.auto_advance_badge.toolTip() == "advance 1000000000"


def test_project_xs_never_reflows_to_vertical(app, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        MainWindow,
        "_screen_available_geometry",
        lambda _self: QRect(0, 0, 1920, 1080),
    )
    window = MainWindow(profile_settings=_settings(tmp_path))
    window.show()
    window.resize(1024, 640)
    app.processEvents()
    assert window.project_xs_tab.orientation() == Qt.Orientation.Horizontal

    window.resize(1280, 760)
    app.processEvents()

    assert window.project_xs_tab.orientation() == Qt.Orientation.Horizontal


def test_window_geometry_and_tab_restore_when_effective_scale_matches(
    app,
    monkeypatch,
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    saved = QRect(120, 80, 1300, 950)
    for key, value in zip(
        ("window/x", "window/y", "window/width", "window/height"),
        (saved.x(), saved.y(), saved.width(), saved.height()),
        strict=True,
    ):
        settings.setValue(key, value)
    settings.setValue(MAIN_WINDOW_UI_SCALE_KEY, 75)
    settings.setValue(MAIN_WINDOW_CURRENT_TAB_KEY, 3)
    monkeypatch.setattr(
        MainWindow,
        "_screen_available_geometry",
        lambda _self: QRect(0, 0, 1920, 1200),
    )

    window = MainWindow(profile_settings=settings, ui_scale=75, ui_scale_percent=75)

    assert window.geometry() == saved
    assert window.tabs.currentIndex() == 3


def test_window_geometry_resets_but_tab_restores_when_scale_changes(
    app,
    monkeypatch,
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    settings.setValue("window/x", 120)
    settings.setValue("window/y", 80)
    settings.setValue("window/width", 1300)
    settings.setValue("window/height", 950)
    settings.setValue(MAIN_WINDOW_UI_SCALE_KEY, 75)
    settings.setValue(MAIN_WINDOW_CURRENT_TAB_KEY, 4)
    monkeypatch.setattr(
        MainWindow,
        "_screen_available_geometry",
        lambda _self: QRect(0, 0, 1920, 1200),
    )

    window = MainWindow(profile_settings=settings, ui_scale=80, ui_scale_percent=80)

    assert window.geometry().size() == QSize(1150, 900)
    assert window.geometry() != QRect(120, 80, 1300, 950)
    assert window.tabs.currentIndex() == 4


def test_window_geometry_save_records_effective_scale_and_current_tab(
    app,
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    window = MainWindow(profile_settings=settings, ui_scale="auto", ui_scale_percent=65)
    window.tabs.setCurrentIndex(2)

    window._save_window_geometry()

    assert int(settings.value(MAIN_WINDOW_UI_SCALE_KEY)) == 65
    assert int(settings.value(MAIN_WINDOW_CURRENT_TAB_KEY)) == 2
