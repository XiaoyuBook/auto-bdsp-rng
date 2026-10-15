"""Cancellation through production adapters and manual/automatic services."""
from __future__ import annotations

import sys
import threading
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest
from PySide6.QtTest import QTest

from auto_bdsp_rng.automation.auto_rng import AutoRngConfig, AutoRngSeedResult
from auto_bdsp_rng.automation.auto_rng.runner import AutoRngRunner
from auto_bdsp_rng.blink_detection import BlinkObservation
from auto_bdsp_rng.blink_detection import project_xs
from auto_bdsp_rng.rng_core import SeedState32
from auto_bdsp_rng.rng_core.cancellation import SearchCancellation, SearchCancelled
from auto_bdsp_rng.ui import main_window as mw
from tests.test_start_readiness import window
from tests.ui_wait import wait_until


STATE = SeedState32(1, 2, 3, 4)
OBSERVATION = BlinkObservation.from_sequences([], [0, 1, 1, 1, 1, 1, 1])


def _native_name(noisy):
    return "_try_native_reidentify_by_intervals_noisy" if noisy else "_try_native_reidentify_by_intervals"


def _public(noisy):
    return project_xs.reidentify_seed_from_observation_noisy if noisy else project_xs.reidentify_seed_from_observation


@pytest.mark.parametrize("noisy", [False, True])
@pytest.mark.parametrize("backend", ["native", "python", "legacy_extension"])
def test_public_reidentify_adapter_stops_actual_native_or_vendor_search(window, monkeypatch, noisy, backend):
    entered = threading.Event()
    if backend == "native":
        pytest.importorskip("auto_bdsp_rng.rng_core._native")
        original = getattr(project_xs, _native_name(noisy))

        def native(*args, **kwargs):
            entered.set()
            return original(*args, **kwargs)

        monkeypatch.setattr(project_xs, _native_name(noisy), native)
    else:
        # The original vendor algorithms run unchanged; the trace must cover
        # RNG generation as well as their subsequent matching loops.
        vendor = project_xs._load_module("rngtool")
        name = "reidentiy_by_intervals_noisy" if noisy else "reidentiy_by_intervals"
        original_vendor = getattr(vendor, name)

        def python(*args, **kwargs):
            entered.set()
            return original_vendor(*args, **kwargs)

        monkeypatch.setattr(vendor, name, python)
        if backend == "python":
            monkeypatch.setattr(project_xs, _native_name(noisy), lambda *args, **kwargs: None)
        else:
            original_import = project_xs.importlib.import_module

            def old_native(*args):
                raise AssertionError("legacy native must not be invoked without cancellation")

            legacy = SimpleNamespace(reidentify_by_intervals=old_native, reidentify_by_intervals_noisy=old_native)
            monkeypatch.setattr(project_xs.importlib, "import_module", lambda name, *args, **kwargs:
                                legacy if name == "auto_bdsp_rng.rng_core._native" else original_import(name, *args, **kwargs))
    token = SearchCancellation()
    results, errors, finished = [], [], []
    previous_trace = []

    def work():
        previous_trace.append(sys.gettrace())
        try:
            results.append(_public(noisy)(STATE, OBSERVATION, search_max=20_000_000, cancellation=token))
        except Exception as error:
            errors.append(error)
        finally:
            assert sys.gettrace() is previous_trace[0]
            finished.append(time.perf_counter())

    thread = threading.Thread(target=work)
    thread.start()
    assert entered.wait(3)
    QTest.qWait(10)
    started = time.perf_counter()
    token.cancel()
    wait_until(lambda: not thread.is_alive())
    elapsed = finished[0] - started
    print(f"public {'noisy' if noisy else 'regular'} {backend} cancel={elapsed:.4f}s", flush=True)
    assert elapsed < 0.3
    assert not results and len(errors) == 1 and isinstance(errors[0], SearchCancelled)


