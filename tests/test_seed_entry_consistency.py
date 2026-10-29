"""Compare real solver calls across stopped automation, config tests and new runs.

Device observations are generated, and failures use a deliberately altered blink.
These tests verify entry consistency, not the cause of a user's device failure.
"""
from __future__ import annotations

from dataclasses import replace
import json
import time

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

from auto_bdsp_rng.automation.auto_rng.models import AutoRngConfig, AutoRngPhase
from auto_bdsp_rng.blink_detection import load_project_xs_config, save_project_xs_config
from auto_bdsp_rng.blink_detection.models import BlinkObservation
from auto_bdsp_rng.blink_detection.project_xs import ProjectXsSeedRecoveryError
from auto_bdsp_rng.rng_core import SeedState32
from auto_bdsp_rng.rng_core.generators import BDSPXorshift
from auto_bdsp_rng.ui import main_window as mw
from tests.test_seed_config_editor import configs, open_editor
from tests.test_start_readiness import window


@pytest.fixture(autouse=True)
def disable_update_checks(monkeypatch):
    monkeypatch.setenv("AUTO_BDSP_RNG_DISABLE_UPDATES", "1")


def wait_for_solver(predicate):
    # Let NumPy's worker reacquire the GIL between Qt event deliveries.
    deadline = time.monotonic() + 30
    while not predicate() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.005)
    assert predicate(), "Seed solver did not finish before timeout"


def observe(rng, npc):
    blinks, intervals, previous, frame = [], [], 0, 0
    while len(blinks) < 40:
        frame += 1
        before = SeedState32(*rng.words)
        value = rng.next()
        for _ in range(npc):
            rng.next()
        if value & 14 == 0:
            blinks.append(value & 1)
            intervals.append(frame - previous)
            previous = frame
    return BlinkObservation.from_sequences(blinks, intervals, offset_time=100.0), before


@pytest.mark.parametrize("npc", [0, 1])
def test_real_solver_with_different_valid_seeds_and_large_advance_recovers_after_bad_input(npc, capsys):
    rng = BDSPXorshift(SeedState32(0x12345678, 0x9ABCDEF0, 0x11111111, 0x22222222))
    for index in range(8):
        if index == 6:
            rng.jump(104_523_047)
        observation, expected = observe(rng, npc)
        if index == 6:
            blinks = list(observation.blinks)
            blinks[10] ^= 1
            with pytest.raises(ProjectXsSeedRecoveryError):
                mw.recover_seed_from_observation(replace(observation, blinks=tuple(blinks)), npc=npc)
        assert mw.recover_seed_from_observation(observation, npc=npc).state == expected
    capsys.readouterr()


