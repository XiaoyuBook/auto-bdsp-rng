from __future__ import annotations

from dataclasses import dataclass

from auto_bdsp_rng.automation.auto_rng.models import AutoRngConfig, AutoRngDecisionKind, AutoRngPhase, AutoRngSeedResult, AutoRngTarget
from auto_bdsp_rng.automation.auto_rng.runner import (
    AutoRngRunner,
    AutoRngServices,
    decide_after_advance_script,
    decide_search_target,
    decide_target_advance,
    finalize_flash_frames,
)


@dataclass(frozen=True)
class FakeState:
    advances: int


def test_target_1000_delay_100_current_0_runs_advance_script():
    target = AutoRngTarget(raw_target_advances=1000)

    decision = decide_target_advance(target, current_advances=0, fixed_delay=100, max_wait_frames=300)

    assert decision.kind == AutoRngDecisionKind.RUN_ADVANCE_SCRIPT
    assert decision.phase == AutoRngPhase.RUN_ADVANCE_SCRIPT
    assert decision.trigger_advances == 900
    assert decision.remaining_to_trigger == 900
    assert decision.requested_advances == 900


def test_target_1000_delay_100_current_600_enters_final_calibrate():
    target = AutoRngTarget(raw_target_advances=1000)

    decision = decide_target_advance(target, current_advances=600, fixed_delay=100, max_wait_frames=300)

    assert decision.kind == AutoRngDecisionKind.FINAL_CALIBRATE
    assert decision.phase == AutoRngPhase.FINAL_CALIBRATE
    assert decision.remaining_to_trigger == 300
    assert decision.flash_frames is None


def test_target_1300_delay_1200_current_0_final_flash_frames_are_100_without_elapsed_time():
    target = AutoRngTarget(raw_target_advances=1300)

    decision = finalize_flash_frames(
        target,
        fixed_delay=1200,
        current_advances_at_ref=0,
        ref_time=10.0,
        now_monotonic=10.0,
        npc=0,
        min_final_flash_frames=30,
    )

    assert decision.kind == AutoRngDecisionKind.RUN_HIT_SCRIPT
    assert decision.trigger_advances == 100
    assert decision.flash_frames == 100


def test_final_calibrate_subtracts_elapsed_advances_from_flash_frames():
    target = AutoRngTarget(raw_target_advances=1000)

    decision = finalize_flash_frames(
        target,
        fixed_delay=100,
        current_advances_at_ref=600,
        ref_time=1.0,
        now_monotonic=3.036,
        npc=0,
        min_final_flash_frames=30,
    )

    assert decision.kind == AutoRngDecisionKind.RUN_HIT_SCRIPT
    assert decision.flash_frames == 298


def test_final_calibrate_does_not_run_hit_script_after_missing_target():
    target = AutoRngTarget(raw_target_advances=1000)

    decision = finalize_flash_frames(
        target,
        fixed_delay=100,
        current_advances_at_ref=901,
        ref_time=1.0,
        now_monotonic=1.0,
        npc=0,
        min_final_flash_frames=30,
    )

    assert decision.kind == AutoRngDecisionKind.TARGET_MISSED


def test_final_calibrate_does_not_run_hit_script_when_too_close():
    target = AutoRngTarget(raw_target_advances=1000)

    decision = finalize_flash_frames(
        target,
        fixed_delay=100,
        current_advances_at_ref=875,
        ref_time=1.0,
        now_monotonic=1.0,
        npc=0,
        min_final_flash_frames=30,
    )

    assert decision.kind == AutoRngDecisionKind.TARGET_TOO_CLOSE
    assert decision.flash_frames == 25


