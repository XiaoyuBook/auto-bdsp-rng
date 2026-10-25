from __future__ import annotations

from threading import Thread
from types import SimpleNamespace

import pytest

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from auto_bdsp_rng.log_events import LogMessage, log_summary, script_output_message
from auto_bdsp_rng.run_log import RunLogManager
from auto_bdsp_rng.ui.live_log_panel import LiveLogPanel
from auto_bdsp_rng.ui.run_log_panel import RunLogBuffer, RunLogPanel
from auto_bdsp_rng.ui.run_log_window import RunLogWindow
from tests.test_run_records_ui import app


def settle(app):
    for _ in range(4):
        app.processEvents()


def test_user_views_require_authored_summaries_and_keep_failures_safe(app):
    buffer = RunLogBuffer()
    panel, live = RunLogPanel(buffer), LiveLogPanel(buffer)
    buffer.publish("自动 TID", LogMessage("开始=True；线程=False", summary="按钮状态", diagnostic=True, level="DEBUG"))
    buffer.publish("自动 TID", LogMessage("Seed=123；monotonic=9", summary="测种完成", event_type="seed.captured"), round_id=3)
    buffer.publish("外部脚本", "未迁移的普通消息")
    buffer.publish("外部脚本", LogMessage("TimeoutError: 原始原因", diagnostic=True), level="ERROR")
    settle(app)

    assert len(buffer.snapshot()) == 4
    assert len(panel.visible_entries()) == 2
    assert panel.proxy_model.index(0, 1).data() == "信息"
    assert panel.proxy_model.index(0, 2).data() == "自动 TID · 第 3 轮"
    assert panel.proxy_model.index(0, 3).data() == "测种完成"
    text = live.text.toPlainText()
    assert "测种完成" in text and "第 3 轮" in text
    assert "未迁移的普通消息" not in text and "TimeoutError: 原始原因" not in text
    assert "操作未能完成，请查看完整日志中的诊断详情" in text
    assert "monotonic" not in text and "开始=True" not in text
    panel.view_combo.setCurrentIndex(panel.view_combo.findData("all"))
    assert len(panel.visible_entries()) == 4
    panel.view_combo.setCurrentIndex(panel.view_combo.findData("problems"))
    assert [entry.level for entry in panel.visible_entries()] == ["ERROR"]
    panel.clear_filters()
    assert panel.view_combo.currentData() == "user" and len(panel.visible_entries()) == 2


def test_details_search_copy_and_export_preserve_chinese_and_raw_parameters(app, tmp_path, monkeypatch):
    buffer = RunLogBuffer()
    panel = RunLogPanel(buffer)
    original = 'TID=000123；Seed=ABC；<中文> & "原文"\nmonotonic=98.765'
    buffer.publish("自动 TID", LogMessage(original, summary="已找到目标 TID：000123", details="延迟：100 次推进"), run_id="run-a", round_id=2)
    panel.search_edit.setText("98.765")
    assert len(panel.visible_entries()) == 1
    panel.table.setCurrentIndex(panel.proxy_model.index(0, 3))
    panel.details_button.click()
    assert "已找到目标 TID：000123" in panel.details_text.toPlainText()
    assert original not in panel.details_text.toPlainText()
    assert "run-a" not in panel.details_text.toPlainText()
    panel.copy_details_button.click()
    assert original not in QApplication.clipboard().text()
    panel.raw_details_button.click()
    assert original in panel.details_text.toPlainText()
    assert "延迟：100 次推进" in panel.details_text.toPlainText()
    assert "run-a" in panel.details_text.toPlainText()
    panel.copy_details_button.click()
    assert original in QApplication.clipboard().text()
    panel.copy_button.click()
    assert "已找到目标 TID：000123" in QApplication.clipboard().text()
    assert "monotonic" not in QApplication.clipboard().text()

    panel.view_combo.setCurrentIndex(panel.view_combo.findData("all"))
    output = tmp_path / "诊断日志.txt"
    monkeypatch.setattr("auto_bdsp_rng.ui.run_log_panel.QFileDialog.getSaveFileName", lambda *args: (str(output), ""))
    panel.export_button.click()
    assert original in output.read_text(encoding="utf-8")
    panel.clear_display()
    assert not panel.details_text.toPlainText()
    assert not panel.copy_details_button.isEnabled()
    assert not panel.raw_details_button.isEnabled()
    assert buffer.snapshot()[0].message == original


