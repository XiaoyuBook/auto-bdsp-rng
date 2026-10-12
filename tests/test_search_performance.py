"""Hardware-free search responsiveness and complete-result compatibility checks.

Run with ``python -m pytest tests/test_search_performance.py -q -s`` to print
the 10k-row UI and 20m-frame native measurements alongside their assertions.
"""
from __future__ import annotations

import csv
import gc
import threading
import time
import weakref
from dataclasses import replace

import pytest
from PySide6.QtCore import QObject, QTimer, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog, QTableWidgetItem

from auto_bdsp_rng.automation.auto_rng.search import StaticSearchCriteria
from auto_bdsp_rng.automation.auto_rng import AutoRngConfig, AutoRngSeedResult
from auto_bdsp_rng.automation.auto_tid_rng import AutoTidRngConfig, AutoTidSeedResult
from auto_bdsp_rng.data import get_static_encounters
from auto_bdsp_rng.gen8_id import IDState8
from auto_bdsp_rng.gen8_static import Lead, Profile8, Shiny, State8, StateFilter, StaticGenerator8
from auto_bdsp_rng.gen8_static.models import StaticTemplate8
from auto_bdsp_rng.gen8_static import generator as static_generator
from auto_bdsp_rng.rng_core import SeedPair64
from auto_bdsp_rng.rng_core.cancellation import SearchCancellation, SearchCancelled
from auto_bdsp_rng.ui import main_window as mw
from auto_bdsp_rng.ui import result_paging
from auto_bdsp_rng.ui.auto_rng_panel import AutoRngWorker
from auto_bdsp_rng.ui.auto_tid_rng_panel import AutoTidRngWorker
from auto_bdsp_rng.ui.background_search import BackgroundSearch
from auto_bdsp_rng.ui.result_paging import PagedResultTable, RESULT_PAGE_SIZE
from tests.test_start_readiness import window
from tests.ui_wait import wait_search, wait_until


@pytest.fixture(autouse=True)
def release_closed_windows_before_performance_sample():
    # Prior fixtures have delivered Qt destruction on the GUI thread. Collect
    # remaining Python cycles before the next sample, with GC still enabled
    # throughout each measured search and Qt heartbeat.
    gc.collect()


def _heartbeat(window):
    beats = [time.perf_counter()]
    timer = QTimer(window)
    timer.setTimerType(Qt.TimerType.PreciseTimer)
    timer.setInterval(10)
    timer.timeout.connect(lambda: beats.append(time.perf_counter()))
    timer.start()
    return timer, beats


def _max_gap(timer, beats):
    beats.append(time.perf_counter())
    timer.stop()
    return max(b - a for a, b in zip(beats, beats[1:]))


def _states(count):
    return [State8(advances=i, ec=0x1000 + i, sidtid=0, pid=i + 1,
                   ivs=(i % 32, (i * 7) % 32, 31, 15, 20, 7),
                   ability=0, gender=2, level=50, nature=i % 25,
                   shiny=0, height=120, weight=80) for i in range(count)]


def _finish(window, states):
    record = next(r for r in get_static_encounters() if r.description == "Shaymin")
    criteria = StaticSearchCriteria(seed=SeedPair64(1, 2), profile=window._current_profile(),
                                    record=record, state_filter=StateFilter(), initial_advances=0,
                                    max_advances=len(states) - 1, offset=0, lead=Lead.NONE)
    window._finish_static_generation(record, states, criteria=criteria, collapse_query=False)


def _delay_first_prefix_text(pager, monkeypatch):
    gate, entered = threading.Event(), threading.Event()
    original = pager._snapshot

    def snapshot():
        sort_key, text, identity = original()

        def delayed_text(source, column):
            value = text(source, column)
            if not entered.is_set():
                entered.set()
                assert gate.wait(5)
            return value

        return sort_key, delayed_text, identity

    monkeypatch.setattr(pager, "_snapshot", snapshot)
    return gate, entered


def _record_source_selections(table):
    selected = []

    def changed(row, _column, _previous_row, _previous_column):
        if row >= 0 and (item := table.item(row, 0)) is not None:
            selected.append(int(item.text()))

    table.currentCellChanged.connect(changed)
    return selected


