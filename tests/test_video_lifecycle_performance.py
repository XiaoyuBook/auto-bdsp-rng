from __future__ import annotations

import threading
import time
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QEvent, QTimer, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from auto_bdsp_rng.run_log import RunLogManager
from auto_bdsp_rng.ui import MainWindow
from auto_bdsp_rng.ui.task_settings import TaskConfigBinding
import auto_bdsp_rng.ui.main_window as mw
from tests.test_ui import isolated_ui_qsettings
from tests.ui_wait import close_all_widgets, wait_until


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setattr(mw, "should_show_startup_notice", lambda: False)
    monkeypatch.setattr(mw, "_enumerate_capture_devices", lambda _api: [])
    monkeypatch.setattr(QMessageBox, "warning", lambda *_a, **_kw: None)
    monkeypatch.setattr(QMessageBox, "critical", lambda *_a, **_kw: None)
    application = QApplication.instance() or QApplication([])
    yield application
    close_all_widgets(application)


def heartbeat(window):
    ticks = []
    timer = QTimer(window)
    timer.setInterval(10)
    timer.timeout.connect(lambda: ticks.append(time.perf_counter()))
    timer.start()
    return timer, ticks


def wait_video_stopped(window):
    wait_until(lambda: window._capture_broker_stop_thread is None and window._capture_broker_start_thread is None)


def test_slow_broker_stop_keeps_qt_alive_and_reconnect_waits(app):
    gui_thread = threading.get_ident()
    stop_threads = []

    def stop():
        stop_threads.append(threading.get_ident())
        time.sleep(.3)
        return True

    window = MainWindow(capture_broker_process=SimpleNamespace(stop=stop))
    window._video_source_connected = True
    timer, ticks = heartbeat(window)
    started = time.perf_counter()
    assert window.disconnect_video_source(force=True) is False
    assert time.perf_counter() - started < .15
    assert window.connect_video_source() is False
    wait_video_stopped(window)
    timer.stop()
    assert len(ticks) >= 3
    assert max(b - a for a, b in zip(ticks, ticks[1:])) < .2
    assert stop_threads == [stop_threads[0]] and stop_threads[0] != gui_thread
    assert not window._video_source_stop_pending
    assert window.video_source_button.isEnabled()
    assert window.capture_device_combo.isEnabled()


def test_cancelled_start_stops_child_created_after_first_stop_and_allows_retry(app):
    class Process:
        def __init__(self):
            self.start_entered = threading.Event()
            self.allow_start = threading.Event()
            self.owned = False
            self.stop_calls = []

        def start(self, **_kwargs):
            self.start_entered.set()
            assert self.allow_start.wait(2)
            self.owned = True
            return True

        def client(self):
            return SimpleNamespace(close=lambda: None)

        def stop(self):
            self.stop_calls.append(self.owned)
            self.owned = False
            return True

    process = Process()
    window = MainWindow(capture_broker_process=process)
    window._preview_timer.setInterval(100_000)
    assert window.connect_video_source()
    assert process.start_entered.wait(1)
    old_thread, old_attempt = window._capture_broker_start_thread, window._capture_broker_attempt
    assert window.disconnect_video_source(force=True) is False
    wait_until(lambda: bool(process.stop_calls))
    assert process.stop_calls == [False]
    process.allow_start.set()
    wait_video_stopped(window)
    assert process.stop_calls == [False, True]
    assert not process.owned and not window._video_source_connected
    assert window.video_source_status.text() == "连接已取消"
    assert window.connect_video_source()
    wait_until(lambda: window._video_source_connected and window._capture_broker_start_thread is None)
    generation = window._video_source_generation
    window._finish_video_source_connection(old_thread, old_attempt, process, 0, 1400, True, None)
    window._capture_broker_start_finished(old_thread, old_attempt, process)
    assert window._video_source_generation == generation
    assert window._video_source_connected


