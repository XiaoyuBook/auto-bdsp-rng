from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from auto_bdsp_rng.automation.auto_rng.models import (
    AutoRngConfig,
    AutoRngDecisionKind,
    AutoRngPhase,
    AutoRngSeedResult,
    AutoRngTarget,
    ShinyCheckResult,
)
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


def test_target_1800_delay_1400_fixed_flash_60_runs_script_at_340():
    target = AutoRngTarget(raw_target_advances=1800)

    decision = decide_target_advance(
        target,
        current_advances=0,
        fixed_delay=1400,
        fixed_flash_frames=60,
        max_wait_frames=300,
    )

    assert decision.kind == AutoRngDecisionKind.RUN_ADVANCE_SCRIPT
    assert decision.trigger_advances == 340
    assert decision.remaining_to_trigger == 340
    assert decision.requested_advances == 340


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
    hit_script.write_text("_闪帧 = 60\n", encoding="utf-8")
    services = AutoRngServices(
        capture_seed=lambda: AutoRngSeedResult(seed="seed-1", current_advances=835, npc=0),
        search_candidates=lambda _seed: [FakeState(1000)],
        reidentify=lambda _seed: AutoRngSeedResult(seed="seed-1", current_advances=835, npc=0),
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

    runner.run(max_steps=5)

    assert runner.progress.phase == AutoRngPhase.SEARCH_TARGET
    assert runner.progress.locked_target is None
    assert "最终剩余帧" in runner.progress.log_message
    assert "放弃本目标" in runner.progress.log_message


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
    hit_script.write_text("_闪帧 = 60\n", encoding="utf-8")
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
    assert runner.progress.phase == AutoRngPhase.COMPLETED


def test_runner_count_mode_stops_after_requested_no_candidate_loops(tmp_path):
    seed_script = tmp_path / "BDSP测种.txt"
    advance_script = tmp_path / "bdsp过帧.txt"
    hit_script = tmp_path / "谢米.txt"
    seed_script.write_text("A 100\n", encoding="utf-8")
    advance_script.write_text("_目标帧数 = 填写目标帧数\n", encoding="utf-8")
    hit_script.write_text("_闪帧 = 60\n", encoding="utf-8")
    scripts: list[str] = []
    services = AutoRngServices(
        capture_seed=lambda: AutoRngSeedResult(seed="seed-1", current_advances=0),
        search_candidates=lambda _seed: [],
        run_script_text=lambda _text, name: scripts.append(name),
    )
    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            seed_script_path=seed_script,
            advance_script_path=advance_script,
            hit_script_path=hit_script,
            loop_mode="count",
            loop_count=2,
        ),
        services=services,
    )

    runner.run(max_steps=10)

    assert runner.progress.phase == AutoRngPhase.COMPLETED
    assert runner.progress.loop_index == 2
    assert scripts == ["BDSP测种.txt", "BDSP测种.txt"]


def test_runner_starts_by_running_seed_script_before_capture(tmp_path):
    seed_script = tmp_path / "BDSP测种.txt"
    advance_script = tmp_path / "bdsp过帧.txt"
    hit_script = tmp_path / "谢米.txt"
    seed_script.write_text("A 100\n", encoding="utf-8")
    advance_script.write_text("_目标帧数 = 填写目标帧数\n", encoding="utf-8")
    hit_script.write_text("_闪帧 = 60\n", encoding="utf-8")
    events: list[str] = []
    services = AutoRngServices(
        capture_seed=lambda: events.append("capture") or AutoRngSeedResult(seed="seed-1", current_advances=0),
        search_candidates=lambda _seed: [],
        run_script_text=lambda _text, name: events.append(f"script:{name}"),
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

    runner.run(max_steps=2)

    assert events == ["script:BDSP测种.txt", "capture"]


def test_runner_can_start_first_cycle_from_capture_seed_then_resume_seed_script(tmp_path):
    seed_script = tmp_path / "BDSP测种.txt"
    advance_script = tmp_path / "bdsp过帧.txt"
    hit_script = tmp_path / "谢米.txt"
    seed_script.write_text("A 100\n", encoding="utf-8")
    advance_script.write_text("_目标帧数 = 填写目标帧数\n", encoding="utf-8")
    hit_script.write_text("_闪帧 = 60\n", encoding="utf-8")
    events: list[str] = []
    capture_count = 0

    def capture_seed() -> AutoRngSeedResult:
        nonlocal capture_count
        capture_count += 1
        events.append("capture")
        return AutoRngSeedResult(seed=f"seed-{capture_count}", current_advances=0, npc=0)

    services = AutoRngServices(
        capture_seed=capture_seed,
        search_candidates=lambda _seed: [FakeState(1300)],
        reidentify=lambda _seed: AutoRngSeedResult(seed="seed-reid", current_advances=0, npc=0),
        run_script_text=lambda _text, name: events.append(f"script:{name}"),
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
            start_phase=AutoRngPhase.CAPTURE_SEED,
        ),
        services=services,
    )

    runner.run(max_steps=9)

    assert events == [
        "capture",
        "script:谢米.txt",
        "script:BDSP测种.txt",
        "capture",
    ]
    assert runner.progress.phase != AutoRngPhase.RUN_SEED_SCRIPT
    assert runner.progress.loop_index == 2


