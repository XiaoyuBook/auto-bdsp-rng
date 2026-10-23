from __future__ import annotations

from auto_bdsp_rng.log_events import LogMessage

import pytest
import time

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox, QTabWidget, QWidget
from PySide6.QtTest import QTest

from auto_bdsp_rng.automation.auto_tid_rng import AutoTidRngPhase, AutoTidRngProgress
from auto_bdsp_rng.run_log import RunLogManager
from auto_bdsp_rng.automation.easycon import EasyConStatus
from auto_bdsp_rng.ui import MainWindow
from auto_bdsp_rng.ui.history_panel import HistoryPanel
from auto_bdsp_rng.ui.run_log_panel import MAX_LOG_ENTRIES, RunLogBuffer, RunLogPanel
from auto_bdsp_rng.ui.run_records_panel import RunRecordsPanel
from auto_bdsp_rng.ui.run_log_window import RunLogWindow
import auto_bdsp_rng.ui.main_window as main_window_module
from tests.ui_wait import close_all_widgets, wait_until


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setattr(MainWindow, "_start_ocr_warmup", lambda self: None)
    monkeypatch.setattr(main_window_module, "should_show_startup_notice", lambda: False)
    application = QApplication.instance() or QApplication([])
    yield application
    close_all_widgets(application)


def _messages(panel: RunLogPanel) -> list[str]:
    return [entry.message for entry in panel.visible_entries()]


@pytest.mark.parametrize("invalidate", ["refilter", "no-candidate", "seed"])
def test_round_candidate_snapshot_clears_when_search_is_invalidated(app, invalidate):
    from types import SimpleNamespace

    history = HistoryPanel()
    history.begin_run("run-1", "帝牙卢卡")
    history.cycle_start(1)
    history.seed_captured("first-seed", 0, 0, 1000)
    history.candidates_found([SimpleNamespace(advances=100)], 0)
    assert history.round_candidate_table.rowCount() == 1

    if invalidate == "refilter":
        history.candidates_refiltered([], -1)
    elif invalidate == "no-candidate":
        history.target_missed(100, 101)
        history.cycle_no_candidate()
    else:
        history.seed_captured("new-seed", 0, 0, 1000)
    assert history.round_candidate_table.rowCount() == 0
    assert history.locked_adv_value_label.text() == "-"
    assert history.candidate_value_label.text() == "0 个"

    history.cycle_start(2)
    history.candidates_found([SimpleNamespace(advances=200)], 0)
    assert history.round_candidate_table.rowCount() == 1
    history.round_list.setCurrentRow(1)
    assert history.round_candidate_table.rowCount() == 0
    assert "adv=100" in history.text_view.toPlainText()


@pytest.mark.parametrize("transition", ["next-round", "new-rng-run", "new-tid-run", "clear"])
def test_auto_follow_new_round_renders_only_its_snapshot_and_events(app, transition):
    from types import SimpleNamespace

    history = HistoryPanel()
    history.begin_run("old-run", "帝牙卢卡")
    history.cycle_start(1)
    history.seed_captured("old-seed", 0, 0, 1000)
    history.candidates_found([SimpleNamespace(advances=100)], 0)
    assert history.round_candidate_table.rowCount() == 1

    if transition == "next-round":
        history.cycle_start(2)
    else:
        if transition == "clear":
            history.clear()
        target = "自动 TID" if transition == "new-tid-run" else "帝牙卢卡"
        history.begin_run("new-run", target)
        history.cycle_start(1)
    record = history._selected_record()
    assert record is not None and record.uid == history._active_uid
    assert record.candidate_snapshot is None
    assert history.round_candidate_table.rowCount() == 0
    assert history._feed_layout.count() == len(record.entries)
    assert history.locked_adv_value_label.text() == "-"
    history.seed_captured("new-seed", 0, 0, 1000)
    assert history.round_candidate_table.rowCount() == 0
    assert history._feed_layout.count() == len(record.entries)