def test_static_10k_page_keeps_gui_responsive_and_exports_complete_results(window, monkeypatch, tmp_path):
    w = window
    w.tabs.setCurrentWidget(w.bdsp_tab)
    for box, value in zip(w.bdsp_seed64_inputs, ("1234567890ABCDEF", "FEDCBA0987654321")):
        box.setText(value)
    w.max_advances.setText("9999")
    monkeypatch.setattr(w, "_current_filter", lambda: (StateFilter(), "any"))
    QTest.qWait(30)
    errors, population_times = [], []
    monkeypatch.setattr(w, "_show_error", lambda *args: errors.append(args))
    populate = w._populate_table

    def measured_populate(states):
        started = time.perf_counter()
        populate(states)
        population_times.append(time.perf_counter() - started)

    monkeypatch.setattr(w, "_populate_table", measured_populate)
    timer, beats = _heartbeat(w)
    started = time.perf_counter()
    w.generate_results()
    entry_elapsed = time.perf_counter() - started
    wait_search(w._static_search)
    QTest.qWait(20)
    gap = _max_gap(timer, beats)
    total_elapsed = time.perf_counter() - started
    print(f"10k static: entry={entry_elapsed:.4f}s total={total_elapsed:.4f}s population={population_times[0]:.4f}s heartbeat_gap={gap:.4f}s", flush=True)
    assert not errors
    assert entry_elapsed < 0.1
    assert population_times[0] < 0.25
    assert gap < 0.3
    assert len(w._states) == 10_000 and w.table.rowCount() == RESULT_PAGE_SIZE
    w.static_table_tools._sort_clicked(0)
    w.table.pager.set_page(17)
    w.table.setCurrentCell(3, 0)
    selected = w.static_table_tools.selected_text().splitlines()
    assert len(selected) == 2 and selected[1].split("\t")[0] == "3403"
    all_rows = [line.split("\t") for line in w._table_text().splitlines()]
    assert len(all_rows) == 10_001
    assert [int(row[0]) for row in all_rows[1:]] == list(range(10_000))
    w.copy_results()
    wait_until(lambda: w._result_transfer.kind is None)
    copied = QGuiApplication.clipboard().text()
    assert [line.split("\t") for line in copied.splitlines()] == all_rows
    output = tmp_path / "complete.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (str(output), ""))
    w.export_results()
    wait_until(lambda: w._result_transfer.kind is None)
    with output.open(encoding="utf-8", newline="") as handle:
        assert list(csv.reader(handle)) == all_rows
    text_output = tmp_path / "complete.txt"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(text_output), ""))
    w.export_results_txt()
    wait_until(lambda: w._result_transfer.kind is None)
    assert text_output.read_text(encoding="utf-8") == copied


def test_static_sort_and_stats_relocate_selection_across_pages(window):
    w = window
    states = _states(1051)
    _finish(w, states)
    pager = w.table.pager
    w.static_table_tools.set_pinned(True)
    w.static_table_tools.set_column_visible(9, False)
    w.table.horizontalHeader().setSortIndicator(8, Qt.SortOrder.AscendingOrder)
    w.table.setSortingEnabled(True)
    ordered = []
    for page in range(pager.page_count):
        pager.set_page(page)
        ordered.extend(int(w.table.item(row, 0).text()) for row in range(w.table.rowCount()))
    expected = sorted(range(len(states)), key=lambda i: states[i].ivs[1])
    assert ordered == expected and len(set(ordered)) == len(states)
    pager.locate(27, 8)
    w.show_stats_check.setChecked(True)
    assert int(w.table.item(w.table.currentRow(), 0).text()) == 27
    assert w.table.currentItem().isSelected()
    assert w.table.isColumnHidden(9)
    expected = sorted(expected, key=lambda i: w._stat_values(states[i])[1])
    assert pager.indices == expected
    w.static_table_tools.refresh_frozen()
    assert w.static_table_tools.frozen.model() is w.table.model()
    assert w.static_table_tools.frozen.selectionModel().isSelected(w.table.currentIndex())
    assert w.table._select_next_prefix_match("126")
    assert w.table.currentItem().text().startswith("126")
    assert len(w._table_text().splitlines()) == len(states) + 1


def test_tid_pages_filter_all_results_and_locate_off_page_target(window):
    panel = window.auto_tid_rng_tab
    states = [IDState8(advances=i, tid=i % 65536, sid=20, tsv=1, display_tid=i % 17) for i in range(1051)]
    panel._clear_targets()
    panel.add_target_display_tid(3)
    panel.set_id_states(states, elapsed_seconds=tuple(i * 1.01 for i in range(len(states))), measured_wall_time=1_700_000_000)
    original = panel._table_text()
    panel._target_key = (1006, 3)
    panel._highlight_target()
    assert panel._highlighted_target_row is None and panel.target_data_button.isEnabled()
    panel.target_data_button.click()
    assert panel.id_table.item(panel.id_table.currentRow(), 0).text() == "1006"
    panel.id_table_tools._sort_clicked(4)
    assert panel.id_table.item(panel.id_table.currentRow(), 0).text() == "1006"
    panel.id_filter_targets_button.click()
    expected = [s.advances for s in states if s.display_tid == 3]
    assert panel._visible_id_count == len(expected)
    assert panel.id_table.rowCount() == len(expected)
    assert [int(panel.id_table.item(row, 0).text()) for row in range(len(expected))] == expected
    assert panel._table_text() == original
    panel.update_tid_current_advances(1006)
    panel.target_data_button.click()
    assert panel.id_table.item(panel.id_table.currentRow(), 6).text() == "当前帧"
    panel.id_filter_all_button.click()
    panel.target_data_button.click()
    assert panel.id_table.item(panel.id_table.currentRow(), 6).text() == "当前帧"