def test_runner_runs_advance_script_then_reidentifies_when_request_is_within_threshold(tmp_path):
    seed_script = tmp_path / "BDSP测种.txt"
    advance_script = tmp_path / "bdsp过帧.txt"
    hit_script = tmp_path / "谢米.txt"
    seed_script.write_text("A 100\n", encoding="utf-8")
    advance_script.write_text("_目标帧数 = 填写目标帧数\n", encoding="utf-8")
    hit_script.write_text("_闪帧 = 60\n", encoding="utf-8")
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

    runner.run(max_steps=6)

    assert scripts == [("BDSP测种.txt", "A 100\n"), ("bdsp过帧.txt", "_目标帧数 = 840\n")]
    assert calls == ["reidentify"]
    assert runner.progress.phase == AutoRngPhase.DECIDE_ADVANCE
    assert runner.progress.current_advances == 600


def test_runner_final_calibrate_runs_fixed_hit_script(tmp_path):
    seed_script = tmp_path / "BDSP测种.txt"
    advance_script = tmp_path / "bdsp过帧.txt"
    hit_script = tmp_path / "谢米.txt"
    seed_script.write_text("A 100\n", encoding="utf-8")
    advance_script.write_text("_目标帧数 = 填写目标帧数\n", encoding="utf-8")
    hit_script.write_text("_闪帧 = 60\n", encoding="utf-8")
    scripts: list[tuple[str, str]] = []
    calls: list[str] = []
    services = AutoRngServices(
        capture_seed=lambda: AutoRngSeedResult(seed="seed-1", current_advances=0, npc=0),
        # 靶帧 1320：trigger=1320-1200-60=60，remaining=60==flash → 直接 FINAL_CALIBRATE
        search_candidates=lambda _seed: [FakeState(1320)],
        reidentify=lambda _seed: calls.append("reidentify") or AutoRngSeedResult(seed="seed-1", current_advances=0, npc=0),
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

    runner.run(max_steps=6)

    assert scripts == [("BDSP测种.txt", "A 100\n"), ("谢米.txt", "_闪帧 = 60\n")]
    assert calls == []
    assert runner.progress.phase == AutoRngPhase.LOOP_CHECK
    assert runner.progress.trigger_advances == 60


def test_runner_runs_fixed_hit_script_without_rewriting_flash_frames(tmp_path):
    seed_script = tmp_path / "BDSP测种.txt"
    advance_script = tmp_path / "bdsp过帧.txt"
    hit_script = tmp_path / "谢米.txt"
    seed_script.write_text("A 100\n", encoding="utf-8")
    advance_script.write_text("_目标帧数 = 填写目标帧数\n", encoding="utf-8")
    hit_script.write_text("_闪帧=60\nA 100\n", encoding="utf-8")
    scripts: list[tuple[str, str]] = []
    services = AutoRngServices(
        capture_seed=lambda: AutoRngSeedResult(seed="seed-1", current_advances=0, npc=0),
        # raw=1520: trigger=1520-1400-60=60, remaining=60==flash → FINAL_CALIBRATE
        search_candidates=lambda _seed: [FakeState(1520)],
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
            fixed_delay=1400,
            fixed_flash_frames=60,
            max_wait_frames=400,
            min_final_flash_frames=5,
        ),
        services=services,
    )

    runner.run(max_steps=6)

    assert scripts == [("BDSP测种.txt", "A 100\n"), ("谢米.txt", "_闪帧=60\nA 100\n")]
    assert runner.progress.trigger_advances == 60
    assert runner.progress.final_flash_frames == 60