def test_runner_clears_locked_target_when_final_calibrate_abandons_target(tmp_path):
    seed_script = tmp_path / "BDSP测种.txt"
    advance_script = tmp_path / "bdsp过帧.txt"
    hit_script = tmp_path / "谢米.txt"
    seed_script.write_text("A 100\n", encoding="utf-8")
    advance_script.write_text("_目标帧数 = 填写目标帧数\n", encoding="utf-8")
    hit_script.write_text("_闪帧 = 填入这里\n", encoding="utf-8")
    services = AutoRngServices(
        capture_seed=lambda: AutoRngSeedResult(seed="seed-1", current_advances=600, npc=0),
        search_candidates=lambda _seed: [FakeState(1000)],
        reidentify=lambda _seed: AutoRngSeedResult(seed="seed-1", current_advances=875, npc=0),
        run_script_text=lambda _text, _name: None,
        monotonic=lambda: 10.0,
    )
    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            seed_script_path=seed_script,
            advance_script_path=advance_script,
            hit_script_path=hit_script,
            fixed_delay=100,
            max_wait_frames=300,
            min_final_flash_frames=30,
        ),
        services=services,
    )

    runner.run(max_steps=4)

    assert runner.progress.phase == AutoRngPhase.SEARCH_TARGET
    assert runner.progress.locked_target is None
    assert runner.progress.log_message == "最终剩余帧过近，放弃本目标"


def test_no_candidates_decides_to_run_seed_script():
    decision = decide_search_target([])

    assert decision.kind == AutoRngDecisionKind.RUN_SEED_SCRIPT
    assert decision.phase == AutoRngPhase.RUN_SEED_SCRIPT


def test_advance_request_above_threshold_recaptures_seed():
    decision = decide_after_advance_script(990_001, reseed_threshold_frames=990_000)

    assert decision.kind == AutoRngDecisionKind.CAPTURE_SEED
    assert decision.phase == AutoRngPhase.CAPTURE_SEED


def test_advance_request_at_threshold_reidentifies():
    decision = decide_after_advance_script(990_000, reseed_threshold_frames=990_000)

    assert decision.kind == AutoRngDecisionKind.REIDENTIFY
    assert decision.phase == AutoRngPhase.REIDENTIFY


def test_runner_runs_seed_script_when_search_has_no_candidates(tmp_path):
    seed_script = tmp_path / "BDSP测种.txt"
    advance_script = tmp_path / "bdsp过帧.txt"
    hit_script = tmp_path / "谢米.txt"
    seed_script.write_text("A 100\n", encoding="utf-8")
    advance_script.write_text("_目标帧数 = 填写目标帧数\n", encoding="utf-8")
    hit_script.write_text("_闪帧 = 填入这里\n", encoding="utf-8")
    calls: list[str] = []
    scripts: list[tuple[str, str]] = []
    services = AutoRngServices(
        capture_seed=lambda: calls.append("capture") or AutoRngSeedResult(seed="seed-1", current_advances=0),
        search_candidates=lambda _seed: [],
        run_script_text=lambda text, name: scripts.append((name, text)),
    )
    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            seed_script_path=seed_script,
            advance_script_path=advance_script,
            hit_script_path=hit_script,
        ),
        services=services,
    )

    runner.run(max_steps=3)

    assert calls == ["capture"]
    assert scripts == [("BDSP测种.txt", "A 100\n")]
    assert runner.progress.phase == AutoRngPhase.CAPTURE_SEED


def test_runner_runs_advance_script_then_reidentifies_when_request_is_within_threshold(tmp_path):
    seed_script = tmp_path / "BDSP测种.txt"
    advance_script = tmp_path / "bdsp过帧.txt"
    hit_script = tmp_path / "谢米.txt"
    seed_script.write_text("A 100\n", encoding="utf-8")
    advance_script.write_text("_目标帧数 = 填写目标帧数\n", encoding="utf-8")
    hit_script.write_text("_闪帧 = 填入这里\n", encoding="utf-8")
    scripts: list[tuple[str, str]] = []
    calls: list[str] = []
    services = AutoRngServices(
        capture_seed=lambda: AutoRngSeedResult(seed="seed-1", current_advances=0),
        search_candidates=lambda _seed: [FakeState(1000)],
        reidentify=lambda _seed: calls.append("reidentify") or AutoRngSeedResult(seed="seed-1", current_advances=600),
        run_script_text=lambda text, name: scripts.append((name, text)),
    )
    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            seed_script_path=seed_script,
            advance_script_path=advance_script,
            hit_script_path=hit_script,
            fixed_delay=100,
            max_wait_frames=300,
        ),
        services=services,
    )

    runner.run(max_steps=5)

    assert scripts == [("bdsp过帧.txt", "_目标帧数 = 900\n")]
    assert calls == ["reidentify"]
    assert runner.progress.phase == AutoRngPhase.DECIDE_ADVANCE
    assert runner.progress.current_advances == 600


