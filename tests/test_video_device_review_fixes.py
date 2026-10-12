from __future__ import annotations

import threading
import time
from concurrent.futures import Future
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from auto_bdsp_rng.automation.auto_rng import AutoRngConfig
from auto_bdsp_rng.automation.auto_tid_rng import AutoTidRngConfig
from auto_bdsp_rng.automation.easycon import EasyConStatus
from auto_bdsp_rng.automation.easycon.native.device import MemoryTransport, NintendoSwitchDevice
from auto_bdsp_rng.automation.easycon.native_backend import NativeEasyConBackend
from auto_bdsp_rng.ui import MainWindow
from auto_bdsp_rng.ui import main_window as mw
from auto_bdsp_rng.ui import result_transfer as rt
from tests.test_easycon_panel import FakeNativeBackend
from tests.test_ui import isolated_ui_qsettings
from tests.test_video_lifecycle_performance import app, heartbeat, start_gui_preview_action
from tests.ui_wait import wait_until


def settle_startup(window):
    QApplication.processEvents()
    wait_until(lambda: window._capture_device_enumeration_thread is None)


def configure_automatic_buttons(window, monkeypatch, tmp_path):
    window._ocr_warmup_result = (True, "ready")
    rng = AutoRngConfig(script_dir=tmp_path)
    tid = AutoTidRngConfig(script_dir=tmp_path, target_display_tids=(123456,))
    panel = window.auto_rng_tab
    monkeypatch.setattr(panel, "build_config", lambda **kwargs: replace(rng, **kwargs))
    monkeypatch.setattr(panel, "_validate_manual_scripts", lambda _config: None)
    monkeypatch.setattr(window.auto_tid_rng_tab, "build_config", lambda **kwargs: replace(tid, **kwargs))
    monkeypatch.setattr(window.auto_tid_rng_tab, "_validate_config", lambda _config: None)
    window.auto_tid_rng_tab.add_target_display_tid(123456)
    starts = {"rng": [], "tid": []}
    monkeypatch.setattr(panel, "run_with_runner", starts["rng"].append)
    monkeypatch.setattr(window.auto_tid_rng_tab, "run_with_runner", starts["tid"].append)
    window._refresh_automation_start_state()
    return rng, tid, starts


def connected_controller(window):
    backend = FakeNativeBackend()
    backend.connected_port = "COM7"
    window.easycon_tab.native_backend = backend
    window.easycon_tab.port_combo.addItem("COM7")
    window.easycon_tab.port_combo.setCurrentText("COM7")
    return backend


@pytest.mark.parametrize("asynchronous", [False, True])
def test_scale_restart_launches_once_at_real_close_acceptance(app, monkeypatch, asynchronous):
    process = SimpleNamespace(stop=lambda: time.sleep(.12) or True) if asynchronous else None
    window = MainWindow(capture_broker_process=process)
    window._video_source_connected = asynchronous
    window.show()
    settle_startup(window)
    launches, quits = [], []
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_kw: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(mw, "_start_detached_gui_process", lambda: launches.append(window._close_accepted) or True)
    monkeypatch.setattr(QApplication, "quit", lambda: quits.append(True))
    window._request_ui_scale_restart(70)
    if asynchronous:
        assert window.isVisible() and not launches
        assert window._ui_scale_restart_pending
    wait_until(lambda: window._close_accepted)
    assert launches == [True] and quits == [True]
    assert not window._ui_scale_restart_pending
    assert window.close()
    app.processEvents()
    assert launches == [True]


@pytest.mark.parametrize("rejection", ["confirmation", "cleanup"])
def test_rejected_restart_does_not_restart_a_later_ordinary_close(app, monkeypatch, rejection):
    process = SimpleNamespace(allow_stop=False)
    process.stop = lambda: process.allow_stop
    window = MainWindow(capture_broker_process=process if rejection == "cleanup" else None)
    window._video_source_connected = rejection == "cleanup"
    settle_startup(window)
    launches = []
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_kw: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(mw, "_start_detached_gui_process", lambda: launches.append(True) or True)
    monkeypatch.setattr(window, "_confirm_unsaved_easycon_script", lambda: rejection != "confirmation")
    window._request_ui_scale_restart(70)
    wait_until(lambda: not window._ui_scale_restart_pending)
    assert not window._close_accepted and not launches
    monkeypatch.setattr(window, "_confirm_unsaved_easycon_script", lambda: True)
    process.allow_stop = True
    window.close()
    wait_until(lambda: window._close_accepted)
    assert launches == []


