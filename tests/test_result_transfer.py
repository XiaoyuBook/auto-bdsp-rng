"""Real result actions, fixed snapshots, safe file commits, and UI latency."""
from __future__ import annotations

import csv
import gc
import threading
import time
from datetime import datetime
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog

from auto_bdsp_rng.automation.auto_tid_rng import predict_tid_elapsed_seconds
from auto_bdsp_rng.gen8_id import generate_ids
from auto_bdsp_rng.rng_core import SeedPair64
from auto_bdsp_rng.ui import result_transfer
from auto_bdsp_rng.ui.result_transfer import ResultTransfer
from tests.test_search_performance import _finish, _heartbeat, _max_gap, _states
from tests.test_start_readiness import window
from tests.ui_wait import wait_search, wait_until


@pytest.fixture(autouse=True)
def collect_closed_ui_cycles():
    gc.collect()  # GC remains enabled throughout all measured operations.


@pytest.fixture
def clipboard_sink(monkeypatch):
    main_thread = threading.get_ident()
    sink = SimpleNamespace(value="original clipboard", calls=[])

    def set_text(text):
        assert threading.get_ident() == main_thread
        sink.value = text
        sink.calls.append(text)

    monkeypatch.setattr(QGuiApplication, "clipboard", lambda: SimpleNamespace(setText=set_text, text=lambda: sink.value))
    return sink


def _setup(window, kind, count):
    if kind == "static":
        _finish(window, _states(count))
        return window
    panel = window.auto_tid_rng_tab
    seed = SeedPair64(1, 2)
    states = generate_ids(seed, max_advances=count)
    seconds = predict_tid_elapsed_seconds(seed, [state.advances for state in states])
    panel.set_id_states(states, elapsed_seconds=seconds, measured_wall_time=1_700_000_000)
    return panel


def _wait_transfer(owner):
    wait_until(lambda: owner._result_transfer.kind is None, timeout_ms=15_000)


def _gate_rows(owner, monkeypatch):
    gate, entered = threading.Event(), threading.Event()
    original = owner._result_export_snapshot

    def snapshot():
        rows, count = original()

        def blocked():
            values = iter(rows())
            yield next(values)
            first = next(values)
            entered.set()
            assert gate.wait(5)
            yield first
            yield from values

        return blocked, count

    monkeypatch.setattr(owner, "_result_export_snapshot", snapshot)
    return gate, entered


@pytest.mark.parametrize("kind,operation,warm", [
    (kind, operation, warm)
    for warm in (False, True)
    for operation in ("copy", "csv")
    for kind in ("static", "tid")
] + [("static", "txt", False)])
def test_complete_100k_transfer_keeps_gui_responsive_and_all_values(window, monkeypatch, tmp_path, clipboard_sink, kind, operation, warm):
    owner = _setup(window, kind, 100_001)
    if kind == "static":
        owner.show_stats_check.setChecked(True)
        owner.table.horizontalHeader().setSortIndicator(8, Qt.SortOrder.DescendingOrder)
        owner.table.setSortingEnabled(True)
        wait_search(owner.table.pager._search)
    else:
        owner._clear_targets()
        owner.add_target_display_tid(3)
        owner.id_filter_targets_button.click()
        wait_search(owner.id_table.pager._search)
        owner.update_tid_current_advances(900)
        if not warm:
            assert len(owner._id_arrival_texts._cache) < 1000
    if warm:
        owner._table_text()  # Real cold formatting is measured in the other case.
    output = tmp_path / f"{kind}.{operation}"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(output), ""))
    QTest.qWait(20)
    timer, beats = _heartbeat(window)
    started = time.perf_counter()
    if operation == "txt":
        owner.export_results_txt()
    else:
        (owner.copy_button if operation == "copy" else owner.export_button).click()
    entry = time.perf_counter() - started
    _wait_transfer(owner)
    total = time.perf_counter() - started
    gap = _max_gap(timer, beats)
    print(f"100k {kind} {operation} {'warm' if warm else 'cold'}: entry={entry:.4f}s total={total:.4f}s heartbeat_gap={gap:.4f}s", flush=True)
    assert gc.isenabled()
    assert entry < 0.1 and gap < 0.3
    if operation == "copy":
        rows = [line.split("\t") for line in clipboard_sink.value.splitlines()]
        assert len(clipboard_sink.calls) == 1
    elif operation == "csv":
        with output.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.reader(handle))
        assert not list(tmp_path.glob(".rng-export-*.tmp"))
    else:
        rows = [line.split("\t") for line in output.read_text(encoding="utf-8").splitlines()]
        assert not list(tmp_path.glob(".rng-export-*.tmp"))
    assert len(rows) == 100_002
    assert [int(row[0]) for row in rows[1:]] == list(range(100_001))
    if kind == "static":
        assert rows[0] == owner._result_headers()
        assert rows[1:] == [owner._state_row(state) for state in owner._states]
    else:
        assert all(len(row[4]) == 6 for row in rows[1:])
        assert all(row[6] not in ("已过", "当前帧") for row in rows[1:])
        assert rows == [list(row) for row in owner._export_rows()]


