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
)
from auto_bdsp_rng.automation.auto_rng.scripts import prepare_advance_script_text, prepare_hit_script_text

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
) -> AutoRngDecision:
    trigger_advances = target.raw_target_advances - fixed_delay
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
            message="已错过目标触发点",
            **common,
        )
    if remaining_to_trigger <= max_wait_frames:
        return AutoRngDecision(
            kind=AutoRngDecisionKind.FINAL_CALIBRATE,
            phase=AutoRngPhase.FINAL_CALIBRATE,
            message="进入最终实时校准",
            **common,
        )
    return AutoRngDecision(
        kind=AutoRngDecisionKind.RUN_ADVANCE_SCRIPT,
        phase=AutoRngPhase.RUN_ADVANCE_SCRIPT,
        requested_advances=remaining_to_trigger,
        message=f"过帧到触发点前 {remaining_to_trigger} 帧",
        **common,
    )


def finalize_flash_frames(
    target: AutoRngTarget,
    *,
    fixed_delay: int,
    current_advances_at_ref: int,
    ref_time: float,
    now_monotonic: float | None = None,
    npc: int = 0,
    min_final_flash_frames: int = 30,
) -> AutoRngDecision:
    now = time.monotonic() if now_monotonic is None else now_monotonic
    trigger_advances = target.raw_target_advances - fixed_delay
    elapsed_seconds = max(0.0, now - ref_time)
    elapsed_advances = math.floor(elapsed_seconds / 1.018) * (npc + 1)
    live_current_advances = current_advances_at_ref + elapsed_advances
    flash_frames = trigger_advances - live_current_advances
    common = {
        "target": target,
        "raw_target_advances": target.raw_target_advances,
        "fixed_delay": fixed_delay,
        "trigger_advances": trigger_advances,
        "current_advances": live_current_advances,
        "remaining_to_trigger": flash_frames,
        "flash_frames": flash_frames,
    }
    if flash_frames <= 0:
        return AutoRngDecision(
            kind=AutoRngDecisionKind.TARGET_MISSED,
            phase=AutoRngPhase.SEARCH_TARGET,
            message="最终校准后已错过目标，不运行撞闪脚本",
            **common,
        )
    if flash_frames < min_final_flash_frames:
        return AutoRngDecision(
            kind=AutoRngDecisionKind.TARGET_TOO_CLOSE,
            phase=AutoRngPhase.SEARCH_TARGET,
            message="最终剩余帧过近，放弃本目标",
            **common,
        )
    return AutoRngDecision(
        kind=AutoRngDecisionKind.RUN_HIT_SCRIPT,
        phase=AutoRngPhase.RUN_HIT_SCRIPT,
        message=f"最终撞闪剩余 {flash_frames} 帧",
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
        self._seed_result = self.services.capture_seed()
        self._set_progress(
            AutoRngPhase.SEARCH_TARGET,
            "捕获 seed 完成",
            current_advances=self._seed_result.current_advances,
            seed_text=self._seed_result.seed_text,
        )

    def _search_target(self) -> None:
        seed = self._require_seed()
        candidates = self.services.search_candidates(seed)
        decision = decide_search_target(candidates)
        if decision.kind == AutoRngDecisionKind.RUN_SEED_SCRIPT:
            self._set_progress(AutoRngPhase.RUN_SEED_SCRIPT, decision.message)
            return
        self._locked_target = decision.target
        self._set_progress(
            AutoRngPhase.DECIDE_ADVANCE,
            decision.message,
            locked_target=self._locked_target,
            raw_target_advances=decision.raw_target_advances,
            current_advances=seed.current_advances,
        )

    def _run_seed_script(self) -> None:
        path = self.config.seed_script_path
        if path is None:
            raise RuntimeError("测种脚本未配置")
        text = path.read_text(encoding="utf-8")
        self.services.run_script_text(text, path.name)
        self._set_progress(AutoRngPhase.CAPTURE_SEED, f"测种脚本完成: {path.name}", last_script_path=path)

    def _decide_advance(self) -> None:
        target = self._require_target()
        seed = self._require_seed()
        decision = decide_target_advance(
            target,
            current_advances=seed.current_advances,
            fixed_delay=self.config.fixed_delay,
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
        self._seed_result = self.services.reidentify(self._require_seed())
        self._set_progress(
            next_phase,
            "reidentify 完成",
            current_advances=self._seed_result.current_advances,
            seed_text=self._seed_result.seed_text,
        )

    def _final_calibrate(self) -> None:
        self._seed_result = self.services.reidentify(self._require_seed())
        target = self._require_target()
        ref_time = self.services.monotonic()
        decision = finalize_flash_frames(
            target,
            fixed_delay=self.config.fixed_delay,
            current_advances_at_ref=self._seed_result.current_advances,
            ref_time=ref_time,
            now_monotonic=self.services.monotonic(),
            npc=self._seed_result.npc,
            min_final_flash_frames=self.config.min_final_flash_frames,
        )
        self._set_progress_from_decision(decision)

    def _run_hit_script(self) -> None:
        path = self.config.hit_script_path
        if path is None:
            raise RuntimeError("撞闪脚本未配置")
        flash_frames = self.progress.final_flash_frames
        if flash_frames is None:
            raise RuntimeError("最终撞闪帧未计算")
        text = prepare_hit_script_text(path.read_text(encoding="utf-8"), flash_frames)
        self.services.run_script_text(text, path.name)
        self._set_progress(AutoRngPhase.LOOP_CHECK, f"撞闪脚本完成: {path.name}", last_script_path=path)

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
        self._set_progress(
            decision.phase,
            decision.message,
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

    def _require_target(self) -> AutoRngTarget:
        if self._locked_target is None:
            raise RuntimeError("目标尚未锁定")
        return self._locked_target
