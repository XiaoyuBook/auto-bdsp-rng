from __future__ import annotations

import threading
from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QCoreApplication, QEvent, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from auto_bdsp_rng.ui.history_panel import HistoryPanel
from auto_bdsp_rng.ui.main_window import MainWindow
import auto_bdsp_rng.ui.history_panel as history_module
import auto_bdsp_rng.ui.result_transfer as transfer_module
from tests.test_history_performance import app, finish_archive, state, wait_until
from tests.test_start_readiness import window as readiness_window


@pytest.fixture
def window(app, monkeypatch, tmp_path):
    monkeypatch.setattr(MainWindow, "_start_ocr_warmup", lambda self: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: None)
    fixture = readiness_window.__wrapped__(monkeypatch, tmp_path)
    value = next(fixture)
    yield value
    try:
        next(fixture)
    except StopIteration:
        pass


@pytest.mark.parametrize("storage", ["pending", "fallback", "archived"])
def test_first_last_candidate_pages_are_bounded_and_full_export_keeps_duplicate_order(app, monkeypatch, tmp_path, storage):
    panel = HistoryPanel()
    count = 3100
    source = [replace(state(index), ec=index % 11, pid=index % 11) for index in range(count)]
    flags = [""] * count
    flags[-1] = "sync"
    entered, release = threading.Event(), threading.Event()
    real_store = panel._archive._store_candidate_batch

    def store(*args, **kwargs):
        if storage == "fallback":
            raise OSError("candidate storage unavailable")
        if storage == "pending":
            entered.set()
            assert release.wait(8)
        return real_store(*args, **kwargs)

    monkeypatch.setattr(panel._archive, "_store_candidate_batch", store)
    panel.candidates_found(source, count - 1, flags)
    flags[-1] = ""
    panel._archive.start_worker()
    if storage == "pending":
        wait_until(entered.is_set)
    else:
        finish_archive(panel, app)
    gui_thread = threading.get_ident()
    snapshots = []
    real_snapshot = history_module._snapshot_candidate

    def snapshot(candidate):
        if threading.get_ident() == gui_thread:
            snapshots.append(candidate.advances)
        return real_snapshot(candidate)

    monkeypatch.setattr(history_module, "_snapshot_candidate", snapshot)
    table = panel.round_candidate_table
    try:
        panel._archive._cache.clear()
        table._page = 29
        table._next_button.setEnabled(True)
        table._next_button.click()
        assert table.item(0, 0).text() == "3001" and table.item(0, 2).text() == "3000"
        assert table.item(99, 1).text() == "锁定 · 同步 · 同候选3089"
        assert len(snapshots) <= 256
        if storage == "archived":
            assert not snapshots
        elif snapshots:
            assert min(snapshots) >= 2944
        panel._archive._cache.clear()
        snapshots.clear()
        table._page = 1
        table._previous_button.setEnabled(True)
        table._previous_button.click()
        assert table.item(0, 2).text() == "0"
        assert table.item(11, 1).text() == "同候选1"
        assert len(snapshots) <= 128
        path = tmp_path / "complete.txt"
        monkeypatch.setattr(history_module.QFileDialog, "getSaveFileName", lambda *_args: (str(path), ""))
        panel.export_to_file()
        wait_until(lambda: not panel._export_busy)
        text = path.read_text(encoding="utf-8")
        assert text.count("  候选") == count
        assert text.index("候选1 (普通) adv=0 ") < text.index("候选3100 (锁定 · 同步 · 同候选3089) adv=3099 ")
    finally:
        release.set()
    finish_archive(panel, app)


