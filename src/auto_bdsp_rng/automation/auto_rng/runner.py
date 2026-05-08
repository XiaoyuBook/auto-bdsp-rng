from __future__ import annotations

import math
import time
from collections.abc import Callable, Sequence

from auto_bdsp_rng.automation.auto_rng.models import (
    AutoRngConfig,
    AutoRngDecision,
    AutoRngDecisionKind,
    AutoRngPhase,
    AutoRngProgress,
    AutoRngTarget,
)


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


class AutoRngRunner:
    """Small orchestration shell around the pure automatic RNG decisions.

    The first implementation keeps hardware calls injectable so UI and unit tests
    can exercise the state machine without requiring Project_Xs or EasyCon.
    """

    def __init__(
        self,
        config: AutoRngConfig,
        *,
        progress_callback: Callable[[AutoRngProgress], None] | None = None,
        log_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.config = config
        self.progress_callback = progress_callback
        self.log_callback = log_callback
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True
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

    def _emit(self, progress: AutoRngProgress) -> None:
        if self.log_callback is not None and progress.log_message:
            self.log_callback(progress.log_message)
        if self.progress_callback is not None:
            self.progress_callback(progress)