def test_runner_uses_flash_frames_from_hit_script_for_trigger_timing(tmp_path):
    seed_script = tmp_path / "BDSP测种.txt"
    advance_script = tmp_path / "bdsp过帧.txt"
    hit_script = tmp_path / "谢米.txt"
    seed_script.write_text("A 100\n", encoding="utf-8")
    advance_script.write_text("_目标帧数 = 填写目标帧数\n", encoding="utf-8")
    hit_script.write_text("_闪帧 = 30\nA 100\n", encoding="utf-8")
    scripts: list[tuple[str, str]] = []
    services = AutoRngServices(
        capture_seed=lambda: AutoRngSeedResult(seed="seed-1", current_advances=0, npc=0),
        # raw=1460: trigger=1460-1400-30=30, remaining=30==flash → FINAL_CALIBRATE
        search_candidates=lambda _seed: [FakeState(1460)],
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
            fixed_delay=1400,
            fixed_flash_frames=60,
            max_wait_frames=400,
            min_final_flash_frames=5,
        ),
        services=services,
    )

    runner.run(max_steps=6)

    assert scripts == [("BDSP测种.txt", "A 100\n"), ("谢米.txt", "_闪帧 = 30\nA 100\n")]
    assert runner.progress.trigger_advances == 30
    assert runner.progress.final_flash_frames == 30


def test_runner_does_not_reidentify_again_after_entering_final_calibrate(tmp_path):
    seed_script = tmp_path / "BDSP测种.txt"
    advance_script = tmp_path / "bdsp过帧.txt"
    hit_script = tmp_path / "谢米.txt"
    seed_script.write_text("A 100\n", encoding="utf-8")
    advance_script.write_text("_目标帧数 = 填写目标帧数\n", encoding="utf-8")
    hit_script.write_text("_闪帧 = 60\n", encoding="utf-8")
    calls: list[str] = []
    scripts: list[tuple[str, str]] = []
    services = AutoRngServices(
        capture_seed=lambda: AutoRngSeedResult(seed="seed-1", current_advances=0, npc=0),
        # raw=820: trigger=820-100-60=660, remaining=660-600=60==flash → FINAL_CALIBRATE
        search_candidates=lambda _seed: [FakeState(820)],
        reidentify=lambda _seed: calls.append("reidentify") or AutoRngSeedResult(seed="seed-1", current_advances=600, npc=0),
        run_script_text=lambda text, name: scripts.append((name, text)),
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
            min_final_flash_frames=5,
        ),
        services=services,
    )

    runner.run(max_steps=9)

    assert calls == ["reidentify"]
    assert scripts == [
        ("BDSP测种.txt", "A 100\n"),
        ("bdsp过帧.txt", "_目标帧数 = 840\n"),
        ("谢米.txt", "_闪帧 = 60\n"),
    ]
    assert runner.progress.phase == AutoRngPhase.LOOP_CHECK


def test_runner_recomputes_hit_start_at_script_start_using_whole_elapsed_frames(tmp_path):
    seed_script = tmp_path / "BDSP测种.txt"
    advance_script = tmp_path / "bdsp过帧.txt"
    hit_script = tmp_path / "谢米.txt"
    seed_script.write_text("A 100\n", encoding="utf-8")
    advance_script.write_text("_目标帧数 = 填写目标帧数\n", encoding="utf-8")
    hit_script.write_text("_闪帧 = 60\n", encoding="utf-8")
    scripts: list[tuple[str, str]] = []
    monotonic_values = iter([10.0, 10.0 + (5.9 * 1.018)])
    services = AutoRngServices(
        capture_seed=lambda: AutoRngSeedResult(seed="seed-1", current_advances=0, npc=0, measured_at=0.0),
        search_candidates=lambda _seed: [FakeState(1000)],
        reidentify=lambda _seed: AutoRngSeedResult(seed="seed-1", current_advances=834, npc=0, measured_at=10.0),
        run_script_text=lambda text, name: scripts.append((name, text)),
        monotonic=lambda: next(monotonic_values),
    )
    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            seed_script_path=seed_script,
            advance_script_path=advance_script,
            hit_script_path=hit_script,
            fixed_delay=100,
            max_wait_frames=300,
            min_final_flash_frames=1,
        ),
        services=services,
    )

    runner.run(max_steps=9)

    assert scripts[-1] == ("谢米.txt", "_闪帧 = 60\n")
    assert runner.progress.current_advances == 839
    assert runner.progress.remaining_to_trigger == 1
    assert runner.progress.final_flash_frames == 60