def test_stop_failure_requires_explicit_async_retry(app):
    process = SimpleNamespace(stopped=False)
    process.stop = lambda: process.stopped
    window = MainWindow(capture_broker_process=process)
    window._video_source_connected = True
    assert not window.disconnect_video_source(force=True)
    wait_video_stopped(window)
    assert window._video_source_stop_pending
    assert window.video_source_button.text() == "重试断开"
    assert not window.connect_video_source()
    process.stopped = True
    assert not window.disconnect_video_source(force=True)
    wait_video_stopped(window)
    assert not window._video_source_stop_pending
    assert window.video_source_button.text() == "连接"


def test_preview_read_and_render_are_bounded_background_work_with_raw_ownership(app, monkeypatch):
    window = MainWindow()
    frame = np.zeros((360, 640, 3), np.uint8)
    gui_thread = threading.get_ident()
    threads = []
    config = object()
    monkeypatch.setattr(window, "_config_from_form", lambda: SimpleNamespace(capture=config))

    def read(_config):
        threads.append(threading.get_ident())
        time.sleep(.08)
        return frame

    def render(_config, raw):
        threads.append(threading.get_ident())
        owned = raw.copy()
        owned[0, 0] = (1, 2, 3)
        return owned, SimpleNamespace(match_score=.8)

    monkeypatch.setattr(window, "_read_live_preview_frame", read)
    monkeypatch.setattr(mw, "render_eye_preview", render)
    timer, ticks = heartbeat(window)
    window._preview_timer.start()
    started = time.perf_counter()
    for _ in range(100):
        window._update_preview_frame()
    assert time.perf_counter() - started < .15
    wait_until(lambda: window._latest_preview_frame is not None)
    assert len(ticks) >= 3
    assert all(ident != gui_thread for ident in threads)
    assert window._latest_preview_frame is frame
    assert window._latest_annotated_preview_frame is not frame
    assert not frame.any()
    assert window._latest_annotated_preview_frame[0, 0].tolist() == [1, 2, 3]
    window._preview_timer.stop()
    window._release_preview_capture()
    wait_until(lambda: window._preview_thread is None)
    timer.stop()


def test_auto_capture_coalesces_10000_display_frames_into_one_qt_wakeup(app, monkeypatch):
    window = MainWindow()
    rendered = []
    inputs = []
    monkeypatch.setattr(window, "_display_frame", rendered.append)
    wakes = QSignalSpy(window.autoCaptureFrameReady)

    def produce():
        for frame in range(10_000):
            inputs.append(frame)  # Detection/producer work remains intact.
            window.autoCaptureFrameChanged.emit(frame)

    producer = threading.Thread(target=produce)
    producer.start()
    producer.join(2)
    assert not producer.is_alive()
    assert wakes.count() == 1
    wait_until(lambda: bool(rendered))
    assert rendered == [9_999]
    assert len(inputs) == 10_000
    generation = window._video_source_generation
    window._invalidate_video_source_consumers()
    window._publish_auto_capture_frame("late", generation=generation)
    app.processEvents()
    assert rendered == [9_999]


def test_first_frame_continuation_waits_for_legacy_release_without_gui_sleep(app, monkeypatch):
    window = MainWindow()
    frame = np.zeros((20, 30, 3), np.uint8)
    resumed = []
    released = []
    monkeypatch.setattr(window, "_config_from_form", lambda: SimpleNamespace(capture=object()))

    def read(_config):
        time.sleep(.08)
        return frame

    def release():
        time.sleep(.08)
        released.append(threading.get_ident())

    monkeypatch.setattr(window, "_read_live_preview_frame", read)
    monkeypatch.setattr(window, "_close_preview_capture_on_worker", release)
    monkeypatch.setattr(mw, "render_eye_preview", lambda _c, raw: (raw, SimpleNamespace(match_score=.8)))
    timer, ticks = heartbeat(window)
    started = time.perf_counter()
    assert not window._ensure_preview_frame_before_capture(on_ready=lambda: resumed.append(window._preview_thread))
    assert time.perf_counter() - started < .15
    wait_until(lambda: bool(resumed))
    timer.stop()
    assert resumed == [None]
    assert released and released[0] != threading.get_ident()
    assert len(ticks) >= 5
    assert not window._first_preview_timer.isActive()


