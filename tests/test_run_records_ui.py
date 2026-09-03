from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox, QTabWidget, QWidget

from auto_bdsp_rng.automation.auto_tid_rng import AutoTidRngPhase, AutoTidRngProgress
from auto_bdsp_rng.run_log import RunLogManager
from auto_bdsp_rng.ui import MainWindow
from auto_bdsp_rng.ui.history_panel import HistoryPanel
from auto_bdsp_rng.ui.run_log_panel import MAX_LOG_ENTRIES, RunLogBuffer, RunLogPanel
from auto_bdsp_rng.ui.run_records_panel import RunRecordsPanel
import auto_bdsp_rng.ui.main_window as main_window_module


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setattr(MainWindow, "_start_ocr_warmup", lambda self: None)
    monkeypatch.setattr(main_window_module, "should_show_startup_notice", lambda: False)
    application = QApplication.instance() or QApplication([])
    yield application
    for widget in application.topLevelWidgets():
        for timer in widget.findChildren(QTimer):
            timer.stop()
        widget.close()
        widget.deleteLater()
    application.processEvents()


def _messages(panel: RunLogPanel) -> list[str]:
    return [entry.message for entry in panel.visible_entries()]


def test_compact_log_controls_remain_centered_and_inside_their_panels(app, tmp_path):
    window = MainWindow(run_log_manager=RunLogManager(tmp_path / "logs"))
    window.resize(1150, 900)
    window.show()

    auto_panel = window.auto_rng_tab
    window.tabs.setCurrentWidget(auto_panel)
    app.processEvents()
    auto_button_rect = auto_panel.view_log_button.geometry()
    assert auto_panel.view_log_button.height() == 34
    assert auto_panel.log_group.rect().contains(auto_button_rect)
    assert abs(auto_button_rect.center().y() - auto_panel.log_group.rect().center().y()) <= 5

    easycon_panel = window.easycon_tab
    window.tabs.setCurrentWidget(easycon_panel)
    easycon_panel.latest_log_label.setText(
        "脚本已就绪，等待连接伊机控后运行；这是一条用于验证布局的较长最近消息，"
        "需要确保窄列中的多行内容不会被裁切。"
    )
    app.processEvents()
    assert easycon_panel.view_log_button.height() == 30
    assert easycon_panel.overview_panel.rect().contains(easycon_panel.view_log_button.geometry())
    assert easycon_panel.overview_panel.rect().contains(easycon_panel.latest_log_label.geometry())
    assert not easycon_panel.view_log_button.geometry().intersects(
        easycon_panel.latest_log_label.geometry()
    )
    assert easycon_panel.latest_log_label.height() >= easycon_panel.latest_log_label.heightForWidth(
        easycon_panel.latest_log_label.width()
    )
    side_panel = easycon_panel.overview_panel.parentWidget()
    assert easycon_panel.keyboard_control_group.parentWidget() is side_panel
    assert easycon_panel.overview_panel.geometry().bottom() < easycon_panel.action_row.geometry().top()
    assert easycon_panel.action_row.geometry().bottom() < easycon_panel.keyboard_control_group.geometry().top()
    assert easycon_panel.findChild(QWidget, "EasyConControlPanel") is None
    elapsed_rect = easycon_panel.elapsed_label.geometry()
    run_rect = easycon_panel.run_button.geometry()
    assert easycon_panel.action_row.rect().contains(elapsed_rect)
    assert easycon_panel.action_row.rect().contains(run_rect)
    assert elapsed_rect.top() == run_rect.top()
    assert elapsed_rect.bottom() == run_rect.bottom()
    assert elapsed_rect.height() == run_rect.height() == 50
    assert abs(elapsed_rect.width() - run_rect.width()) <= 1

    records_panel = window.run_records_tab
    window.tabs.setCurrentWidget(records_panel)
    records_panel.view_tabs.setCurrentWidget(records_panel.log_panel)
    app.processEvents()
    footer = records_panel.log_panel.footer_frame
    assert footer.height() == 50
    for button in (
        records_panel.log_panel.clear_button,
        records_panel.log_panel.copy_button,
        records_panel.log_panel.export_button,
        records_panel.log_panel.open_dir_button,
    ):
        assert button.height() == 34
        assert footer.rect().contains(button.geometry())
        assert button.geometry().top() == 8
        assert footer.rect().bottom() - button.geometry().bottom() == 8