def test_device_refresh_is_serial_coalesced_and_keeps_qt_alive(app, monkeypatch):
    window = MainWindow()
    window.show()
    settle_startup(window)
    gate, entered = threading.Event(), threading.Event()
    calls = []
    gui_thread = threading.get_ident()

    def enumerate_devices(api):
        calls.append((api, threading.get_ident()))
        if len(calls) == 1:
            entered.set()
            assert gate.wait(3)
            return [(0, "obsolete")]
        time.sleep(.25)
        return [(1400, "latest USB"), (701, "latest OBS")]

    monkeypatch.setattr(mw, "_enumerate_capture_devices", enumerate_devices)
    timer, ticks = heartbeat(window)
    before = time.perf_counter()
    window.capture_device_refresh_button.click()
    click_ms = (time.perf_counter() - before) * 1000
    try:
        assert click_ms < 150
        wait_until(entered.is_set)
        assert window.capture_device_refresh_button.text() == "…"
        window.capture_api_combo.setCurrentIndex(window.capture_api_combo.findData(700))
        for _ in range(100):
            window.refresh_capture_devices()
        window.capture_api_combo.setCurrentIndex(window.capture_api_combo.findData(0))
        QTest.qWait(60)
        assert len(calls) == 1 and len(ticks) >= 3
        gate.set()
        wait_until(lambda: window._capture_device_enumeration_thread is None)
    finally:
        gate.set()
        timer.stop()
    max_gap_ms = max(b - a for a, b in zip(ticks, ticks[1:])) * 1000
    assert max_gap_ms < 200
    assert len(calls) == 2 and calls[-1][0] == 0
    assert all(ident != gui_thread for _api, ident in calls)
    assert window.capture_device_combo.itemText(0) == "1400 - latest USB"
    assert window.capture_device_refresh_button.isEnabled()
    print(f"device_refresh click={click_ms:.1f}ms heartbeat_max={max_gap_ms:.1f}ms calls={len(calls)}")


def test_close_retains_slow_enumeration_and_rejects_its_late_result(app, monkeypatch):
    window = MainWindow()
    settle_startup(window)
    entered, gate = threading.Event(), threading.Event()

    def enumerate_devices(_api):
        entered.set()
        assert gate.wait(3)
        return [(91, "late")]

    monkeypatch.setattr(mw, "_enumerate_capture_devices", enumerate_devices)
    window.capture_device_refresh_button.click()
    wait_until(entered.is_set)
    owner = window._capture_device_enumeration_thread
    try:
        assert not window.close()
        assert window._capture_device_enumeration_thread is owner and owner.isRunning()
        assert not window._close_accepted
        gate.set()
        wait_until(lambda: window._close_accepted)
        assert window.capture_device_combo.findData(91) < 0
    finally:
        gate.set()


