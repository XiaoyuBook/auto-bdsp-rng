from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

from auto_bdsp_rng.blink_detection.models import BlinkObservation
from auto_bdsp_rng.blink_detection.project_xs import (
    ProjectXsSeedRecoveryError, _load_module, recover_seed_from_observation,
)
from auto_bdsp_rng.log_events import exception_details, task_failure, log_user_text


@pytest.mark.parametrize('error_type', [AssertionError, RuntimeError])
def test_solver_failure_keeps_exact_replay_inputs_without_changing_user_copy(monkeypatch, error_type):
    received = []

    def fail_recov(blinks, intervals, npc=0):
        received.append((blinks, intervals, npc))
        raise error_type('private solver failure')

    monkeypatch.setitem(sys.modules, 'rngtool', SimpleNamespace(recov=fail_recov))
    observation = BlinkObservation.from_sequences([0, 1, 0], [999, 12, 24])
    with pytest.raises(ProjectXsSeedRecoveryError) as caught:
        recover_seed_from_observation(observation, npc=2)
    assert received == [([0, 1, 0], [999, 12, 24], 2)]
    error = caught.value
    payload = json.loads(error.diagnostic_details.split('\n', 1)[1])
    assert payload['blinks'] == [0, 1, 0] and payload['intervals'] == [999, 12, 24]
    assert payload['npc'] == 2 and payload['stage'] == 'recover'
    assert payload['failure_function'] == 'fail_recov' and payload['failure_type'] == error_type.__name__
    event = task_failure(error, task='自动定点', phase='捕获Seed')
    assert log_user_text(event) == '测种失败，流程已停止'
    assert '测种求解输入' in exception_details(error) and 'blinks' in event.details
    assert observation.blinks == (0, 1, 0) and observation.intervals == (999, 12, 24)


def test_actual_solver_recovers_after_a_failed_sample_without_restarting_the_process(capsys):
    xorshift = _load_module('xorshift')
    rng = xorshift.Xorshift(0x12345678, 0x9ABCDEF0, 0x11111111, 0x22222222)
    blinks, intervals, previous, frame = [], [], 0, 0
    while len(blinks) < 40:
        frame += 1
        value = rng.next()
        if value & 14 == 0:
            blinks.append(value & 1)
            intervals.append(frame - previous)
            previous = frame
    observation = BlinkObservation.from_sequences(blinks, intervals)
    recovered = [recover_seed_from_observation(observation).state for _ in range(6)]
    assert all(state == recovered[0] for state in recovered)
    invalid = list(blinks)
    invalid[10] ^= 1
    with pytest.raises(ProjectXsSeedRecoveryError):
        recover_seed_from_observation(BlinkObservation.from_sequences(invalid, intervals))
    assert recover_seed_from_observation(observation).state == recovered[0]
    assert observation.blinks == tuple(blinks)
    capsys.readouterr()


def test_invalid_returned_seed_also_keeps_the_original_input(monkeypatch):
    monkeypatch.setitem(sys.modules, 'rngtool', SimpleNamespace(
        recov=lambda *_args, **_kwargs: SimpleNamespace(get_state=lambda: [1, 2, 3]),
    ))
    observation = BlinkObservation.from_sequences([0, 1], [100, 12])
    with pytest.raises(ProjectXsSeedRecoveryError) as caught:
        recover_seed_from_observation(observation)
    payload = json.loads(caught.value.diagnostic_details.split('\n', 1)[1])
    assert payload['stage'] == 'result_state'
    assert payload['intervals'] == [100, 12]