def test_static_100k_sorts_and_full_set_prefix_search_are_responsive(window):
    w = window
    states = _states(100_000)
    w.tabs.setCurrentWidget(w.bdsp_tab)
    QTest.qWait(30)
    timer, beats = _heartbeat(w)
    started = time.perf_counter()
    _finish(w, states)
    population = time.perf_counter() - started
    QTest.qWait(20)
    gap = _max_gap(timer, beats)
    print(f"100k static: population={population:.4f}s heartbeat_gap={gap:.4f}s", flush=True)
    assert population < 0.3 and w.table.rowCount() == RESULT_PAGE_SIZE
    pager = w.table.pager
    expected = list(range(len(states)))
    for column, key in ((1, lambda i: states[i].ec),
                        (8, lambda i: states[i].ivs[1]),
                        (4, lambda i: w._state_row(states[i])[4].casefold()),
                        (15, lambda i: w._state_row(states[i])[15].casefold())):
        timer, beats = _heartbeat(w)
        started = time.perf_counter()
        w.table.horizontalHeader().setSortIndicator(column, Qt.SortOrder.AscendingOrder)
        w.table.setSortingEnabled(True)
        entry = time.perf_counter() - started
        wait_search(pager._search)
        elapsed = time.perf_counter() - started
        gap = _max_gap(timer, beats)
        print(f"100k sort column={column}: entry={entry:.4f}s total={elapsed:.4f}s heartbeat_gap={gap:.4f}s", flush=True)
        # Header resizing measures only the bounded page; allow its font/layout
        # work while rejecting a synchronous full-set sort.
        assert entry < 0.15 and gap < 0.3
        expected = sorted(expected, key=key)
        assert pager.indices == expected
    pager.locate(27, 8)
    timer, beats = _heartbeat(w)
    started = time.perf_counter()
    w.show_stats_check.setChecked(True)
    wait_search(pager._search)
    assert int(w.table.item(w.table.currentRow(), 0).text()) == 27
    assert w.table.currentItem().isSelected()
    assert _max_gap(timer, beats) < 0.3
    # Every snapshot column matches the established formatting, including stats.
    _, text, _ = w._result_sort_callbacks(states)
    for source in (0, 27, 99999):
        assert [text(source, column) for column in range(16)] == w._state_row(states[source])
    timer, beats = _heartbeat(w)
    started = time.perf_counter()
    assert pager.select_prefix("99999", 0)
    entry = time.perf_counter() - started
    wait_search(pager._search)
    elapsed = time.perf_counter() - started
    gap = _max_gap(timer, beats)
    print(f"100k prefix: entry={entry:.4f}s total={elapsed:.4f}s heartbeat_gap={gap:.4f}s", flush=True)
    assert entry < 0.1 and gap < 0.3
    assert w.table.currentItem().text() == "99999"


