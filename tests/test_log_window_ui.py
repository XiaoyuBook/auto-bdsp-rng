from threading import Thread

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from tests.test_run_records_ui import app
from tests.test_start_readiness import window
from tests.test_workspace_layout import settle


def messages(panel):
    return [entry.message for entry in panel.visible_entries()]


def test_complete_log_window_reuses_model_and_receives_workers_while_closed(app, window):
    w = window
    w.tabs.setCurrentWidget(w.easycon_tab)
    assert w.easycon_tab.connect_native()
    assert w.easycon_tab._activate_virtual_controller()
    original = w.tabs.currentWidget()
    logs, panel = w.run_log_window, w.run_log_window.log_panel
    assert not logs.isVisible()
    w._show_run_logs(None)
    assert logs.isVisible() and logs.windowModality() == Qt.WindowModality.NonModal
    assert not logs.testAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    model = panel.log_model
    logs.resize(700, 480)
    settle()
    size = logs.size()
    assert w.tabs.currentWidget() is original
    w.tabs.setCurrentWidget(w.auto_rng_tab)
    assert w.easycon_tab.virtual_controller_enabled
    logs.close()
    assert not logs.isVisible()
    worker = Thread(target=lambda: w._run_log_buffer.publish("后台测试", "关闭后仍收到日志", level="ERROR"))
    worker.start()
    worker.join()
    settle()
    assert "关闭后仍收到日志" in messages(panel)
    assert w._run_log_buffer.snapshot()[-1].message == "关闭后仍收到日志"
    assert logs.unread_problem_count == 1
    assert w.live_log_panel.problem_label.isVisible()
    w.live_log_panel.expand_button.click()
    settle()
    assert logs.isVisible() and w.run_log_window is logs
    assert panel.log_model is model and logs.size() == size
    assert "关闭后仍收到日志" in messages(panel)
    assert logs.unread_problem_count == 0 and w.live_log_panel.problem_label.isHidden()
    w._show_run_logs(None)
    assert w.run_log_window is logs and w.tabs.currentWidget() is w.auto_rng_tab


def test_live_source_current_run_and_historical_round_routes_are_independent(app, window):
    w = window
    w._active_auto_rng_run_id, w._active_auto_rng_round_id = "current", 2
    entries = [("old", 2, "历史同编号轮次"), ("current", 1, "当前运行上一轮"),
               ("current", 2, "当前运行本轮")]
    for run, round_id, message in entries:
        w._run_log_buffer.publish("自动定点", message, run_id=run, round_id=round_id)
    w._run_log_buffer.publish("OCR", "历史轮次的 OCR", run_id="old", round_id=2)
    settle()
    log = w.run_log_window.log_panel
    w.tabs.setCurrentWidget(w.auto_rng_tab)
    w.auto_rng_tab.view_log_button.click()
    assert messages(log) == ["当前运行本轮"]
    assert "当前运行" in log.correlation_label.text()
    w.run_log_window.close()
    w.live_log_panel.source_combo.setCurrentIndex(w.live_log_panel.source_combo.findData("自动定点"))
    w.live_log_panel.expand_button.click()
    assert all(message in messages(log) for _, _, message in entries)
    assert log.correlation_frame.isHidden()
    w.history_tab.begin_run("old", "帝牙卢卡")
    w.history_tab.cycle_start(2, run_id="old", round_id=2)
    w.tabs.setCurrentWidget(w.run_records_tab)
    w.history_tab.related_logs_button.click()
    assert messages(log) == ["历史同编号轮次", "历史轮次的 OCR"]
    assert log.source_combo.currentData() is None
    assert "第 2 轮" in log.correlation_label.text()
    assert w.tabs.currentWidget() is w.run_records_tab
    w.help_menu_controller.view_run_logs_action.trigger()
    assert all(message in messages(log) for _, _, message in entries)
    assert log.correlation_frame.isHidden() and log.source_combo.currentData() is None


@pytest.mark.parametrize("size", [(1150, 880), (860, 600), (854, 480)])
def test_realtime_fold_restore_preserves_state_and_keeps_video_and_actions(app, window, monkeypatch, size):
    w, live = window, window.live_log_panel
    monkeypatch.setattr(w, "_screen_available_geometry", lambda: QRect(0, 0, 2400, 1400))
    w.resize(*size)
    w.tabs.setCurrentWidget(w.run_records_tab)
    for index in range(40):
        w._run_log_buffer.publish("折叠测试", f"保留 {index}")
    settle()
    live.source_combo.setCurrentIndex(live.source_combo.findData("折叠测试"))
    live.follow_check.setChecked(False)
    live.text.verticalScrollBar().setValue(0)
    document = live.text.document()
    live.collapse_button.click()
    settle()
    assert live.collapsed and live.body.isHidden() and live.height() == 40
    assert live.expand_button.text() == "查看完整日志" and live.expand_button.isVisible()
    assert w.preview_label.isVisible() and w.monitor_preview_scroll.verticalScrollBar().maximum() == 0
    for control in (live.collapse_button, live.expand_button):
        assert live.rect().contains(QRect(control.mapTo(live, QPoint()), control.size()))
    worker = Thread(target=lambda: w._run_log_buffer.publish("折叠测试", "折叠期间更新"))
    worker.start()
    worker.join()
    settle()
    assert "折叠期间更新" in live.text.toPlainText()
    live.expand_button.click()
    assert w.run_log_window.log_panel.source_combo.currentData() == "折叠测试"
    w.run_log_window.close()
    live.collapse_button.click()
    settle()
    assert not live.collapsed and live.body.isVisible()
    assert live.text.document() is document
    assert live.source_combo.currentData() == "折叠测试" and not live.follow_check.isChecked()
    assert live.text.verticalScrollBar().value() == 0
    assert live.text.viewport().height() >= live.text.fontMetrics().height() + 4
    assert w.preview_label.isVisible()


def test_complete_window_log_operations_preserve_shared_buffer(app, window, monkeypatch, tmp_path):
    w, logs = window, window.run_log_window
    w._run_log_buffer.publish("操作测试", '<完整消息> & "中文"', level="ERROR")
    settle()
    logs.show_logs("操作测试")
    panel = logs.log_panel
    panel.search_edit.setText("完整消息")
    panel.level_combo.setCurrentIndex(panel.level_combo.findData("ERROR"))
    panel.copy_button.click()
    assert '<完整消息> & "中文"' in QApplication.clipboard().text()
    output = tmp_path / "导出日志.txt"
    monkeypatch.setattr("auto_bdsp_rng.ui.run_log_panel.QFileDialog.getSaveFileName", lambda *args: (str(output), ""))
    panel.export_button.click()
    assert '<完整消息> & "中文"' in output.read_text(encoding="utf-8")
    calls = []
    panel._open_log_dir_callback = lambda: calls.append("directory")
    panel.open_dir_action.trigger()
    assert calls == ["directory"]
    snapshot = w._run_log_buffer.snapshot()
    panel.clear_action.trigger()
    assert panel.log_model.rowCount() == 0 and w._run_log_buffer.snapshot() == snapshot
    assert '<完整消息> & "中文"' in w.live_log_panel.text.toPlainText()
    w._run_log_buffer.publish("操作测试", "新的完整消息", level="ERROR")
    settle()
    assert messages(panel) == ["新的完整消息"]