@pytest.mark.parametrize("kind", ["static", "tid"])
@pytest.mark.parametrize("operation", ["copy", "csv"])
def test_transfer_uses_click_time_language_stats_and_dates(window, monkeypatch, tmp_path, clipboard_sink, kind, operation):
    owner = _setup(window, kind, 2001)
    if kind == "static":
        owner.show_stats_check.setChecked(True)
    expected = owner._table_text()
    gate, entered = _gate_rows(owner, monkeypatch)
    output = tmp_path / "snapshot.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(output), ""))
    try:
        (owner.copy_button if operation == "copy" else owner.export_button).click()
        assert entered.wait(1)
        if kind == "static":
            owner.lang = "en"
            owner.show_stats_check.setChecked(False)
        else:
            owner.set_tid_timing_origin(1_700_086_400)
            owner.update_tid_current_advances(1800)
        gate.set()
        _wait_transfer(owner)
        if operation == "copy":
            assert clipboard_sink.value == expected
        else:
            with output.open(encoding="utf-8", newline="") as handle:
                assert list(csv.reader(handle)) == [line.split("\t") for line in expected.splitlines()]
    finally:
        gate.set()


@pytest.mark.parametrize("kind,operation", [("static", "csv"), ("static", "txt"), ("tid", "csv")])
@pytest.mark.parametrize("during_dialog", ["view_change", "new_results"])
def test_export_snapshot_precedes_file_dialog_and_new_results_invalidate_it(window, monkeypatch, tmp_path, kind, operation, during_dialog):
    owner = _setup(window, kind, 2001)
    if kind == "static":
        owner.show_stats_check.setChecked(True)
    expected = owner._table_text()
    output = tmp_path / f"snapshot.{operation}"
    output.write_text("existing", encoding="utf-8")

    def dialog(*args):
        if during_dialog == "new_results":
            _setup(window, kind, 3)
        elif kind == "static":
            owner.lang = "en"
            owner.show_stats_check.setChecked(False)
        else:
            owner.set_tid_timing_origin(1_700_086_400)
            owner.update_tid_current_advances(1800)
        return str(output), ""

    monkeypatch.setattr(QFileDialog, "getSaveFileName", dialog)
    (owner.export_results_txt if operation == "txt" else owner.export_results)()
    _wait_transfer(owner)
    if during_dialog == "new_results":
        assert output.read_text(encoding="utf-8") == "existing"
    elif operation == "txt":
        assert output.read_text(encoding="utf-8") == expected
    else:
        with output.open(encoding="utf-8", newline="") as handle:
            assert list(csv.reader(handle)) == [line.split("\t") for line in expected.splitlines()]
    assert not list(tmp_path.glob(".rng-export-*.tmp"))