def test_runner_single_mode_completes_after_hit_script(tmp_path):
    seed_script = tmp_path / "BDSP测种.txt"
    advance_script = tmp_path / "bdsp过帧.txt"
    hit_script = tmp_path / "谢米.txt"
    seed_script.write_text("A 100\n", encoding="utf-8")
    advance_script.write_text("_目标帧数 = 填写目标帧数\n", encoding="utf-8")
    hit_script.write_text("_闪帧 = 60\n", encoding="utf-8")
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

    runner.run(max_steps=7)

    assert runner.progress.phase == AutoRngPhase.COMPLETED
    assert runner.progress.loop_index == 1


def test_runner_count_mode_runs_requested_number_of_loops(tmp_path):
    seed_script = tmp_path / "BDSP测种.txt"
    advance_script = tmp_path / "bdsp过帧.txt"
    hit_script = tmp_path / "谢米.txt"
    seed_script.write_text("A 100\n", encoding="utf-8")
    advance_script.write_text("_目标帧数 = 填写目标帧数\n", encoding="utf-8")
    hit_script.write_text("_闪帧 = 60\n", encoding="utf-8")
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

    runner.run(max_steps=14)

    assert runner.progress.phase == AutoRngPhase.COMPLETED
    assert runner.progress.loop_index == 2
    assert scripts == ["BDSP测种.txt", "谢米.txt", "BDSP测种.txt", "谢米.txt"]


def test_runner_uses_hit_monitor_and_restarts_seed_script_when_not_shiny(tmp_path):
    seed_script = tmp_path / "BDSP测种.txt"
    advance_script = tmp_path / "bdsp过帧.txt"
    hit_script = tmp_path / "谢米.txt"
    seed_script.write_text("A 100\n", encoding="utf-8")
    advance_script.write_text("_目标帧数 = 填写目标帧数\n", encoding="utf-8")
    hit_script.write_text("_闪帧 = 60\n", encoding="utf-8")
    scripts: list[str] = []
    monitor_calls: list[tuple[str, str, float]] = []
    services = AutoRngServices(
        capture_seed=lambda: AutoRngSeedResult(seed="seed-1", current_advances=0, npc=0),
        search_candidates=lambda _seed: [FakeState(1300)],
        reidentify=lambda _seed: AutoRngSeedResult(seed="seed-1", current_advances=0, npc=0),
        run_script_text=lambda _text, name: scripts.append(name),
        run_hit_script_with_shiny_check=lambda text, name, threshold: monitor_calls.append((text, name, threshold))
        or ShinyCheckResult(is_shiny=False, interval_seconds=2.3),
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
            loop_mode="infinite",
            shiny_threshold_seconds=2.8,
        ),
        services=services,
    )

    runner.run(max_steps=7)

    assert monitor_calls == [("_闪帧 = 60\n", "谢米.txt", 2.8)]
    assert scripts == ["BDSP测种.txt", "BDSP测种.txt"]
    assert runner.progress.phase == AutoRngPhase.CAPTURE_SEED
    assert runner.progress.loop_index == 1


def test_runner_stops_after_hit_monitor_reports_shiny(tmp_path):
    seed_script = tmp_path / "BDSP测种.txt"
    advance_script = tmp_path / "bdsp过帧.txt"
    hit_script = tmp_path / "谢米.txt"
    seed_script.write_text("A 100\n", encoding="utf-8")
    advance_script.write_text("_目标帧数 = 填写目标帧数\n", encoding="utf-8")
    hit_script.write_text("_闪帧 = 60\n", encoding="utf-8")
    services = AutoRngServices(
        capture_seed=lambda: AutoRngSeedResult(seed="seed-1", current_advances=0, npc=0),
        search_candidates=lambda _seed: [FakeState(1300)],
        reidentify=lambda _seed: AutoRngSeedResult(seed="seed-1", current_advances=0, npc=0),
        run_script_text=lambda _text, _name: None,
        run_hit_script_with_shiny_check=lambda _text, _name, _threshold: ShinyCheckResult(
            is_shiny=True,
            interval_seconds=4.2,
        ),
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
            loop_mode="infinite",
            shiny_threshold_seconds=3.5,
        ),
        services=services,
    )

    runner.run(max_steps=7)

    assert runner.progress.phase == AutoRngPhase.COMPLETED
    assert runner.progress.loop_index == 1
    assert "4.200" in runner.progress.log_message


