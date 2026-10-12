from __future__ import annotations

import sqlite3
import gc
import threading
import time
import weakref
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QEvent, QObject, Qt, QTimer, Signal, Slot
from PySide6.QtWidgets import QApplication

from auto_bdsp_rng.gen8_static.models import State8
from auto_bdsp_rng.ui.auto_rng_panel import AutoRngPanel
from auto_bdsp_rng.ui.history_archive import MAX_PENDING_CANDIDATE_BATCHES, HistoryArchive, ReservedHistoryEvent, publish_history_event
from auto_bdsp_rng.ui.history_panel import CANDIDATE_PAGE_SIZE, FEED_PAGE_SIZE, ROUND_PAGE_SIZE, HistoryPanel
from auto_bdsp_rng.ui.run_log_panel import MAX_LOG_ENTRIES
import auto_bdsp_rng.ui.history_panel as history_module
from tests.ui_wait import close_all_widgets


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    application = QApplication.instance() or QApplication([])
    yield application
    close_all_widgets(application)


def state(index: int) -> State8:
    return State8(index, index, 0, index, (31,) * 6, 0, 0, 50, 0, 0, 0, 0)


def wait_until(predicate, timeout_ms=5000):
    # QTest.qWait can keep the GIL in this Windows/PySide build. Give Python
    # workers CPU time, as the application's normal Qt event loop does.
    deadline = time.monotonic() + timeout_ms / 1000
    application = QApplication.instance()
    while not predicate() and time.monotonic() < deadline:
        application.processEvents()
        time.sleep(0.003)
    assert predicate(), "history operation did not finish before timeout"


def finish_archive(panel: HistoryPanel, app, timeout: float = 30) -> None:
    panel._archive.start_worker()
    deadline = time.monotonic() + timeout
    while (panel._archive._pending or panel._archive._reservations) and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    assert not panel._archive._pending
    assert not panel._archive._reservations


def finish_search(panel: HistoryPanel) -> None:
    wait_until(lambda: not panel._search_inflight and not panel._filter_start_timer.isActive(), timeout_ms=20_000)


def test_thousand_rounds_use_one_page_and_update_only_the_existing_row(app, monkeypatch):
    panel = HistoryPanel()
    panel.begin_run("scale", "帝牙卢卡")
    for index in range(1000):
        panel.cycle_start(index + 1)
        if index % 50 == 0:
            app.processEvents()
    assert panel.round_list.count() == ROUND_PAGE_SIZE
    assert len(panel._round_records.cache) <= 64
    items = tuple(panel.round_list.item(index) for index in range(ROUND_PAGE_SIZE))
    rows = tuple(panel.round_list.itemWidget(item) for item in items)
    monkeypatch.setattr(panel.text_view, "toPlainText", lambda: pytest.fail("routine updates must not copy full history"))
    monkeypatch.setattr(panel, "_refresh_round_list", lambda *_args, **_kwargs: pytest.fail("routine updates must not rebuild all rounds"))
    panel.seed_captured("updated-seed", 1, 0, 10)
    assert tuple(panel.round_list.item(index) for index in range(ROUND_PAGE_SIZE)) == items
    assert tuple(panel.round_list.itemWidget(item) for item in items) == rows
    assert panel.round_page_label.text() == "1 / 10"
    panel.round_next_button.click()
    assert panel.selected_round_id == 900
    panel.round_previous_button.click()
    assert panel.selected_round_id == 1000
    # A round outside the bounded summary cache reloads complete metadata and
    # event payloads when selected or copied.
    first = panel._round_records[panel._group_key("scale", 1)]
    assert first.cycle_index == 1 and len(first.entries) == 4
    assert "第 1 轮" in "\n".join(first.plain_lines)


def test_large_candidate_pages_format_only_visible_rows_and_export_every_result(app, monkeypatch, tmp_path):
    panel = HistoryPanel()
    panel.begin_run("scale", "帝牙卢卡")
    panel.cycle_start(1)
    calls = []
    real_row = HistoryPanel._candidate_row
    monkeypatch.setattr(HistoryPanel, "_candidate_row", staticmethod(lambda *args: calls.append(args[0]) or real_row(*args)))
    panel.candidates_found([state(index) for index in range(5000)], 0)
    assert panel.round_candidate_table.rowCount() == CANDIDATE_PAGE_SIZE
    assert len(calls) <= 2 * CANDIDATE_PAGE_SIZE
    assert panel.round_candidate_table.item(0, 1).text() == "锁定"
    panel.round_candidate_table._next_button.click()
    assert panel.round_candidate_table.item(0, 0).text() == "101"
    assert panel.round_candidate_table.item(0, 2).text() == "100"
    finish_archive(panel, app)
    assert len(panel._archive._cache) <= 128
    path = tmp_path / "complete.txt"
    monkeypatch.setattr(history_module.QFileDialog, "getSaveFileName", lambda *_args: (str(path), ""))
    monkeypatch.setattr(panel.text_view, "toPlainText", lambda: pytest.fail("export must stream the archive"))
    panel.export_to_file()
    wait_until(lambda: not panel._export_busy)
    text = path.read_text(encoding="utf-8")
    assert text.count("  候选") == 5000
    assert "候选5000 (普通) adv=4999" in text
    assert text.index("候选1 (") < text.index("候选5000 (")
    assert panel._complete_plain_text() == text


