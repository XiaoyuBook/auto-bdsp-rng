from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, QRect, QSettings, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QScrollArea

from auto_bdsp_rng import app_settings
from auto_bdsp_rng.automation.auto_rng.delay_strategy import DelayStrategy
from auto_bdsp_rng.ui import MainWindow
from auto_bdsp_rng.ui.auto_rng_panel import AutoRngPanel
from auto_bdsp_rng.ui.guide_steps import dialog_steps, workspace_step
from auto_bdsp_rng.ui.guide_tip import GuideTip
from tests.test_ui import app, isolated_ui_qsettings  # noqa: F401 -- reuse isolated GUI fixtures


@pytest.fixture
def guided(app, tmp_path, monkeypatch):
    monkeypatch.setattr(app_settings, "SETTINGS_PATH", tmp_path / "guide.json")
    window = MainWindow()
    monkeypatch.setattr(window, "_screen_available_geometry", lambda: QRect(0, 0, 2000, 1400))
    window.resize(1150, 760)
    window.show()
    QTest.qWait(50)
    window.guide_button.click()
    yield window, window.auto_rng_tab, window.guide_controller
    window.guide_controller.pause()


def in_dialog(controller, callback):
    """Run a bounded interaction inside the real modal exec loop; surface callback errors."""
    errors = []

    def interact():
        dialog = controller._dialog()
        try:
            assert controller.dialog_overlay.isVisible()
            callback(dialog)
        except BaseException as error:
            errors.append(error)
        finally:
            if dialog.isVisible():
                dialog.reject()

    QTimer.singleShot(100, interact)
    controller.next()
    QTest.qWait(30)
    if errors:
        raise errors[0]


def assert_body_text_is_readable(tip):
    for label in tip.body.findChildren(QLabel):
        if not label.wordWrap() or not label.isVisibleTo(tip.body):
            continue
        # Measure the same text with a fresh, unconstrained label. Checking only
        # the card bounds misses paragraphs clipped inside their own widgets.
        probe = QLabel(label.text())
        probe.setWordWrap(True)
        probe.setFont(label.font())
        required = probe.heightForWidth(label.width())
        probe.deleteLater()
        assert label.height() >= required, (label.text(), label.height(), required)
        bottom = label.mapTo(tip.body, label.rect().bottomLeft()).y()
        assert bottom < tip.body.height()
        assert bottom < tip.scroll.verticalScrollBar().maximum() + tip.scroll.viewport().height()


