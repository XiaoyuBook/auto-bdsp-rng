from __future__ import annotations

from pathlib import Path

import pytest

from auto_bdsp_rng.automation.auto_tid_rng import (
    AutoTidRngConfig,
    AutoTidRngPhase,
    AutoTidRngProgress,
    ProjectXsMunchlaxAdvanceCounter,
    AutoTidRngRunner,
    AutoTidRngServices,
    AutoTidSeedResult,
    parse_tid_text,
    reverse_lookup_span,
    select_target_display_tid,
    select_target_tid,
)
from auto_bdsp_rng.gen8_id import IDState8
from auto_bdsp_rng.rng_core import BDSPXorshift, SeedPair64, SeedState32


def _project_xs_munchlax_interval(state: SeedState32) -> float:
    rng = BDSPXorshift(state)
    temp = (rng.next() & 0x7FFFFF) / 8388607.0
    return temp * 3.0 + (1.0 - temp) * 12.0 + 0.285


def test_select_target_tid_uses_earliest_matching_frame() -> None:
    states = [
        IDState8(advances=255, tid=1, sid=10, tsv=0, display_tid=1),
        IDState8(advances=42, tid=7, sid=20, tsv=0, display_tid=7),
        IDState8(advances=12, tid=2, sid=30, tsv=0, display_tid=2),
    ]

    target = select_target_tid(states, [1, 2, 9], frame_threshold=300)

    assert target is not None
    assert target.advances == 12
    assert target.tid == 2
    assert target.sid == 30


def test_select_target_tid_ignores_frames_above_threshold() -> None:
    states = [IDState8(advances=301, tid=1, sid=10, tsv=0, display_tid=1)]

    assert select_target_tid(states, [1], frame_threshold=300) is None


def test_select_target_display_tid_uses_display_tid_and_earliest_frame() -> None:
    states = [
        IDState8(advances=80, tid=111, sid=1, tsv=0, display_tid=123456),
        IDState8(advances=20, tid=222, sid=2, tsv=0, display_tid=654321),
        IDState8(advances=10, tid=333, sid=3, tsv=0, display_tid=999999),
    ]

    target = select_target_display_tid(states, [654321, 123456], frame_threshold=300)

    assert target is not None
    assert target.advances == 20
    assert target.tid == 222
    assert target.display_tid == 654321


def test_auto_tid_runner_checks_zoom_before_second_seed_script(tmp_path: Path) -> None:
    seed_script = tmp_path / "id测种.txt"
    seed_script.write_text("A 100\n", encoding="utf-8")
    scripts: list[str] = []
    zoom_checks: list[bool] = []
    runner = AutoTidRngRunner(
        AutoTidRngConfig(
            script_dir=tmp_path,
            seed_script_path=seed_script,
            target_display_tids=(123456,),
        ),
        services=AutoTidRngServices(
            capture_seed=lambda: AutoTidSeedResult(seed=SeedPair64(1, 2), measured_at=10.0),
            search_id_states=lambda _seed, _threshold, _targets: [],
            run_script_text=lambda _text, name: scripts.append(name),
            recover_zoom_mode=lambda: zoom_checks.append(True) or True,
        ),
    )

    runner.run(max_steps=4)

    assert scripts == ["id测种.txt", "id测种.txt"]
    assert zoom_checks == [True]


def test_auto_tid_runner_does_not_run_second_seed_script_when_stopped_during_zoom_recovery(tmp_path: Path) -> None:
    seed_script = tmp_path / "id测种.txt"
    seed_script.write_text("A 100\n", encoding="utf-8")
    scripts: list[str] = []
    holder = {}

    def stop_during_recovery() -> bool:
        holder["runner"].stop()
        return True

    runner = AutoTidRngRunner(
        AutoTidRngConfig(script_dir=tmp_path, seed_script_path=seed_script, target_display_tids=(123456,)),
        services=AutoTidRngServices(
            capture_seed=lambda: AutoTidSeedResult(seed=SeedPair64(1, 2), measured_at=10.0),
            search_id_states=lambda _seed, _threshold, _targets: [],
            run_script_text=lambda _text, name: scripts.append(name),
            recover_zoom_mode=stop_during_recovery,
        ),
    )
    holder["runner"] = runner

    runner.run(max_steps=4)

    assert scripts == ["id测种.txt"]
    assert runner.progress.phase == AutoTidRngPhase.IDLE
    assert runner.progress.log_message == "已请求停止自动 TID 乱数"


