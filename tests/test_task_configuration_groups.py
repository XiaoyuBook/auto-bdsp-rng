from dataclasses import replace

import pytest
from PySide6.QtCore import QSettings, QSignalBlocker, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox

from auto_bdsp_rng.blink_detection import save_project_xs_config
from auto_bdsp_rng.ui import main_window as mw
from auto_bdsp_rng.ui.auto_rng_panel import AutoRngPanel
from auto_bdsp_rng.ui.auto_tid_rng_panel import AutoTidRngPanel
from auto_bdsp_rng.ui.task_settings import TaskConfigBinding
from tests.test_start_readiness import window


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def scripts(tmp_path):
    for name in ("BDSP测种.txt", "备用测种.txt", "bdsp过帧.txt", "谢米.txt", "取名.txt", "逃跑.txt"):
        (tmp_path / name).write_text("WAIT 10\n", encoding="utf-8")


@pytest.mark.parametrize("panel_type", [AutoRngPanel, AutoTidRngPanel])
def test_visible_save_configuration_persists_parameters_and_scripts(app, tmp_path, panel_type):
    scripts(tmp_path)
    settings = QSettings(str(tmp_path / "task.ini"), QSettings.Format.IniFormat)
    panel = panel_type(script_dir=tmp_path, settings=settings)
    try:
        field = panel.max_advances if isinstance(panel, AutoRngPanel) else panel.frame_threshold
        field.setValue(456)
        if isinstance(panel, AutoRngPanel):
            panel.config_groups.select_group("transition")
            panel.reseeding_threshold.setValue(654_321)
        selected = str(tmp_path / "备用测种.txt")
        panel.seed_script_combo.setCurrentIndex(panel.seed_script_combo.findData(selected))
        assert panel.task_save_state_label.text() == "有未保存修改"
        panel.save_task_button.click()
        assert panel.task_save_state_label.text() == "已保存"
        key = "max_advances" if isinstance(panel, AutoRngPanel) else "frame_threshold"
        assert int(settings.value(key)) == 456
        assert settings.value("seed_script") == selected
        if isinstance(panel, AutoRngPanel):
            assert int(settings.value("reseeding_threshold")) == 654_321
        restored = panel_type(script_dir=tmp_path, settings=settings)
        try:
            assert restored.seed_script_combo.currentData() == selected
            assert getattr(restored, key).value() == 456
            if isinstance(restored, AutoRngPanel):
                assert restored.reseeding_threshold.value() == 654_321
        finally:
            restored.close()
            restored.deleteLater()
    finally:
        panel.close()
        panel.deleteLater()
        app.processEvents()


def test_groups_keep_edits_and_reveal_missing_escape_script(app, tmp_path):
    scripts(tmp_path)
    panel = AutoRngPanel(script_dir=tmp_path, settings=QSettings(str(tmp_path / "task.ini"), QSettings.Format.IniFormat))
    try:
        panel.resize(600, 850)
        panel.show()
        app.processEvents()
        QTest.mouseClick(panel.config_groups.buttons["shiny"], Qt.MouseButton.LeftButton)
        panel.shiny_threshold_seconds.setValue(1.7)
        panel.auto_reverse_combo.setCurrentIndex(1)
        assert panel.reverse_script_combo.isVisible()
        assert not panel.exit_script_combo.isVisible()
        QTest.mouseClick(panel.config_groups.buttons["transition"], Qt.MouseButton.LeftButton)
        assert panel.reidentify_config_binding.isVisible()
        assert panel.exit_script_combo.isVisible()
        assert not panel.seed_config_binding.isVisible()
        QTest.mouseClick(panel.config_groups.buttons["continuation"], Qt.MouseButton.LeftButton)
        panel.escape_continue_check.setChecked(True)
        panel.escape_script_combo.setCurrentIndex(0)
        panel.config_groups.select_group("basic")
        panel._focus_missing_script()
        # Complete the mandatory scripts first; the next missing control is
        # the escape script even while its configuration page is inactive.
        panel.advance_script_combo.setCurrentIndex(panel.advance_script_combo.findText("bdsp过帧.txt"))
        panel.hit_script_combo.setCurrentIndex(panel.hit_script_combo.findText("谢米.txt"))
        panel._focus_missing_script()
        assert panel.config_groups.stack.currentWidget() is panel.config_groups.pages["continuation"]
        edits = []
        panel.scriptEditRequested.connect(edits.append)
        path = tmp_path / "逃跑.txt"
        panel.escape_script_combo.setCurrentIndex(panel.escape_script_combo.findData(str(path)))
        panel.script_edit_buttons[panel.escape_script_combo].click()
        assert edits == [path]
        panel.config_groups.select_group("shiny")
        assert panel.shiny_threshold_seconds.value() == 1.7
        assert panel.auto_reverse_combo.currentIndex() == 1
    finally:
        panel.close()
        panel.deleteLater()
        app.processEvents()