def test_run_records_header_and_history_empty_state_switch_cleanly(app):
    history = HistoryPanel()
    records = RunRecordsPanel(history, RunLogBuffer())
    records.resize(1000, 650)
    records.show()
    app.processEvents()

    assert records.heading.height() == 42
    assert records.title_label.text() == "日志区"
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
    app.processEvents()
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


def test_run_log_buffer_hard_cap_and_panel_small_capacity_receive_live_entries(app):
    hard_cap_buffer = RunLogBuffer(max_entries=MAX_LOG_ENTRIES + 100)

    for index in range(MAX_LOG_ENTRIES + 1):
        hard_cap_buffer.publish("容量测试", f"记录 {index}")

    hard_cap_entries = hard_cap_buffer.snapshot()
    assert hard_cap_buffer.max_entries == MAX_LOG_ENTRIES
    assert len(hard_cap_entries) == MAX_LOG_ENTRIES
    assert hard_cap_entries[0].message == "记录 1"
    assert hard_cap_entries[-1].message == f"记录 {MAX_LOG_ENTRIES}"

    small_buffer = RunLogBuffer(max_entries=3)
    panel = RunLogPanel(small_buffer)
    for message in ("第一条", "第二条", "第三条", "第四条"):
        small_buffer.publish("自动定点", message)
    app.processEvents()

    assert [entry.message for entry in small_buffer.snapshot()] == ["第二条", "第三条", "第四条"]
    assert panel.log_model.rowCount() == 3
    assert _messages(panel) == ["第二条", "第三条", "第四条"]


def test_run_log_panel_combines_source_level_and_keyword_filters(app):
    buffer = RunLogBuffer()
    panel = RunLogPanel(buffer)
    buffer.publish("自动定点", "needle but informational", level="INFO")
    buffer.publish("自动定点", "ordinary failure", level="ERROR")
    buffer.publish("自动 TID", "needle from another source", level="ERROR")
    buffer.publish("自动定点", "Final NEEDLE failure", level="ERROR")
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
    buffer.publish("自动定点", "本轮日志", run_id="run-a", round_id=8)
    buffer.publish("自动定点", "同轮次号但另一次运行", run_id="run-b", round_id=8)
    buffer.publish("自动定点", "同一次运行但另一轮", run_id="run-a", round_id=9)
    history = HistoryPanel()
    history.cycle_start(8, run_id="run-a", round_id=8, target_label="谢米")
    records = RunRecordsPanel(history, buffer)

    assert history.related_logs_button.isEnabled()
    history.related_logs_button.click()
    app.processEvents()

    assert records.view_tabs.currentIndex() == records.LOG_TAB
    assert records.log_panel.correlation_frame.isVisibleTo(records)
    assert _messages(records.log_panel) == ["本轮日志"]

    records.log_panel.correlation_clear_button.click()
    app.processEvents()

    assert not records.log_panel.correlation_frame.isVisibleTo(records)
    assert set(_messages(records.log_panel)) == {
        "本轮日志",
        "同轮次号但另一次运行",
        "同一次运行但另一轮",
    }


