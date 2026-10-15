from __future__ import annotations

import threading
import time

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from auto_bdsp_rng.automation.auto_rng.models import AutoRngConfig
from auto_bdsp_rng.automation.auto_rng.runner import AutoRngServices
from auto_bdsp_rng.automation.easycon import EasyConStatus
from auto_bdsp_rng.gen8_static.models import State8
from auto_bdsp_rng.rng_core.cancellation import SearchCancelled
from auto_bdsp_rng.ui.history_archive import MAX_PENDING_CANDIDATE_BATCHES, ReservedHistoryEvent
from auto_bdsp_rng.ui.main_window import MainWindow
import auto_bdsp_rng.ui.main_window as main_module
from tests.ui_wait import close_all_widgets
from tests.test_history_performance import wait_until


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setattr(MainWindow, "_start_ocr_warmup", lambda self: None)
    monkeypatch.setattr(main_module, "should_show_startup_notice", lambda: False)
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: None)
    application = QApplication.instance() or QApplication([])
    yield application
    close_all_widgets(application)


def _runner_from_actual_start(window, monkeypatch, tmp_path):
    captured = []
    monkeypatch.setattr(window, "_ensure_automation_start_available", lambda **_kwargs: True)
    monkeypatch.setattr(window, "_ensure_preview_for_auto_rng", lambda: True)
    monkeypatch.setattr(window.easycon_tab, "_native_status", lambda: EasyConStatus.BRIDGE_CONNECTED)
    monkeypatch.setattr(window, "_build_auto_rng_services", lambda _config: AutoRngServices())
    monkeypatch.setattr(window.auto_rng_tab, "run_with_runner", captured.append)
    window._start_auto_rng_after_warmup(AutoRngConfig(script_dir=tmp_path))
    assert len(captured) == 1
    return captured[0]


def test_actual_runner_keeps_going_while_slow_disk_archives_all_candidates(app, monkeypatch, tmp_path):
    window = MainWindow()
    runner = _runner_from_actual_start(window, monkeypatch, tmp_path)
    archive = window.history_tab._archive
    real_store = archive._store_candidate_batch
    first_write, allow_disk = threading.Event(), threading.Event()
    write_threads = []
    gui_thread = threading.get_ident()

    def slow_store(*args, **kwargs):
        write_threads.append(threading.get_ident())
        first_write.set()
        assert allow_disk.wait(3)
        return real_store(*args, **kwargs)

    monkeypatch.setattr(archive, "_store_candidate_batch", slow_store)
    emitted, errors = [], []

    def produce():
        try:
            for batch in range(6):
                rows = [State8(batch * 2000 + index, index, 0, index, (31,) * 6, 0, 0, 50, 0, 0, 0, 0)
                        for index in range(1500)]
                runner._history("candidates_found" if batch == 0 else "candidates_refiltered", rows, 0, [])
                emitted.append(batch)
        except Exception as exc:
            errors.append(exc)

    producer = threading.Thread(target=produce)
    producer.start()
    wait_until(first_write.is_set)
    wait_until(lambda: not producer.is_alive() and window.history_tab._candidate_count == 9000)
    # All six publications finish before even the first disk write is allowed.
    assert len(archive._reservations) == 6
    assert len(archive._pending) == 6
    beats = []
    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda: beats.append(time.monotonic()))
    timer.start()
    wait_until(lambda: len(beats) >= 5)
    allow_disk.set()
    wait_until(lambda: not producer.is_alive() and not archive._reservations, timeout_ms=10_000)
    timer.stop()
    assert errors == [] and emitted == list(range(6))
    assert gui_thread not in write_threads and len(write_threads) == 6
    assert not archive._pending and len(archive._cache) <= 128
    copied = window.history_tab._complete_plain_text()
    assert copied.count("  候选") == 9000
    positions = [copied.index(f"候选1 (锁定) adv={batch * 2000} ") for batch in range(6)]
    assert positions == sorted(positions)


def test_actual_run_stop_cancels_publication_and_rejected_close_reopens_admission(app, monkeypatch, tmp_path):
    window = MainWindow()
    runner = _runner_from_actual_start(window, monkeypatch, tmp_path)
    archive = window.history_tab._archive
    held = [archive.reserve_candidate_batch() for _ in range(MAX_PENDING_CANDIDATE_BATCHES)]
    started, finished = threading.Event(), threading.Event()
    errors = []

    def produce():
        started.set()
        try:
            runner._history("candidates_found", [State8(1, 1, 0, 1, (31,) * 6, 0, 0, 50, 0, 0, 0, 0)], 0)
        except SearchCancelled:
            errors.append("cancelled")
        finally:
            finished.set()

    producer = threading.Thread(target=produce)
    runner.stop()
    producer.start()
    assert started.wait(1)
    wait_until(finished.is_set)
    assert errors == ["cancelled"]
    for reservation in held:
        reservation.release()
    archive.stop_accepting()
    window._is_closing = True
    monkeypatch.setattr(window, "_show_error", lambda *_args: None)
    window._reject_pending_close("test", "shutdown rejected")
    assert not window._is_closing
    reservation = archive.reserve_candidate_batch(wait=False)
    assert reservation is not None
    # A handler error before it claims the payload cannot leak a future slot.
    monkeypatch.setattr(window, "_dispatch_auto_history_event", lambda *_args: (_ for _ in ()).throw(RuntimeError("handler failed")))
    with pytest.raises(RuntimeError, match="handler failed"):
        window._handle_auto_history_event("candidates_found", ReservedHistoryEvent(([1], 0), reservation))
    assert not archive._reservations
