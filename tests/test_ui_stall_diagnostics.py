from __future__ import annotations

from datetime import datetime
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

from auto_bdsp_rng.run_log import RunLogManager
from auto_bdsp_rng.ui_stall_diagnostics import UiStallWatchdog, start_ui_stall_diagnostics
import auto_bdsp_rng.ui_stall_diagnostics as diagnostics_module


def test_disabled_logging_does_not_create_files_or_touch_native_timer(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostics_module.faulthandler, "dump_traceback_later", lambda *_args, **_kwargs: pytest.fail("disabled logging must not arm native timer"))
    monkeypatch.setattr(diagnostics_module.faulthandler, "cancel_dump_traceback_later", lambda: pytest.fail("unowned timer must not be cancelled"))
    directory = tmp_path / "logs"
    watchdog = UiStallWatchdog(directory, lambda: False, native_fallback=True)
    watchdog._check_once()
    watchdog.start()
    assert watchdog.stop()
    assert watchdog.stop()
    assert not directory.exists()


def test_stall_snapshot_contains_thread_stacks_versions_and_one_report_per_heartbeat(tmp_path):
    clock = [0.0]
    reports = []
    watchdog = UiStallWatchdog(tmp_path, lambda: True, threshold_seconds=2, clock=lambda: clock[0], now=lambda: datetime(2026, 10, 12, 10), report=reports.append)
    clock[0] = 2.5
    watchdog._check_once()
    watchdog._check_once()
    path = watchdog.last_path
    assert path is not None
    text = path.read_text(encoding="utf-8")
    assert text.count("UI stall at ") == 1
    assert "heartbeat_delay=2.500s" in text
    assert "app=" in text and "python=" in text and "PySide6=" in text
    assert "Python thread" in text and "MainThread" in text
    assert "test_stall_snapshot_contains" in text
    assert len(reports) == 1
    watchdog.heartbeat()
    clock[0] += 3
    watchdog._check_once()
    assert path.read_text(encoding="utf-8").count("UI stall at ") == 2
    assert watchdog.stop()


def test_repeated_save_toggles_stop_writes_and_use_only_exact_diagnostic_retention(tmp_path):
    saved = [False]
    clock = [0.0]
    expired = tmp_path / "ui_stall_2026-10-05_pid-1.log"
    retained = tmp_path / "ui_stall_2026-10-06_pid-1.log"
    unrelated = tmp_path / "ui_stall_2026-10-05_custom.log"
    daily = tmp_path / "run_2026-10-05.log"
    for path in (expired, retained, unrelated, daily):
        path.write_text("keep", encoding="utf-8")
    watchdog = UiStallWatchdog(tmp_path, lambda: saved[0], threshold_seconds=1, clock=lambda: clock[0], now=lambda: datetime(2026, 10, 12))
    watchdog._check_once()
    assert expired.exists()
    for _ in range(3):
        saved[0] = True
        watchdog.heartbeat()
        clock[0] += 2
        watchdog._check_once()
        assert watchdog.last_path is not None
        size = watchdog.last_path.stat().st_size
        saved[0] = False
        watchdog._check_once()
        clock[0] += 3
        watchdog._check_once()
        assert watchdog.last_path.stat().st_size == size
    assert not expired.exists()
    assert retained.exists() and unrelated.exists() and daily.exists()
    watchdog.stop()


def test_native_timer_is_single_shot_and_guards_never_cancel_each_others_timer(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(diagnostics_module.faulthandler, "dump_traceback_later", lambda timeout, **kwargs: calls.append(("arm", timeout, kwargs)))
    monkeypatch.setattr(diagnostics_module.faulthandler, "cancel_dump_traceback_later", lambda: calls.append(("cancel",)))
    owner = UiStallWatchdog(tmp_path / "owner", lambda: True, native_fallback=True)
    other = UiStallWatchdog(tmp_path / "other", lambda: True, native_fallback=True)
    owner._check_once()
    assert calls[0][0] == "arm"
    assert calls[0][2]["repeat"] is False
    assert calls[0][2]["exit"] is False
    other._check_once()
    other.stop()
    assert len(calls) == 1
    owner.stop()
    owner.stop()
    assert calls[-1] == ("cancel",)
    assert len(calls) == 2
    assert diagnostics_module._native_owner is None


def test_storage_failure_is_reported_once_without_killing_the_monitor(tmp_path):
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("block", encoding="utf-8")
    reports = []
    watchdog = UiStallWatchdog(blocker, lambda: True, threshold_seconds=0.05, poll_seconds=0.01, report=reports.append).start()
    deadline = time.monotonic() + 2
    while watchdog.last_error is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert watchdog.last_error is not None
    time.sleep(0.03)
    assert len(reports) == 1
    assert watchdog.stop()
    assert not watchdog._thread.is_alive()


def test_qt_integration_keeps_io_off_heartbeat_and_stops_timer_and_worker(tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest

    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    manager = RunLogManager(tmp_path / "logs")
    guard = start_ui_stall_diagnostics(app, manager, threshold_seconds=0.1, heartbeat_ms=10)
    try:
        QTest.qWait(20)
        assert not manager.directory.exists()
        assert guard.timer.isActive()
        manager.enable()
        deadline = time.monotonic() + 2
        while guard.watchdog.last_path is None and time.monotonic() < deadline:
            QTest.qWait(10)
        assert guard.watchdog.last_path is not None
        manager.disable()
        guard.refresh_enabled()
        QTest.qWait(30)
        size = guard.watchdog.last_path.stat().st_size
        QTest.qWait(30)
        assert guard.watchdog.last_path.stat().st_size == size
        assert guard.stop()
        assert guard.stop()
        assert not guard.timer.isActive()
        assert not guard.watchdog._thread.is_alive()
    finally:
        guard.stop()
        guard.deleteLater()
        manager.close()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows GIL-holding native call")
def test_native_fallback_captures_a_gil_holding_call_without_repeating(tmp_path):
    # A separate process protects pytest's own/global faulthandler timer.
    code = """
import ctypes, sys, time
from pathlib import Path
from auto_bdsp_rng.ui_stall_diagnostics import UiStallWatchdog
w = UiStallWatchdog(Path(sys.argv[1]), lambda: True, threshold_seconds=.05, poll_seconds=.01, native_fallback=True).start()
deadline = time.monotonic() + 3
while w._armed_generation is None and time.monotonic() < deadline:
    w.heartbeat()
    time.sleep(.005)
assert w._armed_generation is not None
sleep = ctypes.PyDLL('kernel32').Sleep
sleep.argtypes = [ctypes.c_ulong]
sleep(200)
time.sleep(.04)
assert w.stop()
text = w.last_path.read_text(encoding='utf-8')
assert 'Timeout (' in text, text
assert 'Native faulthandler already captured' in text, text
assert text.count('Timeout (') == 1, text
assert text.count('UI stall at ') == 1, text
"""
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path)], text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
