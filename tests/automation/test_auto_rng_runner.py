from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from auto_bdsp_rng.automation.auto_rng.models import (
    AutoRngConfig,
    AutoRngDecisionKind,
    AutoRngPhase,
    AutoRngProgress,
    AutoRngSeedResult,
    AutoRngTarget,
    ShinyCheckResult,
)
from auto_bdsp_rng.automation.auto_rng.runner import (
    AutoRngRunner,
    AutoRngServices,
    ProjectXsAdvanceCounter,
    ProjectXsTimelineAdvanceCounter,
    decide_after_advance_script,
    decide_search_target,
    decide_target_advance,
    finalize_flash_frames,
    is_linear_trigger_reachable,
)
from auto_bdsp_rng.automation.auto_rng.scripts import AUTO_ADVANCE_PARAMETER, AUTO_HIT_PARAMETER
from auto_bdsp_rng.blink_detection.project_xs import plan_timeline
from auto_bdsp_rng.rng_core.seed import SeedState32


@dataclass(frozen=True)
class FakeState:
    advances: int
    ec: int = 0
    pid: int = 0
    nature: int = 0


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
        fixed_flash_frames=60,
        current_advances_at_ref=825,
        ref_time=1.0,
        now_monotonic=1.0,
        npc=0,
        min_final_flash_frames=30,
    )

    assert decision.kind == AutoRngDecisionKind.TARGET_TOO_CLOSE
    assert decision.flash_frames == 60


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

    runner.run(max_steps=4)

    assert runner.progress.phase == AutoRngPhase.SEARCH_TARGET
    assert runner.progress.locked_target is None
    assert "不足 6 帧" in runner.progress.log_message
    assert "放弃" in runner.progress.log_message


def test_no_candidates_decides_to_run_seed_script():
    decision = decide_search_target([])

    assert decision.kind == AutoRngDecisionKind.RUN_SEED_SCRIPT
    assert decision.phase == AutoRngPhase.RUN_SEED_SCRIPT


@pytest.mark.parametrize(
    ("target_advances", "expected"),
    [(1541, False), (1542, True), (1543, False)],
)
def test_linear_trigger_reachability_uses_script_start_frame(target_advances, expected):
    """npc=1 时按脚本启动帧的奇偶性筛选，而不是按 raw Adv 盲算。"""
    assert is_linear_trigger_reachable(
        target_advances,
        current_advances=0,
        npc=1,
        fixed_delay=195,
        fixed_flash_frames=5,
    ) is expected


def test_linear_trigger_reachability_respects_current_frame_and_npc_zero():
    assert is_linear_trigger_reachable(
        1542,
        current_advances=4,
        npc=1,
        fixed_delay=195,
        fixed_flash_frames=5,
    )
    assert not is_linear_trigger_reachable(
        1541,
        current_advances=4,
        npc=1,
        fixed_delay=195,
        fixed_flash_frames=5,
    )
    assert is_linear_trigger_reachable(
        1541,
        current_advances=0,
        npc=0,
        fixed_delay=195,
        fixed_flash_frames=5,
    )


def test_runner_excludes_unreachable_linear_candidates(tmp_path):
    seed_script = tmp_path / "seed.txt"
    hit_script = tmp_path / "hit.txt"
    seed_script.write_text("SEED\n", encoding="utf-8")
    hit_script.write_text("_闪帧 = 5\n", encoding="utf-8")
    history: list[tuple[str, tuple[object, ...]]] = []

    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            seed_script_path=seed_script,
            hit_script_path=hit_script,
            fixed_delay=195,
            start_phase=AutoRngPhase.CAPTURE_SEED,
        ),
        services=AutoRngServices(
            capture_seed=lambda: AutoRngSeedResult(seed="seed-1", current_advances=0, npc=1),
            # 两个 Adv 使用同一 PID+EC，验证不可达低帧不会阻塞可达高帧。
            search_candidates=lambda _seed: [FakeState(1541), FakeState(1542)],
            run_script_text=lambda _text, _name: None,
        ),
        history_callback=lambda event, args: history.append((event, args)),
    )

    runner.run(max_steps=2)

    candidates, locked_index, _flags = next(
        args for event, args in history if event == "candidates_found"
    )
    assert [candidate.advances for candidate in candidates] == [1542]
    assert locked_index == 0
    assert runner.progress.locked_target is not None
    assert runner.progress.locked_target.raw_target_advances == 1542


def test_runner_restarts_when_all_linear_candidates_are_unreachable(tmp_path):
    seed_script = tmp_path / "seed.txt"
    hit_script = tmp_path / "hit.txt"
    seed_script.write_text("SEED\n", encoding="utf-8")
    hit_script.write_text("_闪帧 = 5\n", encoding="utf-8")
    history: list[tuple[str, tuple[object, ...]]] = []

    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            seed_script_path=seed_script,
            hit_script_path=hit_script,
            fixed_delay=195,
            loop_mode="infinite",
            start_phase=AutoRngPhase.CAPTURE_SEED,
        ),
        services=AutoRngServices(
            capture_seed=lambda: AutoRngSeedResult(seed="seed-1", current_advances=0, npc=1),
            search_candidates=lambda _seed: [FakeState(1541)],
            run_script_text=lambda _text, _name: None,
        ),
        history_callback=lambda event, args: history.append((event, args)),
    )

    runner.run(max_steps=2)

    assert runner.progress.phase == AutoRngPhase.RUN_SEED_SCRIPT
    assert runner.progress.locked_target is None
    assert ("cycle_no_candidate", ()) in history


def test_runner_keeps_timeline_candidates_without_linear_modulus_filter(tmp_path):
    seed_script = tmp_path / "seed.txt"
    hit_script = tmp_path / "hit.txt"
    seed_script.write_text("SEED\n", encoding="utf-8")
    hit_script.write_text("_闪帧 = 5\n", encoding="utf-8")
    history: list[tuple[str, tuple[object, ...]]] = []

    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            seed_script_path=seed_script,
            hit_script_path=hit_script,
            fixed_delay=195,
            start_phase=AutoRngPhase.CAPTURE_SEED,
        ),
        services=AutoRngServices(
            capture_seed=lambda: AutoRngSeedResult(
                seed=SeedState32(1, 2, 3, 4),
                current_advances=0,
                npc=1,
                advance_mode="timeline",
            ),
            search_candidates=lambda _seed: [FakeState(1541)],
            run_script_text=lambda _text, _name: None,
        ),
        history_callback=lambda event, args: history.append((event, args)),
    )

    runner.run(max_steps=2)

    candidates, _locked_index, _flags = next(
        args for event, args in history if event == "candidates_found"
    )
    assert [candidate.advances for candidate in candidates] == [1541]


def test_runner_checks_zoom_before_the_second_seed_script(tmp_path):
    seed_script = tmp_path / "BDSP测种.txt"
    seed_script.write_text("A 100\n", encoding="utf-8")
    scripts = []
    zoom_checks = []

    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            seed_script_path=seed_script,
            loop_mode="count",
            loop_count=2,
        ),
        services=AutoRngServices(
            capture_seed=lambda: AutoRngSeedResult(seed="seed", current_advances=0),
            search_candidates=lambda _seed: [],
            run_script_text=lambda _text, name: scripts.append(name),
            recover_zoom_mode=lambda: zoom_checks.append(True) or True,
        ),
    )

    runner.run(max_steps=5)

    assert scripts == ["BDSP测种.txt", "BDSP测种.txt"]
    assert zoom_checks == [True]


def test_runner_does_not_run_second_seed_script_when_stopped_during_zoom_recovery(tmp_path):
    seed_script = tmp_path / "BDSP测种.txt"
    seed_script.write_text("A 100\n", encoding="utf-8")
    scripts = []
    holder = {}

    def stop_during_recovery():
        holder["runner"].stop()
        return True

    runner = AutoRngRunner(
        AutoRngConfig(script_dir=tmp_path, seed_script_path=seed_script, loop_mode="count", loop_count=2),
        services=AutoRngServices(
            capture_seed=lambda: AutoRngSeedResult(seed="seed", current_advances=0),
            search_candidates=lambda _seed: [],
            run_script_text=lambda _text, name: scripts.append(name),
            recover_zoom_mode=stop_during_recovery,
        ),
    )
    holder["runner"] = runner

    runner.run(max_steps=5)

    assert scripts == ["BDSP测种.txt"]
    assert runner.progress.phase == AutoRngPhase.IDLE
    assert runner.progress.log_message == "已请求停止自动流程"


def test_advance_request_above_threshold_recaptures_seed():
    decision = decide_after_advance_script(990_001, reseed_threshold_frames=990_000)

    assert decision.kind == AutoRngDecisionKind.CAPTURE_SEED
    assert decision.phase == AutoRngPhase.CAPTURE_SEED


def test_advance_request_at_threshold_reidentifies():
    decision = decide_after_advance_script(990_000, reseed_threshold_frames=990_000)

    assert decision.kind == AutoRngDecisionKind.REIDENTIFY
    assert decision.phase == AutoRngPhase.REIDENTIFY
    assert decision.message.endswith("执行校正")


def test_runner_preserves_original_advance_request_without_exit_script(tmp_path):
    seed_script = tmp_path / "seed.txt"
    advance_script = tmp_path / "advance.txt"
    hit_script = tmp_path / "hit.txt"
    seed_script.write_text("A 100\n", encoding="utf-8")
    advance_script.write_text(f"{AUTO_ADVANCE_PARAMETER} = 0\n", encoding="utf-8")
    hit_script.write_text(f"{AUTO_HIT_PARAMETER} = 0\n", encoding="utf-8")
    scripts: list[tuple[str, str]] = []
    services = AutoRngServices(
        capture_seed=lambda: AutoRngSeedResult(seed="seed-1", current_advances=0),
        search_candidates=lambda _seed: [FakeState(2_000_100)],
        reidentify=lambda _seed: AutoRngSeedResult(seed="seed-1", current_advances=0),
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
            reseeding_threshold=10_000,
        ),
        services=services,
    )

    runner.run(max_steps=5)

    assert (advance_script.name, f"{AUTO_ADVANCE_PARAMETER} = 2000000\n") in scripts


def test_runner_reserves_threshold_before_large_exit_reseed_advance(tmp_path):
    seed_script = tmp_path / "seed.txt"
    advance_script = tmp_path / "advance.txt"
    hit_script = tmp_path / "hit.txt"
    exit_script = tmp_path / "exit.txt"
    seed_script.write_text("A 100\n", encoding="utf-8")
    advance_script.write_text(f"{AUTO_ADVANCE_PARAMETER} = 0\n", encoding="utf-8")
    hit_script.write_text(f"{AUTO_HIT_PARAMETER} = 0\n", encoding="utf-8")
    exit_script.write_text("B 100\n", encoding="utf-8")
    scripts: list[tuple[str, str]] = []
    services = AutoRngServices(
        capture_seed=lambda: AutoRngSeedResult(seed="seed-1", current_advances=0),
        search_candidates=lambda _seed: [FakeState(2_000_100)],
        reidentify=lambda _seed: AutoRngSeedResult(seed="seed-1", current_advances=0),
        run_script_text=lambda text, name: scripts.append((name, text)),
    )
    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            seed_script_path=seed_script,
            advance_script_path=advance_script,
            hit_script_path=hit_script,
            exit_script_path=exit_script,
            fixed_delay=100,
            max_wait_frames=300,
            reseeding_threshold=10_000,
        ),
        services=services,
    )

    runner.run(max_steps=5)

    assert (advance_script.name, f"{AUTO_ADVANCE_PARAMETER} = 1990000\n") in scripts
    assert runner.progress.phase == AutoRngPhase.CAPTURE_SEED


def test_runner_recaptures_seed_when_advance_exceeds_configured_reseed_threshold(tmp_path):
    advance_script = tmp_path / "advance.txt"
    hit_script = tmp_path / "hit.txt"
    advance_script.write_text(f"{AUTO_ADVANCE_PARAMETER} = 0\n", encoding="utf-8")
    hit_script.write_text(f"{AUTO_HIT_PARAMETER} = 2\n", encoding="utf-8")
    scripts: list[tuple[str, str]] = []

    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            advance_script_path=advance_script,
            hit_script_path=hit_script,
            fixed_delay=97,
            max_wait_frames=360,
            start_phase=AutoRngPhase.CAPTURE_SEED,
        ),
        services=AutoRngServices(
            capture_seed=lambda: AutoRngSeedResult(seed="seed-1", current_advances=0),
            search_candidates=lambda _seed: [FakeState(909_885)],
            run_script_text=lambda text, name: scripts.append((name, text)),
        ),
    )

    runner.run(max_steps=4)

    assert (advance_script.name, f"{AUTO_ADVANCE_PARAMETER} = 909786\n") in scripts
    assert runner.progress.phase == AutoRngPhase.CAPTURE_SEED
    assert runner.progress.log_message == "过帧量 909786 超过重测阈值 900000，重新捕获 seed"


