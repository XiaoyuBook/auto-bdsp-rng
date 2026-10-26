"""Exercise recovery logs through the real worker, history and main-window UI."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QModelIndex

from auto_bdsp_rng.automation.auto_rng.models import AutoRngConfig, AutoRngPhase, AutoRngSeedResult, ShinyCheckResult
from auto_bdsp_rng.automation.auto_rng.runner import AutoRngServices
from auto_bdsp_rng.automation.auto_rng.scripts import AUTO_ADVANCE_PARAMETER
from auto_bdsp_rng.run_log import RunLogManager
from tests.test_start_readiness import window
from tests.ui_wait import wait_until


@pytest.fixture(autouse=True)
def disable_update_checks(monkeypatch):
    monkeypatch.setenv('AUTO_BDSP_RNG_DISABLE_UPDATES', '1')


@pytest.mark.parametrize('scenario', [
    'seed_failure_then_success',
    'correction_failure_then_next_round',
    'five_seed_failures_stop',
    'seed_script_failure_stops',
])
def test_failure_and_next_round_logs_use_real_main_window_routing(window, monkeypatch, tmp_path, scenario):
    """Only device/recognition services and the notification transport are fake.

    Startup, QThread/Worker signals, history publication, run/round assignment,
    user summaries, selection/filtering and disk logging use production code.
    """
    w = window
    w.tabs.setCurrentWidget(w.auto_rng_tab)
    seed_script, advance_script, hit_script = (tmp_path / name for name in ('测种.txt', '过帧.txt', '撞闪.txt'))
    seed_script.write_text('A 100\n', encoding='utf-8')
    advance_script.write_text(f'{AUTO_ADVANCE_PARAMETER} = 0\nA 100\n', encoding='utf-8')
    hit_script.write_text('B 100\n', encoding='utf-8')
    clock, scripts, captures, corrections, notifications = [10.0], [], [], [], []
    raw_error = '模拟异常：Project_Xs 未完成操作；配置 C:\\private\\capture.json'

    def run_script(_text, name):
        scripts.append(name)
        if scenario == 'seed_script_failure_stops' and name == seed_script.name:
            raise RuntimeError(raw_error)

    def capture_seed():
        captures.append(len(captures) + 1)
        if scenario == 'five_seed_failures_stop' or (scenario == 'seed_failure_then_success' and len(captures) == 1):
            raise RuntimeError(raw_error)
        return AutoRngSeedResult(seed=f'simulated-seed-{len(captures)}', seed_text='模拟 Seed', current_advances=0)

    def candidates(_seed):
        advances = (1000,) if scenario == 'correction_failure_then_next_round' and len(captures) == 1 else (280, 500, 800)
        return [SimpleNamespace(advances=adv, ec=100 + index, pid=200 + index) for index, adv in enumerate(advances)]

    def reidentify(_seed):
        corrections.append(len(corrections) + 1)
        raise RuntimeError(raw_error)

    def hit_with_result(_text, name, _threshold):
        scripts.append(name)
        return ShinyCheckResult(False, 2.3)

    services = AutoRngServices(
        capture_seed=capture_seed, search_candidates=candidates, reidentify=reidentify,
        run_script_text=run_script, run_hit_script_with_shiny_check=hit_with_result,
        monotonic=lambda: clock[0], sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )
    config = AutoRngConfig(
        script_dir=tmp_path, seed_script_path=seed_script, advance_script_path=advance_script,
        hit_script_path=hit_script, fixed_delay=100, max_wait_frames=300, max_advances=10000,
        shiny_threshold_seconds=4.0, reidentify_max_attempts=2, reidentify_failure_policy='next_round',
    )
    monkeypatch.setattr(w, '_ensure_preview_for_auto_rng', lambda: True)
    monkeypatch.setattr(w.easycon_tab, '_native_is_connected', lambda: True)
    monkeypatch.setattr(w, '_build_auto_rng_services', lambda _config: services)
    monkeypatch.setattr(w, '_notify_qq_task', lambda *args: notifications.append(args))

    w._run_log_manager.close()
    manager = RunLogManager(tmp_path / 'diagnostics')
    monkeypatch.setattr(w, '_run_log_manager', manager)
    saved_path = manager.enable()
    try:
        w._start_auto_rng_after_warmup(config)
        worker = w.auto_rng_tab._runner_worker
        assert worker is not None
        runner, run_id = worker.runner, w._active_auto_rng_run_id
        assert run_id
        wait_until(lambda: w.auto_rng_tab._runner_thread is None)
        entries = [entry for entry in w._run_log_buffer.snapshot() if entry.run_id == run_id]
        visible = [entry for entry in entries if entry.user_visible]
        starts = [entry for entry in visible if entry.event_type == 'round.started']
        rounds = [entry.round_id for entry in starts]
        assert entries and all(entry.round_id is not None for entry in visible)
        user_text = '\n'.join(entry.user_message for entry in visible)
        for internal in ('Project_Xs', 'RuntimeError', 'private', 'monotonic', 'backend'):
            assert internal not in user_text
            assert internal not in w.live_log_panel.text.toPlainText()
            assert all(internal not in entry.user_details_text() for entry in visible)

        recovered = scenario in ('seed_failure_then_success', 'correction_failure_then_next_round')
        if recovered:
            assert rounds == [1, 2]
            assert captures == [1, 2]
            assert runner.progress.phase == AutoRngPhase.COMPLETED
            assert notifications[-1][2] == '已完成'
            assert not any(entry.level == 'ERROR' for entry in visible)
            problem = next(entry for entry in visible if entry.level == 'WARNING')
            assert problem.round_id == 1
            assert starts[0].seq < problem.seq < starts[1].seq
            recovered_seed = next(entry for entry in visible if entry.event_type == 'seed.captured' and entry.round_id == 2)
            assert starts[1].seq < recovered_seed.seq
            assert any('本次未检测到闪光，自动流程已结束' in entry.user_message and entry.round_id == 2 for entry in visible)
            assert '操作遇到问题' not in user_text
            archived = [entry for entry in entries if entry.event_type == 'history.round_restarted']
            assert len(archived) == 1 and archived[0].round_id == 1 and not archived[0].user_visible
            if scenario == 'seed_failure_then_success':
                assert scripts == ['测种.txt', '测种.txt', '撞闪.txt']
                warnings = [entry for entry in visible if entry.level == 'WARNING']
                assert len(warnings) == 1 and warnings[0].event_type == 'seed.retry'
                assert '测种失败（连续 1/5），将重新运行测种脚本' in warnings[0].user_message
                assert all('超时' not in entry.user_message and '眼睛框选' not in entry.user_message for entry in visible)
            else:
                assert scripts == ['测种.txt', '过帧.txt', '测种.txt', '撞闪.txt']
                assert corrections == [1, 2]
                warnings = [entry for entry in visible if entry.level == 'WARNING']
                assert len(warnings) == 2
                assert '校正失败，准备第 2/2 次尝试' in warnings[0].user_message
                assert '本轮未能继续，正在重新测种' in warnings[1].user_message
        else:
            expected_rounds = list(range(1, 6)) if scenario == 'five_seed_failures_stop' else [1]
            assert rounds == expected_rounds
            assert scripts == ['测种.txt'] * len(expected_rounds)
            assert notifications[-1][2] == '失败'
            failures = [entry for entry in visible if entry.level == 'ERROR']
            assert len(failures) == 1 and failures[0].round_id == expected_rounds[-1]
            assert visible[-1].seq == failures[0].seq
            problem = failures[0]
            assert not any(entry.event_type == 'seed.captured' for entry in entries)
            if scenario == 'five_seed_failures_stop':
                retries = [entry for entry in visible if entry.event_type == 'seed.retry']
                assert [entry.round_id for entry in retries] == [1, 2, 3, 4]
                assert '测种失败，流程已停止' in problem.user_message
                assert '已连续 5 次测种失败，触发保护停止' in problem.user_message
            else:
                assert captures == []
                assert '测种脚本执行失败，流程已停止' in problem.user_message
                assert not any(entry.event_type == 'seed.retry' for entry in visible)

        # Use actual filters and details, never a test-supplied constant round ID.
        full = w.run_log_window.log_panel
        full.show_round_logs(run_id, 1)
        assert full.visible_entries() and all(entry.round_id == 1 for entry in full.visible_entries())
        if recovered:
            if scenario == 'seed_failure_then_success':
                assert not any('测种完成，正在搜索可用目标' in entry.user_message for entry in full.visible_entries())
            full.show_round_logs(run_id, 2)
            assert full.visible_entries() and all(entry.round_id == 2 for entry in full.visible_entries())
            assert not any(entry.level in ('WARNING', 'ERROR') for entry in full.visible_entries())
        for problem in (entry for entry in visible if entry.level in ('WARNING', 'ERROR')):
            full.show_round_logs(run_id, problem.round_id)
            row = next(index for index, entry in enumerate(full.visible_entries()) if entry.seq == problem.seq)
            full.table.setCurrentIndex(full.proxy_model.index(row, 3))
            full.details_button.setChecked(True)
            assert problem.user_message in full.details_text.toPlainText()
            assert 'Project_Xs' not in full.details_text.toPlainText()
            full.raw_details_button.click()
            assert raw_error in full.details_text.toPlainText()
            full.raw_details_button.click()
        full.table.setCurrentIndex(QModelIndex())

        # Keep replay evidence beside each pytest scenario for human review.
        evidence = dict(
            scenario=scenario, recovered=recovered, scripts=scripts, rounds=rounds,
            user_entries=[dict(round=entry.round_id, level=entry.level, source=entry.source,
                               text=entry.user_message, event=entry.event_type, repeats=entry.repeat_count) for entry in visible],
        )
        (tmp_path / 'simulation-evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    finally:
        manager.close()
    assert raw_error in saved_path.read_text(encoding='utf-8')
