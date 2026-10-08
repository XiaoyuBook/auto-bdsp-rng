import json
from pathlib import Path
from types import SimpleNamespace
import threading
import time

import pytest

from auto_bdsp_rng.automation.auto_rng.models import AutoRngConfig, AutoRngPhase, AutoRngSeedResult, AutoRngTarget, ShinyCheckResult
from auto_bdsp_rng.automation.auto_rng.runner import AutoRngRunner, AutoRngServices
from auto_bdsp_rng.automation.auto_rng.starter_flow import (
    DialogObserver, STARTER_TIMING, StarterFlow, StarterTargetMissed,
    TIMELINE_BUFFER, is_start_dialog, is_doctor_dialog, is_second_dialog,
    is_ball_dialog, normalize_dialog, selection_script, validate_starter_delay,
    DOCTOR_PRESS_INTERVALS_MS, SECOND_PRESS_INTERVALS_MS, BALL_PRESS_INTERVALS_MS,
)
from auto_bdsp_rng.rng_core.seed import SeedState32
from auto_bdsp_rng.blink_detection.project_xs import _load_module
from auto_bdsp_rng.automation.auto_rng.starter_clock import BlinkTracking

Xorshift = _load_module("xorshift").Xorshift


class Clock:
    def __init__(self):
        self.now = 0.0
    def time(self):
        return self.now
    def sleep(self, seconds):
        self.now += seconds


def run_flow(species=387, delay=40, *, missing_second=False, missing_balls=False,
             dropped_press=None, stop_at=None, late_zero=None, script_log=None,
             ocr_reads=None, timing_log=None, stall_dialogue=False, cursor_log=None,
             cursor_seconds=.44, cursor_failure=False, stop_in_cursor=False,
             cursor_finish_at_trigger=False, stall_before_cursor=False):
    clock, observations = Clock(), []
    scripts = [] if script_log is None else script_log
    seed = AutoRngSeedResult(SeedState32(0x12345678, 0x9ABCDEF0, 0x11111111, 0x22222222),
                             current_advances=1, npc=1, measured_at=0.0)
    target = AutoRngTarget(raw_target_advances=401)
    # Actual order before A frames 61,167,223,250,276,296,446,480,514 in 11.mp4.
    dialogue = ["怎么回事？刚才那两人……", "咦？test！我们去看看吧！", "………………",
                "怎么了？你说不要到草丛里去？", "没事没事！", "就进去一下而已，宝可梦不会出来啦。",
                "是个手提箱……", "一定是刚才那人掉的，怎么办？",
                "就是想还也不知道对方是谁啊，虽然有听到叫博士……"]
    index = 0
    flow = None
    paused_at_zero, enabled, stalled, stopped = False, True, False, False
    def monotonic():
        nonlocal paused_at_zero
        if flow is None:
            return clock.time()
        timeline = flow.tracker.timeline
        at_first_zero = flow.tracker.countdown == 0 and timeline is None
        at_second_zero = timeline is not None and timeline.delay2_zero_at is not None
        if not paused_at_zero and ((late_zero == "first" and at_first_zero) or (late_zero == "second" and at_second_zero)):
            paused_at_zero = True
            clock.sleep(.2)  # Simulate a stalled worker after the clock update.
        return clock.time()
    def text():
        assert enabled, 'OCR must be paused during fixed keys and clock waits'
        if ocr_reads is not None:
            ocr_reads.append(index)
        if index < len(dialogue):
            return normalize_dialog(dialogue[index])
        if index == len(dialogue):
            return normalize_dialog("哇啊！宝、宝可梦！？")
        if index == len(dialogue)+1:
            return "其他对话" if missing_second else "搞什么啊"
        if index == len(dialogue)+2:
            return "是精灵球用它来应战吧"
        return "其他对话" if missing_balls else "要选哪一只"
    def press(script, name):
        nonlocal index
        scripts.append((name, clock.time(), flow.tracker.update(clock.time())["advances"]))
        if len(scripts) != dropped_press:
            index += 1
        clock.sleep(int(script.split()[1])/1000)
    def observe(value):
        nonlocal enabled
        enabled = value
    def advance_to_trigger():
        while flow.tracker.update(clock.time())["advances"] < flow.select_at:
            timeline = flow.tracker.timeline
            next_at = timeline.queue[0][0] if timeline.queue else timeline.starts_at
            clock.sleep(max(.001, next_at-clock.time()))
        assert flow.tracker.update(clock.time())["advances"] == flow.select_at
    def position_cursor():
        nonlocal stopped
        assert not enabled, 'OCR must be closed while positioning the cursor'
        entry = {"species": species, "dialogue": index, "started": clock.time(),
                 "advances": flow.tracker.update(clock.time())["advances"]}
        if cursor_log is not None:
            cursor_log.append(entry)
        if cursor_failure:
            raise RuntimeError('手柄定位失败')
        clock.sleep(cursor_seconds)
        if cursor_finish_at_trigger:
            advance_to_trigger()
        entry.update(completed=clock.time(), completed_advances=flow.tracker.update(clock.time())["advances"])
        stopped = stop_in_cursor
    def progress(stage, msg, current):
        nonlocal stalled
        observations.append((stage,current,clock.time()))
        if stall_dialogue and not stalled and stage == 'doctor' and clock.time() >= 3:
            stalled = True
            clock.sleep(10)
        if stall_before_cursor and stage == 'position':
            advance_to_trigger()
    flow = StarterFlow(seed,target,delay,species,{"menuClose":True},latest_text=text,run_script=press,
                       monotonic=monotonic,sleep=clock.sleep,observe_dialog=observe,
                       on_press=lambda timing: timing_log.append(timing) if timing_log is not None else None,
                       progress=progress,position_cursor=position_cursor,
                       should_stop=lambda:stopped or (stop_at is not None and clock.time()>=stop_at))
    result = flow.run()
    return result, scripts, observations, flow, seed


