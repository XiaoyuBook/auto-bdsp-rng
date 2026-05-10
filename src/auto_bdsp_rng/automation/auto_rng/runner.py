from __future__ import annotations

import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from auto_bdsp_rng.automation.auto_rng.models import (
    AutoRngConfig,
    AutoRngDecision,
    AutoRngDecisionKind,
    AutoRngPhase,
    AutoRngProgress,
    AutoRngSeedResult,
    AutoRngTarget,
    ShinyCheckResult,
)
from auto_bdsp_rng.automation.auto_rng.scripts import (
    AUTO_HIT_PARAMETER,
    prepare_advance_script_text,
    prepare_hit_script_text,
    read_advance_script_offset,
    read_integer_parameter,
)

_UNSET = object()


def decide_search_target(candidates: Sequence[object]) -> AutoRngDecision:
    if not candidates:
        return AutoRngDecision(
            kind=AutoRngDecisionKind.RUN_SEED_SCRIPT,
            phase=AutoRngPhase.RUN_SEED_SCRIPT,
            message="无候选，运行测种脚本后重新捕获 seed",
        )
    target = AutoRngTarget.from_state(min(candidates, key=lambda state: int(getattr(state, "advances"))))
    return AutoRngDecision(
        kind=AutoRngDecisionKind.FINAL_CALIBRATE
        if target.raw_target_advances == 0
        else AutoRngDecisionKind.RUN_ADVANCE_SCRIPT,
        phase=AutoRngPhase.DECIDE_ADVANCE,
        target=target,
        raw_target_advances=target.raw_target_advances,
        message=f"锁定最低帧目标 {target.raw_target_advances}",
    )


def decide_target_advance(
    target: AutoRngTarget,
    *,
    current_advances: int,
    fixed_delay: int,
    max_wait_frames: int,
    fixed_flash_frames: int = 0,
) -> AutoRngDecision:
    """三段式决策：脚本启动帧 = 原始目标帧 - delay - 撞闪_闪帧，已含闪帧扣除。"""
    trigger_advances = target.raw_target_advances - fixed_delay - fixed_flash_frames
    remaining_to_trigger = trigger_advances - current_advances
    common = {
        "target": target,
        "raw_target_advances": target.raw_target_advances,
        "fixed_delay": fixed_delay,
        "trigger_advances": trigger_advances,
        "current_advances": current_advances,
        "remaining_to_trigger": remaining_to_trigger,
    }
    if remaining_to_trigger <= 0:
        return AutoRngDecision(
            kind=AutoRngDecisionKind.TARGET_MISSED,
            phase=AutoRngPhase.SEARCH_TARGET,
            message=f"错过脚本启动点（脚本启动帧 {trigger_advances}，目前帧数 {current_advances}）",
            **common,
        )
    if remaining_to_trigger > max_wait_frames:
        return AutoRngDecision(
            kind=AutoRngDecisionKind.RUN_ADVANCE_SCRIPT,
            phase=AutoRngPhase.RUN_ADVANCE_SCRIPT,
            requested_advances=remaining_to_trigger,
            message=f"还需过 {remaining_to_trigger} 帧（大于最大等待窗口 {max_wait_frames}），继续运行过帧脚本",
            **common,
        )
    # 剩余帧数 ≤ 最大等待窗口，进入最终等待区
    if fixed_flash_frames > 0:
        if remaining_to_trigger > fixed_flash_frames:
            # 脚本启动帧已含闪帧扣除，等待全部剩余帧数后直接运行撞闪脚本
            return AutoRngDecision(
                kind=AutoRngDecisionKind.FINAL_WAIT,
                phase=AutoRngPhase.FINAL_WAIT,
                message=f"还需过 {remaining_to_trigger} 帧（≤ 最大等待窗口 {max_wait_frames}），不再运行过帧脚本，直接等待 {remaining_to_trigger} 帧",
                **common,
            )
        if remaining_to_trigger == fixed_flash_frames:
            return AutoRngDecision(
                kind=AutoRngDecisionKind.FINAL_CALIBRATE,
                phase=AutoRngPhase.FINAL_CALIBRATE,
                message=f"正好到达脚本启动点，剩余帧数等于撞闪_闪帧 {fixed_flash_frames}，直接进入校准",
                **common,
            )
        # remaining < fixed_flash_frames，尝试动态调整 _闪帧
        min_adjustable = 5
        if remaining_to_trigger >= min_adjustable + 1:
            return AutoRngDecision(
                kind=AutoRngDecisionKind.FINAL_ADJUST,
                phase=AutoRngPhase.FINAL_ADJUST,
                message=f"过帧过头（还需过 {remaining_to_trigger} 帧），动态调整撞闪_闪帧为 {remaining_to_trigger - 1}",
                **common,
            )
        return AutoRngDecision(
            kind=AutoRngDecisionKind.TARGET_MISSED,
            phase=AutoRngPhase.SEARCH_TARGET,
            message=f"还需过 {remaining_to_trigger} 帧，不足 {min_adjustable + 1} 帧无法调整闪帧，放弃",
            **common,
        )
    # fixed_flash_frames == 0，无固定闪帧
    return AutoRngDecision(
        kind=AutoRngDecisionKind.FINAL_CALIBRATE,
        phase=AutoRngPhase.FINAL_CALIBRATE,
        message=f"进入最终实时校准（无固定闪帧，还需过 {remaining_to_trigger} 帧）",
        **common,
    )