@pytest.mark.parametrize("enumeration_finishes_before_rejection", [False, True])
def test_history_commit_close_rejection_restores_device_refresh(
    app, monkeypatch, tmp_path, enumeration_finishes_before_rejection,
):
    window = MainWindow()
    window.show()
    settle_startup(window)
    commit_entered, commit_gate = threading.Event(), threading.Event()
    enumeration_entered, enumeration_gate = threading.Event(), threading.Event()
    calls = []
    gui_thread = threading.get_ident()
    original_commit = rt.PreparedExport.commit
    destination = tmp_path / "history.csv"
    destination.write_text("previous", encoding="utf-8")

    def commit(staged, cancellation=None):
        if staged.destination == destination:
            commit_entered.set()
            assert commit_gate.wait(5)
        return original_commit(staged, cancellation)

    def enumerate_devices(api):
        calls.append((api, threading.get_ident()))
        if len(calls) == 1:
            enumeration_entered.set()
            assert enumeration_gate.wait(5)
            return [(91, "obsolete")]
        return [(92, "recovered")]

    monkeypatch.setattr(rt.PreparedExport, "commit", commit)
    monkeypatch.setattr(mw, "_enumerate_capture_devices", enumerate_devices)
    history = window.history_tab
    transfer = history._export_transfer
    transfer.prepare_export(
        lambda token: rt.prepare_result_export(
            (("history", "历史") for _ in range(1001)), destination,
            csv_format=True, cancellation=token,
        ),
        1001,
    )
    previous_quit = app.quitOnLastWindowClosed()
    app.setQuitOnLastWindowClosed(False)
    try:
        wait_until(commit_entered.is_set)
        assert transfer.committing
        window.capture_device_refresh_button.click()
        wait_until(enumeration_entered.is_set)
        owner = window._capture_device_enumeration_thread
        assert not window.close()
        assert window._close_pending and not history._archive._accepting
        if enumeration_finishes_before_rejection:
            enumeration_gate.set()
            wait_until(lambda: window._capture_device_enumeration_thread is None)
            assert not window.capture_device_refresh_button.isEnabled()
            assert window.capture_device_combo.findData(91) < 0
        window._close_deadline = time.monotonic() - 1
        window._poll_confirmed_close()
        assert not window._is_closing and not window._close_pending and window.isVisible()
        assert history._archive._accepting and not history._closing_requested
        assert transfer.committing and destination.read_text(encoding="utf-8") == "previous"
        if not enumeration_finishes_before_rejection:
            assert window._capture_device_enumeration_thread is owner and owner.isRunning()
            assert not window.capture_device_refresh_button.isEnabled()
            window.capture_device_refresh_button.click()
            assert len(calls) == 1
            enumeration_gate.set()
            wait_until(lambda: window._capture_device_enumeration_thread is None)
        assert window.capture_device_refresh_button.isEnabled()
        window.capture_device_refresh_button.click()
        assert not window.capture_device_refresh_button.isEnabled()
        wait_until(lambda: window._capture_device_enumeration_thread is None)
        assert len(calls) == 2 and all(ident != gui_thread for _api, ident in calls)
        assert window.capture_device_combo.findData(92) >= 0
        assert window.capture_device_refresh_button.isEnabled()
        commit_gate.set()
        wait_until(lambda: not transfer.committing)
        assert transfer.last_outcome == ("exported", destination)
        assert "历史" in destination.read_text(encoding="utf-8")
        assert not window._close_accepted and history._archive._accepting
    finally:
        commit_gate.set()
        enumeration_gate.set()
        wait_until(lambda: window._capture_device_enumeration_thread is None and not transfer.committing)
        app.setQuitOnLastWindowClosed(previous_quit)


def test_easycon_shutdown_failure_restores_device_refresh_after_enumeration(app, monkeypatch):
    window = MainWindow()
    window.show()
    settle_startup(window)
    close_entered, close_gate = threading.Event(), threading.Event()
    enumeration_entered, enumeration_gate = threading.Event(), threading.Event()
    calls = []
    gui_thread = threading.get_ident()

    class Backend(FakeNativeBackend):
        fail_close = True

        def close(self):
            if self.fail_close:
                close_entered.set()
                assert close_gate.wait(5)
                raise RuntimeError("review close failure")
            super().close()

    def enumerate_devices(api):
        calls.append((api, threading.get_ident()))
        if len(calls) == 1:
            enumeration_entered.set()
            assert enumeration_gate.wait(5)
            return [(91, "obsolete")]
        return [(92, "recovered")]

    backend = Backend()
    backend.connected_port = "COM7"
    window.easycon_tab.native_backend = backend
    monkeypatch.setattr(mw, "_enumerate_capture_devices", enumerate_devices)
    try:
        window.capture_device_refresh_button.click()
        wait_until(enumeration_entered.is_set)
        owner = window._capture_device_enumeration_thread
        assert not window.close()
        assert window._capture_device_enumeration_thread is owner and owner.isRunning()
        enumeration_gate.set()
        wait_until(close_entered.is_set)
        assert window._capture_device_enumeration_thread is None
        assert window._is_closing and window._easycon_close_pending
        assert not window.capture_device_refresh_button.isEnabled()
        assert window.capture_device_combo.findData(91) < 0
        close_gate.set()
        wait_until(lambda: not window._is_closing and not window._easycon_close_pending)
        assert window.isVisible() and not window._close_accepted
        assert window.easycon_tab.native_backend is backend
        assert window.history_tab._archive._accepting
        assert window.capture_device_refresh_button.isEnabled()
        window.capture_device_refresh_button.click()
        assert not window.capture_device_refresh_button.isEnabled()
        wait_until(lambda: window._capture_device_enumeration_thread is None)
        assert len(calls) == 2 and all(ident != gui_thread for _api, ident in calls)
        assert window.capture_device_combo.findData(92) >= 0
        assert window.capture_device_refresh_button.isEnabled()
    finally:
        backend.fail_close = False
        close_gate.set()
        enumeration_gate.set()
        wait_until(lambda: window._capture_device_enumeration_thread is None and not window.easycon_tab._shutdown_in_progress)