def test_tid_100k_filter_latest_request_keeps_all_domain_results(window, monkeypatch, tmp_path):
    from datetime import datetime

    panel = window.auto_tid_rng_tab
    states = [IDState8(i, i % 65536, 20, 1, i % 17) for i in range(100_000)]
    panel._clear_targets()
    panel.add_target_display_tid(3)
    elapsed = tuple(i * 1.01 for i in range(len(states)))
    timer, beats = _heartbeat(window)
    started = time.perf_counter()
    panel.set_id_states(states, elapsed_seconds=elapsed, measured_wall_time=1_700_000_000)
    wait_search(panel.id_table.pager._search)
    population = time.perf_counter() - started
    gap = _max_gap(timer, beats)
    print(f"100k TID dates: total={population:.4f}s heartbeat_gap={gap:.4f}s", flush=True)
    assert gap < 0.3
    assert len(panel._id_arrival_texts._cache) <= RESULT_PAGE_SIZE
    timer, beats = _heartbeat(window)
    started = time.perf_counter()
    panel.id_filter_targets_button.click()
    entry = time.perf_counter() - started
    panel.id_filter_all_button.click()
    panel.id_filter_targets_button.click()
    wait_search(panel.id_table.pager._search)
    elapsed = time.perf_counter() - started
    gap = _max_gap(timer, beats)
    print(f"100k TID filter: entry={entry:.4f}s total={elapsed:.4f}s heartbeat_gap={gap:.4f}s", flush=True)
    assert entry < 0.1 and gap < 0.3
    assert panel.id_table.pager.indices == [i for i in range(len(states)) if i % 17 == 3]
    assert panel._visible_id_count == 5883 and len(panel._id_states) == 100_000
    assert panel.id_table.rowCount() == RESULT_PAGE_SIZE
    # Locate a locked result excluded by the current target-only filter.
    panel._target_key = (99_999, 99_999 % 17)
    panel._highlight_target()
    panel.target_data_button.click()
    wait_search(panel.id_table.pager._search)
    assert panel.id_filter_all_button.isChecked()
    assert panel.id_table.item(panel.id_table.currentRow(), 0).text() == "99999"
    panel.update_tid_current_advances(100_000)
    assert panel.id_table.pager.select_prefix("已过", 6)
    wait_search(panel.id_table.pager._search)
    assert panel.id_table.currentItem().text() == "已过"
    timer, beats = _heartbeat(window)
    started = time.perf_counter()
    panel.id_table.horizontalHeader().setSortIndicator(6, Qt.SortOrder.DescendingOrder)
    panel.id_table.setSortingEnabled(True)
    entry = time.perf_counter() - started
    wait_search(panel.id_table.pager._search)
    elapsed = time.perf_counter() - started
    gap = _max_gap(timer, beats)
    print(f"100k TID time sort: entry={entry:.4f}s total={elapsed:.4f}s heartbeat_gap={gap:.4f}s", flush=True)
    assert entry < 0.15 and gap < 0.3
    assert panel.id_table.pager.indices == list(reversed(range(len(states))))
    panel.id_filter_targets_button.click()
    wait_search(panel.id_table.pager._search)
    panel.copy_results()
    wait_until(lambda: panel._result_transfer.kind is None)
    # Copy-all/export preserve the original full result order and timestamps,
    # including rows outside the current page and target-only filtered view.
    copied = QGuiApplication.clipboard().text()
    rows = [line.split("\t") for line in copied.splitlines()]
    assert len(rows) == 100_001
    assert [int(row[0]) for row in rows[1:]] == list(range(100_000))
    assert all(len(row[4]) == 6 for row in rows[1:])
    for source in (0, 199, 200, 99_999):
        expected_arrival = datetime.fromtimestamp(1_700_000_000 + source * 1.01).strftime("%Y.%m.%d %H:%M:%S")
        assert rows[source + 1][6] == expected_arrival
    output = tmp_path / "all_tid_dates.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(output), ""))
    panel.export_results()
    wait_until(lambda: panel._result_transfer.kind is None)
    with output.open(encoding="utf-8", newline="") as handle:
        assert list(csv.reader(handle)) == rows


def test_result_callback_can_submit_next_search_without_manual_polling(window):
    search = BackgroundSearch(window)
    results, errors = [], []
    gate, entered = threading.Event(), threading.Event()

    def next_work(token):
        entered.set()
        assert gate.wait(3)
        token.check()
        return 2

    def first_done(result):
        results.append(result)
        search.submit(next_work, results.append, errors.append)

    try:
        search.submit(lambda token: 1, first_done, errors.append)
        wait_until(entered.is_set)
        assert search.timer.isActive()
        gate.set()
        wait_until(lambda: results == [1, 2])
        assert not errors
    finally:
        gate.set()
        search.close()


def test_closed_search_ignores_repeat_close_after_its_qt_owner_is_deleted(window):
    from shiboken6 import delete, isValid

    owner = QObject(window)
    search = BackgroundSearch(owner)
    gate, entered = threading.Event(), threading.Event()
    delivered = []

    def work(token):
        entered.set()
        assert gate.wait(3)
        token.check()
        return 1

    try:
        search.submit(work, delivered.append, delivered.append)
        assert entered.wait(1)
        search.close()
        delete(owner)
        assert not isValid(search.timer)
        search.close()
        search.cancel()
        gate.set()
        wait_until(lambda: search.worker_thread is None)
        assert not delivered
    finally:
        gate.set()


def test_pager_close_releases_full_domain_data_without_waiting_for_gc(window):
    class Batch:
        values = range(201)

    table = PagedResultTable(window)
    table.setColumnCount(1)
    batch = Batch()
    reference = weakref.ref(batch)
    table.pager.set_source(
        len(batch.values),
        lambda source, data=batch: [QTableWidgetItem(str(data.values[source]))],
        lambda source, column, data=batch: data.values[source],
        lambda source, column, data=batch: str(data.values[source]),
        lambda source: source,
    )
    del batch
    assert reference() is not None
    table.pager.close()
    assert reference() is None


def test_async_sort_keeps_the_users_new_selection(window, monkeypatch):
    states = _states(10_001)
    _finish(window, states)
    pager = window.table.pager
    gate, entered = threading.Event(), threading.Event()
    original = result_paging._ordered_indices

    def delayed(*args):
        entered.set()
        assert gate.wait(3)
        return original(*args)

    monkeypatch.setattr(result_paging, "_ordered_indices", delayed)
    try:
        window.table.setCurrentCell(1, 0)
        window.table.horizontalHeader().setSortIndicator(8, Qt.SortOrder.DescendingOrder)
        window.table.setSortingEnabled(True)
        assert entered.wait(1)
        window.table.setCurrentCell(5, 0)
        gate.set()
        wait_search(pager._search)
        assert window.table.item(window.table.currentRow(), 0).text() == "5"
        assert window.table.currentItem().isSelected()
        assert pager.indices == sorted(range(len(states)), key=lambda i: states[i].ivs[1], reverse=True)
    finally:
        gate.set()