def test_runner_logs_advance_script_start_before_running_service(tmp_path):
    seed_script = tmp_path / "seed.txt"
    advance_script = tmp_path / "advance.txt"
    hit_script = tmp_path / "hit.txt"
    exit_script = tmp_path / "exit.txt"
    seed_script.write_text("A 100\n", encoding="utf-8")
    advance_script.write_text(f"{AUTO_ADVANCE_PARAMETER} = 0\n", encoding="utf-8")
    hit_script.write_text(f"{AUTO_HIT_PARAMETER} = 60\n", encoding="utf-8")
    exit_script.write_text("EXIT\n", encoding="utf-8")
    events: list[str] = []
    scripts: list[tuple[str, str]] = []

    def run_script(text: str, name: str) -> None:
        events.append(f"service:{name}")
        scripts.append((name, text))

    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            seed_script_path=seed_script,
            advance_script_path=advance_script,
            hit_script_path=hit_script,
            exit_script_path=exit_script,
            fixed_delay=100,
            max_wait_frames=450,
            reseeding_threshold=10_000,
            start_phase=AutoRngPhase.CAPTURE_SEED,
        ),
        services=AutoRngServices(
            capture_seed=lambda: AutoRngSeedResult(seed="seed-1", current_advances=0),
            search_candidates=lambda _seed: [FakeState(2_840_819)],
            run_script_text=run_script,
        ),
        progress_callback=lambda progress: events.append(progress.log_message or ""),
    )

    runner.run(max_steps=4)

    start_index = events.index("启动过帧脚本——advance.txt，本次过帧 2830659 帧")
    service_index = events.index("service:advance.txt")
    assert start_index < service_index
    assert (advance_script.name, f"{AUTO_ADVANCE_PARAMETER} = 2830659\n") in scripts


def test_runner_reports_advance_script_start_failure_with_script_name(tmp_path):
    advance_script = tmp_path / "advance.txt"
    hit_script = tmp_path / "hit.txt"
    advance_script.write_text(f"{AUTO_ADVANCE_PARAMETER} = 0\n", encoding="utf-8")
    hit_script.write_text(f"{AUTO_HIT_PARAMETER} = 60\n", encoding="utf-8")
    messages: list[str] = []

    def fail_script(_text: str, _name: str) -> None:
        raise RuntimeError("串口未就绪")

    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            advance_script_path=advance_script,
            hit_script_path=hit_script,
            fixed_delay=100,
            max_wait_frames=450,
            start_phase=AutoRngPhase.CAPTURE_SEED,
        ),
        services=AutoRngServices(
            capture_seed=lambda: AutoRngSeedResult(seed="seed-1", current_advances=0),
            search_candidates=lambda _seed: [FakeState(2_840_819)],
            run_script_text=fail_script,
        ),
        progress_callback=lambda progress: messages.append(progress.log_message or ""),
    )

    with pytest.raises(RuntimeError, match="串口未就绪"):
        runner.run(max_steps=4)

    assert "过帧脚本启动失败——advance.txt: 串口未就绪" in messages


def test_runner_exit_reseed_runs_exit_script_and_zeroes_underground_advance(tmp_path):
    seed_script = tmp_path / "seed.txt"
    advance_script = tmp_path / "advance.txt"
    hit_script = tmp_path / "hit.txt"
    exit_script = tmp_path / "exit.txt"
    seed_script.write_text("A 100\n", encoding="utf-8")
    advance_script.write_text(f"{AUTO_ADVANCE_PARAMETER} = 0\n$地下过帧 = 1\n", encoding="utf-8")
    hit_script.write_text(f"{AUTO_HIT_PARAMETER} = 0\n", encoding="utf-8")
    exit_script.write_text("B 100\n", encoding="utf-8")
    scripts: list[tuple[str, str]] = []
    capture_advances = iter([0, 1_990_000])
    exit_reidentify_calls: list[AutoRngSeedResult] = []

    def capture_seed() -> AutoRngSeedResult:
        return AutoRngSeedResult(seed="seed-1", current_advances=next(capture_advances), npc=0)

    def reidentify_exit(seed: AutoRngSeedResult) -> AutoRngSeedResult:
        exit_reidentify_calls.append(seed)
        return AutoRngSeedResult(seed=seed.seed, current_advances=1_990_000, npc=1)

    services = AutoRngServices(
        capture_seed=capture_seed,
        search_candidates=lambda _seed: [FakeState(2_000_100)],
        reidentify=lambda seed: AutoRngSeedResult(seed=seed.seed, current_advances=2_000_000, npc=1),
        reidentify_exit=reidentify_exit,
        run_script_text=lambda text, name: scripts.append((name, text)),
    )
    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            seed_script_path=seed_script,
            advance_script_path=advance_script,
            hit_script_path=hit_script,
            exit_script_path=exit_script,
            fixed_delay=100,
            max_wait_frames=300,
            reseeding_threshold=10_000,
        ),
        services=services,
    )

    runner.run(max_steps=12)

    assert (exit_script.name, "B 100\n") in scripts
    assert exit_reidentify_calls
    assert runner._seed_result is not None
    assert runner._seed_result.after_exit_reseed is True
    assert (advance_script.name, f"{AUTO_ADVANCE_PARAMETER} = 10000\n$地下过帧 = 0\n") in scripts


def test_runner_enters_exit_reseed_when_reidentified_remaining_is_within_reserve(tmp_path):
    advance_script = tmp_path / "advance.txt"
    hit_script = tmp_path / "hit.txt"
    exit_script = tmp_path / "exit.txt"
    advance_script.write_text(f"{AUTO_ADVANCE_PARAMETER} = 0\n", encoding="utf-8")
    hit_script.write_text(f"{AUTO_HIT_PARAMETER} = 60\n", encoding="utf-8")
    exit_script.write_text("EXIT\n", encoding="utf-8")
    scripts: list[tuple[str, str]] = []
    services = AutoRngServices(
        capture_seed=lambda: AutoRngSeedResult(seed="seed-1", current_advances=11_004),
        search_candidates=lambda _seed: [FakeState(21_788)],
        reidentify=lambda seed: AutoRngSeedResult(seed=seed.seed, current_advances=21_442),
        reidentify_exit=lambda seed: AutoRngSeedResult(seed=seed.seed, current_advances=21_442),
        run_script_text=lambda text, name: scripts.append((name, text)),
    )
    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            advance_script_path=advance_script,
            hit_script_path=hit_script,
            exit_script_path=exit_script,
            start_phase=AutoRngPhase.CAPTURE_SEED,
            fixed_delay=0,
            max_wait_frames=300,
            reseeding_threshold=10_000,
        ),
        services=services,
    )

    runner.run(max_steps=6)

    assert (advance_script.name, f"{AUTO_ADVANCE_PARAMETER} = 724\n") in scripts
    assert runner.progress.phase == AutoRngPhase.EXIT_RESEED
    assert runner.progress.remaining_to_trigger == 286


def test_runner_enters_exit_reseed_when_first_target_is_within_reserve(tmp_path):
    advance_script = tmp_path / "advance.txt"
    hit_script = tmp_path / "hit.txt"
    exit_script = tmp_path / "exit.txt"
    advance_script.write_text(f"{AUTO_ADVANCE_PARAMETER} = 0\n", encoding="utf-8")
    hit_script.write_text(f"{AUTO_HIT_PARAMETER} = 60\n", encoding="utf-8")
    exit_script.write_text("EXIT\n", encoding="utf-8")
    scripts: list[tuple[str, str]] = []
    services = AutoRngServices(
        capture_seed=lambda: AutoRngSeedResult(seed="seed-1", current_advances=0),
        search_candidates=lambda _seed: [FakeState(5_000)],
        reidentify_exit=lambda seed: AutoRngSeedResult(seed=seed.seed, current_advances=0, npc=1),
        run_script_text=lambda text, name: scripts.append((name, text)),
    )
    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            advance_script_path=advance_script,
            hit_script_path=hit_script,
            exit_script_path=exit_script,
            start_phase=AutoRngPhase.CAPTURE_SEED,
            fixed_delay=0,
            max_wait_frames=300,
            reseeding_threshold=10_000,
        ),
        services=services,
    )

    runner.run(max_steps=4)

    assert (exit_script.name, "EXIT\n") in scripts
    assert all(name != advance_script.name for name, _text in scripts)
    assert runner._seed_result is not None
    assert runner._seed_result.after_exit_reseed is True


def test_runner_restarts_seed_script_after_exit_reseed_miss_when_next_target_exceeds_reseed_threshold(tmp_path):
    seed_script = tmp_path / "seed.txt"
    advance_script = tmp_path / "advance.txt"
    hit_script = tmp_path / "hit.txt"
    exit_script = tmp_path / "exit.txt"
    seed_script.write_text("SEED\n", encoding="utf-8")
    advance_script.write_text(f"{AUTO_ADVANCE_PARAMETER} = 0\n", encoding="utf-8")
    hit_script.write_text(f"{AUTO_HIT_PARAMETER} = 60\n", encoding="utf-8")
    exit_script.write_text("EXIT\n", encoding="utf-8")
    scripts: list[tuple[str, str]] = []
    searches = iter([
        [FakeState(21_788)],
        [FakeState(1_022_000)],
    ])

    def search_candidates(_seed: AutoRngSeedResult) -> list[FakeState]:
        try:
            return next(searches)
        except StopIteration:
            return []

    services = AutoRngServices(
        capture_seed=lambda: AutoRngSeedResult(seed="seed-1", current_advances=11_004),
        search_candidates=search_candidates,
        reidentify=lambda seed: AutoRngSeedResult(seed=seed.seed, current_advances=21_442),
        reidentify_exit=lambda seed: AutoRngSeedResult(seed=seed.seed, current_advances=22_000, npc=1),
        run_script_text=lambda text, name: scripts.append((name, text)),
    )
    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            seed_script_path=seed_script,
            advance_script_path=advance_script,
            hit_script_path=hit_script,
            exit_script_path=exit_script,
            start_phase=AutoRngPhase.CAPTURE_SEED,
            fixed_delay=0,
            max_wait_frames=300,
            reseeding_threshold=10_000,
            reseed_threshold_frames=900_000,
        ),
        services=services,
    )

    runner.run(max_steps=11)

    assert (exit_script.name, "EXIT\n") in scripts
    assert (seed_script.name, "SEED\n") in scripts
    assert all(text != f"{AUTO_ADVANCE_PARAMETER} = 999940\n" for _name, text in scripts)


def test_runner_runs_seed_script_when_search_has_no_candidates(tmp_path):
    seed_script = tmp_path / "BDSP测种.txt"
    advance_script = tmp_path / "bdsp过帧.txt"
    hit_script = tmp_path / "谢米.txt"
    seed_script.write_text("A 100\n", encoding="utf-8")
    advance_script.write_text("_目标帧数 = 填写目标帧数\n", encoding="utf-8")
    hit_script.write_text("_闪帧 = 60\n", encoding="utf-8")
    calls: list[str] = []
    scripts: list[tuple[str, str]] = []
    history: list[tuple[str, tuple[object, ...]]] = []
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
        history_callback=lambda event, args: history.append((event, args)),
    )

    runner.run(max_steps=3)

    assert calls == ["capture"]
    assert scripts == [("BDSP测种.txt", "A 100\n")]
    assert runner.progress.phase == AutoRngPhase.COMPLETED
    assert ("cycle_no_candidate", ()) in history
    assert all(event != "cycle_result" for event, _args in history)