@pytest.mark.parametrize("recovery", ["pending", "easycon"])
@pytest.mark.parametrize("lock", ["enumeration", "connected", "connecting", "stopping", "controls"])
def test_close_recovery_keeps_device_refresh_locked(app, monkeypatch, recovery, lock):
    window = MainWindow()
    settle_startup(window)
    entered, gate = threading.Event(), threading.Event()
    calls = []

    def enumerate_devices(api):
        calls.append(api)
        entered.set()
        assert gate.wait(5)
        return [(91, "retained owner")]

    monkeypatch.setattr(mw, "_enumerate_capture_devices", enumerate_devices)
    if lock == "enumeration":
        window.capture_device_refresh_button.click()
        wait_until(entered.is_set)
    elif lock == "connected":
        window._video_source_connected = True
        window._set_video_source_config_enabled(False)
    elif lock == "connecting":
        window._video_source_connecting = True
        window._set_video_source_config_enabled(False)
    elif lock == "stopping":
        window._set_video_source_stop_failure("review retained source")
    else:
        window.capture_api_combo.setEnabled(False)
    owner = window._capture_device_enumeration_thread
    window._is_closing = True
    try:
        if recovery == "pending":
            window._close_pending = True
            window._reject_pending_close("review", "review close rejection")
        else:
            window._easycon_close_pending = True
            window._finish_easycon_close(False)
        assert not window._is_closing
        assert window._capture_device_enumeration_thread is owner
        assert not window.capture_device_refresh_button.isEnabled()
        window.capture_device_refresh_button.click()
        assert len(calls) == int(lock == "enumeration")
        if lock == "enumeration":
            assert owner.isRunning()
            gate.set()
            wait_until(lambda: window._capture_device_enumeration_thread is None)
            assert window.capture_device_refresh_button.isEnabled()
        else:
            assert not window.capture_api_combo.isEnabled()
    finally:
        gate.set()
        window._video_source_connected = False
        window._video_source_connecting = False
        window._video_source_stop_pending = False
        window._video_source_stop_error = None
        window._set_video_source_config_enabled(True)
        wait_until(lambda: window._capture_device_enumeration_thread is None)


@pytest.mark.parametrize("selection", ["numeric", "mock"])
def test_device_refresh_preserves_manual_and_mock_selection(app, monkeypatch, selection):
    window = MainWindow(dev_mock_devices=selection == "mock")
    settle_startup(window)
    choice = "mock_video" if selection == "mock" else 91
    if window.capture_device_combo.findData(choice) < 0:
        window.capture_device_combo.addItem(str(choice), choice)
    window.capture_device_combo.setCurrentIndex(window.capture_device_combo.findData(choice))
    monkeypatch.setattr(mw, "_enumerate_capture_devices", lambda _api: [(0, "USB")])
    window.capture_device_refresh_button.click()
    wait_until(lambda: window._capture_device_enumeration_thread is None)
    assert window.capture_device_combo.currentData() == choice


def test_real_source_form_change_invalidates_frozen_roi_snapshot(app, monkeypatch):
    window = MainWindow()
    settle_startup(window)
    window.monitor_window.setChecked(False)
    window.camera.setText("0")
    window._synchronize_preview_source()
    window._latest_preview_frame = np.full((20, 30, 3), 11, np.uint8)
    window._latest_preview_source = window._preview_source
    window.select_roi_button.click()
    assert window._selection_preview_frame is not None and window.preview_label._selection_enabled
    window.camera.setText("1")
    assert window._latest_preview_frame is None
    assert window._selection_preview_frame is None and window._selection_mode is None
    assert not window.preview_label._selection_enabled