def install_slow_preview(window, monkeypatch, *, delay=.25, gate=None):
    frame = np.full((120, 160, 3), 29, np.uint8)
    config = window._config_from_form().capture
    source = [config]
    entered = threading.Event()
    calls = []

    class Capture:
        keep_open_for_preview = True

        def __init__(self, config):
            self.config = config
            calls.append(("open", threading.get_ident()))

        def read(self):
            calls.append(("read", threading.get_ident()))
            entered.set()
            if gate is None:
                time.sleep(delay)
            else:
                assert gate.wait(2)
            return frame

        def release(self):
            calls.append(("release", threading.get_ident()))
            time.sleep(.06)

    monkeypatch.setattr(window, "_config_from_form", lambda: SimpleNamespace(capture=source[0]))
    monkeypatch.setattr(mw, "PreviewFrameCapture", Capture)
    monkeypatch.setattr(mw, "render_eye_preview", lambda _config, raw: (raw.copy(), SimpleNamespace(match_score=.8)))
    monkeypatch.setattr(window, "_capture_preview_frame_for_config", lambda _config: pytest.fail("GUI action must use the preview worker"))
    return frame, source, entered, calls


def start_gui_preview_action(window, kind):
    if kind == "roi":
        window.select_roi_button.click()
    elif kind == "eye":
        window.raw_screenshot_button.click()
    elif kind in ("ocr_region", "ocr_overlay"):
        window._ensure_ocr_settings()
        dialog = window._ocr_settings_dialog
        dialog.set_region("nature", (1, 2, 30, 40))
        if kind == "ocr_region":
            dialog.regionSelectionRequested.emit("nature")
        else:
            dialog.regionDisplayRequested.emit("nature", mw.OcrRegion(1, 2, 30, 40))
    else:
        window._ensure_tid_ocr_settings()
        dialog = window._tid_ocr_dialog
        dialog.set_region((1, 2, 30, 40))
        (dialog.select_button if kind == "tid_ocr_region" else dialog.show_button).click()


@pytest.mark.parametrize("kind", ["roi", "eye", "ocr_region", "tid_ocr_region", "ocr_overlay", "tid_overlay"])
def test_gui_region_actions_wait_for_slow_first_frame_and_release_without_blocking(app, monkeypatch, kind):
    window = MainWindow()
    window.show()
    frame, _source, entered, calls = install_slow_preview(window, monkeypatch)
    started = QSignalSpy(window.captureSelectionStarted)
    timer, ticks = heartbeat(window)
    before = time.perf_counter()
    start_gui_preview_action(window, kind)
    assert time.perf_counter() - before < .15
    assert window._preview_action is not None
    assert not window.preview_label._selection_enabled
    assert "Esc" in window.statusBar().currentMessage()
    wait_until(lambda: window._preview_action is None and window._preview_thread is None)
    timer.stop()
    assert entered.is_set()
    assert len(ticks) >= 10
    assert max(b - a for a, b in zip(ticks, ticks[1:])) < .2
    assert all(ident != threading.get_ident() for _name, ident in calls)
    assert [name for name, _ident in calls][-1] == "release"
    assert window._latest_preview_frame is frame
    assert not window._preview_timer.isActive()
    if "overlay" in kind:
        assert started.count() == 0
        assert window.preview_label._ocr_overlay_region == mw.OcrRegion(1, 2, 30, 40)
    else:
        assert started.count() == 1 and started.at(0) == [kind]
        assert window.preview_label._selection_enabled
        assert np.array_equal(window._selection_preview_frame, frame)
        assert not np.shares_memory(window._selection_preview_frame, frame)
        window._cancel_preview_selection()


