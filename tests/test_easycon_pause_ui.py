from __future__ import annotations

from auto_bdsp_rng.automation.easycon.native_backend import NativeEasyConBackend
from tests.test_easycon_panel import app, easycon_panel_factory, process_events_until


def test_pause_edit_validation_and_continue_use_current_editor_in_same_run(easycon_panel_factory, tmp_path):
    backend = NativeEasyConBackend()
    backend.connect("mock")
    panel = easycon_panel_factory(native_backend=backend)
    panel._video_source_connected = lambda: False
    panel.port_combo.addItem("mock", "mock")
    panel.port_combo.setCurrentIndex(panel.port_combo.count() - 1)
    original = 'PRINT "before"\nWAIT 500\nPRINT "old"'
    path = tmp_path / "manual.ecs"
    path.write_text(original, encoding="utf-8")
    panel.load_script(path)
    panel.resize(1000, 720)
    panel.show()
    try:
        assert panel.pause_button.isVisible() and not panel.pause_button.isEnabled()
        panel.run_button.click()
        process_events_until(lambda: panel.editor.execution_line == 2 and panel.pause_button.isEnabled())
        assert panel.editor.isReadOnly()
        thread = panel.native_run_thread
        panel.pause_button.click()
        process_events_until(lambda: panel.execution.snapshot.state == "paused")
        run_id = panel.execution.snapshot.run_id
        assert not panel.editor.isReadOnly()
        assert panel.run_button.text() == "继续运行" and panel.run_button.isEnabled()
        assert not panel.pause_button.isEnabled() and panel.stop_button.isEnabled()
        assert not panel.open_button.isEnabled() and not panel.new_button.isEnabled()
        assert panel.execution.pause_note.isVisible()
        panel.editor.setPlainText('PRINT "before"\nWAIT 500\nIF')
        panel.run_button.click()
        assert backend.is_paused and panel.native_run_thread is thread
        assert "未继续" in panel.execution.pause_note.text()
        assert not panel.editor.isReadOnly()
        revised = original.replace('PRINT "old"', 'PRINT "new"')
        panel.editor.setPlainText(revised)
        panel.run_button.click()
        assert panel.native_run_thread is thread
        assert panel.editor.isReadOnly()
        process_events_until(lambda: panel.native_run_thread is None)
        panel.execution.poll()
        assert panel.execution.snapshot.state == "completed"
        assert panel.execution.snapshot.run_id == run_id
        assert panel.execution.snapshot.sources[0][1] == revised
        assert panel.editor.toPlainText() == revised and panel.has_unsaved_script_changes()
        assert path.read_text(encoding="utf-8") == original
        assert "new" in panel.log_view.toPlainText()
        assert panel.run_button.text() == "运行脚本"
        assert panel.execution.pause_note.isHidden()
    finally:
        panel.shutdown()


def test_stopping_paused_script_restores_editing_and_run_controls(easycon_panel_factory, tmp_path):
    backend = NativeEasyConBackend()
    backend.connect("mock")
    panel = easycon_panel_factory(native_backend=backend)
    panel.port_combo.addItem("mock", "mock")
    panel.port_combo.setCurrentIndex(panel.port_combo.count() - 1)
    path = tmp_path / "long.ecs"
    path.write_text("WAIT 10000\nA 10", encoding="utf-8")
    panel.load_script(path)
    panel.show()
    try:
        panel.run_button.click()
        process_events_until(lambda: panel.pause_button.isEnabled())
        panel.pause_button.click()
        process_events_until(lambda: panel.execution.snapshot.state == "paused")
        panel.stop_button.click()
        process_events_until(lambda: panel.native_run_thread is None)
        panel.execution.poll()
        assert panel.execution.snapshot.state == "stopped"
        assert panel.open_button.isEnabled() and panel.run_button.isEnabled()
        assert not panel.stop_button.isEnabled() and not panel.pause_button.isEnabled()
        assert not panel.editor.isReadOnly()
    finally:
        panel.shutdown()
