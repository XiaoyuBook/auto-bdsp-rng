from __future__ import annotations

from pathlib import Path
from time import monotonic

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox

from auto_bdsp_rng.automation.easycon.native.errors import SourceLocation
from auto_bdsp_rng.automation.easycon.native.trace import ExecutionPoint, ExecutionTrace
from auto_bdsp_rng.automation.easycon.native_backend import NativeEasyConBackend
from auto_bdsp_rng.ui.easycon_execution import source_key
from tests.automation.test_easycon_native_backend import FakeFrameClient
from tests.test_easycon_panel import (
    app, easycon_panel_factory, easycon_panel, process_events_until,
)


def publish(panel, path, text, line, *, library=None, state="running"):
    trace = ExecutionTrace()
    panel.native_backend.execution_trace = trace
    trace.begin(str(path))
    trace.set_sources(((str(path), text),) + (() if library is None else (library,)))
    trace.record(ExecutionPoint(SourceLocation(str(path), line), "等待 3 秒", monotonic(), 3000))
    if state != "running":
        trace.finish(state)
    panel.execution.poll()
    return trace


def test_execution_highlight_scrolls_without_moving_edit_cursor_and_preserves_stop(easycon_panel, tmp_path):
    panel = easycon_panel
    panel.resize(1000, 720)
    panel.show()
    text = "\n".join(["WAIT 10"] * 90)
    path = tmp_path / "main.ecs"
    path.write_text(text, encoding="utf-8")
    panel.load_script(path)
    QTest.qWait(50)
    assert panel.execution.follow.isVisible()
    assert panel.execution.locate.isVisible() and not panel.execution.locate.isEnabled()
    assert panel.execution.state_label.text() == "等待运行"
    panel.editor.setTextCursor(QTextCursor(panel.editor.document().findBlockByNumber(2)))
    app = panel.window().windowHandle()
    assert app is not None
    trace = publish(panel, path, text, 70)
    process_events_until(lambda: panel.editor.execution_line == 70)
    assert panel.editor.textCursor().blockNumber() == 2
    block = panel.editor.document().findBlockByNumber(69)
    rect = panel.editor.blockBoundingGeometry(block).translated(panel.editor.contentOffset())
    assert 0 <= rect.top() < rect.bottom() <= panel.editor.viewport().height()
    trace.finish("stopped")
    panel.execution.poll()
    assert panel.editor.execution_line == 70
    panel.execution.follow.setChecked(False)
    panel.editor.verticalScrollBar().setValue(0)
    panel.execution.locate.click()
    assert panel.editor.execution_line == 70
    assert "已停止" in panel.execution.state_label.text()
    assert "70" in panel.execution.action_label.text()
    panel.shutdown()


def test_library_follow_keeps_main_draft_and_dirty_state(easycon_panel, tmp_path):
    panel = easycon_panel
    path = tmp_path / "main.ecs"
    text = "WAIT 100\n"
    path.write_text(text, encoding="utf-8")
    panel.load_script(path)
    panel.editor.appendPlainText("# 尚未保存")
    draft = panel.editor.toPlainText()
    library = tmp_path / "lib" / "helper.ecs"
    library.parent.mkdir()
    library.write_text("FUNC help()\nWAIT 30\nENDFUNC\n", encoding="utf-8")
    lib_text = library.read_text(encoding="utf-8")
    trace = publish(panel, path, draft, 1, library=(str(library), lib_text))
    trace.record(ExecutionPoint(SourceLocation(str(library), 2), "等待 0.03 秒", monotonic(), 30, SourceLocation(str(path), 1)))
    panel.execution.poll()
    assert panel.execution.viewing_snapshot
    assert panel.execution.viewer.execution_line == 2
    tree = panel.script_sources
    library_item = tree.topLevelItem(tree.topLevelItemCount() - 1)
    assert not library_item.isExpanded()
    assert panel.editor.toPlainText() == draft and panel.has_unsaved_script_changes()
    assert panel.current_script_path == path
    assert panel.save_script() is None
    assert path.read_text(encoding="utf-8") == text
    # Even an external file edit cannot change the source being followed.
    library.write_text("changed on disk", encoding="utf-8")
    panel.execution.locate_point()
    assert panel.execution.viewer.toPlainText() == lib_text
    panel.execution.follow.setChecked(False)
    panel.execution.show_current()
    trace.record(ExecutionPoint(SourceLocation(str(library), 2), "等待", monotonic()))
    panel.execution.poll()
    assert not panel.execution.viewing_snapshot
    panel.execution.follow.setChecked(True)
    assert panel.execution.viewing_snapshot
    panel.shutdown()