@pytest.mark.parametrize("cancellation", ["escape", "button", "status_button", "overlay_button", "source", "form", "connect", "close", "timeout"])
def test_pending_gui_selection_cancels_and_rejects_late_slow_frames(app, monkeypatch, cancellation):
    window = MainWindow()
    window.show()
    gate = threading.Event()
    _frame, source, entered, calls = install_slow_preview(window, monkeypatch, gate=gate)
    started = QSignalSpy(window.captureSelectionStarted)
    finished = QSignalSpy(window.captureSelectionFinished)
    timer, ticks = heartbeat(window)
    if cancellation == "overlay_button":
        start_gui_preview_action(window, "tid_overlay")
    else:
        window.select_roi_button.click()
    wait_until(entered.is_set)
    assert window._preview_cancel_button.isVisible()
    QTest.qWait(40)
    if cancellation == "escape":
        QTest.keyClick(window, Qt.Key.Key_Escape)
    elif cancellation == "button":
        window.select_roi_button.click()
    elif cancellation == "status_button":
        window._preview_cancel_button.click()
    elif cancellation == "overlay_button":
        window._tid_ocr_dialog.show_button.click()
    elif cancellation == "source":
        window._invalidate_video_source_consumers()
    elif cancellation == "form":
        source[0] = replace(source[0], monitor_window=not source[0].monitor_window)
        window._poll_preview_action()
    elif cancellation == "connect":
        assert not window.connect_video_source()
        assert window._capture_broker_start_thread is None
    elif cancellation == "close":
        assert not window.close()
    else:
        window._preview_action.deadline = time.monotonic() - 1
        window._poll_preview_action()
        assert "5 秒" in window.statusBar().currentMessage()
    assert window._preview_action is None
    assert window._selection_mode is None
    assert not window.preview_label._selection_enabled
    assert not window._preview_timer.isActive()
    assert not window._preview_cancel_action.isEnabled()
    assert not window._preview_cancel_button.isVisible()
    if cancellation == "overlay_button":
        assert finished.count() == 0
        assert window.preview_label._ocr_overlay_region is None
    else:
        assert finished.count() == 1 and finished.at(0) == ["roi", False]
    gate.set()
    wait_until(lambda: window._preview_thread is None)
    if cancellation == "close":
        wait_until(lambda: window._close_accepted)
    timer.stop()
    assert started.count() == 0
    assert window._latest_preview_frame is None
    assert window._selection_preview_frame is None
    assert calls[-1][0] == "release"
    assert len(ticks) >= 3


def test_real_tid_recognition_button_uses_managed_worker_and_keeps_qt_alive(app, monkeypatch):
    window = MainWindow()
    window.show()
    frame, _source, _entered, calls = install_slow_preview(window, monkeypatch)
    window._ensure_tid_ocr_settings()
    dialog = window._tid_ocr_dialog
    dialog.set_region((1, 2, 30, 40))
    ocr_threads = []

    def recognize(actual_frame, field, region):
        assert actual_frame is frame and field == "tid" and region == dialog.region
        ocr_threads.append(threading.get_ident())
        time.sleep(.25)
        return "123456"

    monkeypatch.setattr(mw, "recognize_ocr_field", recognize)
    timer, ticks = heartbeat(window)
    before = time.perf_counter()
    dialog.recognize_button.click()
    assert time.perf_counter() - before < .15
    assert dialog.recognize_button.text() == "取消识别"
    assert not dialog.select_button.isEnabled()
    wait_until(lambda: dialog.result_value.text() == "123456" and window._ocr_task_thread is None)
    timer.stop()
    assert ocr_threads and ocr_threads[0] != threading.get_ident()
    assert all(ident != threading.get_ident() for _name, ident in calls)
    assert len(ticks) >= 20
    assert max(b - a for a, b in zip(ticks, ticks[1:])) < .2
    assert dialog.select_button.isEnabled() and dialog.show_button.isEnabled()
    assert dialog.recognize_button.text() == "识别当前内容"
    assert window._tid_ocr_request_token is None


@pytest.mark.parametrize("cancellation", ["button", "source", "close"])
def test_tid_recognition_cancellation_retains_worker_and_rejects_late_results(app, monkeypatch, cancellation):
    window = MainWindow()
    window.show()
    window._latest_preview_frame = np.zeros((120, 160, 3), np.uint8)
    window._ensure_tid_ocr_settings()
    dialog = window._tid_ocr_dialog
    dialog.set_region((1, 2, 30, 40))
    entered, gate = threading.Event(), threading.Event()

    def recognize(*_args):
        entered.set()
        assert gate.wait(2)
        return "LATE-TID"

    monkeypatch.setattr(mw, "recognize_ocr_field", recognize)
    dialog.recognize_button.click()
    wait_until(entered.is_set)
    thread = window._ocr_task_thread
    assert thread is not None and thread.isRunning()
    if cancellation == "button":
        dialog.recognize_button.click()
    elif cancellation == "source":
        window._invalidate_video_source_consumers()
    else:
        assert not window.close()
    assert window._tid_ocr_request_token is None
    assert window._ocr_task_thread is thread and thread.isRunning()
    assert "取消" in dialog.result_value.text()
    gate.set()
    wait_until(lambda: window._ocr_task_thread is None)
    if cancellation == "close":
        wait_until(lambda: window._close_accepted)
    assert "LATE-TID" not in dialog.result_value.text()