def test_coalescing_keeps_first_and_latest_failures_and_all_disk_records(app, tmp_path):
    buffer = RunLogBuffer()
    panel, live = RunLogPanel(buffer), LiveLogPanel(buffer)
    manager = RunLogManager(tmp_path)
    path = manager.enable()

    def publish(round_id, attempt):
        message = LogMessage(
            f"校正失败 {attempt}：设备原因-{attempt}", summary=f"正在第 {attempt + 1}/4 次尝试",
            event_type="position.retry", level="WARNING", coalesce_key="reidentify",
        )
        entry = buffer.publish("自动定点", message, run_id="run-a", round_id=round_id)
        manager.write("自动定点", message, level="WARNING")
        return entry

    worker = Thread(target=lambda: [publish(1, attempt) for attempt in range(1, 4)])
    worker.start()
    worker.join()
    settle(app)
    entry = buffer.snapshot()[0]
    assert entry.repeat_count == 3
    assert entry.first_message.endswith("设备原因-1") and entry.message.endswith("设备原因-3")
    assert "累计 3 次" in panel.proxy_model.index(0, 3).data()
    assert "累计 3 次" in live.text.toPlainText()
    assert "设备原因-1" in entry.full_text() and "设备原因-3" in entry.full_text()
    publish(2, 1)
    assert len(buffer.snapshot()) == 2
    assert buffer.snapshot()[1].repeat_count == 1
    manager.close()
    disk = path.read_text(encoding="utf-8")
    for attempt in range(1, 4):
        assert f"设备原因-{attempt}" in disk


def test_late_add_cannot_overwrite_a_newer_coalesced_update(app):
    buffer = RunLogBuffer()
    panel = RunLogPanel(buffer)
    buffer.blockSignals(True)
    first = buffer.publish("计时", LogMessage("first", summary="首次摘要", coalesce_key="sample"))
    latest = buffer.publish("计时", LogMessage("latest", summary="最近摘要", coalesce_key="sample"))
    panel.update_entry(latest)
    panel.add_entry(first)
    assert panel.visible_entries()[0].message == "latest"
    assert panel.visible_entries()[0].repeat_count == 2


def test_a_new_retry_after_reading_reopens_one_unread_problem(app):
    buffer = RunLogBuffer()
    logs = RunLogWindow(buffer)
    message = LogMessage("重试失败", level="WARNING", coalesce_key="retry")
    buffer.publish("自动定点", message)
    settle(app)
    assert logs.unread_problem_count == 1
    logs.show_logs()
    settle(app)
    assert logs.unread_problem_count == 0
    logs.close()
    buffer.publish("自动定点", message)
    buffer.publish("自动定点", message)
    settle(app)
    assert logs.unread_problem_count == 1
    assert logs.log_panel.visible_entries()[0].repeat_count == 3
    logs.show_logs()
    assert logs.unread_problem_count == 0


def test_plain_script_output_stays_in_diagnostics_with_literal_raw_details(app):
    buffer = RunLogBuffer()
    panel = RunLogPanel(buffer)
    raw = '<输出> & "中文"\n' * 1000
    buffer.publish("伊机控", script_output_message(raw))
    assert not panel.visible_entries()
    panel.view_combo.setCurrentIndex(panel.view_combo.findData("all"))
    assert panel.proxy_model.index(0, 3).data() == "脚本输出已更新（1000 行）"
    panel.table.setCurrentIndex(panel.proxy_model.index(0, 0))
    assert raw in panel.details_text.toPlainText()
    assert len(panel.visible_entries()) == 1
    assert buffer.snapshot()[0].message == raw