def test_mutable_sources_and_reverse_metadata_are_frozen_and_preserve_duplicate_tags(app):
    panel = HistoryPanel()
    source = SimpleNamespace(advances=10, pid=15, ec=16, ivs=[31] * 6, nature=3)
    flags = ["sync", ""]
    panel.candidates_found([source, source], 1, flags)
    source.advances = 999
    source.ivs[0] = 0
    flags[0] = ""
    assert panel.round_candidate_table.item(0, 2).text() == "10"
    assert panel.round_candidate_table.item(0, 5).text() == "31"
    assert panel.round_candidate_table.item(0, 1).text() == "同步"
    assert panel.round_candidate_table.item(1, 1).text() == "锁定 · 同候选1"
    source.advances = 20
    delays = [7]
    panel.reverse_lookup_results([source], characteristic="测试个性", delays=delays)
    delays[0] = 999
    source.advances = 1000
    text = panel.text_view.toPlainText()
    assert "反查候选1: adv=20 delay=7" in text
    assert "个性=测试个性" in text
    assert "adv=1000" not in text


def test_long_feed_is_paged_and_old_text_is_still_searchable_and_copyable(app):
    panel = HistoryPanel()
    panel.begin_run("tid", "自动 TID")
    for index in range(FEED_PAGE_SIZE * 3):
        panel.auto_tid_log(f"消息 {index}")
    assert panel._feed_layout.count() == FEED_PAGE_SIZE
    assert len(panel._selected_record().entries) == FEED_PAGE_SIZE * 3
    panel.feed_previous_button.click()
    assert panel._feed_start == FEED_PAGE_SIZE
    panel.feed_previous_button.click()
    assert panel._feed_start == 0
    panel.search_edit.setText("消息 0")
    finish_search(panel)
    assert panel.round_list.count() == 1
    panel.copy_selected_round()
    copied = QApplication.clipboard().text()
    assert "消息 0\n" in copied and "消息 299\n" in copied


def test_hidden_auto_rng_log_mirror_has_a_hard_cap_but_sink_receives_complete_event(app):
    received = []
    panel = AutoRngPanel(run_log_sink=lambda level, text: received.append((level, text)))
    message = "\n".join(f"line {index}" for index in range(MAX_LOG_ENTRIES + 5))
    panel.add_log(message)
    assert panel.log_view.document().maximumBlockCount() == MAX_LOG_ENTRIES
    assert panel.log_view.document().blockCount() <= MAX_LOG_ENTRIES
    assert "line 0\n" not in panel.log_view.toPlainText()
    assert f"line {MAX_LOG_ENTRIES + 4}" in panel.log_view.toPlainText()
    assert received == [("INFO", message)]


def test_archive_io_failure_recovers_all_previous_payloads_before_retrying():
    notices = []
    archive = HistoryArchive(notices.append)
    archive.append("plain", 1, ("text", "before"))
    real_db = archive._db

    class FailingDatabase:
        def execute(self, *_args, **_kwargs):
            raise sqlite3.OperationalError("disk unavailable")

        def rollback(self):
            real_db.rollback()

        def backup(self, recovered):
            real_db.backup(recovered)

        def close(self):
            real_db.close()

    archive._db = FailingDatabase()
    archive.append("plain", 1, ("text", "after"))
    assert archive.in_memory_fallback
    assert list(archive.values("plain")) == [("text", "before"), ("text", "after")]
    assert len(notices) == 1
    archive.close()
    archive.close()


