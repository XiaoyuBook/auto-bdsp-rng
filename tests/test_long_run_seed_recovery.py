from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from auto_bdsp_rng.automation.auto_rng.models import AutoRngConfig, AutoRngPhase, AutoRngSeedResult, ShinyCheckResult
from auto_bdsp_rng.automation.auto_rng.runner import AutoRngRunner, AutoRngServices
from auto_bdsp_rng.blink_detection.models import BlinkObservation
from auto_bdsp_rng.blink_detection.project_xs import ProjectXsSeedRecoveryError
from auto_bdsp_rng.rng_core import SeedState32
from auto_bdsp_rng.ui import main_window as main_window_module
from tests.test_start_readiness import window
from tests.ui_wait import wait_until


@pytest.fixture(autouse=True)
def disable_update_checks(monkeypatch):
    monkeypatch.setenv('AUTO_BDSP_RNG_DISABLE_UPDATES', '1')


def long_run_services(tmp_path, *, fail_once=False):
    """Reproduce the reported route; solver failures are explicitly injected."""
    seed, advance, hit = (tmp_path / name for name in ('seed.txt', 'advance.txt', 'hit.txt'))
    seed.write_text('A 100\n', encoding='utf-8')
    advance.write_text('_目标帧数 = 0\n', encoding='utf-8')
    hit.write_text('B 100\n', encoding='utf-8')
    state = dict(round=0, clock=0.0, advanced=False, failures=[], hits=0, scripts=[], starts=[])

    def script(text, name):
        state['scripts'].append((name, text))
        if name == seed.name:
            state['round'] += 1
        elif name == advance.name:
            state['advanced'] = True
            state['clock'] += 34_690.127  # Reported elapsed script time; no real wait.

    def capture():
        if state['advanced'] and (not fail_once or not state['failures']):
            state['failures'].append(state['round'])
            cause = AssertionError('injected solver failure; not an actual captured sample')
            observation = BlinkObservation.from_sequences([0, 1] * 20, [0] + [12] * 39)
            raise ProjectXsSeedRecoveryError(
                'Project_Xs could not validate the recovered seed', observation, 0, cause, stage='recover',
            ) from cause
        return AutoRngSeedResult(seed='simulated-seed', current_advances=0)

    def monitor(*_args):
        state['hits'] += 1
        return ShinyCheckResult(False, 2.3)

    def history(event, args):
        if event == 'cycle_start':
            state['starts'].append(args[0])

    config = AutoRngConfig(
        script_dir=tmp_path, seed_script_path=seed, advance_script_path=advance, hit_script_path=hit,
        loop_mode='count', loop_count=10, fixed_delay=212, max_wait_frames=300,
        max_advances=150_000_000, shiny_threshold_seconds=4,
    )
    services = AutoRngServices(
        capture_seed=capture,
        search_candidates=lambda _seed: [SimpleNamespace(
            advances=104_523_259 if state['round'] == 7 and not state['advanced'] else 280,
            ec=1, pid=2,
        )],
        run_script_text=script, run_hit_script_with_shiny_check=monitor,
        monotonic=lambda: state['clock'], sleep=lambda seconds: state.__setitem__('clock', state['clock'] + seconds),
    )
    return config, services, state, history


def test_six_good_rounds_then_large_advance_and_failures_cannot_start_round_eleven(tmp_path):
    config, services, state, history = long_run_services(tmp_path)
    progress = []
    result = AutoRngRunner(config, services=services, history_callback=history, progress_callback=progress.append).run()
    assert state['starts'] == list(range(1, 11))
    assert state['hits'] == 6
    assert state['failures'] == [7, 8, 9, 10]
    assert ('advance.txt', '_目标帧数 = 104523047\n') in state['scripts']
    assert result.phase == AutoRngPhase.FAILED and result.loop_index == 10
    assert result.log_message.summary == '测种失败，流程已停止'
    assert '已达到设定的 10 轮' in result.log_message.context
    assert '将重新运行' not in result.log_message.summary
    assert '测种求解输入' in result.log_message.details
    assert len([p for p in progress if getattr(p.log_message, 'event_type', '') == 'seed.retry']) == 3


def test_one_solver_failure_after_large_advance_does_not_poison_the_runner(tmp_path):
    config, services, state, history = long_run_services(tmp_path, fail_once=True)
    result = AutoRngRunner(config, services=services, history_callback=history).run()
    assert state['starts'] == list(range(1, 11))
    assert state['failures'] == [7]
    assert state['hits'] == 9
    assert result.phase == AutoRngPhase.COMPLETED


@pytest.mark.parametrize('limit', [1, 2, 3])
def test_fixed_round_limit_also_bounds_failures_before_the_five_failure_guard(tmp_path, limit):
    path = tmp_path / 'seed.txt'
    path.write_text('A 100\n', encoding='utf-8')
    scripts = []

    def fail_capture():
        raise RuntimeError('injected measurement failure')

    runner = AutoRngRunner(
        AutoRngConfig(script_dir=tmp_path, seed_script_path=path, loop_mode='count', loop_count=limit),
        services=AutoRngServices(capture_seed=fail_capture, run_script_text=lambda *args: scripts.append(args)),
    )
    result = runner.run()
    assert result.phase == AutoRngPhase.FAILED and result.loop_index == limit
    assert len(scripts) == limit


