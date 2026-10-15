from __future__ import annotations

import gc
import threading
import weakref
from dataclasses import replace
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from auto_bdsp_rng.rng_core.cancellation import SearchCancellation, SearchCancelled
from auto_bdsp_rng.ui.history_archive import MAX_PENDING_CANDIDATE_BATCHES, publish_history_event
from auto_bdsp_rng.ui.history_panel import HistoryPanel
import auto_bdsp_rng.ui.history_panel as history_module
from tests.test_history_performance import app, finish_archive, state, wait_until
from tests.test_history_producer_backpressure import _runner_from_actual_start
from tests.test_history_review_fixes import window


@pytest.mark.parametrize("event", ["candidates_found", "candidates_refiltered", "reverse_lookup_results"])
def test_actual_producer_freezes_and_indexes_before_qt_and_history_reuses_it(window, app, monkeypatch, tmp_path, event):
    runner = _runner_from_actual_start(window, monkeypatch, tmp_path)
    panel = window.history_tab
    gui_thread = threading.get_ident()
    constructor_threads, source_refs = [], []
    real_rows = history_module._CandidateRows

    def rows(*args, **kwargs):
        constructor_threads.append(threading.get_ident())
        assert threading.get_ident() != gui_thread, "Qt must reuse the producer's index"
        source = real_rows(*args, **kwargs)
        source_refs.append(weakref.ref(source))
        return source

    monkeypatch.setattr(history_module, "_CandidateRows", rows)
    mutable = SimpleNamespace(advances=1499, pid=0, ec=0, ivs=[31] * 6, nature=3,
                              shiny=0, ability=0, gender=0, height=0, weight=0,
                              _reverse_species="integration-extra")
    candidates = [state(index) for index in range(1500)]
    candidates[-1] = mutable
    flags = [""] * len(candidates)
    flags[-1] = "sync"
    delays = list(range(len(candidates)))
    runtime_inputs = []
    real_targets = window.auto_rng_tab.set_candidate_targets

    def targets(values, *args):
        assert values[-1] is mutable and values[-1]._reverse_species == "integration-extra"
        runtime_inputs.append(values)
        real_targets(values, *args)

    monkeypatch.setattr(window.auto_rng_tab, "set_candidate_targets", targets)
    finished = threading.Event()
    errors = []

    def produce():
        try:
            if event == "reverse_lookup_results":
                runner._history(event, candidates, "测试个性", delays)
            else:
                runner._history(event, candidates, 1499, flags)
        except Exception as error:
            errors.append(str(error))
        finally:
            finished.set()

    producer = threading.Thread(target=produce)
    producer.start()
    # Do not deliver Qt events until the caller has changed its original input.
    assert finished.wait(5)
    producer.join(0)
    assert not producer.is_alive() and not errors
    assert len(constructor_threads) == 1
    mutable.advances, mutable.pid, mutable.ec = 99999, 99, 99
    mutable.ivs[0] = 0
    candidates.clear()
    flags[-1], delays[-1] = "", 99999
    wait_until(lambda: panel._candidate_count == 1500)
    finish_archive(panel, app)
    candidate, status, delay, characteristic = panel._archive.candidate(1, 1499)
    assert candidate.advances == 1499 and candidate.ivs[0] == 31
    if event == "reverse_lookup_results":
        assert status == "匹配 · 同候选1" and delay == 1499 and characteristic == "测试个性"
        assert not runtime_inputs
    else:
        assert status == "锁定 · 同步 · 同候选1"
        assert panel._active_record().locked_advances == 1499
        assert len(runtime_inputs) == 1 and len(runtime_inputs[0]) == 1500
    text = panel._complete_plain_text()
    marker = "反查候选" if event == "reverse_lookup_results" else "  候选"
    assert text.count(marker) == 1500 and "adv=99999" not in text
    assert text.index(f"{marker}1") < text.index(f"{marker}1500")
    assert len(constructor_threads) == 1
    gc.collect()
    assert all(reference() is None for reference in source_refs)


@pytest.mark.parametrize("action", ["cancel", "shutdown", "clear", "suspend_resume"])
def test_cancel_during_producer_preparation_releases_slot_and_never_emits(app, monkeypatch, action):
    panel = HistoryPanel()
    archive = panel._archive
    cancellation = SearchCancellation()
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    snapshot_threads, emitted, errors = [], [], []
    real_snapshot = history_module._snapshot_candidate

    def snapshot(value):
        snapshot_threads.append(threading.get_ident())
        if len(snapshot_threads) == 512:
            entered.set()
            assert release.wait(5)
        return real_snapshot(value)

    monkeypatch.setattr(history_module, "_snapshot_candidate", snapshot)
    candidates = [SimpleNamespace(advances=index, pid=index, ec=index, ivs=[31] * 6) for index in range(5000)]

    def produce():
        try:
            publish_history_event(archive, lambda *args: emitted.append(args), "candidates_found",
                                  (candidates, 0), cancelled=cancellation.is_cancelled)
        except SearchCancelled:
            errors.append("cancelled")
        finally:
            finished.set()

    producer = threading.Thread(target=produce)
    producer.start()
    try:
        assert entered.wait(5)
        assert len(archive._reservations) == 1 and not emitted
        if action == "cancel":
            cancellation.cancel()
        elif action == "shutdown":
            assert panel.shutdown()
        elif action == "clear":
            panel.clear()
        else:
            archive.stop_accepting()
            archive.resume_accepting()
    finally:
        release.set()
    assert finished.wait(5)
    producer.join(0)
    assert not producer.is_alive() and errors == (["cancelled"] if action == "cancel" else [])
    assert not emitted and not archive._reservations and not archive._pending and archive._jobs.empty()
    assert 512 <= len(snapshot_threads) <= 1024
    assert threading.get_ident() not in snapshot_threads