@pytest.mark.parametrize("failed_write", ["plain", "feed", "round", "candidate"])
def test_real_sqlite_full_preserves_committed_history_and_retries_the_entire_write(failed_write):
    notices = []
    archive = HistoryArchive(notices.append)
    archive.append("plain", 1, ("text", "before"))
    archive.append("feed", 1, ("feed-before",))
    archive.store_record(1, {"marker": "before"})
    old_candidates = [("before", index) for index in range(130)]
    archive._store_candidate_batch(1, lambda: iter(old_candidates))
    target = archive._candidate_db if failed_write == "candidate" else archive._db
    page_count = target.execute("PRAGMA page_count").fetchone()[0]
    assert target.execute(f"PRAGMA max_page_count={page_count}").fetchone()[0] == page_count
    large = "x" * 100_000
    new_candidates = [("after" + "x" * 1000, index) for index in range(260)]
    if failed_write in {"plain", "feed"}:
        archive.append(failed_write, 2, ("text", large))
    elif failed_write == "round":
        archive.store_record(2, {"marker": large})
    else:
        archive._store_candidate_batch(2, lambda: iter(new_candidates))
    assert archive.in_memory_fallback and len(notices) == 1
    assert "完整记录已转为内存保存" in notices[0]
    assert list(archive.values("plain")) == [("text", "before")] + ([("text", large)] if failed_write == "plain" else [])
    assert list(archive.values("feed")) == [("feed-before",)] + ([("text", large)] if failed_write == "feed" else [])
    assert archive.record(1) == {"marker": "before"}
    if failed_write == "round":
        assert archive.record(2) == {"marker": large}
    assert list(archive.candidate_rows(1)) == old_candidates
    if failed_write == "candidate":
        assert list(archive.candidate_rows(2)) == new_candidates
    for db in (archive._db, archive._candidate_db):
        assert not db.in_transaction
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    archive.close()


def test_real_candidate_sqlite_full_keeps_ordered_full_copy_and_export(app, monkeypatch, tmp_path):
    panel = HistoryPanel()
    panel.candidates_found([state(index) for index in range(1500)], 0)
    finish_archive(panel, app)
    db = panel._archive._candidate_db
    page_count = db.execute("PRAGMA page_count").fetchone()[0]
    db.execute(f"PRAGMA max_page_count={page_count}")
    panel.candidates_refiltered([state(10_000 + index) for index in range(1500)], 1)
    finish_archive(panel, app)
    app.processEvents()
    assert panel.archive_notice.text() and panel._archive.in_memory_fallback
    panel.copy_selected_round()
    wait_until(lambda: not panel._copy_busy)
    copied = QApplication.clipboard().text()
    assert copied.count("  候选") == 3000
    assert copied.index("adv=1499 ") < copied.index("adv=10000 ") < copied.index("adv=11499 ")
    path = tmp_path / "recovered.txt"
    monkeypatch.setattr(history_module.QFileDialog, "getSaveFileName", lambda *_args: (str(path), ""))
    panel.export_to_file()
    wait_until(lambda: not panel._export_busy)
    assert path.read_text(encoding="utf-8") == copied == panel._complete_plain_text()


def test_temporary_database_creation_failure_falls_back_before_losing_records(monkeypatch):
    real_connect = sqlite3.connect

    class UnavailableTemporaryDatabase:
        def executescript(self, _schema):
            raise sqlite3.OperationalError("temporary disk unavailable")

        def close(self):
            pass

    monkeypatch.setattr(sqlite3, "connect", lambda name, **kwargs: UnavailableTemporaryDatabase() if name == "" else real_connect(name, **kwargs))
    archive = HistoryArchive()
    assert archive.in_memory_fallback
    archive.append("plain", 1, ("text", "complete"))
    assert list(archive.values("plain")) == [("text", "complete")]
    archive.close()


def test_candidate_temporary_directory_failure_keeps_a_readable_memory_store(monkeypatch):
    import auto_bdsp_rng.ui.history_archive as archive_module
    monkeypatch.setattr(archive_module.tempfile, "TemporaryDirectory",
                        lambda **_kwargs: (_ for _ in ()).throw(OSError("temporary disk unavailable")))
    archive = HistoryArchive()
    rows = [("candidate", index) for index in range(260)]
    archive._store_candidate_batch(1, lambda: iter(rows))
    assert archive.in_memory_fallback
    assert list(archive.candidate_rows(1)) == rows
    assert archive.candidate(1, 259) == rows[-1]
    archive.close()


