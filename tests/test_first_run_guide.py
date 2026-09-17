from dataclasses import replace
import threading

import pytest
from PySide6.QtCore import QPoint, QTimer, Qt
from PySide6.QtTest import QTest

from auto_bdsp_rng import app_settings
from auto_bdsp_rng.automation.auto_rng import AutoRngPhase, AutoRngProgress
from tests.test_guide_flow import guided, assert_body_text_is_readable
from tests.test_ui import app, isolated_ui_qsettings  # noqa: F401


def wait_for(predicate):
    for _ in range(300):
        if predicate():
            return
        QTest.qWait(10)
    assert predicate()


class HeldRun:
    def __init__(self, config, phase=AutoRngPhase.COMPLETED, fail=False):
        self.config = config
        self.phase, self.fail = phase, fail
        self.release = threading.Event()

    def run(self):
        self.progress_callback(AutoRngProgress(phase=AutoRngPhase.REVERSE_LOOKUP))
        assert self.release.wait(10)
        if self.fail:
            raise RuntimeError("测试异常")
        progress = AutoRngProgress(phase=self.phase)
        self.progress_callback(progress)
        return progress

    def stop(self, **_):
        self.release.set()


def begin_run(guided, *, phase=AutoRngPhase.COMPLETED, fail=False, guide=True):
    w, p, c = guided
    w.tabs.setCurrentWidget(p)
    p._activate_delay_profile(481)
    p.mode_combo.setCurrentIndex(p.mode_combo.findData("single"))
    if guide:
        c._go("first_auto_run", "mode")
    else:
        c.pause()
    config = replace(p.build_config(), target_species=481)
    runner = HeldRun(config, phase, fail)
    p.run_with_runner(runner)
    assert not c.active and not c.overlay.isVisible()
    return runner


def finish_run(guided, runner, samples=(120,)):
    _, p, c = guided
    try:
        if samples:
            p.record_delay_sample(samples, species_id=481)
        assert c.first_run.result_dialog is None
    finally:
        runner.release.set()
    wait_for(lambda: p._runner_thread is None)
    QTest.qWait(30)
    return c.first_run.result_dialog


