from __future__ import annotations

from pathlib import Path
import threading

import numpy as np
import pytest

from auto_bdsp_rng.automation.easycon import EasyConStatus
from auto_bdsp_rng.automation.easycon.native import EasyConScriptEngine
from auto_bdsp_rng.automation.easycon.native_backend import NativeEasyConBackend
from tests.automation.test_easycon_native_backend import FakeFrameClient
from tests.automation.test_easycon_native_engine import RecordingGamepad, RecordingWaiter


def test_trace_follows_loops_branch_and_library_without_changing_actions(tmp_path: Path):
    lib = tmp_path / "lib"
    lib.mkdir()
    library = lib / "菜单.ecs"
    library.write_text("FUNC menu()\nWAIT 25\nB 10\nENDFUNC\n", encoding="utf-8")
    text = "$n = 2\nFOR $n\nA 15\nWAIT 20\nNEXT\nIF $n == 2\nmenu()\nELSE\nX 5\nENDIF\nWAIT 40\n"
    program = EasyConScriptEngine().compile(text, source=str(tmp_path / "main.ecs"), script_dir=tmp_path)
    traces = []
    gamepad, waiter = RecordingGamepad(), RecordingWaiter()
    program.run(gamepad=gamepad, waiter=waiter, trace=traces.append)
    plain_gamepad, plain_waiter = RecordingGamepad(), RecordingWaiter()
    program.run(gamepad=plain_gamepad, waiter=plain_waiter)
    assert gamepad.events == plain_gamepad.events == [("click", "A", 15), ("click", "A", 15), ("click", "B", 10)]
    assert waiter.milliseconds == plain_waiter.milliseconds == [20, 20, 25, 40]
    assert [p.action for p in traces if p.action.startswith("循环 ")] == ["循环 1 / 2", "循环 2 / 2"]
    waits_in_loop = [p for p in traces if p.duration_ms == 20]
    assert [(p.loops[-1].iteration, p.loops[-1].total) for p in waits_in_loop] == [(1, 2), (2, 2)]
    assert not any(p.location.line == 9 and p.location.source.endswith("main.ecs") for p in traces)
    lib_wait = next(p for p in traces if p.duration_ms == 25)
    assert lib_wait.location.source == str(library.resolve())
    assert lib_wait.location.line == 2
    assert lib_wait.caller.line == 7
    assert traces[-1].location.source.endswith("main.ecs") and traces[-1].location.line == 11
    assert traces[-1].caller is None and not traces[-1].loops


def test_compiled_sources_preserve_comments_blank_lines_and_loaded_library(tmp_path: Path):
    lib = tmp_path / "lib"
    lib.mkdir()
    path = lib / "library.ecs"
    original = "# 注释\n\nFUNC demo()\nWAIT 2\nENDFUNC\n"
    path.write_text(original, encoding="utf-8")
    program = EasyConScriptEngine().compile("# main\n\ndemo()\n", source="main.ecs", script_dir=tmp_path)
    path.write_text("different text", encoding="utf-8")
    assert program.ast.main.text == "# main\n\ndemo()\n"
    assert program.ast.libraries[0].text == original


def test_backend_retains_stopped_position_and_clears_it_for_failed_next_run(tmp_path: Path):
    entered = threading.Event()

    class Waiter:
        def wait(self, milliseconds, cancel_event):
            entered.set()
            cancel_event.wait(2)

    backend = NativeEasyConBackend(frame_client_factory=lambda: FakeFrameClient(np.zeros((8, 8, 3), dtype=np.uint8)), waiter=Waiter())
    result = []
    worker = threading.Thread(target=lambda: result.append(backend.run_script_text("# 注释\nWAIT 9000\n", "long.ecs", script_dir=tmp_path)))
    try:
        worker.start()
        assert entered.wait(2)
        live = backend.execution_trace.snapshot()
        assert live.state == "running" and live.point.location.line == 2
        assert live.point.duration_ms == 9000
        # Taking a snapshot is read-only; the cancelled run keeps the same point.
        backend.stop_current_script()
        worker.join(2)
        assert not worker.is_alive()
        assert result[0].status == EasyConStatus.CANCELLED
        stopped = backend.execution_trace.snapshot()
        assert stopped.state == "stopped" and stopped.point is live.point
        assert stopped.sources[0][1] == "# 注释\nWAIT 9000\n"
        backend.run_script_text("IF", "bad.ecs", script_dir=tmp_path)
        failed = backend.execution_trace.snapshot()
        assert failed.run_id == live.run_id + 1
        assert failed.state == "failed" and failed.point is None
        assert failed.source.endswith("bad.ecs")
    finally:
        backend.stop_current_script()
        worker.join(2)
        backend.close()