def test_new_round_does_not_replace_a_selected_historical_round(app):
    from types import SimpleNamespace

    history = HistoryPanel()
    history.begin_run("run-a", "帝牙卢卡")
    history.cycle_start(1)
    history.candidates_found([SimpleNamespace(advances=100)], 0)
    history.cycle_start(2)
    history.candidates_found([SimpleNamespace(advances=200)], 0)
    history.round_list.setCurrentRow(1)
    selected = history._selected_record()
    entries_before = history._feed_layout.count()

    history.cycle_start(3)
    history.candidates_found([SimpleNamespace(advances=300)], 0)

    assert history._selected_record() is selected
    assert selected.cycle_index == 1
    assert history.round_candidate_table.rowCount() == 1
    assert history.round_candidate_table.item(0, 2).text() == "100"
    assert history._feed_layout.count() == entries_before == len(selected.entries)


@pytest.mark.parametrize("reverse_matches", [0, 1])
def test_reverse_lookup_keeps_search_snapshot_count_and_records_its_own_matches(app, reverse_matches):
    from types import SimpleNamespace

    history = HistoryPanel()
    history.begin_run("run-a", "帝牙卢卡")
    history.cycle_start(1)
    candidates = [SimpleNamespace(advances=adv, pid=adv, ec=adv) for adv in (100, 200, 300)]
    history.candidates_found(candidates, 0)
    history.reverse_lookup_results(candidates[:reverse_matches])

    record = history._selected_record()
    assert record.candidate_count == 3
    assert history.round_candidate_table.rowCount() == 3
    assert history.candidate_value_label.text() == "3 个"
    assert "3 个候选" in history.round_summary.text()
    expected = "反查结果 (1 个匹配)" if reverse_matches else "反查结果: 未找到匹配个体"
    assert expected in history.text_view.toPlainText()

    history.cycle_start(2)
    history.round_list.setCurrentRow(1)
    assert history._selected_record() is record
    assert history.round_candidate_table.rowCount() == 3
    assert history.candidate_value_label.text() == "3 个"
    assert "3 个候选" in history.round_summary.text()


@pytest.mark.parametrize("after_miss", ["next-round", "finish"])
def test_starter_missed_round_keeps_its_outcome_and_reason(app, tmp_path, after_miss):
    window = MainWindow(run_log_manager=RunLogManager(tmp_path / "logs"))
    history = window.history_tab
    history.begin_run("starter-run", "草苗龟")
    window._handle_auto_history_event("cycle_start", (1,))
    window._handle_auto_history_event("target_missed", (100, 101))
    reason = "已错过选择帧，本轮不选精灵"
    window._handle_auto_history_event("starter_missed", (reason, 66))

    if after_miss == "next-round":
        window._handle_auto_history_event("cycle_start", (2,))
        history.round_list.setCurrentRow(1)
    else:
        history.finish_run("starter-run", "已完成", round_id=1)
    assert history.detail_status_label.text() == "错过目标"
    assert "御三家选择未执行" in history.detail_title_label.text()
    assert reason in history.warning_label.text()
    assert f"使用 delay: 66  原因: {reason}" in history.text_view.toPlainText()
    assert any(reason in entry.message for entry in window._run_log_buffer.snapshot())


