from __future__ import annotations

from pathlib import Path

from auto_bdsp_rng.automation.auto_tid_rng import (
    AutoTidRngConfig,
    AutoTidRngPhase,
    AutoTidRngRunner,
    AutoTidRngServices,
    AutoTidSeedResult,
    parse_tid_text,
    reverse_lookup_span,
    select_target_display_tid,
    select_target_tid,
)
from auto_bdsp_rng.gen8_id import IDState8
from auto_bdsp_rng.rng_core import SeedPair64


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


def test_reverse_lookup_span_is_symmetric_and_inclusive() -> None:
    assert reverse_lookup_span(255, 20) == (235, 275, 41)
    assert reverse_lookup_span(10, 20) == (0, 30, 31)
    assert reverse_lookup_span(42, 0) == (42, 42, 1)


def test_parse_tid_text_extracts_first_valid_tid() -> None:
    assert parse_tid_text("TID 00001") == 1
    assert parse_tid_text("识别结果: 65535") == 65535
    assert parse_tid_text("65536") is None
    assert parse_tid_text("没有数字") is None


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


def test_auto_tid_runner_retries_seed_script_when_display_tid_not_in_threshold(tmp_path: Path) -> None:
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
        ),
        services=AutoTidRngServices(
            capture_seed=lambda: AutoTidSeedResult(seed=SeedPair64(1, 2), measured_at=10.0),
            search_id_states=lambda _seed, _threshold, _targets: searches.pop(0),
            run_script_text=lambda _text, name: scripts.append(name),
            monotonic=lambda: clock[0],
            sleep=sleep,
        ),
    )

    runner.run(max_steps=10)

    assert runner.progress.phase == AutoTidRngPhase.COMPLETED
    assert scripts == ["BDSP测种.txt", "BDSP测种.txt", "取名.txt"]
