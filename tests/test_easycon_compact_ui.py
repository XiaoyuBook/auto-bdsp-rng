import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QBoxLayout

from tests.test_easycon_execution_ui import publish
from tests.test_easycon_panel import (
    FakeKeyboardHookFactory, app, connect_native, easycon_panel, easycon_panel_factory, shutdown_panel,
)
from tests.test_start_readiness import window
from tests.test_workspace_layout import settle


@pytest.mark.parametrize("size,stacked", [((1150, 880), False), ((860, 600), True)])
def test_full_window_compact_controls_preserve_monitor_and_editor(window, monkeypatch, size, stacked):
    panel = window.easycon_tab
    monkeypatch.setattr(window, "_screen_available_geometry", lambda: QRect(0, 0, 2200, 1400))
    window.resize(*size)
    window.tabs.setCurrentWidget(panel)
    settle()
    direction = QBoxLayout.Direction.TopToBottom if stacked else QBoxLayout.Direction.LeftToRight
    assert panel.toolbar.layout().direction() == direction
    assert panel.execution.bar.height() <= 36
    assert panel.execution.details.isHidden()
    assert panel.open_button.isHidden() and panel.new_button.isHidden()
    assert [action.text() for action in panel.script_menu.actions() if not action.isSeparator()] == ["打开…", "新建", "脚本库"]
    controls = (panel.script_button, panel.save_button, panel.record_btn,
                panel.elapsed_label, panel.run_button, panel.pause_button, panel.stop_button)
    rects = [QRect(control.mapTo(panel, QPoint()), control.size()) for control in controls]
    assert all(panel.rect().contains(rect) for rect in rects)
    assert all(not a.intersects(b) for i, a in enumerate(rects) for b in rects[i + 1:])
    monitor = window.monitor_sidebar.geometry()
    editor_geometry = panel.editor.geometry()
    assert connect_native(panel)
    panel.show_tools("control")
    settle()
    assert panel._controller_overlay.isVisible()
    assert panel.tools_panel.isHidden()
    assert panel.editor.geometry() == editor_geometry
    assert panel.editor.isVisible() and panel.editor.height() >= (500 if not stacked else 250)
    assert window.monitor_sidebar.geometry() == monitor
    assert window.live_log_panel.isVisible() and window.preview_label.isVisible()


def test_tools_keep_draft_undo_execution_position_and_menu_locks(easycon_panel, tmp_path):
    panel = easycon_panel
    path = tmp_path / "draft.ecs"
    path.write_text("WAIT 100\nWAIT 200", encoding="utf-8")
    panel.load_script(path)
    panel.resize(700, 600)
    panel.show()
    panel.editor.moveCursor(QTextCursor.MoveOperation.End)
    panel.editor.insertPlainText("\n# 未保存")
    draft = panel.editor.toPlainText()
    document = panel.editor.document()
    publish(panel, path, draft, 2, state="stopped")
    for section in ("library", "library"):
        panel.show_tools(section)
        panel.close_tools_button.click()
        settle()
        assert panel.editor.document() is document
        assert panel.editor.toPlainText() == draft
        assert panel.editor.execution_line == 2
        assert panel.has_unsaved_script_changes() and document.isUndoAvailable()
    panel.editor.undo()
    assert panel.editor.toPlainText() == "WAIT 100\nWAIT 200"
    panel.execution.set_busy(True)
    assert not panel.open_action.isEnabled() and not panel.new_action.isEnabled()
    panel.new_action.trigger()
    assert panel.current_script_path == path
    panel.execution.set_busy(False)
    assert panel.open_action.isEnabled() and panel.new_action.isEnabled()
    shutdown_panel(panel)


def test_terminal_details_can_expand_without_losing_last_line(easycon_panel, tmp_path):
    panel = easycon_panel
    path = tmp_path / "wait.ecs"
    path.write_text("WAIT 3000", encoding="utf-8")
    panel.load_script(path)
    panel.show()
    trace = publish(panel, path, "WAIT 3000", 1)
    assert not panel.execution.details.isHidden()
    assert "剩余约" in panel.execution.action_label.text()
    trace.finish("stopped")
    panel.execution.poll()
    assert panel.execution.details.isHidden()
    assert panel.editor.execution_line == 1
    panel.execution.details_toggle.click()
    assert not panel.execution.details.isHidden()
    assert "第 1 行" in panel.execution.location_label.text()
    panel.execution.clear()
    assert panel.execution.details.isHidden() and panel.execution.details_toggle.isHidden()
    shutdown_panel(panel)


