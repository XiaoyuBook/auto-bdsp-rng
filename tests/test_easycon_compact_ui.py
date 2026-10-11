import pytest
from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QBoxLayout

from tests.test_easycon_execution_ui import publish
from tests.test_easycon_panel import app, easycon_panel, easycon_panel_factory
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
    controls = (panel.script_button, panel.save_button, panel.control_tools_button,
                panel.elapsed_label, panel.run_button, panel.pause_button, panel.stop_button)
    rects = [QRect(control.mapTo(panel, QPoint()), control.size()) for control in controls]
    assert all(panel.rect().contains(rect) for rect in rects)
    assert all(not a.intersects(b) for i, a in enumerate(rects) for b in rects[i + 1:])
    monitor = window.monitor_sidebar.geometry()
    panel.show_tools("control")
    settle()
    assert panel.editor.isVisible() and panel.editor.height() >= (400 if not stacked else 150)
    assert panel.tools_panel.height() <= 120
    assert panel.sidebar_scroll.verticalScrollBar().maximum() == 0
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
    for section in ("control", "library", "control"):
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
    panel.shutdown()


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
    panel.shutdown()


def test_narrow_pause_explanation_fits_complete_lines(window, monkeypatch, tmp_path):
    panel = window.easycon_tab
    monkeypatch.setattr(window, "_screen_available_geometry", lambda: QRect(0, 0, 2200, 1400))
    window.resize(860, 600)
    window.tabs.setCurrentWidget(panel)
    path = tmp_path / "pause.ecs"
    text = "\n".join(["WAIT 3000"] * 80)
    path.write_text(text, encoding="utf-8")
    panel.load_script(path)
    panel.show_tools("control")
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


def test_collapsed_tools_show_real_recording_and_keyboard_state(easycon_panel):
    panel = easycon_panel
    panel.show()
    assert panel.connect_native() and panel._activate_virtual_controller()
    panel.show_tools("control")
    panel._start_recording()
    panel.hide_tools()
    assert panel._recording and panel.virtual_controller_enabled
    assert panel.control_tools_button.text() == "录制中 · 控制中"
    assert not panel.new_action.isEnabled() and not panel.open_action.isEnabled()
    panel.show_tools("control")
    panel.pause_btn.click()
    panel.hide_tools()
    assert panel._recording_paused
    assert panel.control_tools_button.text() == "录制暂停 · 控制中"
    panel.show_tools("control")
    panel.record_btn.click()
    assert not panel._recording and panel.virtual_controller_enabled
    assert panel.control_tools_button.text() == "键盘控制中"
    assert panel.new_action.isEnabled() and panel.open_action.isEnabled()
    panel.shutdown()


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
    panel.shutdown()