def test_runner_stops_infinite_loop_when_same_seed_repeatedly_has_no_candidates(tmp_path):
    seed_script = tmp_path / "BDSP测种.txt"
    seed_script.write_text("A 100\n", encoding="utf-8")
    captures: list[str] = []
    scripts: list[str] = []
    services = AutoRngServices(
        capture_seed=lambda: captures.append("seed") or AutoRngSeedResult(seed="stale-seed", current_advances=0),
        search_candidates=lambda _seed: [],
        run_script_text=lambda _text, name: scripts.append(name),
    )
    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            seed_script_path=seed_script,
            loop_mode="infinite",
        ),
        services=services,
    )

    with pytest.raises(RuntimeError, match="连续 3 次捕获到相同 seed 且无候选"):
        runner.run(max_steps=12)

    assert captures == ["seed", "seed", "seed"]
    assert scripts == ["BDSP测种.txt", "BDSP测种.txt", "BDSP测种.txt"]


def test_runner_allows_no_candidate_retries_with_different_seeds(tmp_path):
    seed_script = tmp_path / "BDSP测种.txt"
    seed_script.write_text("A 100\n", encoding="utf-8")
    seeds = iter(["seed-1", "seed-2", "seed-3"])
    scripts: list[str] = []
    services = AutoRngServices(
        capture_seed=lambda: AutoRngSeedResult(seed=next(seeds), current_advances=0),
        search_candidates=lambda _seed: [],
        run_script_text=lambda _text, name: scripts.append(name),
    )
    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            seed_script_path=seed_script,
            loop_mode="infinite",
        ),
        services=services,
    )

    runner.run(max_steps=9)

    assert scripts == ["BDSP测种.txt", "BDSP测种.txt", "BDSP测种.txt"]
    assert runner.progress.phase == AutoRngPhase.RUN_SEED_SCRIPT


def test_runner_preserves_sync_candidate_source_and_nature(tmp_path):
    seed_script = tmp_path / "seed.txt"
    hit_script = tmp_path / "hit.txt"
    seed_script.write_text("A 100\n", encoding="utf-8")
    hit_script.write_text("_闪帧 = 60\n", encoding="utf-8")
    calls: list[tuple[int, int | None]] = []
    history: list[tuple[str, tuple[object, ...]]] = []

    def search_sync(_seed: AutoRngSeedResult, lead: int, nature_locked: int | None) -> list[object]:
        calls.append((lead, nature_locked))
        if lead == 255:
            return []
        return [FakeState(1000, ec=0x8BAE6A32, pid=0xAC3EC480, nature=0)]

    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            seed_script_path=seed_script,
            hit_script_path=hit_script,
            fixed_delay=100,
            sync_mode=2,
            sync_nature="勤奋",
            start_phase=AutoRngPhase.CAPTURE_SEED,
        ),
        services=AutoRngServices(
            capture_seed=lambda: AutoRngSeedResult(seed="seed-1", current_advances=0, npc=0),
            search_sync=search_sync,
            reidentify=lambda _seed: AutoRngSeedResult(seed="seed-1", current_advances=700, npc=0),
            run_script_text=lambda _text, _name: None,
        ),
        history_callback=lambda event, args: history.append((event, args)),
    )

    runner.run(max_steps=2)

    assert calls == [(0, 0)]
    candidates_event = next(args for event, args in history if event == "candidates_found")
    candidates, locked_index, sync_flags = candidates_event
    assert locked_index == 0
    assert sync_flags == ["sync"]
    assert candidates[0].nature == 0
    assert runner.progress.locked_target is not None
    assert runner.progress.locked_target.sync_source == "sync"
    assert runner.progress.locked_target.sync_nature == 0


def test_runner_leaves_teleport_slot_unchanged_when_sync_is_disabled(tmp_path):
    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            sync_mode=0,
            target_species=481,
        )
    )
    text = "_闪帧 = 60\n_瞬移精灵槽位 = 2\n"

    assert runner._prepare_teleport_slot(text) == text


def test_runner_updates_teleport_slot_after_switching_to_no_sync_table(tmp_path):
    seed_script = tmp_path / "BDSP测种.txt"
    advance_script = tmp_path / "bdsp过帧.txt"
    hit_script = tmp_path / "红圣菇.txt"
    seed_script.write_text("A 100\n", encoding="utf-8")
    advance_script.write_text(
        "_目标帧数 = 0\n$精灵切换开关 = 0\n",
        encoding="utf-8",
    )
    hit_script.write_text(
        "_瞬移精灵槽位 = 1\n",
        encoding="utf-8",
    )
    scripts: list[tuple[str, str]] = []
    clock = [10.0]

    def fake_sleep(seconds: float) -> None:
        clock[0] += seconds

    def search_sync(_seed: AutoRngSeedResult, lead: int, nature: int | None) -> list[FakeState]:
        if lead == 0 and nature == 0:
            return [FakeState(2200, ec=0x1001, pid=0x2001, nature=0)]
        assert lead == 255
        assert nature is None
        return [FakeState(2000, ec=0x1002, pid=0x2002, nature=18)]

    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            seed_script_path=seed_script,
            advance_script_path=advance_script,
            hit_script_path=hit_script,
            fixed_delay=1200,
            max_wait_frames=300,
            min_final_flash_frames=5,
            sync_mode=2,
            sync_nature="勤奋",
            target_species=481,
            has_body_filters=True,
        ),
        services=AutoRngServices(
            capture_seed=lambda: AutoRngSeedResult(seed="seed-1", current_advances=0, npc=0),
            search_sync=search_sync,
            reidentify=lambda _seed: AutoRngSeedResult(seed="seed-1", current_advances=680, npc=0),
            run_script_text=lambda text, name: scripts.append((name, text)),
            monotonic=lambda: clock[0],
            sleep=fake_sleep,
        ),
    )

    runner.run(max_steps=9)

    advance_text = next(text for name, text in scripts if name == advance_script.name)
    hit_text = next(text for name, text in scripts if name == hit_script.name)
    assert "$精灵切换开关 = 1" in advance_text
    assert runner._is_sync_active is False
    assert "_瞬移精灵槽位 = 2" in hit_text
    assert "_闪帧" not in hit_text
    assert hit_script.read_text(encoding="utf-8") == "_瞬移精灵槽位 = 1\n"
    assert runner.progress.phase == AutoRngPhase.LOOP_CHECK


def test_runner_does_not_label_secondary_no_sync_candidate_as_sync(tmp_path):
    seed_script = tmp_path / "seed.txt"
    hit_script = tmp_path / "hit.txt"
    seed_script.write_text("A 100\n", encoding="utf-8")
    hit_script.write_text("_闪帧 = 60\n", encoding="utf-8")
    history: list[tuple[str, tuple[object, ...]]] = []

    def search_sync(_seed: AutoRngSeedResult, lead: int, nature_locked: int | None) -> list[object]:
        if lead == 0:
            return []
        return [FakeState(1000, ec=0x8BAE6A32, pid=0xAC3EC480, nature=18)]

    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            seed_script_path=seed_script,
            hit_script_path=hit_script,
            fixed_delay=100,
            sync_mode=1,
            sync_nature="勤奋",
            start_phase=AutoRngPhase.CAPTURE_SEED,
        ),
        services=AutoRngServices(
            capture_seed=lambda: AutoRngSeedResult(seed="seed-1", current_advances=0, npc=0),
            search_sync=search_sync,
            reidentify=lambda _seed: AutoRngSeedResult(seed="seed-1", current_advances=700, npc=0),
            run_script_text=lambda _text, _name: None,
        ),
        history_callback=lambda event, args: history.append((event, args)),
    )

    runner.run(max_steps=2)

    candidates_event = next(args for event, args in history if event == "candidates_found")
    candidates, _locked_index, sync_flags = candidates_event
    assert sync_flags == ["no_sync"]
    assert candidates[0].nature == 18
    assert runner.progress.locked_target is not None
    assert runner.progress.locked_target.sync_source == "no_sync"
    assert runner.progress.locked_target.sync_nature is None


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


def test_runner_reidentify_failure_restarts_seed_script_in_infinite_loop(tmp_path):
    seed_script = tmp_path / "seed.txt"
    advance_script = tmp_path / "advance.txt"
    hit_script = tmp_path / "hit.txt"
    seed_script.write_text("SEED\n", encoding="utf-8")
    advance_script.write_text(f"{AUTO_ADVANCE_PARAMETER} = 0\n", encoding="utf-8")
    hit_script.write_text(f"{AUTO_HIT_PARAMETER} = 60\n", encoding="utf-8")
    scripts: list[tuple[str, str]] = []
    attempts = 0
    messages: list[str] = []

    def reidentify(_seed: AutoRngSeedResult) -> AutoRngSeedResult:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("Project_Xs reidentify failed")

    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            seed_script_path=seed_script,
            advance_script_path=advance_script,
            hit_script_path=hit_script,
            loop_mode="infinite",
            fixed_delay=100,
            max_wait_frames=300,
        ),
        services=AutoRngServices(
            capture_seed=lambda: AutoRngSeedResult(seed="seed-1", current_advances=0),
            search_candidates=lambda _seed: [FakeState(1_000)],
            reidentify=reidentify,
            run_script_text=lambda text, name: scripts.append((name, text)),
        ),
        progress_callback=lambda progress: messages.append(progress.log_message or ""),
    )

    runner.run(max_steps=7)

    assert scripts == [
        (seed_script.name, "SEED\n"),
        (advance_script.name, f"{AUTO_ADVANCE_PARAMETER} = 840\n"),
        (seed_script.name, "SEED\n"),
    ]
    assert attempts == 2
    assert runner.progress.phase == AutoRngPhase.CAPTURE_SEED
    assert any(message.startswith("校正连续 2 次失败:") for message in messages)


def test_runner_continues_when_second_reidentify_attempt_succeeds(tmp_path):
    seed_script = tmp_path / "seed.txt"
    advance_script = tmp_path / "advance.txt"
    hit_script = tmp_path / "hit.txt"
    seed_script.write_text("SEED\n", encoding="utf-8")
    advance_script.write_text(f"{AUTO_ADVANCE_PARAMETER} = 0\n", encoding="utf-8")
    hit_script.write_text(f"{AUTO_HIT_PARAMETER} = 60\n", encoding="utf-8")
    scripts: list[tuple[str, str]] = []
    attempts = 0

    def reidentify(seed: AutoRngSeedResult) -> AutoRngSeedResult:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("Project_Xs reidentify failed")
        return AutoRngSeedResult(seed=seed.seed, current_advances=600)

    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            seed_script_path=seed_script,
            advance_script_path=advance_script,
            hit_script_path=hit_script,
            loop_mode="infinite",
            fixed_delay=100,
            max_wait_frames=300,
            reseeding_threshold=900_000,
        ),
        services=AutoRngServices(
            capture_seed=lambda: AutoRngSeedResult(seed="seed-1", current_advances=0),
            search_candidates=lambda _seed: [FakeState(1_000)],
            reidentify=reidentify,
            run_script_text=lambda text, name: scripts.append((name, text)),
        ),
    )

    runner.run(max_steps=6)

    assert attempts == 2
    assert scripts == [
        (seed_script.name, "SEED\n"),
        (advance_script.name, f"{AUTO_ADVANCE_PARAMETER} = 840\n"),
    ]
    assert runner.progress.phase == AutoRngPhase.DECIDE_ADVANCE
    assert runner.progress.current_advances == 600


def test_runner_uses_configurable_ordinary_reidentify_attempts(tmp_path):
    attempts = 0
    messages: list[str] = []

    def reidentify(seed: AutoRngSeedResult) -> AutoRngSeedResult:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RuntimeError(f"temporary failure {attempts}")
        return AutoRngSeedResult(seed=seed.seed, current_advances=700)

    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            reidentify_max_attempts=3,
        ),
        services=AutoRngServices(reidentify=reidentify),
        progress_callback=lambda progress: messages.append(progress.log_message or ""),
    )
    runner.progress = AutoRngProgress(phase=AutoRngPhase.REIDENTIFY, loop_index=1)
    runner._seed_result = AutoRngSeedResult(seed="seed-1", current_advances=100)

    runner._reidentify(AutoRngPhase.DECIDE_ADVANCE)

    assert attempts == 3
    assert runner.progress.phase == AutoRngPhase.DECIDE_ADVANCE
    assert runner.progress.current_advances == 700
    assert any("第 1 次失败（1/3）" in message for message in messages)
    assert any("第 2 次失败（2/3）" in message for message in messages)