def test_external_run_shows_its_source_and_edit_invalidates_old_line(easycon_panel, tmp_path):
    panel = easycon_panel
    panel.editor.setPlainText("# 用户草稿\nWAIT 1")
    draft = panel.editor.toPlainText()
    external = tmp_path / "automatic.ecs"
    publish(panel, external, "WAIT 50", 1, state="completed")
    assert panel.execution.viewing_snapshot
    assert panel.execution.viewer.execution_line == 1
    assert panel.editor.toPlainText() == draft
    panel.execution.show_current()
    assert panel.editor.toPlainText() == draft
    # A matching main run can be edited after stopping; its old highlight clears.
    publish(panel, Path(panel.execution.current_source()), draft, 2, state="stopped")
    assert panel.editor.execution_line == 2
    panel.editor.insertPlainText("# 修改\n")
    assert panel.editor.execution_line is None
    panel.execution.locate_point()
    assert panel.execution.viewer.toPlainText() == draft
    assert panel.execution.viewer.execution_line == 2
    panel.new_script()
    panel.execution.poll()
    assert not panel.execution.bar.isHidden()
    assert panel.execution.state_label.text() == "等待运行"
    assert not panel.execution.locate.isEnabled()
    assert not panel.execution.viewing_snapshot
    panel.shutdown()


def test_real_native_worker_wait_stop_restart_and_draft_guards(easycon_panel_factory, tmp_path):
    backend = NativeEasyConBackend(frame_client_factory=lambda: FakeFrameClient(np.zeros((8, 8, 3), dtype=np.uint8)))
    backend.connect("mock")
    panel = easycon_panel_factory(native_backend=backend)
    panel.port_combo.addItem("mock", "mock")
    panel.port_combo.setCurrentIndex(panel.port_combo.count() - 1)
    path = tmp_path / "main.ecs"
    path.write_text("# 等待\nWAIT 10000", encoding="utf-8")
    panel.load_script(path)
    panel.resize(1000, 720)
    panel.show()
    try:
        QTest.mouseClick(panel.run_button, Qt.MouseButton.LeftButton)
        process_events_until(lambda: panel.execution.snapshot.point is not None, timeout_ms=2000)
        assert panel.editor.execution_line == 2
        assert panel.editor.isReadOnly() and not panel.open_button.isEnabled()
        assert not panel.load_script(path)
        QTest.mouseClick(panel.stop_button, Qt.MouseButton.LeftButton)
        process_events_until(lambda: panel.native_run_thread is None, timeout_ms=2000)
        panel.execution.poll()
        assert "已停止" in panel.execution.state_label.text()
        assert panel.editor.execution_line == 2
        assert not panel.editor.isReadOnly()
        first_id = panel.execution.snapshot.run_id
        panel.editor.setPlainText("WAIT 5")
        QTest.mouseClick(panel.run_button, Qt.MouseButton.LeftButton)
        process_events_until(lambda: backend.execution_trace.snapshot().run_id > first_id and panel.native_run_thread is None, timeout_ms=2000)
        panel.execution.poll()
        assert panel.execution.snapshot.state == "completed"
        assert panel.editor.execution_line == 1
    finally:
        panel.shutdown()


def test_real_loop_counter_stays_visible_during_wait_and_after_stop_without_video(easycon_panel_factory, tmp_path):
    def unexpected_video():
        raise AssertionError("按键和循环脚本不应请求视频源")

    backend = NativeEasyConBackend(frame_client_factory=unexpected_video)
    backend.connect("mock")
    panel = easycon_panel_factory(native_backend=backend)
    panel._video_source_connected = lambda: False
    panel.port_combo.addItem("mock", "mock")
    panel.port_combo.setCurrentIndex(panel.port_combo.count() - 1)
    path = tmp_path / "loop.ecs"
    path.write_text("FOR 3\nA 10\nWAIT 500\nNEXT\nWAIT 1", encoding="utf-8")
    panel.load_script(path)
    panel.resize(1000, 720)
    panel.show()
    try:
        assert panel.run_button.isEnabled()
        panel.run_button.click()
        process_events_until(lambda: panel.execution.snapshot.point is not None
                             and panel.execution.snapshot.point.duration_ms == 500
                             and panel.execution.snapshot.point.loops[-1].iteration == 2)
        assert panel.editor.execution_line == 3
        assert panel.execution.loop_label.isVisible()
        assert "第 2 / 3 次" in panel.execution.loop_label.text()
        panel.execution.follow.setChecked(False)
        panel.stop_button.click()
        process_events_until(lambda: panel.native_run_thread is None)
        panel.execution.poll()
        assert panel.execution.snapshot.state == "stopped"
        assert "第 2 / 3 次" in panel.execution.loop_label.text()
        panel.execution.clear()
        assert panel.execution.loop_label.isHidden()
    finally:
        panel.shutdown()