@pytest.mark.parametrize("storage", ["pending", "fallback", "archived"])
def test_reverse_source_pages_keep_frozen_delays_characteristic_and_cross_page_duplicates(app, monkeypatch, storage):
    panel = HistoryPanel()
    first = state(10)
    source = [state(index + 10) for index in range(1500)]
    source[-1] = replace(source[-1], ec=first.ec, pid=first.pid)
    delays = list(range(1500))
    release, entered = threading.Event(), threading.Event()
    real_store = panel._archive._store_candidate_batch

    def store(*args, **kwargs):
        if storage == "fallback":
            raise OSError("candidate storage unavailable")
        if storage == "pending":
            entered.set()
            assert release.wait(5)
        return real_store(*args, **kwargs)

    monkeypatch.setattr(panel._archive, "_store_candidate_batch", store)
    panel.reverse_lookup_results(source, "测试个性", delays)
    delays[-1] = 99999
    source[-1] = state(99999)
    panel._archive.start_worker()
    if storage == "pending":
        wait_until(entered.is_set)
    else:
        finish_archive(panel, app)
    batch = 1
    try:
        candidate, status, delay, characteristic = panel._archive.candidate(batch, 1499)
        assert candidate.advances == 1509
        assert status == "匹配 · 同候选1" and delay == 1499 and characteristic == "测试个性"
        assert panel._archive.candidate(batch, 0)[0].advances == 10
    finally:
        release.set()
    finish_archive(panel, app)


def _begin_export(panel, monkeypatch, destination):
    panel.candidates_found([state(index) for index in range(1500)], 0)
    monkeypatch.setattr(history_module.QFileDialog, "getSaveFileName", lambda *_args: (str(destination), ""))
    panel.export_to_file()


def test_history_export_snapshot_is_fixed_before_the_file_dialog(app, monkeypatch, tmp_path):
    panel = HistoryPanel()
    panel.candidates_found([state(index) for index in range(1500)], 0)
    finish_archive(panel, app)
    path = tmp_path / "clicked-snapshot.txt"

    def dialog(*_args):
        panel._ts("after-dialog-opened")
        return str(path), ""

    monkeypatch.setattr(history_module.QFileDialog, "getSaveFileName", dialog)
    panel.export_to_file()
    wait_until(lambda: not panel._export_busy)
    text = path.read_text(encoding="utf-8")
    assert text.count("  候选") == 1500 and "after-dialog-opened" not in text
    assert "after-dialog-opened" in panel._complete_plain_text()


@pytest.mark.parametrize("action", ["clear", "shutdown"])
def test_file_dialog_return_cannot_revive_a_cleared_or_closed_history_export(app, monkeypatch, tmp_path, action):
    panel = HistoryPanel()
    panel.candidates_found([state(index) for index in range(1500)], 0)
    finish_archive(panel, app)
    path = tmp_path / "existing.txt"
    path.write_text("previous export", encoding="utf-8")

    def dialog(*_args):
        getattr(panel, action)()
        return str(path), ""

    monkeypatch.setattr(history_module.QFileDialog, "getSaveFileName", dialog)
    panel.export_to_file()
    assert not panel._export_busy and panel._export_work.worker_thread is None
    assert path.read_text(encoding="utf-8") == "previous export"
    assert not list(tmp_path.glob(".history-export-*.tmp"))


@pytest.mark.parametrize("action", ["clear", "destroy"])
@pytest.mark.parametrize("failure", [False, True])
def test_accepted_history_commit_keeps_owner_until_success_or_failure(app, monkeypatch, tmp_path, action, failure):
    panel = HistoryPanel()
    destination = tmp_path / "existing.txt"
    destination.write_text("previous export", encoding="utf-8")
    entered, release = threading.Event(), threading.Event()
    real_replace = transfer_module.os.replace

    def blocked_replace(*args):
        entered.set()
        assert release.wait(5)
        if failure:
            raise OSError("replace failed")
        return real_replace(*args)

    monkeypatch.setattr(transfer_module.os, "replace", blocked_replace)
    _begin_export(panel, monkeypatch, destination)
    wait_until(entered.is_set)
    transfer = panel._export_transfer
    token = transfer._search._active_token
    try:
        assert transfer.committing and not panel.export_button.isEnabled()
        assert not panel.copy_button.isEnabled() and not panel.copy_round_button.isEnabled()
        assert panel.export_button.text() == "正在保存…"
        if action == "clear":
            panel.clear()
            panel.auto_tid_log("new history")
            panel.export_to_file()
            panel.copy_all()
            assert panel._export_busy and not panel.copy_button.isEnabled()
        else:
            panel.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            assert transfer._orphaned
        assert not token.is_cancelled()
        assert destination.read_text(encoding="utf-8") == "previous export"
    finally:
        release.set()
    wait_until(lambda: not transfer.committing)
    assert transfer.last_outcome[0] == ("failed" if failure else "exported")
    text = destination.read_text(encoding="utf-8")
    assert text == "previous export" if failure else text.count("  候选") == 1500
    assert not list(tmp_path.glob(".history-export-*.tmp"))
    if action == "clear":
        assert "历史导出未完成" in panel.archive_notice.text() if failure else "历史已导出" in panel.archive_notice.text()
        assert panel.export_button.isEnabled()
    else:
        assert transfer._closed


