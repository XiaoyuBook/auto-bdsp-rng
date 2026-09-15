from __future__ import annotations

import shutil

import numpy as np
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from auto_bdsp_rng import app_settings
from auto_bdsp_rng.blink_detection import ProjectXsIntegrationError
from auto_bdsp_rng.ui import main_window as mw
from tests.test_guide_flow import guided, assert_body_text_is_readable  # noqa: F401
from tests.test_ui import app, isolated_ui_qsettings  # noqa: F401
from tests.test_startup_webview import evaluate, wait_until


@pytest.fixture
def practice(guided, tmp_path, monkeypatch):
    window, panel, controller = guided
    config = tmp_path / "practice.json"
    shutil.copyfile(window._selected_config_path(), config)
    monkeypatch.setattr(window, "_selected_config_path", lambda: str(config))
    original_resource = mw.resource_path
    monkeypatch.setattr(mw, "resource_path", lambda *parts: tmp_path / "eyes" if parts[-2:] == ("images", "custom") else original_resource(*parts))
    monkeypatch.setattr("auto_bdsp_rng.blink_detection.project_xs._load_eye_template", lambda _: np.zeros((8, 8), dtype=np.uint8))
    monkeypatch.setattr(window, "_confirm_preview_selection", lambda _: True)
    errors = []
    monkeypatch.setattr(window, "_show_error", lambda *args: errors.append(args))
    window._latest_preview_frame = np.full((240, 320, 3), 80, dtype=np.uint8)
    window._display_frame(window._latest_preview_frame)
    window.tabs.setCurrentWidget(window.project_xs_tab)
    controller._go("seed_capture_tools", "roi_button")
    QTest.qWait(50)
    return window, controller, config, errors


def drag(window, region):
    label = window.preview_label
    area = label._pixmap_rect
    x, y, w, h = region
    start = QPoint(area.x() + round(x / 320 * area.width()), area.y() + round(y / 240 * area.height()))
    end = QPoint(area.x() + round((x + w) / 320 * area.width()) - 1, area.y() + round((y + h) / 240 * area.height()) - 1)
    QTest.mousePress(label, Qt.MouseButton.RightButton, pos=start)
    QTest.mouseMove(label, end)
    QTest.mouseRelease(label, Qt.MouseButton.RightButton, pos=end)
    QTest.qWait(50)


def select_roi(window, controller):
    window.select_roi_button.click()
    QTest.qWait(50)
    assert controller.detail == "roi_drag"
    assert controller.overlay.spec.target is window.preview_label
    drag(window, (20, 30, 100, 110))
    assert controller.detail == "eye_button"


def test_real_right_drags_preserve_roi_then_require_successful_save(practice, monkeypatch):
    window, controller, config, errors = practice
    select_roi(window, controller)
    selected = tuple(int(f.text()) for f in (window.x, window.y, window.w, window.h))
    window.raw_screenshot_button.click()
    QTest.qWait(50)
    assert controller.detail == "eye_drag"
    drag(window, (35, 50, 25, 30))
    assert controller.step == "seed_capture_save"
    assert window._selection_mode is None
    assert tuple(int(f.text()) for f in (window.x, window.y, window.w, window.h)) == selected
    assert window._eye_image_path.is_file()
    before = config.read_bytes()
    controller.next()
    assert controller.step == "seed_capture_save"
    with monkeypatch.context() as patch:
        def fail(*_):
            raise ProjectXsIntegrationError("disk full")
        patch.setattr(mw, "save_project_xs_config", fail)
        window.save_config_button.click()
        assert not controller.overlay.tip.next_button.isEnabled()
        assert config.read_bytes() == before
    window.save_config_button.click()
    assert controller.eye_guide.config_saved and config.read_bytes() != before
    window.threshold.setValue(.71)
    assert not controller.overlay.tip.next_button.isEnabled()
    window.save_config_button.click()
    controller.next()
    assert controller.step == "auto_flow_config"
    assert len(errors) == 1


def test_eye_outside_roi_requires_another_valid_roi(practice):
    window, controller, _, errors = practice
    select_roi(window, controller)
    window.raw_screenshot_button.click()
    QTest.qWait(50)
    drag(window, (140, 50, 25, 30))
    assert controller.detail == "roi_adjust" and window._selection_mode == "roi"
    drag(window, (130, 40, 75, 80))
    assert controller.step == "seed_capture_save" and not errors


def test_cancel_failure_pause_resume_and_previous_do_not_claim_a_selection(practice, monkeypatch):
    window, controller, _, errors = practice
    session = app_settings.get_guide_progress()["session_id"]
    window.select_roi_button.click()
    QTest.qWait(50)
    with monkeypatch.context() as patch:
        patch.setattr(window, "_confirm_preview_selection", lambda _: False)
        drag(window, (20, 30, 100, 110))
    assert controller.detail == "roi_button"
    window.select_roi_button.click()
    QTest.qWait(50)
    drag(window, (20, 30, 2, 2))
    assert controller.detail == "roi_button" and errors
    window.select_roi_button.click()
    QTest.qWait(50)
    controller.pause()
    QTest.qWait(20)
    assert window._selection_mode is None
    assert app_settings.get_guide_progress()["detail"] == "roi_drag"
    controller.begin_or_resume()
    assert controller.detail == "roi_button"
    assert app_settings.get_guide_progress()["session_id"] == session
    select_roi(window, controller)
    controller.previous()
    assert controller.detail == "roi_button"


def test_demo_close_resume_and_failed_progress_write(guided, monkeypatch):
    window, _, controller = guided
    window.tabs.setCurrentWidget(window.project_xs_tab)
    controller._go("seed_capture_tools", "demo")
    dialog = controller.eye_guide.dialog
    wait_until(lambda: dialog.ready)
    before = app_settings.get_guide_progress()
    with monkeypatch.context() as patch:
        def fail(*_):
            raise OSError("disk full")
        patch.setattr(app_settings.os, "replace", fail)
        evaluate(dialog, "document.querySelector('.learned').click()")
        wait_until(lambda: evaluate(dialog, "document.querySelector('.error').textContent").startswith("无法保存"))
        assert not dialog.submitting
        assert controller.detail == "demo" and dialog.isVisible()
        assert app_settings.get_guide_progress() == before
    dialog.reject()
    assert not controller.active
    controller.begin_or_resume()
    dialog = controller.eye_guide.dialog
    wait_until(lambda: dialog.ready)
    evaluate(dialog, "document.querySelector('.learned').click()")
    wait_until(lambda: controller.eye_guide.dialog is None)
    QTest.qWait(50)
    assert controller.detail == "roi_button"
    assert controller.overlay.spec.target is window.select_roi_button
    assert window._selection_mode is None


@pytest.mark.parametrize("width,height", [(1150, 760), (860, 600)])
def test_practice_spotlight_reaches_visible_preview_without_covering_it(practice, width, height):
    window, controller, _, _ = practice
    window.resize(width, height)
    window.select_roi_button.click()
    QTest.qWait(150)
    overlay = controller.overlay
    assert controller.detail == "roi_drag"
    assert overlay.tip.isVisible()
    assert overlay.rect().contains(overlay.tip.geometry())
    assert not overlay.hole.intersects(overlay.tip.geometry())
    center = overlay.mapFromGlobal(window.preview_label.mapToGlobal(window.preview_label.rect().center()))
    assert overlay.hole.contains(center)
    assert not overlay.mask().contains(center)
    assert_body_text_is_readable(overlay.tip)
