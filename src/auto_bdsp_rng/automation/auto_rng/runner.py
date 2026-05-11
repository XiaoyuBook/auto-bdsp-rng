from __future__ import annotations

import math
import re
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
        message=f"候选最低帧 {target.raw_target_advances}",
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
    if remaining_to_trigger > 0 and remaining_to_trigger < min_final_flash_frames and fixed_flash_frames > 0:
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


_NATURE_MAP: dict[str, int] = {
    "勤奋": 0, "怕寂寞": 1, "勇敢": 2, "固执": 3, "顽皮": 4,
    "大胆": 5, "坦率": 6, "悠闲": 7, "淘气": 8, "乐天": 9,
    "胆小": 10, "急躁": 11, "认真": 12, "爽朗": 13, "天真": 14,
    "害羞": 15, "温顺": 16, "冷静": 17, "内敛": 18, "慢吞吞": 19,
    "马虎": 20, "温和": 21, "自大": 22, "慎重": 23, "浮躁": 24,
}


def _nature_index(name: str) -> int | None:
    return _NATURE_MAP.get(name)


@dataclass(frozen=True)
class AutoRngServices:
    capture_seed: Callable[[], AutoRngSeedResult] = _missing_service  # type: ignore[assignment]
    reidentify: Callable[[AutoRngSeedResult], AutoRngSeedResult] = _missing_service  # type: ignore[assignment]
    search_candidates: Callable[[AutoRngSeedResult], Sequence[object]] = _missing_service  # type: ignore[assignment]
    search_sync: Callable[[AutoRngSeedResult, int, int | None], list[object]] | None = None
    run_script_text: Callable[[str, str], object] = _missing_service  # type: ignore[assignment]
    run_hit_script_with_shiny_check: Callable[[str, str, float], ShinyCheckResult] | None = None
    run_reverse_lookup: Callable[[AutoRngSeedResult, AutoRngTarget], None] | None = None
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
        history_callback: Callable[[str, tuple[object, ...]], None] | None = None,
    ) -> None:
        self.config = config
        self.services = services or AutoRngServices()
        self.progress_callback = progress_callback
        self.log_callback = log_callback
        self.history_callback = history_callback
        self._stop_requested = False
        self.progress = AutoRngProgress(phase=AutoRngPhase.IDLE)
        self._seed_result: AutoRngSeedResult | None = None
        self._locked_target: AutoRngTarget | None = None
        self._missed_target_advance: int | None = None
        self._requested_advances = 0
        self._completed_loops = 0
        self._cycle_started = False
        self._all_candidates: list[object] = []  # 本轮所有候选
        self._last_shiny_interval: float | None = None
        self._last_used_delay: int | None = None
        self._is_sync_active: bool = False  # 当前队首是否为同步精灵
        self._sync_initial: bool = False  # 本轮初始同步状态（每轮重置）
        self._need_sync_switch: bool = False  # 本次过帧是否需要切换同步状态

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
            elif phase == AutoRngPhase.REVERSE_LOOKUP:
                self._reverse_lookup()
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

    def _history(self, event: str, *args: object) -> None:
        if self.history_callback is not None:
            self.history_callback(event, args)

    def _capture_seed(self) -> None:
        self._seed_result = self._with_measurement_time(self.services.capture_seed())
        seed = self._seed_result
        self._history("seed_captured", seed.seed_text, seed.current_advances, seed.npc, self.config.max_advances)
        self._set_progress(
            AutoRngPhase.SEARCH_TARGET,
            "seed 捕获完成",
            current_advances=seed.current_advances,
            seed_text=seed.seed_text,
        )

    def _search_target(self) -> None:
        seed = self._require_seed()
        sync_enabled = self.config.sync_mode >= 1
        nature_idx: int | None = None
        if sync_enabled and self.config.sync_nature:
            nature_idx = _nature_index(self.config.sync_nature)
        was_missed = self._missed_target_advance is not None

        # 双重搜索：按当前同步状态先搜，再搜另一状态
        from auto_bdsp_rng.gen8_static.models import Lead
        results_primary: list[object] = []
        results_secondary: list[object] = []

        lead_primary = nature_idx if self._is_sync_active and nature_idx is not None else int(Lead.NONE)
        if sync_enabled and self.services.search_sync is not None:
            results_primary = self.services.search_sync(seed, lead_primary, nature_idx if self._is_sync_active else None)
            # 另一状态
            lead_secondary = nature_idx if not self._is_sync_active and nature_idx is not None else int(Lead.NONE)
            nature_secondary = nature_idx if not self._is_sync_active else None
            results_secondary = self.services.search_sync(seed, lead_secondary, nature_secondary)
        else:
            results_primary = list(self.services.search_candidates(seed))

        # 合并去重（按 advances 排序，PID+EC 相同取低帧）
        seen_keys: set[str] = set()
        merged: list[object] = []
        for state in sorted(results_primary + results_secondary, key=lambda s: getattr(s, "advances", 0)):
            key = f"{getattr(state, 'pid', 0):08X}:{getattr(state, 'ec', 0):08X}"
            if key not in seen_keys:
                seen_keys.add(key)
                merged.append(state)

        # 过滤已过帧
        min_reachable = seed.current_advances + self.config.fixed_delay + self._fixed_flash_frames()
        if self._missed_target_advance is not None:
            min_reachable = max(min_reachable, self._missed_target_advance + 1)
        reachable = [c for c in merged if getattr(c, "advances", 0) >= min_reachable]
        decision = decide_search_target(reachable if reachable else [])
        if decision.kind == AutoRngDecisionKind.RUN_SEED_SCRIPT:
            self._set_progress(AutoRngPhase.RUN_SEED_SCRIPT, decision.message)
            self._history("cycle_result", False, None, None, None)
            self._cycle_started = False
            return
        self._locked_target = decision.target
        self._missed_target_advance = None
        # 判断目标是否需要切换同步状态
        locked_adv = decision.target.raw_target_advances if decision.target else 0
        if sync_enabled and self.services.search_sync is not None:
            in_primary = any(getattr(c, "advances", 0) == locked_adv for c in results_primary)
            if in_primary:
                self._need_sync_switch = False  # 目标在当前同步状态下找到，无需切换
            else:
                self._need_sync_switch = True   # 目标在另一同步状态下找到，需要切换
        else:
            self._need_sync_switch = False
        locked_idx = next((i for i, c in enumerate(reachable) if getattr(c, "advances", 0) == locked_adv), 0)
        if was_missed:
            self._history("candidates_refiltered", reachable, locked_idx)
        else:
            self._history("candidates_found", reachable, locked_idx)
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
        self._missed_target_advance = None
        self._completed_loops += 1
        self._cycle_started = True
        self._sync_initial = self.config.sync_mode >= 2  # 首位同步精灵
        self._is_sync_active = self._sync_initial
        self._history("cycle_start", self._completed_loops)
        text = path.read_text(encoding="utf-8")
        self.services.run_script_text(text, path.name)
        self._set_progress(AutoRngPhase.CAPTURE_SEED, f"测种脚本完成——{path.name}",
                          loop_index=self._completed_loops,
                          last_script_path=path)

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
        text = path.read_text(encoding="utf-8")
        # 同步切换：目标在另一同步状态下找到，需要翻转队首
        if self._need_sync_switch and self.config.sync_mode >= 1:
            text = re.sub(r"\$精灵切换开关\s*=\s*\d+", "$精灵切换开关 = 1", text)
        text = prepare_advance_script_text(text, self._requested_advances)
        self.services.run_script_text(text, path.name)
        # 执行后翻转内部同步状态
        if self._need_sync_switch:
            self._is_sync_active = not self._is_sync_active
            self._need_sync_switch = False
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
        """定时触发：算好还需要多少秒，睡到点直接启动撞闪脚本。"""
        seed = self._require_seed()
        remaining = self.progress.remaining_to_trigger
        fixed_flash = self._fixed_flash_frames()
        if remaining is None or remaining <= 0:
            self._set_progress(AutoRngPhase.RUN_HIT_SCRIPT, "等待量 ≤ 0，跳过，直接启动撞闪脚本")
            return

        wait_seconds = remaining * 1.018 / max(1, seed.npc + 1)
        trigger = (self.progress.raw_target_advances or 0) - self.config.fixed_delay - fixed_flash
        self._set_progress(
            AutoRngPhase.FINAL_WAIT,
            f"设置定时触发——还需过 {remaining} 帧（约 {wait_seconds:.0f} 秒），"
            f"到脚本启动帧 {trigger} 时自动运行撞闪脚本",
            current_advances=seed.current_advances,
            remaining_to_trigger=remaining,
        )
        time.sleep(wait_seconds)
        new_current = seed.current_advances + remaining
        self._seed_result = replace(seed, current_advances=new_current, measured_at=self.services.monotonic())
        msg = f"定时触发——目前帧数 ≈{new_current} 帧，启动撞闪脚本（撞闪_闪帧 {fixed_flash}）"
        if self.config.debug_output:
            msg = f"[{time.strftime('%H:%M:%S')}] {msg}"
        self._set_progress(AutoRngPhase.RUN_HIT_SCRIPT, msg,
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
            f"（基准 {decision.current_advances} + 已过 {diag_frames_since_ref} 帧），"
            f"撞闪_闪帧 {decision.flash_frames}"
        )
        if self.config.debug_output:
            commit_log = f"[{time.strftime('%H:%M:%S')}] {commit_log}"
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
        msg = f"撞闪脚本完成——{path.name}"
        if self.config.debug_output:
            msg = f"[{time.strftime('%H:%M:%S')}] {msg}"
        self._set_progress(
            AutoRngPhase.LOOP_CHECK,
            msg,
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
        trigger = self.progress.trigger_advances
        used_delay = self.progress.remaining_to_trigger
        if result.is_shiny:
            self._locked_target = None
            self._history("cycle_result", True, result.interval_seconds, trigger, used_delay)
            self._cycle_started = False
            self._set_progress(
                AutoRngPhase.COMPLETED,
                f"疑似出闪，间隔 {interval_text}，已停止自动流程",
                loop_index=self._completed_loops,
                last_script_path=path,
            )
            return
        self._locked_target = None
        # 自动反查：未出闪时先反查个体再进入下一轮
        if self.config.auto_reverse and self.config.reverse_script_path is not None:
            self._last_shiny_interval = result.interval_seconds
            self._last_used_delay = used_delay
            self._set_progress(
                AutoRngPhase.REVERSE_LOOKUP,
                f"未出闪，间隔 {interval_text}，启动自动反查",
                loop_index=self._completed_loops,
                last_script_path=path,
            )
            return
        self._history("cycle_result", False, result.interval_seconds, trigger, used_delay)
        self._cycle_started = False
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

    def _reverse_lookup(self) -> None:
        """运行反查脚本 → OCR → 搜索 → 输出候选个体。"""
        seed = self._require_seed()
        target = self._require_target()
        service = self.services.run_reverse_lookup
        interval = self._last_shiny_interval
        used_delay = self._last_used_delay
        if service is None:
            self._history("cycle_result", False, interval, None, used_delay)
            self._cycle_started = False
            self._set_progress(AutoRngPhase.LOOP_CHECK, "无反查服务，跳过反查")
            return
        if self.config.reverse_script_path is None:
            self._history("cycle_result", False, interval, None, used_delay)
            self._cycle_started = False
            self._set_progress(AutoRngPhase.LOOP_CHECK, "未配置反查脚本，跳过反查")
            return
        try:
            service(seed, target)
        except Exception as exc:
            self._history("cycle_result", False, interval, None, used_delay)
            self._cycle_started = False
            self._set_progress(AutoRngPhase.LOOP_CHECK, f"反查失败: {exc}")
            return
        self._history("cycle_result", False, interval, None, used_delay)
        self._cycle_started = False
        self._loop_check()

    def _loop_check(self) -> None:
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
            # 记录已错过的目标帧，下次搜索跳过
            if decision.raw_target_advances is not None:
                self._missed_target_advance = decision.raw_target_advances
            self._locked_target = None
            locked_target: object | None = None
            self._history("target_missed", decision.raw_target_advances, decision.current_advances)
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