@pytest.mark.parametrize("npc", [0, 1])
def test_protection_stop_then_configuration_test_and_both_automatic_starts_use_same_solver(
    window, configs, monkeypatch, tmp_path, npc,
):
    w = window
    path, _other, _template = configs
    saved = replace(load_project_xs_config(path), npc=npc)
    save_project_xs_config(saved, path)
    rng = BDSPXorshift(SeedState32(0x12345678, 0x9ABCDEF0, 0x11111111, 0x22222222))
    observation, expected = observe(rng, npc)
    altered = list(observation.blinks)
    altered[10] ^= 1
    invalid = replace(observation, blinks=tuple(altered))
    sample = [invalid]
    captures, scripts, calls = [], [], []

    def capture(config, **kwargs):
        captures.append((config, kwargs.get("discard_first_blink_within_seconds")))
        kwargs["progress_callback"](40, 40)
        return sample[0]

    real_recover = mw.recover_seed_from_observation

    def recover(actual, *, npc):
        calls.append((actual, npc))
        return real_recover(actual, npc=npc)

    real_build = w._build_auto_rng_services

    def build(config):
        return replace(
            real_build(config),
            run_script_text=lambda _text, name: scripts.append(name),
            search_candidates=lambda _seed: [], search_sync=None,
        )

    monkeypatch.setattr(mw, "capture_player_blinks", capture)
    monkeypatch.setattr(mw, "recover_seed_from_observation", recover)
    monkeypatch.setattr(mw.time, "perf_counter", lambda: 100.0)
    monkeypatch.setattr(w, "_build_auto_rng_services", build)
    monkeypatch.setattr(w, "_ensure_preview_for_auto_rng", lambda: True)
    monkeypatch.setattr(w.easycon_tab, "_native_is_connected", lambda: True)
    monkeypatch.setattr(w, "_recover_zoom_mode_with_preview_paused", lambda *_: False)
    monkeypatch.setattr(w, "_sync_bdsp_data_from_auto_rng", lambda *_: None)
    monkeypatch.setattr(w, "_notify_qq_task", lambda *_: None)
    seed_script = tmp_path / "seed.txt"
    seed_script.write_text("A 100\n", encoding="utf-8")
    config = AutoRngConfig(
        script_dir=tmp_path, seed_script_path=seed_script, loop_mode="count", loop_count=10,
    )

    # Real Runner, Worker/QThread, services, solver and UI failure path.
    # Only the camera input and controller/search/notification boundaries are replaced.
    w._start_auto_rng_after_warmup(config)
    failed_run_id = w._active_auto_rng_run_id
    wait_for_solver(lambda: w.auto_rng_tab._runner_thread is None)
    assert len(captures) == 5 and len(scripts) == 5
    failures = [entry for entry in w._run_log_buffer.snapshot()
                if entry.run_id == failed_run_id and entry.event_type == "task.failed"]
    assert failures and "已连续 5 次测种失败" in failures[-1].user_message

    # The config editor loads the saved task file without editing its parameters.
    sample[0] = observation
    _panel, _binding, editor = open_editor(w, "seed", path)
    before = path.read_bytes()
    w._latest_preview_source = w._synchronize_preview_source(w._config_from_form().capture)
    w._latest_preview_frame = np.zeros((40, 40, 3), dtype=np.uint8)
    editor.test_button.click()
    wait_for_solver(lambda: w._capture_thread is None)
    assert w._capture_error is None and w._capture_result.state == expected
    assert "成功" in editor.status.text() and path.read_bytes() == before
    editor.cancel()

    # Cover both menu entries because the reported successful click is uncertain.
    for phase in (AutoRngPhase.RUN_SEED_SCRIPT, AutoRngPhase.CAPTURE_SEED):
        w._start_auto_rng_after_warmup(replace(config, start_phase=phase, loop_mode="single"))
        wait_for_solver(lambda: w.auto_rng_tab._runner_thread is None)
        assert w._state32_from_auto_seed_result(w._current_auto_rng_seed_result()) == expected
    assert len(calls) == 8 and all(actual_npc == npc for _sample, actual_npc in calls)
    assert len(scripts) == 6  # Five failed rounds plus the fresh script-start run.
    assert captures[5][1] is None and captures[6][1] == captures[7][1] == 1.0
    assert all(capture_config == captures[0][0] for capture_config, _warmup in captures)

    entries = [entry for entry in w._run_log_buffer.snapshot()
               if entry.event_type in {"seed.solver_input", "seed.solver_result"}]
    inputs = [json.loads(entry.message.split("：", 1)[1]) for entry in entries
              if entry.event_type == "seed.solver_input"]
    outputs = [json.loads(entry.message.split("：", 1)[1]) for entry in entries
               if entry.event_type == "seed.solver_result"]
    assert len(inputs) == len(outputs) == 8
    assert [row["measurement_id"] for row in inputs] == [row["measurement_id"] for row in outputs]
    assert [row["outcome"] for row in outputs] == ["failed"] * 5 + ["succeeded"] * 3
    assert inputs[5]["entry_point"] == "configuration_test"
    assert inputs[6]["start_phase"] == AutoRngPhase.RUN_SEED_SCRIPT.value
    assert inputs[7]["start_phase"] == AutoRngPhase.CAPTURE_SEED.value
    for key in ("npc", "seed_config_path", "eye_image_path", "roi", "threshold", "crop", "capture_source"):
        assert all(row[key] == inputs[0][key] for row in inputs)
    assert all(entry.level == "DEBUG" and not entry.user_visible for entry in entries)
    assert any(entry.source == "Seed 捕捉" for entry in entries)
    assert all(entry.details for entry in entries
               if entry.event_type == "seed.solver_result" and '"failed"' in entry.message)
    assert "measurement_id" not in w.live_log_panel.text.toPlainText()


def test_diagnostics_distinguish_solver_failure_from_elapsed_advance_failure(window, monkeypatch):
    w = window
    config = w._config_from_form()
    observation = BlinkObservation.from_sequences([0, 1], [0, 12], offset_time=100)
    state = SeedState32(1, 2, 3, 4)
    monkeypatch.setattr(mw, "recover_seed_from_observation", lambda *_a, **_kw:
                        mw.ProjectXsSeedResult(state=state, observation=observation))
    monkeypatch.setattr(mw.time, "perf_counter", lambda: 102.0)

    def fail_advance(*_args):
        raise RuntimeError("injected elapsed advance failure")

    monkeypatch.setattr(mw, "advance_seed_state", fail_advance)
    with pytest.raises(RuntimeError, match="injected elapsed"):
        w._recover_player_seed(observation, config, entry_point="manual")
    result = next(entry for entry in w._run_log_buffer.snapshot() if entry.event_type == "seed.solver_result")
    payload = json.loads(result.message.split("：", 1)[1])
    assert payload["stage"] == "elapsed_advance" and payload["outcome"] == "failed"
    assert "injected elapsed advance failure" in result.details and not result.user_visible