@pytest.mark.parametrize("kind", ["static", "tid"])
def test_latest_copy_replaces_pending_export_without_cancelling_generation_or_view(window, monkeypatch, tmp_path, clipboard_sink, kind):
    owner = _setup(window, kind, 2001)
    table = owner.table if kind == "static" else owner.id_table
    wait_search(table.pager._search)
    domain = owner._static_search if kind == "static" else owner._manual_search
    domain_gate, view_gate = threading.Event(), threading.Event()
    domain_entered, view_entered = threading.Event(), threading.Event()
    domain_results, view_results = [], []

    def blocked_search(token, gate, entered):
        entered.set()
        assert gate.wait(5)
        token.check()
        return "finished"

    def fail(error):
        raise error

    domain.submit(lambda token: blocked_search(token, domain_gate, domain_entered), domain_results.append, fail)
    table.pager._search.submit(lambda token: blocked_search(token, view_gate, view_entered), view_results.append, fail)
    gate, entered = _gate_rows(owner, monkeypatch)
    output = tmp_path / "superseded.csv"
    output.write_text("existing", encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(output), ""))
    try:
        assert domain_entered.wait(1) and view_entered.wait(1)
        domain_token, view_token = domain._active_token, table.pager._search._active_token
        owner.copy_button.click()
        assert entered.wait(1)
        search = owner._result_transfer._search
        worker, old_token = search.worker_thread, search._active_token
        owner.export_button.click()  # Pending export replaces the blocked copy.
        owner.copy_button.click()  # Only this latest copy may be delivered.
        assert old_token.is_cancelled() and search.worker_thread is worker
        assert search._pending is not None and owner._result_transfer.kind == "copy"
        assert not domain_token.is_cancelled() and not view_token.is_cancelled()
        expected = owner._table_text()
        gate.set()
        _wait_transfer(owner)
        assert clipboard_sink.calls == [expected]
        assert output.read_text(encoding="utf-8") == "existing"
        assert not list(tmp_path.glob(".rng-export-*.tmp"))
        domain_gate.set()
        view_gate.set()
        wait_search(domain)
        wait_search(table.pager._search)
        assert domain_results == view_results == ["finished"]
    finally:
        gate.set()
        domain_gate.set()
        view_gate.set()


@pytest.mark.parametrize("kind", ["static", "tid"])
@pytest.mark.parametrize("operation", ["copy", "csv"])
@pytest.mark.parametrize("invalidate", ["cancel", "new_results", "close"])
def test_preparation_cancel_new_results_and_close_preserve_existing_outputs(window, monkeypatch, tmp_path, clipboard_sink, kind, operation, invalidate):
    owner = _setup(window, kind, 2001)
    gate, entered = _gate_rows(owner, monkeypatch)
    output = tmp_path / "existing.csv"
    output.write_text("existing file", encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(output), ""))
    transfer = owner._result_transfer
    try:
        button = owner.copy_button if operation == "copy" else owner.export_button
        button.click()
        assert entered.wait(1)
        assert "取消" in button.text()
        token = transfer._search._active_token
        if invalidate == "cancel":
            button.click()
        elif invalidate == "new_results":
            _setup(window, kind, 3)
        else:
            window.close()
            wait_until(lambda: transfer._closed)
        assert token.is_cancelled()
        gate.set()
        wait_until(lambda: transfer._search.worker_thread is None)
        if not transfer._closed:
            transfer._search.poll()
        assert clipboard_sink.value == "original clipboard" and not clipboard_sink.calls
        assert output.read_text(encoding="utf-8") == "existing file"
        assert not list(tmp_path.glob(".rng-export-*.tmp"))
    finally:
        gate.set()