def test_per_call_factory_and_annotation_changes_preserve_shared_preview_source(app, monkeypatch):
    window = MainWindow()
    settle_startup(window)
    window._video_source_connected = True
    base = window._config_from_form()
    frame = np.full((20, 30, 3), 22, np.uint8)
    configs = []

    def config():
        value = replace(base.capture, threshold=.7 + (len(configs) % 2) * .1, frame_source_factory=lambda: object())
        configs.append(value)
        return replace(base, capture=value)

    monkeypatch.setattr(window, "_config_from_form", config)
    monkeypatch.setattr(window, "_read_live_preview_frame", lambda _c: frame)
    monkeypatch.setattr(mw, "render_eye_preview", lambda _c, raw: (raw.copy(), SimpleNamespace(match_score=.8)))
    window._preview_timer.start()
    window._update_preview_frame()
    wait_until(lambda: window._latest_preview_frame is frame)
    window.select_roi_button.click()
    assert np.array_equal(window._selection_preview_frame, frame)
    assert len(configs) >= 3 and configs[0].frame_source_factory is not configs[-1].frame_source_factory


@pytest.mark.parametrize("kind", ["roi", "eye", "ocr_region", "tid_ocr_region", "ocr_overlay", "tid_overlay", "tid_recognition", "first_frame"])
def test_new_source_action_ignores_old_inflight_frame(app, monkeypatch, kind):
    window = MainWindow()
    window.show()
    settle_startup(window)
    base = window._config_from_form()
    source = [replace(base.capture, monitor_window=False, camera=0)]
    gates = [threading.Event(), threading.Event()]
    entered = [threading.Event(), threading.Event()]
    frames = [np.full((120, 160, 3), value, np.uint8) for value in (11, 22)]
    reads, completed = [], []

    class Capture:
        keep_open_for_preview = True

        def __init__(self, config):
            self.config = config

        def read(self):
            camera = self.config.camera
            reads.append(camera)
            entered[camera].set()
            assert gates[camera].wait(3)
            return frames[camera]

        def release(self):
            pass

    monkeypatch.setattr(window, "_config_from_form", lambda: replace(base, capture=source[0]))
    monkeypatch.setattr(mw, "PreviewFrameCapture", Capture)
    monkeypatch.setattr(mw, "render_eye_preview", lambda _c, frame: (frame.copy(), SimpleNamespace(match_score=.8)))
    monkeypatch.setattr(mw, "recognize_ocr_field", lambda frame, *_a: str(int(frame[0, 0, 0])))
    window._preview_timer.start()
    window._update_preview_frame()
    try:
        wait_until(entered[0].is_set)
        source[0] = replace(source[0], camera=1)
        if kind == "first_frame":
            assert not window._ensure_preview_frame_before_capture(on_ready=lambda: completed.append(window._latest_preview_frame))
        elif kind == "tid_recognition":
            window._ensure_tid_ocr_settings()
            window._tid_ocr_dialog.set_region((1, 2, 30, 40))
            window._tid_ocr_dialog.recognize_button.click()
        else:
            start_gui_preview_action(window, kind)
        gates[0].set()
        wait_until(entered[1].is_set)
        QTest.qWait(60)
        assert window._latest_preview_frame is None
        assert window._selection_preview_frame is None and not completed
        assert not window.preview_label._selection_enabled
        gates[1].set()
        if kind == "first_frame":
            wait_until(lambda: bool(completed))
            assert completed == [frames[1]]
        elif kind == "tid_recognition":
            wait_until(lambda: window._tid_ocr_dialog.result_value.text() == "22")
        else:
            wait_until(lambda: window._preview_action is None)
            if "overlay" not in kind:
                assert np.array_equal(window._selection_preview_frame, frames[1])
        assert window._latest_preview_source == window._preview_source
        assert np.array_equal(window._latest_preview_frame, frames[1])
        assert reads[0] == 0 and 1 in reads
    finally:
        for gate in gates:
            gate.set()
        window._cancel_preview_interaction(restore_preview=False)
        window._preview_timer.stop()
        window._release_preview_capture()
        wait_until(lambda: window._preview_thread is None)