@pytest.mark.parametrize("candidates", [False, True])
def test_non_sqlite_write_failure_rolls_back_before_the_next_transaction(candidates):
    archive = HistoryArchive()

    def fail_after_write(db):
        db.execute("INSERT INTO round_record VALUES (?, ?)", (1, b"uncommitted"))
        raise ValueError("bad serialization")

    with pytest.raises(ValueError, match="bad serialization"):
        archive._perform(fail_after_write, write=True, candidates=candidates)
    db = archive._candidate_db if candidates else archive._db
    assert not db.in_transaction
    assert db.execute("SELECT COUNT(*) FROM round_record").fetchone() == (0,)
    archive._perform(lambda db: db.execute("INSERT INTO round_record VALUES (?, ?)", (2, b"committed")),
                     write=True, candidates=candidates)
    assert db.execute("SELECT COUNT(*) FROM round_record").fetchone() == (1,)
    archive.close()


def test_old_candidate_page_and_full_reader_do_not_wait_on_a_new_write_transaction(app, monkeypatch):
    panel = HistoryPanel()
    panel.begin_run("slow-disk", "自动定点")
    panel.cycle_start(1)
    panel.candidates_found([state(index) for index in range(1500)], 0)
    finish_archive(panel, app)
    real_db = panel._archive._candidate_db
    writing, release = threading.Event(), threading.Event()

    class HeldTransactionDatabase:
        def __getattr__(self, name):
            return getattr(real_db, name)

        def executemany(self, *args):
            result = real_db.executemany(*args)
            if not writing.is_set():
                writing.set()
                assert release.wait(3)
            return result

    panel._archive._candidate_db = HeldTransactionDatabase()
    panel.cycle_start(2)
    panel.candidates_found([state(10_000 + index) for index in range(1500)], 0)
    panel._archive.start_worker()
    wait_until(writing.is_set)
    try:
        panel._archive._cache.clear()
        started = time.perf_counter()
        panel.round_list.setCurrentItem(panel._round_items[1])
        panel.round_candidate_table._next_button.click()
        assert panel.round_candidate_table.item(0, 2).text() == "100"
        assert list(panel._archive.candidate_rows(1))[-1][0].advances == 1499
        assert time.perf_counter() - started < 0.2
        assert not release.is_set() and panel._archive._pending
    finally:
        release.set()
    finish_archive(panel, app)


def test_clear_timeout_preserves_old_text_and_unwritten_candidate_suffix(app, monkeypatch):
    panel = HistoryPanel()
    real_db = panel._archive._candidate_db
    writing, release = threading.Event(), threading.Event()

    class HeldTransactionDatabase:
        def __getattr__(self, name):
            return getattr(real_db, name)

        def executemany(self, *args):
            result = real_db.executemany(*args)
            if not writing.is_set():
                writing.set()
                assert release.wait(3)
            return result

    panel._archive._candidate_db = HeldTransactionDatabase()
    panel.candidates_found([state(index) for index in range(1500)], 0)
    panel.candidates_refiltered([state(10_000 + index) for index in range(1500)], 0)
    panel._archive.start_worker()
    wait_until(writing.is_set)
    before = panel._complete_plain_text()
    try:
        started = time.perf_counter()
        panel.clear()
        assert time.perf_counter() - started < 0.8
        assert "请稍后重试清空" in panel.archive_notice.text()
        assert panel._complete_plain_text() == before
        assert panel._candidate_count == 3000 and len(panel._archive._reservations) == 2
        assert panel.copy_button.isEnabled() and panel.export_button.isEnabled()
    finally:
        release.set()
    finish_archive(panel, app)
    assert panel._complete_plain_text() == before
    assert panel._archive.candidate(1, 1499)[0].advances == 1499
    assert panel._archive.candidate(2, 1499)[0].advances == 11499
    panel.clear()
    assert panel._complete_plain_text() == "" and not panel._archive._reservations


def test_rejected_clear_retains_source_when_the_writer_already_stopped_mid_batch(app, monkeypatch):
    panel = HistoryPanel()
    archive = panel._archive
    real_store = archive._store_candidate_batch
    writing, release = threading.Event(), threading.Event()

    def stopped_mid_batch(batch, factory, **options):
        if batch != 1:
            return real_store(batch, factory, **options)
        archive._store_candidate_batch = real_store
        assert real_store(batch, lambda: iter(tuple(factory())[:128]), **options)
        writing.set()
        assert archive._stop_event.wait(3)
        assert release.wait(3)
        return False

    monkeypatch.setattr(archive, "_store_candidate_batch", stopped_mid_batch)
    panel.candidates_found([state(index) for index in range(1500)], 0)
    panel._archive.start_worker()
    wait_until(writing.is_set)
    before = panel._complete_plain_text()
    panel.clear()
    assert "请稍后重试清空" in panel.archive_notice.text()
    release.set()
    finish_archive(panel, app)
    assert 1 in archive._fallback_candidates and not archive._pending
    assert panel._complete_plain_text() == before
    assert archive.candidate(1, 1499)[0].advances == 1499
    panel.clear()
    assert panel._complete_plain_text() == "" and not archive._fallback_candidates


