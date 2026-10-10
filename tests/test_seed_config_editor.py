from dataclasses import replace
from types import SimpleNamespace
import time

import cv2
import numpy as np
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QPoint

from auto_bdsp_rng.blink_detection import load_project_xs_config, save_project_xs_config
from auto_bdsp_rng.blink_detection.models import BlinkObservation
from auto_bdsp_rng.rng_core import SeedState32
from auto_bdsp_rng.ui import main_window as mw
from tests.test_start_readiness import window


@pytest.fixture
def configs(window, monkeypatch, tmp_path):
    template = tmp_path / "eye.png"
    assert cv2.imwrite(str(template), np.full((8, 8), 127, dtype=np.uint8))
    config = window._config_from_form()
    config = replace(config, capture=replace(config.capture, eye_image_path=template, roi=(0, 0, 40, 40)),
                     reidentify_1_pk_npc=False)
    for name, npc in (("a.json", 1), ("b.json", 9)):
        path = tmp_path / name
        save_project_xs_config(replace(config, source_path=path, npc=npc), path)
    monkeypatch.setattr(mw, "PROJECT_XS_CONFIGS", tmp_path)
    window._refresh_config_list()
    return tmp_path / "a.json", tmp_path / "b.json", template


def binding_for(w, kind):
    if kind == "tid":
        return w.auto_tid_rng_tab, w.auto_tid_rng_tab.seed_config_binding
    return w.auto_rng_tab, getattr(w.auto_rng_tab, "reidentify_config_binding" if kind == "reidentify" else "seed_config_binding")


def open_editor(w, kind, path):
    panel, binding = binding_for(w, kind)
    w.tabs.setCurrentWidget(panel)
    binding.combo.setCurrentIndex(binding.combo.findData(str(path)))
    if panel is w.auto_rng_tab:
        panel.config_groups.reveal(binding)
    binding.edit_button.click()
    QApplication.processEvents()
    QApplication.processEvents()
    return panel, binding, w.seed_config_editor


@pytest.mark.parametrize("kind", ["seed", "tid", "reidentify"])
def test_editor_stays_in_task_and_cancels_without_changing_shared_files(window, configs, kind):
    first, second, _template = configs
    before = second.read_bytes()
    window.npc_count.setText("12")  # A separate unsaved manual-tool draft.
    panel, binding, editor = open_editor(window, kind, second)
    assert window.tabs.currentWidget() is panel
    assert binding.isAncestorOf(editor.editor)
    assert editor.editor.isVisible()
    scroll = panel.config_scroll if kind == "tid" else panel.config_panel
    assert 0 <= editor.title.mapTo(scroll.viewport(), QPoint(0, 0)).y() < 140
    assert "b.json" in editor.title.text()
    assert window.npc_count.text() == "9"
    assert not window.capture_timing_group.isVisible()
    assert not panel.start_button.isEnabled()
    assert not binding.combo.isEnabled()
    assert editor.save_button.isEnabled()
    assert window.monitor_sidebar.isVisible()
    editor.advanced_toggle.setChecked(True)
    assert window.capture_timing_group.isVisible()
    window.npc_count.setText("31")
    window.threshold.setValue(.73)
    editor.cancel_button.click()
    assert second.read_bytes() == before
    assert window.npc_count.text() == "12"
    assert window._selected_config_path() == str(first)
    assert not editor.active
    assert binding.combo.isEnabled()
    assert window.project_xs_config_scroll.isAncestorOf(window.capture_timing_group)


def test_save_changes_same_shared_file_and_preserves_independent_correction(window, configs):
    first, second, _template = configs
    _panel, binding, editor = open_editor(window, "seed", second)
    window.npc_count.setText("17")
    window.threshold.setValue(.81)
    editor.save_button.click()
    saved = load_project_xs_config(second)
    assert saved.npc == 17 and saved.capture.threshold == .81
    assert window.auto_tid_rng_tab.seed_config_binding.combo.currentData() == str(second)
    assert window.auto_rng_tab.reidentify_config_binding.combo.currentData() == str(first)
    assert not editor.active
    assert binding.combo.isEnabled()
    assert window.auto_rng_tab.start_button.isEnabled()


def test_save_failure_keeps_edit_open_and_file_intact(window, configs, monkeypatch):
    _first, second, _template = configs
    before = second.read_bytes()
    _panel, _binding, editor = open_editor(window, "seed", second)
    window.npc_count.setText("17")
    def fail(*_args):
        raise OSError("write denied")
    monkeypatch.setattr(mw, "save_project_xs_config", fail)
    monkeypatch.setattr(window, "_show_error", lambda *_args: None)
    editor.save_button.click()
    assert editor.active and editor.editor.isVisible()
    assert "write denied" in editor.status.text()
    assert window.npc_count.text() == "17"
    assert second.read_bytes() == before