def test_runner_final_calibrate_runs_hit_script_with_live_flash_frames(tmp_path):
    seed_script = tmp_path / "BDSP测种.txt"
    advance_script = tmp_path / "bdsp过帧.txt"
    hit_script = tmp_path / "谢米.txt"
    seed_script.write_text("A 100\n", encoding="utf-8")
    advance_script.write_text("_目标帧数 = 填写目标帧数\n", encoding="utf-8")
    hit_script.write_text("_闪帧 = 填入这里\n", encoding="utf-8")
    scripts: list[tuple[str, str]] = []
    services = AutoRngServices(
        capture_seed=lambda: AutoRngSeedResult(seed="seed-1", current_advances=0, npc=0),
        search_candidates=lambda _seed: [FakeState(1300)],
        reidentify=lambda _seed: AutoRngSeedResult(seed="seed-1", current_advances=0, npc=0),
        run_script_text=lambda text, name: scripts.append((name, text)),
        monotonic=lambda: 10.0,
    )
    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            seed_script_path=seed_script,
            advance_script_path=advance_script,
            hit_script_path=hit_script,
            fixed_delay=1200,
            max_wait_frames=300,
            min_final_flash_frames=30,
        ),
        services=services,
    )

    runner.run(max_steps=5)

    assert scripts == [("谢米.txt", "_闪帧 = 100\n")]
    assert runner.progress.phase == AutoRngPhase.LOOP_CHECK
    assert runner.progress.final_flash_frames == 100


def test_runner_single_mode_completes_after_hit_script(tmp_path):
    seed_script = tmp_path / "BDSP测种.txt"
    advance_script = tmp_path / "bdsp过帧.txt"
    hit_script = tmp_path / "谢米.txt"
    seed_script.write_text("A 100\n", encoding="utf-8")
    advance_script.write_text("_目标帧数 = 填写目标帧数\n", encoding="utf-8")
    hit_script.write_text("_闪帧 = 填入这里\n", encoding="utf-8")
    services = AutoRngServices(
        capture_seed=lambda: AutoRngSeedResult(seed="seed-1", current_advances=0, npc=0),
        search_candidates=lambda _seed: [FakeState(1300)],
        reidentify=lambda _seed: AutoRngSeedResult(seed="seed-1", current_advances=0, npc=0),
        run_script_text=lambda _text, _name: None,
        monotonic=lambda: 10.0,
    )
    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            seed_script_path=seed_script,
            advance_script_path=advance_script,
            hit_script_path=hit_script,
            fixed_delay=1200,
            max_wait_frames=300,
        ),
        services=services,
    )

    runner.run(max_steps=6)

    assert runner.progress.phase == AutoRngPhase.COMPLETED
    assert runner.progress.loop_index == 1


def test_runner_count_mode_runs_requested_number_of_loops(tmp_path):
    seed_script = tmp_path / "BDSP测种.txt"
    advance_script = tmp_path / "bdsp过帧.txt"
    hit_script = tmp_path / "谢米.txt"
    seed_script.write_text("A 100\n", encoding="utf-8")
    advance_script.write_text("_目标帧数 = 填写目标帧数\n", encoding="utf-8")
    hit_script.write_text("_闪帧 = 填入这里\n", encoding="utf-8")
    scripts: list[str] = []
    services = AutoRngServices(
        capture_seed=lambda: AutoRngSeedResult(seed="seed-1", current_advances=0, npc=0),
        search_candidates=lambda _seed: [FakeState(1300)],
        reidentify=lambda _seed: AutoRngSeedResult(seed="seed-1", current_advances=0, npc=0),
        run_script_text=lambda _text, name: scripts.append(name),
        monotonic=lambda: 10.0,
    )
    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            seed_script_path=seed_script,
            advance_script_path=advance_script,
            hit_script_path=hit_script,
            fixed_delay=1200,
            max_wait_frames=300,
            loop_mode="count",
            loop_count=2,
        ),
        services=services,
    )

    runner.run(max_steps=12)

    assert runner.progress.phase == AutoRngPhase.COMPLETED
    assert runner.progress.loop_index == 2
    assert scripts == ["谢米.txt", "谢米.txt"]