def test_compact_log_controls_remain_centered_and_inside_their_panels(app, tmp_path):
    window = MainWindow(run_log_manager=RunLogManager(tmp_path / "logs"))
    window.resize(1150, 900)
    window.show()

    auto_panel = window.auto_rng_tab
    window.tabs.setCurrentWidget(auto_panel)
    app.processEvents()
    assert auto_panel.log_group.isHidden()
    assert window.view_status_logs_button.isVisible()
    assert window.view_status_logs_button.height() <= window.statusBar().height()

    easycon_panel = window.easycon_tab
    window.tabs.setCurrentWidget(easycon_panel)
    easycon_panel.latest_log_label.setText(
        "脚本已就绪，等待连接伊机控后运行；这是一条用于验证布局的较长最近消息，"
        "需要确保窄列中的多行内容不会被裁切。"
    )
    app.processEvents()
    # A font with taller lines can schedule a second height-for-width layout.
    QTest.qWait(10)
    assert easycon_panel.overview_panel.isHidden()
    assert easycon_panel.output_toggle.isVisible()
    assert easycon_panel.script_error_label.text() == "暂无脚本错误"
    assert window.live_log_panel.isVisible()
    assert not easycon_panel.tools_panel.isVisible()
    original_editor = easycon_panel.editor
    assert easycon_panel.record_btn.isVisible()
    assert easycon_panel.pause_btn.isHidden()
    assert easycon_panel.editor is original_editor
    assert window.easycon_header_button.menu() is easycon_panel.controller_tools_menu
    assert easycon_panel.action_row.isHidden()
    assert easycon_panel.findChild(QWidget, "EasyConControlPanel") is None
    toolbar = easycon_panel.findChild(QWidget, "EasyConToolbar")
    assert toolbar is not None
    assert easycon_panel.elapsed_label.parentWidget() is toolbar
    assert easycon_panel.run_button.parentWidget() is toolbar
    assert easycon_panel.stop_button.parentWidget() is toolbar
    elapsed_rect = easycon_panel.elapsed_label.geometry()
    run_rect = easycon_panel.run_button.geometry()
    stop_rect = easycon_panel.stop_button.geometry()
    assert toolbar.rect().contains(elapsed_rect)
    assert toolbar.rect().contains(run_rect)
    assert toolbar.rect().contains(stop_rect)
    assert elapsed_rect.top() == run_rect.top()
    assert run_rect.top() == stop_rect.top()
    assert elapsed_rect.height() == run_rect.height() == stop_rect.height() == 32
    assert elapsed_rect.right() < run_rect.left()
    assert run_rect.right() < stop_rect.left()

    records_panel = window.run_records_tab
    window._show_run_logs(None)
    log = window.run_log_window.log_panel
    app.processEvents()
    footer = log.footer_frame
    assert footer.height() == 42
    for button in (
        log.copy_button,
        log.export_button,
    ):
        assert button.height() == 34
        assert footer.rect().contains(button.geometry())
        assert button.geometry().top() == 4
        assert footer.rect().bottom() - button.geometry().bottom() == 4
    assert log.clear_button.isHidden()
    assert log.open_dir_button.isHidden()
    assert [action.text() for action in log.more_button.menu().actions()] == ["清空显示", "日志目录"]


def test_run_records_header_and_history_empty_state_switch_cleanly(app):
    history = HistoryPanel()
    records = RunRecordsPanel(history)
    records.resize(1000, 650)
    records.show()
    app.processEvents()

    assert records.heading.height() == 34
    assert records.title_label.text() == "轮次记录"
    assert records.subtitle_label.text() == "本次会话"
    assert records.live_status_label.text() == "等待任务"
    assert history.empty_state.isVisible()
    assert history.empty_state_title.text() == "暂无轮次记录"
    assert not history.round_splitter.isVisible()
    assert not history.copy_button.isEnabled()
    assert not history.clear_button.isEnabled()
    assert not history.export_button.isEnabled()

    history.begin_run("run-1", "帝牙卢卡")
    history.cycle_start(1)
    app.processEvents()
    assert not history.empty_state.isVisible()
    assert history.round_splitter.isVisible()
    assert history.clear_button.isEnabled()
    assert history.export_button.isEnabled()

    history.search_edit.setText("不存在的轮次")
    deadline = time.monotonic() + 5
    while (history._search_inflight or history._filter_start_timer.isActive()) and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.003)
    assert not history._search_inflight and not history._filter_start_timer.isActive()
    assert history.empty_state.isVisible()
    assert history.empty_state_title.text() == "没有符合条件的轮次"
    assert not history.round_splitter.isVisible()

    history.search_edit.clear()
    app.processEvents()
    assert not history.empty_state.isVisible()
    assert history.round_splitter.isVisible()

    history.clear()
    app.processEvents()
    assert history.empty_state.isVisible()
    assert history.empty_state_title.text() == "暂无轮次记录"
    assert not history.round_splitter.isVisible()
    assert not history.clear_button.isEnabled()
    assert not history.export_button.isEnabled()