def test_live_reader_preserves_literal_text_and_retains_complete_messages(app):
    buffer = RunLogBuffer()
    live = LiveLogPanel(buffer)
    live.resize(410, 320)
    live.show()

    def publish():
        for index in range(600):
            summary = f'记录 {index}：<b>中文原文</b> & "引号"'
            buffer.publish("输入检查", LogMessage(f"内部原文 {index}", summary=summary, context=f"本条结果 {index}"))

    worker = Thread(target=publish)
    worker.start()
    worker.join()
    settle(app)
    text = live.text.toPlainText()
    assert '<b>中文原文</b> & "引号"' in text
    assert "记录 299：" not in text and "记录 300：" in text and "记录 599：" in text
    first = live.text.document().begin()
    assert "输入检查" in first.text()
    assert "记录 300：" in first.next().text()
    assert text.count("记录 ") == live.DISPLAY_LIMIT
    assert len(buffer.snapshot()) == 600
    live.follow_check.setChecked(False)
    live.text.verticalScrollBar().setValue(0)
    buffer.publish("输入检查", LogMessage("最后一条", summary="最后一条"))
    settle(app)
    assert live.text.verticalScrollBar().value() == 0


def test_selecting_debug_level_opens_diagnostics(app):
    buffer = RunLogBuffer()
    panel = RunLogPanel(buffer)
    buffer.publish("自动 TID", LogMessage("诊断原文", diagnostic=True, level="DEBUG"))
    assert not panel.visible_entries()
    panel.level_combo.setCurrentIndex(panel.level_combo.findData("DEBUG"))
    assert panel.view_combo.currentData() == "all"
    assert [entry.message for entry in panel.visible_entries()] == ["诊断原文"]


def test_diagnostic_metadata_cannot_downgrade_a_failure(app):
    buffer = RunLogBuffer()
    panel = RunLogPanel(buffer)
    buffer.publish("自动 TID", LogMessage("原始错误", diagnostic=True, level="DEBUG"), level="ERROR")
    assert panel.visible_entries()[0].level == "ERROR"
    assert panel.proxy_model.index(0, 1).data() == "错误"


