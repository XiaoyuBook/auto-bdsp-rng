from __future__ import annotations

import threading
import time
from dataclasses import replace
from pathlib import Path
from types import FunctionType, MethodType, SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("PySide6")

from auto_bdsp_rng.automation.auto_rng.models import AutoRngConfig, AutoRngPhase, AutoRngProgress, AutoRngSeedResult
from auto_bdsp_rng.automation.auto_tid_rng import AutoTidRngConfig
from auto_bdsp_rng.blink_detection.models import BlinkCaptureConfig
from auto_bdsp_rng.gen8_static.models import State8
from auto_bdsp_rng.rng_core.seed import SeedPair64
from auto_bdsp_rng.rng_core.cancellation import SearchCancelled
from auto_bdsp_rng.ui import MainWindow
from auto_bdsp_rng.ui.video_lifecycle import PreviewFrameThread
import auto_bdsp_rng.ui.main_window as mw
from tests.test_history_producer_backpressure import _runner_from_actual_start
from tests.test_ui import _install_connected_native_backend
from tests.test_video_lifecycle_performance import app
from tests.ui_wait import wait_until


def test_clear_history_during_actual_search_preserves_locked_target(app, monkeypatch, tmp_path):
    window = MainWindow()
    runner = _runner_from_actual_start(window, monkeypatch, tmp_path)
    candidate = State8(1204, 0, 0, 1, (31,) * 6, 0, 0, 50, 0, 0, 0, 0)
    runner.services = replace(runner.services, search_candidates=lambda _seed: [candidate])
    runner._seed_result = AutoRngSeedResult(seed=SeedPair64(1, 2))
    runner.progress = AutoRngProgress(phase=AutoRngPhase.SEARCH_TARGET)
    entered, release = threading.Event(), threading.Event()
    errors = []
    prepare = window.history_tab._archive._prepare_candidates

    def pause_preparation(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return prepare(*args, **kwargs)

    monkeypatch.setattr(window.history_tab._archive, "_prepare_candidates", pause_preparation)

    def search():
        try:
            runner.run(max_steps=1)
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=search)
    worker.start()
    try:
        assert entered.wait(3)
        window.history_tab.clear()
    finally:
        release.set()
        worker.join(3)
    assert not worker.is_alive() and not errors
    assert not runner.should_stop()
    assert runner.progress.phase == AutoRngPhase.DECIDE_ADVANCE
    assert runner.progress.raw_target_advances == 1204
    assert not window.history_tab._archive._reservations


def test_delayed_script_preparation_keeps_its_caller_and_releases_backend(app, monkeypatch):
    window = MainWindow()
    executed, errors = [], []
    backend = SimpleNamespace(run_script_text=lambda: executed.append(True) or "ok")
    _install_connected_native_backend(window, monkeypatch, backend)

    class FastTimeoutEvent:
        """Compress the former 30 second deadline while preserving an unbounded wait."""

        def __init__(self):
            self.event = threading.Event()

        def wait(self, timeout=None):
            return self.event.wait(None if timeout is None else .01)

        def set(self):
            self.event.set()

    original = MainWindow._call_on_ui_thread
    namespace = dict(original.__globals__, threading=SimpleNamespace(Event=FastTimeoutEvent))
    method = FunctionType(original.__code__, namespace)
    monkeypatch.setattr(window, "_call_on_ui_thread", MethodType(method, window))

    def run_script():
        try:
            native = window._call_on_ui_thread(lambda: window._prepare_auto_easycon_script("seed.txt"))
            result = native.run_script_text()
            window._finalize_auto_script_result(result, "seed.txt")
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=run_script)
    worker.start()
    deadline = time.monotonic() + 2
    while not window._pending_ui_calls and time.monotonic() < deadline:
        time.sleep(.001)
    assert window._pending_ui_calls
    # Keep Qt paused beyond the simulated former timeout, then deliver the
    # preparation and finalizer. A detached caller would leave the slot held.
    time.sleep(.04)
    wait_until(lambda: not worker.is_alive())
    worker.join(0)
    assert not errors and executed == [True]
    assert not window.easycon_tab._native_run_reserved
    assert not window._pending_ui_calls