@pytest.mark.parametrize("width", [430, 1150])
def test_round_records_present_history_and_session_without_secondary_navigation(app, width):
    records = RunRecordsPanel(HistoryPanel())
    records.resize(width, 600)
    records.set_session_context("自动 TID")
    records.set_active_round(1)
    records.show()
    app.processEvents()
    assert not hasattr(records, "view_tabs") and not hasattr(records, "log_panel")
    assert records.history_panel.isVisible()
    assert records.live_status_label.text() == "第 1 轮进行中"
    assert records.live_status_label.toolTip() == "自动 TID"
    assert records.heading.rect().contains(records.live_status_label.geometry())
    assert records.heading.rect().contains(records.title_label.geometry())
    records.set_run_finished()
    assert records.live_status_label.text() == "运行已结束"


def test_run_log_buffer_hard_cap_and_panel_small_capacity_receive_live_entries(app):
    hard_cap_buffer = RunLogBuffer(max_entries=MAX_LOG_ENTRIES + 100)

    for index in range(MAX_LOG_ENTRIES + 1):
        hard_cap_buffer.publish("容量测试", LogMessage(f"记录 {index}", summary=f"记录 {index}"))

    hard_cap_entries = hard_cap_buffer.snapshot()
    assert hard_cap_buffer.max_entries == MAX_LOG_ENTRIES
    assert len(hard_cap_entries) == MAX_LOG_ENTRIES
    assert hard_cap_entries[0].message == "记录 1"
    assert hard_cap_entries[-1].message == f"记录 {MAX_LOG_ENTRIES}"

    small_buffer = RunLogBuffer(max_entries=3)
    panel = RunLogPanel(small_buffer)
    for message in ("第一条", "第二条", "第三条", "第四条"):
        small_buffer.publish("自动定点", LogMessage(message, summary=message))
    app.processEvents()

    assert [entry.message for entry in small_buffer.snapshot()] == ["第二条", "第三条", "第四条"]
    assert panel.log_model.rowCount() == 3
    assert _messages(panel) == ["第二条", "第三条", "第四条"]


def test_run_log_panel_combines_source_level_and_keyword_filters(app):
    buffer = RunLogBuffer()
    panel = RunLogPanel(buffer)
    buffer.publish("自动定点", LogMessage("needle but informational", summary="needle but informational"), level="INFO")
    buffer.publish("自动定点", LogMessage("ordinary failure", summary="ordinary failure"), level="ERROR")
    buffer.publish("自动 TID", LogMessage("needle from another source", summary="needle from another source"), level="ERROR")
    buffer.publish("自动定点", LogMessage("Final NEEDLE failure", summary="Final NEEDLE failure"), level="ERROR")
    app.processEvents()

    panel.set_source_filter("自动定点")
    panel.level_combo.setCurrentIndex(panel.level_combo.findData("ERROR"))
    panel.search_edit.setText("needle")
    app.processEvents()

    entries = panel.visible_entries()
    assert [(entry.source, entry.level, entry.message) for entry in entries] == [
        ("自动定点", "ERROR", "Final NEEDLE failure")
    ]
    assert panel.count_label.text() == "显示 1 条 · 当前会话共 4 条"