def test_preview_pixmap_owns_bgr_pixels_after_raw_frame_changes(app):
    raw = np.zeros((2, 3, 3), np.uint8)
    raw[0, 0] = (0, 0, 255)
    pixmap = mw._bgr_frame_to_pixmap(raw)
    raw[:] = 0
    color = pixmap.toImage().pixelColor(0, 0)
    assert (color.red(), color.green(), color.blue()) == (255, 0, 0)


def test_close_waits_for_slow_broker_stop_and_worker_ui_finalizer(app, monkeypatch):
    window = MainWindow(capture_broker_process=SimpleNamespace(stop=lambda: time.sleep(.25) or True))
    assert window.id_tab.parent() is window
    window._video_source_connected = True
    window.show()
    assert not window.id_tab.isVisible()
    finalizers = []

    def worker():
        assert window._capture_cancel.wait(2)
        window._call_on_ui_thread(lambda: finalizers.append("released"))

    window._capture_thread = threading.Thread(target=worker)
    window._capture_thread.start()
    timer, ticks = heartbeat(window)
    started = time.perf_counter()
    assert window.close() is False
    assert time.perf_counter() - started < .15
    assert window.isVisible() and window._close_pending
    wait_until(lambda: window._static_search._closed)
    timer.stop()
    assert finalizers == ["released"]
    assert len(ticks) >= 3
    assert window._capture_broker_stop_thread is None
    assert window._ui_calls_closed.is_set()
    monkeypatch.setattr(window.id_tab, "close_search", lambda: pytest.fail("Accepted close must not repeat child cleanup"))
    assert window.close() is True
    with pytest.raises(RuntimeError, match="已经关闭"):
        window._call_on_ui_thread(lambda: None)


def test_close_preparation_freezes_recording_and_keeps_qt_alive_before_cancel(app, monkeypatch):
    window = MainWindow()
    panel = window.easycon_tab
    hooks = []
    panel._keyboard_hook = SimpleNamespace(stop=lambda: time.sleep(.25) or hooks.append("stopped"))
    panel._vpad_input_source = "hook"
    panel.virtual_controller_enabled = True
    panel._recording = True
    panel._recorded_lines = ["A 10"]
    checks = []

    def confirm():
        checks.append((hooks[:], panel._recording, panel.virtual_controller_enabled))
        return False

    monkeypatch.setattr(window, "_confirm_unsaved_easycon_script", confirm)
    timer, ticks = heartbeat(window)
    window.show()
    assert window.close() is False
    assert not panel._recording and not panel.virtual_controller_enabled
    assert not checks
    wait_until(lambda: bool(checks))
    timer.stop()
    assert checks == [(["stopped"], False, False)]
    assert len(ticks) >= 3
    assert not window._is_closing and window.isVisible()
    assert panel._keyboard_hook is None
    assert not panel._native_connection_busy()
    monkeypatch.setattr(window, "_confirm_unsaved_easycon_script", lambda: True)


def test_closed_ui_call_gate_unblocks_workers_and_rejects_late_qt_access(app):
    window = MainWindow()
    touched = []
    errors = []

    def worker():
        try:
            window._call_on_ui_thread(lambda: touched.append(True))
        except RuntimeError as exc:
            errors.append(str(exc))

    thread = threading.Thread(target=worker)
    thread.start()
    deadline = time.perf_counter() + 2
    while not window._pending_ui_calls and time.perf_counter() < deadline:
        time.sleep(.001)  # Deliberately do not dispatch the queued callback.
    assert window._pending_ui_calls
    window._close_ui_calls()
    thread.join(2)
    app.processEvents()
    assert not thread.is_alive()
    assert errors and not touched