def test_auto_tid_runner_stop_includes_first_reason_and_state_snapshot(tmp_path: Path) -> None:
    runner = AutoTidRngRunner(
        AutoTidRngConfig(script_dir=tmp_path),
        progress_callback=None,
    )
    messages: list[str] = []
    runner.log_callback = messages.append
    runner.progress = AutoTidRngProgress(
        phase=AutoTidRngPhase.WAIT_NAME_TRIGGER,
        loop_index=3,
        seed_text="0000000000000001 0000000000000002",
        current_advances=3330,
        target_tid=12,
        target_sid=34,
        target_display_tid=777777,
        target_advances=3338,
        trigger_advances=3337,
        remaining_to_trigger=7,
    )

    runner.stop("视频源读帧失败")
    runner.stop("第二次停止不应覆盖")

    assert runner.progress.phase is AutoTidRngPhase.IDLE
    assert runner.progress.stop_reason == "视频源读帧失败"
    assert len(messages) == 1
    message = messages[0]
    assert "原因=视频源读帧失败" in message
    assert "阶段=等待取名帧" in message
    assert "轮次=3" in message
    assert "当前Adv=3330" in message
    assert "目标Adv=3338" in message
    assert "触发Adv=3337" in message
    assert "剩余Adv=7" in message
    assert "目标DisplayTID=777777" in message
    assert "Seed=0000000000000001 0000000000000002" in message


def test_reverse_lookup_span_is_symmetric_and_inclusive() -> None:
    assert reverse_lookup_span(255, 20) == (235, 275, 41)
    assert reverse_lookup_span(10, 20) == (0, 30, 31)
    assert reverse_lookup_span(42, 0) == (42, 42, 1)


def test_parse_tid_text_extracts_first_valid_tid() -> None:
    assert parse_tid_text("TID 00001") == 1
    assert parse_tid_text("识别结果: 65535") == 65535
    assert parse_tid_text("65536") is None
    assert parse_tid_text("没有数字") is None


def test_project_xs_munchlax_counter_uses_project_xs_rangefloat_interval() -> None:
    state = SeedState32(0x11111111, 0x22222222, 0x33333333, 0x44444444)
    counter = ProjectXsMunchlaxAdvanceCounter()

    counter.reset(current_advances=0, seed=state, now=100.0)

    assert counter.next_tick_at == pytest.approx(100.0 + _project_xs_munchlax_interval(state))
    assert counter.advance_to(counter.next_tick_at - 0.001) == 0
    assert counter.advance_to(counter.next_tick_at) == 1
    assert counter.current_advances == 1


def test_project_xs_munchlax_estimate_target_at_matches_ticks_without_mutating_counter() -> None:
    state = SeedState32(0x11111111, 0x22222222, 0x33333333, 0x44444444)
    counter = ProjectXsMunchlaxAdvanceCounter()
    reference = ProjectXsMunchlaxAdvanceCounter()
    counter.reset(current_advances=3330, seed=state, now=100.0)
    reference.reset(current_advances=3330, seed=state, now=100.0)

    assert counter._rng is not None
    before = (
        counter.current_advances,
        counter.next_tick_at,
        counter.last_tick_at,
        counter.last_interval_seconds,
        counter._rng.words,
    )

    for _ in range(7):
        reference.advance_one_blink()
    expected_target_at = reference.last_tick_at

    assert counter.estimate_target_at(3337) == pytest.approx(expected_target_at)
    assert counter.estimate_target_at(3337) == pytest.approx(expected_target_at)
    after = (
        counter.current_advances,
        counter.next_tick_at,
        counter.last_tick_at,
        counter.last_interval_seconds,
        counter._rng.words,
    )
    assert after == before