def test_runner_normalizes_ordinary_reidentify_attempts_to_at_least_one(tmp_path):
    attempts = 0

    def reidentify(_seed: AutoRngSeedResult) -> AutoRngSeedResult:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("failed")

    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            reidentify_max_attempts=0,
        ),
        services=AutoRngServices(reidentify=reidentify),
    )
    runner.progress = AutoRngProgress(phase=AutoRngPhase.REIDENTIFY, loop_index=1)
    runner._seed_result = AutoRngSeedResult(seed="seed-1", current_advances=100)

    runner._reidentify(AutoRngPhase.DECIDE_ADVANCE)

    assert attempts == 1
    assert runner.progress.phase == AutoRngPhase.RUN_SEED_SCRIPT
    assert runner.progress.log_message.startswith("校正连续 1 次失败:")


def test_runner_recaptures_seed_in_same_round_after_ordinary_reidentify_failure(tmp_path):
    old_target = AutoRngTarget(raw_target_advances=1_000, state=FakeState(1_000))
    capture_attempts = 0
    capture_phases: list[AutoRngPhase] = []
    capture_targets: list[object | None] = []
    scripts: list[str] = []
    history: list[tuple[str, tuple[object, ...]]] = []
    runner: AutoRngRunner | None = None

    def capture_seed() -> AutoRngSeedResult:
        nonlocal capture_attempts
        capture_attempts += 1
        assert runner is not None
        capture_phases.append(runner.progress.phase)
        capture_targets.append(runner.progress.locked_target)
        if capture_attempts == 1:
            raise RuntimeError("temporary seed failure")
        return AutoRngSeedResult(
            seed="seed-2",
            seed_text="new seed",
            current_advances=0,
            npc=1,
        )

    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            reidentify_max_attempts=1,
            reidentify_failure_policy="recapture_seed",
            reidentify_seed_max_attempts=3,
        ),
        services=AutoRngServices(
            capture_seed=capture_seed,
            reidentify=lambda _seed: (_ for _ in ()).throw(RuntimeError("reidentify failed")),
            run_script_text=lambda _text, name: scripts.append(name),
        ),
        history_callback=lambda event, args: history.append((event, args)),
    )
    runner.progress = AutoRngProgress(
        phase=AutoRngPhase.REIDENTIFY,
        loop_index=4,
        seed_text="old seed",
        locked_target=old_target,
        raw_target_advances=old_target.raw_target_advances,
        fixed_delay=100,
        trigger_advances=840,
        current_advances=100,
        remaining_to_trigger=740,
        final_flash_frames=60,
        last_script_path=tmp_path / "advance.txt",
    )
    runner._completed_loops = 4
    runner._cycle_started = True
    runner._seed_result = AutoRngSeedResult(seed="seed-1", seed_text="old seed", current_advances=100)
    runner._locked_target = old_target
    runner._missed_target_advance = old_target.raw_target_advances
    runner._last_search_was_missed = True
    runner._requested_advances = 740
    runner._later_candidate_count = 2
    runner._seed_capture_failures = 4

    runner._reidentify(AutoRngPhase.DECIDE_ADVANCE)

    assert capture_attempts == 2
    assert capture_phases == [AutoRngPhase.CAPTURE_SEED, AutoRngPhase.CAPTURE_SEED]
    assert capture_targets == [None, None]
    assert scripts == []
    assert runner._completed_loops == 4
    assert runner._cycle_started is True
    assert runner._seed_capture_failures == 0
    assert runner._seed_result is not None
    assert runner._seed_result.seed == "seed-2"
    assert runner._advance_counter.current_advances == 0
    assert runner._locked_target is None
    assert runner._missed_target_advance is None
    assert runner._requested_advances == 0
    assert runner.progress.phase == AutoRngPhase.SEARCH_TARGET
    assert runner.progress.loop_index == 4
    assert runner.progress.seed_text == "new seed"
    assert runner.progress.locked_target is None
    assert runner.progress.raw_target_advances is None
    assert runner.progress.fixed_delay is None
    assert runner.progress.trigger_advances is None
    assert runner.progress.remaining_to_trigger is None
    assert runner.progress.final_flash_frames is None
    assert runner.progress.last_script_path is None
    assert ("seed_captured", ("new seed", 0, 1, 100_000)) in history


def test_runner_counts_exhausted_seed_recovery_as_one_global_failure(tmp_path):
    reidentify_attempts = 0
    capture_attempts = 0
    scripts: list[str] = []
    history: list[tuple[str, tuple[object, ...]]] = []

    def reidentify(_seed: AutoRngSeedResult) -> AutoRngSeedResult:
        nonlocal reidentify_attempts
        reidentify_attempts += 1
        raise RuntimeError("reidentify failed")

    def capture_seed() -> AutoRngSeedResult:
        nonlocal capture_attempts
        capture_attempts += 1
        raise RuntimeError("seed failed")

    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            reidentify_max_attempts=3,
            reidentify_failure_policy="recapture_seed",
            reidentify_seed_max_attempts=4,
        ),
        services=AutoRngServices(
            capture_seed=capture_seed,
            reidentify=reidentify,
            run_script_text=lambda _text, name: scripts.append(name),
        ),
        history_callback=lambda event, args: history.append((event, args)),
    )
    runner.progress = AutoRngProgress(
        phase=AutoRngPhase.REIDENTIFY,
        loop_index=2,
        seed_text="old seed",
        locked_target=AutoRngTarget(raw_target_advances=1_000),
        raw_target_advances=1_000,
        fixed_delay=100,
        trigger_advances=840,
        current_advances=100,
        remaining_to_trigger=740,
        final_flash_frames=60,
        last_script_path=tmp_path / "advance.txt",
    )
    runner._completed_loops = 2
    runner._cycle_started = True
    runner._seed_result = AutoRngSeedResult(seed="seed-1", current_advances=100)
    runner._locked_target = AutoRngTarget(raw_target_advances=1_000)
    runner._seed_capture_failures = 2

    runner._reidentify(AutoRngPhase.DECIDE_ADVANCE)

    assert reidentify_attempts == 3
    assert capture_attempts == 4
    assert scripts == []
    assert runner._seed_capture_failures == 3
    assert runner._seed_result is None
    assert runner._locked_target is None
    assert runner.progress.phase == AutoRngPhase.RUN_SEED_SCRIPT
    assert runner.progress.seed_text == ""
    assert runner.progress.locked_target is None
    assert runner.progress.raw_target_advances is None
    assert runner.progress.fixed_delay is None
    assert runner.progress.trigger_advances is None
    assert runner.progress.current_advances is None
    assert runner.progress.remaining_to_trigger is None
    assert runner.progress.final_flash_frames is None
    assert runner.progress.last_script_path is None
    assert any(event == "cycle_restart" for event, _args in history)
    assert "补救测 Seed 连续 4 次失败" in runner.progress.log_message
    assert "全局连续 3/5" in runner.progress.log_message


def test_runner_stops_on_fifth_exhausted_seed_recovery_sequence(tmp_path):
    capture_attempts = 0

    def capture_seed() -> AutoRngSeedResult:
        nonlocal capture_attempts
        capture_attempts += 1
        raise RuntimeError("seed failed")

    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            reidentify_max_attempts=1,
            reidentify_failure_policy="recapture_seed",
            reidentify_seed_max_attempts=3,
        ),
        services=AutoRngServices(
            capture_seed=capture_seed,
            reidentify=lambda _seed: (_ for _ in ()).throw(RuntimeError("reidentify failed")),
        ),
    )
    old_target = AutoRngTarget(raw_target_advances=1_000)
    runner.progress = AutoRngProgress(
        phase=AutoRngPhase.REIDENTIFY,
        loop_index=5,
        seed_text="old seed",
        locked_target=old_target,
        raw_target_advances=1_000,
        fixed_delay=100,
        trigger_advances=840,
        current_advances=100,
        remaining_to_trigger=740,
        final_flash_frames=60,
        last_script_path=tmp_path / "advance.txt",
    )
    runner._seed_result = AutoRngSeedResult(seed="seed-1", current_advances=100)
    runner._locked_target = old_target
    runner._requested_advances = 740
    runner._cycle_started = True
    runner._seed_capture_failures = 4

    with pytest.raises(RuntimeError, match="连续 5 次 seed 捕获失败"):
        runner._reidentify(AutoRngPhase.DECIDE_ADVANCE)

    assert capture_attempts == 3
    assert runner._seed_capture_failures == 5
    assert runner._seed_result is None
    assert runner._locked_target is None
    assert runner._requested_advances == 0
    assert runner._cycle_started is False
    assert runner.progress.phase == AutoRngPhase.FAILED
    assert runner.progress.seed_text == ""
    assert runner.progress.locked_target is None
    assert runner.progress.raw_target_advances is None
    assert runner.progress.fixed_delay is None
    assert runner.progress.trigger_advances is None
    assert runner.progress.current_advances is None
    assert runner.progress.remaining_to_trigger is None
    assert runner.progress.final_flash_frames is None
    assert runner.progress.last_script_path is None


def test_runner_stop_during_seed_recovery_suppresses_retries_and_fallback(tmp_path):
    capture_attempts = 0
    scripts: list[str] = []
    runner: AutoRngRunner | None = None

    def capture_seed() -> AutoRngSeedResult:
        nonlocal capture_attempts
        capture_attempts += 1
        assert runner is not None
        runner.stop()
        raise RuntimeError("Blink capture stopped")

    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            reidentify_max_attempts=1,
            reidentify_failure_policy="recapture_seed",
            reidentify_seed_max_attempts=100,
        ),
        services=AutoRngServices(
            capture_seed=capture_seed,
            reidentify=lambda _seed: (_ for _ in ()).throw(RuntimeError("reidentify failed")),
            run_script_text=lambda _text, name: scripts.append(name),
        ),
    )
    runner.progress = AutoRngProgress(phase=AutoRngPhase.REIDENTIFY, loop_index=1)
    runner._seed_result = AutoRngSeedResult(seed="seed-1", current_advances=100)

    runner._reidentify(AutoRngPhase.DECIDE_ADVANCE)

    assert capture_attempts == 1
    assert scripts == []
    assert runner._seed_capture_failures == 0
    assert runner.progress.phase == AutoRngPhase.IDLE
    assert runner.progress.log_message == "已请求停止自动流程"


def test_runner_exit_reidentify_failure_restarts_seed_script(tmp_path):
    seed_script = tmp_path / "seed.txt"
    hit_script = tmp_path / "hit.txt"
    exit_script = tmp_path / "exit.txt"
    seed_script.write_text("SEED\n", encoding="utf-8")
    hit_script.write_text(f"{AUTO_HIT_PARAMETER} = 60\n", encoding="utf-8")
    exit_script.write_text("EXIT\n", encoding="utf-8")
    scripts: list[tuple[str, str]] = []
    attempts = 0
    messages: list[str] = []

    def reidentify_exit(_seed: AutoRngSeedResult) -> AutoRngSeedResult:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("Project_Xs reidentify failed")

    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            seed_script_path=seed_script,
            hit_script_path=hit_script,
            exit_script_path=exit_script,
            start_phase=AutoRngPhase.CAPTURE_SEED,
            loop_mode="infinite",
            fixed_delay=0,
            reseeding_threshold=10_000,
            reidentify_max_attempts=7,
            reidentify_failure_policy="recapture_seed",
            reidentify_seed_max_attempts=9,
        ),
        services=AutoRngServices(
            capture_seed=lambda: AutoRngSeedResult(seed="seed-1", current_advances=0),
            search_candidates=lambda _seed: [FakeState(5_000)],
            reidentify_exit=reidentify_exit,
            run_script_text=lambda text, name: scripts.append((name, text)),
        ),
        progress_callback=lambda progress: messages.append(progress.log_message or ""),
    )

    runner.run(max_steps=5)

    assert scripts == [
        (exit_script.name, "EXIT\n"),
        (seed_script.name, "SEED\n"),
    ]
    assert attempts == 2
    assert runner.progress.phase == AutoRngPhase.CAPTURE_SEED
    assert any(message.startswith("过场校正连续 2 次失败:") for message in messages)