def finalize_flash_frames(
    target: AutoRngTarget,
    *,
    fixed_delay: int,
    current_advances_at_ref: int,
    ref_time: float,
    fixed_flash_frames: int = 0,
    now_monotonic: float | None = None,
    npc: int = 0,
    min_final_flash_frames: int = 30,
) -> AutoRngDecision:
    now = time.monotonic() if now_monotonic is None else now_monotonic
    trigger_advances = target.raw_target_advances - fixed_delay - fixed_flash_frames
    elapsed_seconds = max(0.0, now - ref_time)
    elapsed_advances = math.floor(elapsed_seconds / 1.018) * (npc + 1)
    live_current_advances = current_advances_at_ref + elapsed_advances
    remaining_to_trigger = trigger_advances - live_current_advances
    flash_frames = fixed_flash_frames if fixed_flash_frames > 0 else remaining_to_trigger
    common = {
        "target": target,
        "raw_target_advances": target.raw_target_advances,
        "fixed_delay": fixed_delay,
        "trigger_advances": trigger_advances,
        "current_advances": live_current_advances,
        "remaining_to_trigger": remaining_to_trigger,
        "flash_frames": flash_frames,
    }
    if remaining_to_trigger < 0:
        return AutoRngDecision(
            kind=AutoRngDecisionKind.TARGET_MISSED,
            phase=AutoRngPhase.SEARCH_TARGET,
            message=f"已过脚本启动点 {abs(remaining_to_trigger)} 帧，不运行撞闪脚本",
            **common,
        )
    if remaining_to_trigger < min_final_flash_frames and fixed_flash_frames > 0:
        return AutoRngDecision(
            kind=AutoRngDecisionKind.TARGET_TOO_CLOSE,
            phase=AutoRngPhase.SEARCH_TARGET,
            message=f"还需过 {remaining_to_trigger} 帧（小于最小允许 {min_final_flash_frames}），放弃",
            **common,
        )
    flash_label = f"撞闪_闪帧={flash_frames}" if fixed_flash_frames > 0 else f"动态闪帧={flash_frames}"
    return AutoRngDecision(
        kind=AutoRngDecisionKind.RUN_HIT_SCRIPT,
        phase=AutoRngPhase.RUN_HIT_SCRIPT,
        message=f"启动撞闪脚本（{flash_label}，还需过 {remaining_to_trigger} 帧）",
        **common,
    )


def decide_after_advance_script(requested_advances: int, *, reseed_threshold_frames: int) -> AutoRngDecision:
    if requested_advances > reseed_threshold_frames:
        return AutoRngDecision(
            kind=AutoRngDecisionKind.CAPTURE_SEED,
            phase=AutoRngPhase.CAPTURE_SEED,
            requested_advances=requested_advances,
            message="过帧量超过阈值，重新捕获 seed",
        )
    return AutoRngDecision(
        kind=AutoRngDecisionKind.REIDENTIFY,
        phase=AutoRngPhase.REIDENTIFY,
        requested_advances=requested_advances,
        message="过帧量未超过阈值，执行 reidentify",
    )