@pytest.mark.parametrize("automatic_action", ["layout", "clear-delay-samples"])
def test_queued_config_and_readiness_callbacks_cancel_when_window_is_destroyed(app, capsys, automatic_action):
    window = MainWindow()
    panel = window.auto_rng_tab
    for binding in window.findChildren(TaskConfigBinding):
        binding._schedule_refresh()
    if automatic_action == "layout":
        window.show()
        window.tabs.setCurrentWidget(panel)
        assert panel.max_advances.minimumWidth() == panel.max_advances.maximumWidth() == 156
    else:
        panel.record_delay_sample([1451, 1452])
        assert panel.delay_samples() == [(1451, 1452)]
        dialog = panel.delay_strategy_dialog
        dialog.clear_samples_button.click()
        dialog.confirm_clear_button.click()
        assert panel.delay_samples() == [(1451, 1452)]
    assert window.close() is True
    window.deleteLater()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    stderr = capsys.readouterr().err
    assert "RuntimeError" not in stderr and "already deleted" not in stderr, stderr
    if automatic_action == "clear-delay-samples":
        assert len(panel._delay_sample_rounds) == 1


@pytest.mark.parametrize("cancel_close", [False, True])
def test_deferred_automatic_layout_runs_for_live_window_after_cancelled_close(app, monkeypatch, capsys, cancel_close):
    window = MainWindow()
    panel = window.auto_rng_tab
    window.show()
    window.tabs.setCurrentWidget(panel)
    panel.max_advances.setValue(123)
    panel.max_wait_frames.setValue(456)
    fields = (panel.max_advances, panel.max_wait_frames, panel.delay_settings_button)
    assert all(field.minimumWidth() == field.maximumWidth() == 156 for field in fields)
    try:
        if cancel_close:
            monkeypatch.setattr(window, "_confirm_unsaved_easycon_script", lambda: False)
            assert window.close() is False
            assert window.isVisible() and not window._is_closing
        wait_until(lambda: all(field.minimumWidth() == 0 and field.maximumWidth() == 16777215 for field in fields))
        assert panel.max_advances.value() == 123 and panel.max_wait_frames.value() == 456
        assert panel.strategy_group.isVisible()
        stderr = capsys.readouterr().err
        assert "RuntimeError" not in stderr and "already deleted" not in stderr, stderr
    finally:
        monkeypatch.setattr(window, "_confirm_unsaved_easycon_script", lambda: True)


def test_queued_picture_in_picture_draw_cancels_when_window_is_destroyed(app, capsys):
    preview = mw.PictureInPicturePreview()
    preview.show()
    preview.resize(640, 440)
    preview.close()
    preview.deleteLater()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    assert "RuntimeError" not in capsys.readouterr().err


@pytest.mark.parametrize(
    "output_kind, line_count, line_padding",
    [("short-lines", 30_000, 0), ("long-lines", 12, 100_000), ("single-long-line", 1, 1_000_000)],
)
def test_native_stream_logs_full_text_once_without_per_line_qt_events(app, tmp_path, output_kind, line_count, line_padding):
    manager = RunLogManager(tmp_path / "logs")
    path = manager.enable()
    window = MainWindow(run_log_manager=manager)
    window.show()
    window.tabs.setCurrentWidget(window.easycon_tab)
    window.run_log_window.show_logs("伊机控")
    QTest.qWait(120)
    entries = QSignalSpy(window._run_log_buffer.entryAdded)
    text = "".join(f"PRINT-{index:05d}:" + "x" * line_padding + "\n" for index in range(line_count))
    sink = window.easycon_tab._native_log_sink
    assert callable(sink)
    worker = threading.Thread(target=lambda: [sink("INFO", line) for line in text.splitlines()])
    worker.start()
    worker.join(5)
    assert not worker.is_alive()
    assert entries.count() == 0
    timer, ticks = heartbeat(window)
    QTest.qWait(30)
    started = time.perf_counter()
    window.easycon_tab._append_log("stdout", text, persist=False)
    elapsed = time.perf_counter() - started
    QTest.qWait(30)
    timer.stop()
    assert elapsed < .8
    max_gap = max(b - a for a, b in zip(ticks, ticks[1:]))
    assert max_gap < 1.0
    print(f"native output: kind={output_kind} lines={line_count} append_ms={elapsed * 1000:.1f} full_ui_heartbeat_gap_ms={max_gap * 1000:.1f}")
    assert entries.count() == 1
    display_message = window._run_log_buffer.snapshot()[-1].message
    assert len(display_message) <= 256 * 1024 + 80
    if line_padding:
        assert "显示已截断" in display_message
        assert "显示已截断" in window.easycon_tab.log_view.toPlainText()
    manager.disable()
    disk_before = path.read_bytes()
    sink("INFO", "DISABLED-NO-DISK")
    assert path.read_bytes() == disk_before
    disk = disk_before.decode("utf-8")
    assert disk.count("PRINT-00000") == disk.count(f"PRINT-{line_count - 1:05d}") == 1
    assert "DISABLED-NO-DISK" not in disk
    manager.close()