@pytest.mark.parametrize("noisy", [False, True])
def test_manual_button_stop_cancels_adapter_and_can_restart_without_losing_seed_or_frame(window, monkeypatch, noisy):
    w = window
    for box, text in zip(w.seed32_inputs, STATE.format_words()):
        box.setText(text)
    w._tracked_advances = 321
    w.advances_value.setText("321")
    w.reidentify_1_pk_npc.setChecked(noisy)
    monkeypatch.setattr(w, "_ensure_preview_frame_before_capture", lambda **kwargs: True)
    monkeypatch.setattr(w, "_pause_preview_for_capture", lambda: None)
    monkeypatch.setattr(w, "_restore_preview_after_capture", lambda: None)
    monkeypatch.setattr(mw, "capture_player_blinks", lambda *args, **kwargs: OBSERVATION)
    gate, entered = threading.Event(), threading.Event()
    tokens, errors, completions = [], [], []
    monkeypatch.setattr(w, "_show_error", lambda *args: errors.append(args))
    w.manualCaptureFinished.connect(lambda *args: completions.append(args))

    def native(state, observation, *, cancellation, **kwargs):
        tokens.append(cancellation)
        if len(tokens) == 1:
            entered.set()
            assert gate.wait(3)
            cancellation.check()
        return state, 999

    monkeypatch.setattr(project_xs, _native_name(noisy), native)
    history_before = tuple(w.history_tab._all_plain_lines)
    try:
        w.reidentify_seed()
        assert entered.wait(1)
        w.capture_button.click()  # The actual manual stop entry.
        assert tokens[0].is_cancelled()
        gate.set()
        wait_until(lambda: not w._is_capturing())
        w._poll_capture_thread()
        assert not errors and w._capture_result is None
        assert completions and not completions[-1][2]
        assert [box.text() for box in w.seed32_inputs] == list(STATE.format_words())
        assert w._tracked_advances == 321 and w.advances_value.text() == "321"
        assert tuple(w.history_tab._all_plain_lines) == history_before
        w.reidentify_seed()
        wait_until(lambda: not w._is_capturing())
        w._poll_capture_thread()
        assert len(tokens) == 2 and tokens[1] is not tokens[0] and not tokens[1].is_cancelled()
        assert w._tracked_advances == 999 and completions[-1][2]
        assert not errors
    finally:
        gate.set()


@pytest.mark.parametrize("flow", ["regular", "noisy", "exit"])
def test_auto_service_stop_does_not_retry_capture_publish_or_reuse_cancelled_token(window, monkeypatch, tmp_path, flow):
    w = window
    original_config = mw.load_project_xs_config
    monkeypatch.setattr(mw, "load_project_xs_config", lambda *args, **kwargs:
                        replace(original_config(*args, **kwargs), reidentify_1_pk_npc=flow == "noisy"))
    monkeypatch.setattr(w, "_call_on_ui_thread", lambda action: action())
    monkeypatch.setattr(w.video_overlay, "begin_capture", lambda **kwargs: None)
    monkeypatch.setattr(w, "_pause_auto_preview_for_capture", lambda: False)
    restored, captures = [], []
    monkeypatch.setattr(w, "_restore_auto_preview_after_capture", restored.append)

    def capture(*args, **kwargs):
        captures.append(True)
        return OBSERVATION

    monkeypatch.setattr(mw, "capture_player_blinks", capture)
    config = AutoRngConfig(script_dir=tmp_path, max_advances=20_000_000, debug_output=True)
    services = w._build_auto_rng_services(config)
    initial = AutoRngSeedResult(seed=STATE, current_advances=321, expected_advances_hint=400)
    gate, entered = threading.Event(), threading.Event()
    tokens, results, errors, published, logs = [], [], [], [], []
    w.autoSeedCaptured.disconnect(w._handle_auto_seed_captured)
    w.autoSeedCaptured.connect(published.append)
    w.auto_rng_tab.captureLog.connect(logs.append)

    def native(state, observation, *, cancellation, **kwargs):
        tokens.append(cancellation)
        if len(tokens) == 1:
            entered.set()
            assert gate.wait(3)
            cancellation.check()
        return state, 999

    monkeypatch.setattr(project_xs, _native_name(flow != "regular"), native)

    def invoke(target):
        return (target.reidentify_exit if flow == "exit" else target.reidentify)(initial)

    def work():
        try:
            results.append(invoke(services))
        except Exception as error:
            errors.append(error)

    thread = threading.Thread(target=work)
    history_before = tuple(w.history_tab._all_plain_lines)
    try:
        thread.start()
        assert entered.wait(1)
        AutoRngRunner(config, services=services).stop()
        assert tokens[0].is_cancelled()
        gate.set()
        wait_until(lambda: not thread.is_alive())
        assert not results and len(errors) == 1 and isinstance(errors[0], SearchCancelled)
        assert len(tokens) == len(captures) == 1 and restored == [False]
        assert not published and not any("重试" in text or "成功" in text for text in logs)
        assert tuple(w.history_tab._all_plain_lines) == history_before
        with pytest.raises(SearchCancelled):
            invoke(services)
        assert len(captures) == 1  # Cannot clear stop and silently recapture.
        restarted = w._build_auto_rng_services(config)
        result = invoke(restarted)
        assert result.seed == initial.seed and result.current_advances == 999
        assert tokens[-1] is not tokens[0] and not tokens[-1].is_cancelled()
        assert published == [result] and len(captures) == 2
    finally:
        gate.set()