def test_follow_reveals_line_in_outer_scroll_and_after_showing_page(easycon_panel, tmp_path):
    panel = easycon_panel
    panel.resize(860, 430)
    panel.show()
    path = tmp_path / "main.ecs"
    text = "\n".join(["WAIT 100"] * 80)
    path.write_text(text, encoding="utf-8")
    panel.load_script(path)
    trace = publish(panel, path, text, 70)
    QTest.qWait(100)
    outer = panel.workspace_splitter.widget(1)
    outer.verticalScrollBar().setValue(outer.verticalScrollBar().maximum())
    trace.record(ExecutionPoint(SourceLocation(str(path), 1), "等待", monotonic()))
    panel.execution.poll()
    QTest.qWait(100)
    rect = panel.editor.cursorRect(QTextCursor(panel.editor.document().firstBlock()))
    assert panel.editor.viewport().visibleRegion().contains(rect)
    panel.hide()
    trace.record(ExecutionPoint(SourceLocation(str(path), 40), "等待", monotonic()))
    panel.execution.poll()
    panel.show()
    panel.execution.poll()
    QTest.qWait(100)
    rect = panel.editor.cursorRect(QTextCursor(panel.editor.document().findBlockByNumber(39)))
    assert panel.editor.viewport().visibleRegion().contains(rect)
    panel.shutdown()


def test_replaced_backend_trace_starts_fresh_after_old_record_dismissed(easycon_panel, tmp_path):
    panel = easycon_panel
    path = tmp_path / "main.ecs"
    path.write_text("WAIT 5", encoding="utf-8")
    panel.load_script(path)
    publish(panel, path, "WAIT 5", 1, state="completed")
    panel.execution.clear()
    assert not panel.execution.bar.isHidden()
    assert panel.execution.state_label.text() == "等待运行"
    publish(panel, path, "WAIT 5", 1, state="completed")
    assert not panel.execution.bar.isHidden()
    assert panel.editor.execution_line == 1
    panel.shutdown()


def test_script_library_lists_txt_ecs_and_double_click_loads_current(easycon_panel):
    panel = easycon_panel
    directory = panel.script_library_directory()
    selected = directory / "另一个脚本.ecs"
    selected.write_text("WAIT 30\n", encoding="utf-8")
    (directory / "ignore.json").write_text("{}", encoding="utf-8")
    panel.execution.refresh_sources()
    panel.resize(1000, 720)
    panel.show()
    QTest.qWait(100)
    tree = panel.script_sources
    library = tree.topLevelItem(tree.topLevelItemCount() - 1)
    assert library.text(0) == "lib"
    assert not library.isExpanded()
    collapsed_height = tree.height()
    QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=tree.visualItemRect(library).center())
    assert library.isExpanded()
    assert tree.height() > collapsed_height
    panel.execution.refresh_sources()
    library = tree.topLevelItem(tree.topLevelItemCount() - 1)
    assert library.isExpanded()
    names = {library.child(i).text(0) for i in range(library.childCount())}
    assert names == {"玫瑰公园.txt", "另一个脚本.ecs"}
    item = next(library.child(i) for i in range(library.childCount()) if library.child(i).text(0) == selected.name)
    tree.scrollToItem(item)
    QTest.qWait(30)
    QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=tree.visualItemRect(item).center())
    QTest.mouseDClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=tree.visualItemRect(item).center())
    process_events_until(lambda: panel.current_script_path == selected)
    assert tree.topLevelItem(0).text(0) == selected.name
    assert panel.editor.toPlainText() == "WAIT 30\n"
    assert not panel.execution.viewing_snapshot
    assert not panel.editor.isReadOnly()
    library = tree.topLevelItem(tree.topLevelItemCount() - 1)
    assert library.isExpanded()
    QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=tree.visualItemRect(library).center())
    assert not library.isExpanded()
    assert tree.height() == collapsed_height
    panel.execution.refresh_sources()
    assert not tree.topLevelItem(tree.topLevelItemCount() - 1).isExpanded()
    panel.shutdown()


def test_open_from_library_protects_dirty_current_script(easycon_panel, monkeypatch):
    panel = easycon_panel
    directory = panel.script_library_directory()
    original, other = directory / "原脚本.ecs", directory / "新脚本.ecs"
    original.write_text("WAIT 1", encoding="utf-8")
    other.write_text("WAIT 2", encoding="utf-8")
    panel.load_script(original)
    panel.editor.setPlainText("WAIT 123")
    def item_for_other():
        return panel.execution._items[source_key(str(other))]
    panel.execution._source_clicked(item_for_other(), 0)
    assert panel.execution.viewing_snapshot
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Cancel)
    panel.execution._activate_source(item_for_other(), 0)
    assert panel.current_script_path == original
    assert panel.editor.toPlainText() == "WAIT 123"
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Save)
    panel.execution._activate_source(item_for_other(), 0)
    assert original.read_text(encoding="utf-8") == "WAIT 123"
    assert panel.current_script_path == other
    assert panel.editor.toPlainText() == "WAIT 2"
    panel.shutdown()