def test_narrow_pause_explanation_fits_complete_lines(window, monkeypatch, tmp_path):
    panel = window.easycon_tab
    monkeypatch.setattr(window, "_screen_available_geometry", lambda: QRect(0, 0, 2200, 1400))
    window.resize(860, 600)
    window.tabs.setCurrentWidget(panel)
    path = tmp_path / "pause.ecs"
    text = "\n".join(["WAIT 3000"] * 80)
    path.write_text(text, encoding="utf-8")
    panel.load_script(path)
    publish(panel, path, text, 70, state="paused")
    settle()
    note = panel.execution.pause_note
    assert note.isVisible() and "游戏和 RNG 仍会继续推进" in note.text()
    assert note.height() >= note.heightForWidth(note.width())
    assert panel.execution.bar.rect().contains(QRect(note.mapTo(panel.execution.bar, QPoint()), note.size()))
    bar_rect = QRect(panel.execution.bar.mapTo(panel, QPoint()), panel.execution.bar.size())
    header_rect = QRect(panel.editor_header.mapTo(panel, QPoint()), panel.editor_header.size())
    assert bar_rect.bottom() < header_rect.top()
    cursor_rect = panel.editor.cursorRect(QTextCursor(panel.editor.document().findBlockByNumber(69)))
    assert panel.editor.viewport().visibleRegion().contains(cursor_rect)
    assert window.live_log_panel.isVisible()


def test_toolbar_and_device_menu_show_real_recording_and_keyboard_state(easycon_panel):
    panel = easycon_panel
    panel.show()
    assert connect_native(panel) and panel._activate_virtual_controller()
    panel._start_recording()
    panel.hide_tools()
    assert panel._recording and panel.virtual_controller_enabled
    assert panel.record_btn.text() == "停止录制" and panel.pause_btn.isVisible()
    assert not panel.run_button.isEnabled()
    assert panel.pause_recording_action.isVisible() and panel.stop_recording_action.isVisible()
    assert not panel.new_action.isEnabled() and not panel.open_action.isEnabled()
    panel.pause_btn.click()
    panel.hide_tools()
    assert panel._recording_paused
    assert not panel.run_button.isEnabled()
    assert panel.pause_btn.text() == panel.pause_recording_action.text() == "继续录制"
    assert "已暂停" in panel.record_btn.toolTip()
    panel.record_btn.click()
    assert not panel._recording and panel.virtual_controller_enabled
    assert panel.pause_btn.isHidden() and not panel.stop_recording_action.isVisible()
    assert panel.virtual_controller_action.text() == "关闭虚拟手柄"
    assert panel.new_action.isEnabled() and panel.open_action.isEnabled()
    shutdown_panel(panel)


def test_global_device_menu_works_across_pages_and_keeps_editor_draft(window):
    w, panel = window, window.easycon_tab
    assert w.tabs.tabText(w.tabs.indexOf(panel)) == "脚本编辑区"
    assert w.easycon_header_button.menu() is panel.controller_tools_menu
    document = panel.editor.document()
    panel.editor.insertPlainText("# 当前草稿\n")
    before = panel.editor.toPlainText()
    panel.connection_settings_action.trigger()
    assert panel.connection_dialog.isVisible()
    panel.connection_dialog_close_button.click()
    assert connect_native(panel)
    w.tabs.setCurrentWidget(w.auto_rng_tab)
    panel.virtual_controller_action.trigger()
    settle()
    assert panel.virtual_controller_enabled and panel._controller_overlay.isVisible()
    assert "键盘：控制中" in w.easycon_header_button.toolTip()
    assert w.tabs.currentWidget() is w.auto_rng_tab
    panel._start_recording()
    panel._handle_virtual_controller_key(panel.key_mapping["A"], True)
    panel._handle_virtual_controller_key(panel.key_mapping["A"], False)
    recorded = panel.editor.toPlainText()
    assert recorded.startswith(before) and "A DOWN" in recorded and "A UP" in recorded
    assert "录制：录制中" in w.easycon_header_button.toolTip()
    panel.pause_recording_action.trigger()
    assert panel._recording_paused and panel.pause_btn.text() == "继续录制"
    assert "录制：已暂停" in w.easycon_header_button.toolTip()
    panel.pause_recording_action.trigger()
    assert panel._recording and not panel._recording_paused
    panel.stop_recording_action.trigger()
    assert not panel._recording and panel.editor.toPlainText() == recorded
    assert panel.editor.document() is document and document.isUndoAvailable()
    panel.virtual_controller_action.trigger()
    assert not panel.virtual_controller_enabled and not panel._controller_overlay.isVisible()
    assert panel.virtual_controller_action.text() == "打开虚拟手柄"
    assert "键盘：已关闭" in w.easycon_header_button.toolTip()
    assert w.tabs.currentWidget() is w.auto_rng_tab
    assert w.preview_label.isVisible() and w.live_log_panel.isVisible()
    panel._saved_editor_text = panel.editor.toPlainText()