@pytest.mark.parametrize("operation", ["search", "copy", "export"])
def test_accepted_main_close_cancels_history_preparation_without_waiting_for_worker(window, app, monkeypatch, tmp_path, operation):
    panel = window.history_tab
    panel.candidates_found([state(index) for index in range(1500)], 0)
    finish_archive(panel, app)
    destination = tmp_path / "existing.txt"
    destination.write_text("previous export", encoding="utf-8")
    QApplication.clipboard().setText("previous clipboard")
    entered, release = threading.Event(), threading.Event()
    real_lines = history_module._plain_lines

    def blocked_lines(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        yield from real_lines(*args, **kwargs)

    monkeypatch.setattr(history_module, "_plain_lines", blocked_lines)
    if operation == "search":
        controller = panel._history_search
        panel.search_edit.setText("missing-search")
    elif operation == "copy":
        controller = panel._copy_work
        panel.copy_all()
    else:
        controller = panel._export_work
        monkeypatch.setattr(history_module.QFileDialog, "getSaveFileName", lambda *_args: (str(destination), ""))
        panel.export_to_file()
    wait_until(entered.is_set)
    token = controller._active_token
    previous_quit = app.quitOnLastWindowClosed()
    app.setQuitOnLastWindowClosed(False)
    try:
        window.close()
        wait_until(lambda: window._close_accepted)
        assert token.is_cancelled()
        assert panel._archive._closed and panel._history_search._closed and panel._copy_work._closed
        assert panel._export_transfer._closed
        assert controller.worker_thread is not None
    finally:
        release.set()
        app.setQuitOnLastWindowClosed(previous_quit)
    wait_until(lambda: controller.worker_thread is None)
    assert destination.read_text(encoding="utf-8") == "previous export"
    assert QApplication.clipboard().text() == "previous clipboard"
    assert not list(tmp_path.glob(".history-export-*.tmp"))


@pytest.mark.parametrize("reject_close", [False, True])
def test_main_close_waits_for_accepted_history_commit_and_rejected_close_preserves_notice(window, app, monkeypatch, tmp_path, reject_close):
    panel = window.history_tab
    destination = tmp_path / "existing.txt"
    destination.write_text("previous export", encoding="utf-8")
    entered, release = threading.Event(), threading.Event()
    real_replace = transfer_module.os.replace

    def blocked_replace(*args):
        entered.set()
        assert release.wait(5)
        return real_replace(*args)

    monkeypatch.setattr(transfer_module.os, "replace", blocked_replace)
    _begin_export(panel, monkeypatch, destination)
    wait_until(entered.is_set)
    transfer = panel._export_transfer
    token = transfer._search._active_token
    beats = []
    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda: beats.append(True))
    timer.start()
    previous_quit = app.quitOnLastWindowClosed()
    app.setQuitOnLastWindowClosed(False)
    try:
        assert not window.close()
        assert not window._close_accepted and window._close_pending
        assert not token.is_cancelled() and not transfer._closed
        wait_until(lambda: len(beats) >= 4)
        if reject_close:
            window._close_deadline = 0
            window._poll_confirmed_close()
            assert not window._is_closing and not panel._closing_requested
            assert not transfer._ignore_completion
        release.set()
        wait_until(lambda: not transfer.committing)
        if reject_close:
            assert not window._close_accepted and panel._archive._accepting
            assert "历史已导出" in panel.archive_notice.text()
            window.close()
        wait_until(lambda: window._close_accepted)
    finally:
        release.set()
        timer.stop()
        app.setQuitOnLastWindowClosed(previous_quit)
    assert transfer.last_outcome == ("exported", Path(destination))
    assert destination.read_text(encoding="utf-8").count("  候选") == 1500
    assert panel._archive._closed and not list(tmp_path.glob(".history-export-*.tmp"))
