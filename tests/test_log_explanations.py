"""User explanations exercised through real log widgets and script execution."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication

from auto_bdsp_rng.automation.auto_rng.models import AutoRngConfig, AutoRngPhase
from auto_bdsp_rng.automation.auto_rng.runner import AutoRngServices
from auto_bdsp_rng.automation.auto_rng.scripts import AUTO_ADVANCE_PARAMETER
from auto_bdsp_rng.automation.easycon import EasyConStatus
from auto_bdsp_rng.automation.easycon.native.device import DeviceConnectionError, DeviceNotConnectedError
from auto_bdsp_rng.automation.easycon.native_backend import NativeEasyConBackend
from auto_bdsp_rng.blink_detection.models import BlinkObservation
from auto_bdsp_rng.blink_detection.project_xs import (
    ProjectXsCaptureConfigError, ProjectXsIntegrationError, ProjectXsNoFrameError, ProjectXsSeedRecoveryError,
)
from auto_bdsp_rng.capture_broker import CaptureOpenError, FrameFormatError
from auto_bdsp_rng.log_events import (
    LogMessage,
    exception_details,
    operation_failure,
    script_failure,
    task_failure,
)
from auto_bdsp_rng.ui import main_window as main_window_module
from auto_bdsp_rng.ui.easycon_panel import BridgeScriptWorker, NativeScriptWorker
from auto_bdsp_rng.ui.live_log_panel import LiveLogPanel
from auto_bdsp_rng.ui.run_log_panel import RunLogBuffer, RunLogPanel
from tests.test_easycon_panel import easycon_panel_factory, easycon_panel
from tests.test_run_records_ui import app
from tests.test_start_readiness import window
from tests.ui_wait import wait_until


PRIVATE = "C:/private/Project_Xs/config.json; monotonic=23; backend=False"


def solver_error():
    return ProjectXsSeedRecoveryError(
        "Project_Xs could not validate the recovered seed",
        BlinkObservation.from_sequences([0, 1], [12, 24]), 0, AssertionError(PRIVATE), stage="recover",
    )


@pytest.mark.parametrize("source,error,summary,reason", [
    ("Seed 捕捉", solver_error(), "测种失败", "Seed 未通过验证"),
    ("Seed 捕捉", ProjectXsNoFrameError(PRIVATE), "测种失败", "未收到有效游戏画面"),
    ("Seed 捕捉", ProjectXsCaptureConfigError("眼睛 ROI 无效; " + PRIVATE), "测种配置无效", "识别范围与当前画面不匹配"),
    ("自动定点", ProjectXsIntegrationError("Project_Xs 校正未找到匹配状态; " + PRIVATE), "位置校正失败", "未找到匹配的推进位置"),
    ("伊机控", DeviceNotConnectedError(PRIVATE), "手柄按键发送失败", "串口不可用或连接已断开"),
    ("伊机控", DeviceConnectionError("access is denied; " + PRIVATE), "伊机控连接失败", "串口访问被拒绝"),
    ("视频源", CaptureOpenError(PRIVATE), "视频源连接失败", "无法打开所选视频设备"),
    ("视频源", FrameFormatError(PRIVATE), "视频读取失败", "视频画面格式不受支持"),
    ("OCR", FileNotFoundError(PRIVATE), "文字识别失败", "未找到所需文件"),
    ("应用", PermissionError(PRIVATE), "配置保存失败", "权限不足"),
    ("软件更新", TimeoutError(PRIVATE), "检查更新失败", "等待操作结果超时"),
    ("视频源", TimeoutError("获取游戏画面超时; " + PRIVATE), "视频读取失败", "读取游戏画面超时"),
])
def test_known_causes_are_visible_in_default_details_without_exposing_diagnostics(app, source, error, summary, reason):
    buffer = RunLogBuffer()
    full, live = RunLogPanel(buffer), LiveLogPanel(buffer)
    event = operation_failure(error, summary=summary)
    entry = buffer.publish(source, event, level="ERROR", round_id=3)
    wait_until(lambda: reason in live.text.toPlainText())
    full.table.setCurrentIndex(full.proxy_model.index(0, 3))
    full.details_button.click()
    details = full.details_text.toPlainText()
    assert reason in entry.user_message and reason in details
    assert details.index("具体原因：") < details.index("记录信息：")
    assert not full.raw_details_button.isChecked()
    for internal in ("private", "Project_Xs", "monotonic", "backend=False", type(error).__name__):
        assert internal not in details and internal not in live.text.toPlainText()
    full.copy_details_button.click()
    assert QApplication.clipboard().text() == details
    full.raw_details_button.click()
    assert PRIVATE in full.details_text.toPlainText() or "测种求解输入" in full.details_text.toPlainText()
    assert type(error).__name__ in full.details_text.toPlainText()


@pytest.mark.parametrize("source,raw,expected", [
    ("伊机控", "保存脚本失败: " + PRIVATE, "脚本保存失败"),
    ("伊机控", "串口已连接，但无法保存配置: " + PRIVATE, "已连接，但连接设置未保存"),
    ("伊机控", "伊机控连接已断开；" + PRIVATE, "伊机控连接已断开"),
    ("伊机控", "捕捉亮屏保活发送 A 失败，继续捕捉: " + PRIVATE, "继续捕捉"),
    ("伊机控", "系统级键盘捕获不可用，已回退窗口内捕获: " + PRIVATE, "已切换为窗口内键盘控制"),
    ("伊机控", "已有伊机控脚本正在运行", "请先停止当前脚本"),
    ("伊机控", "第 7 行参数 _闪帧 需要填写", "第 7 行缺少必填参数 _闪帧"),
    ("OCR", "性格自定义 ROI 在当前画面中无效，已回退到默认范围", "已使用备用范围"),
    ("自动 TID", "目标 Display TID 必须是 0-999999 的数字", "0–999999"),
    ("历史记录", "错过目标；目标 Adv 500；当前 Adv 580", "目标推进 500；当前推进 580"),
    ("历史记录", "个性匹配失败，已忽略个性条件；反查完成；候选 2 个", "已忽略该条件"),
    ("软件更新", "启动自动检查失败：" + PRIVATE, "自动检查更新未能启动"),
    ("QQ 通知", "QQ 通知未发送：" + PRIVATE, "当前游戏任务不受影响"),
])
def test_unmigrated_application_notices_have_operation_specific_copy(app, source, raw, expected):
    buffer = RunLogBuffer()
    entry = buffer.publish(source, raw, level="WARNING")
    assert expected in entry.user_message
    assert raw == entry.message
    assert "请查看完整日志" not in entry.user_message
    assert all(secret not in entry.user_details_text() for secret in ("private", "Project_Xs", "monotonic"))
    assert raw in entry.full_text()


def test_plain_seed_failure_remains_a_plain_failure_and_explains_missing_evidence(app):
    buffer = RunLogBuffer()
    entry = buffer.publish("自动定点", task_failure(RuntimeError(PRIVATE), task="自动定点", phase="捕获Seed"))
    assert entry.user_message == "测种失败，流程已停止"
    details = entry.user_details_text()
    assert "未提供可确认的具体原因" in details
    assert not any(word in details for word in ("超时", "画面", "框选", "求解", "private", "Project_Xs"))
    assert PRIVATE in entry.full_text()


def test_wrapped_causes_are_readable_in_order_and_coalescing_keeps_first_and_latest(app):
    buffer = RunLogBuffer()
    errors = [FileNotFoundError(PRIVATE), PermissionError(PRIVATE)]
    for inner in errors:
        try:
            raise inner
        except Exception as error:
            try:
                raise ProjectXsIntegrationError("Cannot read Project_Xs config: " + PRIVATE) from error
            except ProjectXsIntegrationError as outer:
                event = operation_failure(outer, summary="测种配置读取失败")
        event.coalesce_key = "config.retry"
        buffer.publish("Seed 捕捉", event, level="WARNING")
    entry = buffer.snapshot()[0]
    assert entry.repeat_count == 2
    details = entry.user_details_text()
    assert "首次问题：" in details and "最近问题：" in details
    assert "未找到所需文件" in details and "权限不足" in details
    assert details.index("权限不足") < details.rindex("测种配置文件无法读取")
    assert "private" not in details
    assert "FileNotFoundError" in entry.full_text() and "PermissionError" in entry.full_text()


@pytest.mark.parametrize("entrypoint", ["_native_run_finished", "finish_external_native_script", "_bridge_run_finished", "finish_external_bridge_script"])
@pytest.mark.parametrize("text,expected", [
    ("A 100\n$次数 =\nB 100\n", "赋值语句缺少表达式"),
    ("$x = 1\n$result = $x / 0\n", "计算时除数为 0"),
])
def test_actual_script_error_is_locatable_and_only_reported_once(easycon_panel, tmp_path, entrypoint, text, expected):
    panel = easycon_panel
    buffer = RunLogBuffer()
    panel._run_log_sink = lambda level, event: buffer.publish("伊机控", event, level)
    path = tmp_path / "测种.txt"
    path.write_text(text, encoding="utf-8")
    backend = NativeEasyConBackend()
    try:
        result = backend.run_script_text(text, str(path))
        assert result.status == EasyConStatus.FAILED and result.error is not None
        panel.begin_external_native_script(str(path))
        getattr(panel, entrypoint)(result)
        problems = [entry for entry in buffer.snapshot() if entry.level == "ERROR"]
        assert len(problems) == 1
        entry = problems[0]
        assert expected in entry.user_message
        assert "第 2 行" in entry.user_message and "第 2 行" in entry.user_details_text()
        assert "脚本：测种.txt" in entry.user_details_text()
        assert "赋值语句缺少表达式" in panel.script_error_label.text() or "除数不能为 0" in panel.script_error_label.text()
        assert panel._script_error_location == (str(path), 2)
        assert panel.locate_error_button.isVisibleTo(panel)
        assert str(tmp_path) not in entry.user_details_text()
        assert str(tmp_path) in entry.full_text()
    finally:
        backend.close()


def test_real_script_compile_failure_stops_the_real_auto_worker_before_seed_capture(window, monkeypatch, tmp_path):
    w = window
    paths = [tmp_path / name for name in ("测种.txt", "过帧.txt", "撞闪.txt")]
    paths[0].write_text("A 100\n$次数 =\n", encoding="utf-8")
    paths[1].write_text(f"{AUTO_ADVANCE_PARAMETER} = 0\nA 100\n", encoding="utf-8")
    paths[2].write_text("B 100\n", encoding="utf-8")
    backend, captures = NativeEasyConBackend(), []

    def run_script(text, name):
        w._call_on_ui_thread(lambda: w.easycon_tab.begin_external_native_script(str(paths[0])))
        result = backend.run_script_text(text, str(paths[0]))
        return w._finalize_auto_script_result(result, name)

    services = AutoRngServices(capture_seed=lambda: captures.append(True), run_script_text=run_script)
    config = AutoRngConfig(script_dir=tmp_path, seed_script_path=paths[0], advance_script_path=paths[1], hit_script_path=paths[2])
    monkeypatch.setattr(w, "_ensure_preview_for_auto_rng", lambda: True)
    monkeypatch.setattr(w.easycon_tab, "_native_is_connected", lambda: True)
    monkeypatch.setattr(w, "_build_auto_rng_services", lambda config: services)
    monkeypatch.setattr(w, "_notify_qq_task", lambda *args: None)
    try:
        w._start_auto_rng_after_warmup(config)
        runner = w.auto_rng_tab._runner_worker.runner
        run_id = w._active_auto_rng_run_id
        wait_until(lambda: w.auto_rng_tab._runner_thread is None)
        entries = [entry for entry in w._run_log_buffer.snapshot() if entry.run_id == run_id]
        failed = next(entry for entry in entries if entry.event_type == "task.failed")
        assert "测种脚本第 2 行语法错误：赋值语句缺少表达式，流程已停止" in failed.user_message
        assert "脚本：测种.txt" in failed.user_details_text()
        assert "ScriptCompileError" in failed.full_text()
        assert not captures and runner.progress.phase == AutoRngPhase.RUN_SEED_SCRIPT
        assert not any(entry.event_type == "seed.retry" for entry in entries)
        assert "请查看完整日志" not in w.live_log_panel.text.toPlainText()
        full = w.run_log_window.log_panel
        index = next(i for i, entry in enumerate(full.visible_entries()) if entry.seq == failed.seq)
        full.table.setCurrentIndex(full.proxy_model.index(index, 3))
        full.details_button.setChecked(True)
        assert "赋值语句缺少表达式" in full.details_text.toPlainText()
        assert not full.raw_details_button.isChecked()
    finally:
        backend.close()


@pytest.mark.parametrize("size", [(980, 640), (760, 500)])
def test_default_details_show_cause_location_and_advice_without_scrolling(window, tmp_path, size):
    w = window
    backend = NativeEasyConBackend()
    try:
        result = backend.run_script_text("A 100\n$次数 =\n", str(tmp_path / "测种.txt"))
        entry = w._run_log_buffer.publish("伊机控", script_failure(result.error), level="ERROR")
        w.run_log_window.show_logs()
        w.run_log_window.resize(*size)
        QApplication.processEvents()
        panel = w.run_log_window.log_panel
        index = next(i for i, item in enumerate(panel.visible_entries()) if item.seq == entry.seq)
        panel.table.setCurrentIndex(panel.proxy_model.index(index, 3))
        panel.details_button.setChecked(True)
        QApplication.processEvents()
        editor = panel.details_text
        assert not panel.raw_details_button.isChecked()
        assert editor.verticalScrollBar().value() == 0
        for text in ("具体原因：赋值语句缺少表达式", "错误位置：第 2 行", "修正后重新运行"):
            cursor = editor.document().find(text)
            assert not cursor.isNull()
            cursor.setPosition(cursor.selectionStart(), QTextCursor.MoveMode.MoveAnchor)
            rectangle = editor.cursorRect(cursor)
            assert rectangle.top() >= 0 and rectangle.bottom() <= editor.viewport().height()
    finally:
        backend.close()


@pytest.mark.parametrize("mode", ["native", "bridge"])
def test_script_worker_exception_keeps_typed_reason_in_default_details(easycon_panel, tmp_path, mode):
    panel = easycon_panel
    buffer = RunLogBuffer()
    panel._run_log_sink = lambda level, event: buffer.publish("伊机控", event, level)

    def fail(*args, **kwargs):
        raise DeviceNotConnectedError(PRIVATE)

    backend = SimpleNamespace(run_script_text=fail)
    if mode == "native":
        worker = NativeScriptWorker(backend, "A 100", "测种.txt", tmp_path)
        panel.native_run_worker = worker
        worker.failed.connect(panel._native_run_failed)
    else:
        worker = BridgeScriptWorker(backend, "A 100", "测种.txt")
        panel.bridge_run_worker = worker
        worker.failed.connect(panel._bridge_run_failed)
    worker.run()
    problems = [entry for entry in buffer.snapshot() if entry.level == "ERROR"]
    assert len(problems) == 1
    assert "串口不可用或连接已断开" in problems[0].user_details_text()
    assert "DeviceNotConnectedError" in problems[0].full_text()
    assert PRIVATE not in problems[0].user_details_text()


def test_auto_script_failure_signal_keeps_reason_and_original_diagnostic(window):
    error = DeviceNotConnectedError(PRIVATE)
    window._fail_auto_script(error)
    entry = next(entry for entry in window._run_log_buffer.snapshot() if entry.event_type == "script.failed")
    assert "串口不可用或连接已断开" in entry.user_details_text()
    assert "DeviceNotConnectedError" in entry.full_text()
    assert PRIVATE not in entry.user_details_text()


def test_calibration_worker_exception_keeps_reason_before_worker_reset(window, monkeypatch):
    def fail(*args, **kwargs):
        raise FileNotFoundError(PRIVATE)

    monkeypatch.setattr(main_window_module, "measure_keyword_interval", fail)
    monkeypatch.setattr(main_window_module.QMessageBox, "critical", lambda *args: None)
    worker = main_window_module.ShinyThresholdCalibrationWorker(lambda: None)
    window._shiny_calibration_worker = worker
    worker.failed.connect(window._shiny_threshold_calibration_failed)
    worker.run()
    entry = next(entry for entry in window._run_log_buffer.snapshot() if entry.summary == "闪光判定校准失败")
    assert "未找到所需文件" in entry.user_details_text()
    assert "FileNotFoundError" in entry.full_text()
    assert PRIVATE not in entry.user_details_text()
    assert window._shiny_calibration_worker is None


def test_protection_stop_context_keeps_punctuation_after_cause_details(app):
    failure = RuntimeError("连续 5 次 seed 捕获失败")
    failure.__cause__ = solver_error()
    entry = RunLogBuffer().publish("自动定点", task_failure(failure, task="自动定点", phase="捕获Seed"))
    details = entry.user_details_text()
    assert "已连续 5 次测种失败，触发保护停止。" in details
    assert details.index("具体原因：") < details.index("已连续 5 次测种失败") < details.index("记录信息：")
    assert details.count("请重新测种") == 1


@pytest.mark.parametrize("error", [
    TimeoutError("Seed solver exceeded frame budget"),
    TimeoutError("frame counter update deadline exceeded"),
    ProjectXsCaptureConfigError("Cannot read eye template image: C:/private/ROI/template.png"),
])
def test_diagnostic_keywords_and_paths_do_not_create_unconfirmed_capture_causes(app, error):
    entry = RunLogBuffer().publish("Seed 捕捉", operation_failure(error, summary="测种失败"), level="ERROR")
    public = entry.user_message + entry.user_details_text()
    assert "读取游戏画面超时" not in public
    assert "识别范围与当前画面不匹配" not in public
    assert "private" not in public and "ROI" not in public
    assert str(error) in entry.full_text()