def test_while_conditions_and_nested_call_return_locations(tmp_path: Path):
    text = "FUNC pause():INT\nWAIT 2\nRETURN 3\nENDFUNC\n$n = 0\nWHILE $n < 2\n$n += 1\nEND\nWAIT pause()\n"
    program = EasyConScriptEngine().compile(text, source="main.ecs")
    traces = []
    waiter = RecordingWaiter()
    program.run(waiter=waiter, trace=traces.append)
    assert sum(p.location.line == 6 and p.action == "判断循环条件" for p in traces) == 4
    assert traces[-1].location.line == 9 and traces[-1].duration_ms == 3
    assert traces[-1].caller is None
    assert waiter.milliseconds == [2, 3]


@pytest.mark.parametrize(("text", "expected"), [
    ("FOR $i = 5 TO 1 STEP -2\nWAIT 1\nNEXT", [(1, 3), (2, 3), (3, 3)]),
    ("FOR $i = 2 TO 7 STEP 2\nWAIT 1\nNEXT", [(1, 3), (2, 3), (3, 3)]),
    ("$n = 0\nWHILE $n < 2\n$n += 1\nWAIT 1\nEND", [(1, None), (2, None)]),
    ("$n = 0\nFOR\n$n += 1\nWAIT 1\nIF $n == 3\nBREAK\nENDIF\nNEXT", [(1, None), (2, None), (3, None)]),
    ("FOR 0\nWAIT 1\nNEXT", []),
    ("FOR $i = 1 TO 5 STEP -1\nWAIT 1\nNEXT", []),
])
def test_loop_progress_counts_body_iterations_and_clears_on_exit(text, expected):
    program = EasyConScriptEngine().compile(text + "\nWAIT 9", source="loop.ecs")
    traces = []
    waiter, plain_waiter = RecordingWaiter(), RecordingWaiter()
    program.run(waiter=waiter, trace=traces.append)
    program.run(waiter=plain_waiter)
    assert waiter.milliseconds == plain_waiter.milliseconds
    assert [(p.loops[-1].iteration, p.loops[-1].total) for p in traces if p.duration_ms == 1] == expected
    assert traces[-1].duration_ms == 9 and traces[-1].loops == ()


def test_nested_library_loop_progress_unwinds_after_return_continue_and_break(tmp_path: Path):
    library = tmp_path / "lib"
    library.mkdir()
    (library / "helper.ecs").write_text("FUNC menu()\nFOR 2\nWAIT 1\nRETURN\nNEXT\nENDFUNC", encoding="utf-8")
    text = "FOR $i = 1 TO 3\nFOR $j = 1 TO 3\nIF $j == 2\nCONTINUE\nENDIF\nIF $i == 2\nIF $j == 3\nBREAK 2\nENDIF\nENDIF\nmenu()\nWAIT 2\nNEXT\nNEXT\nWAIT 9"
    program = EasyConScriptEngine().compile(text, source=str(tmp_path / "main.ecs"), script_dir=tmp_path)
    traces = []
    waiter = RecordingWaiter()
    program.run(waiter=waiter, trace=traces.append)
    waits = [p for p in traces if p.duration_ms == 1]
    assert [[loop.iteration for loop in p.loops] for p in waits] == [[1, 1, 1], [1, 3, 1], [2, 1, 1]]
    assert all(p.loops[-1].location.source.endswith("helper.ecs") for p in waits)
    after_return = [p for p in traces if p.duration_ms == 2]
    assert [[loop.iteration for loop in p.loops] for p in after_return] == [[1, 1], [1, 3], [2, 1]]
    assert traces[-1].duration_ms == 9 and traces[-1].loops == ()
