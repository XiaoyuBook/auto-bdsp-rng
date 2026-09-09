from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from auto_bdsp_rng.ui.auto_rng_panel import AutoRngPanel
from auto_bdsp_rng.ui.auto_tid_rng_panel import AutoTidRngPanel


@pytest.fixture(params=[AutoRngPanel, AutoTidRngPanel], ids=["static", "tid"])
def editor(request, tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    for name, content in (
        ("BDSP测种.txt", "A 100\n"),
        ("bdsp过帧.txt", "_目标帧数 = 100\n"),
        ("谢米.txt", "_瞬移精灵槽位 = 1\nA 100\n"),
        ("取名.txt", "A 100\n"),
        ("备用测种.txt", "B 100\n"),
    ):
        (tmp_path / name).write_text(content, encoding="utf-8")
    settings = QSettings(str(tmp_path / "panel.ini"), QSettings.Format.IniFormat)
    panel = request.param(script_dir=tmp_path, settings=settings)
    if isinstance(panel, AutoTidRngPanel):
        panel.add_target_display_tid(1)
        field, key = panel.frame_threshold, "frame_threshold"
        config_save, config_label = panel.save_button, panel.save_state_label
        combos = (panel.seed_script_combo, panel.name_script_combo, panel.reverse_id_script_combo)
    else:
        panel.hit_script_combo.setCurrentIndex(panel.hit_script_combo.findText("谢米.txt"))
        field, key = panel.max_advances, "max_advances"
        config_save, config_label = panel.save_config_button, panel.config_saved_label
        combos = panel._script_combos()
    panel._save_panel_state()
    restored_panels = []

    def restore():
        restored = request.param(
            script_dir=tmp_path,
            settings=QSettings(settings.fileName(), QSettings.Format.IniFormat),
        )
        restored_panels.append(restored)
        return restored

    yield SimpleNamespace(
        panel=panel, settings=settings, field=field, key=key, combos=combos,
        config_save=config_save, config_label=config_label, restore=restore,
        original=tmp_path / "BDSP测种.txt", alternate=tmp_path / "备用测种.txt",
    )
    for widget in [panel, *restored_panels]:
        widget.set_preparing(False)
        widget.close()
        widget.deleteLater()
    app.processEvents()


def test_script_save_persists_only_script_section(editor):
    e, panel = editor, editor.panel
    original_value = e.field.value()
    e.field.setValue(original_value + 10)
    panel.seed_script_combo.setCurrentIndex(panel.seed_script_combo.findData(str(e.alternate)))
    if isinstance(panel, AutoRngPanel):
        panel.escape_continue_check.setChecked(True)
    panel.save_scripts_button.click()

    assert e.settings.value("seed_script") == str(e.alternate)
    assert int(e.settings.value(e.key)) == original_value
    assert e.config_label.text() == "有未保存修改"
    assert panel.script_save_state_label.text() == "已保存"
    assert not panel.save_scripts_button.isEnabled()
    restored = e.restore()
    assert restored.seed_script_combo.currentData() == str(e.alternate)
    assert getattr(restored, e.key).value() == original_value
    if isinstance(panel, AutoRngPanel):
        assert restored.escape_continue_check.isChecked()


def test_config_save_leaves_script_choice_unsaved(editor):
    e, panel = editor, editor.panel
    e.field.setValue(e.field.value() + 10)
    panel.seed_script_combo.setCurrentIndex(panel.seed_script_combo.findData(str(e.alternate)))
    e.config_save.click()

    assert int(e.settings.value(e.key)) == e.field.value()
    assert e.settings.value("seed_script") == str(e.original)
    assert e.config_label.text() == "已保存"
    assert panel.script_save_state_label.text() == "有未保存修改"
    assert panel.save_scripts_button.isEnabled()
    if isinstance(panel, AutoRngPanel):
        assert panel.toolbar_status.text() == "有未保存修改"

    panel.refresh_scripts()
    assert panel.seed_script_combo.currentData() == str(e.alternate)
    assert panel.script_save_state_label.text() == "有未保存修改"
    panel.seed_script_combo.setCurrentIndex(panel.seed_script_combo.findData(str(e.original)))
    assert panel.script_save_state_label.text() == "已保存"
    assert not panel.save_scripts_button.isEnabled()


def test_saved_empty_script_choices_override_defaults_on_reopen(editor):
    e, panel = editor, editor.panel
    for combo in e.combos:
        combo.setCurrentIndex(0)
    panel.save_scripts_button.click()
    restored = e.restore()
    combos = (restored._script_combos() if isinstance(restored, AutoRngPanel) else
              (restored.seed_script_combo, restored.name_script_combo, restored.reverse_id_script_combo))
    assert all(combo.currentData() is None for combo in combos)
    restored.refresh_scripts()
    assert all(combo.currentData() is None for combo in combos)
    assert restored.script_save_state_label.text() == "已保存"


@pytest.mark.parametrize("action", ["start", "close"])
def test_start_and_close_still_save_both_sections(editor, action):
    e, panel = editor, editor.panel
    e.field.setValue(e.field.value() + 10)
    panel.seed_script_combo.setCurrentIndex(panel.seed_script_combo.findData(str(e.alternate)))
    emitted = []
    panel.startRequested.connect(emitted.append)
    if action == "start":
        panel.start_button.click()
        assert len(emitted) == 1
        assert emitted[0].seed_script_path == e.alternate
    else:
        panel.close()
    assert int(e.settings.value(e.key)) == e.field.value()
    assert e.settings.value("seed_script") == str(e.alternate)
    assert e.config_label.text() == panel.script_save_state_label.text() == "已保存"

    if action == "start":
        panel.seed_script_combo.setCurrentIndex(panel.seed_script_combo.findData(str(e.original)))
        panel.save_scripts_button.click()
        assert e.settings.value("seed_script") == str(e.original)
        assert emitted[0].seed_script_path == e.alternate
        if isinstance(panel, AutoTidRngPanel):
            assert panel._active_config.seed_script_path == e.alternate