@pytest.mark.parametrize("table_kind", ["static", "tid"])
@pytest.mark.parametrize("editing", ["append", "backspace", "rapid", "clear"])
def test_keyboard_prefix_edits_replace_only_the_latest_pending_lookup(window, monkeypatch, table_kind, editing):
    if table_kind == "static":
        _finish(window, _states(10_001))
        table = window.table
    else:
        panel = window.auto_tid_rng_tab
        panel.set_id_states([IDState8(i, i % 65536, 20, 1, i % 17) for i in range(10_001)])
        table = panel.id_table
    pager = table.pager
    wait_search(pager._search)
    table.setCurrentCell(0, 0)
    selected = _record_source_selections(table)
    statuses = []
    table.searchStatusChanged.connect(statuses.append)
    gate, entered = _delay_first_prefix_text(pager, monkeypatch)
    try:
        QTest.keyClicks(table, "1")
        assert entered.wait(1)
        if editing == "append":
            QTest.keyClicks(table, "2")
            expected = "12"
        elif editing == "backspace":
            QTest.keyClicks(table, "23")
            QTest.keyClick(table, Qt.Key.Key_Backspace)
            expected = "12"
        elif editing == "rapid":
            QTest.keyClicks(table, "234")
            expected = "1234"
        else:
            QTest.keyClick(table, Qt.Key.Key_Backspace)
            expected = ""
        gate.set()
        wait_search(pager._search)
        assert table._search_text == expected
        assert statuses[-1] == (f"查找: {expected}" if expected else "")
        assert selected == ([int(expected)] if expected else [])
        assert table.item(table.currentRow(), 0).text() == (expected or "0")
    finally:
        gate.set()


@pytest.mark.parametrize("previous,current,old_prefix,new_prefix", [
    (300, 500, "当前帧", "当前帧"),
    (500, 300, "当前帧", "已过"),
    (300, 500, "已过", "当前帧"),
])
def test_off_page_tid_advances_invalidate_old_prefix_without_cell_changes(
    window, monkeypatch, previous, current, old_prefix, new_prefix,
):
    panel = window.auto_tid_rng_tab
    panel.set_id_states([IDState8(i, i % 65536, 20, 1, i % 17) for i in range(10_001)])
    table, pager = panel.id_table, panel.id_table.pager
    wait_search(pager._search)
    panel.update_tid_current_advances(previous)
    table.setCurrentCell(0, 6)
    selected = _record_source_selections(table)
    changes = []
    table.model().dataChanged.connect(lambda *args: changes.append(True))
    gate, entered = _delay_first_prefix_text(pager, monkeypatch)
    try:
        assert pager.select_prefix(old_prefix, 6)
        assert entered.wait(1)
        panel.update_tid_current_advances(current)
        assert changes == []  # Every displayed row was already "已过".
        gate.set()
        wait_search(pager._search)
        assert selected == [] and table.item(table.currentRow(), 0).text() == "0"
        assert pager.select_prefix(new_prefix, 6)
        wait_search(pager._search)
        expected = current if new_prefix == "当前帧" else 1
        assert table.item(table.currentRow(), 0).text() == str(expected)
        assert table.currentItem().text() == new_prefix
    finally:
        gate.set()


def test_off_page_tid_origin_change_invalidates_date_prefix_without_cell_changes(window, monkeypatch):
    panel = window.auto_tid_rng_tab
    panel._clear_targets()
    panel.add_target_display_tid(3)
    states = [IDState8(i, i % 65536, 20, 1, 2 if i < 200 else 3) for i in range(12_001)]
    elapsed = tuple(None if i < 400 else i * 1.01 for i in range(len(states)))
    panel.set_id_states(states, elapsed_seconds=elapsed, measured_wall_time=1_700_000_000)
    wait_search(panel.id_table.pager._search)
    panel.id_filter_targets_button.click()
    table, pager = panel.id_table, panel.id_table.pager
    wait_search(pager._search)
    # Normalize the already-visible tooltips as well as the time origin. The
    # next origin change then has no Qt item change to invalidate the lookup.
    panel.set_tid_timing_origin(1_700_000_000)
    table.setCurrentCell(0, 6)
    assert table.item(0, 0).text() == "200" and table.currentItem().text() == "—"
    old_date = panel._id_arrival_texts[500]
    selected = _record_source_selections(table)
    changes = []
    table.model().dataChanged.connect(lambda *args: changes.append(True))
    gate, entered = _delay_first_prefix_text(pager, monkeypatch)
    try:
        assert pager.select_prefix(old_date, 6)
        assert entered.wait(1)
        panel.set_tid_timing_origin(1_700_086_400)
        assert changes == []  # This page has no elapsed times or Adv 0.
        gate.set()
        wait_search(pager._search)
        assert selected == [] and table.item(table.currentRow(), 0).text() == "200"
        assert pager.select_prefix(panel._id_arrival_texts[500], 6)
        wait_search(pager._search)
        assert table.item(table.currentRow(), 0).text() == "500"
        assert table.currentItem().text() == panel._id_arrival_texts[500]
    finally:
        gate.set()


