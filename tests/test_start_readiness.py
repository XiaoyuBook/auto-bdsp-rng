from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPoint, QSettings, QTimer, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from auto_bdsp_rng.automation.auto_rng.models import AutoRngPhase
from auto_bdsp_rng.automation.auto_tid_rng import AutoTidRngPhase, AutoTidRngServices
from auto_bdsp_rng.rng_core import SeedState32
from auto_bdsp_rng.automation.easycon import EasyConConfig, EasyConStatus
from auto_bdsp_rng.ui import main_window as mw, auto_rng_panel, auto_tid_rng_panel, easycon_panel
from tests.test_easycon_panel import FakeNativeBackend, UnsupportedKeyboardHookFactory


@pytest.fixture
def window(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    for module, name in ((mw, "main"), (auto_rng_panel, "auto"), (auto_tid_rng_panel, "tid")):
        monkeypatch.setattr(module, "QSettings", lambda *args, n=name: QSettings(str(tmp_path / f"{n}.ini"), QSettings.Format.IniFormat))
    monkeypatch.setattr(mw, "should_show_startup_notice", lambda: False)
    monkeypatch.setattr(mw.MainWindow, "refresh_capture_devices", lambda self: None)
    monkeypatch.setattr(easycon_panel, "load_config", lambda *a: EasyConConfig())
    monkeypatch.setattr(easycon_panel, "save_config", lambda *a: tmp_path / "easycon.json")
    original = mw.EasyConPanel
    monkeypatch.setattr(mw, "EasyConPanel", lambda *a, **kw: original(
        *a, **kw, native_backend=FakeNativeBackend(["COM3"]), keyboard_hook_factory=UnsupportedKeyboardHookFactory()))
    w = mw.MainWindow(profile_settings=QSettings(str(tmp_path / "profile.ini"), QSettings.Format.IniFormat))
    w.resize(1150, 900)
    w.show()
    app.processEvents()
    w.readiness.timer.stop()
    w.easycon_tab._native_status_timer.stop()
    yield w
    for widget in app.topLevelWidgets():
        for timer in widget.findChildren(QTimer):
            timer.stop()
        widget.close()
        widget.deleteLater()
    app.processEvents()


def checks(window):
    return {item.key: item for item in window.readiness.collect()}


def test_readiness_has_no_persistent_page_button(window):
    assert not hasattr(window.readiness, "button")
    for page in (window.auto_rng_tab, window.auto_tid_rng_tab,
                 window.project_xs_tab, window.auto_rng_tab):
        window.tabs.setCurrentWidget(page)
        QApplication.processEvents()
        if page in (window.auto_rng_tab, window.auto_tid_rng_tab):
            assert window.readiness.panel is page
            window.readiness.dialog.hide()


def script(combo, tmp_path, name, text="A 100\n"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    combo.addItem(name, str(path))
    combo.setCurrentIndex(combo.count() - 1)
    return path


def test_checks_do_not_connect_save_start_or_change_button_rules(window, monkeypatch):
    w = window
    forbidden = []
    def record(*args, **kwargs):
        forbidden.append(True)
    for obj, name in ((w.easycon_tab, "connect_native"), (w, "_ensure_preview_for_auto_rng"),
                      (w, "_start_ocr_warmup"), (w.auto_rng_tab, "_save_panel_state"),
                      (w.auto_rng_tab, "_start_with_phase")):
        monkeypatch.setattr(obj, name, record)
    monkeypatch.setattr(w.easycon_tab, "_native_status", lambda: EasyConStatus.BRIDGE_DISCONNECTED)
    monkeypatch.setattr(w.easycon_tab, "_connection_port", lambda: "COM3")
    enabled = w.auto_rng_tab.start_button.isEnabled()
    w.auto_rng_tab.max_wait_frames.setValue(w.auto_rng_tab.max_wait_frames.value() + 1)
    result = checks(w)
    assert result["video"].state == "blocked"
    assert result["controller"].state == "pending"
    assert result["saved"].state == "info"
    assert result["targets"].state == "ok"  # Existing fallback target is real.
    assert result["ocr"].state == "pending"
    w.readiness.show()
    w.readiness.refresh()
    assert not forbidden
    assert w.auto_rng_tab.start_button.isEnabled() == enabled


def test_tid_checks_follow_start_mode_even_without_targets(window, tmp_path):
    panel = window.auto_tid_rng_tab
    window.readiness.dialog.module_combo.setCurrentIndex(1)
    panel.seed_script_combo.setCurrentIndex(0)
    panel.name_script_combo.setCurrentIndex(0)
    panel._clear_targets()
    result = checks(window)
    assert result["targets"].state == "blocked"
    assert "测种" in result["scripts"].detail and "取名" in result["scripts"].detail
    script(panel.name_script_combo, tmp_path, "name.txt")
    window.readiness.show()
    window.readiness.dialog.mode_combo.setCurrentIndex(1)
    assert checks(window)["scripts"].state == "ok"
    assert window.readiness.dialog.rows["scripts"][1].text() == "脚本 · 已就绪"
    assert "ocr" not in checks(window)
    assert not panel.start_button.isEnabled()
    panel.add_target_display_tid(1)
    assert checks(window)["targets"].state == "ok"
    window.readiness.dialog.mode_combo.setCurrentIndex(0)
    assert checks(window)["scripts"].state == "blocked"


@pytest.mark.parametrize("replace_method", ["delete", "close", "clear"])
def test_tid_manual_capture_replace_target_and_save_can_start(window, tmp_path, monkeypatch, replace_method):
    w, panel = window, window.auto_tid_rng_tab
    seed = SeedState32(0x39AAEC0A, 0x68541BB0, 0x96CF56BB, 0xA710731C)
    panel.frame_threshold.setValue(10)
    panel._clear_targets()
    panel.add_target_display_tid(777777)
    script(panel.seed_script_combo, tmp_path, "seed.txt")
    script(panel.name_script_combo, tmp_path, "name.txt")
    monkeypatch.setattr(mw, "capture_pokemon_blinks", lambda *a, **kw: SimpleNamespace(intervals=[]))
    monkeypatch.setattr(mw, "recover_tidsid_seed_from_observation", lambda observation: SimpleNamespace(state=seed))
    w._latest_preview_frame = object()
    w.capture_tidsid_seed()
    w._capture_thread.join(timeout=2)
    assert not w._capture_thread.is_alive()
    w._poll_capture_thread()
    w.tabs.setCurrentWidget(panel)
    QApplication.processEvents()
    panel.id_table.setCurrentCell(1, 4)
    selected_tid = panel.id_table.item(1, 4).text()
    panel.target_list.setCurrentRow(0)
    if replace_method == "delete":
        QTest.keyClick(panel.target_list, Qt.Key.Key_Delete)
    elif replace_method == "close":
        rect = panel.target_list.visualItemRect(panel.target_list.item(0))
        QTest.mouseClick(panel.target_list.viewport(), Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier, rect.topRight() + QPoint(-8, rect.height() // 2))
    else:
        panel.clear_targets_button.click()
    assert not panel.start_button.isEnabled()
    panel.target_input.setFocus()
    QTest.keyClicks(panel.target_input, selected_tid)
    if replace_method == "close":
        panel.add_target_button.click()
    else:
        QTest.keyClick(panel.target_input, Qt.Key.Key_Return)
    panel.save_button.click()

    assert panel.target_display_tids() == (int(selected_tid),)
    assert panel.save_state_label.text() == "已保存"
    assert panel.start_button.isEnabled()
    assert all(action.isEnabled() for action in panel.start_menu.actions())
    assert not panel.stop_button.isEnabled()
    assert w.auto_rng_tab.start_button.isEnabled()
    started = []
    monkeypatch.setattr(w, "_ensure_preview_for_auto_rng", lambda: True)
    monkeypatch.setattr(w, "_ensure_bridge_connected", lambda: True)
    monkeypatch.setattr(w, "_build_auto_tid_rng_services", lambda config: AutoTidRngServices())
    monkeypatch.setattr(panel, "run_with_runner", started.append)
    QTest.mouseClick(panel.start_button, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier, QPoint(15, 15))
    assert len(started) == 1
    assert started[0].config.target_display_tids == (int(selected_tid),)


@pytest.mark.parametrize("save_only", [False, True])
def test_tid_target_changes_refresh_released_script_state_without_polling(window, save_only):
    w, panel = window, window.auto_tid_rng_tab
    panel.add_target_display_tid(1)
    w.easycon_tab._native_run_reserved = True
    w._refresh_automation_start_state()
    assert not panel.start_button.isEnabled()
    assert "占用资源" in panel.start_button.toolTip()
    # A release can happen between status polls. Target edits and Save should
    # recompute actual ownership rather than retaining cached unavailability.
    w.easycon_tab._native_run_reserved = False
    w.auto_rng_tab.set_start_available(True)
    assert w.auto_rng_tab.start_button.isEnabled()
    assert not panel.start_button.isEnabled()
    if save_only:
        panel.save_button.click()
    else:
        panel._clear_targets()
        panel.target_input.setText("123456")
        panel.add_target_button.click()
    assert panel.start_button.isEnabled()
    assert w.auto_rng_tab.start_button.isEnabled()
    assert all(action.isEnabled() for action in panel.start_menu.actions())
    assert not panel.start_button.toolTip()


def test_tid_target_changes_keep_active_script_locked_and_log_only_transitions(window):
    w, panel = window, window.auto_tid_rng_tab
    w.easycon_tab._native_run_reserved = True
    try:
        panel.add_target_display_tid(1)
        panel.save_button.click()
        assert not panel.start_button.isEnabled()
        assert not panel.stop_button.isEnabled()
        assert not w.auto_rng_tab.start_button.isEnabled()
        assert "占用资源" in panel.start_button.toolTip()
        before = panel.log_view.toPlainText()
        for _ in range(3):
            w._refresh_automation_start_state()
        assert panel.log_view.toPlainText() == before
        assert "目标数=1" in before
        assert "共享启动可用=False" in before
    finally:
        w.easycon_tab._native_run_reserved = False
        w._refresh_automation_start_state()


def test_script_content_and_config_changes_invalidate_check_cache(window, tmp_path):
    panel = window.auto_tid_rng_tab
    window.readiness.dialog.module_combo.setCurrentIndex(1)
    panel.add_target_display_tid(1)
    window.readiness.dialog.mode_combo.setCurrentIndex(1)
    path = script(panel.name_script_combo, tmp_path, "name.txt")
    assert checks(window)["scripts"].state == "ok"
    path.write_bytes(b"\xff\xff")
    assert checks(window)["scripts"].state == "blocked"
    path.write_text("A 200\n", encoding="utf-8")
    assert checks(window)["scripts"].state == "ok"
    config = tmp_path / "broken.json"
    config.write_text("{}", encoding="utf-8")
    window.seed_config_combo.addItem(config.name, str(config))
    window.seed_config_combo.setCurrentIndex(window.seed_config_combo.count() - 1)
    assert checks(window)["seed"].state == "blocked"
    window.seed_config_combo.setCurrentIndex(0)
    assert checks(window)["seed"].state == "ok"


def test_static_script_validation_and_reidentify_seed_are_preserved(window, tmp_path):
    panel = window.auto_rng_tab
    script(panel.advance_script_combo, tmp_path, "advance.txt", "_目标帧数 = 100\nA 100\n")
    script(panel.hit_script_combo, tmp_path, "hit.txt")
    panel.seed_script_combo.setCurrentIndex(0)
    panel.escape_continue_check.setChecked(False)
    assert checks(window)["scripts"].state == "ok"
    panel.escape_continue_check.setChecked(True)
    panel.escape_script_combo.setCurrentIndex(0)
    assert "逃跑" in checks(window)["scripts"].detail
    window.readiness.dialog.mode_combo.setCurrentIndex(2)
    window.seed32_inputs[0].setText("bad-seed")
    assert checks(window)["seed_value"].state == "blocked"
    for box, value in zip(window.seed32_inputs, ("00000001", "00000002", "00000003", "00000004")):
        box.setText(value)
    assert checks(window)["seed_value"].state == "ok"


@pytest.mark.parametrize("module,mode,phase", [
    (0, 0, AutoRngPhase.RUN_SEED_SCRIPT), (0, 1, AutoRngPhase.CAPTURE_SEED),
    (0, 2, AutoRngPhase.REIDENTIFY), (1, 0, AutoTidRngPhase.RUN_SEED_SCRIPT),
    (1, 1, AutoTidRngPhase.CAPTURE_TIDSID),
])
def test_check_dialog_uses_original_selected_start_path(window, monkeypatch, module, mode, phase):
    controller = window.readiness
    controller.dialog.module_combo.setCurrentIndex(module)
    controller.dialog.mode_combo.setCurrentIndex(mode)
    panel = controller.panel
    if module:
        panel.add_target_display_tid(1)
    calls = []
    monkeypatch.setattr(panel, "_start_with_phase", calls.append)
    controller.show()
    controller.dialog.start_button.click()
    assert calls == [phase]
    assert window.tabs.currentWidget() is panel
    assert not controller.dialog.isVisible()


def test_log_navigation_uses_current_run_and_round_and_clears_stale_filters(window):
    w = window
    log = w.run_records_tab.log_panel
    w._active_auto_rng_run_id, w._active_auto_rng_round_id = "current", 2
    for run, round_id, message in (("old", 2, "old run"), ("current", 1, "old round"), ("current", 2, "current round")):
        w._run_log_buffer.publish("自动定点", message, run_id=run, round_id=round_id)
    QApplication.processEvents()
    log.search_edit.setText("stale search")
    log.level_combo.setCurrentIndex(log.level_combo.findData("ERROR"))
    w.auto_rng_tab.view_log_button.click()
    assert [e.message for e in log.visible_entries()] == ["current round"]
    assert "第 2 轮" in log.correlation_label.text()
    assert not log.search_edit.text()
    w._show_run_logs(None)
    assert {"old run", "old round", "current round"} <= {e.message for e in log.visible_entries()}
    assert not log.correlation_frame.isVisible()
    w._active_auto_rng_run_id = w._active_auto_rng_round_id = None


def test_empty_actions_navigate_or_clear_filters_without_changing_data(window):
    w = window
    w.tabs.setCurrentWidget(w.auto_tid_rng_tab)
    QApplication.processEvents()
    empty = w.auto_tid_rng_tab.id_empty_state
    assert w.auto_tid_rng_tab.id_table.viewport().rect().contains(empty.action_button.geometry())
    assert empty.title.geometry().bottom() < empty.detail.geometry().top()
    assert empty.detail.geometry().bottom() < empty.action_button.geometry().top()
    empty.action_button.click()
    assert w.readiness.dialog.isVisible()
    assert w.readiness.panel is w.auto_tid_rng_tab
    w.readiness.dialog.hide()
    w.readiness.navigate("scripts")
    assert w.auto_tid_rng_tab.script_fields.isVisible()
    log = w.run_records_tab.log_panel
    w._show_run_logs(None)
    w._run_log_buffer.publish("测试", "保留的数据")
    QApplication.processEvents()
    original = w._run_log_buffer.snapshot()
    log.search_edit.setText("does not match")
    assert log.empty_state.isVisible()
    assert log.empty_state.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    QTest.mouseClick(log.empty_state.action_button, Qt.MouseButton.LeftButton)
    assert not log.search_edit.text()
    assert not log.empty_state.isVisible()
    assert not log.empty_state.action_button.isVisible()
    assert w._run_log_buffer.snapshot() == original


def test_preview_action_disappears_for_real_frames_and_roi_selection(window):
    w = window
    w.tabs.setCurrentWidget(w.project_xs_tab)
    w.preview_label.clear()
    w.preview_label.repaint()
    QApplication.processEvents()
    assert w.readiness.preview_button.isVisible()
    w.preview_label.setPixmap(QPixmap(640, 360))
    w.preview_label.repaint()
    assert not w.readiness.preview_button.isVisible()
    w.preview_label.clear()
    w.preview_label._selection_enabled = True
    w.preview_label.repaint()
    assert not w.readiness.preview_button.isVisible()