@pytest.mark.parametrize("cancel_run", [False, True])
def test_unclaimed_history_does_not_block_producer_and_run_cancel_still_works(app, monkeypatch, cancel_run):
    panel = HistoryPanel()
    archive = panel._archive
    cancellation = SearchCancellation()
    emitted, prepared_threads, source_refs, errors = [], [], [], []
    real_prepare = archive._prepare_candidates

    def prepare(*args, **kwargs):
        prepared_threads.append(threading.get_ident())
        values, source = real_prepare(*args, **kwargs)
        source_refs.append(weakref.ref(source))
        return values, source

    monkeypatch.setattr(archive, "_prepare_candidates", prepare)

    def produce():
        try:
            for batch in range(4):
                if cancel_run and batch == MAX_PENDING_CANDIDATE_BATCHES:
                    cancellation.cancel()
                publish_history_event(archive, lambda *_args: emitted.append(_args[1]), "candidates_found",
                                      ([state(batch * 2000 + index) for index in range(1500)], 0),
                                      cancelled=cancellation.is_cancelled)
        except SearchCancelled:
            errors.append("cancelled")

    producer = threading.Thread(target=produce)
    producer.start()
    try:
        # No GUI consumer releases a slot. The fourth publication must still
        # return, unless the actual run was explicitly stopped.
        wait_until(lambda: not producer.is_alive())
        assert len(prepared_threads) == (3 if cancel_run else 4)
        assert len(emitted) == len(prepared_threads)
        assert errors == (["cancelled"] if cancel_run else [])
        assert len(archive._reservations) == len(emitted)
        assert all(payload.reservation.prepared_rows is not None for payload in emitted)
        assert threading.get_ident() not in prepared_threads
    finally:
        cancellation.cancel()
        for payload in emitted:
            payload.reservation.release_unclaimed()
        producer.join(2)
    assert not producer.is_alive() and not archive._reservations
    assert all(payload.reservation.prepared_rows is None for payload in emitted)
    gc.collect()
    assert all(reference() is None for reference in source_refs)


def test_preparation_failure_releases_its_reservation_before_emission(app, monkeypatch):
    panel = HistoryPanel()
    archive = panel._archive
    emitted, errors = [], []

    def fail(*_args, **_kwargs):
        assert len(archive._reservations) == 1
        raise ValueError("bad integration candidate")

    monkeypatch.setattr(archive, "_prepare_candidates", fail)

    def produce():
        try:
            publish_history_event(archive, lambda *args: emitted.append(args), "candidates_found", ([state(0)], 0))
        except ValueError as error:
            errors.append(str(error))

    producer = threading.Thread(target=produce)
    producer.start()
    producer.join(5)
    assert not producer.is_alive() and errors == ["bad integration candidate"]
    assert not emitted and not archive._reservations and archive._jobs.empty()


def test_stop_after_preparation_before_emission_discards_the_completed_source(app, monkeypatch):
    panel = HistoryPanel()
    archive = panel._archive
    entered, release = threading.Event(), threading.Event()
    emitted, errors, source_refs = [], [], []
    real_prepare = archive._prepare_candidates

    def prepare(*args, **kwargs):
        values, source = real_prepare(*args, **kwargs)
        source_refs.append(weakref.ref(source))
        entered.set()
        assert release.wait(5)
        return values, source

    monkeypatch.setattr(archive, "_prepare_candidates", prepare)

    def produce():
        try:
            publish_history_event(archive, lambda *args: emitted.append(args), "candidates_found",
                                  ([state(index) for index in range(1500)], 0))
        except SearchCancelled:
            errors.append("cancelled")

    producer = threading.Thread(target=produce)
    producer.start()
    try:
        assert entered.wait(5)
        archive.stop_accepting()
        archive.resume_accepting()
    finally:
        release.set()
    producer.join(5)
    assert not producer.is_alive() and errors == []
    assert not emitted and not archive._reservations and archive._jobs.empty()
    gc.collect()
    assert all(reference() is None for reference in source_refs)


def test_duplicate_index_preserves_unknown_and_wide_integration_keys(app):
    panel = HistoryPanel()
    wide = SimpleNamespace(advances=0, pid=0, ec=1 << 32, ivs=[31] * 6)
    valid = replace(state(1), pid=1, ec=0)
    unknown = SimpleNamespace(advances=3, pid=None, ec=None, ivs=[31] * 6)
    same_valid = SimpleNamespace(advances=5, pid=1, ec=0, ivs=[31] * 6)
    panel.candidates_found([wide, valid, wide, unknown, unknown, same_valid], -1)
    statuses = [panel.round_candidate_table.item(index, 1).text() for index in range(6)]
    assert statuses == ["普通", "普通", "同候选1", "普通", "同候选4", "同候选2"]
