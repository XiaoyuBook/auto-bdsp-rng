from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from auto_bdsp_rng.automation.auto_rng.models import AutoRngConfig, AutoRngPhase, AutoRngProgress, AutoRngSeedResult, ShinyCheckResult
from auto_bdsp_rng.automation.auto_rng.runner import AutoRngRunner, AutoRngServices
from auto_bdsp_rng.log_events import LogMessage
from auto_bdsp_rng.ui.live_log_panel import LiveLogPanel
from auto_bdsp_rng.ui.run_log_panel import RunLogBuffer, RunLogPanel
from tests.test_run_records_ui import app
from tests.test_start_readiness import window


def run_rng_scenario(directory, *, no_target=False, monitor=True):
    """Use the production state machine with a clock and controller stand-ins."""
    directory.mkdir(exist_ok=True)
    seed_script, hit_script = directory / "测种.txt", directory / "撞闪.txt"
    seed_script.write_text("A 100\n", encoding="utf-8")
    hit_script.write_text("B 100\n", encoding="utf-8")
    clock, scripts, buffer = [10.0], [], RunLogBuffer()
    candidates = [] if no_target else [SimpleNamespace(advances=a, pid=i, ec=i) for i, a in enumerate((280, 500, 800))]

    def publish(progress):
        if progress.log_message:
            buffer.publish("自动定点", progress.log_message, run_id="readability", round_id=progress.loop_index or None)

    runner = AutoRngRunner(
        AutoRngConfig(script_dir=directory, seed_script_path=seed_script, hit_script_path=hit_script,
                      fixed_delay=100, max_wait_frames=300, max_advances=10000, shiny_threshold_seconds=4.0),
        services=AutoRngServices(
            capture_seed=lambda: AutoRngSeedResult(seed="seed-1", current_advances=0, npc=0),
            search_candidates=lambda _: candidates,
            run_script_text=lambda _text, name: scripts.append(name),
            run_hit_script_with_shiny_check=(lambda _text, name, _threshold: scripts.append(name) or ShinyCheckResult(False, 2.3)) if monitor else None,
            monotonic=lambda: clock[0], sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
        ), progress_callback=publish,
    )
    result = runner.run(max_steps=20)
    return buffer, result, scripts, clock[0]


def test_actual_rng_flow_explains_results_trigger_wait_and_shiny_basis(app, tmp_path):
    buffer, result, scripts, now = run_rng_scenario(tmp_path)
    live, full = LiveLogPanel(buffer), RunLogPanel(buffer)
    text = live.text.toPlainText()
    assert result.phase == AutoRngPhase.COMPLETED
    assert scripts == ["测种.txt", "撞闪.txt"]
    assert now == pytest.approx(10 + 180 * 1.018)
    for fact in ("第 1 轮开始", "搜索上限 10,000", "3 个可达候选", "目标推进 280", "脚本在 180 启动", "delay 100", "识别间隔 2.300 秒", "判闪阈值 4.000 秒"):
        assert fact in text
    waiting = [entry for entry in buffer.snapshot() if entry.event_type == "wait.progress"]
    assert waiting and max(entry.repeat_count for entry in waiting) >= 3
    assert "仍在等待撞闪" in text and "当前推进" in text
    assert "monotonic" not in text and "current=" not in text and str(tmp_path) not in text
    assert "识别间隔 2.300 秒" in full._filtered_text()
    assert live.text.document().blockCount() == 2 * len([entry for entry in buffer.snapshot() if entry.user_visible])


def test_no_target_is_a_search_result_with_scope_and_next_step(app, tmp_path):
    buffer, result, scripts, _ = run_rng_scenario(tmp_path, no_target=True)
    text = LiveLogPanel(buffer).text.toPlainText()
    assert result.phase == AutoRngPhase.COMPLETED and scripts == ["测种.txt"]
    assert "搜索上限 10,000 帧，匹配 0 个个体" in text
    assert "当前可达候选 0 个" in text
    assert "扩大搜索范围或放宽筛选条件" in text
    assert all(entry.level == "INFO" for entry in buffer.snapshot())


def test_script_completion_does_not_imply_a_confirmed_game_result(app, tmp_path):
    buffer, result, scripts, _ = run_rng_scenario(tmp_path, monitor=False)
    text = LiveLogPanel(buffer).text.toPlainText()
    assert result.phase == AutoRngPhase.COMPLETED and scripts == ["测种.txt", "撞闪.txt"]
    assert "请人工确认游戏结果" in text and "本轮没有自动判闪结果" in text
    assert "本次未检测到闪光" not in text