@pytest.mark.parametrize("species", [387,390,393])
def test_starter_runs_two_clock_gates_positions_early_then_selects_at_software_delay(species):
    moves = []
    result,scripts,observations,flow,seed = run_flow(species, cursor_log=moves)
    assert result == 'A 50\nWAIT 800\nUP 50\nWAIT 100\nA 50\n'
    assert len(moves) == 1
    assert moves[0]['species'] == species
    assert moves[0]['dialogue'] == 12  # Only after confirming the ball anchor.
    assert moves[0]['completed_advances'] < flow.select_at
    assert moves[0]['completed'] < observations[-1][2]
    assert next(row[2] for row in observations if row[0] == 'position') >= scripts[-1][1]+.2
    assert scripts[0][0] == "御三家·起点对话"
    first = next(row for row in scripts if row[0] == "御三家·第一段归零")
    second = next(row for row in scripts if row[0] == "御三家·第二段归零")
    anchor = next(row for row in observations if row[0] == "first_countdown")
    assert anchor[1] == 401-TIMELINE_BUFFER
    # Replay both visible Timer zeroes; advance compensation occurs later.
    oracle = BlinkTracking(Xorshift(*seed.seed.words), {**STARTER_TIMING,"mode":"recover","menuClose":True},0,0,0)
    oracle.update(anchor[2])
    assert oracle.request_timeline()
    oracle.update(first[1])
    assert oracle.countdown == 0
    assert oracle.timeline is None
    assert first[1] == pytest.approx(anchor[2] + 11 * 1.018, abs=.021)
    assert first[2] == 401-200+22
    oracle.update(second[1])
    assert oracle.timeline is not None
    assert oracle.timeline.delay2_count == 0
    assert second[1] == pytest.approx(oracle.timeline.delay2_zero_at, abs=.021)
    assert oracle.timeline.delay2_at is None
    assert second[2] == 401-200+22+41+10
    assert sum(name == "御三家·第一段归零" for name, *_ in scripts) == 1
    assert sum(name == "御三家·第二段归零" for name, *_ in scripts) == 1
    oracle.update(observations[-1][2])
    assert flow.tracker.update(observations[-1][2])["advances"] == 401-40
    assert flow.tracker.rng.get_state() == oracle.rng.get_state()
    assert observations[-1][0] == "confirm"