@pytest.mark.parametrize("view", ["sort", "filter"])
@pytest.mark.parametrize("change", ["advances", "origin"])
def test_tid_prefix_edits_and_time_changes_leave_pending_view_in_charge(window, monkeypatch, view, change):
    panel = window.auto_tid_rng_tab
    states = [IDState8(i, i % 65536, 20, 1, i % 17) for i in range(10_001)]
    panel._clear_targets()
    panel.add_target_display_tid(3)
    panel.set_id_states(states, elapsed_seconds=tuple(i * 1.01 for i in range(len(states))),
                        measured_wall_time=1_700_000_000)
    table, pager = panel.id_table, panel.id_table.pager
    wait_search(pager._search)
    table.horizontalHeader().setSortIndicator(0, Qt.SortOrder.AscendingOrder)
    table.setSortingEnabled(True)
    wait_search(pager._search)
    table.setCurrentCell(0, 0)
    gate, entered = threading.Event(), threading.Event()
    original = result_paging._ordered_indices

    def delayed(indices, sort_key, column, reverse, token):
        entered.set()
        assert gate.wait(5)
        token.check()
        return original(indices, sort_key, column, reverse, token)

    monkeypatch.setattr(result_paging, "_ordered_indices", delayed)
    try:
        if view == "sort":
            table.sortItems(0, Qt.SortOrder.DescendingOrder)
        else:
            panel.id_filter_targets_button.click()
        assert entered.wait(1)
        token = pager._search._active_token
        QTest.keyClicks(table, "12")
        QTest.keyClick(table, Qt.Key.Key_Backspace)
        if change == "advances":
            panel.update_tid_current_advances(500)
        else:
            panel.set_tid_timing_origin(1_700_086_400)
        assert not token.is_cancelled()
        gate.set()
        wait_search(pager._search)
        expected = (list(reversed(range(len(states)))) if view == "sort"
                    else [i for i in range(len(states)) if i % 17 == 3])
        assert pager.indices == expected
    finally:
        gate.set()


@pytest.mark.parametrize("kind", ["static", "tid"])
def test_automatic_stop_cancels_search_and_new_services_can_restart(window, tmp_path, kind):
    seed = SeedPair64(1, 2)
    if kind == "static":
        config = AutoRngConfig(script_dir=tmp_path, max_advances=500_000_000)
        record = get_static_encounters()[0]
        window.auto_rng_tab._targets = [(record, StateFilter(natures=(False,) * 25), "any")]
        services = window._build_auto_rng_services(config)
        invoke = lambda service: service.search_candidates(AutoRngSeedResult(seed=seed))
    else:
        config = AutoTidRngConfig(script_dir=tmp_path)
        services = window._build_auto_tid_rng_services(config)
        invoke = lambda service: service.search_id_states(AutoTidSeedResult(seed=seed), 500_000_000, (3,))
    results, errors, completed = [], [], []

    def work():
        try:
            results.append(invoke(services))
        except Exception as error:
            errors.append(error)
        finally:
            completed.append(time.perf_counter())

    worker = threading.Thread(target=work)
    worker.start()
    QTest.qWait(20)
    started = time.perf_counter()
    services.stop_current_script()
    stop_elapsed = time.perf_counter() - started
    wait_until(lambda: not worker.is_alive())
    elapsed = time.perf_counter() - started
    print(f"automatic {kind} cancel: request={stop_elapsed:.4f}s worker={completed[0] - started:.4f}s ui_wait={elapsed:.4f}s", flush=True)
    assert elapsed < 0.3
    assert not results and len(errors) == 1 and isinstance(errors[0], SearchCancelled)
    if kind == "static":
        restarted = window._build_auto_rng_services(replace(config, max_advances=0))
        assert invoke(restarted) == []
    else:
        restarted = window._build_auto_tid_rng_services(config)
        assert len(restarted.search_id_states(AutoTidSeedResult(seed=seed), 0, (3,))) == 1


@pytest.mark.parametrize("worker_type", [AutoRngWorker, AutoTidRngWorker])
def test_stopped_automation_worker_reports_cancelled_search_as_finished(window, worker_type):
    from types import SimpleNamespace
    finished, failed = [], []

    def cancelled():
        raise SearchCancelled("Search cancelled")

    runner = SimpleNamespace(run=cancelled, should_stop=lambda: True, progress=object())
    worker = worker_type(runner)
    worker.finished.connect(finished.append)
    worker.failed.connect(failed.append)
    worker.run()
    assert finished == [runner.progress] and not failed


