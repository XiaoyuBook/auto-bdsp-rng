from auto_bdsp_rng.log_events import LogMessage, problem_context
from threading import Thread

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from tests.test_easycon_panel import connect_native
from tests.test_run_records_ui import app
from tests.test_start_readiness import window
from tests.test_workspace_layout import settle
from tests.ui_wait import close_widget


def messages(panel):
    return [entry.message for entry in panel.visible_entries()]


def test_complete_log_window_reuses_model_and_receives_workers_while_closed(app, window):
    w = window
    w.tabs.setCurrentWidget(w.easycon_tab)
    assert connect_native(w.easycon_tab)
    backend = w.easycon_tab.native_backend
    assert backend.connected_port == w.easycon_tab._connection_port()
    assert w.easycon_tab._native_lifecycle_thread is None
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
    worker = Thread(target=lambda: w._run_log_buffer.publish("后台测试", LogMessage("关闭后仍收到日志", summary="关闭后仍收到日志"), level="ERROR"))
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
    close_widget(w)
    assert w._close_accepted and w.easycon_tab._shutdown_complete
    assert not w.easycon_tab.virtual_controller_enabled
    assert w.easycon_tab._native_lifecycle_thread is None
    assert w.easycon_tab._close_preparation_thread is None
    assert w.easycon_tab._keyboard_hook is None
    assert w.easycon_tab.native_backend is None and backend.closed
    assert backend.connected_port is None


def test_live_source_current_run_and_historical_round_routes_are_independent(app, window):
    w = window
    w._active_auto_rng_run_id, w._active_auto_rng_round_id = "current", 2
    entries = [("old", 2, "历史同编号轮次"), ("current", 1, "当前运行上一轮"),
               ("current", 2, "当前运行本轮")]
    for run, round_id, message in entries:
        w._run_log_buffer.publish("自动定点", LogMessage(message, summary=message), run_id=run, round_id=round_id)
    w._run_log_buffer.publish("OCR", LogMessage("历史轮次的 OCR", summary="历史轮次的 OCR"), run_id="old", round_id=2)
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
        w._run_log_buffer.publish("折叠测试", LogMessage(f"保留 {index}", summary=f"保留 {index}"))
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
    worker = Thread(target=lambda: w._run_log_buffer.publish("折叠测试", LogMessage("折叠期间更新", summary="折叠期间更新")))
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
    w._run_log_buffer.publish("操作测试", LogMessage('<完整消息> & "中文"', summary='<完整消息> & "中文"'), level="ERROR")
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
    w._run_log_buffer.publish("操作测试", LogMessage("新的完整消息", summary="新的完整消息"), level="ERROR")
    settle()
    assert messages(panel) == ["新的完整消息"]


def test_all_main_window_log_sinks_keep_internal_text_out_of_live_view(app, window):
    w = window
    raw = 'Traceback: C:\\private\\device.json; Seed=123; monotonic=9; backend_ready=False'
    w._write_run_log("视频源", raw)
    w._write_run_log("OCR", raw, level="ERROR")
    w.auto_rng_tab.add_log(raw)
    w.auto_rng_tab.add_log(raw, level="ERROR")
    w.auto_tid_rng_tab.add_log(raw, level="WARNING")
    w.easycon_tab._append_log("info", raw)
    w.easycon_tab._append_log("stdout", raw)
    w.easycon_tab._append_log("stderr", raw)
    w._write_run_log("QQ 通知", raw, level="WARNING")
    w._write_run_log("Seed 捕捉", LogMessage(raw, summary="测种完成，正在搜索目标"))
    settle()
    live, panel = w.live_log_panel, w.run_log_window.log_panel
    for source in ("OCR", "自动定点", "自动 TID", "伊机控", "QQ 通知", "Seed 捕捉", None):
        live.source_combo.setCurrentIndex(live.source_combo.findData(source))
        live.set_collapsed(True)
        live.set_collapsed(False)
        assert "Traceback" not in live.text.toPlainText()
        assert "private" not in live.text.toPlainText()
        assert "monotonic" not in live.text.toPlainText()
        assert "backend_ready" not in live.text.toPlainText()
    assert "测种完成，正在搜索目标" in live.text.toPlainText()
    assert "脚本返回错误输出" in live.text.toPlainText()
    assert "操作未能完成" in live.text.toPlainText()
    assert any(entry.message == raw for entry in w._run_log_buffer.snapshot())
    assert "Traceback" not in panel._filtered_text()
    assert "Traceback" not in w.auto_rng_tab.latest_log_label.toolTip()
    assert "Traceback" not in w.auto_tid_rng_tab.latest_log_label.toolTip()
    assert "Traceback" not in w.easycon_tab.latest_log_label.toolTip()
    panel.view_combo.setCurrentIndex(panel.view_combo.findData("all"))
    assert raw in panel._filtered_text()


def test_round_restart_shows_one_actionable_warning_instead_of_a_history_duplicate(app, window):
    window._handle_auto_history_event("cycle_start", (1,))
    message = LogMessage(
        "seed 捕获失败（连续 1/5）: 原始原因，进入下一轮测种",
        summary="测种失败（连续 1/5），将重新运行测种脚本",
        context=problem_context("原始原因", operation="眨眼测种"), level="WARNING", event_type="seed.retry",
    )
    window._handle_auto_history_event("cycle_restart", (message,))
    window.auto_rng_tab.add_log(message)
    settle()
    warnings = [entry for entry in window._run_log_buffer.snapshot() if entry.user_visible and entry.level == "WARNING"]
    assert len(warnings) == 1 and warnings[0].source == "自动定点"
    assert "操作遇到问题" not in window.live_log_panel.text.toPlainText()
    assert "测种失败（连续 1/5），将重新运行测种脚本" in window.live_log_panel.text.toPlainText()
    assert "眼睛框选" not in window.live_log_panel.text.toPlainText()
    assert "超时" not in window.live_log_panel.text.toPlainText()
    archived = next(entry for entry in window._run_log_buffer.snapshot() if entry.event_type == "history.round_restarted")
    assert not archived.user_visible and "原始原因" in archived.full_text()
    assert window.run_log_window.unread_problem_count == 1