def test_shared_binding_survives_blocked_source_refresh(app):
    source = QComboBox()
    source.addItem("a.json", "a.json")
    source.addItem("b.json", "b.json")
    first, second = TaskConfigBinding("Seed 配置"), TaskConfigBinding("Seed 配置")
    for view in (first, second):
        view.bind_source(source)
    try:
        first.combo.setCurrentIndex(1)
        assert source.currentData() == second.combo.currentData() == "b.json"
        with QSignalBlocker(source):
            source.clear()
            source.addItem("c.json", "c.json")
            source.addItem("b.json", "b.json")
            source.setCurrentIndex(1)
        app.processEvents()
        assert source.currentData() == first.combo.currentData() == second.combo.currentData() == "b.json"
        assert first.combo.count() == second.combo.count() == 2
    finally:
        for view in (first, second, source):
            view.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("escape", [False, True])
def test_readiness_script_action_reveals_the_missing_controls_group(window, tmp_path, escape):
    w, panel = window, window.auto_rng_tab
    scripts(tmp_path)
    panel.script_dir = tmp_path
    panel.refresh_scripts()
    panel.advance_script_combo.setCurrentIndex(panel.advance_script_combo.findText("bdsp过帧.txt") if escape else 0)
    panel.hit_script_combo.setCurrentIndex(panel.hit_script_combo.findText("谢米.txt") if escape else 0)
    panel.escape_continue_check.setChecked(escape)
    panel.escape_script_combo.setCurrentIndex(0)
    w.tabs.setCurrentWidget(panel)
    panel.config_groups.select_group("shiny")
    w.readiness.navigate("scripts")
    QApplication.processEvents()
    expected = panel.escape_script_combo if escape else panel.advance_script_combo
    group = "continuation" if escape else "basic"
    assert panel.config_groups.stack.currentWidget() is panel.config_groups.pages[group]
    assert expected.isVisible()
    assert expected.hasFocus()


def test_task_bindings_edit_same_seed_file_and_keep_correction_independent(window, monkeypatch, tmp_path):
    w = window
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    config = w._config_from_form()
    save_project_xs_config(replace(config, source_path=first), first)
    save_project_xs_config(replace(config, source_path=second, npc=9), second)
    monkeypatch.setattr(mw, "PROJECT_XS_CONFIGS", tmp_path)
    w._refresh_config_list()
    binding = w.auto_rng_tab.seed_config_binding
    binding.combo.setCurrentIndex(binding.combo.findData(str(second)))
    assert w._selected_auto_seed_config_path() == str(second)
    assert w.auto_tid_rng_tab.seed_config_binding.combo.currentData() == str(second)
    assert w._selected_auto_reidentify_config_path() == str(first)
    assert w._selected_config_path() == str(first)
    binding.edit_button.click()
    assert w.tabs.currentWidget() is w.project_xs_tab
    assert w._selected_config_path() == str(second)
    assert w.npc_count.text() == "9"
    assert not w.seed_config_combo.isVisible()
    assert not w.reidentify_config_combo.isVisible()