def test_round_history_link_filters_by_run_and_round_and_can_be_cancelled(app):
    buffer = RunLogBuffer()
    buffer.publish("自动定点", LogMessage("本轮日志", summary="本轮日志"), run_id="run-a", round_id=8)
    buffer.publish("自动定点", LogMessage("同轮次号但另一次运行", summary="同轮次号但另一次运行"), run_id="run-b", round_id=8)
    buffer.publish("自动定点", LogMessage("同一次运行但另一轮", summary="同一次运行但另一轮"), run_id="run-a", round_id=9)
    history = HistoryPanel()
    history.cycle_start(8, run_id="run-a", round_id=8, target_label="谢米")
    logs = RunLogWindow(buffer)
    history.related_logs_requested.connect(lambda run, round_id: logs.show_logs(run_id=run, round_id=round_id))
    assert history.related_logs_button.isEnabled()
    assert history.related_logs_button.text() == "查看本轮日志"
    history.related_logs_button.click()
    app.processEvents()
    assert logs.isVisible() and logs.log_panel.correlation_frame.isVisibleTo(logs)
    assert _messages(logs.log_panel) == ["本轮日志"]
    logs.log_panel.correlation_clear_button.click()
    app.processEvents()
    assert not logs.log_panel.correlation_frame.isVisibleTo(logs)
    assert set(_messages(logs.log_panel)) == {"本轮日志", "同轮次号但另一次运行", "同一次运行但另一轮"}


def test_clear_display_does_not_clear_the_session_buffer(app):
    buffer = RunLogBuffer()
    panel = RunLogPanel(buffer)
    buffer.publish("应用", LogMessage("保留一", summary="保留一"))
    buffer.publish("应用", LogMessage("保留二", summary="保留二"))
    app.processEvents()

    panel.clear_button.click()
    app.processEvents()

    assert panel.log_model.rowCount() == 0
    assert [entry.message for entry in buffer.snapshot()] == ["保留一", "保留二"]

    buffer.publish("应用", LogMessage("清空后新增", summary="清空后新增"))
    app.processEvents()
    assert _messages(panel) == ["清空后新增"]

    restored_view = RunLogPanel(buffer)
    app.processEvents()
    assert _messages(restored_view) == ["保留一", "保留二", "清空后新增"]


def test_log_empty_artwork_tracks_filtered_rows_without_changing_the_buffer(app):
    from PySide6.QtCore import Qt

    buffer = RunLogBuffer()
    panel = RunLogPanel(buffer)
    panel.resize(900, 500)
    panel.show()
    app.processEvents()
    assert panel.empty_state.isVisible()
    assert panel.empty_state.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    buffer.publish("应用", LogMessage("可见记录", summary="可见记录"))
    app.processEvents()
    assert not panel.empty_state.isVisible()
    panel.search_edit.setText("未匹配的内容")
    app.processEvents()
    assert panel.empty_state.isVisible()
    assert panel.empty_state.title.text() == "没有符合条件的日志"
    assert panel.visible_entries() == ()
    assert len(buffer.snapshot()) == 1

    panel.search_edit.clear()
    app.processEvents()
    assert not panel.empty_state.isVisible()
    panel.clear_display()
    app.processEvents()
    assert panel.empty_state.isVisible()
    assert len(buffer.snapshot()) == 1
    buffer.publish("应用", LogMessage("新记录", summary="新记录"))
    app.processEvents()
    assert not panel.empty_state.isVisible()
    assert _messages(panel) == ["新记录"]
    panel.close()
    panel.deleteLater()


def test_save_checkbox_rolls_back_when_callback_fails(app):
    requested_states: list[bool] = []

    def fail(requested: bool) -> bool:
        requested_states.append(requested)
        raise RuntimeError("settings unavailable")

    panel = RunLogPanel(
        RunLogBuffer(),
        save_enabled=True,
        set_save_enabled=fail,
    )

    panel.save_check.setChecked(False)
    app.processEvents()

    assert requested_states == [False]
    assert panel.save_check.isChecked() is True


def test_main_window_uses_run_records_as_sixth_tab_and_keeps_history_alias(
    app,
    tmp_path,
):
    manager = RunLogManager(tmp_path / "logs")
    window = MainWindow(run_log_manager=manager)

    assert window.tabs.count() == 7
    assert window.tabs.widget(5) is window.run_records_tab
    assert window.tabs.tabText(5) == "轮次记录"
    assert not hasattr(window.run_records_tab, "view_tabs")
    assert window.run_log_window.parentWidget() is window
    assert window.run_records_tab.history_panel is window.history_tab
    assert window.records_tab is window.run_records_tab