def test_runner_post_exit_reidentify_never_recaptures_seed(tmp_path):
    reidentify_attempts = 0
    capture_attempts = 0

    def reidentify(_seed: AutoRngSeedResult) -> AutoRngSeedResult:
        nonlocal reidentify_attempts
        reidentify_attempts += 1
        raise RuntimeError("noisy reidentify failed")

    def capture_seed() -> AutoRngSeedResult:
        nonlocal capture_attempts
        capture_attempts += 1
        return AutoRngSeedResult(seed="forbidden")

    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            reidentify_max_attempts=7,
            reidentify_failure_policy="recapture_seed",
            reidentify_seed_max_attempts=9,
        ),
        services=AutoRngServices(
            capture_seed=capture_seed,
            reidentify=reidentify,
        ),
    )
    runner.progress = AutoRngProgress(phase=AutoRngPhase.REIDENTIFY, loop_index=1)
    runner._seed_result = AutoRngSeedResult(
        seed="seed-after-exit",
        current_advances=100,
        after_exit_reseed=True,
    )
    runner._exit_reseed_done = True

    runner._reidentify(AutoRngPhase.DECIDE_ADVANCE)

    assert reidentify_attempts == 2
    assert capture_attempts == 0
    assert runner.progress.phase == AutoRngPhase.RUN_SEED_SCRIPT
    assert runner.progress.log_message.startswith("过场后校正连续 2 次失败:")


def test_runner_post_exit_large_advance_enters_next_round_instead_of_capturing_seed(tmp_path):
    advance_script = tmp_path / "advance.txt"
    advance_script.write_text(f"{AUTO_ADVANCE_PARAMETER} = 0\n", encoding="utf-8")
    scripts: list[str] = []
    capture_attempts = 0

    def capture_seed() -> AutoRngSeedResult:
        nonlocal capture_attempts
        capture_attempts += 1
        return AutoRngSeedResult(seed="forbidden")

    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            advance_script_path=advance_script,
            reseed_threshold_frames=100,
        ),
        services=AutoRngServices(
            capture_seed=capture_seed,
            run_script_text=lambda _text, name: scripts.append(name),
        ),
    )
    runner.progress = AutoRngProgress(phase=AutoRngPhase.RUN_ADVANCE_SCRIPT, loop_index=3)
    runner._completed_loops = 3
    runner._seed_result = AutoRngSeedResult(
        seed="seed-after-exit",
        current_advances=500,
        after_exit_reseed=True,
    )
    runner._requested_advances = 101
    runner._exit_reseed_done = True

    runner._run_advance_script()

    assert scripts == [advance_script.name]
    assert capture_attempts == 0
    assert runner._completed_loops == 3
    assert runner._seed_result is None
    assert runner.progress.phase == AutoRngPhase.RUN_SEED_SCRIPT
    assert "不直接重测 Seed" in runner.progress.log_message


def test_runner_post_exit_large_advance_skips_advance_script_before_next_round(tmp_path):
    advance_script = tmp_path / "advance.txt"
    advance_script.write_text(f"{AUTO_ADVANCE_PARAMETER} = 0\n", encoding="utf-8")
    scripts: list[str] = []

    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            advance_script_path=advance_script,
            fixed_delay=0,
            fixed_flash_frames=0,
            max_wait_frames=10,
            reseed_threshold_frames=100,
        ),
        services=AutoRngServices(
            run_script_text=lambda _text, name: scripts.append(name),
        ),
    )
    target = AutoRngTarget(raw_target_advances=500)
    runner.progress = AutoRngProgress(
        phase=AutoRngPhase.DECIDE_ADVANCE,
        loop_index=3,
        locked_target=target,
    )
    runner._completed_loops = 3
    runner._cycle_started = True
    runner._seed_result = AutoRngSeedResult(
        seed="seed-after-exit",
        current_advances=0,
        after_exit_reseed=False,
    )
    runner._locked_target = target
    runner._exit_reseed_done = True

    runner._decide_advance()

    assert scripts == []
    assert runner._seed_result is None
    assert runner._locked_target is None
    assert runner.progress.phase == AutoRngPhase.RUN_SEED_SCRIPT
    assert "不直接重测 Seed" in runner.progress.log_message


def test_runner_full_seed_recapture_discards_previous_target_state(tmp_path):
    old_target = AutoRngTarget(raw_target_advances=1_000)
    runner = AutoRngRunner(
        AutoRngConfig(script_dir=tmp_path),
        services=AutoRngServices(
            capture_seed=lambda: AutoRngSeedResult(
                seed="new-seed",
                seed_text="new seed",
                current_advances=0,
            ),
        ),
    )
    runner.progress = AutoRngProgress(
        phase=AutoRngPhase.CAPTURE_SEED,
        loop_index=2,
        seed_text="old seed",
        locked_target=old_target,
        raw_target_advances=1_000,
        fixed_delay=100,
        trigger_advances=840,
        current_advances=100,
        remaining_to_trigger=740,
        final_flash_frames=60,
        last_script_path=tmp_path / "advance.txt",
    )
    runner._seed_result = AutoRngSeedResult(seed="old-seed", seed_text="old seed", current_advances=100)
    runner._locked_target = old_target
    runner._requested_advances = 740

    runner._capture_seed()

    assert runner._seed_result is not None
    assert runner._seed_result.seed == "new-seed"
    assert runner._locked_target is None
    assert runner._requested_advances == 0
    assert runner.progress.phase == AutoRngPhase.SEARCH_TARGET
    assert runner.progress.seed_text == "new seed"
    assert runner.progress.locked_target is None
    assert runner.progress.raw_target_advances is None
    assert runner.progress.trigger_advances is None
    assert runner.progress.remaining_to_trigger is None
    assert runner.progress.final_flash_frames is None
    assert runner.progress.last_script_path is None


def test_runner_retries_seed_capture_failures_until_fifth_failure(tmp_path):
    seed_script = tmp_path / "seed.txt"
    seed_script.write_text("SEED\n", encoding="utf-8")
    scripts: list[str] = []
    attempts = 0

    def capture_seed() -> AutoRngSeedResult:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("Project_Xs seed capture failed")

    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            seed_script_path=seed_script,
            loop_mode="infinite",
        ),
        services=AutoRngServices(
            capture_seed=capture_seed,
            run_script_text=lambda _text, name: scripts.append(name),
        ),
    )

    try:
        runner.run(max_steps=10)
    except RuntimeError as exc:
        error = str(exc)
    else:
        raise AssertionError("expected fifth seed capture failure to stop the runner")

    assert attempts == 5
    assert scripts == [seed_script.name] * 5
    assert "连续 5 次 seed 捕获失败" in error


def test_runner_treats_seed_capture_stop_as_user_cancel(tmp_path):
    seed_script = tmp_path / "seed.txt"
    seed_script.write_text("SEED\n", encoding="utf-8")
    logs: list[str] = []
    attempts = 0
    runner: AutoRngRunner | None = None

    def capture_seed() -> AutoRngSeedResult:
        nonlocal attempts
        attempts += 1
        assert runner is not None
        runner.stop()
        raise RuntimeError("Blink capture stopped")

    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            seed_script_path=seed_script,
            start_phase=AutoRngPhase.CAPTURE_SEED,
            loop_mode="infinite",
        ),
        services=AutoRngServices(
            capture_seed=capture_seed,
            run_script_text=lambda _text, _name: None,
        ),
        log_callback=logs.append,
    )

    progress = runner.run(max_steps=3)

    assert attempts == 1
    assert progress.phase == AutoRngPhase.IDLE
    assert not any("seed 捕获失败" in log for log in logs)


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


def test_runner_can_start_from_reidentify_with_current_seed(tmp_path):
    calls: list[str] = []
    messages: list[str] = []
    services = AutoRngServices(
        current_seed=lambda: calls.append("current_seed") or AutoRngSeedResult(seed="seed-0", current_advances=0),
        reidentify=lambda seed: calls.append(f"reidentify:{seed.seed}") or AutoRngSeedResult(
            seed=seed.seed,
            current_advances=123,
        ),
        search_candidates=lambda _seed: [],
    )
    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            start_phase=AutoRngPhase.REIDENTIFY,
        ),
        services=services,
        progress_callback=lambda progress: messages.append(progress.log_message or ""),
    )

    runner.run(max_steps=3)

    assert calls == ["current_seed", "reidentify:seed-0"]
    assert runner.progress.phase == AutoRngPhase.COMPLETED
    assert runner.progress.current_advances == 123
    assert "开始自动流程，从校正开始" in messages


def test_runner_final_calibrate_runs_fixed_hit_script(tmp_path):
    seed_script = tmp_path / "BDSP测种.txt"
    advance_script = tmp_path / "bdsp过帧.txt"
    hit_script = tmp_path / "谢米.txt"
    seed_script.write_text("A 100\n", encoding="utf-8")
    advance_script.write_text("_目标帧数 = 填写目标帧数\n", encoding="utf-8")
    hit_script.write_text("_闪帧 = 60\n_瞬移精灵槽位 = 2\n", encoding="utf-8")
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
            sync_mode=2,
            target_species=488,
        ),
        services=services,
    )

    runner.run(max_steps=6)

    assert scripts == [
        ("BDSP测种.txt", "A 100\n"),
        ("谢米.txt", "_闪帧 = 60\n_瞬移精灵槽位 = 1\n"),
    ]
    assert hit_script.read_text(encoding="utf-8") == "_闪帧 = 60\n_瞬移精灵槽位 = 2\n"
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
        ("bdsp过帧.txt", "_目标帧数 = 660\n"),
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

    assert scripts[-1] == ("谢米.txt", "_闪帧 = 5\n")
    assert runner.progress.current_advances == 835
    assert runner.progress.remaining_to_trigger == 6
    assert runner.progress.final_flash_frames == 5


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

    assert monitor_calls == [("_闪帧 = 39\n", "谢米.txt", 2.8)]
    assert scripts == ["BDSP测种.txt", "BDSP测种.txt"]
    assert runner.progress.phase == AutoRngPhase.SEARCH_TARGET
    assert runner.progress.loop_index == 2


def _write_escape_runner_scripts(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    hit_script = tmp_path / "hit.txt"
    escape_script = tmp_path / "escape.txt"
    reverse_script = tmp_path / "reverse.txt"
    exit_script = tmp_path / "exit.txt"
    hit_script.write_text(f"{AUTO_HIT_PARAMETER} = 60\n", encoding="utf-8")
    escape_script.write_text("ESCAPE\n", encoding="utf-8")
    reverse_script.write_text("REVERSE\n", encoding="utf-8")
    exit_script.write_text("EXIT\n", encoding="utf-8")
    return hit_script, escape_script, reverse_script, exit_script


def test_runner_escape_continue_can_escape_twice_and_target_third_candidate_in_same_loop(tmp_path):
    hit_script, escape_script, _reverse_script, _exit_script = _write_escape_runner_scripts(tmp_path)
    first = FakeState(1300, ec=0x1001, pid=0x2001)
    second = FakeState(1400, ec=0x1002, pid=0x2002)
    third = FakeState(1500, ec=0x1003, pid=0x2003)
    events: list[str] = []
    hit_checks: list[str] = []
    logs: list[str] = []
    reidentified_advances = iter((100, 200))

    def search_candidates(seed: AutoRngSeedResult) -> list[FakeState]:
        events.append(f"search:{seed.current_advances}")
        return [first, second, third]

    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            hit_script_path=hit_script,
            escape_script_path=escape_script,
            escape_continue=True,
            target_species=492,
            start_phase=AutoRngPhase.CAPTURE_SEED,
            fixed_delay=1200,
            max_wait_frames=300,
            shiny_threshold_seconds=2.8,
        ),
        services=AutoRngServices(
            capture_seed=lambda: events.append("capture")
            or AutoRngSeedResult(seed="seed-1", current_advances=0, npc=0),
            search_candidates=search_candidates,
            reidentify=lambda _seed: events.append("reidentify")
            or AutoRngSeedResult(seed="seed-1", current_advances=next(reidentified_advances), npc=0),
            run_script_text=lambda _text, name: events.append(f"script:{name}"),
            run_hit_script_with_shiny_check=lambda _text, name, _threshold: hit_checks.append(name)
            or events.append("hit")
            or ShinyCheckResult(is_shiny=False, interval_seconds=2.3),
            monotonic=lambda: 10.0,
        ),
        log_callback=logs.append,
    )

    runner.run(max_steps=7)

    assert events == [
        "capture",
        "search:0",
        "hit",
        f"script:{escape_script.name}",
        "reidentify",
        "search:100",
    ]
    assert hit_checks == [hit_script.name]
    assert runner.progress.phase == AutoRngPhase.DECIDE_ADVANCE
    assert runner.progress.loop_index == 1
    assert runner.progress.locked_target is not None
    assert runner.progress.locked_target.raw_target_advances == second.advances
    assert any("第 1 轮 / 第 1 次" in message for message in logs)

    runner.run(max_steps=5)

    assert hit_checks == [hit_script.name, hit_script.name]
    assert events.count(f"script:{escape_script.name}") == 2
    assert events[-2:] == ["reidentify", "search:200"]
    assert runner.progress.phase == AutoRngPhase.DECIDE_ADVANCE
    assert runner.progress.loop_index == 1
    assert runner.progress.locked_target is not None
    assert runner.progress.locked_target.raw_target_advances == third.advances
    assert any("第 1 轮 / 第 2 次" in message for message in logs)
    assert any("当前搜索仍有 1 个更晚候选" in message for message in logs)