def test_clear_display_does_not_clear_the_session_buffer(app):
    buffer = RunLogBuffer()
    panel = RunLogPanel(buffer)
    buffer.publish("应用", "保留一")
    buffer.publish("应用", "保留二")
    app.processEvents()

    panel.clear_button.click()
    app.processEvents()

    assert panel.log_model.rowCount() == 0
    assert [entry.message for entry in buffer.snapshot()] == ["保留一", "保留二"]

    buffer.publish("应用", "清空后新增")
    app.processEvents()
    assert _messages(panel) == ["清空后新增"]

    restored_view = RunLogPanel(buffer)
    app.processEvents()
    assert _messages(restored_view) == ["保留一", "保留二", "清空后新增"]


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

    assert window.tabs.count() == 6
    assert window.tabs.widget(5) is window.run_records_tab
    assert window.tabs.tabText(5) == "日志区"
    assert window.run_records_tab.view_tabs.count() == 2
    assert window.run_records_tab.view_tabs.tabText(0) == "轮次记录"
    assert window.run_records_tab.view_tabs.tabText(1).startswith("详细日志")
    assert window.run_records_tab.history_panel is window.history_tab
    assert window.records_tab is window.run_records_tab


def test_disabled_disk_logging_still_reaches_the_live_log_page(app, tmp_path):
    manager = RunLogManager(tmp_path / "logs")
    window = MainWindow(run_log_manager=manager)
    assert manager.enabled is False

    window._write_run_log("自动定点", "仅进入实时页", level="WARNING")
    app.processEvents()

    entries = window.run_records_tab.log_panel.visible_entries()
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

    assert window.tabs.currentWidget() is window.run_records_tab
    assert window.run_records_tab.view_tabs.currentIndex() == window.run_records_tab.LOG_TAB
    assert window.run_records_tab.log_panel.source_combo.currentData() == source


def test_help_menu_opens_unfiltered_detailed_logs(app, tmp_path):
    window = MainWindow(run_log_manager=RunLogManager(tmp_path / "logs"))
    window.run_records_tab.log_panel.set_source_filter("自动定点")
    window.tabs.setCurrentWidget(window.auto_rng_tab)

    window.help_menu_controller.view_run_logs_action.trigger()
    app.processEvents()

    assert window.tabs.currentWidget() is window.run_records_tab
    assert window.run_records_tab.view_tabs.currentIndex() == window.run_records_tab.LOG_TAB
    assert window.run_records_tab.log_panel.source_combo.currentData() is None


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
    page_checkbox = window.run_records_tab.log_panel.save_check

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


def test_hidden_log_page_counts_critical_as_unread_and_clears_when_shown(app):
    buffer = RunLogBuffer()
    history = HistoryPanel()
    records = RunRecordsPanel(history, buffer)
    host = QTabWidget()
    other = QWidget()
    host.addTab(records, "日志区")
    host.addTab(other, "其他")
    host.show()
    app.processEvents()

    buffer.publish("应用", "警告", level="WARNING")
    buffer.publish("应用", "严重错误", level="CRITICAL")
    app.processEvents()
    assert records.view_tabs.tabText(records.LOG_TAB) == "详细日志 (2)"

    records.view_tabs.setCurrentIndex(records.LOG_TAB)
    app.processEvents()
    assert records.view_tabs.tabText(records.LOG_TAB) == "详细日志"

    host.setCurrentWidget(other)
    buffer.publish("应用", "隐藏期间发生错误", level="ERROR")
    app.processEvents()
    assert records.view_tabs.tabText(records.LOG_TAB) == "详细日志 (1)"

    host.setCurrentWidget(records)
    app.processEvents()
    assert records.view_tabs.tabText(records.LOG_TAB) == "详细日志"


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
    window._write_run_log("自动 TID", "第一轮日志")

    window.auto_tid_rng_tab.apply_progress(
        AutoTidRngProgress(
            phase=AutoTidRngPhase.SEARCH_TARGET,
            loop_index=2,
            log_message="第二轮开始",
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
    assert _messages(window.run_records_tab.log_panel) == ["第二轮开始"]


def test_start_auto_tid_prepares_first_round_before_worker_logs(app, tmp_path, monkeypatch):
    window = MainWindow(run_log_manager=RunLogManager(tmp_path / "logs"))
    started = []
    monkeypatch.setattr(window, "_ensure_preview_for_auto_rng", lambda: True)
    monkeypatch.setattr(window, "_ensure_bridge_connected", lambda: True)
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