def test_disabled_disk_logging_still_reaches_the_live_log_page(app, tmp_path):
    manager = RunLogManager(tmp_path / "logs")
    window = MainWindow(run_log_manager=manager)
    assert manager.enabled is False

    window._write_run_log("自动定点", "仅进入实时页", level="WARNING")
    app.processEvents()

    entries = window.run_log_window.log_panel.visible_entries()
    assert any(
        (entry.source, entry.level, entry.message) == ("自动定点", "WARNING", "仅进入实时页")
        for entry in entries
    )
    assert manager.current_path is None


@pytest.mark.parametrize(
    ("panel_attribute", "source"),
    (
        ("auto_rng_tab", "自动定点"),
        ("auto_tid_rng_tab", "自动 TID"),
        ("easycon_tab", "伊机控"),
    ),
)
def test_business_page_log_request_opens_logs_with_its_source_selected(
    app,
    tmp_path,
    panel_attribute,
    source,
):
    window = MainWindow(run_log_manager=RunLogManager(tmp_path / panel_attribute / "logs"))
    panel = getattr(window, panel_attribute)
    window.tabs.setCurrentWidget(panel)

    panel.runLogRequested.emit()
    app.processEvents()

    assert window.tabs.currentWidget() is panel
    assert window.run_log_window.isVisible()
    assert window.run_log_window.log_panel.source_combo.currentData() == source


def test_auto_rng_round_button_opens_round_records_instead_of_detailed_logs(
    app,
    tmp_path,
):
    window = MainWindow(run_log_manager=RunLogManager(tmp_path / "logs"))
    window._show_run_logs(None)
    window.tabs.setCurrentWidget(window.auto_rng_tab)

    window.auto_rng_tab.runtime_log_button.click()
    app.processEvents()

    assert window.tabs.currentWidget() is window.run_records_tab
    assert window.history_tab.parentWidget() is window.run_records_tab


def test_auto_rng_round_button_preserves_unread_detailed_log_count(app, tmp_path):
    window = MainWindow(run_log_manager=RunLogManager(tmp_path / "logs"))
    window.show()
    window._show_run_logs(None)
    app.processEvents()
    window.run_log_window.close()
    window.tabs.setCurrentWidget(window.auto_rng_tab)
    window._run_log_buffer.publish("自动定点", LogMessage("需要查看的错误", summary="需要查看的错误"), level="ERROR")
    app.processEvents()
    assert window.run_log_window.unread_problem_count == 1
    window.auto_rng_tab.runtime_log_button.click()
    app.processEvents()
    assert window.tabs.currentWidget() is window.run_records_tab
    assert not window.run_log_window.isVisible()
    assert window.run_log_window.unread_problem_count == 1
    assert window.view_status_logs_button.text() == "完整日志 (1)"


def test_auto_rng_latest_message_uses_only_the_workspace_footer(app, tmp_path):
    window = MainWindow(run_log_manager=RunLogManager(tmp_path / "logs"))
    window.tabs.setCurrentWidget(window.auto_rng_tab)
    # Dispatch and finish startup device enumeration before publishing the log;
    # its progress and completion messages also use the workspace footer.
    app.processEvents()
    wait_until(
        lambda: window._capture_device_enumeration_thread is None
        and window._capture_device_enumeration_request is None
    )
    before = len(window._run_log_buffer.snapshot())

    window.auto_rng_tab.add_log(LogMessage("第一行\n最终消息", summary="最终消息"))
    app.processEvents()

    assert window.auto_rng_tab.log_group.isHidden()
    assert "第一行" in window.auto_rng_tab.log_view.toPlainText()
    assert "最终消息" in window.auto_rng_tab.log_view.toPlainText()
    assert window.statusBar().currentMessage() == "最终消息"
    entries = window._run_log_buffer.snapshot()
    assert len(entries) == before + 1
    assert entries[-1].message == "第一行\n最终消息"


