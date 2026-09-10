from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QRect, QSettings, QSize, Qt
from PySide6.QtWidgets import QApplication, QSplitter, QTabWidget

from auto_bdsp_rng.automation.easycon import EasyConConfig
from auto_bdsp_rng.ui import MainWindow
import auto_bdsp_rng.ui.auto_rng_panel as auto_rng_panel_module
import auto_bdsp_rng.ui.auto_tid_rng_panel as auto_tid_rng_panel_module
import auto_bdsp_rng.ui.easycon_panel as easycon_panel_module
import auto_bdsp_rng.ui.main_window as main_window_module
import auto_bdsp_rng.ui.ocr_settings_dialog as ocr_settings_dialog_module
import auto_bdsp_rng.ui.tid_ocr_dialog as tid_ocr_dialog_module
from auto_bdsp_rng.ui.main_window import (
    APP_TITLE,
    MAIN_WINDOW_CURRENT_TAB_KEY,
    MAIN_WINDOW_MIN_SIZE,
    MAIN_WINDOW_SCREEN_MARGIN,
    MAIN_WINDOW_UI_SCALE_KEY,
    _clamp_window_rect,
    _fit_window_rect,
)


@pytest.fixture(autouse=True)
def isolated_ui_qsettings(monkeypatch, tmp_path: Path):
    isolation_root = (tmp_path / ".ui-isolation").resolve()
    isolation_root.mkdir()
    applications = {
        "MainWindowProfile": "main-window.ini",
        "AutoRngPanel": "auto-rng.ini",
        "AutoTidRngPanel": "auto-tid-rng.ini",
        "OcrSettings": "ocr.ini",
        "AutoTidRngOcr": "tid-ocr.ini",
    }
    settings = {
        application: QSettings(
            str((isolation_root / filename).resolve()),
            QSettings.Format.IniFormat,
        )
        for application, filename in applications.items()
    }
    for value in settings.values():
        value.clear()

    def router(application: str):
        def create(organization: str, requested_application: str) -> QSettings:
            assert (organization, requested_application) == (
                "auto-bdsp-rng",
                application,
            )
            return settings[application]

        return create

    monkeypatch.setattr(main_window_module, "QSettings", router("MainWindowProfile"))
    monkeypatch.setattr(auto_rng_panel_module, "QSettings", router("AutoRngPanel"))
    monkeypatch.setattr(
        auto_tid_rng_panel_module,
        "QSettings",
        router("AutoTidRngPanel"),
    )
    monkeypatch.setattr(
        ocr_settings_dialog_module,
        "QSettings",
        router("OcrSettings"),
    )
    monkeypatch.setattr(
        tid_ocr_dialog_module,
        "QSettings",
        router("AutoTidRngOcr"),
    )
    isolated_script_dir = isolation_root / "easycon-script"
    isolated_script_dir.mkdir()
    isolated_easycon_config = (isolation_root / "easycon.json").resolve()
    monkeypatch.setattr(easycon_panel_module, "SCRIPT_DIR", isolated_script_dir)
    monkeypatch.setattr(easycon_panel_module, "load_config", lambda: EasyConConfig())
    monkeypatch.setattr(
        easycon_panel_module,
        "save_config",
        lambda _config: isolated_easycon_config,
    )
    monkeypatch.setattr(main_window_module, "should_show_startup_notice", lambda: False)

    yield settings

    resolved_paths = set()
    for value in settings.values():
        value.sync()
        path = Path(value.fileName()).resolve()
        assert value.format() == QSettings.Format.IniFormat
        assert value.status() == QSettings.Status.NoError
        assert path.is_relative_to(isolation_root)
        resolved_paths.add(path)
    assert len(resolved_paths) == len(settings)


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


