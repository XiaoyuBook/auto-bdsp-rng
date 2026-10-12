"""Hardware-free history scale/Qt heartbeat check; prints one JSON result.

Run with .venv\\Scripts\\python.exe scripts/benchmark_history_ui.py
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import re
import tempfile
import threading
import time
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QEventLoop, QObject, QTimer, Signal, Slot
from PySide6.QtWidgets import QApplication, QFileDialog

from auto_bdsp_rng.gen8_static.models import State8
from auto_bdsp_rng.ui.history_archive import publish_history_event
from auto_bdsp_rng.ui.history_panel import HistoryPanel
import auto_bdsp_rng.ui.history_panel as history_module


class _HistoryEmitter(QObject):
    event = Signal(str, object)

    @Slot(str, object)
    def receive(self, event, payload):
        self.callback(event, payload)


def _wait_for(predicate, *, timeout_ms=30_000):
    if predicate():
        return
    loop = QEventLoop()
    poll = QTimer()
    poll.setInterval(5)
    poll.timeout.connect(lambda: loop.quit() if predicate() else None)
    timeout = QTimer()
    timeout.setSingleShot(True)
    timeout.timeout.connect(loop.quit)
    poll.start()
    timeout.start(timeout_ms)
    loop.exec()
    poll.stop()
    timeout.stop()
    if not predicate():
        raise TimeoutError("history benchmark did not finish before timeout")


def _measure_gui_action(action, snapshots, finished=None):
    """Use the normal Qt event loop, with GC enabled and a real button call."""
    loop = QEventLoop()
    last_beat = [time.perf_counter()]
    phase_started = last_beat[0]
    gaps, active_gaps, timing, errors = [], [], {}, []

    def run():
        before = snapshots[0]
        timing["started"] = time.perf_counter()
        try:
            action()
        except BaseException as error:
            errors.append(error)
        timing["ended"] = time.perf_counter()
        timing["gui_snapshots"] = snapshots[0] - before

    def heartbeat():
        now = time.perf_counter()
        gaps.append((now - last_beat[0]) * 1000)
        if "started" in timing:
            active_gaps.append((now - max(last_beat[0], timing["started"])) * 1000)
        last_beat[0] = now
        if "ended" in timing and now - timing["ended"] >= 0.03 and (errors or finished is None or finished()):
            timing["completed"] = now
            loop.quit()

    beat = QTimer()
    beat.setInterval(10)
    beat.timeout.connect(heartbeat)
    invoke = QTimer()
    invoke.setSingleShot(True)
    invoke.timeout.connect(run)
    timeout = QTimer()
    timeout.setSingleShot(True)
    timeout.timeout.connect(loop.quit)
    beat.start()
    invoke.start(30)
    timeout.start(30_000)
    loop.exec()
    beat.stop()
    invoke.stop()
    timeout.stop()
    if errors:
        raise errors[0]
    if "completed" not in timing:
        raise TimeoutError("history benchmark action did not finish before timeout")
    return {
        "gui_action_ms": round((timing["ended"] - timing["started"]) * 1000, 3),
        "complete_ms": round((timing["completed"] - timing["started"]) * 1000, 3),
        "max_qt_heartbeat_gap_ms": round(max(gaps, default=0), 3),
        "max_heartbeat_gap_after_action_start_ms": round(max(active_gaps, default=0), 3),
        "pre_action_delay_ms": round((timing["started"] - phase_started) * 1000, 3),
        "gui_snapshots": timing["gui_snapshots"],
    }


def _benchmark_page_storage(app, args, storage):
    panel = HistoryPanel()
    panel.begin_run("page-benchmark", "Benchmark")
    for index in range(args.rounds):
        panel.cycle_start(index + 1)
        if index % 25 == 0:
            app.processEvents()
    count = args.candidates
    candidates = [State8(index, index, 0, index, (31,) * 6, 0, 0, 50, 0, 0, 0, 0) for index in range(count)]
    # One far duplicate tests that a direct last-page read retains its origin.
    candidates[-1] = replace(candidates[-1], ec=candidates[0].ec, pid=candidates[0].pid)
    flags = [""] * count
    flags[-1] = "sync"
    entered, release = threading.Event(), threading.Event()
    real_store = panel._archive._store_candidate_batch
    real_snapshot = history_module._snapshot_candidate
    gui_thread = threading.get_ident()
    snapshots = [0]

    def store(*values, **options):
        if storage == "fallback":
            raise OSError("benchmark storage failure")
        if storage == "pending":
            entered.set()
            if not release.wait(30):
                raise TimeoutError("benchmark archive gate was not released")
        return real_store(*values, **options)

    def snapshot(candidate):
        if threading.get_ident() == gui_thread:
            snapshots[0] += 1
        return real_snapshot(candidate)

    panel._archive._store_candidate_batch = store
    history_module._snapshot_candidate = snapshot
    producer = None
    try:
        if args.producer:
            received = threading.Event()
            emitter = _HistoryEmitter(panel)
            archive, emit = panel._archive, emitter.event.emit
            receiver_timing, producer_timing, errors = {}, {}, []
            real_prepare = panel._archive._prepare_candidates

            def prepare(*values, **options):
                started = time.perf_counter()
                result = real_prepare(*values, **options)
                producer_timing["preparation_ms"] = round((time.perf_counter() - started) * 1000, 3)
                producer_timing["prepared_on_gui"] = threading.get_ident() == gui_thread
                return result

            panel._archive._prepare_candidates = prepare

            def receive(event, payload):
                before = snapshots[0]
                started = time.perf_counter()
                try:
                    assert threading.get_ident() == gui_thread
                    assert event == "candidates_found" and payload.reservation.prepared_rows is not None
                    source = payload.reservation.prepared_rows
                    assert panel.candidates_found(*payload.values, reservation=payload.reservation)
                    assert panel._archive._pending[1][0] is source
                except BaseException as error:
                    errors.append(error)
                finally:
                    receiver_timing["gui_receive_ms"] = round((time.perf_counter() - started) * 1000, 3)
                    receiver_timing["gui_receive_snapshots"] = snapshots[0] - before
                    payload.reservation.release_unclaimed()
                    received.set()

            def produce():
                try:
                    publish_history_event(archive, emit, "candidates_found", (candidates, count - 1, flags))
                except BaseException as error:
                    errors.append(error)
                    received.set()

            emitter.callback = receive
            emitter.event.connect(emitter.receive)
            producer = threading.Thread(target=produce)
            admission = _measure_gui_action(producer.start, snapshots, received.is_set)
            _wait_for(lambda: not producer.is_alive())
            if errors:
                raise errors[0]
            admission.update(receiver_timing)
            admission.update(producer_timing)
            assert not producer_timing["prepared_on_gui"] and receiver_timing["gui_receive_snapshots"] <= 100
        else:
            admission = _measure_gui_action(lambda: panel.candidates_found(candidates, count - 1, flags), snapshots)
        panel._archive.start_worker()
        _wait_for(entered.is_set if storage == "pending" else lambda: not panel._archive._reservations)
        assert bool(panel._archive._pending) == (storage == "pending")
        assert bool(panel._archive._fallback_candidates) == (storage == "fallback")
        table = panel.round_candidate_table
        page_count = (count + 99) // 100
        table._page = page_count - 2
        table._next_button.setEnabled(True)
        panel._archive._cache.clear()
        last_page = _measure_gui_action(table._next_button.click, snapshots)
        assert table._page == page_count - 1
        assert table.item(table.rowCount() - 1, 2).text() == str(count - 1)
        assert table.item(table.rowCount() - 1, 1).text() == "锁定 · 同步 · 同候选1"
        table._page = 1
        table._previous_button.setEnabled(True)
        panel._archive._cache.clear()
        first_page = _measure_gui_action(table._previous_button.click, snapshots)
        assert table._page == 0 and table.item(0, 2).text() == "0"
        assert last_page["gui_snapshots"] <= 256 and first_page["gui_snapshots"] <= 128
        if storage == "archived":
            assert not last_page["gui_snapshots"] and not first_page["gui_snapshots"]
        with tempfile.TemporaryDirectory(prefix="bdsp-history-page-benchmark-") as directory:
            destination = Path(directory) / "complete.txt"
            dialog = QFileDialog.getSaveFileName
            try:
                QFileDialog.getSaveFileName = lambda *_args: (str(destination), "")
                export = _measure_gui_action(panel.export_to_file, snapshots, lambda: not panel._export_busy)
            finally:
                QFileDialog.getSaveFileName = dialog
            text = destination.read_text(encoding="utf-8")
            rows = [(int(match[1]), int(match[2])) for match in re.finditer(r"^  候选(\d+) \([^)]*\) adv=(\d+) ", text, re.MULTILINE)]
            assert len(rows) == count and all(number == index + 1 and advances == index for index, (number, advances) in enumerate(rows))
            assert f"候选{count} (锁定 · 同步 · 同候选1) adv={count - 1} " in text
        return {
            "storage": storage, "rounds": args.rounds, "candidates": count,
            "admission_path": "producer" if args.producer else "direct-gui",
            "gc_enabled": gc.isenabled(), "admission": admission,
            "last_page": last_page, "first_page": first_page,
            "full_export": export, "exported_candidates": len(rows),
            "export_order_complete": True, "candidate_cache_entries": len(panel._archive._cache),
        }
    finally:
        release.set()
        history_module._snapshot_candidate = real_snapshot
        panel.shutdown()
        if producer is not None:
            _wait_for(lambda: not producer.is_alive())
        _wait_for(lambda: panel._archive._worker is None or not panel._archive._worker.is_alive())
        panel.deleteLater()
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rounds", type=int, default=1000)
    parser.add_argument("--candidates", type=int, default=100_000)
    parser.add_argument("--batches", type=int, default=3, choices=(1, 2, 3))
    parser.add_argument("--write-delay-ms", type=float, default=0)
    parser.add_argument("--review-pages", action="store_true", help="measure real first/last buttons and full export in all source states")
    parser.add_argument("--storage", choices=("all", "pending", "fallback", "archived"), default="all")
    parser.add_argument("--producer", action="store_true", help="prepare before queued Qt emission, using the product publisher")
    args = parser.parse_args()
    if args.rounds <= 0 or args.candidates <= 0 or args.write_delay_ms < 0:
        parser.error("counts must be positive")
    if args.producer and not args.review_pages:
        parser.error("--producer requires --review-pages")
    app = QApplication.instance() or QApplication([])
    if args.review_pages:
        if args.candidates <= 100:
            parser.error("page review requires more than 100 candidates")
        gc.enable()
        states = ("pending", "fallback", "archived") if args.storage == "all" else (args.storage,)
        results = [_benchmark_page_storage(app, args, storage) for storage in states]
        print(json.dumps({"page_review": results}, ensure_ascii=True))
        return 0
    panel = HistoryPanel()
    panel.begin_run("benchmark", "Benchmark")
    for index in range(args.rounds):
        panel.cycle_start(index + 1)
        if index % 25 == 0:
            app.processEvents()
    started = time.perf_counter()
    panel.seed_captured("benchmark-seed", 0, 0, args.candidates)
    round_update_ms = (time.perf_counter() - started) * 1000
    candidates = [State8(index, index, 0, index, (31,) * 6, 0, 0, 50, 0, 0, 0, 0) for index in range(args.candidates)]
    write_threads = set()
    original_db = panel._archive._candidate_db

    class MeasuredDatabase:
        def __getattr__(self, name):
            return getattr(original_db, name)

        def executemany(self, *values):
            write_threads.add(threading.get_ident())
            if args.write_delay_ms:
                time.sleep(args.write_delay_ms / 1000)
            return original_db.executemany(*values)

    panel._archive._candidate_db = MeasuredDatabase()
    archive_worker_timings = []
    operation_worker_timings = {}
    burst_started = [0.0]
    real_store = panel._archive._store_candidate_batch

    def measured_store(batch, *values, **options):
        started = time.perf_counter()
        try:
            return real_store(batch, *values, **options)
        finally:
            completed = time.perf_counter()
            archive_worker_timings.append({
                "batch": batch,
                "started_after_burst_ms": round((started - burst_started[0]) * 1000, 3),
                "completed_after_burst_ms": round((completed - burst_started[0]) * 1000, 3),
                "duration_ms": round((completed - started) * 1000, 3),
            })

    panel._archive._store_candidate_batch = measured_store

    def measure_worker(controller, operation):
        submit = controller.submit

        def measured_submit(work, *callbacks):
            submitted = time.perf_counter()

            def measured_work(cancellation):
                started = time.perf_counter()
                try:
                    return work(cancellation)
                finally:
                    completed = time.perf_counter()
                    operation_worker_timings[operation] = {
                        "started_after_submit_ms": round((started - submitted) * 1000, 3),
                        "completed_after_submit_ms": round((completed - submitted) * 1000, 3),
                        "duration_ms": round((completed - started) * 1000, 3),
                    }

            return submit(measured_work, *callbacks)

        controller.submit = measured_submit

    measure_worker(panel._history_search, "search")
    measure_worker(panel._copy_work, "copy")
    loop = QEventLoop()
    last_beat = [time.perf_counter()]
    gaps = []
    candidate_update_ms = []
    peaks = {"pending_sources": 0, "reservations": 0, "queued_jobs": 0}
    failed = []
    condition = [lambda: len(candidate_update_ms) == args.batches and not panel._archive._reservations]

    def heartbeat():
        now = time.perf_counter()
        gaps.append((now - last_beat[0]) * 1000)
        last_beat[0] = now
        peaks["pending_sources"] = max(peaks["pending_sources"], len(panel._archive._pending))
        peaks["reservations"] = max(peaks["reservations"], len(panel._archive._reservations))
        peaks["queued_jobs"] = max(peaks["queued_jobs"], panel._archive._jobs.qsize())
        if condition[0]():
            loop.quit()

    def ingest(index):
        started = time.perf_counter()
        operation = panel.candidates_found if index == 0 else panel.candidates_refiltered
        if not operation(candidates, index):
            failed.append(f"batch {index + 1} was not admitted")
        candidate_update_ms.append((time.perf_counter() - started) * 1000)

    def timeout():
        failed.append("history operation did not finish in 30 seconds")
        loop.quit()

    beat_timer = QTimer()
    beat_timer.setInterval(10)
    beat_timer.timeout.connect(heartbeat)
    beat_timer.start()
    timeout_timer = QTimer()
    timeout_timer.setSingleShot(True)
    timeout_timer.timeout.connect(timeout)
    timeout_timer.start(30_000)
    burst_started[0] = time.perf_counter()
    for index in range(args.batches):
        QTimer.singleShot(0, lambda index=index: ingest(index))
    loop.exec()
    archive_complete_ms = (time.perf_counter() - burst_started[0]) * 1000
    snapshot = panel._active_record().candidate_snapshot
    archived_count = panel._archive._perform(lambda db: db.execute("SELECT COUNT(*) FROM candidate").fetchone()[0], candidates=True)
    started = time.perf_counter()
    panel.search_edit.setText("history-benchmark-missing-keyword")
    search_submit_ms = (time.perf_counter() - started) * 1000
    condition[0] = lambda: not panel._search_inflight and not panel._filter_start_timer.isActive()
    timeout_timer.start(30_000)
    loop.exec()
    search_complete_ms = (time.perf_counter() - started) * 1000
    panel.search_edit.clear()
    started = time.perf_counter()
    panel.copy_selected_round()
    copy_submit_ms = (time.perf_counter() - started) * 1000
    condition[0] = lambda: not panel._copy_busy
    timeout_timer.start(30_000)
    loop.exec()
    copy_complete_ms = (time.perf_counter() - started) * 1000
    copied_count = QApplication.clipboard().text().count("  候选")
    beat_timer.stop()
    timeout_timer.stop()
    result = {
        "rounds": args.rounds, "candidates_per_batch": args.candidates, "batches": args.batches,
        "write_delay_ms": args.write_delay_ms,
        "round_update_ms": round(round_update_ms, 3),
        "candidate_update_ms": round(candidate_update_ms[0], 3),
        "candidate_burst_update_ms": [round(value, 3) for value in candidate_update_ms],
        "archive_complete_ms": round(archive_complete_ms, 3),
        "archive_worker_batches": archive_worker_timings,
        "search_submit_ms": round(search_submit_ms, 3),
        "search_complete_ms": round(search_complete_ms, 3),
        "copy_submit_ms": round(copy_submit_ms, 3),
        "copy_complete_ms": round(copy_complete_ms, 3),
        "operation_workers": operation_worker_timings,
        "max_qt_heartbeat_gap_ms": round(max(gaps, default=0), 3),
        "visible_rounds": panel.round_list.count(),
        "visible_candidates": panel.round_candidate_table.rowCount(),
        "round_cache_entries": len(panel._round_records.cache),
        "candidate_cache_entries": len(panel._archive._cache),
        "archived_candidates": archived_count,
        "copied_candidates": copied_count,
        "peak_backlog": peaks,
        "candidate_writes_on_gui": threading.get_ident() in write_threads,
        "last_candidate_adv": snapshot.states[-1].advances,
        "errors": failed,
    }
    print(json.dumps(result, ensure_ascii=True))
    panel._archive.close()
    return int(bool(failed) or archived_count != args.candidates * args.batches or copied_count != archived_count)


if __name__ == "__main__":
    raise SystemExit(main())