def test_help_menu_opens_unfiltered_detailed_logs(app, tmp_path):
    window = MainWindow(run_log_manager=RunLogManager(tmp_path / "logs"))
    window.run_log_window.log_panel.set_source_filter("自动定点")
    window.tabs.setCurrentWidget(window.auto_rng_tab)

    window.help_menu_controller.view_run_logs_action.trigger()
    app.processEvents()

    assert window.tabs.currentWidget() is window.auto_rng_tab
    assert window.run_log_window.isVisible()
    assert window.run_log_window.log_panel.source_combo.currentData() is None


def test_help_menu_and_log_page_save_switches_stay_synchronized(
    app,
    tmp_path,
    monkeypatch,
):
    saved_states: list[bool] = []
    monkeypatch.setattr(
        main_window_module,
        "set_run_log_enabled",
        lambda enabled: saved_states.append(bool(enabled)) or bool(enabled),
    )
    manager = RunLogManager(tmp_path / "logs")
    window = MainWindow(run_log_manager=manager)
    menu_action = window.help_menu_controller.run_log_save_action
    page_checkbox = window.run_log_window.log_panel.save_check

    assert menu_action.isChecked() is False
    assert page_checkbox.isChecked() is False

    page_checkbox.setChecked(True)
    app.processEvents()

    assert manager.enabled is True
    assert menu_action.isChecked() is True
    assert page_checkbox.isChecked() is True

    menu_action.trigger()
    app.processEvents()

    assert manager.enabled is False
    assert menu_action.isChecked() is False
    assert page_checkbox.isChecked() is False
    assert saved_states == [True, False]


def test_closed_log_window_counts_critical_and_clears_unread_when_reopened(app):
    buffer = RunLogBuffer()
    logs = RunLogWindow(buffer)
    buffer.publish("应用", LogMessage("警告", summary="警告"), level="WARNING")
    buffer.publish("应用", LogMessage("严重错误", summary="严重错误"), level="CRITICAL")
    app.processEvents()
    assert logs.unread_problem_count == 2
    logs.show_logs()
    app.processEvents()
    assert logs.unread_problem_count == 0
    logs.close()
    buffer.publish("应用", LogMessage("关闭期间发生错误", summary="关闭期间发生错误"), level="ERROR")
    app.processEvents()
    assert logs.unread_problem_count == 1
    logs.show_logs()
    app.processEvents()
    assert logs.unread_problem_count == 0
    assert _messages(logs.log_panel) == ["警告", "严重错误", "关闭期间发生错误"]


def test_history_rows_distinguish_runs_without_painting_duplicate_item_text(app):
    history = HistoryPanel()
    history.begin_run("run-a", "帝牙卢卡")
    history.cycle_start(1)
    history.begin_run("run-b", "帝牙卢卡")
    history.cycle_start(1)
    app.processEvents()

    titles = [
        history.round_list.itemWidget(history.round_list.item(row)).title_label.text()
        for row in range(history.round_list.count())
    ]
    assert titles == ["运行 2 · 第 1 轮", "运行 1 · 第 1 轮"]
    assert all(history.round_list.item(row).text() == "" for row in range(2))


def test_auto_tid_progress_creates_round_records_and_links_exact_round_logs(app, tmp_path):
    window = MainWindow(run_log_manager=RunLogManager(tmp_path / "logs"))
    window._active_auto_tid_run_id = "tid-run"
    window._active_auto_tid_round_id = 1
    window.history_tab.begin_run("tid-run", "自动 TID")
    window.history_tab.cycle_start(1, run_id="tid-run", round_id=1, target_label="自动 TID")
    window._write_run_log("自动 TID", LogMessage("第一轮日志", summary="第一轮日志"))

    window.auto_tid_rng_tab.apply_progress(
        AutoTidRngProgress(
            phase=AutoTidRngPhase.SEARCH_TARGET,
            loop_index=2,
            log_message=LogMessage("第二轮开始", summary="第二轮开始"),
        )
    )
    app.processEvents()

    first_record = window.history_tab._round_records[
        window.history_tab._group_key("tid-run", 1)
    ]
    second_record = window.history_tab._round_records[
        window.history_tab._group_key("tid-run", 2)
    ]
    assert "第二轮开始" not in "\n".join(first_record.plain_lines)
    assert "第二轮开始" in "\n".join(second_record.plain_lines)
    assert window.history_tab.current_run_id == "tid-run"
    assert window.history_tab.current_round_id == 2
    assert window.history_tab.detail_status_label.text() == "进行中"
    window.history_tab.related_logs_button.click()
    app.processEvents()
    assert _messages(window.run_log_window.log_panel) == ["第二轮开始"]