def test_virtual_controller_does_not_hide_short_editor(window, monkeypatch):
    w, panel = window, window.easycon_tab
    monkeypatch.setattr(w, "_screen_available_geometry", lambda: QRect(0, 0, 1800, 1200))
    w.resize(854, 480)
    w.tabs.setCurrentWidget(panel)
    settle()
    geometry = panel.editor.geometry()
    assert connect_native(panel)
    panel.virtual_controller_action.trigger()
    settle()
    assert panel.editor.isVisible() and panel.editor.geometry() == geometry
    assert panel.tools_panel.isHidden()
    panel._controller_overlay.hideRequested.emit()
    assert panel._vpad_input_source is None
    assert not panel._controller_overlay.isVisible()
    assert panel.virtual_controller_action.text() == "打开虚拟手柄"


def test_device_menu_navigation_does_not_send_controller_input(easycon_panel):
    panel = easycon_panel
    panel.show()
    assert connect_native(panel) and panel._activate_virtual_controller()
    panel._start_recording()
    panel.controller_tools_menu.popup(panel.mapToGlobal(QPoint(0, 32)))
    QTest.keyClick(panel.controller_tools_menu, Qt.Key.Key_Down)
    assert not panel.native_backend.stick_events
    assert panel.controller_tools_menu.activeAction() is not None
    QTest.keyClick(panel.controller_tools_menu, Qt.Key.Key_Escape)
    assert panel.controller_tools_menu.isHidden()
    assert panel.virtual_controller_enabled and panel._recording
    panel._handle_virtual_controller_key(int(Qt.Key.Key_Down), True)
    panel._handle_virtual_controller_key(int(Qt.Key.Key_Down), False)
    assert panel.native_backend.stick_events == [("right", "Down", True), ("right", "Down", False)]
    shutdown_panel(panel)


def test_device_menu_suspends_global_mapped_keys_and_restores_real_mode(easycon_panel_factory):
    factory = FakeKeyboardHookFactory()
    panel = easycon_panel_factory(keyboard_hook_factory=factory)
    panel.show()
    assert connect_native(panel) and panel._activate_virtual_controller()
    hook = factory.instances[-1]
    panel._start_recording()
    hook.emit_key(panel.key_mapping["A"], True)
    settle()
    panel.controller_tools_menu.popup(panel.mapToGlobal(QPoint(0, 32)))
    assert not hook.mapped_keys_enabled and panel._recording
    assert panel.native_backend.key_events == [("down", "A"), ("up", "A")]
    hook.emit_key(int(Qt.Key.Key_Down), True)
    hook.emit_key(int(Qt.Key.Key_Down), False)
    settle()
    assert not panel.native_backend.stick_events
    hook.emit_key(int(Qt.Key.Key_Escape), True)
    hook.emit_key(int(Qt.Key.Key_Escape), False)
    settle()
    assert panel.controller_tools_menu.isHidden() and hook.mapped_keys_enabled
    assert panel.virtual_controller_enabled and panel._recording
    panel._set_virtual_controller_standby()
    panel.controller_tools_menu.popup(panel.mapToGlobal(QPoint(0, 32)))
    panel.controller_tools_menu.hide()
    assert not hook.mapped_keys_enabled and not panel.virtual_controller_enabled
    assert panel._vpad_input_source == "hook"
    assert panel._activate_virtual_controller()
    panel.controller_tools_menu.popup(panel.mapToGlobal(QPoint(0, 32)))
    hook.emit_key(int(Qt.Key.Key_Escape), True, control_down=True)
    settle()
    assert panel._vpad_input_source is None and not hook.is_running
    assert panel.controller_tools_menu.isHidden() and not panel._controller_overlay.isVisible()
    shutdown_panel(panel)


def test_script_error_locates_main_and_library_and_keeps_literal_output(easycon_panel, tmp_path):
    panel = easycon_panel
    main = tmp_path / "main.ecs"
    main.write_text("WAIT 1\nWAIT 2\nWAIT 3", encoding="utf-8")
    panel.load_script(main)
    received = []
    panel._run_log_sink = lambda level, text: received.append((level, text))
    message = f"{main}:2:1: <错误> & 参数不完整"
    panel._append_log("error", message)
    assert panel.script_error_label.text() == "第 2 行：<错误> & 参数不完整"
    assert received == [("ERROR", message)]
    panel.locate_error_button.click()
    assert panel.editor.textCursor().blockNumber() == 1
    library = tmp_path / "lib.ecs"
    library.write_text("FUNC help()\nWAIT 1\nENDFUNC", encoding="utf-8")
    panel._set_script_error(f"{library}:2:1: 无效参数")
    panel.locate_error_button.click()
    assert panel.execution.viewing_snapshot
    assert panel.execution.viewer.textCursor().blockNumber() == 1
    assert panel.editor.toPlainText() == main.read_text(encoding="utf-8")
    panel._append_log("stdout", '<原始输出> & "引号"')
    assert '<原始输出> & "引号"' in panel.log_view.toPlainText()
    assert "<错误> & 参数不完整" in panel.log_view.toPlainText()
    panel.execution.clear()
    assert panel.script_error_label.text() == "暂无脚本错误"
    assert "<错误>" in panel.log_view.toPlainText()
    shutdown_panel(panel)