@pytest.mark.parametrize("species", [387,390,393])
def test_selection_matches_verified_ecs_clicks_and_release_gaps_without_navigation(species):
    commands = [line.split() for line in selection_script(species).splitlines()]
    assert [(command, int(duration)) for command, duration in commands] == [
        ('A', 50), ('WAIT', 800), ('UP', 50), ('WAIT', 100), ('A', 50),
    ]


@pytest.mark.parametrize('finish_at_trigger', [False,True])
def test_cursor_must_finish_strictly_before_trigger_even_if_it_reaches_the_exact_frame(finish_at_trigger):
    moves = []
    with pytest.raises(StarterTargetMissed, match='光标定位未在选择帧前完成'):
        run_flow(393, cursor_log=moves, cursor_seconds=0 if finish_at_trigger else 300,
                 cursor_finish_at_trigger=finish_at_trigger)
    assert len(moves) == 1
    assert moves[0]['completed_advances'] >= 401-40


def test_late_cursor_dispatch_never_moves_or_returns_selection():
    moves = []
    with pytest.raises(StarterTargetMissed, match='尚未开始光标定位'):
        run_flow(393, cursor_log=moves, stall_before_cursor=True)
    assert moves == []


def test_stop_during_cursor_positioning_prevents_final_confirmation():
    moves = []
    with pytest.raises(RuntimeError, match='已停止'):
        run_flow(393, cursor_log=moves, stop_in_cursor=True)
    assert len(moves) == 1


def test_failed_cursor_positioning_prevents_final_confirmation():
    moves = []
    with pytest.raises(RuntimeError, match='手柄定位失败'):
        run_flow(393, cursor_log=moves, cursor_failure=True)
    assert len(moves) == 1


def test_missed_second_dialog_never_returns_selection():
    with pytest.raises(RuntimeError,match="第二段|搞什么"):
        run_flow(missing_second=True)


def test_fixed_sequences_match_recorded_intervals_and_only_read_four_anchors():
    reads, timings = [], []
    _,scripts,*_ = run_flow(ocr_reads=reads, timing_log=timings)
    assert set(reads) == {0, 8, 10, 12}
    assert len(scripts) == 12  # Eight + two ordinary + two clock-controlled A.
    evidence = json.loads((Path(__file__).parents[2]/'docs/evidence/bdsp-starter-controller-timing.json').read_text(encoding='utf-8'))
    recorded = [event for event in evidence['events'] if event['key'] == 'A']
    assert len(recorded) == 12
    for indices, intervals in [([*range(1,8)], DOCTOR_PRESS_INTERVALS_MS),
                               ([9], SECOND_PRESS_INTERVALS_MS), ([11], BALL_PRESS_INTERVALS_MS)]:
        for index, interval in zip(indices, intervals, strict=True):
            measured = recorded[index]['since_previous_A_down_ms']
            assert 0 <= interval-measured < 10
            actual_gap = (scripts[index][1]-scripts[index-1][1])*1000
            assert actual_gap == pytest.approx(interval, abs=21)
            assert timings[index]['since_previous_press_ms'] == pytest.approx(actual_gap, abs=.001)
    assert all(row['hold_ms'] == (100 if i in (8,10) else 200) for i,row in enumerate(timings))


def test_dropped_ordinary_input_stops_at_doctor_without_sending_extra_a():
    scripts = []
    with pytest.raises(RuntimeError, match='博士|对话识别超时'):
        run_flow(dropped_press=3, script_log=scripts)
    assert len(scripts) == 8
    assert all('归零' not in row[0] for row in scripts)