# ─── decide_target_advance 三段式决策 ──────────────────────────────

def test_bug_repro_raw11915_current10309_remaining94_should_final_wait_94_not_24():
    """raw=11915 delay=1452 flash=60 current=10309 → script_trigger=10403, remaining=94, FINAL_WAIT wait=94（不再扣闪帧）。"""
    target = AutoRngTarget(raw_target_advances=11915)

    decision = decide_target_advance(
        target,
        current_advances=10309,
        fixed_delay=1452,
        fixed_flash_frames=60,
        max_wait_frames=500,
    )

    # 脚本启动帧 = 11915 - 1452 - 60 = 10403
    # 还需过 = 10403 - 10309 = 94
    assert decision.trigger_advances == 10403
    assert decision.remaining_to_trigger == 94
    assert decision.kind == AutoRngDecisionKind.FINAL_WAIT
    assert "94" in decision.message  # wait 94帧，不是 24帧


def test_bug_repro_raw3674_current2078_remaining84_should_final_wait_84():
    """raw=3674 delay=1452 flash=60 current=2078 → 脚本启动帧=2162，还需过=84，等待84帧。"""
    target = AutoRngTarget(raw_target_advances=3674)

    decision = decide_target_advance(
        target,
        current_advances=2078,
        fixed_delay=1452,
        fixed_flash_frames=60,
        max_wait_frames=500,
    )

    assert decision.trigger_advances == 3674 - 1452 - 60  # 2162
    assert decision.remaining_to_trigger == 84
    assert decision.kind == AutoRngDecisionKind.FINAL_WAIT
    assert "84" in decision.message


def test_remaining_equal_flash_triggers_final_calibrate():
    """remaining == fixed_flash_frames 时进入 FINAL_CALIBRATE。"""
    target = AutoRngTarget(raw_target_advances=11915)

    decision = decide_target_advance(
        target,
        current_advances=10403,
        fixed_delay=1452,
        fixed_flash_frames=60,
        max_wait_frames=500,
    )

    assert decision.remaining_to_trigger == 60
    assert decision.kind == AutoRngDecisionKind.FINAL_CALIBRATE
    assert decision.phase == AutoRngPhase.FINAL_CALIBRATE


def test_remaining_less_than_flash_triggers_target_missed():
    """remaining < fixed_flash_frames 时已错过脚本启动窗口。"""
    target = AutoRngTarget(raw_target_advances=11915)

    decision = decide_target_advance(
        target,
        current_advances=10410,
        fixed_delay=1452,
        fixed_flash_frames=60,
        max_wait_frames=500,
    )

    assert decision.remaining_to_trigger == 53  # < 60
    assert decision.kind == AutoRngDecisionKind.TARGET_MISSED
    assert decision.phase == AutoRngPhase.SEARCH_TARGET


def test_still_runs_advance_script_when_remaining_exceeds_max_wait():
    """remaining > max_wait_frames 时仍走过帧脚本。"""
    target = AutoRngTarget(raw_target_advances=20000)

    decision = decide_target_advance(
        target,
        current_advances=0,
        fixed_delay=1452,
        fixed_flash_frames=60,
        max_wait_frames=500,
    )

    assert decision.remaining_to_trigger > 500
    assert decision.kind == AutoRngDecisionKind.RUN_ADVANCE_SCRIPT
    assert decision.requested_advances == decision.remaining_to_trigger


def test_fixed_flash_zero_goes_directly_to_final_calibrate():
    """fixed_flash_frames=0 时，remaining <= max_wait 直接进入 FINAL_CALIBRATE。"""
    target = AutoRngTarget(raw_target_advances=1000)

    decision = decide_target_advance(
        target,
        current_advances=600,
        fixed_delay=100,
        fixed_flash_frames=0,
        max_wait_frames=300,
    )

    assert decision.remaining_to_trigger == 300
    assert decision.kind == AutoRngDecisionKind.FINAL_CALIBRATE
    assert decision.phase == AutoRngPhase.FINAL_CALIBRATE


