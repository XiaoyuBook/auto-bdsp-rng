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
from auto_bdsp_rng.automation.auto_rng import starter_scripts
from tests.test_starter_automation_ui import select
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
    assert window.capture_timing_group.isVisible()
    assert not window.threshold.isVisible()
    assert window.video_overlay.threshold.isVisible()
    assert not panel.start_button.isEnabled()
    assert not binding.combo.isEnabled()
    assert editor.save_button.isEnabled()
    assert window.monitor_sidebar.isVisible()
    editor.advanced_toggle.setChecked(False)
    assert not window.capture_timing_group.isVisible()
    editor.advanced_toggle.setChecked(True)
    assert window.capture_timing_group.isVisible()
    window.npc_count.setText("31")
    window.video_overlay.threshold.setValue(.73)
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
    window.video_overlay.threshold.setValue(.81)
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


@pytest.fixture
def starter_file(window, configs, monkeypatch):
    first, second, _template = configs
    path = first.parent / "config_starter.json"
    original = replace(load_project_xs_config(second), source_path=path,
                       white_delay=.25, advance_delay=99, npc=9)
    save_project_xs_config(original, path)
    monkeypatch.setattr(starter_scripts, "resource_path", lambda *_parts: path)
    window._refresh_config_list()
    window.seed_config_combo.setCurrentIndex(window.seed_config_combo.findData(str(second)))
    return path


@pytest.mark.parametrize("species", [387, 390, 393])
def test_starter_pins_its_seed_file_without_changing_tid_or_normal_selection(window, configs, starter_file, species):
    _first, second, _template = configs
    panel = window.auto_rng_tab
    binding = panel.seed_config_binding
    select(panel, species)
    panel.starter_automation_check.setChecked(True)
    assert panel.build_config().seed_config_path == str(starter_file)
    assert binding.combo.currentData() == str(starter_file)
    assert not binding.combo.isEnabled() and binding.edit_button.isEnabled()
    window._refresh_config_list()
    QApplication.processEvents()
    assert binding.combo.count() == 1 and binding.combo.currentData() == str(starter_file)
    assert window._selected_auto_seed_config_path() == str(second)
    assert window.auto_tid_rng_tab.seed_config_binding.combo.currentData() == str(second)
    assert "怎么回事？刚才那两人" in panel.starter_script_description.text()
    select(panel, 480)
    assert not panel.starter_automation_check.isChecked()
    assert binding.combo.isEnabled() and binding.combo.currentData() == str(second)


@pytest.mark.parametrize("saved", [False, True])
def test_starter_editor_only_changes_roi_and_threshold_and_restores_normal_editing(window, configs, starter_file, saved, monkeypatch):
    _first, second, template = configs
    original = load_project_xs_config(starter_file)
    before = starter_file.read_bytes()
    manual_before = second.read_bytes()
    template_before = template.read_bytes()
    errors = []
    monkeypatch.setattr(window, "_show_error", lambda title, error: errors.append(error))
    panel = window.auto_rng_tab
    select(panel, 390)
    panel.starter_automation_check.setChecked(True)
    panel, binding, editor = open_editor(window, "seed", starter_file)
    assert editor.starter_locked
    assert window.select_roi_button.isEnabled() and window.video_overlay.threshold.isEnabled()
    assert not window.capture_timing_group.isEnabled()
    assert not window.npc_count.isEnabled() and not window.raw_screenshot_button.isEnabled()
    assert not panel.starter_automation_check.isEnabled()
    editor.refresh()
    assert not window.capture_timing_group.isEnabled()
    assert (window._config_from_form().npc, window._config_from_form().advance_delay) == (1, 41)
    window.video_overlay.threshold.setValue(.79)
    window._latest_preview_frame = np.full((40, 40, 3), 127, dtype=np.uint8)
    roi = (4, 6, 30, 28)
    window.apply_selected_roi(roi)
    # Even a stale signal cannot change a fixed template or non-editable field.
    window.apply_selected_eye((0, 0, 8, 8))
    window.npc_count.setText("invalid")
    window.camera.setText("invalid")
    window._eye_image_path = template.with_name("wrong.png")
    assert window._config_from_form().capture.eye_image_path == template
    if saved:
        editor.save_button.click()
        assert load_project_xs_config(starter_file) == replace(
            original, capture=replace(original.capture, roi=roi, threshold=.79),
        )
    else:
        editor.cancel_button.click()
        assert starter_file.read_bytes() == before
    assert not editor.active
    assert not binding.combo.isEnabled() and binding.edit_button.isEnabled()
    assert window.auto_tid_rng_tab.seed_config_binding.combo.currentData() == str(second)
    assert second.read_bytes() == manual_before and template.read_bytes() == template_before
    panel.starter_automation_check.setChecked(False)
    assert binding.combo.isEnabled() and binding.combo.currentData() == str(second)
    _panel, _binding, editor = open_editor(window, "seed", second)
    assert not errors
    assert not editor.starter_locked
    assert window.npc_count.isEnabled() and window.raw_screenshot_button.isEnabled()
    editor.cancel_button.click()


def test_missing_starter_config_does_not_fall_back_to_another_seed_file(window, configs, starter_file, monkeypatch):
    from tests.test_start_readiness import checks

    missing = starter_file.parent / "missing" / "config_starter.json"
    monkeypatch.setattr(starter_scripts, "resource_path", lambda *_parts: missing)
    panel = window.auto_rng_tab
    select(panel, 387)
    panel.starter_automation_check.setChecked(True)
    assert window._selected_auto_rng_seed_config_path() == str(missing)
    assert panel.seed_config_binding.combo.currentData() == str(missing)
    assert not panel.seed_config_binding.combo.isEnabled()
    seed = checks(window)["seed"]
    assert seed.state == "blocked" and "config_starter.json" in seed.detail