@pytest.mark.parametrize("level", ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
@pytest.mark.parametrize("kind", ["plain", "unclassified", "diagnostic", "authored"])
def test_raw_payload_never_bypasses_the_user_presentation_boundary(app, level, kind):
    raw = 'Traceback: C:\\private\\config.json\nSeed=123; monotonic=9; enabled=True; token=internal-test'
    summary = "测种完成，正在搜索目标"
    message = raw if kind == "plain" else LogMessage(
        raw, summary=summary if kind in {"authored", "diagnostic"} else "",
        diagnostic=kind == "diagnostic", details="内部参数详情",
    )
    buffer = RunLogBuffer()
    panel, live = RunLogPanel(buffer), LiveLogPanel(buffer)
    entry = buffer.publish("测试模块", message, level=level)
    settle(app)
    # Check rendering itself as well as normal signal delivery and filtering.
    live.text.set_entries([entry])
    live.source_combo.setCurrentIndex(0)
    live.set_collapsed(True)
    live.set_collapsed(False)
    text = live.text.toPlainText()
    for internal in ("Traceback", "private", "Seed=", "monotonic", "enabled", "token", "内部参数详情"):
        assert internal not in text
    expected_visible = level in {"WARNING", "ERROR", "CRITICAL"} or (kind == "authored" and level != "DEBUG")
    assert entry.user_visible is expected_visible
    assert bool(panel.visible_entries()) is expected_visible
    if expected_visible:
        expected = summary if kind == "authored" else log_summary(message, level)
        assert expected in text
        assert panel.proxy_model.index(0, 3).data() == expected
        assert "Traceback" not in panel.proxy_model.index(0, 3).data(int(Qt.ItemDataRole.ToolTipRole))
        panel.copy_filtered()
        assert "Traceback" not in QApplication.clipboard().text()
        panel.view_combo.setCurrentIndex(panel.view_combo.findData("problems"))
        assert "Traceback" not in panel._filtered_text()
    else:
        assert text == ""
        assert live.source_combo.findData("测试模块") < 0
    panel.view_combo.setCurrentIndex(panel.view_combo.findData("all"))
    assert panel.visible_entries()[0].message == raw
    assert raw in panel._filtered_text()


@pytest.mark.parametrize("task", ["rng", "tid"])
def test_runner_failed_progress_keeps_exception_in_diagnostics_only(app, tmp_path, task):
    from auto_bdsp_rng.automation.auto_rng.models import AutoRngConfig, AutoRngPhase
    from auto_bdsp_rng.automation.auto_rng.runner import AutoRngRunner
    from auto_bdsp_rng.automation.auto_tid_rng import (
        AutoTidRngConfig, AutoTidRngPhase, AutoTidRngRunner,
    )

    raw = "RuntimeError: C:\\private\\seed.json; monotonic=999; internal-state=False"
    buffer = RunLogBuffer()
    live = LiveLogPanel(buffer)

    def receive(progress):
        if progress.log_message:
            buffer.publish("自动任务", progress.log_message)

    if task == "rng":
        runner = AutoRngRunner(
            AutoRngConfig(script_dir=tmp_path), progress_callback=receive,
        )
        failed = AutoRngPhase.FAILED
    else:
        runner = AutoTidRngRunner(
            AutoTidRngConfig(script_dir=tmp_path), progress_callback=receive,
        )
        failed = AutoTidRngPhase.FAILED
    runner._set_progress(failed, raw)
    settle(app)
    assert any(raw in entry.full_text() for entry in buffer.snapshot())
    assert "流程已停止" in live.text.toPlainText()
    for internal in ("RuntimeError", "private", "monotonic", "internal-state"):
        assert internal not in live.text.toPlainText()


@pytest.mark.parametrize("has_target", [False, True])
def test_tid_runner_and_main_sink_keep_user_summary_and_original_diagnostics(app, tmp_path, has_target):
    from auto_bdsp_rng.automation.auto_tid_rng import (
        AutoTidRngConfig, AutoTidRngPhase, AutoTidRngRunner, AutoTidRngServices, AutoTidSeedResult,
    )
    from auto_bdsp_rng.gen8_id import IDState8
    from auto_bdsp_rng.rng_core import SeedPair64
    from auto_bdsp_rng.ui import MainWindow

    buffer = RunLogBuffer()
    panel = RunLogPanel(buffer)
    manager = RunLogManager(tmp_path / "logs")
    path = manager.enable()
    context = SimpleNamespace(
        _qq_task_details={}, _run_log_context=lambda source: ("run-tid", 1),
        _run_log_buffer=buffer, _run_log_manager=manager,
    )
    script = tmp_path / "取名.txt"
    script.write_text("A 100\n", encoding="utf-8")
    clock = [100.0]
    states = (IDState8(advances=6, tid=123, sid=42, tsv=0, display_tid=123),) if has_target else ()

    def receive(progress):
        if progress.log_message:
            MainWindow._write_run_log(context, "自动 TID", progress.log_message)

    runner = AutoTidRngRunner(
        AutoTidRngConfig(
            script_dir=tmp_path, name_script_path=script, start_phase=AutoTidRngPhase.CAPTURE_TIDSID,
            target_display_tids=(123,), delay=1,
        ),
        services=AutoTidRngServices(
            capture_seed=lambda: AutoTidSeedResult(SeedPair64(1, 2), measured_at=100.0),
            search_id_states=lambda *_: states, run_script_text=lambda *_: None,
            monotonic=lambda: clock[0], sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
        ),
        progress_callback=receive,
    )
    result = runner.run(max_steps=100 if has_target else 2)
    summaries = [entry.display_message for entry in panel.visible_entries()]
    assert "测种完成，正在搜索目标 TID" in summaries
    seed_event = next(entry for entry in buffer.snapshot() if entry.event_type == "seed.captured")
    assert "monotonic" in seed_event.full_text()
    assert "monotonic" not in seed_event.display_message
    if has_target:
        assert result.phase == AutoTidRngPhase.COMPLETED
        assert "已找到目标 TID：000123，正在等待取名时机" in summaries
        assert summaries[-1] == "取名脚本执行完成，请确认游戏内 TID"
    else:
        assert "本轮搜索范围内未找到目标 TID，将重新测种" in summaries
        assert all(entry.level == "INFO" for entry in panel.visible_entries())
        assert any(entry.event_type == "search.diagnostics" for entry in buffer.snapshot())
    manager.close()
    assert "monotonic" in path.read_text(encoding="utf-8")