def test_start_auto_tid_prepares_first_round_before_worker_logs(app, tmp_path, monkeypatch):
    window = MainWindow(run_log_manager=RunLogManager(tmp_path / "logs"))
    started = []
    monkeypatch.setattr(window, "_ensure_preview_for_auto_rng", lambda: True)
    monkeypatch.setattr(window.easycon_tab, "_native_status", lambda: EasyConStatus.BRIDGE_CONNECTED)
    monkeypatch.setattr(window, "_build_auto_tid_rng_services", lambda _config: object())
    monkeypatch.setattr(window.auto_tid_rng_tab, "run_with_runner", started.append)

    window._start_auto_tid_rng(object())  # type: ignore[arg-type]

    assert len(started) == 1
    assert window._active_auto_tid_run_id is not None
    assert window._active_auto_tid_round_id == 1
    assert window.history_tab.current_round_id == 1
    assert window.run_records_tab.live_status_label.text() == "第 1 轮进行中"


@pytest.mark.parametrize(
    ("source", "status", "expected"),
    (
        ("auto_rng", "已完成", "已完成"),
        ("auto_rng", "失败", "失败"),
        ("auto_tid", "状态：空闲", "已停止"),
    ),
)
def test_run_state_finalizes_active_history_record(app, tmp_path, source, status, expected):
    window = MainWindow(run_log_manager=RunLogManager(tmp_path / source / "logs"))
    run_id = f"{source}-run"
    target = "自动 TID" if source == "auto_tid" else "帝牙卢卡"
    window.history_tab.begin_run(run_id, target)
    window.history_tab.cycle_start(1, run_id=run_id, round_id=1, target_label=target)

    if source == "auto_tid":
        window._active_auto_tid_run_id = run_id
        window._active_auto_tid_round_id = 1
        window.auto_tid_rng_tab.status_badge.setText(status)
        window._handle_auto_tid_run_state_changed(False)
    else:
        window._active_auto_rng_run_id = run_id
        window._active_auto_rng_round_id = 1
        window.auto_rng_tab.status_badge.setText(status)
        window._handle_auto_rng_run_state_changed(False)

    assert window.history_tab.detail_status_label.text() == expected
    assert window.run_records_tab.live_status_label.text() == f"运行{expected}"
    expected_task = "TID" if source == "auto_tid" else "定点"
    assert window.navigation_status.text() == f"● {expected_task} · {expected}"
    assert "第 1 轮" not in window.navigation_status.text()


def test_auto_flows_are_mutually_exclusive_before_start_checks(app, tmp_path, monkeypatch):
    window = MainWindow(run_log_manager=RunLogManager(tmp_path / "logs"))
    warnings = []
    active_thread = object()
    window.auto_rng_tab._runner_thread = active_thread  # type: ignore[assignment]
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *args: warnings.append((args[1], args[2])),
    )
    monkeypatch.setattr(
        window,
        "_ensure_preview_for_auto_rng",
        lambda: pytest.fail("preview checks must not run while another automation is active"),
    )

    try:
        window._start_auto_tid_rng(object())  # type: ignore[arg-type]
    finally:
        window.auto_rng_tab._runner_thread = None

    assert warnings == [
        ("自动流程正在运行", "自动定点正在运行，请先停止当前流程后再启动另一项自动任务。")
    ]
    assert window._active_auto_tid_run_id is None