@pytest.mark.parametrize("saved", [False, True])
def test_eye_selection_uses_draft_and_cancel_preserves_existing_template(window, configs, monkeypatch, tmp_path, saved):
    _first, second, template = configs
    original_image = template.read_bytes()
    original_config = second.read_bytes()
    output_dir = tmp_path / "eyes"
    monkeypatch.setattr(mw, "resource_path", lambda *_args: output_dir)
    _panel, _binding, editor = open_editor(window, "seed", second)
    window._latest_preview_frame = np.full((40, 40, 3), 255, dtype=np.uint8)
    window.apply_selected_eye((4, 4, 8, 8), preserve_roi=True)
    draft = window._eye_image_path
    assert draft != template and draft.is_file()
    assert template.read_bytes() == original_image
    if saved:
        editor.save_button.click()
        assert load_project_xs_config(second).capture.eye_image_path == draft
        assert draft.is_file()
    else:
        editor.cancel_button.click()
        assert second.read_bytes() == original_config
        assert not draft.exists()
    assert template.read_bytes() == original_image


@pytest.mark.parametrize("kind", ["seed", "tid", "reidentify"])
def test_capture_test_uses_unsaved_config_through_existing_pipeline(window, configs, monkeypatch, kind):
    _first, second, _template = configs
    before = second.read_bytes()
    _panel, _binding, editor = open_editor(window, kind, second)
    window.threshold.setValue(.67)
    window.npc_count.setText("3")
    state = SeedState32(1, 2, 3, 4)
    for field, word in zip(window.seed32_inputs, state.format_words()):
        field.setText(word)
    captures = []
    def capture(config, **_kwargs):
        captures.append(config)
        return BlinkObservation.from_sequences([1, 0], [12, 24], offset_time=time.perf_counter())
    monkeypatch.setattr(mw, "capture_player_blinks", capture)
    monkeypatch.setattr(mw, "capture_pokemon_blinks", capture)
    monkeypatch.setattr(mw, "recover_seed_from_observation", lambda *_args, **_kw: SimpleNamespace(state=state))
    monkeypatch.setattr(mw, "recover_tidsid_seed_from_observation", lambda *_args: SimpleNamespace(state=state))
    monkeypatch.setattr(window, "_reidentify_from_observation", lambda *_args, **_kw: SimpleNamespace(state=state, advances=25))
    monkeypatch.setattr(window, "_ensure_preview_frame_before_capture", lambda: True)
    editor.test_button.click()
    window._capture_thread.join(timeout=2)
    assert not window._capture_thread.is_alive()
    window._poll_capture_thread()
    assert captures[0].threshold == .67
    assert captures[0].blink_count == {"seed": 40, "tid": 64, "reidentify": 7}[kind]
    assert "成功" in editor.status.text()
    assert editor.active and editor.save_button.isEnabled()
    assert second.read_bytes() == before


def test_tools_entry_opens_manual_capture_without_primary_navigation(window):
    window.tabs.setCurrentWidget(window.auto_tid_rng_tab)
    index = window.tabs.indexOf(window.project_xs_tab)
    assert not window.tabs.tabBar().isTabVisible(index)
    window.manual_seed_action.trigger()
    QApplication.processEvents()
    assert window.tabs.currentWidget() is window.project_xs_tab
    assert window.capture_button.isVisible() and window.tidsid_button.isVisible()
    assert window.reidentify_button.isVisible()
    window.manual_seed_return_button.click()
    assert window.tabs.currentWidget() is window.auto_tid_rng_tab


def test_switching_edit_targets_keeps_current_draft_and_blocks_start(window, configs):
    first, second, _template = configs
    _panel, binding, editor = open_editor(window, "seed", second)
    window.npc_count.setText("27")
    window._edit_automation_config(str(first), window.auto_rng_tab.reidentify_config_binding)
    assert editor.binding is binding
    assert window.npc_count.text() == "27"
    assert window._selected_config_path() == str(second)
    assert not window._ensure_automation_start_available()
    editor.cancel_button.click()
    assert window._ensure_automation_start_available()


def test_saved_manual_tool_index_restores_to_primary_task(window):
    window._profile_settings.setValue(mw.MAIN_WINDOW_CURRENT_TAB_KEY, window.tabs.indexOf(window.project_xs_tab))
    window._restore_window_geometry()
    assert window.tabs.currentWidget() is window.auto_rng_tab


def test_cancel_restores_imported_windows_path_spelling_and_manual_draft(window, configs):
    first, second, _template = configs
    imported_path = first.as_posix()
    window.config_combo.addItem(first.name, imported_path)
    window.config_combo.setCurrentIndex(window.config_combo.count() - 1)
    window.npc_count.setText("11")
    _panel, _binding, editor = open_editor(window, "seed", second)
    editor.cancel_button.click()
    assert window._selected_config_path() == imported_path
    assert window._config_from_form().npc == 11