@pytest.mark.parametrize("kind", ["rng", "tid", "manual"])
@pytest.mark.parametrize("restart", [False, True])
def test_stop_invalidates_real_ready_poll_before_queued_first_frame_callback(app, monkeypatch, tmp_path, kind, restart):
    window = MainWindow()
    settle_startup(window)
    connected_controller(window)
    _rng, _tid, starts = configure_automatic_buttons(window, monkeypatch, tmp_path)
    window._video_source_connected = True
    window._first_preview_timer.setInterval(100_000)
    window._preview_timer.setInterval(100_000)
    frame = np.full((20, 30, 3), 22, np.uint8)
    manual_starts = []
    monkeypatch.setattr(window, "_read_live_preview_frame", lambda _c: frame)
    monkeypatch.setattr(mw, "render_eye_preview", lambda _c, raw: (raw.copy(), SimpleNamespace(match_score=.8)))

    def manual_capture(*_a, **_kw):
        manual_starts.append(True)
        raise RuntimeError("test capture ended")

    monkeypatch.setattr(mw, "capture_player_blinks", manual_capture)
    panel = window.auto_rng_tab if kind == "rng" else window.auto_tid_rng_tab
    start_button = window.capture_button if kind == "manual" else panel.start_button
    start_button.click()
    assert window._first_preview_pending
    wait_until(lambda: window._preview_thread._latest is not None)
    window._update_preview_frame()
    window._preview_timer.stop()
    window._poll_first_preview_frame()
    assert window._first_preview_pending and not window._first_preview_timer.isActive()
    (window.capture_button if kind == "manual" else panel.stop_button).click()
    assert not window._first_preview_pending
    if kind != "manual":
        assert not panel._preparing
    if restart:
        start_button.click()
    app.processEvents()
    QTest.qWait(30)
    assert len(manual_starts if kind == "manual" else starts[kind]) == int(restart)


@pytest.mark.parametrize("kind", ["rng", "tid"])
def test_preview_read_error_finishes_real_automatic_preparation(app, monkeypatch, tmp_path, kind):
    window = MainWindow(capture_broker_process=SimpleNamespace(stop=lambda: time.sleep(.08) or True))
    settle_startup(window)
    connected_controller(window)
    _rng, _tid, starts = configure_automatic_buttons(window, monkeypatch, tmp_path)
    window._video_source_connected = True

    def fail_read(_config):
        raise RuntimeError("driver read failed")

    monkeypatch.setattr(window, "_read_live_preview_frame", fail_read)
    panel = window.auto_rng_tab if kind == "rng" else window.auto_tid_rng_tab
    panel.start_button.click()
    assert panel._preparing
    wait_until(lambda: window._preview_thread is None and window._capture_broker_stop_thread is None and not panel._preparing)
    assert not window._video_source_connected and not window._first_preview_timer.isActive()
    assert not window.auto_rng_tab._preparing and not window.auto_tid_rng_tab._preparing
    assert not panel.stop_button.isEnabled() and panel.start_button.isEnabled()
    assert not starts["rng"] and not starts["tid"]


@pytest.mark.parametrize("cleanup_failure", [False, True])
def test_late_cancel_of_real_native_connect_keeps_owner_until_background_disconnect(app, monkeypatch, cleanup_failure):
    window = MainWindow()
    settle_startup(window)
    entered, gate = threading.Event(), threading.Event()
    transports, cleanup_threads = [], []

    class Transport(MemoryTransport):
        allow_close = not cleanup_failure

        def close(self, timeout=1.0):
            cleanup_threads.append(threading.get_ident())
            entered.set()
            assert gate.wait(3)
            if not self.allow_close:
                return False
            return super().close(timeout)

    def factory(port, baudrate):
        transport = Transport(port, baudrate)
        transports.append(transport)
        return transport

    device = NintendoSwitchDevice(transport_factory=factory, baudrates=(115200,))
    backend = NativeEasyConBackend(device=device)
    panel = window.easycon_tab
    panel.native_backend = backend
    monkeypatch.setattr(panel, "_connection_port", lambda: "mock")
    results = []
    assert panel.connect_native(on_finished=results.append)
    operation = panel._native_lifecycle_thread
    assert operation.wait(1000)  # Deliberately hold Qt's finished delivery.
    assert operation.result is True and backend.connected_port == "mock" and results == []
    try:
        assert panel.disconnect_native()
        app.processEvents()
        wait_until(lambda: entered.is_set() or panel._native_lifecycle_thread is None)
        assert entered.is_set(), (panel.task_state_text, panel.log.toPlainText() if hasattr(panel, "log") else panel.latest_log_label.text())
        assert panel._native_lifecycle_thread.operation == "cancel_connect"
        assert results == [] and not panel.connect_native()
        gate.set()
        wait_until(lambda: results == [False])
        assert cleanup_threads and all(ident != threading.get_ident() for ident in cleanup_threads)
        if cleanup_failure:
            assert panel.native_backend is backend and device.transport is transports[0]
            retry = []
            assert panel.connect_native(on_finished=retry.append)
            wait_until(lambda: retry == [False])
            assert len(transports) == 1
            transports[0].allow_close = True
            assert panel.disconnect_native()
            wait_until(lambda: not panel._native_connection_busy())
        assert backend.connected_port is None
        assert panel.connection_presentation()[1] != "connected"
    finally:
        gate.set()
        for transport in transports:
            transport.allow_close = True
        wait_until(lambda: not panel._native_connection_busy())
        if device.transport is not None:
            assert panel.disconnect_native()
            wait_until(lambda: not panel._native_connection_busy())