def test_missing_ball_anchor_never_selects_or_sends_extra_a():
    scripts, moves = [], []
    with pytest.raises(RuntimeError, match='精灵球界面|错过选择帧'):
        run_flow(393, missing_balls=True, script_log=scripts, cursor_log=moves)
    assert len(scripts) == 12
    assert moves == []


def test_stop_during_fixed_sequence_prevents_remaining_keys():
    scripts = []
    with pytest.raises(RuntimeError, match='已停止'):
        run_flow(stop_at=6.5, script_log=scripts)
    assert len(scripts) == 4


def test_stalled_fixed_sequence_keeps_intervals_without_catch_up_burst():
    _,scripts,*_ = run_flow(stall_dialogue=True)
    for index, interval in enumerate(DOCTOR_PRESS_INTERVALS_MS, 1):
        assert (scripts[index][1]-scripts[index-1][1])*1000 >= interval-.001
    assert scripts[1][1] > 10


def test_stop_during_countdown_does_not_dispatch_later_presses():
    with pytest.raises(RuntimeError,match="已停止"):
        run_flow(stop_at=105)


@pytest.mark.parametrize("stage", ["first", "second"])
def test_late_timer_zero_never_dispatches_a_later_countdown_press(stage):
    scripts = []
    with pytest.raises(StarterTargetMissed, match="倒计时按键超时"):
        run_flow(late_zero=stage, script_log=scripts)
    missed_press = "御三家·第一段归零" if stage == "first" else "御三家·第二段归零"
    assert all(name != missed_press for name, *_ in scripts)


@pytest.mark.parametrize("delay", [-1,78,100,200])
def test_impossible_software_delay_is_rejected_without_changing_200(delay):
    with pytest.raises(ValueError,match="200"):
        validate_starter_delay(delay)


def test_dialog_keywords_accept_recording_punctuation_and_traditional_chinese():
    assert is_start_dialog(normalize_dialog("久司：怎么回事？\n刚才那两人……"))
    assert is_doctor_dialog(normalize_dialog("就是想還也不知道對方是誰啊，雖然有聽到叫博士……"))
    assert is_second_dialog(normalize_dialog("搞什麼啊！"))
    assert is_ball_dialog(normalize_dialog("要選哪一隻？"))
    assert not is_doctor_dialog("好像是博士")


def test_slow_ocr_does_not_block_clock_reader_or_publish_after_close():
    entered, release = threading.Event(), threading.Event()
    def read():
        entered.set()
        release.wait(1)
        return "搞什么啊"
    observer = DialogObserver(read,lambda:False)
    observer.start()
    assert entered.wait(1)
    assert observer.latest() is None
    observer.close()
    release.set()
    observer.thread.join(1)
    assert observer.latest() is None


def test_paused_observer_does_not_read_intermediate_dialogues():
    entered = threading.Event()
    observer = DialogObserver(lambda: entered.set() or '起点对话', lambda: False)
    observer.set_enabled(False)
    observer.start()
    try:
        assert not entered.wait(.15)
        observer.set_enabled(True)
        assert entered.wait(1)
    finally:
        observer.close()
        observer.thread.join(1)


def test_resumed_anchor_discards_in_flight_ocr_and_requires_two_fresh_reads():
    entered = [threading.Event() for _ in range(3)]
    release = [threading.Event() for _ in range(3)]
    count = 0
    def read():
        nonlocal count
        index = count
        count += 1
        if index < 3:
            entered[index].set()
            release[index].wait(1)
        return '搞什么啊'
    observer = DialogObserver(read,lambda:False)
    observer.start()
    try:
        assert entered[0].wait(1)
        observer.set_enabled(False)
        observer.set_enabled(True)
        release[0].set()
        assert entered[1].wait(1)
        release[1].set()
        assert entered[2].wait(1)
        assert observer.latest() is None
        release[2].set()
        deadline = time.monotonic()+1
        while observer.latest() is None and time.monotonic() < deadline:
            time.sleep(.01)
        assert observer.latest() == '搞什么啊'
    finally:
        observer.close()
        for event in release:
            event.set()
        observer.thread.join(1)