def test_saved_scripts_lead_to_single_mode_then_real_start(guided, monkeypatch):
    w, p, c = guided
    p.mode_combo.setCurrentIndex(p.mode_combo.findData("infinite"))
    w.tabs.setCurrentWidget(p)
    c._go("auto_script_config", "save:select")
    p.save_scripts_button.click()
    c.next()
    assert (c.step, c.detail) == ("first_auto_run", "mode")
    assert c.overlay.focus_target is p.mode_combo
    assert not c.overlay.tip.next_button.isEnabled()
    p.mode_combo.setCurrentIndex(p.mode_combo.findData("single"))
    assert c.detail == "start"
    assert c.overlay.focus_target is p.start_button
    assert c.overlay.title.text() == "开始你的第一次乱数吧！"
    assert_body_text_is_readable(c.overlay.tip)
    c.pause()
    w.tabs.setCurrentWidget(w.easycon_tab)
    c.begin_or_resume()
    assert c.overlay.waiting_for_page
    bar = w.tabs.tabBar()
    QTest.mouseClick(bar, Qt.MouseButton.LeftButton, pos=bar.tabRect(w.tabs.indexOf(p)).center())
    assert not c.overlay.waiting_for_page
    configs = []
    p.startRequested.disconnect(w._start_auto_rng)
    p.startRequested.connect(configs.append)
    monkeypatch.setattr("auto_bdsp_rng.ui.auto_rng_panel.validate_auto_scripts", lambda *_args, **_kwargs: None)
    # Avoid the menu-arrow half of the split start button.
    QTest.mouseClick(p.start_button, Qt.MouseButton.LeftButton, pos=QPoint(12, p.start_button.height() // 2))
    assert len(configs) == 1 and configs[0].loop_mode == "single"
    assert configs[0].start_phase == AutoRngPhase.RUN_SEED_SCRIPT


def test_single_run_result_recent_sample_and_complete(guided):
    w, p, c = guided
    p._activate_delay_profile(481)
    p.record_delay_sample((80,), species_id=481)
    runner = begin_run(guided)
    assert c.first_run.watching
    dialog = finish_run(guided, runner)
    assert dialog is not None and dialog.value.text() == "120 帧"
    assert "草苗龟" not in dialog.copy.text()  # name follows the frozen species, not the edited form
    assert c.detail == "result" and app_settings.get_guide_progress()
    # New samples added after the run must not move the teaching to a wrong row.
    p.record_delay_sample((130,), species_id=481)
    errors = []

    def inspect():
        try:
            overlay = c.first_run.samples_overlay
            assert overlay is not None and overlay.isVisible() and overlay.tip.isVisible()
            assert c.first_run.sample_anchor.row == 1
            assert "120 帧" in overlay.copy.text()
            assert_body_text_is_readable(overlay.tip)
            assert not overlay.hole.intersects(overlay.tip.geometry())
            old_baseline = p.delay_strategy_dialog.baseline_delay.value()
            QTest.keyClick(p.delay_strategy_dialog.baseline_delay, Qt.Key.Key_Up)
            assert p.delay_strategy_dialog.baseline_delay.value() == old_baseline
            QTest.keyClick(overlay.tip.next_button, Qt.Key.Key_Tab)
            assert overlay.tip.next_button.hasFocus() or overlay.close_button.hasFocus()
            overlay.tip.next_button.click()
        except BaseException as exc:
            errors.append(exc)
            p.delay_strategy_dialog.reject()

    QTimer.singleShot(200, inspect)
    dialog.primary.click()
    if errors:
        raise errors[0]
    assert app_settings.get_guide_progress() is None
    assert app_settings.load_settings()["guide_progress"]["status"] == "completed"
    assert c.button.text() == "开始引导"
    assert p.delay_strategy_config().baseline_delay == 100


@pytest.mark.parametrize("phase,samples,fail,title", [
    (AutoRngPhase.COMPLETED, (), False, "尚未获得"),
    (AutoRngPhase.COMPLETED, (120, 122), False, "不能唯一确定"),
    (AutoRngPhase.IDLE, (120,), False, "已停止"),
    (AutoRngPhase.FAILED, (120,), False, "未完成"),
    (AutoRngPhase.COMPLETED, (120,), True, "未完成"),
])
def test_no_false_success_and_retry(guided, phase, samples, fail, title):
    _, p, c = guided
    p._activate_delay_profile(481)
    p.record_delay_sample((120,), species_id=481)  # old success must not qualify
    runner = begin_run(guided, phase=phase, fail=fail)
    dialog = finish_run(guided, runner, samples)
    assert title in dialog.title.text()
    assert not c.first_run.result[0] and c.detail == "retry"
    dialog.primary.click()
    assert c.active and c.detail == "start"
    assert app_settings.get_guide_progress()["status"] == "in_progress"


def test_normal_run_does_not_show_teaching_card(guided):
    runner = begin_run(guided, guide=False)
    assert finish_run(guided, runner) is None


def test_stop_request_cannot_be_reported_as_success(guided):
    _, p, c = guided
    runner = begin_run(guided)
    p.record_delay_sample((120,), species_id=481)
    p.request_stop()
    dialog = finish_run(guided, runner, ())
    assert "已停止" in dialog.title.text()
    assert not c.first_run.result[0]


def test_preparation_cancel_restores_start_guide(guided):
    _, p, c = guided
    p.mode_combo.setCurrentIndex(p.mode_combo.findData("single"))
    c._go("first_auto_run", "mode")
    p.set_preparing(True)
    c.first_run._requested(p.build_config())
    assert c.first_run.preparing and not c.active
    p.set_preparing(False)
    wait_for(lambda: c.active)
    assert not c.first_run.preparing and c.detail == "start"
    assert c.first_run.result_dialog is None


def test_warmup_then_run_keeps_first_run_context(guided):
    _, p, c = guided
    p._activate_delay_profile(481)
    c._go("first_auto_run", "mode")
    config = replace(p.build_config(), target_species=481, loop_mode="single")
    p.set_preparing(True)
    c.first_run._requested(config)
    assert not c.active
    runner = HeldRun(config)
    p.run_with_runner(runner)
    dialog = finish_run(guided, runner)
    assert dialog.value.text() == "120 帧"


def test_dismiss_resume_logs_and_restart_do_not_reuse_stale_result(guided):
    w, p, c = guided
    runner = begin_run(guided)
    dialog = finish_run(guided, runner)
    dialog.reject()
    assert c.first_run.result_dialog is None
    c.begin_or_resume()
    assert c.first_run.result_dialog is not None
    c.first_run.result_dialog.logs.click()
    assert w.tabs.currentWidget() is w.run_records_tab
    assert app_settings.get_guide_progress()
    c.restart()
    assert c.step == "target_selection" and c.first_run.result is None
    # Persisted running/result positions after process restart return to Start.
    c._go("first_auto_run", "running")
    assert c.detail == "start" and c.first_run.result_dialog is None


def test_different_species_never_shows_wrong_samples(guided):
    _, p, c = guided
    runner = begin_run(guided)
    p._activate_delay_profile(482)
    dialog = finish_run(guided, runner)
    assert dialog.value.text() == "120 帧" and not dialog.primary.isEnabled()
    assert "当前目标已切换" in dialog.copy.text()


def test_shiny_stop_preserves_game_and_does_not_claim_reverse_failure(guided):
    _, p, c = guided
    runner = begin_run(guided)
    finish_run(guided, runner, ())
    c.first_run.result_dialog.reject()
    c.first_run.outcome = AutoRngProgress(phase=AutoRngPhase.COMPLETED, log_message="疑似出闪，已停止自动流程")
    c.first_run._settled()
    dialog = c.first_run.result_dialog
    assert "疑似出闪" in dialog.title.text()
    assert "保留当前游戏现场" in dialog.copy.text()
    assert dialog.primary.text() == "返回准备"
    assert p._runner_thread is None


def test_complete_guide_preserves_settings_and_reports_write_failure(tmp_path, monkeypatch):
    path = tmp_path / "guide.json"
    app_settings.save_settings({"other": "保留"}, path)
    app_settings.start_guide_progress(path)
    def fail(*_):
        raise OSError("disk full")
    with monkeypatch.context() as m:
        m.setattr(app_settings.os, "replace", fail)
        with pytest.raises(OSError):
            app_settings.complete_guide_progress(path)
    assert app_settings.get_guide_progress(path)
    app_settings.complete_guide_progress(path)
    assert app_settings.get_guide_progress(path) is None
    assert app_settings.load_settings(path)["other"] == "保留"