def test_auto_tid_runner_waits_until_display_tid_target_then_runs_name_script(tmp_path: Path) -> None:
    seed_script = tmp_path / "BDSP测种.txt"
    name_script = tmp_path / "取名.txt"
    seed_script.write_text("A 100\n", encoding="utf-8")
    name_script.write_text("B 100\n", encoding="utf-8")
    scripts: list[str] = []
    clock = [10.0]
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock[0] += seconds

    services = AutoTidRngServices(
        capture_seed=lambda: AutoTidSeedResult(
            seed=SeedPair64(1, 2),
            current_advances=0,
            npc=0,
            seed_text="0000000000000001 0000000000000002",
            measured_at=clock[0],
        ),
        search_id_states=lambda _seed, _threshold, _targets: [
            IDState8(advances=255, tid=1, sid=100, tsv=0, display_tid=123456)
        ],
        run_script_text=lambda _text, name: scripts.append(name),
        monotonic=lambda: clock[0],
        sleep=sleep,
    )
    runner = AutoTidRngRunner(
        AutoTidRngConfig(
            script_dir=tmp_path,
            seed_script_path=seed_script,
            name_script_path=name_script,
            frame_threshold=300,
            target_display_tids=(123456,),
            delay=20,
        ),
        services=services,
    )

    runner.run(max_steps=10)

    assert scripts == ["BDSP测种.txt", "取名.txt"]
    assert runner.progress.phase == AutoTidRngPhase.COMPLETED
    assert runner.progress.target_advances == 255
    assert runner.progress.target_display_tid == 123456
    assert runner.progress.trigger_advances == 235
    assert sleeps


def test_auto_tid_runner_can_start_from_capture_seed(tmp_path: Path) -> None:
    seed_script = tmp_path / "BDSP娴嬬.txt"
    name_script = tmp_path / "鍙栧悕.txt"
    seed_script.write_text("A 100\n", encoding="utf-8")
    name_script.write_text("B 100\n", encoding="utf-8")
    scripts: list[str] = []

    runner = AutoTidRngRunner(
        AutoTidRngConfig(
            script_dir=tmp_path,
            seed_script_path=seed_script,
            name_script_path=name_script,
            frame_threshold=300,
            target_display_tids=(123456,),
            delay=0,
            start_phase=AutoTidRngPhase.CAPTURE_TIDSID,
        ),
        services=AutoTidRngServices(
            capture_seed=lambda: AutoTidSeedResult(seed=SeedPair64(1, 2), measured_at=10.0),
            search_id_states=lambda _seed, _threshold, _targets: [
                IDState8(advances=0, tid=1, sid=100, tsv=0, display_tid=123456)
            ],
            run_script_text=lambda _text, name: scripts.append(name),
            monotonic=lambda: 10.0,
            sleep=lambda _seconds: None,
        ),
    )

    runner.run(max_steps=10)

    assert scripts == ["鍙栧悕.txt"]
    assert runner.progress.phase == AutoTidRngPhase.COMPLETED
    assert runner.progress.loop_index == 1