# ─── final_wait 流程集成测试 ──────────────────────────────────────

def test_runner_final_wait_flows_directly_to_run_hit(tmp_path):
    """过帧后 remaining > flash → FINAL_WAIT（等待全部remaining）→ RUN_HIT_SCRIPT。"""
    seed_script = tmp_path / "BDSP测种.txt"
    advance_script = tmp_path / "bdsp过帧.txt"
    hit_script = tmp_path / "谢米.txt"
    seed_script.write_text("A 100\n", encoding="utf-8")
    # 过帧脚本含 _目标帧数 - 300 偏移
    advance_script.write_text("_目标帧数 = 填写目标帧数\n$目标帧数 = _目标帧数 - 300\nA 100\n", encoding="utf-8")
    hit_script.write_text("_闪帧 = 60\n", encoding="utf-8")

    scripts: list[tuple[str, str]] = []
    calls: list[str] = []
    services = AutoRngServices(
        capture_seed=lambda: AutoRngSeedResult(seed="seed-1", current_advances=0),
        # raw=11915: trigger=10403, reidentify后current=10309, remaining=94>60 → FINAL_WAIT
        search_candidates=lambda _seed: [FakeState(11915)],
        reidentify=lambda _seed: calls.append("reidentify") or AutoRngSeedResult(
            seed="seed-1", current_advances=10309, npc=0,
        ),
        run_script_text=lambda text, name: scripts.append((name, text)),
        monotonic=lambda: 10.0,
    )
    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            seed_script_path=seed_script,
            advance_script_path=advance_script,
            hit_script_path=hit_script,
            fixed_delay=1452,
            fixed_flash_frames=60,
            max_wait_frames=500,
            min_final_flash_frames=5,
        ),
        services=services,
    )

    runner.run(max_steps=10)

    names = [n for n, _ in scripts]
    assert any("谢米.txt" in n for n in names), f"expected hit script, got {names}"
    assert runner.progress.phase in (AutoRngPhase.LOOP_CHECK, AutoRngPhase.RUN_HIT_SCRIPT)


def test_final_calibrate_update_resets_measured_at_after_wait(tmp_path):
    """FINAL_WAIT 后 measured_at 被重置，finalize_flash_frames 的 elapsed 接近 0。"""
    target = AutoRngTarget(raw_target_advances=11915)

    # 模拟 FINAL_WAIT 后刚重置 measured_at 的场景
    now = 10.0
    decision = finalize_flash_frames(
        target,
        fixed_delay=1452,
        fixed_flash_frames=60,
        current_advances_at_ref=10403,
        ref_time=now,  # measured_at 刚被重置
        now_monotonic=now,  # 没有经过时间
        npc=1,
        min_final_flash_frames=5,
    )

    assert decision.kind == AutoRngDecisionKind.RUN_HIT_SCRIPT
    assert decision.trigger_advances == 10403
    assert decision.remaining_to_trigger == 0  # current = trigger
    assert decision.flash_frames == 60  # 固定 _闪帧


# ─── reidentify expected_advances_hint ─────────────────────────────

def test_reidentify_passes_expected_advances_hint_to_service():
    """过帧后 reidentify 应传递 expected_advances_hint。"""
    # 验证 runner._reidentify 设置了 expected_advances_hint
    # 通过检查 services.reidentify 收到的参数来验证
    captured: list[AutoRngSeedResult] = []

    def fake_reidentify(seed: AutoRngSeedResult) -> AutoRngSeedResult:
        captured.append(seed)
        return AutoRngSeedResult(seed=seed.seed, current_advances=11000)

    # 构建一个简单的 runner 并手动调用 _reidentify
    from auto_bdsp_rng.automation.auto_rng.runner import AutoRngRunner
    runner = AutoRngRunner(
        AutoRngConfig(script_dir=Path(".")),
        services=AutoRngServices(reidentify=fake_reidentify),
    )
    runner._seed_result = AutoRngSeedResult(seed="s", current_advances=100)
    runner._requested_advances = 1000
    runner._reidentify(AutoRngPhase.DECIDE_ADVANCE)

    assert len(captured) == 1
    assert captured[0].expected_advances_hint == 100 + 1000  # 1100