@pytest.mark.parametrize(
    ("escape_continue", "candidates"),
    [
        (
            False,
            [FakeState(1300, ec=0x1101, pid=0x2101), FakeState(1500, ec=0x1102, pid=0x2102)],
        ),
        (True, [FakeState(1300, ec=0x1201, pid=0x2201)]),
    ],
    ids=["feature-disabled", "no-later-candidate"],
)
def test_runner_keeps_old_non_shiny_behavior_without_escape_candidate(
    tmp_path,
    escape_continue,
    candidates,
):
    hit_script, escape_script, _reverse_script, _exit_script = _write_escape_runner_scripts(tmp_path)
    scripts: list[str] = []
    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            hit_script_path=hit_script,
            escape_script_path=escape_script,
            escape_continue=escape_continue,
            start_phase=AutoRngPhase.CAPTURE_SEED,
            fixed_delay=1200,
            max_wait_frames=300,
            shiny_threshold_seconds=2.8,
        ),
        services=AutoRngServices(
            capture_seed=lambda: AutoRngSeedResult(seed="seed-1", current_advances=0, npc=0),
            search_candidates=lambda _seed: candidates,
            run_script_text=lambda _text, name: scripts.append(name),
            run_hit_script_with_shiny_check=lambda _text, _name, _threshold: ShinyCheckResult(
                is_shiny=False,
                interval_seconds=2.3,
            ),
            monotonic=lambda: 10.0,
        ),
    )

    runner.run(max_steps=5)

    assert scripts == []
    assert runner.progress.phase == AutoRngPhase.COMPLETED
    assert "未出闪" in runner.progress.log_message


def test_runner_ordinary_species_treats_shiny_timeout_as_non_shiny(tmp_path):
    hit_script, escape_script, _reverse_script, _exit_script = _write_escape_runner_scripts(tmp_path)
    scripts: list[str] = []
    progress_messages: list[str] = []
    candidates = [
        FakeState(1300, ec=0x1301, pid=0x2301),
        FakeState(1500, ec=0x1302, pid=0x2302),
    ]
    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            hit_script_path=hit_script,
            escape_script_path=escape_script,
            escape_continue=True,
            target_species=492,
            start_phase=AutoRngPhase.CAPTURE_SEED,
            fixed_delay=1200,
            max_wait_frames=300,
            shiny_threshold_seconds=2.8,
        ),
        services=AutoRngServices(
            capture_seed=lambda: AutoRngSeedResult(seed="seed-1", current_advances=0, npc=0),
            search_candidates=lambda _seed: candidates,
            run_script_text=lambda _text, name: scripts.append(name),
            run_hit_script_with_shiny_check=lambda _text, _name, _threshold: ShinyCheckResult(
                is_shiny=False,
                interval_seconds=None,
            ),
            monotonic=lambda: 10.0,
        ),
        progress_callback=lambda progress: progress_messages.append(progress.log_message),
    )

    runner.run(max_steps=5)

    assert scripts == [escape_script.name]
    assert runner.progress.phase == AutoRngPhase.REIDENTIFY
    assert any("OCR 超时，按未出闪继续" in message for message in progress_messages)


@pytest.mark.parametrize("target_species", [481, 488, 387, 390, 393])
def test_runner_special_species_unknown_shiny_result_stops_for_manual_confirmation(
    tmp_path,
    target_species,
):
    hit_script, escape_script, _reverse_script, _exit_script = _write_escape_runner_scripts(tmp_path)
    scripts: list[str] = []
    progress_messages: list[str] = []
    candidates = [
        FakeState(1300, ec=0x1351, pid=0x2351),
        FakeState(1500, ec=0x1352, pid=0x2352),
    ]
    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            hit_script_path=hit_script,
            escape_script_path=escape_script,
            escape_continue=True,
            target_species=target_species,
            start_phase=AutoRngPhase.CAPTURE_SEED,
            fixed_delay=1200,
            max_wait_frames=300,
            shiny_threshold_seconds=2.8,
        ),
        services=AutoRngServices(
            capture_seed=lambda: AutoRngSeedResult(seed="seed-1", current_advances=0, npc=0),
            search_candidates=lambda _seed: candidates,
            run_script_text=lambda _text, name: scripts.append(name),
            run_hit_script_with_shiny_check=lambda _text, _name, _threshold: ShinyCheckResult(
                is_shiny=False,
                interval_seconds=None,
            ),
            monotonic=lambda: 10.0,
        ),
        progress_callback=lambda progress: progress_messages.append(progress.log_message),
    )

    runner.run(max_steps=5)

    assert scripts == []
    assert runner.progress.phase == AutoRngPhase.FAILED
    assert runner.progress.last_script_path == hit_script
    assert any("人工确认" in message for message in progress_messages)


def test_runner_user_stop_during_shiny_monitor_returns_idle(tmp_path):
    hit_script, _escape_script, _reverse_script, _exit_script = _write_escape_runner_scripts(tmp_path)
    candidates = [FakeState(1300, ec=0x1301, pid=0x2301)]
    stopped_scripts = []
    runner_holder = []

    def cancel_monitor(_text, _name, _threshold):
        runner_holder[0].stop()
        raise RuntimeError("monitor cancelled")

    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            hit_script_path=hit_script,
            start_phase=AutoRngPhase.CAPTURE_SEED,
            fixed_delay=1200,
            max_wait_frames=300,
            shiny_threshold_seconds=2.8,
        ),
        services=AutoRngServices(
            capture_seed=lambda: AutoRngSeedResult(seed="seed-1", current_advances=0, npc=0),
            search_candidates=lambda _seed: candidates,
            run_script_text=lambda _text, _name: None,
            run_hit_script_with_shiny_check=cancel_monitor,
            stop_current_script=lambda: stopped_scripts.append(True),
            monotonic=lambda: 10.0,
        ),
    )
    runner_holder.append(runner)

    result = runner.run(max_steps=5)

    assert result.phase == AutoRngPhase.IDLE
    assert stopped_scripts == [True]


def test_runner_escape_continue_takes_priority_over_auto_reverse_with_later_candidate(tmp_path):
    hit_script, escape_script, reverse_script, _exit_script = _write_escape_runner_scripts(tmp_path)
    scripts: list[str] = []
    reverse_calls: list[int] = []
    candidates = [
        FakeState(1300, ec=0x1401, pid=0x2401),
        FakeState(1500, ec=0x1402, pid=0x2402),
    ]
    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            hit_script_path=hit_script,
            escape_script_path=escape_script,
            reverse_script_path=reverse_script,
            escape_continue=True,
            auto_reverse=True,
            start_phase=AutoRngPhase.CAPTURE_SEED,
            fixed_delay=1200,
            max_wait_frames=300,
            shiny_threshold_seconds=2.8,
        ),
        services=AutoRngServices(
            capture_seed=lambda: AutoRngSeedResult(seed="seed-1", current_advances=0, npc=0),
            search_candidates=lambda _seed: candidates,
            reidentify=lambda _seed: AutoRngSeedResult(seed="seed-1", current_advances=150, npc=0),
            run_script_text=lambda _text, name: scripts.append(name),
            run_hit_script_with_shiny_check=lambda _text, _name, _threshold: ShinyCheckResult(
                is_shiny=False,
                interval_seconds=2.3,
            ),
            run_reverse_lookup=lambda _seed, target: reverse_calls.append(target.raw_target_advances),
            monotonic=lambda: 10.0,
        ),
    )

    runner.run(max_steps=5)

    assert scripts == [escape_script.name]
    assert reverse_calls == []
    assert runner.progress.phase == AutoRngPhase.REIDENTIFY
    assert runner.progress.loop_index == 1


def test_runner_escape_continue_falls_back_to_reverse_lookup_without_later_candidate(tmp_path):
    hit_script, escape_script, reverse_script, _exit_script = _write_escape_runner_scripts(tmp_path)
    scripts: list[str] = []
    reverse_calls: list[int] = []
    only_target = FakeState(1300, ec=0x1501, pid=0x2501)
    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            hit_script_path=hit_script,
            escape_script_path=escape_script,
            reverse_script_path=reverse_script,
            escape_continue=True,
            auto_reverse=True,
            start_phase=AutoRngPhase.CAPTURE_SEED,
            fixed_delay=1200,
            max_wait_frames=300,
            shiny_threshold_seconds=2.8,
        ),
        services=AutoRngServices(
            capture_seed=lambda: AutoRngSeedResult(seed="seed-1", current_advances=0, npc=0),
            search_candidates=lambda _seed: [only_target],
            run_script_text=lambda _text, name: scripts.append(name),
            run_hit_script_with_shiny_check=lambda _text, _name, _threshold: ShinyCheckResult(
                is_shiny=False,
                interval_seconds=2.3,
            ),
            run_reverse_lookup=lambda _seed, target: reverse_calls.append(target.raw_target_advances),
            monotonic=lambda: 10.0,
        ),
    )

    runner.run(max_steps=5)

    assert scripts == []
    assert reverse_calls == [only_target.advances]
    assert runner.progress.phase == AutoRngPhase.COMPLETED


def test_runner_escape_clears_old_advance_and_exit_state_before_reidentify(tmp_path):
    hit_script, escape_script, _reverse_script, exit_script = _write_escape_runner_scripts(tmp_path)
    first = AutoRngTarget(raw_target_advances=1200, state=FakeState(1200, ec=0x1601, pid=0x2601))
    second = FakeState(1500, ec=0x1602, pid=0x2602)
    reidentify_inputs: list[AutoRngSeedResult] = []
    scripts: list[str] = []

    def reidentify(seed: AutoRngSeedResult) -> AutoRngSeedResult:
        reidentify_inputs.append(seed)
        return AutoRngSeedResult(seed=seed.seed, current_advances=1000, npc=0)

    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            hit_script_path=hit_script,
            escape_script_path=escape_script,
            exit_script_path=exit_script,
            escape_continue=True,
            fixed_delay=0,
            max_wait_frames=300,
            reseeding_threshold=1000,
            shiny_threshold_seconds=2.8,
        ),
        services=AutoRngServices(
            search_candidates=lambda _seed: [second],
            reidentify=reidentify,
            run_script_text=lambda _text, name: scripts.append(name),
            monotonic=lambda: 10.0,
        ),
    )
    runner.progress = AutoRngProgress(
        phase=AutoRngPhase.RUN_ESCAPE_SCRIPT,
        loop_index=1,
        locked_target=first,
        raw_target_advances=first.raw_target_advances,
        trigger_advances=1111,
        remaining_to_trigger=89,
        final_flash_frames=60,
    )
    runner._completed_loops = 1
    runner._cycle_started = True
    runner._seed_result = AutoRngSeedResult(
        seed="seed-1",
        current_advances=1000,
        npc=0,
        expected_advances_hint=1321,
        after_exit_reseed=True,
    )
    runner._locked_target = first
    runner._requested_advances = 321
    runner._reserved_exit_reseed_pending = True
    runner._exit_reseed_done = True

    runner.run(max_steps=1)

    assert scripts == [escape_script.name]
    assert runner.progress.phase == AutoRngPhase.REIDENTIFY
    assert runner.progress.locked_target is None
    assert runner.progress.raw_target_advances is None
    assert runner.progress.trigger_advances is None
    assert runner.progress.remaining_to_trigger is None
    assert runner.progress.final_flash_frames is None
    assert runner.progress.current_advances == 1000
    assert runner.progress.last_script_path == escape_script
    assert runner._requested_advances == 0
    assert runner._reserved_exit_reseed_pending is False
    assert runner._exit_reseed_done is False

    runner.run(max_steps=3)

    assert len(reidentify_inputs) == 1
    assert reidentify_inputs[0].expected_advances_hint is None
    assert reidentify_inputs[0].after_exit_reseed is False
    assert runner._requested_advances == 0
    assert runner._reserved_exit_reseed_pending is False
    assert runner._exit_reseed_done is False
    assert runner._seed_result is not None
    assert runner._seed_result.after_exit_reseed is False
    assert runner.progress.phase == AutoRngPhase.EXIT_RESEED
    assert runner.progress.locked_target is not None
    assert runner.progress.locked_target.raw_target_advances == second.advances