@pytest.mark.parametrize("task", ["rng", "tid"])
def test_worker_failure_names_the_step_and_recovery_without_exposing_exception(app, tmp_path, task):
    from auto_bdsp_rng.automation.auto_tid_rng import AutoTidRngPhase, AutoTidRngProgress
    from auto_bdsp_rng.ui.auto_rng_panel import AutoRngPanel
    from auto_bdsp_rng.ui.auto_tid_rng_panel import AutoTidRngPanel

    buffer = RunLogBuffer()
    sink = lambda level, message: buffer.publish("自动任务", message, level)
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    if task == "rng":
        panel = AutoRngPanel(settings=settings, run_log_sink=sink)
        panel.apply_progress(AutoRngProgress(phase=AutoRngPhase.CAPTURE_SEED))
    else:
        panel = AutoTidRngPanel(settings=settings, run_log_sink=sink)
        panel.apply_progress(AutoTidRngProgress(phase=AutoTidRngPhase.CAPTURE_TIDSID))
    raw = "TimeoutError: C:\\private\\capture.json; monotonic=123; backend=False"
    panel._runner_failed(raw)
    live_text = LiveLogPanel(buffer).text.toPlainText()
    assert "测种失败，流程已停止" in live_text
    assert "超时" not in live_text
    assert "眼睛框选" not in live_text
    assert "画面" not in live_text
    assert any(raw in entry.full_text() for entry in buffer.snapshot())
    assert "private" not in live_text and "monotonic" not in live_text


def test_authored_context_does_not_declassify_internal_events(app):
    buffer = RunLogBuffer()
    buffer.publish("诊断", LogMessage("raw", summary="内部状态", context="private-context", diagnostic=True), level="ERROR")
    buffer.publish("诊断", LogMessage("raw", context="private-context"))
    assert "private-context" not in LiveLogPanel(buffer).text.toPlainText()


def test_user_can_search_and_copy_the_published_facts_without_internal_text(app):
    buffer = RunLogBuffer()
    panel = RunLogPanel(buffer)
    buffer.publish("自动定点", LogMessage(
        "private: target=280; trigger=180; monotonic=100",
        summary="已锁定目标", context="目标推进 280；脚本在 180 启动。",
    ))
    panel.search_edit.setText("脚本在 180 启动")
    assert len(panel.visible_entries()) == 1
    panel.copy_filtered()
    assert "目标推进 280" in QApplication.clipboard().text()
    assert "private" not in QApplication.clipboard().text()
    assert "monotonic" not in QApplication.clipboard().text()
    assert "monotonic=100" in buffer.snapshot()[0].full_text()


def test_serial_failure_is_not_misreported_as_a_missing_script(app):
    buffer = RunLogBuffer()
    entry = buffer.publish("伊机控", "SerialException: could not open port COM3: FileNotFoundError", level="ERROR")
    assert "检查单片机连接和串口选择" in entry.user_message
    assert "脚本或配置文件不可用" not in entry.user_message


def test_tid_flow_shows_target_trigger_and_live_waiting_progress(app, tmp_path):
    from auto_bdsp_rng.automation.auto_tid_rng import AutoTidRngConfig, AutoTidRngPhase, AutoTidRngRunner, AutoTidRngServices, AutoTidSeedResult
    from auto_bdsp_rng.gen8_id import IDState8
    from auto_bdsp_rng.rng_core import SeedPair64

    script = tmp_path / "取名.txt"
    script.write_text("A 100\n", encoding="utf-8")
    clock, scripts, buffer = [100.0], [], RunLogBuffer()
    def publish(progress):
        if progress.log_message:
            buffer.publish("自动 TID", progress.log_message, round_id=progress.loop_index or None)
    runner = AutoTidRngRunner(
        AutoTidRngConfig(script_dir=tmp_path, name_script_path=script, start_phase=AutoTidRngPhase.CAPTURE_TIDSID,
                         target_display_tids=(123,), frame_threshold=1000, delay=1),
        services=AutoTidRngServices(
            capture_seed=lambda: AutoTidSeedResult(SeedPair64(1, 2), measured_at=100.0),
            search_id_states=lambda *_: (IDState8(advances=500, tid=123, sid=42, tsv=0, display_tid=123),),
            run_script_text=lambda _text, name: scripts.append(name),
            monotonic=lambda: clock[0], sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
        ), progress_callback=publish,
    )
    result = runner.run(max_steps=30)
    text = LiveLogPanel(buffer).text.toPlainText()
    assert result.phase == AutoTidRngPhase.COMPLETED and scripts == ["取名.txt"]
    for fact in ("目标 TID：000123", "目标推进 500", "取名脚本在 499 启动", "仍在等待取名", "已等待", "请核对游戏内最终显示的 TID"):
        assert fact in text
    assert "monotonic" not in text and "wait_keep_awake" not in text


def test_user_stopped_script_is_a_result_not_an_unread_problem(app, window):
    before = window.run_log_window.unread_problem_count
    window.easycon_tab._finish_run("已中止", exit_code=130)
    for _ in range(4):
        app.processEvents()
    entry = next(entry for entry in reversed(window._run_log_buffer.snapshot()) if entry.message == "已中止")
    assert entry.level == "INFO" and entry.user_message == "脚本已停止"
    assert window.run_log_window.unread_problem_count == before
