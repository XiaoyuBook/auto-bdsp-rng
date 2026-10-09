from dataclasses import replace

import pytest
from PySide6.QtCore import QPoint, QRect
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from auto_bdsp_rng.blink_detection import load_project_xs_config, save_project_xs_config
from auto_bdsp_rng.ui import main_window as mw
from tests.test_start_readiness import window


def settle():
    for _ in range(4):
        QApplication.processEvents()
        QTest.qWait(10)


@pytest.mark.parametrize("size", [(860, 600), (1150, 900), (854, 480)])
def test_seed_form_scroll_keeps_capture_actions_and_video_threshold_reachable(window, monkeypatch, size):
    w = window
    monkeypatch.setattr(w, "_screen_available_geometry", lambda: QRect(0, 0, 2200, 1400))
    w.resize(*size)
    w.tabs.setCurrentWidget(w.project_xs_tab)
    settle()
    assert w.size().toTuple() == size
    assert w.project_xs_config_scroll.horizontalScrollBar().maximum() == 0
    assert w.capture_timing_group.isVisible()
    assert w.capture_timing_group.isAncestorOf(w.npc_count)
    assert not w.threshold.isVisible()
    overlay = w.video_overlay
    assert overlay.threshold.isVisible() and overlay.threshold.isEnabled()
    assert w.preview_label.contentsRect().contains(overlay.status_panel.geometry())
    overlay.threshold.setValue(.81)
    assert w._config_from_form().capture.threshold == .81
    w.project_xs_config_scroll.verticalScrollBar().setValue(
        w.project_xs_config_scroll.verticalScrollBar().maximum())
    settle()
    for control in (w.capture_button, w.tidsid_button, w.reidentify_button,
                    w.config_combo, w.browse_button, w.new_capture_config_button, w.save_config_button):
        rect = QRect(control.mapTo(w.project_xs_tab, QPoint()), control.size())
        assert control.isVisible() and w.project_xs_tab.rect().contains(rect)
    assert w.capture_button.mapTo(w, QPoint()).y() == w.tidsid_button.mapTo(w, QPoint()).y()
    assert w.reidentify_button.mapTo(w, QPoint()).y() > w.capture_button.mapTo(w, QPoint()).y()
    overlay.set_capture_progress(12, 40)
    assert not overlay.threshold.isEnabled()
    overlay.finish_capture()
    assert overlay.threshold.isEnabled()
    w._set_preview_selection_enabled(True)
    assert not overlay.status_panel.isVisible()
    w._set_preview_selection_enabled(False)
    assert overlay.threshold.isVisible()
    overlay.set_match_score(.98)
    w._clear_video_source_preview()
    assert overlay.score_value.text() == "—"
    assert overlay.threshold.isVisible() and overlay.threshold.isEnabled()


@pytest.mark.parametrize("imported", [False, True])
def test_new_config_saves_current_parameters_without_overwriting_original(window, monkeypatch, tmp_path, imported):
    w = window
    original = tmp_path / "原配置.json"
    output = tmp_path / "新配置.json"
    config = replace(w._config_from_form(), source_path=original)
    save_project_xs_config(config, original)
    monkeypatch.setattr(mw, "PROJECT_XS_CONFIGS", tmp_path)
    w._refresh_config_list()
    automation_path = original
    if imported:
        automation_path = tmp_path / "导入" / "自动流程配置.json"
        save_project_xs_config(replace(config, source_path=automation_path), automation_path)
        for combo in (w.seed_config_combo, w.reidentify_config_combo):
            combo.addItem(automation_path.name, str(automation_path))
            combo.setCurrentIndex(combo.count() - 1)
    original_bytes = original.read_bytes()
    w.npc_count.setText("3")
    w.white_delay.setValue(.8)
    w.advance_delay.setText("13")
    w.advance_delay_2.setText("27")
    w.timeline_npc.setText("2")
    w.pokemon_npc.setText("1")
    w.reidentify_1_pk_npc.setChecked(True)
    w.video_overlay.threshold.setValue(.83)
    monkeypatch.setattr(mw.QFileDialog, "getSaveFileName", lambda *args: (str(output), "JSON files (*.json)"))
    w.new_capture_config_button.click()
    saved = load_project_xs_config(output)
    assert original.read_bytes() == original_bytes
    assert saved.source_path == output
    assert (saved.npc, saved.white_delay, saved.advance_delay, saved.advance_delay_2,
            saved.timeline_npc, saved.pokemon_npc, saved.reidentify_1_pk_npc) == (3, .8, 13, 27, 2, 1, True)
    assert saved.capture.threshold == .83
    assert saved.capture.eye_image_path == config.capture.eye_image_path
    assert w._selected_config_path() == str(output)
    assert w.seed_config_combo.currentData() == str(automation_path)
    assert w.reidentify_config_combo.currentData() == str(automation_path)
    w.threshold.setValue(.79)
    w.save_config_button.click()
    assert load_project_xs_config(output).capture.threshold == .79
    assert original.read_bytes() == original_bytes


def test_cancel_new_config_keeps_selection_and_unsaved_values(window, monkeypatch):
    w = window
    selected = w._selected_config_path()
    w.npc_count.setText("7")
    monkeypatch.setattr(mw.QFileDialog, "getSaveFileName", lambda *args: ("", ""))
    w.new_capture_config_button.click()
    assert w._selected_config_path() == selected
    assert w.npc_count.text() == "7"