# ─── 动态 _闪帧 调整 ──────────────────────────────────────────────

def test_remaining_20_with_flash_60_adjusts_to_19():
    """remaining=20 < flash=60 但 >= 6 → FINAL_ADJUST（new_flash=19）。"""
    target = AutoRngTarget(raw_target_advances=11915)

    decision = decide_target_advance(
        target,
        current_advances=10383,
        fixed_delay=1452,
        fixed_flash_frames=60,
        max_wait_frames=500,
    )

    # trigger = 11915 - 1452 - 60 = 10403，remaining = 10403 - 10383 = 20
    assert decision.remaining_to_trigger == 20
    assert decision.kind == AutoRngDecisionKind.FINAL_ADJUST
    assert decision.phase == AutoRngPhase.FINAL_ADJUST
    assert "动态调整" in decision.message
    assert "19" in decision.message


def test_remaining_6_with_flash_60_adjusts_to_5():
    """remaining=6 → new_flash=5（最小可调整闪帧）。"""
    target = AutoRngTarget(raw_target_advances=11915)

    decision = decide_target_advance(
        target,
        current_advances=10397,
        fixed_delay=1452,
        fixed_flash_frames=60,
        max_wait_frames=500,
    )

    assert decision.remaining_to_trigger == 6
    assert decision.kind == AutoRngDecisionKind.FINAL_ADJUST


def test_remaining_5_with_flash_60_is_target_missed():
    """remaining=5 < min_adjustable+1=6 → TARGET_MISSED。"""
    target = AutoRngTarget(raw_target_advances=11915)

    decision = decide_target_advance(
        target,
        current_advances=10398,
        fixed_delay=1452,
        fixed_flash_frames=60,
        max_wait_frames=500,
    )

    assert decision.remaining_to_trigger == 5
    assert decision.kind == AutoRngDecisionKind.TARGET_MISSED


def test_remaining_2_with_flash_60_is_target_missed():
    """remaining=2 << flash=60 → TARGET_MISSED。"""
    target = AutoRngTarget(raw_target_advances=11915)

    decision = decide_target_advance(
        target,
        current_advances=10401,
        fixed_delay=1452,
        fixed_flash_frames=60,
        max_wait_frames=500,
    )

    assert decision.remaining_to_trigger == 2
    assert decision.kind == AutoRngDecisionKind.TARGET_MISSED
    assert "放弃" in decision.message


# ─── 动态 _闪帧 调整集成测试 ──────────────────────────────────────

def test_runner_final_adjust_dynamic_flash(tmp_path):
    """FINAL_ADJUST 动态写入 _闪帧=19 并运行撞闪脚本。"""
    seed_script = tmp_path / "BDSP测种.txt"
    advance_script = tmp_path / "bdsp过帧.txt"
    hit_script = tmp_path / "谢米.txt"
    seed_script.write_text("A 100\n", encoding="utf-8")
    advance_script.write_text("_目标帧数 = 填写目标帧数\n", encoding="utf-8")
    hit_script.write_text("_闪帧 = 60\n", encoding="utf-8")

    scripts: list[tuple[str, str]] = []
    services = AutoRngServices(
        capture_seed=lambda: AutoRngSeedResult(seed="seed-1", current_advances=0),
        search_candidates=lambda _seed: [FakeState(11915)],
        reidentify=lambda _seed: AutoRngSeedResult(
            seed="seed-1", current_advances=10383, npc=0,
        ),
        run_script_text=lambda text, name: scripts.append((name, text)),
        monotonic=lambda: 10.0,
    )
    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            seed_script_path=seed_script,
            advance_script_path=advance_script,
            hit_script_path=hit_script,
            fixed_delay=1452,
            max_wait_frames=500,
            min_final_flash_frames=5,
        ),
        services=services,
    )

    runner.run(max_steps=10)

    # 确认撞闪脚本被调用，且 _闪帧 被动态改为 19
    hit_texts = [t for n, t in scripts if "谢米" in n]
    assert hit_texts, f"expected hit script call, got {scripts}"
    assert "_闪帧 = 19" in hit_texts[0], f"expected dynamic flash 19, got {hit_texts[0]}"
    assert runner.progress.phase in (AutoRngPhase.LOOP_CHECK, AutoRngPhase.RUN_HIT_SCRIPT)
