"""Compare the port against Project_Xs's actual GUI worker without GUI or I/O."""
import ast
import heapq
from pathlib import Path
from types import SimpleNamespace

import pytest

from auto_bdsp_rng.automation.auto_rng.starter_clock import BlinkTracking
from auto_bdsp_rng.automation.auto_rng.starter_flow import STARTER_TIMING
from auto_bdsp_rng.blink_detection.project_xs import _load_module


@pytest.mark.parametrize("words", [(1, 2, 3, 4), (0x11111111, 0x22222222, 0x33333333, 0x44444444)])
def test_clock_rng_state_and_event_counts_match_project_xs_monitoring_worker(words):
    path = Path(__file__).resolve().parents[2] / "third_party/Project_Xs_CHN/src/player_blink_gui.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    worker = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "monitoring_work")
    # Execute only this worker. Its capture, GUI, keys and wall clock are fakes;
    # its RNG/event ordering comes directly from the vendored upstream source.
    module = ast.Module(body=[worker], type_ignores=[])
    Xorshift = _load_module("xorshift").Xorshift
    rng = Xorshift(*words)
    clock = SimpleNamespace(now=0.0)
    text = SimpleNamespace(delete=lambda *_: None, insert=lambda *_: None)
    gui = SimpleNamespace(
        player_eye=None, menu_check_var=SimpleNamespace(get=lambda: True),
        auto_timeline_check_var=SimpleNamespace(get=lambda: False),
        monitor_blink_button={}, s0_1_2_3=text, s01_23=text,
        keypress_advance=SimpleNamespace(get=lambda: "999999"),
        preview=lambda: None, timelining=False, auto_timeline=False,
        config_json={"view": (0, 0, 1, 1), "MonitorWindow": False,
                     "WindowPrefix": "", "crop": (0, 0, 1, 1), "camera": 0,
                     "thresh": .7, "npc": 1, "white_delay": 0,
                     "advance_delay": 41, "advance_delay_2": 48,
                     "timeline_npc": -1, "pokemon_npc": 2},
    )
    observations = []

    def sleep(seconds):
        clock.now += seconds

    def observe(*values):
        if values and str(values[0]).startswith("帧数:"):
            observations.append((clock.now, gui.advances, tuple(rng.get_state())))
            if gui.advances == 201:
                gui.timelining = True
            if gui.advances >= 335:
                gui.timelining = False
                gui.tracking = False

    namespace = {
        "heapq": heapq, "tk": SimpleNamespace(END="end"), "print": observe,
        "time": SimpleNamespace(perf_counter=lambda: clock.now, sleep=sleep),
        "rngtool": SimpleNamespace(tracking_blink=lambda *_, **kw: ([], [], 0), recov=lambda *_, **kw: rng),
        "press": lambda *_: pytest.fail("Oracle must never send a real key"),
    }
    exec(compile(module, str(path), "exec"), namespace)
    namespace["monitoring_work"](gui)
    tracker = BlinkTracking(Xorshift(*words), {**STARTER_TIMING, "mode": "recover"}, 0, 0, 0)
    for timestamp, advances, state in observations:
        assert tracker.update(timestamp)["advances"] == advances
        assert tuple(tracker.rng.get_state()) == state
        if advances == 201:
            assert tracker.request_timeline()
    assert observations[-1][1] == 335
    assert tracker.countdown_zero_at is not None
    assert tracker.timeline.delay2_zero_at < tracker.timeline.delay2_at