def test_local_output_batches_preserve_line_retention_and_bound_total_text(app):
    panel = MainWindow().easycon_tab
    for batch in range(12):
        panel._append_log("stdout", f"BATCH-{batch}:" + "x" * 100_000)
        assert panel.log_view.document().characterCount() - 1 <= 256 * 1024
        assert len(panel.latest_log_label.text()) <= 512
        assert len(panel.latest_log_label.toolTip()) <= 4096
    panel.log_keep_lines.setValue(3)
    panel._append_log("stdout", "\n".join(f"<LINE-{index}> & output" for index in range(30_000)))
    text = panel.log_view.toPlainText()
    assert panel.log_view.document().blockCount() <= 3
    assert "显示已截断" in text
    assert "<LINE-29999> & output" in text
    assert "LINE-29997" not in text
    panel._append_log("info", "final-line")
    assert panel.log_view.document().blockCount() <= 3
    assert panel.log_view.toPlainText().endswith("final-line")


@pytest.mark.parametrize("failure", [None, "create", "exec"])
def test_run_stops_diagnostics_before_manager_on_all_exit_paths(app, monkeypatch, failure):
    events = []
    manager = SimpleNamespace(
        enabled=False, cleanup=lambda: None, set_error_callback=lambda _c: None,
        write=lambda *_a, **_kw: None, write_exception=lambda *_a: None,
        close=lambda: events.append("manager.close"),
    )
    guard = SimpleNamespace(stop=lambda: events.append("diagnostics.stop"))
    hooks = SimpleNamespace(install=lambda: hooks, restore=lambda: events.append("hooks.restore"))

    def start_diagnostics(_app, _manager, **kwargs):
        assert kwargs["native_fallback"] is True
        events.append("diagnostics.start")
        return guard

    def create(**_kwargs):
        if failure == "create":
            raise ValueError("create failed")
        return SimpleNamespace(show=lambda: None)

    def execute():
        if failure == "exec":
            raise ValueError("exec failed")
        return 3

    monkeypatch.setattr(mw, "QApplication", SimpleNamespace(instance=lambda: SimpleNamespace(setFont=lambda _f: None, exec=execute)))
    monkeypatch.setattr(mw, "configure_application_identity", lambda _a: None)
    monkeypatch.setattr(mw, "RunLogManager", lambda: manager)
    monkeypatch.setattr(mw, "ExceptionHookGuard", lambda _m: hooks)
    monkeypatch.setattr(mw, "start_ui_stall_diagnostics", start_diagnostics)
    monkeypatch.setattr(mw, "create_window", create)
    monkeypatch.setattr(mw, "is_run_log_enabled", lambda: False)
    monkeypatch.setattr(mw, "is_auto_update_check_enabled", lambda: False)
    if failure is None:
        assert mw.run() == 3
    else:
        with pytest.raises(ValueError):
            mw.run()
    assert events == ["diagnostics.start", "diagnostics.stop", "manager.close", "hooks.restore"]