def test_runner_escape_script_error_stops_before_reidentify(tmp_path):
    hit_script, escape_script, _reverse_script, _exit_script = _write_escape_runner_scripts(tmp_path)
    target = AutoRngTarget(raw_target_advances=1300, state=FakeState(1300))
    reidentify_calls: list[AutoRngSeedResult] = []

    def fail_escape(_text: str, _name: str) -> None:
        raise RuntimeError("escape failed")

    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            hit_script_path=hit_script,
            escape_script_path=escape_script,
            escape_continue=True,
        ),
        services=AutoRngServices(
            reidentify=lambda seed: reidentify_calls.append(seed) or seed,
            run_script_text=fail_escape,
        ),
    )
    runner.progress = AutoRngProgress(phase=AutoRngPhase.RUN_ESCAPE_SCRIPT, loop_index=1, locked_target=target)
    runner._seed_result = AutoRngSeedResult(seed="seed-1", current_advances=100)
    runner._locked_target = target
    runner._completed_loops = 1

    with pytest.raises(RuntimeError, match="escape failed"):
        runner.run(max_steps=1)

    assert reidentify_calls == []
    assert runner.progress.phase == AutoRngPhase.RUN_ESCAPE_SCRIPT


def test_runner_treats_escape_script_error_after_stop_as_cancelled(tmp_path):
    hit_script, escape_script, _reverse_script, _exit_script = _write_escape_runner_scripts(tmp_path)
    target = AutoRngTarget(raw_target_advances=1300, state=FakeState(1300))
    reidentify_calls: list[AutoRngSeedResult] = []
    runner: AutoRngRunner | None = None

    def cancel_escape(_text: str, _name: str) -> None:
        assert runner is not None
        runner.stop()
        raise RuntimeError("script cancelled")

    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            hit_script_path=hit_script,
            escape_script_path=escape_script,
            escape_continue=True,
        ),
        services=AutoRngServices(
            reidentify=lambda seed: reidentify_calls.append(seed) or seed,
            run_script_text=cancel_escape,
        ),
    )
    runner.progress = AutoRngProgress(phase=AutoRngPhase.RUN_ESCAPE_SCRIPT, loop_index=1, locked_target=target)
    runner._seed_result = AutoRngSeedResult(seed="seed-1", current_advances=100)
    runner._locked_target = target
    runner._completed_loops = 1

    progress = runner.run(max_steps=1)

    assert progress.phase == AutoRngPhase.IDLE
    assert progress.log_message == "已请求停止自动流程"
    assert reidentify_calls == []


def test_runner_escape_reidentify_retries_once_then_continues(tmp_path):
    hit_script, escape_script, _reverse_script, _exit_script = _write_escape_runner_scripts(tmp_path)
    target = AutoRngTarget(raw_target_advances=1300, state=FakeState(1300))
    attempts = 0
    logs: list[str] = []

    def reidentify(seed: AutoRngSeedResult) -> AutoRngSeedResult:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary failure")
        return AutoRngSeedResult(seed=seed.seed, current_advances=200)

    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            hit_script_path=hit_script,
            escape_script_path=escape_script,
            escape_continue=True,
        ),
        services=AutoRngServices(
            reidentify=reidentify,
            run_script_text=lambda _text, _name: None,
        ),
        log_callback=logs.append,
    )
    runner.progress = AutoRngProgress(phase=AutoRngPhase.RUN_ESCAPE_SCRIPT, loop_index=1, locked_target=target)
    runner._seed_result = AutoRngSeedResult(seed="seed-1", current_advances=100)
    runner._locked_target = target
    runner._completed_loops = 1

    runner.run(max_steps=2)

    assert attempts == 2
    assert runner.progress.phase == AutoRngPhase.SEARCH_TARGET
    assert runner.progress.current_advances == 200
    assert any("校正 第 1 次失败" in message for message in logs)


def test_runner_stop_during_escape_reidentify_does_not_retry_or_reseed(tmp_path):
    hit_script, escape_script, _reverse_script, _exit_script = _write_escape_runner_scripts(tmp_path)
    seed_script = tmp_path / "seed.txt"
    seed_script.write_text("SEED\n", encoding="utf-8")
    target = AutoRngTarget(raw_target_advances=1300, state=FakeState(1300))
    scripts: list[str] = []
    attempts = 0
    runner: AutoRngRunner | None = None

    def reidentify(_seed: AutoRngSeedResult) -> AutoRngSeedResult:
        nonlocal attempts
        attempts += 1
        assert runner is not None
        runner.stop()
        raise RuntimeError("Blink capture stopped")

    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            seed_script_path=seed_script,
            hit_script_path=hit_script,
            escape_script_path=escape_script,
            escape_continue=True,
        ),
        services=AutoRngServices(
            reidentify=reidentify,
            run_script_text=lambda _text, name: scripts.append(name),
        ),
    )
    runner.progress = AutoRngProgress(phase=AutoRngPhase.RUN_ESCAPE_SCRIPT, loop_index=1, locked_target=target)
    runner._seed_result = AutoRngSeedResult(seed="seed-1", current_advances=100)
    runner._locked_target = target
    runner._completed_loops = 1

    progress = runner.run(max_steps=3)

    assert attempts == 1
    assert scripts == [escape_script.name]
    assert progress.phase == AutoRngPhase.IDLE
    assert progress.log_message == "已请求停止自动流程"


def test_runner_escape_preserves_active_sync_lead_and_clears_pending_switch(tmp_path):
    hit_script, escape_script, _reverse_script, _exit_script = _write_escape_runner_scripts(tmp_path)
    target = AutoRngTarget(raw_target_advances=1300, state=FakeState(1300))
    sync_searches: list[tuple[int, int | None]] = []

    def search_sync(_seed: AutoRngSeedResult, lead: int, nature: int | None) -> list[FakeState]:
        sync_searches.append((lead, nature))
        return [FakeState(1500, ec=0x1801, pid=0x2801)]

    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            hit_script_path=hit_script,
            escape_script_path=escape_script,
            escape_continue=True,
            sync_mode=2,
            sync_nature="勤奋",
            fixed_delay=100,
        ),
        services=AutoRngServices(
            reidentify=lambda seed: AutoRngSeedResult(seed=seed.seed, current_advances=200),
            search_sync=search_sync,
            run_script_text=lambda _text, _name: None,
        ),
    )
    runner.progress = AutoRngProgress(phase=AutoRngPhase.RUN_ESCAPE_SCRIPT, loop_index=1, locked_target=target)
    runner._seed_result = AutoRngSeedResult(seed="seed-1", current_advances=100)
    runner._locked_target = target
    runner._completed_loops = 1
    runner._is_sync_active = True
    runner._sync_initial = True
    runner._need_sync_switch = True

    runner.run(max_steps=3)

    assert sync_searches == [(0, 0)]
    assert runner._is_sync_active is True
    assert runner._sync_initial is True
    assert runner._need_sync_switch is False
    assert runner.progress.locked_target is not None
    assert runner.progress.locked_target.sync_source == "sync"


def test_runner_escape_resets_exit_state_and_runs_exit_script_then_exit_reidentify(tmp_path):
    hit_script, escape_script, _reverse_script, exit_script = _write_escape_runner_scripts(tmp_path)
    previous_target = AutoRngTarget(raw_target_advances=1300, state=FakeState(1300))
    next_target = FakeState(200, ec=0x1901, pid=0x2901)
    events: list[str] = []

    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            hit_script_path=hit_script,
            escape_script_path=escape_script,
            exit_script_path=exit_script,
            escape_continue=True,
            fixed_delay=0,
            max_wait_frames=300,
            reseeding_threshold=100,
        ),
        services=AutoRngServices(
            capture_seed=lambda: events.append("capture_seed")
            or AutoRngSeedResult(seed="new-seed", current_advances=0),
            reidentify=lambda seed: events.append("reidentify")
            or AutoRngSeedResult(seed=seed.seed, current_advances=100),
            reidentify_exit=lambda seed: events.append("reidentify_exit")
            or AutoRngSeedResult(seed=seed.seed, current_advances=120),
            search_candidates=lambda _seed: [next_target],
            run_script_text=lambda _text, name: events.append(f"script:{name}"),
        ),
    )
    runner.progress = AutoRngProgress(
        phase=AutoRngPhase.RUN_ESCAPE_SCRIPT,
        loop_index=1,
        locked_target=previous_target,
    )
    runner._seed_result = AutoRngSeedResult(
        seed="seed-1",
        current_advances=50,
        after_exit_reseed=True,
        advance_mode="timeline",
        timing_seed=SeedState32(1, 2, 3, 4),
    )
    runner._locked_target = previous_target
    runner._completed_loops = 1
    runner._exit_reseed_done = True

    runner.run(max_steps=5)

    assert events == [
        f"script:{escape_script.name}",
        "reidentify",
        f"script:{exit_script.name}",
        "reidentify_exit",
    ]
    assert "capture_seed" not in events
    assert runner.progress.phase == AutoRngPhase.SEARCH_TARGET
    assert runner._seed_result is not None
    assert runner._seed_result.after_exit_reseed is True
    assert runner._exit_reseed_done is True
    assert AutoRngPhase.EXIT_RESEED.value == "运行过场脚本"
    assert "过场校正完成" in runner.progress.log_message


@pytest.mark.parametrize(
    ("loop_mode", "loop_count", "expected_phase"),
    [
        ("single", 1, AutoRngPhase.COMPLETED),
        ("count", 2, AutoRngPhase.RUN_SEED_SCRIPT),
        ("infinite", 1, AutoRngPhase.RUN_SEED_SCRIPT),
    ],
)
def test_runner_escape_reidentify_with_no_reachable_candidate_ends_current_loop(
    tmp_path,
    loop_mode,
    loop_count,
    expected_phase,
):
    hit_script, escape_script, _reverse_script, _exit_script = _write_escape_runner_scripts(tmp_path)
    scripts: list[str] = []
    candidates = [
        FakeState(1300, ec=0x1701, pid=0x2701),
        FakeState(1500, ec=0x1702, pid=0x2702),
    ]
    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            hit_script_path=hit_script,
            escape_script_path=escape_script,
            escape_continue=True,
            start_phase=AutoRngPhase.CAPTURE_SEED,
            fixed_delay=1200,
            max_wait_frames=300,
            loop_mode=loop_mode,
            loop_count=loop_count,
            shiny_threshold_seconds=2.8,
        ),
        services=AutoRngServices(
            capture_seed=lambda: AutoRngSeedResult(seed="seed-1", current_advances=0, npc=0),
            search_candidates=lambda _seed: candidates,
            reidentify=lambda _seed: AutoRngSeedResult(seed="seed-1", current_advances=5000, npc=0),
            run_script_text=lambda _text, name: scripts.append(name),
            run_hit_script_with_shiny_check=lambda _text, _name, _threshold: ShinyCheckResult(
                is_shiny=False,
                interval_seconds=2.3,
            ),
            monotonic=lambda: 10.0,
        ),
    )

    runner.run(max_steps=7)

    assert scripts == [escape_script.name]
    assert runner.progress.phase == expected_phase
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