def test_large_advance_failure_limit_and_private_diagnostics_through_real_worker(window, monkeypatch, tmp_path):
    w = window
    config, services, state, _history = long_run_services(tmp_path)
    monkeypatch.setattr(w, '_ensure_preview_for_auto_rng', lambda: True)
    monkeypatch.setattr(w.easycon_tab, '_native_is_connected', lambda: True)
    monkeypatch.setattr(w, '_build_auto_rng_services', lambda _config: services)
    monkeypatch.setattr(w, '_notify_qq_task', lambda *_args: None)
    w._start_auto_rng_after_warmup(config)
    run_id = w._active_auto_rng_run_id
    assert run_id and w.auto_rng_tab._runner_worker is not None
    wait_until(lambda: w.auto_rng_tab._runner_thread is None)
    entries = [entry for entry in w._run_log_buffer.snapshot() if entry.run_id == run_id]
    starts = [entry.round_id for entry in entries if entry.event_type == 'round.started']
    assert starts == list(range(1, 11)) and state['hits'] == 6
    errors = [entry for entry in entries if entry.level == 'ERROR' and entry.user_visible]
    assert len(errors) == 1 and errors[0].round_id == 10
    assert '已达到设定的 10 轮' in errors[0].user_message
    assert '测种求解输入' in errors[0].details
    assert 'blinks' not in errors[0].user_details_text()
    assert 'blinks' not in w.live_log_panel.text.toPlainText()
    assert 'Project_Xs' not in w.live_log_panel.text.toPlainText()


def test_actual_capture_services_keep_fresh_solver_inputs_after_failure_and_rebuild(window, monkeypatch, tmp_path):
    """Probe the real service closures too, rather than replacing all services."""
    w = window
    observation = BlinkObservation.from_sequences([0, 1, 0], [999, 12, 24], offset_time=100)
    received = []
    should_fail = [False]

    def recover(actual, npc=0):
        received.append((actual.blinks, actual.intervals, npc))
        if should_fail[0]:
            raise ProjectXsSeedRecoveryError('solver failure', actual, npc, AssertionError(), stage='recover')
        return SimpleNamespace(state=SeedState32(1, 2, 3, 4))

    monkeypatch.setattr(main_window_module, 'capture_player_blinks', lambda *_args, **_kwargs: observation)
    monkeypatch.setattr(main_window_module, 'recover_seed_from_observation', recover)
    monkeypatch.setattr(main_window_module.time, 'perf_counter', lambda: 100.0)
    monkeypatch.setattr(w, '_recover_zoom_mode_with_preview_paused', lambda *_args: False)
    config = AutoRngConfig(script_dir=tmp_path)
    services = w._build_auto_rng_services(config)
    for _ in range(6):
        services.capture_seed()
    should_fail[0] = True
    with pytest.raises(ProjectXsSeedRecoveryError):
        services.capture_seed()
    should_fail[0] = False
    services.recover_zoom_mode()
    services.capture_seed()
    restarted = w._build_auto_rng_services(config)
    restarted.capture_seed()
    assert len(received) == 9 and all(value == received[0] for value in received)
    entries = [entry for entry in w._run_log_buffer.snapshot() if entry.event_type == 'seed.solver_input']
    assert len(entries) == 9
    snapshots = [json.loads(entry.message.split('：', 1)[1]) for entry in entries]
    assert snapshots[-2]['preparation'] == {'checks': 1, 'recoveries': 0}
    assert snapshots[-1]['preparation'] == {'checks': 0, 'recoveries': 0}
    assert all(not row['run_cancelled'] and not row['capture_cancelled'] and not row['script_reserved'] for row in snapshots)
    assert all(row['blinks'] == [0, 1, 0] and row['intervals'] == [999, 12, 24] for row in snapshots)
    assert all(entry.level == 'DEBUG' and entry.source == '自动定点' and not entry.user_visible for entry in entries)
    assert 'seed_config_path' not in w.live_log_panel.text.toPlainText()


def test_seed_recovery_retry_preserves_solver_diagnostics_in_progress_updates(tmp_path):
    observation = BlinkObservation.from_sequences([0, 1], [999, 12])

    def fail():
        raise ProjectXsSeedRecoveryError('solver failure', observation, 0, AssertionError(), stage='recover')

    progress = []
    runner = AutoRngRunner(
        AutoRngConfig(script_dir=tmp_path, reidentify_seed_max_attempts=2),
        services=AutoRngServices(capture_seed=fail), progress_callback=progress.append,
    )
    runner._recover_seed_after_reidentify_failure(RuntimeError('calibration failed'), 2)
    retried = next(p.log_message for p in progress if getattr(p.log_message, 'event_type', '') == 'seed.retry')
    assert '测种求解输入' in retried.details
    assert 'blinks' in retried.details and 'blinks' not in retried.summary
    assert '测种求解输入' in runner.progress.log_message.details
