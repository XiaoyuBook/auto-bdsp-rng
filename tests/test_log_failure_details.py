from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from auto_bdsp_rng.automation.auto_rng.models import AutoRngConfig, AutoRngPhase, AutoRngProgress
from auto_bdsp_rng.automation.auto_rng.runner import AutoRngRunner, AutoRngServices
from auto_bdsp_rng.log_events import LogMessage, exception_details
from auto_bdsp_rng.ui.auto_rng_panel import AutoRngPanel, AutoRngWorker
from auto_bdsp_rng.ui.live_log_panel import LiveLogPanel
from auto_bdsp_rng.ui.run_log_panel import RunLogBuffer, RunLogPanel
from tests.test_run_records_ui import app


def settle(app):
    for _ in range(5):
        app.processEvents()


def wrapped_capture_error():
    try:
        raise TimeoutError('获取游戏画面超时；配置 C:\\private\\capture.json')
    except TimeoutError as cause:
        raise RuntimeError('Project_Xs 测种未完成') from cause


def plain_capture_error():
    raise RuntimeError('Project_Xs 测种未完成')


@pytest.mark.parametrize('capture', [plain_capture_error, wrapped_capture_error], ids=['plain-failure', 'confirmed-timeout'])
def test_retry_details_show_business_text_and_require_an_explicit_action_for_raw_causes(app, tmp_path, capture):
    buffer = RunLogBuffer()
    full, live = RunLogPanel(buffer), LiveLogPanel(buffer)
    full.resize(1080, 620)
    full.show()
    buffer.publish('自动定点', LogMessage('开始自动流程', summary='正在开始测种'), round_id=1)
    full.table.setCurrentIndex(full.proxy_model.index(0, 3))
    full.details_button.click()
    assert '正在开始测种' in full.details_text.toPlainText()
    settings = QSettings(str(tmp_path / 'settings.ini'), QSettings.Format.IniFormat)
    panel = AutoRngPanel(settings=settings, run_log_sink=lambda level, message: buffer.publish('自动定点', message, level, round_id=1))
    seed_script = tmp_path / '测种.txt'
    seed_script.write_text('A 100\n', encoding='utf-8')
    runner = AutoRngRunner(
        AutoRngConfig(script_dir=tmp_path, seed_script_path=seed_script, start_phase=AutoRngPhase.CAPTURE_SEED),
        services=AutoRngServices(capture_seed=capture, run_script_text=lambda *_: None),
    )
    worker = AutoRngWorker(runner)
    worker.progressChanged.connect(panel.apply_progress)
    worker.failed.connect(panel._runner_failed)
    worker.run()
    settle(app)
    # Automatic scrolling must not silently change a deliberately selected row.
    assert '正在开始测种' in full.details_text.toPlainText()
    row, failure = next((row, entry) for row, entry in enumerate(full.visible_entries()) if entry.event_type == 'seed.retry')
    index = full.proxy_model.index(row, 3)
    full.table.scrollTo(index)
    settle(app)
    rect = full.table.visualRect(index)
    assert full.table.viewport().rect().contains(rect.center())
    QTest.mouseClick(full.table.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    settle(app)
    details = full.details_text.toPlainText()
    assert '测种失败（连续 1/5），将重新运行测种脚本' in details
    for internal in ('Project_Xs', 'RuntimeError', 'TimeoutError', 'private', '异常原因链', '事件：'):
        assert internal not in details
    assert '画面' not in details and '眼睛框选' not in details
    if capture is plain_capture_error:
        assert '超时' not in details
        assert all('超时' not in entry.user_message for entry in buffer.snapshot())
    else:
        assert '等待操作结果超时' in details
    assert failure.timestamp.strftime('%H:%M:%S') in full.details_hint.text()
    assert '警告' in full.details_hint.text()
    full.copy_details_button.click()
    assert QApplication.clipboard().text() == details
    assert 'Project_Xs' not in live.text.toPlainText()

    QTest.mouseClick(full.raw_details_button, Qt.MouseButton.LeftButton)
    details = full.details_text.toPlainText()
    assert 'Project_Xs 测种未完成' in details
    if capture is wrapped_capture_error:
        assert 'TimeoutError: 获取游戏画面超时' in details
        assert 'C:\\private\\capture.json' in details
        assert details.index('TimeoutError: 获取游戏画面超时') < details.index('问题原文：')
        assert details.index('TimeoutError: 获取游戏画面超时') < details.index('RuntimeError: Project_Xs 测种未完成')
    full.copy_details_button.click()
    assert QApplication.clipboard().text() == details
    assert 'private' not in live.text.toPlainText()
    full.copy_button.click()
    assert 'Project_Xs' not in QApplication.clipboard().text()
    full.raw_details_button.click()
    assert 'Project_Xs' not in full.details_text.toPlainText()

    # Returning from diagnostics must also restore safe details in both views.
    for mode in ('problems', 'user'):
        full.view_combo.setCurrentIndex(full.view_combo.findData('all'))
        full.table.setCurrentIndex(full.proxy_model.index(0, 3))
        assert full.raw_details_button.isChecked()
        full.view_combo.setCurrentIndex(full.view_combo.findData(mode))
        full.table.setCurrentIndex(full.proxy_model.index(0, 3))
        assert not full.raw_details_button.isChecked()
        assert 'Project_Xs' not in full.details_text.toPlainText()


@pytest.mark.parametrize('task', ['rng', 'tid'])
def test_worker_keeps_nested_cause_without_changing_its_string_failure_signal(app, tmp_path, task):
    from auto_bdsp_rng.automation.auto_tid_rng import AutoTidRngPhase, AutoTidRngProgress
    from auto_bdsp_rng.ui.auto_tid_rng_panel import AutoTidRngPanel, AutoTidRngWorker

    settings = QSettings(str(tmp_path / 'settings.ini'), QSettings.Format.IniFormat)
    buffer, failures = RunLogBuffer(), []
    if task == 'rng':
        progress = AutoRngProgress(phase=AutoRngPhase.CAPTURE_SEED)
        panel_type, worker_type = AutoRngPanel, AutoRngWorker
    else:
        progress = AutoTidRngProgress(phase=AutoTidRngPhase.CAPTURE_TIDSID)
        panel_type, worker_type = AutoTidRngPanel, AutoTidRngWorker
    class FailingRunner:
        def __init__(self):
            self.progress = progress
        def run(self):
            self.progress_callback(self.progress)
            wrapped_capture_error()
    panel = panel_type(settings=settings, run_log_sink=lambda level, message: buffer.publish('自动任务', message, level))
    worker = worker_type(FailingRunner())
    worker.progressChanged.connect(panel.apply_progress)
    worker.failed.connect(panel._runner_failed)
    worker.failed.connect(failures.append)
    worker.run()
    assert failures == ['Project_Xs 测种未完成']
    failure = next(entry for entry in buffer.snapshot() if entry.level == 'ERROR')
    assert '获取游戏画面超时' in failure.full_text()
    assert 'TimeoutError' in failure.details
    assert '等待操作结果超时' in failure.user_context
    assert '画面' not in failure.user_context
    assert 'private' not in failure.user_message


def test_coalesced_selected_failure_keeps_both_first_and_latest_diagnostic_causes(app):
    buffer = RunLogBuffer()
    panel = RunLogPanel(buffer)
    for attempt in (1, 2):
        error = RuntimeError(f'第 {attempt} 次具体原因')
        buffer.publish('自动定点', LogMessage(
            f'位置校正失败 {attempt}', summary='正在重试位置校正',
            details=exception_details(error), level='WARNING', coalesce_key='retry',
        ))
        if attempt == 1:
            panel.table.setCurrentIndex(panel.proxy_model.index(0, 3))
            panel.details_button.click()
            panel.raw_details_button.click()
    details = panel.details_text.toPlainText()
    assert '第 1 次具体原因' in details and '第 2 次具体原因' in details
    assert '首次诊断详情' in details and '最近记录' in details


def test_tid_script_failure_keeps_its_wrapped_exception_in_details(app, tmp_path):
    from auto_bdsp_rng.automation.auto_tid_rng import AutoTidRngConfig, AutoTidRngPhase, AutoTidRngRunner, AutoTidRngServices

    script = tmp_path / '测种.txt'
    script.write_text('A 100\n', encoding='utf-8')
    buffer = RunLogBuffer()
    runner = AutoTidRngRunner(
        AutoTidRngConfig(script_dir=tmp_path, seed_script_path=script),
        services=AutoTidRngServices(run_script_text=lambda *_: wrapped_capture_error()),
        progress_callback=lambda progress: buffer.publish('自动 TID', progress.log_message) if progress.log_message else None,
    )
    assert runner.run(max_steps=1).phase == AutoTidRngPhase.FAILED
    failure = next(entry for entry in buffer.snapshot() if entry.level == 'ERROR')
    assert 'Project_Xs 测种未完成' in failure.message
    assert 'TimeoutError: 获取游戏画面超时' in failure.full_text()
    assert 'private' not in failure.user_message


def test_original_message_and_inner_cause_are_both_saved_without_a_second_ui_alert(app, tmp_path):
    from auto_bdsp_rng.run_log import RunLogManager
    from auto_bdsp_rng.ui import MainWindow

    buffer = RunLogBuffer()
    manager = RunLogManager(tmp_path)
    path = manager.enable()
    try:
        wrapped_capture_error()
    except RuntimeError as error:
        event = LogMessage(str(error), summary='测种失败，正在重试', details=exception_details(error), level='WARNING')
    target = SimpleNamespace(_qq_task_details={}, _run_log_context=lambda source: (None, None),
                             _run_log_buffer=buffer, _run_log_manager=manager)
    MainWindow._write_run_log(target, '自动定点', event)
    manager.close()
    assert len(buffer.snapshot()) == 1
    saved = path.read_text(encoding='utf-8')
    assert 'Project_Xs 测种未完成' in saved and 'TimeoutError: 获取游戏画面超时' in saved