def _missing_service(*_args: object, **_kwargs: object) -> object:
    raise RuntimeError("AutoRngRunner service is not configured")


@dataclass(frozen=True)
class AutoRngServices:
    capture_seed: Callable[[], AutoRngSeedResult] = _missing_service  # type: ignore[assignment]
    reidentify: Callable[[AutoRngSeedResult], AutoRngSeedResult] = _missing_service  # type: ignore[assignment]
    search_candidates: Callable[[AutoRngSeedResult], Sequence[object]] = _missing_service  # type: ignore[assignment]
    run_script_text: Callable[[str, str], object] = _missing_service  # type: ignore[assignment]
    run_hit_script_with_shiny_check: Callable[[str, str, float], ShinyCheckResult] | None = None
    stop_current_script: Callable[[], None] | None = None
    monotonic: Callable[[], float] = time.monotonic


class AutoRngRunner:
    """Small orchestration shell around the pure automatic RNG decisions.

    The first implementation keeps hardware calls injectable so UI and unit tests
    can exercise the state machine without requiring Project_Xs or EasyCon.
    """

    def __init__(
        self,
        config: AutoRngConfig,
        *,
        services: AutoRngServices | None = None,
        progress_callback: Callable[[AutoRngProgress], None] | None = None,
        log_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.config = config
        self.services = services or AutoRngServices()
        self.progress_callback = progress_callback
        self.log_callback = log_callback
        self._stop_requested = False
        self.progress = AutoRngProgress(phase=AutoRngPhase.IDLE)
        self._seed_result: AutoRngSeedResult | None = None
        self._locked_target: AutoRngTarget | None = None
        self._requested_advances = 0
        self._completed_loops = 0

    def stop(self) -> None:
        self._stop_requested = True
        if self.services.stop_current_script is not None:
            self.services.stop_current_script()
        self._emit(AutoRngProgress(phase=AutoRngPhase.IDLE, log_message="已请求停止自动流程"))

    def should_stop(self) -> bool:
        return self._stop_requested

    def decide_target(self, target: AutoRngTarget, current_advances: int) -> AutoRngDecision:
        return decide_target_advance(
            target,
            current_advances=current_advances,
            fixed_delay=self.config.fixed_delay,
            fixed_flash_frames=self._fixed_flash_frames(),
            max_wait_frames=self.config.max_wait_frames,
        )

    def run(self, *, max_steps: int = 100) -> AutoRngProgress:
        if self.progress.phase == AutoRngPhase.IDLE:
            self._set_progress(AutoRngPhase.RUN_SEED_SCRIPT, "开始自动流程，运行测种脚本")
        steps = 0
        while not self._stop_requested and steps < max_steps:
            steps += 1
            phase = self.progress.phase
            if phase == AutoRngPhase.CAPTURE_SEED:
                self._capture_seed()
            elif phase == AutoRngPhase.SEARCH_TARGET:
                self._search_target()
            elif phase == AutoRngPhase.RUN_SEED_SCRIPT:
                self._run_seed_script()
            elif phase == AutoRngPhase.DECIDE_ADVANCE:
                self._decide_advance()
            elif phase == AutoRngPhase.RUN_ADVANCE_SCRIPT:
                self._run_advance_script()
            elif phase == AutoRngPhase.REIDENTIFY:
                self._reidentify(AutoRngPhase.DECIDE_ADVANCE)
            elif phase == AutoRngPhase.FINAL_CALIBRATE:
                self._final_calibrate()
            elif phase == AutoRngPhase.FINAL_WAIT:
                self._final_wait()
            elif phase == AutoRngPhase.FINAL_ADJUST:
                self._final_adjust()
            elif phase == AutoRngPhase.RUN_HIT_SCRIPT:
                self._run_hit_script()
            elif phase == AutoRngPhase.LOOP_CHECK:
                self._loop_check()
            else:
                break
        return self.progress

    def _emit(self, progress: AutoRngProgress) -> None:
        if self.log_callback is not None and progress.log_message:
            self.log_callback(progress.log_message)
        if self.progress_callback is not None:
            self.progress_callback(progress)

    def _capture_seed(self) -> None:
        self._seed_result = self._with_measurement_time(self.services.capture_seed())
        self._set_progress(
            AutoRngPhase.SEARCH_TARGET,
            "seed 捕获完成",
            current_advances=self._seed_result.current_advances,
            seed_text=self._seed_result.seed_text,
        )

    def _search_target(self) -> None:
        seed = self._require_seed()
        candidates = self.services.search_candidates(seed)
        # 过滤已过帧：只保留可达候选
        min_reachable = seed.current_advances + self.config.fixed_delay + self._fixed_flash_frames()
        reachable = [c for c in candidates if c.advances >= min_reachable]
        decision = decide_search_target(reachable if reachable else [])
        if decision.kind == AutoRngDecisionKind.RUN_SEED_SCRIPT:
            self._set_progress(AutoRngPhase.RUN_SEED_SCRIPT, decision.message)
            return
        self._locked_target = decision.target
        flash = self._fixed_flash_frames()
        trigger = decision.raw_target_advances - self.config.fixed_delay - flash
        self._set_progress(
            AutoRngPhase.DECIDE_ADVANCE,
            f"原始目标帧 {decision.raw_target_advances}，delay {self.config.fixed_delay}，"
            f"撞闪_闪帧 {flash}，脚本启动帧 {trigger}",
            locked_target=self._locked_target,
            raw_target_advances=decision.raw_target_advances,
            fixed_delay=self.config.fixed_delay,
            trigger_advances=trigger,
            current_advances=seed.current_advances,
        )

    def _run_seed_script(self) -> None:
        path = self.config.seed_script_path
        if path is None:
            raise RuntimeError("测种脚本未配置")
        text = path.read_text(encoding="utf-8")
        self.services.run_script_text(text, path.name)
        self._set_progress(AutoRngPhase.CAPTURE_SEED, f"测种脚本完成——{path.name}", last_script_path=path)

    def _decide_advance(self) -> None:
        target = self._require_target()
        seed = self._require_seed()
        decision = decide_target_advance(
            target,
            current_advances=seed.current_advances,
            fixed_delay=self.config.fixed_delay,
            fixed_flash_frames=self._fixed_flash_frames(),
            max_wait_frames=self.config.max_wait_frames,
        )
        self._requested_advances = decision.requested_advances or 0
        self._set_progress_from_decision(decision)

    def _run_advance_script(self) -> None:
        path = self.config.advance_script_path
        if path is None:
            raise RuntimeError("过帧脚本未配置")
        text = prepare_advance_script_text(path.read_text(encoding="utf-8"), self._requested_advances)
        self.services.run_script_text(text, path.name)
        decision = decide_after_advance_script(
            self._requested_advances,
            reseed_threshold_frames=self.config.reseed_threshold_frames,
        )
        self._set_progress_from_decision(decision, last_script_path=path)

    def _reidentify(self, next_phase: AutoRngPhase) -> None:
        seed = self._require_seed()
        prev_advances = seed.current_advances
        # 传递预期位置提示，用于约束 reidentify 搜索范围
        hint = seed.current_advances + self._requested_advances if self._requested_advances else None
        seed_with_hint = seed if hint is None else replace(seed, expected_advances_hint=hint)
        self._seed_result = self._with_measurement_time(self.services.reidentify(seed_with_hint))
        new_advances = self._seed_result.current_advances
        actual_advance = new_advances - prev_advances
        self._set_progress(
            next_phase,
            f"重新识别完成——目前帧数 {new_advances} 帧，上次实际过帧 {actual_advance} 帧",
            current_advances=new_advances,
            seed_text=self._seed_result.seed_text,
        )

    def _final_calibrate(self) -> None:
        seed = self._require_seed()
        target = self._require_target()
        decision = finalize_flash_frames(
            target,
            fixed_delay=self.config.fixed_delay,
            fixed_flash_frames=self._fixed_flash_frames(),
            current_advances_at_ref=seed.current_advances,
            ref_time=self._seed_measured_at(seed),
            now_monotonic=self.services.monotonic(),
            npc=seed.npc,
            min_final_flash_frames=self.config.min_final_flash_frames,
        )
        self._set_progress_from_decision(decision)

    def _final_wait(self) -> None:
        """等待剩余帧数后直接启动撞闪脚本（脚本启动帧已含闪帧扣除，不再重复扣）。"""
        seed = self._require_seed()
        remaining = self.progress.remaining_to_trigger
        fixed_flash = self._fixed_flash_frames()
        if remaining is None or remaining <= 0:
            self._set_progress(AutoRngPhase.RUN_HIT_SCRIPT, "等待量 ≤ 0，跳过，直接启动撞闪脚本")
            return
        wait_frames = remaining  # 等待全部剩余帧数，不再扣除闪帧

        path = self.config.advance_script_path
        if path is None:
            raise RuntimeError("过帧脚本未配置")
        # 过帧脚本内部有 _目标帧数 - offset 的偏移量，需要补偿
        offset = read_advance_script_offset(path)
        script_frames = wait_frames + offset
        text = prepare_advance_script_text(path.read_text(encoding="utf-8"), script_frames)
        self.services.run_script_text(
            text,
            f"{path.name}（等待 {wait_frames} 帧，脚本参数 {script_frames}）",
        )
        # 更新位置并重置计时基准，然后直接启动撞闪脚本
        new_current = seed.current_advances + wait_frames
        self._seed_result = replace(seed, current_advances=new_current, measured_at=self.services.monotonic())
        self._set_progress(
            AutoRngPhase.RUN_HIT_SCRIPT,
            f"等待 {wait_frames} 帧完成，启动撞闪脚本（目前帧数 {new_current} 帧，撞闪_闪帧 {fixed_flash}）",
            current_advances=new_current,
            final_flash_frames=fixed_flash,
        )

    def _final_adjust(self) -> None:
        """过帧过头时动态调整 _闪帧：new_flash = remaining - 1，等1帧后直接运行撞闪脚本。"""
        seed = self._require_seed()
        remaining = self.progress.remaining_to_trigger
        min_adjustable = 5

        if remaining is None or remaining < min_adjustable + 1:
            self._locked_target = None
            self._set_progress(AutoRngPhase.SEARCH_TARGET,
                f"还需过 {remaining} 帧，不足 {min_adjustable + 1} 帧，无法动态调整闪帧")
            return

        new_flash = remaining - 1
        new_current = seed.current_advances + 1
        self._seed_result = replace(seed, current_advances=new_current, measured_at=self.services.monotonic())

        # 动态写入撞闪脚本的 _闪帧
        path = self.config.hit_script_path
        if path is None:
            raise RuntimeError("撞闪脚本未配置")
        text = prepare_hit_script_text(path.read_text(encoding="utf-8"), new_flash)

        self._set_progress(
            AutoRngPhase.RUN_HIT_SCRIPT,
            f"动态调整——将撞闪_闪帧改为 {new_flash}（原还需过 {remaining} 帧），等待 1 帧后启动",
            final_flash_frames=new_flash,
            current_advances=new_current,
            remaining_to_trigger=remaining,
        )

        shiny_result = self._run_hit_script_text(text, path.name)
        if shiny_result is not None:
            self._handle_shiny_check_result(shiny_result, path)
            return
        self._set_progress(
            AutoRngPhase.LOOP_CHECK,
            f"撞闪脚本完成——{path.name}（动态闪帧 {new_flash}）",
            last_script_path=path,
            current_advances=new_current,
            remaining_to_trigger=remaining,
            final_flash_frames=new_flash,
        )

    def _run_hit_script(self) -> None:
        path = self.config.hit_script_path
        if path is None:
            raise RuntimeError("撞闪脚本未配置")
        flash_frames = self.progress.final_flash_frames
        if flash_frames is None:
            raise RuntimeError("最终撞闪帧未计算")
        seed = self._require_seed()
        target = self._require_target()
        now = self.services.monotonic()
        # ── 诊断：距 measured_at 已过时间（换算帧） ──
        ref_time = self._seed_measured_at(seed)
        elapsed_since_ref = max(0.0, now - ref_time)
        diag_frames_since_ref = int(elapsed_since_ref / 1.018) * (seed.npc + 1)
        decision = finalize_flash_frames(
            target,
            fixed_delay=self.config.fixed_delay,
            fixed_flash_frames=self._fixed_flash_frames(),
            current_advances_at_ref=seed.current_advances,
            ref_time=ref_time,
            now_monotonic=now,
            npc=seed.npc,
            min_final_flash_frames=self.config.min_final_flash_frames,
        )
        if decision.kind != AutoRngDecisionKind.RUN_HIT_SCRIPT:
            self._set_progress_from_decision(decision, last_script_path=path)
            return
        text = path.read_text(encoding="utf-8")
        t_before_service = self.services.monotonic()
        elapsed_from_ref_to_service = max(0.0, t_before_service - ref_time)
        diag_frames_to_service = int(elapsed_from_ref_to_service / 1.018) * (seed.npc + 1)
        # 记录提交撞闪脚本时的时序诊断
        commit_log = (
            f"启动撞闪脚本——估算帧数 {decision.current_advances + diag_frames_since_ref} 帧"
            f"（基准 {decision.current_advances} + 诊断已过 {diag_frames_since_ref} 帧），"
            f"撞闪_闪帧 {decision.flash_frames}，"
            f"脚本启动帧 {decision.trigger_advances}"
        )
        self._set_progress(AutoRngPhase.RUN_HIT_SCRIPT, commit_log,
            current_advances=decision.current_advances,
            remaining_to_trigger=decision.remaining_to_trigger,
            final_flash_frames=decision.flash_frames,
            trigger_advances=decision.trigger_advances,
        )
        shiny_result = self._run_hit_script_text(text, path.name)
        # ── 诊断：脚本执行后已过时间和帧数 ──
        t_after_service = self.services.monotonic()
        total_elapsed = max(0.0, t_after_service - ref_time)
        total_diag_frames = int(total_elapsed / 1.018) * (seed.npc + 1)
        if shiny_result is not None:
            self._handle_shiny_check_result(shiny_result, path)
            return
        self._set_progress(
            AutoRngPhase.LOOP_CHECK,
            f"撞闪脚本完成——{path.name}"
            f"（提交时距基准 {diag_frames_to_service} 帧，总计诊断耗时 {total_diag_frames} 帧）",
            last_script_path=path,
            current_advances=decision.current_advances,
            remaining_to_trigger=decision.remaining_to_trigger,
            final_flash_frames=decision.flash_frames,
        )

    def _run_hit_script_text(self, text: str, name: str) -> ShinyCheckResult | None:
        threshold = self.config.shiny_threshold_seconds
        if threshold is not None and self.services.run_hit_script_with_shiny_check is not None:
            return self.services.run_hit_script_with_shiny_check(text, name, threshold)
        self.services.run_script_text(text, name)
        return None

    def _handle_shiny_check_result(self, result: ShinyCheckResult, path: object) -> None:
        interval_text = "-" if result.interval_seconds is None else f"{result.interval_seconds:.3f}s"
        if result.is_shiny:
            self._completed_loops += 1
            self._locked_target = None
            self._set_progress(
                AutoRngPhase.COMPLETED,
                f"疑似出闪，间隔 {interval_text}，已停止自动流程",
                loop_index=self._completed_loops,
                last_script_path=path,
            )
            return
        self._completed_loops += 1
        self._locked_target = None
        if self.config.loop_mode == "infinite":
            self._set_progress(
                AutoRngPhase.RUN_SEED_SCRIPT,
                f"未出闪，间隔 {interval_text}，进入下一轮测种",
                loop_index=self._completed_loops,
                last_script_path=path,
            )
            return
        if self.config.loop_mode == "count" and self._completed_loops < self.config.loop_count:
            self._set_progress(
                AutoRngPhase.RUN_SEED_SCRIPT,
                f"未出闪，间隔 {interval_text}，进入下一轮测种",
                loop_index=self._completed_loops,
                last_script_path=path,
            )
            return
        self._set_progress(
            AutoRngPhase.COMPLETED,
            f"未出闪，间隔 {interval_text}，自动流程完成",
            loop_index=self._completed_loops,
            last_script_path=path,
        )

    def _loop_check(self) -> None:
        self._completed_loops += 1
        if self.config.loop_mode == "infinite":
            self._locked_target = None
            self._set_progress(AutoRngPhase.RUN_SEED_SCRIPT, "进入下一轮无限循环，运行测种脚本", loop_index=self._completed_loops)
            return
        if self.config.loop_mode == "count" and self._completed_loops < self.config.loop_count:
            self._locked_target = None
            self._set_progress(AutoRngPhase.RUN_SEED_SCRIPT, "进入下一轮循环，运行测种脚本", loop_index=self._completed_loops)
            return
        self._set_progress(AutoRngPhase.COMPLETED, "自动流程完成", loop_index=self._completed_loops)

    def _set_progress_from_decision(self, decision: AutoRngDecision, *, last_script_path: object | None = None) -> None:
        if decision.kind in (AutoRngDecisionKind.TARGET_MISSED, AutoRngDecisionKind.TARGET_TOO_CLOSE):
            self._locked_target = None
            locked_target: object | None = None
        else:
            locked_target = decision.target or self._locked_target
        # 关键决策日志（中文）
        details = []
        if decision.raw_target_advances is not None:
            details.append(f"原始目标帧={decision.raw_target_advances}")
        if decision.fixed_delay is not None:
            details.append(f"delay={decision.fixed_delay}")
        if decision.trigger_advances is not None:
            details.append(f"脚本启动帧={decision.trigger_advances}")
        if decision.current_advances is not None:
            details.append(f"目前帧数={decision.current_advances}")
        if decision.remaining_to_trigger is not None:
            details.append(f"还需过={decision.remaining_to_trigger}")
        if decision.flash_frames is not None:
            details.append(f"闪帧={decision.flash_frames}")
        if decision.requested_advances is not None:
            details.append(f"本次计划过帧={decision.requested_advances}")
        log_line = decision.message
        if details:
            log_line += f"（{'，'.join(details)}）"
        self._set_progress(
            decision.phase,
            log_line,
            locked_target=locked_target,
            raw_target_advances=decision.raw_target_advances,
            fixed_delay=decision.fixed_delay,
            trigger_advances=decision.trigger_advances,
            current_advances=decision.current_advances,
            remaining_to_trigger=decision.remaining_to_trigger,
            final_flash_frames=decision.flash_frames,
            last_script_path=last_script_path,
        )

    def _set_progress(self, phase: AutoRngPhase, message: str = "", **updates: object) -> None:
        values = {
            "phase": phase,
            "loop_index": updates.get("loop_index", self.progress.loop_index),
            "log_message": message,
            "locked_target": updates["locked_target"] if updates.get("locked_target", _UNSET) is not _UNSET else self.progress.locked_target,
            "raw_target_advances": updates.get("raw_target_advances", self.progress.raw_target_advances),
            "fixed_delay": updates.get("fixed_delay", self.progress.fixed_delay),
            "trigger_advances": updates.get("trigger_advances", self.progress.trigger_advances),
            "current_advances": updates.get("current_advances", self.progress.current_advances),
            "remaining_to_trigger": updates.get("remaining_to_trigger", self.progress.remaining_to_trigger),
            "final_flash_frames": updates.get("final_flash_frames", self.progress.final_flash_frames),
            "last_script_path": updates.get("last_script_path", self.progress.last_script_path),
            "seed_text": updates.get("seed_text", self.progress.seed_text),
        }
        self.progress = replace(self.progress, **values)
        self._emit(self.progress)

    def _require_seed(self) -> AutoRngSeedResult:
        if self._seed_result is None:
            raise RuntimeError("seed 尚未捕获")
        return self._seed_result

    def _with_measurement_time(self, seed_result: AutoRngSeedResult) -> AutoRngSeedResult:
        if seed_result.measured_at is not None:
            return seed_result
        return replace(seed_result, measured_at=self.services.monotonic())

    def _seed_measured_at(self, seed_result: AutoRngSeedResult) -> float:
        if seed_result.measured_at is not None:
            return seed_result.measured_at
        return self.services.monotonic()

    def _require_target(self) -> AutoRngTarget:
        if self._locked_target is None:
            raise RuntimeError("目标尚未锁定")
        return self._locked_target

    def _fixed_flash_frames(self) -> int:
        path = self.config.hit_script_path
        if path is None:
            return self.config.fixed_flash_frames
        return read_integer_parameter(path, AUTO_HIT_PARAMETER)