def test_failed_background_write_keeps_the_complete_snapshot_fallback(app, monkeypatch):
    panel = HistoryPanel()
    notices = []
    panel.archiveWarning.connect(notices.append)
    monkeypatch.setattr(panel._archive, "_store_candidate_batch", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk unavailable")))
    panel.candidates_found([state(index) for index in range(1500)], 0)
    panel._archive.start_worker()
    deadline = time.monotonic() + 2
    while not notices and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    assert notices and "完整快照仍保存在内存" in notices[0]
    text = panel.text_view.toPlainText()
    assert text.count("  候选") == 1500
    assert "adv=1499" in text
    panel._archive.close()
    assert panel._archive._worker is None or not panel._archive._worker.is_alive()


def test_clear_and_destruction_stop_pending_archive_and_release_frozen_sources(app):
    panel = HistoryPanel()
    panel.candidates_found([state(index) for index in range(5000)], 0)
    archive = panel._archive
    panel.clear()
    assert not archive._pending and not archive._cache
    assert panel.text_view.toPlainText() == ""
    panel.candidates_found([state(index) for index in range(2000)], 0)
    panel._archive.start_worker()
    panel.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert archive._closed
    assert not archive._worker.is_alive()


def test_three_100k_bursts_and_complete_search_copy_keep_the_qt_heartbeat(app, monkeypatch, request):
    panel = HistoryPanel()
    archive = panel._archive
    source = [state(index) for index in range(100_000)]
    gui_thread = threading.get_ident()
    write_threads = []
    real_db = archive._candidate_db

    class SlowCandidateDatabase:
        def __getattr__(self, name):
            return getattr(real_db, name)

        def executemany(self, *args):
            write_threads.append(threading.get_ident())
            time.sleep(0.002)
            return real_db.executemany(*args)

    archive._candidate_db = SlowCandidateDatabase()
    durations, gaps, source_refs = [], [], []
    preparation_threads, preparation_durations, errors = [], [], []
    cancel_producer = threading.Event()
    real_prepare = archive._prepare_candidates

    def prepare(*args, **kwargs):
        started = time.perf_counter()
        preparation_threads.append(threading.get_ident())
        result = real_prepare(*args, **kwargs)
        preparation_durations.append(time.perf_counter() - started)
        return result

    monkeypatch.setattr(archive, "_prepare_candidates", prepare)
    last_beat = [time.perf_counter()]
    peaks = {"pending": 0, "reserved": 0, "queued": 0, "cache": 0}

    def heartbeat():
        now = time.perf_counter()
        gaps.append(now - last_beat[0])
        last_beat[0] = now
        peaks["pending"] = max(peaks["pending"], len(archive._pending))
        peaks["reserved"] = max(peaks["reserved"], len(archive._reservations))
        peaks["queued"] = max(peaks["queued"], archive._jobs.qsize())
        peaks["cache"] = max(peaks["cache"], len(archive._cache))

    timer = QTimer(panel)
    timer.setInterval(10)
    timer.timeout.connect(heartbeat)
    timer.start()

    class CandidateReceiver(QObject):
        event = Signal(str, object)

        @Slot(str, object)
        def receive(self, event, payload):
            started = time.perf_counter()
            try:
                assert threading.get_ident() == gui_thread
                assert isinstance(payload, ReservedHistoryEvent)
                prepared = payload.reservation.prepared_rows
                assert prepared is not None
                candidates, locked_index = payload.values
                operation = panel.candidates_found if event == "candidates_found" else panel.candidates_refiltered
                assert operation(list(candidates), locked_index, reservation=payload.reservation)
                assert archive._pending[locked_index + 1][0] is prepared
                source_refs.append(weakref.ref(prepared))
                durations.append(time.perf_counter() - started)
            except BaseException as error:
                errors.append(error)
                cancel_producer.set()
            finally:
                payload.reservation.release_unclaimed()

    receiver = CandidateReceiver(panel)
    receiver.event.connect(receiver.receive, Qt.ConnectionType.QueuedConnection)
    emit = receiver.event.emit

    def produce():
        try:
            for index in range(3):
                event = "candidates_found" if index == 0 else "candidates_refiltered"
                publish_history_event(archive, emit, event, (source, index), cancelled=cancel_producer.is_set)
        except BaseException as error:
            errors.append(error)

    # The actual producer reserves and freezes before Qt emission. Three
    # direct zero-timer calls would instead freeze all 300k on the GUI in one
    # event pass, bypassing the production preparation path.
    producer = threading.Thread(target=produce, name="History pressure producer")
    producer.start()
    try:
        wait_until(lambda: len(durations) == 3 or errors)
        assert not errors, errors
        wait_until(lambda: not producer.is_alive())
    finally:
        cancel_producer.set()
        producer.join(1)
    assert not producer.is_alive() and not errors, errors
    assert len(preparation_threads) == 3 and gui_thread not in preparation_threads
    finish_archive(panel, app)
    assert len(write_threads) and gui_thread not in write_threads
    assert max(durations) < 0.25, durations
    assert peaks["pending"] <= MAX_PENDING_CANDIDATE_BATCHES
    assert peaks["reserved"] <= MAX_PENDING_CANDIDATE_BATCHES
    assert peaks["queued"] <= MAX_PENDING_CANDIDATE_BATCHES
    assert peaks["cache"] <= 128
    gc.collect()
    assert all(reference() is None for reference in source_refs)
    for batch in range(1, 4):
        count = panel._archive._perform(lambda db: db.execute("SELECT COUNT(*) FROM candidate WHERE batch=?", (batch,)).fetchone()[0], candidates=True)
        assert count == 100_000
        assert panel._archive.candidate(batch, 99_999)[0].advances == 99_999

    started = time.perf_counter()
    panel.search_edit.setText("does-not-exist-in-300000-candidates")
    assert time.perf_counter() - started < 0.15
    finish_search(panel)
    assert panel.round_list.count() == 0
    # An unchanged candidate prefix is never re-formatted just because a
    # filtered round receives a metadata/log update.
    calls = []
    real_plain_lines = history_module._plain_lines
    monkeypatch.setattr(history_module, "_plain_lines", lambda *args, **kwargs: calls.append(kwargs.get("after_seq", 0)) or real_plain_lines(*args, **kwargs))
    previous_boundary = panel._archive.plain_snapshot().through_seq
    panel._ts("new processing text")
    panel._touch_record()
    finish_search(panel)
    assert calls == [previous_boundary]
    panel.search_edit.clear()
    started = time.perf_counter()
    panel.copy_selected_round()
    assert time.perf_counter() - started < 0.15
    wait_until(lambda: not panel._copy_busy, timeout_ms=20_000)
    # Completion can return before an overdue timer runs. Record the next
    # real heartbeat so the final GUI clipboard callback is included.
    copy_complete_beats = len(gaps)
    wait_until(lambda: len(gaps) > copy_complete_beats)
    timer.stop()
    copied = QApplication.clipboard().text()
    assert copied.count("  候选") == 300_000
    assert copied.count("候选100000 (普通) adv=99999 ") == 3
    assert copied.endswith("new processing text\n")
    request.node.user_properties.extend([
        ("history_gc_enabled", gc.isenabled()),
        ("history_heartbeat_max_ms", max(gaps) * 1000),
        ("history_heartbeat_first_ms", gaps[0] * 1000),
        ("history_heartbeat_last_ms", gaps[-1] * 1000),
        ("history_heartbeat_beats", len(gaps)),
        ("history_gui_receive_ms", [duration * 1000 for duration in durations]),
        ("history_worker_prepare_ms", [duration * 1000 for duration in preparation_durations]),
        ("history_backlog_peaks", peaks),
    ])
    assert len(gaps) > 50 and max(gaps) < 0.25, max(gaps)


@pytest.mark.parametrize("action", ["latest", "clear-query", "clear-history", "destroy"])
def test_late_archive_search_cannot_replace_a_new_query_or_cleared_destroyed_panel(app, monkeypatch, action):
    panel = HistoryPanel()
    panel.candidates_found([state(index) for index in range(2000)], 0)
    finish_archive(panel, app)
    started, release = threading.Event(), threading.Event()
    real_plain_lines = history_module._plain_lines

    def delayed_lines(*args, **kwargs):
        if not started.is_set():
            started.set()
            assert release.wait(3)
        yield from real_plain_lines(*args, **kwargs)

    monkeypatch.setattr(history_module, "_plain_lines", delayed_lines)
    panel.search_edit.setText("old-no-match")
    wait_until(started.is_set)
    search = panel._history_search
    if action == "latest":
        panel.search_edit.setText("adv=1999 ")
    elif action == "clear-query":
        panel.search_edit.clear()
    elif action == "clear-history":
        panel.clear()
        panel.auto_tid_log("new record")
    else:
        panel.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    release.set()
    if action == "destroy":
        wait_until(lambda: search.worker_thread is None)
        assert search._closed and search._callbacks is None
        return
    finish_search(panel)
    assert "未完成" not in panel.archive_notice.text()
    if action in {"latest", "clear-query"}:
        assert panel._visible_uids == [1] and panel._selected_uid == 1
        assert panel.round_candidate_table.item(0, 2).text() == "0"
        # The hit is on a page which was never rendered.
        assert panel.round_candidate_table.rowCount() == CANDIDATE_PAGE_SIZE
    else:
        assert panel.round_list.count() == 0
        assert "new record" in panel._complete_plain_text()
        assert "adv=1999" not in panel._complete_plain_text()


def test_search_snapshot_observes_new_text_and_preserves_the_chosen_round(app, monkeypatch):
    panel = HistoryPanel()
    panel.begin_run("selection", "selection")
    panel.cycle_start(1)
    panel.candidates_found([state(index) for index in range(2000)], 0)
    panel.cycle_start(2)
    panel.candidates_found([state(10_000 + index) for index in range(2000)], 0)
    finish_archive(panel, app)
    panel.round_list.setCurrentItem(panel._round_items[1])
    assert panel._selected_uid == 1
    panel.search_edit.setText("adv=1999 ")
    finish_search(panel)
    assert panel._visible_uids == [1] and panel._selected_uid == 1
    panel.search_edit.clear()
    assert panel._selected_uid == 1
    started, release = threading.Event(), threading.Event()
    real_plain_lines = history_module._plain_lines

    def delayed_lines(*args, **kwargs):
        yield from real_plain_lines(*args, **kwargs)
        if not started.is_set():
            started.set()
            assert release.wait(3)

    monkeypatch.setattr(history_module, "_plain_lines", delayed_lines)
    panel.search_edit.setText("future-processing-needle")
    wait_until(started.is_set)
    panel._ts("future-processing-needle")
    panel._touch_record()
    release.set()
    finish_search(panel)
    assert panel._visible_uids == [2] and panel._selected_uid == 2


def test_async_copy_uses_the_clicked_snapshot_and_only_sets_clipboard_on_gui(app, monkeypatch):
    panel = HistoryPanel()
    panel.candidates_found([state(index) for index in range(2000)], 0)
    finish_archive(panel, app)
    started, release = threading.Event(), threading.Event()
    real_plain_lines = history_module._plain_lines
    gui_thread = threading.get_ident()
    callback_threads = []
    real_copy_finished = panel._copy_finished

    def copy_finished(text):
        callback_threads.append(threading.get_ident())
        real_copy_finished(text)

    def delayed_lines(*args, **kwargs):
        started.set()
        assert release.wait(3)
        yield from real_plain_lines(*args, **kwargs)

    monkeypatch.setattr(panel, "_copy_finished", copy_finished)
    monkeypatch.setattr(history_module, "_plain_lines", delayed_lines)
    panel.copy_selected_round()
    wait_until(started.is_set)
    panel._ts("after-copy-click")
    panel._touch_record()
    release.set()
    wait_until(lambda: not panel._copy_busy)
    assert callback_threads == [gui_thread]
    assert "after-copy-click" not in QApplication.clipboard().text()
    assert "after-copy-click" in panel._complete_plain_text()


@pytest.mark.parametrize("operation", ["copy", "export"])
@pytest.mark.parametrize("action", ["clear", "destroy"])
def test_cancelled_copy_export_preserves_clipboard_and_existing_destination(app, monkeypatch, tmp_path, operation, action):
    panel = HistoryPanel()
    panel.candidates_found([state(index) for index in range(1500)], 0)
    finish_archive(panel, app)
    started, release = threading.Event(), threading.Event()
    real_plain_lines = history_module._plain_lines
    path = tmp_path / "existing.txt"
    path.write_text("previous export", encoding="utf-8")
    QApplication.clipboard().setText("previous clipboard")

    def delayed_lines(*args, **kwargs):
        started.set()
        assert release.wait(3)
        yield from real_plain_lines(*args, **kwargs)

    monkeypatch.setattr(history_module, "_plain_lines", delayed_lines)
    monkeypatch.setattr(history_module.QFileDialog, "getSaveFileName", lambda *_args: (str(path), ""))
    controller = panel._copy_work if operation == "copy" else panel._export_work
    if operation == "copy":
        panel.copy_selected_round()
    else:
        panel.export_to_file()
    wait_until(started.is_set)
    if action == "clear":
        panel.clear()
    else:
        panel.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    release.set()
    wait_until(lambda: controller.worker_thread is None)
    assert QApplication.clipboard().text() == "previous clipboard"
    assert path.read_text(encoding="utf-8") == "previous export"
    assert not list(tmp_path.glob(".history-export-*.tmp"))


def test_fourth_direct_gui_batch_is_rejected_without_mutation_and_can_be_retried(app):
    panel = HistoryPanel()
    for index in range(3):
        assert panel.candidates_found([state(index * 2000 + row) for row in range(1500)], index)
    record = panel._active_record()
    before = (panel._candidate_count, len(record.plain_lines), len(record.entries), record.candidate_count,
              record.locked_advances, record.detail_title, panel._complete_plain_text())
    fourth = [state(10_000 + row) for row in range(1500)]
    assert not panel.candidates_refiltered(fourth, 0)
    after = (panel._candidate_count, len(record.plain_lines), len(record.entries), record.candidate_count,
             record.locked_advances, record.detail_title, panel._complete_plain_text())
    assert before == after
    assert "本次未受理" in panel.archive_notice.text()
    assert len(panel._archive._pending) == 3
    finish_archive(panel, app)
    assert panel.candidates_refiltered(fourth, 0)
    finish_archive(panel, app)
    text = panel._complete_plain_text()
    assert text.count("  候选") == 6000
    assert text.count("候选1 (锁定) adv=10000 ") == 1
    assert text.index("adv=5499 ") < text.index("adv=10000 ")


@pytest.mark.parametrize("cancel", ["stop", "close"])
def test_waiting_producer_is_cancelled_and_unused_event_slots_are_reclaimed(cancel):
    from auto_bdsp_rng.rng_core.cancellation import SearchCancelled
    archive = HistoryArchive()
    held = [archive.reserve_candidate_batch() for _ in range(MAX_PENDING_CANDIDATE_BATCHES)]
    started, stopped = threading.Event(), threading.Event()
    errors = []

    def produce():
        started.set()
        try:
            archive.reserve_candidate_batch(cancelled=stopped.is_set)
        except SearchCancelled:
            errors.append("cancelled")

    producer = threading.Thread(target=produce)
    producer.start()
    assert started.wait(1)
    if cancel == "stop":
        stopped.set()
    else:
        archive.stop_accepting()
    producer.join(0.5)
    assert not producer.is_alive() and errors == ["cancelled"]
    for reservation in held:
        reservation.release()
    archive.resume_accepting()
    reservation = archive.reserve_candidate_batch(wait=False)
    assert reservation is not None
    payload = ReservedHistoryEvent(([state(1)], 0), reservation)
    del payload
    gc.collect()
    assert not archive._reservations
    with pytest.raises(RuntimeError, match="delivery failed"):
        publish_history_event(archive, lambda *_args: (_ for _ in ()).throw(RuntimeError("delivery failed")),
                              "candidates_found", ([state(1)], 0))
    assert not archive._reservations
    archive.close()


@pytest.mark.parametrize("size", [(860, 600), (1150, 900)])
def test_pagination_controls_fit_and_remain_operable_at_workspace_sizes(app, size):
    panel = HistoryPanel()
    panel.begin_run("layout", "自动 TID")
    for index in range(ROUND_PAGE_SIZE + 5):
        panel.cycle_start(index + 1)
    panel.candidates_found([state(index) for index in range(CANDIDATE_PAGE_SIZE + 5)], 0)
    for index in range(FEED_PAGE_SIZE + 5):
        panel.auto_tid_log(f"消息 {index}")
    panel.resize(*size)
    panel.show()
    panel.feed_toggle.setChecked(True)
    for _ in range(3):
        app.processEvents()
    for previous, following in (
        (panel.round_previous_button, panel.round_next_button),
        (panel.round_candidate_table._previous_button, panel.round_candidate_table._next_button),
        (panel.feed_previous_button, panel.feed_next_button),
    ):
        parent = previous.parentWidget()
        assert previous.isVisible() and following.isVisible()
        assert parent.rect().contains(previous.geometry())
        assert parent.rect().contains(following.geometry())
        assert previous.geometry().right() < following.geometry().left()
    assert panel.detail_scroll.horizontalScrollBar().maximum() == 0
    panel.round_candidate_table._next_button.click()
    assert panel.round_candidate_table.item(0, 0).text() == "101"
    panel.feed_previous_button.click()
    assert panel._feed_start == 0
    panel.round_next_button.click()
    assert panel.selected_round_id == 5
