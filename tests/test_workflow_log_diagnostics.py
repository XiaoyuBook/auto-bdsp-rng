"""Inject workflow faults through production workers and live/default log views.

Only hardware, recognition, time and notification transports are simulated.
TID reverse-stage cases start from an explicitly prepared Seed/target snapshot.
"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from auto_bdsp_rng.automation.auto_rng.dialog_timing import (
    DialogFrameCaptureError, DialogKeywordTimeoutError, DialogOcrError, DialogTimingEvent,
)
from auto_bdsp_rng.automation.auto_rng.models import (
    AutoRngConfig, AutoRngPhase, AutoRngSeedResult, ReverseLookupResult, ShinyCheckResult,
)
from auto_bdsp_rng.automation.auto_rng.runner import AutoRngServices
from auto_bdsp_rng.automation.auto_rng.scripts import AUTO_ADVANCE_PARAMETER
from auto_bdsp_rng.automation.auto_tid_rng import (
    AutoTidRngConfig, AutoTidRngPhase, AutoTidRngProgress, AutoTidRngServices, AutoTidSeedResult, AutoTidTarget,
)
from auto_bdsp_rng.automation.easycon.native.device import DeviceNotConnectedError
from auto_bdsp_rng.blink_detection.project_xs import ProjectXsIntegrationError
from auto_bdsp_rng.gen8_id import IDState8
from auto_bdsp_rng.log_events import task_failure
from auto_bdsp_rng.rng_core import SeedPair64
from tests.test_start_readiness import window
from tests.ui_wait import wait_until


PRIVATE = r"Project_Xs; C:\private\diagnostic.json; backend=False"


def keyword_timeout(stage):
    event = DialogTimingEvent("timeout_" + stage, 40.0, 30.0)
    return DialogKeywordTimeoutError(PRIVATE, stage=stage, event=event, events=(event,))


def assert_views(w, run_id, *, summary, facts=(), raw="", allow_roundless=False):
    entries = [entry for entry in w._run_log_buffer.snapshot() if entry.run_id == run_id and entry.user_visible]
    issue = next(entry for entry in entries if summary in entry.user_message)
    wait_until(lambda: summary in w.live_log_panel.text.toPlainText())
    for fact in facts:
        assert fact in issue.user_message, (fact, issue.user_message)
    for entry in entries:
        for internal in ("Project_Xs", "private", "backend=", "RuntimeError", "DialogOcrError"):
            assert internal not in entry.user_message
            assert internal not in entry.user_details_text()
            assert internal not in w.live_log_panel.text.toPlainText()
    if not allow_roundless:
        assert all(entry.round_id is not None for entry in entries)
    full = w.run_log_window.log_panel
    full.show_round_logs(run_id, issue.round_id)
    row = next(i for i, entry in enumerate(full.visible_entries()) if entry.seq == issue.seq)
    full.table.setCurrentIndex(full.proxy_model.index(row, 3))
    full.details_button.setChecked(True)
    details = full.details_text.toPlainText()
    assert summary in details
    for fact in facts:
        assert fact in details
    if raw:
        assert raw not in details
        full.raw_details_button.setChecked(True)
        assert raw in full.details_text.toPlainText()
        full.raw_details_button.setChecked(False)
    return issue, entries, details


RNG_CASES = [
    ("no_candidates", "本轮没有可用目标，自动定点流程已结束", ("当前可达候选 0 个", "扩大搜索范围或放宽筛选条件")),
    ("target_missed", "已错过目标，正在重新搜索", ("目标推进 280", "脚本启动点 180")),
    ("search_failure", "目标搜索失败，流程已停止", ("未找到所需文件", "搜索上限 10,000 帧")),
    ("advance_failure", "过帧脚本执行失败，流程已停止", ("串口不可用或连接已断开",)),
    ("post_advance_seed", "测种失败（连续 1/5），将重新运行测种脚本", ("过帧脚本已完成", "失败发生在重新测种阶段")),
    ("correction_retry", "校正失败，准备第 2/2 次尝试", ("未找到匹配的推进位置",)),
    ("correction_last_round", "位置校正失败，流程已停止", ("已达到设定的 1 轮",)),
    ("correction_recapture", "位置校正连续 2 次失败，正在重新测种补救", ("未找到匹配的推进位置", "最多尝试 2 次")),
    ("recovery_exhausted_last_round", "测种失败，流程已停止", ("未找到所需文件", "已达到设定的 1 轮")),
    ("recovery_exhausted", "补救测种连续 2 次失败，将从测种脚本重新开始", ("未找到所需文件", "连续测种失败计数 1/5")),
    ("exit_script_failure", "过场脚本执行失败，流程已停止", ("串口不可用或连接已断开",)),
    ("exit_correction_last_round", "过场校正失败，流程已停止", ("已达到设定的 1 轮",)),
    ("exit_correction", "过场校正连续 2 次失败，将从测种脚本重新开始", ("未找到匹配的推进位置",)),
    ("shiny_frame", "判闪失败，流程已停止", ("读取游戏画面超时",)),
    ("shiny_ocr", "判闪失败，流程已停止", ("文字识别失败",)),
    ("keyword_before", "无法确认是否为闪光，流程已停止", ("第一个判闪关键词", "人工确认")),
    ("keyword_after", "无法确认是否为闪光，流程已停止", ("第二个判闪关键词", "人工确认")),
    ("keyword_continue", "本次判闪未能确认，开始下一轮测种", ("第二个判闪关键词",)),
    ("reverse_failure", "自动反查失败，本轮结束", ("未找到所需文件", "未取得反查结果")),
    ("reverse_no_stats", "反查完成，找到 0 个匹配个体", ("已尝试 3 次", "均未识别到完整六项能力值", "保留原 delay")),
    ("reverse_invalid_stats", "反查完成，找到 0 个匹配个体", ("无法换算成合法个体值范围", "未执行匹配搜索")),
    ("reverse_no_match", "反查完成，找到 0 个匹配个体", ("已识别到能力值", "前后 500 帧内未找到匹配个体")),
    ("delay_save", "延迟样本保存失败，本轮继续使用原策略", ("权限不足",)),
    ("recording_failure", "疑似闪光录像操作失败", ("串口不可用或连接已断开", "流程仍将停止")),
    ("escape_failure", "逃跑脚本执行失败，流程已停止", ("串口不可用或连接已断开",)),
]


@pytest.mark.parametrize("scenario,summary,facts", RNG_CASES, ids=[case[0] for case in RNG_CASES])
def test_rng_faults_use_actual_worker_and_log_views(window, monkeypatch, tmp_path, scenario, summary, facts):
    w = window
    w.tabs.setCurrentWidget(w.auto_rng_tab)
    paths = {name: tmp_path / (name + ".txt") for name in ("测种", "过帧", "撞闪", "过场", "反查", "逃跑")}
    for name, path in paths.items():
        path.write_text(f"{AUTO_ADVANCE_PARAMETER} = 0\nA 100\n" if name == "过帧" else "A 100\n", encoding="utf-8")
    clock, captures, corrections, exits, scripts, notifications = [10.0], [], [], [], [], []
    exit_cases = {"exit_correction", "exit_script_failure", "exit_correction_last_round"}
    searches = []
    reverse_cases = {"reverse_failure", "reverse_no_stats", "reverse_invalid_stats", "reverse_no_match", "delay_save"}
    correction_cases = {"correction_retry", "correction_last_round", "correction_recapture", "recovery_exhausted", "recovery_exhausted_last_round"}

    def capture():
        captures.append(len(captures) + 1)
        if (scenario == "post_advance_seed" and len(captures) == 2) or (scenario in {"recovery_exhausted", "recovery_exhausted_last_round"} and len(captures) in (2, 3)):
            raise FileNotFoundError(PRIVATE)
        return AutoRngSeedResult(seed=f"seed-{len(captures)}", current_advances=0)

    def search(_seed):
        searches.append(True)
        if scenario == "no_candidates":
            return []
        if scenario == "target_missed" and len(searches) == 1:
            clock[0] += 400 * 1.018
        if scenario == "search_failure":
            raise FileNotFoundError(PRIVATE)
        advanced = scenario == "advance_failure" or (scenario == "post_advance_seed" and len(captures) == 1) or (scenario in correction_cases and len(captures) == 1)
        values = (2000,) if advanced else (280, 500, 800)
        return [SimpleNamespace(advances=n, ec=n, pid=n) for n in values]

    def script(_text, name):
        scripts.append(name)
        if (scenario == "exit_script_failure" and name == paths["过场"].name) or (scenario == "advance_failure" and name == paths["过帧"].name) or (scenario == "escape_failure" and name == paths["逃跑"].name) or (scenario == "recording_failure" and name == "auto_capture"):
            raise DeviceNotConnectedError(PRIVATE)

    def reidentify(seed):
        corrections.append(len(corrections) + 1)
        if scenario != "correction_retry" or len(corrections) == 1:
            raise ProjectXsIntegrationError("校正未找到匹配状态; " + PRIVATE)
        return replace(seed, current_advances=1800)

    def exit_reidentify(seed):
        exits.append(len(exits) + 1)
        if len(exits) <= 2:
            raise ProjectXsIntegrationError("校正未找到匹配状态; " + PRIVATE)
        return seed

    def hit(_text, name, _threshold):
        scripts.append(name)
        if scenario == "shiny_frame":
            try:
                raise TimeoutError("读取游戏画面超时; " + PRIVATE)
            except TimeoutError as exc:
                raise DialogFrameCaptureError(PRIVATE) from exc
        if scenario == "shiny_ocr":
            raise DialogOcrError(PRIVATE)
        if scenario.startswith("keyword_") and (scenario != "keyword_continue" or len(captures) == 1):
            return ShinyCheckResult(False, error=keyword_timeout("before_first" if scenario == "keyword_before" else "after_first"))
        return ShinyCheckResult(scenario == "recording_failure", 5.0 if scenario == "recording_failure" else 2.3)

    def reverse_lookup(_seed, _target):
        if scenario == "reverse_failure" and len(captures) == 1:
            raise FileNotFoundError(PRIVATE)
        if scenario in {"reverse_no_stats", "reverse_invalid_stats", "reverse_no_match"}:
            return ReverseLookupResult(
                stats_recognized=scenario != "reverse_no_stats", search_performed=scenario == "reverse_no_match", attempts=3,
            )
        return ReverseLookupResult(candidates=(SimpleNamespace(shiny=0),), delays=(105,))

    def record_sample(_delays):
        if scenario == "delay_save":
            raise PermissionError(PRIVATE)

    services = AutoRngServices(
        capture_seed=capture, search_candidates=search, reidentify=reidentify,
        reidentify_exit=exit_reidentify, run_script_text=script, run_hit_script_with_shiny_check=hit,
        run_reverse_lookup=reverse_lookup, record_delay_observation=record_sample,
        monotonic=lambda: clock[0], sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )
    config = AutoRngConfig(
        script_dir=tmp_path, seed_script_path=paths["测种"], advance_script_path=paths["过帧"], hit_script_path=paths["撞闪"],
        escape_script_path=paths["逃跑"], reverse_script_path=paths["反查"],
        exit_script_path=paths["过场"] if scenario in exit_cases else None,
        fixed_delay=100, max_wait_frames=300, max_advances=10000, shiny_threshold_seconds=4.0,
        reidentify_max_attempts=2, reidentify_seed_max_attempts=2,
        reidentify_failure_policy="recapture_seed" if scenario in {"correction_recapture", "recovery_exhausted", "recovery_exhausted_last_round"} else "next_round",
        reseed_threshold_frames=400 if scenario == "post_advance_seed" else 900000,
        reseeding_threshold=10000 if scenario in exit_cases else 0,
        target_species=481 if scenario in {"keyword_before", "keyword_after"} else None,
        auto_reverse=scenario in reverse_cases, escape_continue=scenario == "escape_failure",
        loop_mode="count" if scenario in {"correction_last_round", "recovery_exhausted_last_round", "exit_correction_last_round", "reverse_failure", "keyword_continue"} else "single",
        loop_count=1 if scenario in {"correction_last_round", "recovery_exhausted_last_round", "exit_correction_last_round"} else 2,
    )
    monkeypatch.setattr(w, "_ensure_preview_for_auto_rng", lambda: True)
    monkeypatch.setattr(w.easycon_tab, "_native_is_connected", lambda: True)
    monkeypatch.setattr(w, "_build_auto_rng_services", lambda _config: services)
    monkeypatch.setattr(w, "_notify_qq_task", lambda *args: notifications.append(args))
    w._start_auto_rng_after_warmup(config)
    worker, run_id = w.auto_rng_tab._runner_worker, w._active_auto_rng_run_id
    assert worker is not None and run_id
    runner = worker.runner
    wait_until(lambda: w.auto_rng_tab._runner_thread is None)
    no_raw_error = scenario in {"no_candidates", "target_missed", "reverse_no_stats", "reverse_invalid_stats", "reverse_no_match"}
    issue, entries, details = assert_views(w, run_id, summary=summary, facts=facts, raw="" if no_raw_error else PRIVATE)
    stops = {"recovery_exhausted_last_round", "exit_script_failure", "exit_correction_last_round", "search_failure", "advance_failure", "correction_last_round", "shiny_frame", "shiny_ocr", "keyword_before", "keyword_after", "escape_failure"}
    if scenario in stops:
        assert issue.level == "ERROR"
        assert notifications[-1][2] == "失败"
        assert not any("第 2 轮开始" in entry.user_message for entry in entries)
        if scenario == "advance_failure":
            assert len([entry for entry in entries if "过帧脚本执行失败" in entry.user_message]) == 1
        if scenario == "correction_last_round":
            assert "重新开始" not in issue.user_message
    else:
        assert runner.progress.phase == AutoRngPhase.COMPLETED
        assert notifications[-1][2] == "已完成"
    if scenario in {"post_advance_seed", "recovery_exhausted", "exit_correction", "reverse_failure", "keyword_continue"}:
        assert any("第 2 轮开始" in entry.user_message for entry in entries)
        assert captures[-1] in (2, 3, 4)
        if scenario == "reverse_failure":
            assert issue.round_id == 1
            assert any(entry.event_type == "lookup.completed" and entry.round_id == 2 for entry in entries)
    if scenario == "correction_recapture":
        assert len(captures) == 2 and runner._completed_loops == 1
        assert "具体原因：本次校正未找到匹配的推进位置" in details
    if scenario == "recording_failure":
        assert "auto_capture" in scripts
        assert any(entry.event_type == "task.needs_confirmation" for entry in entries)
        assert "已录像" not in str(runner.progress.log_message)
    if scenario.startswith("keyword_"):
        assert issue.level in {"WARNING", "ERROR"}
        assert "当前记录未提供可确认的具体原因" not in details
        assert "本次未检测到闪光" not in issue.user_message
    if no_raw_error:
        assert "当前记录未提供可确认的具体原因" not in details


TID_CASES = [
    ("search_failure", "目标 TID 搜索失败，流程已停止", ("未找到所需文件", "搜索上限 300 帧")),
    ("ocr_exception", "TID 识别失败，流程已停止", ("未找到所需文件",)),
    ("ocr_empty", "未识别到有效 TID，流程已停止", ("文字识别没有返回内容",)),
    ("ocr_invalid", "未识别到有效 TID，流程已停止", ("没有可用的 TID 数字",)),
    ("calibration_not_found", "未找到匹配的 TID，流程已停止", ("识别 TID 12345", "目标推进 10", "前后 50 帧", "未更新")),
    ("calibration_error", "延迟校准失败，流程已停止", ("权限不足",)),
]


@pytest.mark.parametrize("scenario,summary,facts", TID_CASES, ids=[case[0] for case in TID_CASES])
def test_tid_faults_use_actual_worker_and_log_views(window, monkeypatch, tmp_path, scenario, summary, facts):
    w = window
    w.tabs.setCurrentWidget(w.auto_tid_rng_tab)
    script = tmp_path / "测种.txt"
    script.write_text("A 100\n", encoding="utf-8")
    clock, notifications = [10.0], []
    seed = AutoTidSeedResult(SeedPair64(1, 2), measured_at=10.0)

    def search(*_args):
        raise FileNotFoundError(PRIVATE)

    def recognize():
        if scenario == "ocr_exception":
            raise FileNotFoundError(PRIVATE)
        return "" if scenario == "ocr_empty" else PRIVATE if scenario == "ocr_invalid" else "12345"

    def lookup(*_args):
        if scenario == "calibration_error":
            raise PermissionError(PRIVATE)
        return None

    config = AutoTidRngConfig(script_dir=tmp_path, seed_script_path=script, target_display_tids=(123456,), delay=2)
    services = AutoTidRngServices(
        capture_seed=lambda: seed, search_id_states=search, lookup_tid_state=lookup,
        run_script_text=lambda *_: None, recognize_tid=recognize,
        monotonic=lambda: clock[0], sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )
    start = w.auto_tid_rng_tab.run_with_runner

    def start_at_stage(runner):
        if scenario != "search_failure":
            # Reverse calibration has an existing Seed and target; don't pretend
            # the normal name-script completion automatically enters this path.
            runner._completed_loops = 1
            runner._seed_result = seed
            runner._target = AutoTidTarget.from_state(IDState8(advances=10, tid=1, sid=100, tsv=0, display_tid=123456))
            runner.progress = AutoTidRngProgress(
                phase=AutoTidRngPhase.OCR_TID, loop_index=1, current_advances=8,
                target_advances=10, trigger_advances=8,
            )
        start(runner)

    monkeypatch.setattr(w, "_ensure_preview_for_auto_rng", lambda: True)
    monkeypatch.setattr(w.easycon_tab, "_native_is_connected", lambda: True)
    monkeypatch.setattr(w, "_build_auto_tid_rng_services", lambda _config: services)
    monkeypatch.setattr(w, "_notify_qq_task", lambda *args: notifications.append(args))
    monkeypatch.setattr(w.auto_tid_rng_tab, "run_with_runner", start_at_stage)
    w._start_auto_tid_rng(config)
    worker, run_id = w.auto_tid_rng_tab._runner_worker, w._active_auto_tid_run_id
    assert worker is not None and run_id
    wait_until(lambda: w.auto_tid_rng_tab._runner_thread is None)
    raw = "" if scenario in {"ocr_empty", "calibration_not_found"} else PRIVATE
    issue, entries, details = assert_views(w, run_id, summary=summary, facts=facts, raw=raw)
    assert issue.level == "ERROR" and notifications[-1][2] == "失败"
    assert len([entry for entry in entries if entry.event_type == "task.failed"]) == 1
    if scenario in {"ocr_empty", "ocr_invalid", "calibration_not_found"}:
        assert "当前记录未提供可确认的具体原因" not in details


@pytest.mark.parametrize("phase", [phase for phase in AutoRngPhase if phase not in {AutoRngPhase.IDLE, AutoRngPhase.COMPLETED, AutoRngPhase.FAILED}])
def test_every_rng_stage_has_a_specific_stop_description(phase):
    message = task_failure(RuntimeError(PRIVATE), task="自动定点", phase=phase.value)
    assert message.summary != "自动定点失败，流程已停止"
    assert "流程已停止" in message.summary
    assert PRIVATE in message.details
    assert "当前记录未提供可确认的具体原因" in message.user_details


@pytest.mark.parametrize("preceding", ["script", "shiny"])
def test_recovery_error_does_not_inherit_the_previous_step_or_script_location(preceding):
    from auto_bdsp_rng.automation.easycon.native.errors import ScriptCompileError, SourceLocation

    earlier = (
        ScriptCompileError("赋值语句缺少表达式", SourceLocation("测种.txt", 2, 6))
        if preceding == "script" else DialogOcrError(PRIVATE)
    )
    try:
        raise earlier
    except Exception:
        try:
            raise FileNotFoundError(PRIVATE)
        except FileNotFoundError as error:
            event = task_failure(error, task="自动定点", phase=AutoRngPhase.REVERSE_LOOKUP.value)
    assert event.summary == "个体反查失败，流程已停止"
    assert event.context.startswith("未找到所需文件")
    assert event.user_details.startswith("具体原因：未找到所需文件")
    assert "相关原因：" in event.user_details
    assert "错误位置：" not in event.user_details
    assert type(earlier).__name__ in event.details


def test_unknown_recovery_error_does_not_claim_the_previous_failure_as_its_cause():
    try:
        raise ProjectXsIntegrationError("校正未找到匹配状态; " + PRIVATE)
    except Exception:
        try:
            raise RuntimeError(PRIVATE)
        except RuntimeError as error:
            event = task_failure(error, task="自动定点", phase=AutoRngPhase.CAPTURE_SEED.value)
    assert "当前记录未提供可确认的具体原因" in event.user_details.split("相关原因：")[0]
    assert "相关原因：" in event.user_details
    assert "未找到匹配的推进位置" not in event.context


@pytest.mark.parametrize("stage", ["before_first", "after_first"])
def test_real_dialog_timeout_survives_the_main_window_service(window, monkeypatch, tmp_path, stage):
    from auto_bdsp_rng.automation.auto_rng.dialog_timing import measure_keyword_interval
    from auto_bdsp_rng.automation.easycon import EasyConStatus
    from auto_bdsp_rng.ui import main_window as main_window_module

    class Backend:
        def run_script_text(self, *_args, **_kwargs):
            return "ok"

    clock = [10.0]
    window._video_source_connected = True
    monkeypatch.setattr(window.easycon_tab, "_native_status", lambda: EasyConStatus.BRIDGE_CONNECTED)
    monkeypatch.setattr(window.easycon_tab, "_ensure_native_backend", lambda: Backend())
    monkeypatch.setattr(window, "_call_on_ui_thread", lambda callback: callback())

    def measure(_capture, _read, **kwargs):
        kwargs["script_done"].set()
        return measure_keyword_interval(
            lambda: object(), lambda _image: "出现了！" if stage == "after_first" else "",
            monotonic=lambda: clock[0], sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds), **kwargs,
        )

    monkeypatch.setattr(main_window_module, "measure_keyword_interval", measure)
    services = window._build_auto_rng_services(AutoRngConfig(script_dir=tmp_path, shiny_threshold_seconds=4.0))
    result = services.run_hit_script_with_shiny_check("A 100\n", "撞闪.txt", 4.0)
    assert result.interval_seconds is None
    assert isinstance(result.error, DialogKeywordTimeoutError)
    assert result.error.stage == stage


@pytest.mark.parametrize("recognized", [False, True])
def test_reverse_service_retains_whether_recognition_or_conversion_failed(window, monkeypatch, tmp_path, recognized):
    from auto_bdsp_rng.automation.auto_rng.models import AutoRngTarget
    from auto_bdsp_rng.automation.easycon import EasyConStatus
    from auto_bdsp_rng.ui import main_window as main_window_module

    class Backend:
        def run_script_text(self, *_args, **_kwargs):
            return "ok"

    path = tmp_path / "反查.txt"
    path.write_text("A 100\n", encoding="utf-8")
    window._video_source_connected = True
    monkeypatch.setattr(window.easycon_tab, "_native_status", lambda: EasyConStatus.BRIDGE_CONNECTED)
    monkeypatch.setattr(window.easycon_tab, "_ensure_native_backend", lambda: Backend())
    monkeypatch.setattr(window, "_call_on_ui_thread", lambda callback: callback())
    monkeypatch.setattr(window, "_capture_preview_frame_for_config", lambda _config: object())
    monkeypatch.setattr(window, "_pause_ocr_and_turn_to_stats_page", lambda **_kwargs: None)
    monkeypatch.setattr(main_window_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(main_window_module, "recognize_characteristic_full_frame", lambda _frame: None)
    # Zero ability values cannot be converted into legal IVs, so no search
    # is allowed to be claimed by the diagnostic result.
    stats = dict.fromkeys(("HP", "攻击", "防御", "特攻", "特防", "速度"), 0)
    monkeypatch.setattr(main_window_module, "extract_pokemon_info", lambda **_kwargs: {"stats": stats if recognized else None})
    monkeypatch.setattr(main_window_module, "generate_static_candidates", lambda _criteria: pytest.fail("invalid ranges must not search"))
    services = window._build_auto_rng_services(AutoRngConfig(script_dir=tmp_path, reverse_script_path=path))
    result = services.run_reverse_lookup(AutoRngSeedResult(SeedPair64(1, 2)), AutoRngTarget(raw_target_advances=1204))
    assert result.candidates == result.delays == ()
    assert result.stats_recognized is recognized
    assert result.search_performed is False
    assert result.attempts == 3


@pytest.mark.parametrize("task", ["rng", "tid"])
def test_startup_failure_keeps_its_stage_and_exception_through_the_real_window(window, monkeypatch, tmp_path, task):
    w = window
    panel = w.auto_rng_tab if task == "rng" else w.auto_tid_rng_tab
    w.tabs.setCurrentWidget(panel)
    monkeypatch.setattr(w, "_ensure_preview_for_auto_rng", lambda: True)
    monkeypatch.setattr(w.easycon_tab, "_native_is_connected", lambda: True)
    monkeypatch.setattr(w, "_notify_qq_task", lambda *_args: None)

    def fail(_config):
        raise FileNotFoundError(PRIVATE)

    if task == "rng":
        monkeypatch.setattr(w, "_build_auto_rng_services", fail)
        w._start_auto_rng_after_warmup(AutoRngConfig(script_dir=tmp_path))
        summary = "自动定点未能启动"
    else:
        monkeypatch.setattr(w, "_build_auto_tid_rng_services", fail)
        w._start_auto_tid_rng(AutoTidRngConfig(script_dir=tmp_path))
        summary = "自动 TID 未能启动"
    entry = next(entry for entry in w._run_log_buffer.snapshot() if summary in entry.user_message)
    assert entry.run_id
    assert_views(w, entry.run_id, summary=summary, facts=("未找到所需文件",), raw=PRIVATE, allow_roundless=True)
    assert panel._runner_thread is None
