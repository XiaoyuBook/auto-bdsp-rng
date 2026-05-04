from __future__ import annotations

import sys
import types

import pytest

from auto_bdsp_rng.blink_detection import BlinkObservation, SeedState32, recover_seed_from_observation
from auto_bdsp_rng.blink_detection.project_xs import ProjectXsIntegrationError


class FakeRng:
    def get_state(self):
        return [0x12345678, 0x9ABCDEF0, 0x11111111, 0x22222222]


def test_seed_state_formats_words_and_seed64_pair():
    state = SeedState32(0x12345678, 0x9ABCDEF0, 0x11111111, 0x22222222)

    assert state.format_words() == ("12345678", "9ABCDEF0", "11111111", "22222222")
    assert state.format_seed64_pair() == ("123456789ABCDEF0", "1111111122222222")


def test_recover_seed_from_observation_uses_project_xs_rngtool(monkeypatch):
    fake_rngtool = types.SimpleNamespace(
        recov=lambda blinks, intervals, npc=0: FakeRng(),
    )
    monkeypatch.setitem(sys.modules, "rngtool", fake_rngtool)
    observation = BlinkObservation.from_sequences([0, 1, 0], [0, 12, 24])

    result = recover_seed_from_observation(observation, npc=0)

    assert result.state.format_words() == ("12345678", "9ABCDEF0", "11111111", "22222222")
    assert result.observation == observation


def test_recover_seed_from_observation_wraps_project_xs_failures(monkeypatch):
    def fail_recov(_blinks, _intervals, npc=0):
        raise AssertionError("bad seed")

    monkeypatch.setitem(sys.modules, "rngtool", types.SimpleNamespace(recov=fail_recov))
    observation = BlinkObservation.from_sequences([0], [0])

    with pytest.raises(ProjectXsIntegrationError):
        recover_seed_from_observation(observation)