def test_guide_progress_keeps_legacy_session_and_atomic_position(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    app_settings.save_settings({"other": "保留"}, path)
    original = app_settings.start_guide_progress(path)
    changed = app_settings.advance_guide_progress("delay_strategy", "baseline", path)
    assert changed["session_id"] == original["session_id"]
    assert app_settings.get_guide_progress(path) == changed
    assert app_settings.load_settings(path)["other"] == "保留"

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(app_settings.os, "replace", fail)
    with pytest.raises(OSError):
        app_settings.advance_guide_progress("save_config", path=path)
    assert app_settings.get_guide_progress(path) == changed


def test_range_choices_custom_validation_skip_save_and_resume(guided):
    window, panel, controller = guided
    session = app_settings.get_guide_progress()["session_id"]
    controller.next()
    tip = controller.overlay.tip
    assert controller.step == "search_range"
    for name, expected in (("regular", 100_000), ("starter", 5000), ("rare", 10_000_000)):
        QTest.mouseClick(tip.presets[name], Qt.MouseButton.LeftButton)
        assert panel.max_advances.value() == expected
        assert controller.step == "search_range"
    tip.presets["custom"].click()
    assert tip.custom_input.text() == "10000000"
    QTest.keyClicks(tip.custom_input, "50000000")
    assert panel.max_advances.value() == 50_000_000
    QTest.keyClick(tip.custom_input, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    QTest.keyClick(tip.custom_input, Qt.Key.Key_Backspace)
    assert not tip.next_button.isEnabled() and not tip.skip_button.isEnabled()
    assert panel.max_advances.value() == 50_000_000
    QTest.keyClicks(tip.custom_input, "1000000001")
    assert not tip.next_button.isEnabled()
    tip.presets["rare"].click()
    assert tip.next_button.isEnabled()
    controller.pause()
    window.tabs.setCurrentWidget(window.easycon_tab)
    controller.begin_or_resume()
    assert controller.overlay.waiting_for_page
    assert app_settings.get_guide_progress()["session_id"] == session
    window.tabs.setCurrentWidget(panel)
    assert controller.step == "search_range" and panel.max_advances.value() == 10_000_000
    controller.skip()
    assert controller.step == "save_config"
    tip = controller.overlay.tip
    assert not tip.next_button.isEnabled()
    controller.next()
    assert controller.step == "save_config"
    panel.save_config_button.click()
    assert controller.overlay.title.text() == "配置已保存"
    assert tip.next_button.isEnabled() and controller.step == "save_config"
    assert int(panel._settings.value("max_advances")) == 10_000_000
    controller.next()
    assert controller.step == "task_configured"
    assert app_settings.get_guide_progress()["status"] == "in_progress"
    controller.pause()
    controller.begin_or_resume()
    assert controller.step == "task_configured" and tip.next_button.isEnabled()
    controller.next()
    assert controller.step == "connect_devices" and controller.detail == "video_source"
    controller.pause()
    controller.begin_or_resume()
    assert controller.step == "connect_devices" and controller.detail == "video_source"
    controller._go("task_configured")
    controller.previous()
    assert controller.step == "save_config" and not tip.next_button.isEnabled()
    controller._go("search_range")
    tip.presets["custom"].click()
    QTest.keyClick(tip.custom_input, Qt.Key.Key_Backspace)
    assert not tip.next_button.isEnabled()
    controller.restart()
    controller.next()
    assert tip.next_button.isEnabled() and tip.custom_container.isHidden()
    assert panel.max_advances.value() == 10_000_000


def test_device_connection_step_guides_video_source_then_easycon(guided, monkeypatch):
    window, panel, controller = guided
    controller._go("connect_devices")
    assert controller.detail == "video_source"
    assert controller.overlay.spec.caption == "第 3 步 · 连接设备"
    assert not controller.overlay.tip.next_button.isEnabled()

    window._video_source_connected = True
    controller._navigation()
    assert controller.overlay.tip.next_button.isEnabled()
    controller.next()
    assert controller.step == "connect_devices"
    assert controller.detail == "easycon"
    assert controller.overlay.spec.target is window.easycon_header_button
    assert not controller.overlay.tip.next_button.isEnabled()

    monkeypatch.setattr(window.easycon_tab, "_native_is_connected", lambda: True)
    controller._navigation()
    assert controller.overlay.tip.next_button.isEnabled()
    controller.next()
    assert controller.step == "devices_connected"


def test_delay_dialog_follows_strategy_and_keeps_edits_when_skipping(guided):
    window, panel, controller = guided
    controller.next()
    controller.next()
    assert controller.step == "delay_strategy"

    def check(dialog):
        assert controller.detail == "strategy"
        assert [spec.key for spec in dialog_steps(panel, "delay_strategy")] == ["strategy", "baseline", "confirm"]
        dialog.strategy_combo.setCurrentIndex(dialog.strategy_combo.findData(DelayStrategy.EWMA.value))
        QTest.qWait(40)
        assert [spec.key for spec in dialog_steps(panel, "delay_strategy")] == ["strategy", "baseline", "window", "multiple", "weight", "confirm"]
        controller.next()
        assert controller.detail == "baseline"
        dialog.baseline_delay.setValue(1234)
        QTest.keyClick(dialog.baseline_delay, Qt.Key.Key_Return)
        assert dialog.isVisible()  # Enter on a number must not accept the whole dialog.
        controller.next()
        controller.next()
        controller.next()
        assert controller.detail == "weight"
        dialog.ewma_weight_percent.setValue(70)
        controller.previous()
        assert controller.detail == "multiple"
        controller.skip()

    in_dialog(controller, check)
    assert controller.step == "save_config"
    assert panel.delay_strategy_config().baseline_delay == 1234
    assert panel.delay_strategy_config().ewma_alpha == .7
    assert not controller.overlay.tip.next_button.isEnabled()


def test_modal_detail_resumes_after_pause_and_preserves_same_session(guided):
    window, panel, controller = guided
    controller._go("correction_strategy")
    original = app_settings.get_guide_progress()["session_id"]

    def pause(dialog):
        assert controller.detail == "limit"
        controller.next()
        assert controller.detail == "attempts"
        controller.pause()
        assert not controller.dialog_overlay.tip.isVisible()

    in_dialog(controller, pause)
    assert app_settings.get_guide_progress()["detail"] == "attempts"
    errors = []

    def resumed():
        try:
            assert controller.dialog_overlay.isVisible()
            assert controller.detail == "attempts"
            assert app_settings.get_guide_progress()["session_id"] == original
            controller.skip()
        except BaseException as error:
            errors.append(error)
            panel.strategy_dialog.reject()

    QTimer.singleShot(120, resumed)
    controller.begin_or_resume()
    QTest.qWait(200)
    assert not errors
    assert controller.step == "save_config"


def test_correction_guide_conditional_fields_limit_and_confirm(guided):
    window, panel, controller = guided
    controller._go("correction_strategy")
    assert panel.more_strategy_button.isChecked()

    def check(dialog):
        assert "100 万" in controller.dialog_overlay.title.text()
        dialog.reseed_threshold_frames.setValue(1_500_000)
        assert dialog.reseed_threshold_frames.value() == 1_000_000
        controller.next()
        controller.next()
        assert controller.detail == "failure"
        dialog.set_policy("recapture_seed")
        QTest.qWait(30)
        controller.next()
        assert controller.detail == "seed_attempts"
        controller.next()
        assert controller.detail == "reserve"
        assert "50 万" in controller.dialog_overlay.copy.text()
        assert "设为 0" in controller.dialog_overlay.copy.text()
        dialog.reseeding_threshold.setValue(321_000)
        controller.next()
        assert controller.detail == "confirm"
        dialog.ok_button.click()

    in_dialog(controller, check)
    assert controller.step == "save_config" and controller.overlay.isVisible()
    assert panel.reseeding_threshold.value() == 321_000
    assert not controller.overlay.tip.next_button.isEnabled()


def test_each_workspace_anchor_survives_resize_and_hidden_strategy_rows(guided):
    window, panel, controller = guided
    for width, height in ((1150, 760), (860, 600)):
        window.resize(width, height)
        for key in app_settings.GUIDE_STEPS:
            controller._go(key)
            QTest.qWait(40)
            overlay = controller.overlay
            assert overlay.isVisible(), (width, key)
            assert overlay.rect().contains(overlay.tip.geometry()), (width, key)
            assert not overlay.hole.intersects(overlay.tip.geometry()), (width, key)
            center = overlay.mapFromGlobal(overlay.focus_target.mapToGlobal(overlay.focus_target.rect().center()))
            assert overlay.hole.contains(center), (width, key)
            assert not overlay.mask().contains(center), (width, key)
            assert overlay.tip.rect().contains(overlay.tip.next_button.geometry())
            assert_body_text_is_readable(overlay.tip)


def test_guide_text_survives_scrollbars_width_changes_and_shorter_steps(guided):
    window, panel, controller = guided
    controller.pause()
    tip = GuideTip(window.centralWidget())
    tip.range_field = panel.max_advances
    tip.show()
    for width in (338, 280, 400):
        tip.setFixedWidth(width)
        for key in ("max_wait", "sync", "auto_reverse", "search_range"):
            tip.show_step(workspace_step(panel, key), search=key == "search_range")
            if key == "search_range":
                tip.presets["custom"].click()
            tip.fit_height(230)
            QTest.qWait(20)
            assert_body_text_is_readable(tip)
            scrollbar = tip.scroll.verticalScrollBar()
            assert scrollbar.maximum() > 0
            scrollbar.setValue(scrollbar.maximum())
            QTest.qWait(20)
            bottom = tip.body.mapTo(tip.scroll.viewport(), tip.body.rect().bottomLeft()).y()
            assert bottom < tip.scroll.viewport().height()
            assert tip.rect().contains(tip.next_button.geometry())
        tip.show_step(workspace_step(panel, "save_config"))
        tip.fit_height(700)
        QTest.qWait(20)
        assert_body_text_is_readable(tip)
        assert tip.scroll.verticalScrollBar().maximum() == 0
        assert tip.height() < 350
    tip.hide()


def test_workspace_guide_scrolls_hidden_advanced_target_into_view(guided):
    window, panel, controller = guided
    window.resize(860, 600)
    config_scroll = next(area for area in panel.findChildren(QScrollArea) if area.objectName() == "AutoRngConfigPanel")
    config_scroll.verticalScrollBar().setValue(0)
    controller._go("correction_strategy")
    QTest.qWait(220)
    bar = config_scroll.verticalScrollBar()
    target = panel.strategy_settings_button
    target_top = target.mapTo(config_scroll.widget(), QPoint()).y()
    target_bottom = target_top + target.height()
    assert bar.value() > 0
    assert target_top >= bar.value()
    assert target_bottom <= bar.value() + config_scroll.viewport().height()
    assert controller.overlay.hole.contains(
        controller.overlay.mapFromGlobal(target.mapToGlobal(target.rect().center()))
    )


def test_switching_and_resizing_keeps_current_anchor_clickable(guided):
    window, panel, controller = guided
    area = panel.findChild(QScrollArea, "AutoRngConfigPanel")
    window.resize(860, 600)
    for key in ("correction_strategy", "save_config", "search_range", "sync", "max_wait"):
        controller._go(key)
    # Old steps must not scroll back to their own targets after a fast switch.
    QTest.qWait(650)
    for key in ("max_wait", "correction_strategy", "search_range"):
        controller._go(key)
        window.resize(860, 650)
        QTest.qWait(50)
        window.resize(860, 600)
        QTest.qWait(50)
        overlay = controller.overlay
        target = overlay.focus_target
        center = target.mapToGlobal(target.rect().center())
        assert area.viewport().rect().contains(area.viewport().mapFromGlobal(center))
        assert overlay.hole.contains(overlay.mapFromGlobal(center))
        assert not overlay.mask().contains(overlay.mapFromGlobal(center))
        hit = window.childAt(window.mapFromGlobal(center))
        assert hit is target or target.isAncestorOf(hit)


@pytest.mark.parametrize("key, height", [("delay_strategy", None), ("delay_strategy", 500), ("correction_strategy", None)])
def test_dialog_guide_stays_inside_dialog_and_restores_layout(guided, key, height):
    window, panel, controller = guided
    dialog = panel.delay_strategy_dialog if key == "delay_strategy" else panel.strategy_dialog
    original_minimum = dialog.minimumHeight()
    original_alignment = dialog.layout().alignment()
    controller._go(key)

    def check(dialog):
        if key == "delay_strategy":
            if height is not None:
                dialog.setMaximumHeight(height)
            dialog.strategy_combo.setCurrentIndex(dialog.strategy_combo.findData(DelayStrategy.EWMA.value))
            QTest.qWait(30)
        for spec in dialog_steps(panel, key):
            assert controller.detail == spec.key
            QTest.qWait(30)
            overlay = controller.dialog_overlay
            tip = overlay.tip
            assert tip.isVisible() and not tip.isWindow()
            assert dialog.isAncestorOf(tip)
            assert overlay.rect().adjusted(16, 16, -16, -16).contains(tip.geometry())
            assert not overlay.hole.intersects(tip.geometry())
            assert_body_text_is_readable(tip)
            center = spec.target.mapToGlobal(spec.target.rect().center())
            assert overlay.hole.contains(overlay.mapFromGlobal(center))
            for area in dialog.findChildren(QScrollArea):
                if area.widget() is not None and area.widget().isAncestorOf(spec.target):
                    assert area.viewport().rect().contains(area.viewport().mapFromGlobal(center))
            hit = dialog.childAt(dialog.mapFromGlobal(center))
            assert hit is spec.target or spec.target.isAncestorOf(hit)
            button = tip.next_button
            # Exercise the embedded card, including its real pointer hit path.
            assert dialog.childAt(button.mapTo(dialog, button.rect().center())) is button
            if spec.key != "confirm":
                QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        controller.pause()
        assert dialog.isVisible() and not controller.dialog_overlay.isVisible()
        assert dialog.minimumHeight() == original_minimum
        assert dialog.layout().alignment() == original_alignment

    in_dialog(controller, check)


def test_save_failure_does_not_complete_and_dirty_edit_requires_resave(guided, monkeypatch):
    window, panel, controller = guided
    controller._go("save_config")
    saved = []
    panel.configSaved.connect(lambda: saved.append(True))
    with monkeypatch.context() as scope:
        scope.setattr(panel._settings, "status", lambda: QSettings.Status.AccessError)
        panel.save_config_button.click()
        assert not saved
        assert controller.overlay.title.text() == "配置尚未保存"
        assert not controller.overlay.tip.next_button.isEnabled()
    panel.save_config_button.click()
    assert saved == [True] and controller.overlay.tip.next_button.isEnabled()
    panel.max_advances.setValue(panel.max_advances.value() + 1)
    assert not controller.overlay.tip.next_button.isEnabled()
    assert controller.step == "save_config"


def test_legacy_over_limit_is_clamped_without_eagerly_saving(app, tmp_path):
    settings = QSettings(str(tmp_path / "legacy.ini"), QSettings.Format.IniFormat)
    settings.setValue("reseed_threshold_frames", 9_000_000)
    settings.sync()
    panel = AutoRngPanel(script_dir=tmp_path, settings=settings)
    assert panel.reseed_threshold_frames.value() == 1_000_000
    assert panel.build_config().reseed_threshold_frames == 1_000_000
    assert int(settings.value("reseed_threshold_frames")) == 9_000_000
    panel.save_config_button.click()
    assert int(settings.value("reseed_threshold_frames")) == 1_000_000