def test_cancel_after_file_preparation_before_gui_acceptance_keeps_old_file(window, monkeypatch, tmp_path):
    owner = _setup(window, "static", 2001)
    output = tmp_path / "existing.csv"
    output.write_text("existing", encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(output), ""))
    owner.export_button.click()
    search = owner._result_transfer._search
    search.timer.stop()  # Deliberately hold the GUI acknowledgement, not formatting.
    worker = search.worker_thread
    worker.join(timeout=3)
    assert not worker.is_alive() and search._completed is not None
    assert list(tmp_path.glob(".rng-export-*.tmp"))
    owner.export_button.click()
    assert output.read_text(encoding="utf-8") == "existing"
    assert not list(tmp_path.glob(".rng-export-*.tmp"))


@pytest.mark.parametrize("invalidate", ["cancel", "close", "destroy"])
def test_accepted_file_commit_cannot_be_revoked_or_lose_its_terminal_state(window, monkeypatch, tmp_path, invalidate):
    from shiboken6 import delete

    gate, entered = threading.Event(), threading.Event()
    original = result_transfer.PreparedExport.commit
    main_thread = threading.get_ident()

    def commit(staged, cancellation=None):
        assert threading.get_ident() != main_thread
        entered.set()
        assert gate.wait(5)
        return original(staged, cancellation)

    monkeypatch.setattr(result_transfer.PreparedExport, "commit", commit)
    output = tmp_path / "commit.csv"
    output.write_text("existing", encoding="utf-8")
    if invalidate == "destroy":
        owner = QObject(window)
        notices = []
        transfer = ResultTransfer(owner, changed=lambda: None, copied=lambda *args: None,
                                  exported=notices.append, failed=notices.append)
        transfer.export(lambda: ((str(index),) for index in range(2001)), 2001, output)
    else:
        owner = _setup(window, "static", 2001)
        transfer = owner._result_transfer
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(output), ""))
        owner.export_button.click()
    try:
        wait_until(entered.is_set)
        token = transfer._search._active_token
        assert transfer.committing
        if invalidate == "cancel":
            assert not owner.copy_button.isEnabled() and not owner.export_button.isEnabled()
            assert not transfer.cancel(preserve_commit_notice=True)
            assert not transfer.close()
        elif invalidate == "close":
            window.close()
            assert window.isVisible() and not window._close_accepted
        else:
            delete(owner)
            assert transfer._orphaned
        assert not token.is_cancelled()
        gate.set()
        wait_until(lambda: transfer.last_outcome is not None)
        assert transfer.last_outcome == ("exported", output)
        assert output.read_text(encoding="utf-8") != "existing"
        assert not list(tmp_path.glob(".rng-export-*.tmp"))
        if invalidate == "close":
            wait_until(lambda: window._close_accepted)
        elif invalidate == "destroy":
            assert transfer._closed and not notices
    finally:
        gate.set()


def test_failed_preparation_and_failed_commit_preserve_target_and_restore_buttons(window, monkeypatch, tmp_path):
    owner = _setup(window, "static", 2001)
    output = tmp_path / "existing.csv"
    output.write_text("existing", encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(output), ""))
    errors = []
    monkeypatch.setattr(owner, "_show_error", lambda *args: errors.append(args))
    original = owner._result_export_snapshot

    def broken_snapshot():
        def rows():
            yield ("Adv",)
            yield ("1",)
            raise OSError("format failure")
        return rows, 2001

    monkeypatch.setattr(owner, "_result_export_snapshot", broken_snapshot)
    owner.export_button.click()
    _wait_transfer(owner)
    assert len(errors) == 1 and output.read_text(encoding="utf-8") == "existing"
    monkeypatch.setattr(owner, "_result_export_snapshot", original)
    monkeypatch.setattr(result_transfer.os, "replace", lambda *args: (_ for _ in ()).throw(OSError("commit failure")))
    owner.export_button.click()
    _wait_transfer(owner)
    assert len(errors) == 2 and output.read_text(encoding="utf-8") == "existing"
    assert owner.copy_button.isEnabled() and owner.export_button.isEnabled()
    wait_until(lambda: owner._result_transfer._search.worker_thread is None)
    assert not list(tmp_path.glob(".rng-export-*.tmp"))
