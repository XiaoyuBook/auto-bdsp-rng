from __future__ import annotations

import threading
import time
from contextlib import contextmanager

import pytest

from auto_bdsp_rng.automation.easycon import EasyConStatus
from auto_bdsp_rng.automation.easycon.native import ScriptCompileError
from auto_bdsp_rng.automation.easycon.native.device import SwitchReport
from auto_bdsp_rng.automation.easycon.native_backend import NativeEasyConBackend, NativeEasyConBusyError


def until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.005)
    raise AssertionError("condition did not become true")


@contextmanager
def running_script(text, tmp_path, **kwargs):
    backend = NativeEasyConBackend(**kwargs)
    backend.connect("mock")
    results = []
    thread = threading.Thread(target=lambda: results.append(backend.run_editable_script_text(text, "main.ecs", script_dir=tmp_path)))
    thread.start()
    try:
        yield backend, thread, results
    finally:
        backend.stop_current_script()
        thread.join(3)
        backend.close()
        assert not thread.is_alive()


def pause_during_wait(backend, line):
    until(lambda: (point := backend.execution_trace.snapshot().point) is not None
          and point.location.line == line and point.duration_ms is not None)
    assert backend.pause_current_script()
    until(lambda: backend.is_paused)
    assert backend.execution_trace.snapshot().state == "paused"


def test_wait_pause_applies_new_tail_without_replaying_state_or_losing_remaining_time(tmp_path):
    text = 'PRINT "before"\n$n = 7\nA DOWN\nWAIT 400\nA UP\nPRINT $n'
    updated = text.replace("PRINT $n", "$n += 1\nPRINT $n")

    def no_video():
        pytest.fail("This script must not open video")

    with running_script(text, tmp_path, frame_client_factory=no_video) as (backend, thread, results):
        pause_during_wait(backend, 4)
        snapshot = backend.execution_trace.snapshot()
        assert 0 < snapshot.point.duration_ms <= 400
        assert backend.get_report() == SwitchReport()
        time.sleep(.12)
        assert backend.execution_trace.snapshot().point is snapshot.point
        assert backend.get_report() == SwitchReport()
        assert results == []
        with pytest.raises(NativeEasyConBusyError):
            backend.run_script_text("A 10")
        started = time.monotonic()
        backend.resume_script_text(updated)
        until(lambda: backend.get_report().button != 0)
        thread.join(2)
        assert time.monotonic() - started >= snapshot.point.duration_ms / 1000 - .04
        assert results[0].status == EasyConStatus.COMPLETED
        assert results[0].stdout == "before\n8\n"
        final = backend.execution_trace.snapshot()
        assert final.run_id == snapshot.run_id and final.sources[0][1] == updated
        assert backend.get_report() == SwitchReport()


def test_invalid_or_executed_edit_keeps_pause_and_old_program_intact_then_accepts_correction(tmp_path):
    text = 'WAIT 250\nPRINT "old"'
    with running_script(text, tmp_path) as (backend, thread, results):
        pause_during_wait(backend, 1)
        snapshot = backend.execution_trace.snapshot()
        for invalid in ('WAIT 250\nIF', 'WAIT 1\nPRINT "changed"'):
            with pytest.raises(ScriptCompileError):
                backend.resume_script_text(invalid)
            assert backend.is_paused
            assert backend.execution_trace.snapshot().sources == snapshot.sources
        backend.resume_script_text('WAIT 250\nPRINT "new"')
        thread.join(2)
        assert results[0].stdout == "new\n"


def test_button_pause_waits_for_action_and_can_replace_not_yet_started_statement(tmp_path):
    text = 'A 200\nWAIT 10000\nPRINT "old"'
    with running_script(text, tmp_path) as (backend, thread, results):
        until(lambda: backend.get_report().button != 0)
        backend.pause_current_script()
        time.sleep(.02)
        assert not backend.is_paused
        until(lambda: backend.is_paused)
        assert backend.get_report() == SwitchReport()
        point = backend.execution_trace.snapshot().point
        assert point.location.line == 2 and point.action == "即将执行"
        backend.resume_script_text('A 200\nWAIT 1\nPRINT "new"')
        thread.join(2)
        assert results[0].stdout == "new\n"


def test_hot_edit_inside_function_and_loop_keeps_locals_and_uses_new_body_on_next_iteration(tmp_path):
    text = 'FUNC show($n:INT)\nWAIT 200\nPRINT $n\nENDFUNC\nFOR $i = 1 TO 2\nshow($i)\nNEXT\nPRINT "done"'
    with running_script(text, tmp_path) as (backend, thread, results):
        pause_during_wait(backend, 2)
        assert backend.execution_trace.snapshot().point.loops[-1].iteration == 1
        backend.resume_script_text(text.replace("PRINT $n", "$result = $n + 10\nPRINT $result"))
        thread.join(2)
        assert results[0].status == EasyConStatus.COMPLETED
        assert results[0].stdout == "11\n12\ndone\n"


def test_inserting_loop_tail_updates_active_sequence_and_future_iterations(tmp_path):
    text = 'FOR 2\nWAIT 200\nNEXT'
    with running_script(text, tmp_path) as (backend, thread, results):
        pause_during_wait(backend, 2)
        backend.resume_script_text('FOR 2\nWAIT 200\nPRINT "added"\nNEXT')
        thread.join(2)
        assert results[0].stdout == "added\nadded\n"


def test_stop_while_paused_releases_run_slot_without_running_tail(tmp_path):
    with running_script('A DOWN\nWAIT 10000\nPRINT "must not run"', tmp_path) as (backend, thread, results):
        pause_during_wait(backend, 2)
        backend.stop_current_script()
        thread.join(2)
        assert results[0].status == EasyConStatus.CANCELLED
        assert results[0].stdout == ""
        assert backend.get_report() == SwitchReport()
        assert backend.execution_trace.snapshot().state == "stopped"
        assert not backend.can_pause and not backend.is_paused
        assert backend.run_script_text('PRINT "fresh"').stdout == "fresh\n"


def test_repeated_edits_remap_call_and_loop_lines_and_preserve_active_function(tmp_path):
    text = 'FUNC demo()\nWAIT 200\nWAIT 250\nPRINT "old"\nENDFUNC\nFOR 1\ndemo()\nNEXT'
    with running_script(text, tmp_path) as (backend, thread, results):
        pause_during_wait(backend, 2)
        first = text.replace('WAIT 250', 'PRINT "inserted"\nWAIT 250')
        backend.resume_script_text(first)
        until(lambda: backend.execution_trace.snapshot().point.location.line == 4)
        backend.pause_current_script()
        until(lambda: backend.is_paused)
        point = backend.execution_trace.snapshot().point
        assert point.caller.line == 8 and point.loops[-1].location.line == 7
        second = first.replace('PRINT "old"', 'PRINT "new"\nPRINT "end"')
        backend.resume_script_text(second)
        thread.join(2)
        assert results[0].stdout == "inserted\nnew\nend\n"