def test_auto_tid_runner_waits_with_project_xs_munchlax_timing(tmp_path: Path) -> None:
    seed_script = tmp_path / "BDSP测种.txt"
    name_script = tmp_path / "取名.txt"
    seed_script.write_text("A 100\n", encoding="utf-8")
    name_script.write_text("B 100\n", encoding="utf-8")
    state = SeedState32(0x11111111, 0x22222222, 0x33333333, 0x44444444)
    clock = [100.0]
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock[0] += seconds

    runner = AutoTidRngRunner(
        AutoTidRngConfig(
            script_dir=tmp_path,
            seed_script_path=seed_script,
            name_script_path=name_script,
            frame_threshold=10,
            target_display_tids=(123456,),
            delay=0,
        ),
        services=AutoTidRngServices(
            capture_seed=lambda: AutoTidSeedResult(seed=state, measured_at=clock[0]),
            search_id_states=lambda _seed, _threshold, _targets: [
                IDState8(advances=1, tid=1, sid=100, tsv=0, display_tid=123456)
            ],
            run_script_text=lambda _text, _name: None,
            monotonic=lambda: clock[0],
            sleep=sleep,
        ),
    )

    runner.run(max_steps=10)

    assert sleeps[:1] == pytest.approx([_project_xs_munchlax_interval(state)])
    assert runner.progress.phase == AutoTidRngPhase.COMPLETED


def test_auto_tid_runner_records_single_adv_wait_timing_and_log(tmp_path: Path) -> None:
    seed_script = tmp_path / "BDSP测种.txt"
    name_script = tmp_path / "取名.txt"
    seed_script.write_text("A 100\n", encoding="utf-8")
    name_script.write_text("B 100\n", encoding="utf-8")

    state = SeedState32(0x11111111, 0x22222222, 0x33333333, 0x44444444)
    measured_at = 100.0
    clock = [measured_at]
    sleeps: list[float] = []
    logs: list[str] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock[0] += seconds

    runner = AutoTidRngRunner(
        AutoTidRngConfig(
            script_dir=tmp_path,
            seed_script_path=seed_script,
            name_script_path=name_script,
            frame_threshold=2,
            target_display_tids=(123456,),
            delay=0,
        ),
        services=AutoTidRngServices(
            capture_seed=lambda: AutoTidSeedResult(seed=state, current_advances=0, measured_at=measured_at),
            search_id_states=lambda _seed, _threshold, _targets: [
                IDState8(advances=1, tid=1, sid=100, tsv=0, display_tid=123456)
            ],
            run_script_text=lambda _text, _name: None,
            monotonic=lambda: clock[0],
            sleep=sleep,
        ),
        log_callback=logs.append,
    )

    runner.run(max_steps=10)

    expected_interval = _project_xs_munchlax_interval(state)
    assert runner.progress.phase is AutoTidRngPhase.COMPLETED
    assert runner.progress.wait_started_at == pytest.approx(measured_at)
    assert runner.progress.wait_target_at == pytest.approx(measured_at + expected_interval)
    assert runner.progress.wait_elapsed_seconds == pytest.approx(expected_interval)
    assert runner.progress.wait_keep_awake is False
    assert sleeps[:1] == pytest.approx([expected_interval])

    arrival_logs = [message for message in logs if message.startswith("到达取名脚本触发帧 1")]
    assert arrival_logs
    arrival_log = arrival_logs[-1]
    assert "理论等待=" in arrival_log
    assert "等待已用=" in arrival_log
    assert "等待Adv保活=未启用" in arrival_log


def test_auto_tid_runner_retries_seed_script_when_display_tid_not_in_threshold(tmp_path: Path) -> None:
    seed_script = tmp_path / "BDSP测种.txt"
    name_script = tmp_path / "取名.txt"
    for path in (seed_script, name_script):
        path.write_text("A 100\n", encoding="utf-8")
    scripts: list[str] = []
    progress_messages: list[str] = []
    searches = [[], [IDState8(advances=42, tid=1, sid=100, tsv=0, display_tid=123456)]]
    clock = [10.0]

    def sleep(seconds: float) -> None:
        clock[0] += seconds

    runner = AutoTidRngRunner(
        AutoTidRngConfig(
            script_dir=tmp_path,
            seed_script_path=seed_script,
            name_script_path=name_script,
            frame_threshold=300,
            target_display_tids=(123456,),
            delay=0,
        ),
        services=AutoTidRngServices(
            capture_seed=lambda: AutoTidSeedResult(seed=SeedPair64(1, 2), measured_at=10.0),
            search_id_states=lambda _seed, _threshold, _targets: searches.pop(0),
            run_script_text=lambda _text, name: scripts.append(name),
            monotonic=lambda: clock[0],
            sleep=sleep,
        ),
        progress_callback=lambda progress: progress_messages.append(progress.log_message),
    )

    runner.run(max_steps=10)

    assert runner.progress.phase == AutoTidRngPhase.COMPLETED
    assert scripts == ["BDSP测种.txt", "BDSP测种.txt", "取名.txt"]
    assert progress_messages.count("阈值 300 帧内未命中目标 Display TID，重新运行测种脚本") == 1