def test_main_window_reflows_within_short_screen(
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
    assert available.contains(window.geometry())
    window.tabs.setCurrentWidget(window.project_xs_tab)
    app.processEvents()
    assert isinstance(window.project_xs_splitter, QSplitter)
    assert window.project_xs_splitter.orientation() == Qt.Orientation.Vertical
    assert window.project_xs_splitter.widget(0).isAncestorOf(window.capture_group)
    assert window.project_xs_splitter.widget(1).isAncestorOf(window.status_group)
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
    window.tabs.setCurrentWidget(window.project_xs_tab)
    app.processEvents()
    assert window.project_xs_splitter.orientation() == Qt.Orientation.Horizontal


def test_main_header_connection_controls_do_not_overlap_at_minimum_width(
    app,
    monkeypatch,
    isolated_ui_qsettings,
) -> None:
    monkeypatch.setattr(
        MainWindow,
        "_screen_available_geometry",
        lambda _self: QRect(0, 0, 1920, 1080),
    )
    window = MainWindow(
        profile_settings=isolated_ui_qsettings["MainWindowProfile"],
    )
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

    controls = tuple(control for control in (
        window.title_label,
        window.version_label,
        window.readiness.button,
        window.video_source_header_button,
        window.easycon_header_button,
        window.help_button,
    ) if control.isVisible())
    for left, right in zip(controls, controls[1:], strict=False):
        assert left.geometry().right() < right.geometry().left()
    assert controls[0].geometry().left() >= window.header.contentsRect().left()
    assert controls[-1].geometry().right() <= window.header.contentsRect().right()
    assert window.header_layout.minimumSize().width() <= window.header.width()
    assert window.title_label.toolTip() == APP_TITLE
    assert window.version_label.text().startswith("v")
    assert window.version_label.text() not in window.title_label.text()
    assert window.title_label.font().pixelSize() == 20
    assert window.version_label.font().pixelSize() == 12
    assert window.title_label.font().weight() == 500
    assert window.version_label.font().weight() == 400
    assert window.video_source_header_button.size() == QSize(150, 32)
    assert window.easycon_header_button.size() == QSize(150, 32)
    assert window.easycon_header_button.status_text == "已连接"
    assert "COM123456789" in window.easycon_header_button.toolTip()
    assert not window.auto_loop_badge.isVisible()
    assert not window.auto_phase_badge.isVisible()
    assert not window.auto_advance_badge.isVisible()
    assert not window.navigation_status.isVisible()
    assert window.navigation_status.text() == "● 定点 · 第 9999 轮"
    assert window.navigation_status.geometry().right() <= window.tabs.width()
    assert window.auto_phase_badge.toolTip() == "阶段 搜索目标 Display TID"
    assert window.auto_advance_badge.toolTip() == "advance 1000000000"
    assert "阶段 搜索目标 Display TID" in window.navigation_status.toolTip()
    assert "advance 1,000,000,000" in window.navigation_status.toolTip()

    window._apply_auto_tid_header_progress(
        SimpleNamespace(
            phase="等待取名",
            loop_index=7,
            current_advances=321,
            log_message="",
        )
    )
    assert window.navigation_status.text() == "● TID · 第 7 轮"
    assert "阶段 等待取名" in window.navigation_status.toolTip()
    assert "advance 321" in window.navigation_status.toolTip()


def test_confirmed_navigation_and_seed_preview_use_content_geometry(
    app,
    monkeypatch,
    isolated_ui_qsettings,
) -> None:
    monkeypatch.setattr(
        MainWindow,
        "_screen_available_geometry",
        lambda _self: QRect(0, 0, 1920, 1080),
    )
    window = MainWindow(
        profile_settings=isolated_ui_qsettings["MainWindowProfile"],
    )
    window.resize(1150, 900)
    window.tabs.setCurrentWidget(window.project_xs_tab)
    window.show()
    app.processEvents()
    app.processEvents()

    assert window.size() == QSize(1150, 900)
    tab_bar = window.tabs.tabBar()
    tab_widths = [tab_bar.tabRect(index).width() for index in range(tab_bar.count())]
    text_widths = [
        tab_bar.fontMetrics().horizontalAdvance(tab_bar.tabText(index))
        for index in range(tab_bar.count())
    ]
    assert len(set(tab_widths)) > 1
    assert all(
        20 <= tab_width - text_width <= 40
        for tab_width, text_width in zip(tab_widths, text_widths, strict=True)
    )

    stylesheet = " ".join(window.styleSheet().split())
    assert "min-width: 0;" in stylesheet
    assert "margin-right: 25px;" in stylesheet
    assert "border-bottom: 2px solid #087C58;" in stylesheet
    assert window.styleSheet().count("QLabel#WindowTitle {") == 1
    assert window.styleSheet().count("QLabel#WindowVersion {") == 1
    assert window.styleSheet().count("QGroupBox::title {") == 1
    group_rule = stylesheet.split("QGroupBox {", 1)[1].split("}", 1)[0]
    group_title_rule = stylesheet.split("QGroupBox::title {", 1)[1].split("}", 1)[0]
    checkbox_rule = stylesheet.split("QCheckBox {", 1)[1].split("}", 1)[0]
    assert "font-weight: 400;" in group_rule
    assert "font-weight: 500;" in group_title_rule
    assert "background: transparent;" in checkbox_rule

    preview = window.preview_aspect_container
    assert preview.parentWidget() is window.preview_group
    assert window.preview_label.parentWidget() is preview
    assert preview.heightForWidth(640) == 360
    assert preview.height() == preview.heightForWidth(preview.width())
    assert abs(preview.width() * 9 - preview.height() * 16) <= 8
    controls = (
        window.preview_title_label,
        window.main_preview_overlay_check,
        window.picture_in_picture_button,
    )
    centers = [control.geometry().center().y() for control in controls]
    assert max(centers) - min(centers) <= 3
    assert controls[0].geometry().right() < controls[1].geometry().left()
    assert controls[1].geometry().right() < controls[2].geometry().left()


def test_tid_target_pool_keeps_three_visible_rows_under_main_window_theme(
    app, isolated_ui_qsettings,
) -> None:
    window = MainWindow(profile_settings=isolated_ui_qsettings["MainWindowProfile"])
    panel = window.auto_tid_rng_tab
    for value in range(50):
        panel.add_target_display_tid(value)
    window.tabs.setCurrentWidget(panel)
    window.show()
    app.processEvents()
    app.processEvents()

    pool = panel.target_list
    assert pool.viewport().height() >= pool.gridSize().height() * 3
    assert pool.verticalScrollBar().maximum() > 0
    pool.scrollToBottom()
    app.processEvents()
    assert pool.viewport().rect().contains(pool.visualItemRect(pool.item(49)))
    assert panel.target_display_tids() == tuple(range(50))


def test_runtime_badge_and_spin_edit_fit_their_actual_main_window_geometry(
    app, isolated_ui_qsettings,
) -> None:
    window = MainWindow(profile_settings=isolated_ui_qsettings["MainWindowProfile"])
    panel = window.auto_rng_tab
    window.tabs.setCurrentWidget(panel)
    window.show()
    app.processEvents()
    app.processEvents()

    badge = panel.runtime_round_label
    assert badge.height() <= badge.fontMetrics().height() + 8
    assert badge.width() >= badge.fontMetrics().horizontalAdvance(badge.text())
    for spin in (panel.max_advances, panel.max_wait_frames):
        edit = spin.lineEdit()
        assert spin.rect().contains(edit.geometry())
        assert edit.height() >= edit.fontMetrics().height()


@pytest.mark.parametrize("task", ("定点", "TID"))
@pytest.mark.parametrize("terminal_phase", ("已完成", "失败", "空闲", "已停止"))
def test_main_header_terminal_progress_does_not_keep_previous_round(
    app,
    tmp_path: Path,
    task: str,
    terminal_phase: str,
) -> None:
    window = MainWindow(profile_settings=_settings(tmp_path))
    window._update_auto_rng_header(
        loop_index=7,
        phase_text="搜索目标",
        advances=321,
        task_label=task,
    )
    progress = SimpleNamespace(
        phase=terminal_phase,
        loop_index=7,
        current_advances=None,
        log_message="",
    )

    if task == "定点":
        window._apply_auto_rng_header_progress(progress)
    else:
        window._apply_auto_tid_header_progress(progress)

    assert window.navigation_status.text() == f"● {task} · {terminal_phase}"
    assert window._header_loop_index == 0
    assert window.navigation_status.property("state") == (
        "failed" if terminal_phase == "失败" else "idle"
    )


@pytest.mark.parametrize(
    ("task", "panel_status", "expected_phase"),
    (
        ("定点", "已完成", "已完成"),
        ("定点", "失败", "失败"),
        ("TID", "状态：空闲", "已停止"),
    ),
)
def test_main_header_run_state_finalizes_without_final_progress(
    app,
    tmp_path: Path,
    task: str,
    panel_status: str,
    expected_phase: str,
) -> None:
    window = MainWindow(profile_settings=_settings(tmp_path))
    window._update_auto_rng_header(
        loop_index=4,
        phase_text="等待触发",
        advances=88,
        task_label=task,
    )

    if task == "定点":
        window._active_auto_rng_run_id = "test-run"
        window.auto_rng_tab.status_badge.setText(panel_status)
        window._handle_auto_rng_run_state_changed(True)
        assert window.navigation_status.text() == "● 定点 · 运行中"
        window._handle_auto_rng_run_state_changed(False)
    else:
        window._active_auto_tid_run_id = "test-run"
        window.auto_tid_rng_tab.status_badge.setText(panel_status)
        window._handle_auto_tid_run_state_changed(True)
        assert window.navigation_status.text() == "● TID · 运行中"
        window._handle_auto_tid_run_state_changed(False)

    assert window.navigation_status.text() == f"● {task} · {expected_phase}"
    assert window._header_loop_index == 0


def test_project_xs_reflows_only_on_narrow_window(app, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        MainWindow,
        "_screen_available_geometry",
        lambda _self: QRect(0, 0, 1920, 1080),
    )
    window = MainWindow(profile_settings=_settings(tmp_path))
    window.tabs.setCurrentWidget(window.project_xs_tab)
    window.show()
    window.resize(980, 640)
    app.processEvents()
    assert window.project_xs_splitter.orientation() == Qt.Orientation.Vertical

    window.resize(1280, 760)
    app.processEvents()

    assert window.project_xs_splitter.orientation() == Qt.Orientation.Horizontal


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