def test_new_seed_replaces_pending_search_and_ignores_old_error(window, monkeypatch):
    w = window
    gate, entered = threading.Event(), threading.Event()
    searched, errors = [], []
    monkeypatch.setattr(w, "_show_error", lambda *args: errors.append(args))

    def search(criteria, **kwargs):
        searched.append(criteria.seed)
        if criteria.seed.seed0 == 1:
            entered.set()
            assert gate.wait(3)
            raise RuntimeError("old seed failure")
        return [replace(_states(1)[0], advances=criteria.seed.seed0)]

    monkeypatch.setattr(mw, "generate_static_candidates", search)
    try:
        w._sync_bdsp_data_from_auto_rng(SeedPair64(1, 2))
        assert entered.wait(1)
        worker = w._static_search.worker_thread
        w._sync_bdsp_data_from_auto_rng(SeedPair64(3, 4))
        w._sync_bdsp_data_from_auto_rng(SeedPair64(5, 6))
        assert w._static_search.worker_thread is worker
        gate.set()
        wait_search(w._static_search)
        assert searched == [SeedPair64(1, 2), SeedPair64(5, 6)]
        assert not errors and w._states[0].advances == 5
        assert "0000000000000005" in w.query_summary.toolTip()
    finally:
        gate.set()


@pytest.mark.parametrize("old_error", [False, True])
def test_invalid_new_auto_seed_cancels_old_search_and_preserves_displayed_results(window, monkeypatch, old_error):
    w = window
    previous_states = _states(7)
    _finish(w, previous_states)
    previous_summary = (w.query_summary.text(), w.query_summary.toolTip())
    gate, entered = threading.Event(), threading.Event()
    tokens, errors = [], []
    monkeypatch.setattr(w, "_show_error", lambda *args: errors.append(args))

    def search(criteria, *, cancellation):
        tokens.append(cancellation)
        entered.set()
        assert gate.wait(5)
        if old_error:
            raise RuntimeError("obsolete seed failure")
        return [replace(_states(1)[0], advances=777)]

    monkeypatch.setattr(mw, "generate_static_candidates", search)
    try:
        w._sync_bdsp_data_from_auto_rng(SeedPair64(1, 2))
        assert entered.wait(1)
        w.tid.selectAll()
        QTest.keyClicks(w.tid, "65536")  # Editable UI accepts it; Profile8 rejects the range.
        assert w.tid.text() == "65536"
        w._sync_bdsp_data_from_auto_rng(SeedPair64(5, 6))
        assert tokens[0].is_cancelled()
        assert w._static_result_state == "failed" and len(errors) == 1
        assert isinstance(errors[0][1], ValueError)
        gate.set()
        wait_search(w._static_search)
        assert len(tokens) == 1 and len(errors) == 1
        assert w._static_result_state == "failed"
        assert w._states is previous_states and w.table.rowCount() == len(previous_states)
        assert (w.query_summary.text(), w.query_summary.toolTip()) == previous_summary
    finally:
        gate.set()


def test_closing_cancels_search_without_delivering_to_closed_window(window, monkeypatch):
    w = window
    gate, entered = threading.Event(), threading.Event()
    delivered = []

    def search(criteria, *, cancellation):
        entered.set()
        assert gate.wait(3)
        cancellation.check()
        return _states(1)

    monkeypatch.setattr(mw, "generate_static_candidates", search)
    monkeypatch.setattr(w, "_finish_static_generation", lambda *a, **kw: delivered.append(True))
    try:
        w._sync_bdsp_data_from_auto_rng(SeedPair64(1, 2))
        assert entered.wait(1)
        w.close()
        wait_until(lambda: w._static_search._closed)
        gate.set()
        wait_search(w._static_search)
        assert not delivered
    finally:
        gate.set()


@pytest.mark.skipif(not static_generator._HAS_NATIVE, reason="native extension required")
def test_native_20m_search_releases_gil_for_qt_heartbeat(window):
    sf = StateFilter(natures=(False,) * 25)
    generator = StaticGenerator8(0, 20_000_000, 0, Lead.NONE,
                                 StaticTemplate8(species=483, shiny=Shiny.RANDOM, iv_count=0, ability=255),
                                 Profile8(tid=12345, sid=54321), sf)
    results = []
    timer, beats = _heartbeat(window)
    started = time.perf_counter()
    worker = threading.Thread(target=lambda: results.append(generator.generate(SeedPair64(0x1234567890ABCDEF, 0xFEDCBA0987654321))))
    worker.start()
    wait_until(lambda: not worker.is_alive())
    elapsed = time.perf_counter() - started
    gap = _max_gap(timer, beats)
    print(f"20m native: total={elapsed:.4f}s heartbeat_gap={gap:.4f}s beats={len(beats) - 2}", flush=True)
    assert results == [[]]
    assert len(beats) >= 5 and gap < 0.15