def test_builtin_runner_hands_back_to_reverse_and_freezes_delay_each_round(tmp_path):
    seed_path, reverse_path = tmp_path/'seed.ecs', tmp_path/'reverse.ecs'
    seed_path.write_text("A 100\n",encoding="utf-8")
    reverse_path.write_text("A 100\n",encoding="utf-8")
    seed = AutoRngSeedResult(SeedState32(1,2,3,4),1,1,measured_at=0)
    trace, saved = [], []
    delays = iter((40,42))
    config = AutoRngConfig(script_dir=tmp_path,seed_script_path=seed_path,reverse_script_path=reverse_path,
                           starter_automation=True,target_species=393,auto_reverse=True,fixed_delay=40,
                           loop_mode="count",loop_count=2)
    services = AutoRngServices(capture_seed=lambda:seed,search_candidates=lambda _:[SimpleNamespace(advances=401,pid=1,ec=2)],
        run_script_text=lambda text,name:trace.append(name),monotonic=lambda:0,
        resolve_round_delay=lambda:next(delays),
        run_starter_flow=lambda seed,target,delay:trace.append((target.used_delay,delay)) or ShinyCheckResult(False,1.0),
        run_reverse_lookup=lambda seed,target:trace.append("reverse") or (41,),
        record_delay_observation=lambda values:saved.append(values))
    runner = AutoRngRunner(config,services=services)
    assert runner.run().phase == AutoRngPhase.COMPLETED
    assert trace == ["seed.ecs",(40,40),"reverse","seed.ecs",(42,42),"reverse"]
    assert saved == [(41,),(41,)]


def test_shiny_and_unknown_results_keep_existing_stop_rules(tmp_path):
    seed = AutoRngSeedResult(SeedState32(1,2,3,4),1,1,measured_at=0)
    for result,phase in [(ShinyCheckResult(True,5.0),AutoRngPhase.COMPLETED),(ShinyCheckResult(False),AutoRngPhase.FAILED)]:
        config = AutoRngConfig(script_dir=tmp_path,start_phase=AutoRngPhase.CAPTURE_SEED,starter_automation=True,
                               target_species=387,auto_reverse=True,fixed_delay=40)
        reverse = []
        runner = AutoRngRunner(config,services=AutoRngServices(capture_seed=lambda:seed,monotonic=lambda:0,
            search_candidates=lambda _:[SimpleNamespace(advances=401,pid=1,ec=2)],
            run_starter_flow=lambda *_:result,run_reverse_lookup=lambda *_:reverse.append(True)))
        assert runner.run().phase == phase
        assert not reverse


def test_missed_timing_never_runs_reverse_or_reports_a_normal_encounter(tmp_path):
    seed = AutoRngSeedResult(SeedState32(1,2,3,4),1,1,measured_at=0)
    config = AutoRngConfig(script_dir=tmp_path,start_phase=AutoRngPhase.CAPTURE_SEED,starter_automation=True,
                           target_species=387,auto_reverse=True,fixed_delay=40)
    reverse,history = [],[]
    def missed(*_):
        raise StarterTargetMissed('已错过选择帧，本轮不选精灵')
    runner = AutoRngRunner(config,services=AutoRngServices(capture_seed=lambda:seed,monotonic=lambda:0,
        search_candidates=lambda _:[SimpleNamespace(advances=401,pid=1,ec=2)],
        run_starter_flow=missed,run_reverse_lookup=lambda *_:reverse.append(True)),
        history_callback=lambda event,args:history.append(event))
    result = runner.run()
    assert result.result_kind == 'missed'
    assert '错过选择帧' in result.log_message
    assert 'starter_missed' in history
    assert 'cycle_result' not in history
    assert not reverse