def test_auto_tid_runner_capture_start_only_skips_seed_script_for_first_cycle(tmp_path: Path) -> None:
    seed_script = tmp_path / "BDSP测种.txt"
    name_script = tmp_path / "取名.txt"
    for path in (seed_script, name_script):
        path.write_text("A 100\n", encoding="utf-8")
    scripts: list[str] = []
    searches = [[], [IDState8(advances=42, tid=1, sid=100, tsv=0, display_tid=123456)]]
    clock = [10.0]

    def sleep(seconds: float) -> None:
        clock[0] += seconds

    runner = AutoTidRngRunner(
        AutoTidRngConfig(
            script_dir=tmp_path,
            seed_script_path=seed_script,
            name_script_path=name_script,
            frame_threshold=300,
            target_display_tids=(123456,),
            delay=0,
            start_phase=AutoTidRngPhase.CAPTURE_TIDSID,
        ),
        services=AutoTidRngServices(
            capture_seed=lambda: AutoTidSeedResult(seed=SeedPair64(1, 2), measured_at=clock[0]),
            search_id_states=lambda _seed, _threshold, _targets: searches.pop(0),
            run_script_text=lambda _text, name: scripts.append(name),
            monotonic=lambda: clock[0],
            sleep=sleep,
        ),
    )

    runner.run(max_steps=10)

    assert runner.progress.phase == AutoTidRngPhase.COMPLETED
    assert scripts == ["BDSP测种.txt", "取名.txt"]
    assert runner.progress.loop_index == 2


def test_auto_tid_runner_restarts_seed_script_when_capture_seed_fails(tmp_path: Path) -> None:
    seed_script = tmp_path / "BDSP测种.txt"
    name_script = tmp_path / "取名.txt"
    for path in (seed_script, name_script):
        path.write_text("A 100\n", encoding="utf-8")
    scripts: list[str] = []
    capture_attempts = 0
    clock = [10.0]

    def capture_seed() -> AutoTidSeedResult:
        nonlocal capture_attempts
        capture_attempts += 1
        if capture_attempts == 1:
            raise RuntimeError("Project_Xs seed capture failed")
        return AutoTidSeedResult(seed=SeedPair64(1, 2), measured_at=clock[0])

    runner = AutoTidRngRunner(
        AutoTidRngConfig(
            script_dir=tmp_path,
            seed_script_path=seed_script,
            name_script_path=name_script,
            frame_threshold=300,
            target_display_tids=(123456,),
            delay=0,
            start_phase=AutoTidRngPhase.CAPTURE_TIDSID,
        ),
        services=AutoTidRngServices(
            capture_seed=capture_seed,
            search_id_states=lambda _seed, _threshold, _targets: [
                IDState8(advances=42, tid=1, sid=100, tsv=0, display_tid=123456)
            ],
            run_script_text=lambda _text, name: scripts.append(name),
            monotonic=lambda: clock[0],
            sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
        ),
    )

    runner.run(max_steps=10)

    assert capture_attempts == 2
    assert runner.progress.phase == AutoTidRngPhase.COMPLETED
    assert scripts == ["BDSP测种.txt", "取名.txt"]
    assert runner.progress.loop_index == 2