@pytest.mark.skipif(not static_generator._HAS_NATIVE, reason="native extension required")
def test_large_matching_native_conversion_is_responsive_and_complete(window):
    generator = StaticGenerator8(0, 250_000, 0, Lead.NONE,
                                 get_static_encounters()[0].template, Profile8(), StateFilter(skip=True))
    results = []
    timer, beats = _heartbeat(window)
    worker = threading.Thread(target=lambda: results.append(generator.generate(SeedPair64(1, 2))))
    worker.start()
    wait_until(lambda: not worker.is_alive(), timeout_ms=10_000)
    gap = _max_gap(timer, beats)
    print(f"250k native conversion: heartbeat_gap={gap:.4f}s", flush=True)
    assert len(results[0]) == 250_001
    assert results[0][0].advances == 0 and results[0][-1].advances == 250_000
    assert gap < 0.15


@pytest.mark.parametrize("native,multi", [(False, False), (False, True), (True, False), (True, True)])
def test_search_cancellation_is_bounded_and_never_returns_partial_results(window, monkeypatch, native, multi):
    if native and not static_generator._HAS_NATIVE:
        pytest.skip("native extension required")
    monkeypatch.setattr(static_generator, "_HAS_NATIVE", native)
    cancellation = SearchCancellation()
    sf = StateFilter(natures=(False,) * 25)
    generator = StaticGenerator8(0, 500_000_000, 0, Lead.NONE,
                                 get_static_encounters()[0].template, Profile8(), sf)
    results, errors, completed = [], [], []

    def search():
        try:
            results.append(generator.generate_matching_any([sf, sf], SeedPair64(1, 2), cancellation=cancellation)
                           if multi else generator.generate(SeedPair64(1, 2), cancellation=cancellation))
        except Exception as error:
            errors.append(error)
        finally:
            completed.append(time.perf_counter())

    worker = threading.Thread(target=search)
    worker.start()
    QTest.qWait(20)
    started = time.perf_counter()
    cancellation.cancel()
    wait_until(lambda: not worker.is_alive())
    elapsed = time.perf_counter() - started
    print(f"static native={native} multi={multi} cancel: worker={completed[0] - started:.4f}s ui_wait={elapsed:.4f}s", flush=True)
    assert elapsed < 0.3 and not results
    assert len(errors) == 1 and isinstance(errors[0], SearchCancelled)


@pytest.mark.skipif(not static_generator._HAS_NATIVE, reason="native extension required")
@pytest.mark.parametrize("roamer,skip", [(False, False), (True, False), (True, True)])
def test_native_multi_and_python_match_single_filter_union(monkeypatch, roamer, skip):
    template = (next(record.template for record in get_static_encounters() if record.template.roamer and record.template.species == 481)
                if roamer else get_static_encounters()[0].template)
    filters = [StateFilter(shiny=0, natures=tuple(i % 2 == 0 for i in range(25))),
               StateFilter(height_min=100, height_max=130)]
    if skip:
        filters[0] = StateFilter(skip=True, iv_max=(0,) * 6, hidden_powers=(False,) * 16)
    seed = SeedPair64(0x1234567890ABCDEF, 0xFEDCBA0987654321)
    generator = StaticGenerator8(31, 2000, 3, Lead.NONE, template, Profile8(tid=12345, sid=54321))
    results = []
    for native in (False, True):
        monkeypatch.setattr(static_generator, "_HAS_NATIVE", native)
        singles = {}
        for state_filter in filters:
            for state in replace(generator, state_filter=state_filter).generate(seed):
                singles.setdefault(state.advances, state)
        expected = [singles[advances] for advances in sorted(singles)]
        actual = generator.generate_matching_any(filters, seed)
        assert actual == expected
        results.append(actual)
    assert results[0] == results[1]


@pytest.mark.skipif(not static_generator._HAS_NATIVE, reason="native extension required")
@pytest.mark.parametrize("noisy", [False, True])
def test_native_reidentify_cancellation_is_bounded(window, noisy):
    from auto_bdsp_rng.rng_core import _native
    cancellation = SearchCancellation()
    errors = []
    results = []

    def work():
        try:
            if noisy:
                result = _native.reidentify_by_intervals_noisy(1, 2, [0, 1, 1, 1], 0, 20_000_000, cancel_token=cancellation.native)
            else:
                result = _native.reidentify_by_intervals(1, 2, [0, 2, 3, 4], 0, 0, 20_000_000, cancel_token=cancellation.native)
            results.append(result)
        except Exception as error:
            errors.append(error)

    worker = threading.Thread(target=work)
    worker.start()
    QTest.qWait(10)
    started = time.perf_counter()
    cancellation.cancel()
    wait_until(lambda: not worker.is_alive())
    assert time.perf_counter() - started < 0.3
    assert not results and len(errors) == 1 and isinstance(errors[0], SearchCancelled)