def test_runner_runs_record_script_after_shiny(tmp_path):
    seed_script = tmp_path / "BDSP测种.txt"
    advance_script = tmp_path / "bdsp过帧.txt"
    hit_script = tmp_path / "谢米.txt"
    record_script = tmp_path / "录屏.txt"
    seed_script.write_text("A 100\n", encoding="utf-8")
    advance_script.write_text("_目标帧数 = 填写目标帧数\n", encoding="utf-8")
    hit_script.write_text("_闪帧 = 60\n", encoding="utf-8")
    record_script.write_text("CAPTURE 7000\n", encoding="utf-8")
    scripts: list[tuple[str, str]] = []
    services = AutoRngServices(
        capture_seed=lambda: AutoRngSeedResult(seed="seed-1", current_advances=0, npc=0),
        search_candidates=lambda _seed: [FakeState(1300)],
        reidentify=lambda _seed: AutoRngSeedResult(seed="seed-1", current_advances=0, npc=0),
        run_script_text=lambda text, name: scripts.append((text, name)),
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
            record_script_path=record_script,
            fixed_delay=1200,
            max_wait_frames=300,
            shiny_threshold_seconds=3.5,
        ),
        services=services,
    )

    runner.run(max_steps=7)

    assert scripts[-1] == ("CAPTURE 7000\n", "录屏.txt")
    assert runner.progress.phase == AutoRngPhase.COMPLETED


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
        current_advances=10343,
        fixed_delay=1452,
        fixed_flash_frames=60,
        max_wait_frames=500,
    )

    assert decision.remaining_to_trigger == 60
    assert decision.kind == AutoRngDecisionKind.FINAL_CALIBRATE
    assert decision.phase == AutoRngPhase.FINAL_CALIBRATE


def test_remaining_less_than_flash_triggers_final_adjust():
    """remaining < fixed_flash_frames 时进入动态闪帧调整。"""
    target = AutoRngTarget(raw_target_advances=11915)

    decision = decide_target_advance(
        target,
        current_advances=10350,
        fixed_delay=1452,
        fixed_flash_frames=60,
        max_wait_frames=500,
    )

    assert decision.remaining_to_trigger == 53  # < 60
    assert decision.kind == AutoRngDecisionKind.FINAL_ADJUST
    assert decision.phase == AutoRngPhase.FINAL_ADJUST


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


def test_missing_flash_parameter_uses_runner_wait_to_target_minus_delay():
    target = AutoRngTarget(raw_target_advances=1000)

    decision = decide_target_advance(
        target,
        current_advances=895,
        fixed_delay=100,
        fixed_flash_frames=None,
        max_wait_frames=300,
    )

    assert decision.trigger_advances == 900
    assert decision.remaining_to_trigger == 5
    assert decision.kind == AutoRngDecisionKind.FINAL_WAIT
    assert decision.phase == AutoRngPhase.FINAL_WAIT


def test_runner_waits_before_running_hit_script_without_flash_parameter(tmp_path):
    seed_script = tmp_path / "BDSP测种.txt"
    hit_script = tmp_path / "hit.txt"
    seed_script.write_text("A 100\n", encoding="utf-8")
    hit_script.write_text("B 100\n", encoding="utf-8")
    scripts: list[tuple[str, str]] = []
    phases: list[AutoRngPhase] = []
    clock = [10.0]
    sleeps: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock[0] += seconds

    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            seed_script_path=seed_script,
            hit_script_path=hit_script,
            fixed_delay=100,
            max_wait_frames=300,
        ),
        services=AutoRngServices(
            capture_seed=lambda: AutoRngSeedResult(seed="seed-1", current_advances=0, npc=0),
            search_candidates=lambda _seed: [FakeState(110)],
            run_script_text=lambda text, name: scripts.append((name, text)),
            monotonic=lambda: clock[0],
            sleep=fake_sleep,
        ),
        progress_callback=lambda progress: phases.append(progress.phase),
    )

    runner.run(max_steps=6)

    assert scripts == [(seed_script.name, "A 100\n"), (hit_script.name, "B 100\n")]
    assert AutoRngPhase.FINAL_WAIT in phases
    assert AutoRngPhase.FINAL_ADJUST not in phases
    assert len(sleeps) == 10
    assert runner.progress.phase == AutoRngPhase.LOOP_CHECK
    assert runner.progress.current_advances == 10
    assert runner.progress.final_flash_frames is None
    assert hit_script.read_text(encoding="utf-8") == "B 100\n"


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
    clock = [10.0]

    def fake_sleep(seconds: float) -> None:
        clock[0] += seconds

    services = AutoRngServices(
        capture_seed=lambda: AutoRngSeedResult(seed="seed-1", current_advances=0),
        # raw=11915: trigger=10403, reidentify后current=10309, remaining=94>60 → FINAL_WAIT
        search_candidates=lambda _seed: [FakeState(11915)],
        reidentify=lambda _seed: calls.append("reidentify") or AutoRngSeedResult(
            seed="seed-1", current_advances=10309, npc=0,
        ),
        run_script_text=lambda text, name: scripts.append((name, text)),
        monotonic=lambda: clock[0],
        sleep=fake_sleep,
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
    assert runner.progress.phase in (AutoRngPhase.LOOP_CHECK, AutoRngPhase.RUN_HIT_SCRIPT, AutoRngPhase.COMPLETED)


def test_project_xs_advance_counter_advances_as_live_state():
    counter = ProjectXsAdvanceCounter(current_advances=100, npc=0, next_tick_at=11.018)

    assert counter.advance_to(11.000) == 0
    assert counter.current_advances == 100

    assert counter.advance_to(11.018) == 1
    assert counter.current_advances == 101

    assert counter.advance_to(13.054) == 2
    assert counter.current_advances == 103


def test_project_xs_advance_counter_runs_target_callback():
    counter = ProjectXsAdvanceCounter(current_advances=100, npc=0, next_tick_at=11.018)
    clock = [10.0]
    frames: list[int] = []
    targets: list[int] = []

    def fake_sleep(seconds: float) -> None:
        clock[0] += seconds

    result = counter.run_until(
        target_advances=105,
        monotonic=lambda: clock[0],
        sleep=fake_sleep,
        on_frame=frames.append,
        on_target=targets.append,
    )

    assert result == 105
    assert frames == [101, 102, 103, 104, 105]
    assert targets == [105]


def test_project_xs_timeline_counter_matches_project_xs_plan_timeline():
    state = SeedState32(0x12345678, 0x9ABCDEF0, 0x11111111, 0x22222222)
    current = 500
    now = 10.0
    counter = ProjectXsTimelineAdvanceCounter()
    counter.reset(
        current_advances=current,
        state=state,
        now=now,
        timeline_npc=0,
        pokemon_npc=1,
    )
    expected_events = plan_timeline(
        state,
        max_events=4,
        timeline_npc=0,
        pokemon_npc=1,
        start_advances=current,
        start_time=now,
    )

    assert counter.advance_to(expected_events[0].scheduled_time - 0.001) == 0
    assert counter.current_advances == current

    for index, event in enumerate(expected_events, start=1):
        assert counter.advance_to(event.scheduled_time) == 1
        assert counter.current_advances == event.advance
        assert counter.current_advances == current + index


def test_project_xs_timeline_counter_applies_delay_fields_like_project_xs():
    state = SeedState32(0x12345678, 0x9ABCDEF0, 0x11111111, 0x22222222)
    counter = ProjectXsTimelineAdvanceCounter()
    counter.reset(
        current_advances=100,
        state=state,
        now=5.0,
        timeline_npc=0,
        pokemon_npc=0,
        white_delay=0.5,
        advance_delay=7,
        advance_delay_2=13,
    )

    assert counter.advance_to(5.499) == 0
    assert counter.current_advances == 100
    assert counter.advance_to(5.5) == 7
    assert counter.current_advances == 107

    first_event_time = 5.5 + 1.017
    assert counter.advance_to(first_event_time) == 1
    assert counter.current_advances == 108

    eleventh_event_time = 5.5 + 1.017 * 11
    assert counter.advance_to(eleventh_event_time) == 23
    assert counter.current_advances == 131


def test_runner_final_wait_uses_live_advance_loop(tmp_path):
    hit_script = tmp_path / "hit.txt"
    hit_script.write_text("_闪帧 = 60\nA 100\n", encoding="utf-8")

    clock = [10.0]
    sleeps: list[float] = []
    progress_events: list[AutoRngProgress] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock[0] += seconds

    services = AutoRngServices(
        run_script_text=lambda _text, _name: None,
        monotonic=lambda: clock[0],
        sleep=fake_sleep,
    )
    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            hit_script_path=hit_script,
            fixed_delay=40,
            fixed_flash_frames=60,
        ),
        services=services,
        progress_callback=progress_events.append,
    )
    runner._seed_result = AutoRngSeedResult(seed="seed-1", current_advances=100, npc=0, measured_at=clock[0])
    runner._locked_target = AutoRngTarget(raw_target_advances=205)
    runner.progress = AutoRngProgress(
        phase=AutoRngPhase.FINAL_WAIT,
        raw_target_advances=205,
        fixed_delay=40,
        trigger_advances=105,
        current_advances=100,
        remaining_to_trigger=5,
        final_flash_frames=60,
    )

    runner._final_wait()

    live_advances = [
        event.current_advances
        for event in progress_events
        if event.phase == AutoRngPhase.FINAL_WAIT and event.current_advances is not None
    ]
    assert live_advances == [100, 101, 102, 103, 104, 105]
    assert len(sleeps) == 5
    assert all(abs(seconds - 1.018) < 0.000001 for seconds in sleeps)
    assert runner.progress.phase == AutoRngPhase.RUN_HIT_SCRIPT
    assert runner.progress.current_advances == 105


def test_runner_final_wait_uses_project_xs_timeline_mode(tmp_path):
    hit_script = tmp_path / "hit.txt"
    hit_script.write_text("_闪帧 = 60\nA 100\n", encoding="utf-8")

    state = SeedState32(0x12345678, 0x9ABCDEF0, 0x11111111, 0x22222222)
    clock = [10.0]
    progress_events: list[AutoRngProgress] = []

    def fake_sleep(seconds: float) -> None:
        clock[0] += seconds

    expected_events = plan_timeline(
        state,
        max_events=3,
        timeline_npc=0,
        pokemon_npc=1,
        start_advances=100,
        start_time=10.0,
    )
    trigger = expected_events[1].advance
    services = AutoRngServices(
        run_script_text=lambda _text, _name: None,
        monotonic=lambda: clock[0],
        sleep=fake_sleep,
    )
    runner = AutoRngRunner(
        AutoRngConfig(
            script_dir=tmp_path,
            hit_script_path=hit_script,
            fixed_delay=40,
            fixed_flash_frames=60,
        ),
        services=services,
        progress_callback=progress_events.append,
    )
    runner._seed_result = AutoRngSeedResult(
        seed="seed-1",
        current_advances=100,
        measured_at=10.0,
        advance_mode="timeline",
        timing_seed=state,
        timeline_npc=0,
        pokemon_npc=1,
    )
    runner._locked_target = AutoRngTarget(raw_target_advances=trigger + 100)
    runner.progress = AutoRngProgress(
        phase=AutoRngPhase.FINAL_WAIT,
        raw_target_advances=trigger + 100,
        fixed_delay=40,
        trigger_advances=trigger,
        current_advances=100,
        remaining_to_trigger=trigger - 100,
        final_flash_frames=60,
    )

    runner._final_wait()

    live_advances = [
        event.current_advances
        for event in progress_events
        if event.phase == AutoRngPhase.FINAL_WAIT and event.current_advances is not None
    ]
    assert live_advances == [100, expected_events[0].advance, expected_events[1].advance]
    assert runner.progress.phase == AutoRngPhase.RUN_HIT_SCRIPT
    assert runner.progress.current_advances == trigger


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
    hit_script.write_text("_闪帧 = 60\n_瞬移精灵槽位 = 1\n", encoding="utf-8")

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
            sync_mode=1,
            target_species=481,
        ),
        services=services,
    )

    runner.run(max_steps=10)

    # 确认撞闪脚本被调用，且 _闪帧 被动态改为 19
    hit_texts = [t for n, t in scripts if "谢米" in n]
    assert hit_texts, f"expected hit script call, got {scripts}"
    assert "_闪帧 = 19" in hit_texts[0], f"expected dynamic flash 19, got {hit_texts[0]}"
    assert "_瞬移精灵槽位 = 2" in hit_texts[0]
    assert hit_script.read_text(encoding="utf-8") == "_闪帧 = 60\n_瞬移精灵槽位 = 1\n"
    assert runner.progress.phase == AutoRngPhase.COMPLETED