@pytest.mark.parametrize("dependency", ["hook", "keep_awake"])
def test_real_disconnect_blocks_both_automatic_start_buttons_and_entry(app, monkeypatch, tmp_path, dependency):
    window = MainWindow()
    settle_startup(window)
    backend = connected_controller(window)
    rng, tid, starts = configure_automatic_buttons(window, monkeypatch, tmp_path)
    panel = window.easycon_tab
    gate, entered = threading.Event(), threading.Event()
    future = Future()

    def stop_hook():
        entered.set()
        assert gate.wait(3)

    if dependency == "hook":
        panel._keyboard_hook = SimpleNamespace(stop=stop_hook)
        panel._vpad_input_source = "hook"
        panel.virtual_controller_enabled = True
    else:
        panel._capture_keep_awake_future = future
    try:
        assert window.auto_rng_tab.start_button.isEnabled() and window.auto_tid_rng_tab.start_button.isEnabled()
        assert panel.disconnect_native()
        if dependency == "hook":
            wait_until(entered.is_set)
        assert backend.status() == EasyConStatus.BRIDGE_CONNECTED
        assert not window.auto_rng_tab.start_button.isEnabled()
        assert not window.auto_tid_rng_tab.start_button.isEnabled()
        for auto in (window.auto_rng_tab, window.auto_tid_rng_tab):
            auto.start_button.click()
        window._start_auto_rng(rng)
        window._start_auto_tid_rng(tid)
        checks = {item.key: item for item in window.readiness.collect()}
        assert checks["controller"].state == checks["busy"].state == "blocked"
        assert not starts["rng"] and not starts["tid"]
        assert window._preview_thread is None and window._capture_thread is None
    finally:
        gate.set()
        if not future.done():
            future.set_result(None)
        wait_until(lambda: not panel._native_connection_busy())


@pytest.mark.parametrize("kind", ["rng", "tid"])
def test_automatic_start_coalesces_same_port_connect_and_waits_for_delivery(app, monkeypatch, tmp_path, kind):
    window = MainWindow()
    settle_startup(window)
    _rng, _tid, starts = configure_automatic_buttons(window, monkeypatch, tmp_path)
    gate, entered = threading.Event(), threading.Event()

    class Backend(FakeNativeBackend):
        calls = 0

        def connect(self, port):
            self.calls += 1
            entered.set()
            assert gate.wait(3)
            super().connect(port)

    backend = Backend()
    window.easycon_tab.native_backend = backend
    monkeypatch.setattr(window.easycon_tab, "_connection_port", lambda: "COM7")
    window._video_source_connected = True
    window._synchronize_preview_source()
    window._latest_preview_frame = np.zeros((20, 30, 3), np.uint8)
    window._latest_preview_source = window._preview_source
    assert window.easycon_tab.connect_native()
    wait_until(entered.is_set)
    panel = window.auto_rng_tab if kind == "rng" else window.auto_tid_rng_tab
    try:
        assert panel.start_button.isEnabled()
        panel.start_button.click()
        assert panel._preparing and not starts[kind]
        gate.set()
        wait_until(lambda: len(starts[kind]) == 1)
        assert backend.calls == 1
    finally:
        gate.set()