@pytest.mark.parametrize("workflow", ["static", "tid"])
@pytest.mark.parametrize("stop_at", ["queued", "prepared"])
def test_stopped_run_cannot_start_its_delayed_script_preparation(app, monkeypatch, tmp_path, workflow, stop_at):
    window = MainWindow()
    backend = SimpleNamespace(run_script_text=lambda *_a, **_kw: pytest.fail("Stopped script must not execute"))
    _install_connected_native_backend(window, monkeypatch, backend)
    if workflow == "static":
        services = window._build_auto_rng_services(AutoRngConfig(script_dir=tmp_path, max_advances=1))
        stop = services.cancel_run
    else:
        services = window._build_auto_tid_rng_services(AutoTidRngConfig(script_dir=tmp_path))
        stop = services.stop_current_script
    errors, started = [], []
    window.autoScriptStarted.connect(started.append)
    if stop_at == "prepared":
        window.autoScriptStarted.connect(lambda _name: stop())

    def run_script():
        try:
            services.run_script_text("A 100", "seed.txt")
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=run_script)
    worker.start()
    deadline = time.monotonic() + 2
    while not window._pending_ui_calls and time.monotonic() < deadline:
        time.sleep(.001)
    assert window._pending_ui_calls
    if stop_at == "queued":
        stop()
    wait_until(lambda: not worker.is_alive())
    worker.join(0)
    assert len(errors) == 1 and isinstance(errors[0], SearchCancelled)
    assert started == ([] if stop_at == "queued" else ["seed.txt"])
    assert not window.easycon_tab._native_run_reserved


@pytest.mark.parametrize("change_source", [False, True])
def test_ocr_reads_after_inflight_preview_and_rejects_changed_source(app, monkeypatch, change_source):
    window = MainWindow()
    config = BlinkCaptureConfig(Path("eye.png"), (0, 0, 1, 1), monitor_window=True, window_prefix="OBS")
    active_config = [config]
    monkeypatch.setattr(window, "_config_from_form", lambda: SimpleNamespace(capture=active_config[0]))
    old, fresh = np.zeros((4, 4, 3), np.uint8), np.ones((4, 4, 3), np.uint8)
    entered, release = threading.Event(), threading.Event()
    read_threads, results, errors = [], [], []

    def read(_config):
        read_threads.append(threading.get_ident())
        if len(read_threads) == 1:
            entered.set()
            assert release.wait(3)
            return old
        return fresh

    monkeypatch.setattr(window, "_read_live_preview_frame", read)
    monkeypatch.setattr(mw, "render_eye_preview", lambda _c, raw: (raw, None))
    monkeypatch.setattr(mw, "capture_preview_frame", lambda _c: pytest.fail("Must reuse the active capture owner"))
    window._latest_preview_frame = old
    window._preview_timer.setInterval(100_000)
    window._preview_timer.start()
    window._update_preview_frame()
    assert entered.wait(3)

    def recognize():
        try:
            results.append(window._capture_preview_frame_for_config(config))
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=recognize)
    worker.start()
    try:
        wait_until(lambda: bool(window._preview_thread._frame_requests))
        assert not results
        if change_source:
            active_config[0] = replace(config, window_prefix="different OBS")
            window._synchronize_preview_source(active_config[0])
    finally:
        release.set()
    wait_until(lambda: not worker.is_alive())
    worker.join(0)
    if change_source:
        assert not results and len(errors) == 1 and "视频源已改变" in str(errors[0])
    else:
        assert not errors and len(results) == 1 and results[0] is fresh
        # No display timer has polled the preview worker during this request.
        assert window._latest_preview_frame is old
    assert len(read_threads) == (1 if change_source else 2)
    assert len(set(read_threads)) == 1 and threading.get_ident() not in read_threads


def test_preview_stop_rejects_queued_fresh_reads_and_releases_owner(app):
    window = MainWindow()
    entered, release = threading.Event(), threading.Event()
    fresh_reads, released = [], []

    def preview(_request):
        entered.set()
        assert release.wait(3)
        return "old-frame"

    owner = PreviewFrameThread(preview, lambda: released.append(True), window)
    owner.submit("preview")
    owner.start()
    try:
        assert entered.wait(3)
        future = owner.request_frame(lambda: fresh_reads.append(True) or "fresh-frame")
        owner.request_stop()
        with pytest.raises(RuntimeError, match="预览已停止"):
            future.result(timeout=.2)
    finally:
        release.set()
        assert owner.wait(3000)
    assert not fresh_reads and released == [True]
