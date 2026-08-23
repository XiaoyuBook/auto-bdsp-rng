from __future__ import annotations

import re
import time
import heapq
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
from auto_bdsp_rng.rng_core.generators import BDSPXorshift
from auto_bdsp_rng.rng_core.seed import SeedPair64, SeedState32

_UNSET = object()
_UNDERGROUND_ADVANCE_RE = re.compile(r"(\$地下过帧\s*=\s*)-?\d+")
_REPEATED_NO_CANDIDATE_SEED_LIMIT = 3


def _zero_underground_advance(text: str) -> str:
    return _UNDERGROUND_ADVANCE_RE.sub(r"\g<1>0", text)


def _seed_identity(seed_result: AutoRngSeedResult) -> str:
    if seed_result.seed_text:
        return seed_result.seed_text
    seed = seed_result.seed
    words = getattr(seed, "words", None)
    if words is not None:
        try:
            return " ".join(f"{int(word):08X}" for word in words)
        except (TypeError, ValueError):
            pass
    return repr(seed)


@dataclass
class ProjectXsAdvanceCounter:
    current_advances: int = 0
    npc: int = 0
    next_tick_at: float = 0.0
    frame_seconds: float = 1.018

    @property
    def step(self) -> int:
        return max(1, self.npc + 1)

    def reset(self, *, current_advances: int, npc: int, now: float) -> None:
        self.current_advances = int(current_advances)
        self.npc = int(npc)
        self.next_tick_at = float(now) + self.frame_seconds

    def set_current(self, current_advances: int, *, now: float) -> None:
        self.current_advances = int(current_advances)
        self.next_tick_at = float(now) + self.frame_seconds

    def advance_one_frame(self) -> int:
        self.current_advances += self.step
        self.next_tick_at += self.frame_seconds
        return self.current_advances

    def advance_to(self, now: float) -> int:
        advanced = 0
        while now + 1e-9 >= self.next_tick_at:
            self.advance_one_frame()
            advanced += 1
        return advanced

    def run_until(
        self,
        target_advances: int,
        *,
        monotonic: Callable[[], float],
        sleep: Callable[[float], None],
        should_stop: Callable[[], bool] | None = None,
        on_frame: Callable[[int], None] | None = None,
        on_target: Callable[[int], None] | None = None,
    ) -> int:
        should_stop = (lambda: False) if should_stop is None else should_stop
        while self.current_advances < target_advances and not should_stop():
            sleep_seconds = self.next_tick_at - monotonic()
            if sleep_seconds > 0:
                sleep(sleep_seconds)
            advanced = self.advance_to(monotonic())
            if advanced <= 0:
                continue
            if on_frame is not None:
                on_frame(self.current_advances)
        if self.current_advances >= target_advances and not should_stop() and on_target is not None:
            on_target(self.current_advances)
        return self.current_advances


@dataclass
class ProjectXsTimelineAdvanceCounter:
    current_advances: int = 0
    timeline_npc: int = 0
    pokemon_npc: int = 0
    white_delay: float = 0.0
    advance_delay: int = 0
    advance_delay_2: int = 0
    _timeline_start_at: float = 0.0
    _started: bool = False
    _delay2_countdown: int = 10
    _rng: BDSPXorshift | None = None
    _queue: list[tuple[float, int, int]] | None = None
    _sequence: int = 0

    def reset(
        self,
        *,
        current_advances: int,
        state: SeedState32,
        now: float,
        timeline_npc: int = 0,
        pokemon_npc: int = 0,
        white_delay: float = 0.0,
        advance_delay: int = 0,
        advance_delay_2: int = 0,
    ) -> None:
        if timeline_npc < 0 or pokemon_npc < 0:
            raise ValueError("timeline NPC counts must be non-negative")
        self.current_advances = int(current_advances)
        self.timeline_npc = int(timeline_npc)
        self.pokemon_npc = int(pokemon_npc)
        self.white_delay = max(0.0, float(white_delay))
        self.advance_delay = max(0, int(advance_delay))
        self.advance_delay_2 = max(0, int(advance_delay_2))
        self._timeline_start_at = float(now) + self.white_delay
        self._started = False
        self._delay2_countdown = 10
        self._rng = BDSPXorshift(state)
        self._queue = []
        self._sequence = 0

    def _rangefloat(self, minimum: float, maximum: float) -> float:
        if self._rng is None:
            raise RuntimeError("timeline counter has not been reset")
        temp = (self._rng.next() & 0x7FFFFF) / 8388607.0
        return temp * minimum + (1 - temp) * maximum

    def _push(self, scheduled_time: float, event_type: int) -> None:
        if self._queue is None:
            raise RuntimeError("timeline counter has not been reset")
        self._sequence += 1
        heapq.heappush(self._queue, (float(scheduled_time), int(event_type), self._sequence))

    def _start(self) -> int:
        if self._started:
            return 0
        if self._rng is None:
            raise RuntimeError("timeline counter has not been reset")
        self._started = True
        self._rng.next()
        advanced = 0
        if self.advance_delay:
            self._rng.advance(self.advance_delay)
            self.current_advances += self.advance_delay
            advanced += self.advance_delay
        for _ in range(self.timeline_npc + 1):
            self._push(self._timeline_start_at + 1.017, 0)
        for _ in range(self.pokemon_npc):
            self._push(self._timeline_start_at + self._rangefloat(3, 12) + 0.285, 1)
        return advanced

    def _next_event_time(self) -> float:
        if not self._started:
            return self._timeline_start_at
        if not self._queue:
            return float("inf")
        return self._queue[0][0]

    def advance_to(self, now: float) -> int:
        if self._rng is None or self._queue is None:
            raise RuntimeError("timeline counter has not been reset")
        advanced = 0
        now = float(now)
        if not self._started:
            if now + 1e-9 < self._timeline_start_at:
                return 0
            advanced += self._start()
        while self._queue and now + 1e-9 >= self._queue[0][0]:
            scheduled_time, event_type, _sequence = heapq.heappop(self._queue)
            self.current_advances += 1
            advanced += 1
            if self.advance_delay_2:
                if self._delay2_countdown > 0:
                    self._delay2_countdown -= 1
                elif self._delay2_countdown != -1:
                    self._delay2_countdown -= 1
                    self._rng.advance(self.advance_delay_2)
                    self.current_advances += self.advance_delay_2
                    advanced += self.advance_delay_2
            if event_type == 0:
                self._rng.next()
                self._push(scheduled_time + 1.017, 0)
            else:
                self._push(scheduled_time + self._rangefloat(3, 12) + 0.285, 1)
        return advanced

    def run_until(
        self,
        target_advances: int,
        *,
        monotonic: Callable[[], float],
        sleep: Callable[[float], None],
        should_stop: Callable[[], bool] | None = None,
        on_frame: Callable[[int], None] | None = None,
        on_target: Callable[[int], None] | None = None,
    ) -> int:
        should_stop = (lambda: False) if should_stop is None else should_stop
        while self.current_advances < target_advances and not should_stop():
            sleep_seconds = self._next_event_time() - monotonic()
            if sleep_seconds > 0:
                sleep(sleep_seconds)
            advanced = self.advance_to(monotonic())
            if advanced <= 0:
                continue
            if on_frame is not None:
                on_frame(self.current_advances)
        if self.current_advances >= target_advances and not should_stop() and on_target is not None:
            on_target(self.current_advances)
        return self.current_advances


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
    counter = ProjectXsAdvanceCounter()
    counter.reset(current_advances=current_advances_at_ref, npc=npc, now=ref_time)
    counter.advance_to(now)
    live_current_advances = counter.current_advances
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
            message=f"过帧量 {requested_advances} 超过重测阈值 {reseed_threshold_frames}，重新捕获 seed",
        )
    return AutoRngDecision(
        kind=AutoRngDecisionKind.REIDENTIFY,
        phase=AutoRngPhase.REIDENTIFY,
        requested_advances=requested_advances,
        message=f"过帧量 {requested_advances} 未超过重测阈值 {reseed_threshold_frames}，执行校正",
    )


def _missing_service(*_args: object, **_kwargs: object) -> object:
    raise RuntimeError("AutoRngRunner service is not configured")


_NATURE_MAP: dict[str, int] = {
    "勤奋": 0, "怕寂寞": 1, "勇敢": 2, "固执": 3, "顽皮": 4,
    "大胆": 5, "坦率": 6, "悠闲": 7, "淘气": 8, "乐天": 9,
    "胆小": 10, "急躁": 11, "认真": 12, "爽朗": 13, "天真": 14,
    "内敛": 15, "慢吞吞": 16, "冷静": 17, "害羞": 18, "马虎": 19,
    "温和": 20, "温顺": 21, "自大": 22, "慎重": 23, "浮躁": 24,
}


def _nature_index(name: str) -> int | None:
    return _NATURE_MAP.get(name)


@dataclass(frozen=True)
class AutoRngServices:
    current_seed: Callable[[], AutoRngSeedResult] | None = None
    capture_seed: Callable[[], AutoRngSeedResult] = _missing_service  # type: ignore[assignment]
    reidentify: Callable[[AutoRngSeedResult], AutoRngSeedResult] = _missing_service  # type: ignore[assignment]
    reidentify_exit: Callable[[AutoRngSeedResult], AutoRngSeedResult] | None = None
    search_candidates: Callable[[AutoRngSeedResult], Sequence[object]] = _missing_service  # type: ignore[assignment]
    search_sync: Callable[[AutoRngSeedResult, int, int | None], list[object]] | None = None
    run_script_text: Callable[[str, str], object] = _missing_service  # type: ignore[assignment]
    run_hit_script_with_shiny_check: Callable[[str, str, float], ShinyCheckResult] | None = None
    run_reverse_lookup: Callable[[AutoRngSeedResult, AutoRngTarget], None] | None = None
    recover_zoom_mode: Callable[[], bool] | None = None
    stop_current_script: Callable[[], None] | None = None
    monotonic: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep


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
        self._last_search_was_missed: bool = False
        self._requested_advances = 0
        self._seed_capture_failures = 0
        self._completed_loops = 0
        self._cycle_started = False
        self._all_candidates: list[object] = []  # 本轮所有候选
        self._attempt_index = 0
        self._later_candidate_count = 0
        self._last_shiny_interval: float | None = None
        self._last_used_delay: int | None = None
        self._is_sync_active: bool = False  # 当前队首是否为同步精灵
        self._sync_initial: bool = False  # 本轮初始同步状态（每轮重置）
        self._need_sync_switch: bool = False  # 本次过帧是否需要切换同步状态
        self._reserved_exit_reseed_pending: bool = False
        self._exit_reseed_done: bool = False
        self._last_no_candidate_seed_key: str | None = None
        self._repeated_no_candidate_seed_count: int = 0
        self._advance_counter: ProjectXsAdvanceCounter | ProjectXsTimelineAdvanceCounter = ProjectXsAdvanceCounter()

    def stop(self) -> None:
        self._stop_requested = True
        if self.services.stop_current_script is not None:
            self.services.stop_current_script()
        self._set_progress(AutoRngPhase.IDLE, "已请求停止自动流程")

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

    def run(self, *, max_steps: int = 10000) -> AutoRngProgress:
        if self.progress.phase == AutoRngPhase.IDLE:
            if self.config.start_phase == AutoRngPhase.CAPTURE_SEED:
                self._completed_loops += 1
                self._cycle_started = True
                self._sync_initial = self.config.sync_mode >= 2
                self._is_sync_active = self._sync_initial
                self._reserved_exit_reseed_pending = False
                self._exit_reseed_done = False
                self._attempt_index = 0
                self._later_candidate_count = 0
                self._history("cycle_start", self._completed_loops)
                self._set_progress(
                    AutoRngPhase.CAPTURE_SEED,
                    "开始自动流程，从捕获 seed 开始",
                    loop_index=self._completed_loops,
                )
            elif self.config.start_phase == AutoRngPhase.REIDENTIFY:
                if self.services.current_seed is None:
                    raise RuntimeError("从校正开始需要当前 Seed")
                self._completed_loops += 1
                self._cycle_started = True
                self._sync_initial = self.config.sync_mode >= 2
                self._is_sync_active = self._sync_initial
                self._reserved_exit_reseed_pending = False
                self._exit_reseed_done = False
                self._attempt_index = 0
                self._later_candidate_count = 0
                self._seed_result = self._with_measurement_time(self.services.current_seed())
                self._history("cycle_start", self._completed_loops)
                self._set_progress(
                    AutoRngPhase.REIDENTIFY,
                    "开始自动流程，从校正开始",
                    loop_index=self._completed_loops,
                    seed_text=self._seed_result.seed_text,
                    current_advances=self._seed_result.current_advances,
                )
            else:
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
                next_phase = AutoRngPhase.DECIDE_ADVANCE if self._locked_target is not None else AutoRngPhase.SEARCH_TARGET
                self._reidentify(next_phase)
            elif phase == AutoRngPhase.EXIT_RESEED:
                self._exit_reseed()
            elif phase == AutoRngPhase.FINAL_CALIBRATE:
                self._final_calibrate()
            elif phase == AutoRngPhase.FINAL_WAIT:
                self._final_wait()
            elif phase == AutoRngPhase.FINAL_ADJUST:
                self._final_adjust()
            elif phase == AutoRngPhase.RUN_HIT_SCRIPT:
                self._run_hit_script()
            elif phase == AutoRngPhase.RUN_ESCAPE_SCRIPT:
                self._run_escape_script()
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
        try:
            self._seed_result = self._with_measurement_time(self.services.capture_seed())
        except Exception as exc:
            if self._stop_requested:
                return
            self._seed_capture_failures += 1
            if self._seed_capture_failures >= 5:
                raise RuntimeError(f"连续 5 次 seed 捕获失败，自动流程停止: {exc}") from exc
            self._restart_from_seed_script(
                f"seed 捕获失败（连续 {self._seed_capture_failures}/5）: {exc}，进入下一轮测种"
            )
            return
        self._seed_capture_failures = 0
        seed = self._seed_result
        self._reset_advance_counter(seed)
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
        was_exit_reseed_overrun = (
            seed.after_exit_reseed
            and self._locked_target is not None
            and seed.current_advances >= self._locked_target.raw_target_advances
        )

        # 双重搜索：仅在有体型筛选时同时查询同步/非同步两张表
        # 无体型筛选时根据同步模式只查一张表
        from auto_bdsp_rng.gen8_static.models import Lead
        results_primary: list[object] = []
        results_secondary: list[object] = []

        lead_primary = nature_idx if self._is_sync_active and nature_idx is not None else int(Lead.NONE)
        if sync_enabled and self.services.search_sync is not None:
            results_primary = self.services.search_sync(seed, lead_primary, nature_idx if self._is_sync_active else None)
            lead_secondary = int(Lead.NONE)
            nature_secondary = None
            if self.config.has_body_filters:
                # 有体型筛选时查询另一同步状态的表
                lead_secondary = nature_idx if not self._is_sync_active and nature_idx is not None else int(Lead.NONE)
                nature_secondary = nature_idx if not self._is_sync_active else None
                results_secondary = self.services.search_sync(seed, lead_secondary, nature_secondary)
        else:
            results_primary = list(self.services.search_candidates(seed))

        def source_for_lead(lead: int) -> str:
            return "sync" if int(Lead.SYNCHRONIZE_START) <= int(lead) <= int(Lead.SYNCHRONIZE_END) else "no_sync"

        primary_source = source_for_lead(lead_primary)
        secondary_source = source_for_lead(lead_secondary) if sync_enabled and self.services.search_sync is not None else "no_sync"

        # 合并去重（按 advances 排序，同一同步来源内 PID+EC 相同取低帧）
        seen_keys: set[str] = set()
        merged: list[object] = []
        sync_flags: list[str] = []  # 记录每个候选的同步来源
        sourced_results = [(state, primary_source) for state in results_primary]
        sourced_results.extend((state, secondary_source) for state in results_secondary)
        for state, source in sorted(sourced_results, key=lambda item: getattr(item[0], "advances", 0)):
            key = f"{source}:{getattr(state, 'pid', 0):08X}:{getattr(state, 'ec', 0):08X}"
            if key not in seen_keys:
                seen_keys.add(key)
                merged.append(state)
                sync_flags.append(source)

        # 过滤已过帧——同时过滤 sync_flags
        min_reachable = seed.current_advances + self.config.fixed_delay + self._fixed_flash_frames()
        if self._missed_target_advance is not None:
            min_reachable = max(min_reachable, self._missed_target_advance + 1)
        reachable = []
        reachable_flags = []
        for i, c in enumerate(merged):
            if getattr(c, "advances", 0) >= min_reachable:
                reachable.append(c)
                reachable_flags.append(sync_flags[i])
        decision = decide_search_target(reachable if reachable else [])
        if decision.kind == AutoRngDecisionKind.RUN_SEED_SCRIPT:
            self._record_no_candidate_seed(seed)
            self._last_search_was_missed = was_missed
            self._history("cycle_result", False, None, None, None)
            self._cycle_started = False
            exhausted_message = decision.message
            if self._attempt_index > 0:
                exhausted_message = (
                    f"第 {self._completed_loops} 轮共尝试 {self._attempt_index} 个目标，"
                    "校正后搜索范围内已无可达候选"
                )
            if self.config.loop_mode == "infinite":
                self._set_progress(AutoRngPhase.RUN_SEED_SCRIPT, exhausted_message)
            elif self.config.loop_mode == "count" and self._completed_loops < self.config.loop_count:
                self._set_progress(AutoRngPhase.RUN_SEED_SCRIPT, exhausted_message)
            else:
                completed_message = "无候选，自动流程已完成"
                if self._attempt_index > 0:
                    completed_message = f"{exhausted_message}，自动流程已完成"
                self._set_progress(AutoRngPhase.COMPLETED, completed_message, loop_index=self._completed_loops)
            return
        self._clear_no_candidate_seed_guard()
        self._locked_target = decision.target
        self._missed_target_advance = None
        self._last_search_was_missed = was_missed or was_exit_reseed_overrun
        # 判断目标是否需要切换同步状态
        locked_adv = decision.target.raw_target_advances if decision.target else 0
        locked_idx = next((i for i, c in enumerate(reachable) if getattr(c, "advances", 0) == locked_adv), 0)
        self._later_candidate_count = sum(
            1 for candidate in reachable if int(getattr(candidate, "advances", 0)) > locked_adv
        )
        if sync_enabled and self.services.search_sync is not None:
            selected_source = reachable_flags[locked_idx] if locked_idx < len(reachable_flags) else primary_source
            current_source = "sync" if self._is_sync_active and nature_idx is not None else "no_sync"
            self._need_sync_switch = selected_source != current_source
        else:
            selected_source = "no_sync"
            self._need_sync_switch = False
        if self._locked_target is not None:
            self._locked_target = replace(
                self._locked_target,
                sync_source=selected_source,
                sync_nature=nature_idx if selected_source == "sync" else None,
            )
        if was_missed:
            self._history("candidates_refiltered", reachable, locked_idx, reachable_flags, self.config.fixed_delay)
        else:
            self._history("candidates_found", reachable, locked_idx, reachable_flags, self.config.fixed_delay)
        flash = self._fixed_flash_frames()
        trigger = decision.raw_target_advances - self.config.fixed_delay - flash
        next_attempt_label = self._next_attempt_label()
        self._set_progress(
            AutoRngPhase.DECIDE_ADVANCE,
            f"{next_attempt_label} 锁定原始目标帧 {decision.raw_target_advances}，delay {self.config.fixed_delay}，"
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
        if self._completed_loops > 0 and self.services.recover_zoom_mode is not None:
            recovered = self.services.recover_zoom_mode()
            if self.should_stop():
                return
            if recovered:
                self._set_progress(
                    AutoRngPhase.RUN_SEED_SCRIPT,
                    "检测到缩放模式，已执行双 HOME 恢复",
                    loop_index=self._completed_loops,
                )
        if self.should_stop():
            return
        self._missed_target_advance = None
        self._completed_loops += 1
        self._cycle_started = True
        self._sync_initial = self.config.sync_mode >= 2  # 首位同步精灵
        self._is_sync_active = self._sync_initial
        self._reserved_exit_reseed_pending = False
        self._exit_reseed_done = False
        self._attempt_index = 0
        self._later_candidate_count = 0
        self._history("cycle_start", self._completed_loops)
        text = path.read_text(encoding="utf-8")
        if self.should_stop():
            return
        self.services.run_script_text(text, path.name)
        if self.should_stop():
            return
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
        if (
            seed.after_exit_reseed
            and self._last_search_was_missed
            and decision.kind == AutoRngDecisionKind.RUN_ADVANCE_SCRIPT
            and (decision.requested_advances or 0) > self.config.reseed_threshold_frames
        ):
            self._reserved_exit_reseed_pending = False
            self._exit_reseed_done = False
            self._last_search_was_missed = False
            self._missed_target_advance = None
            self._locked_target = None
            self._set_progress(
                AutoRngPhase.RUN_SEED_SCRIPT,
                f"过场后已错过目标，新目标需过 {decision.requested_advances} 帧，超过重测阈值 {self.config.reseed_threshold_frames}，进入下一轮测种",
                locked_target=None,
                raw_target_advances=decision.raw_target_advances,
                fixed_delay=decision.fixed_delay,
                trigger_advances=decision.trigger_advances,
                current_advances=decision.current_advances,
                remaining_to_trigger=decision.remaining_to_trigger,
            )
            return
        decision = self._apply_exit_reseed_strategy(decision)
        self._requested_advances = decision.requested_advances or 0
        self._set_progress_from_decision(decision)

    def _apply_exit_reseed_strategy(self, decision: AutoRngDecision) -> AutoRngDecision:
        if self.config.exit_script_path is None:
            return decision
        reserve = max(0, int(self.config.reseeding_threshold))
        if reserve <= 0:
            return decision
        remaining = decision.remaining_to_trigger
        if not self._exit_reseed_done and remaining is not None and 0 < remaining <= reserve:
            self._reserved_exit_reseed_pending = False
            return replace(
                decision,
                kind=AutoRngDecisionKind.REIDENTIFY,
                phase=AutoRngPhase.EXIT_RESEED,
                requested_advances=0,
                message=f"剩余 {remaining} 帧不超过预留帧数 {reserve}，进入过场校正流程",
            )
        if self._exit_reseed_done:
            return decision
        if self._reserved_exit_reseed_pending:
            self._reserved_exit_reseed_pending = False
            return decision
        if decision.kind != AutoRngDecisionKind.RUN_ADVANCE_SCRIPT:
            return decision
        requested = decision.requested_advances or 0
        if requested <= reserve:
            return decision
        adjusted = requested - reserve
        self._reserved_exit_reseed_pending = True
        return replace(
            decision,
            requested_advances=adjusted,
            message=f"{decision.message}；已提前预留 {reserve} 帧，本次过帧 {adjusted} 帧",
        )

    def _run_advance_script(self) -> None:
        path = self.config.advance_script_path
        if path is None:
            raise RuntimeError("过帧脚本未配置")
        text = path.read_text(encoding="utf-8")
        if self._exit_reseed_done:
            text = _zero_underground_advance(text)
        # 同步切换：目标在另一同步状态下找到，需要翻转队首
        if self._need_sync_switch and self.config.sync_mode >= 1:
            text = re.sub(r"\$精灵切换开关\s*=\s*\d+", "$精灵切换开关 = 1", text)
        text = prepare_advance_script_text(text, self._requested_advances)
        self._set_progress(
            AutoRngPhase.RUN_ADVANCE_SCRIPT,
            f"启动过帧脚本——{path.name}，本次过帧 {self._requested_advances} 帧",
            last_script_path=path,
        )
        try:
            self.services.run_script_text(text, path.name)
        except Exception as exc:
            self._set_progress(
                AutoRngPhase.RUN_ADVANCE_SCRIPT,
                f"过帧脚本启动失败——{path.name}: {exc}",
                last_script_path=path,
            )
            raise
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
        try:
            self._seed_result = self._with_measurement_time(
                self._call_reidentify_with_retry(
                    self.services.reidentify,
                    seed_with_hint,
                    label="校正",
                )
            )
        except Exception as exc:
            if self.should_stop():
                return
            self._restart_from_seed_script(f"校正连续 2 次失败: {exc}，进入下一轮测种")
            return
        if self.should_stop():
            return
        self._reset_advance_counter(self._seed_result)
        new_advances = self._seed_result.current_advances
        actual_advance = new_advances - prev_advances
        self._set_progress(
            next_phase,
            f"校正完成——目前帧数 {new_advances} 帧，上次实际过帧 {actual_advance} 帧",
            current_advances=new_advances,
            seed_text=self._seed_result.seed_text,
        )

    def _exit_reseed(self) -> None:
        path = self.config.exit_script_path
        if path is None:
            self._reserved_exit_reseed_pending = False
            self._set_progress(AutoRngPhase.SEARCH_TARGET, "未配置过场脚本，跳过过场校正流程")
            return
        service = self.services.reidentify_exit
        if service is None:
            raise RuntimeError("过场校正服务未配置")
        seed = self._require_seed()
        try:
            self.services.run_script_text(path.read_text(encoding="utf-8"), path.name)
        except Exception:
            if self.should_stop():
                return
            raise
        if self.should_stop():
            return
        try:
            self._seed_result = replace(
                self._with_measurement_time(
                    self._call_reidentify_with_retry(
                        service,
                        seed,
                        label="过场校正",
                    )
                ),
                after_exit_reseed=True,
            )
        except Exception as exc:
            if self.should_stop():
                return
            self._restart_from_seed_script(f"过场校正连续 2 次失败: {exc}，进入下一轮测种")
            return
        if self.should_stop():
            return
        self._reset_advance_counter(self._seed_result)
        self._reserved_exit_reseed_pending = False
        self._exit_reseed_done = True
        self._set_progress(
            AutoRngPhase.SEARCH_TARGET,
            f"过场校正完成——{path.name}，目前帧数 {self._seed_result.current_advances} 帧",
            current_advances=self._seed_result.current_advances,
            seed_text=self._seed_result.seed_text,
            last_script_path=path,
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
        """Use a Project_Xs-style live frame loop until the hit-script frame."""
        seed = self._require_seed()
        remaining = self.progress.remaining_to_trigger
        fixed_flash = self._fixed_flash_frames()
        if remaining is None or remaining <= 0:
            self._set_progress(AutoRngPhase.RUN_HIT_SCRIPT, "等待量 ≤ 0，跳过，直接启动撞闪脚本")
            return

        trigger = (self.progress.raw_target_advances or 0) - self.config.fixed_delay - fixed_flash
        wait_seconds = remaining * 1.018 / max(1, seed.npc + 1)
        self._set_progress(
            AutoRngPhase.FINAL_WAIT,
            f"设置活帧触发——还需过 {remaining} 帧（约 {wait_seconds:.0f} 秒），"
            f"到脚本启动帧 {trigger} 时自动运行撞闪脚本",
            current_advances=seed.current_advances,
            remaining_to_trigger=remaining,
        )
        self._reset_advance_counter(seed)
        counter = self._advance_counter

        def on_frame(live_current: int) -> None:
            live_remaining = max(0, trigger - live_current)
            self._seed_result = replace(seed, current_advances=live_current, measured_at=self.services.monotonic())
            self._set_progress(
                AutoRngPhase.FINAL_WAIT,
                "",
                current_advances=live_current,
                remaining_to_trigger=live_remaining,
            )

        target_current: list[int] = []

        def on_target(live_current: int) -> None:
            target_current.append(live_current)

        counter.run_until(
            trigger,
            monotonic=self.services.monotonic,
            sleep=self.services.sleep,
            should_stop=self.should_stop,
            on_frame=on_frame,
            on_target=on_target,
        )
        if self._stop_requested:
            return

        new_current = target_current[0] if target_current else counter.current_advances
        self._seed_result = replace(seed, current_advances=new_current, measured_at=self.services.monotonic())
        msg = f"活帧触发——目前帧数 {new_current} 帧，启动撞闪脚本（撞闪_闪帧 {fixed_flash}）"
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

        self._attempt_index += 1
        attempt_label = self._attempt_label()
        self._set_progress(
            AutoRngPhase.RUN_HIT_SCRIPT,
            f"{attempt_label} 启动撞闪脚本——{path.name}（动态闪帧 {new_flash}）",
            final_flash_frames=new_flash,
            current_advances=new_current,
            remaining_to_trigger=remaining,
        )
        shiny_result = self._run_hit_script_text(text, path.name)
        if shiny_result is not None:
            self._handle_shiny_check_result(shiny_result, path)
            return
        if self.config.escape_continue:
            self._stop_for_unknown_shiny_result(path)
            return
        # 无闪符检测结果时，若开启了自动反查仍然执行
        if self.config.auto_reverse and self.config.reverse_script_path is not None:
            self._last_shiny_interval = None
            self._last_used_delay = None
            self._set_progress(
                AutoRngPhase.REVERSE_LOOKUP,
                "未出闪(无OCR检测)，启动自动反查",
                loop_index=self._completed_loops,
                last_script_path=path,
            )
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
        self._attempt_index += 1
        attempt_label = self._attempt_label()
        commit_log = (
            f"{attempt_label} 启动撞闪脚本——估算帧数 {decision.current_advances + diag_frames_since_ref} 帧"
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
        if self.config.escape_continue:
            self._stop_for_unknown_shiny_result(path)
            return
        # 无闪符检测结果时，若开启了自动反查仍然执行
        if self.config.auto_reverse and self.config.reverse_script_path is not None:
            self._last_shiny_interval = None
            self._last_used_delay = None
            self._set_progress(
                AutoRngPhase.REVERSE_LOOKUP,
                "未出闪(无OCR检测)，启动自动反查",
                loop_index=self._completed_loops,
                last_script_path=path,
            )
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
        used_delay = self.config.fixed_delay
        attempt_label = self._attempt_label()
        if result.is_shiny:
            self._locked_target = None
            self._history("cycle_result", True, result.interval_seconds, trigger, used_delay)
            self._cycle_started = False
            # 判定出闪后执行可替换的录屏脚本。
            try:
                record_path = self.config.record_script_path
                if record_path is not None and record_path.exists():
                    self.services.run_script_text(record_path.read_text(encoding="utf-8"), record_path.name)
                else:
                    self.services.run_script_text("Capture 5000\n", "auto_capture")
            except Exception:
                pass
            self._set_progress(
                AutoRngPhase.COMPLETED,
                f"{attempt_label} 疑似出闪，间隔 {interval_text}，已录像并停止自动流程",
                loop_index=self._completed_loops,
                last_script_path=path,
            )
            return
        if self.config.escape_continue and result.interval_seconds is None:
            self._stop_for_unknown_shiny_result(path)
            return
        if self.config.escape_continue and self._later_candidate_count > 0:
            target = self._require_target()
            self._missed_target_advance = target.raw_target_advances
            self._history(
                "attempt_result",
                self._completed_loops,
                self._attempt_index,
                False,
                result.interval_seconds,
                trigger,
                used_delay,
            )
            self._set_progress(
                AutoRngPhase.RUN_ESCAPE_SCRIPT,
                f"{attempt_label} 未出闪，间隔 {interval_text}；"
                f"当前搜索仍有 {self._later_candidate_count} 个更晚候选，准备逃跑续搜",
                loop_index=self._completed_loops,
                last_script_path=path,
            )
            return
        # 自动反查：未出闪时先反查个体再进入下一轮（反查需要 _locked_target，先不清除）
        if self.config.auto_reverse and self.config.reverse_script_path is not None:
            self._last_shiny_interval = result.interval_seconds
            self._last_used_delay = used_delay
            self._set_progress(
                AutoRngPhase.REVERSE_LOOKUP,
                f"{attempt_label} 未出闪，间隔 {interval_text}，启动自动反查",
                loop_index=self._completed_loops,
                last_script_path=path,
            )
            return
        self._locked_target = None
        self._history("cycle_result", False, result.interval_seconds, trigger, used_delay)
        self._cycle_started = False
        if self.config.loop_mode == "infinite":
            self._set_progress(
                AutoRngPhase.RUN_SEED_SCRIPT,
                f"{attempt_label} 未出闪，间隔 {interval_text}，进入下一轮测种",
                loop_index=self._completed_loops,
                last_script_path=path,
            )
            return
        if self.config.loop_mode == "count" and self._completed_loops < self.config.loop_count:
            self._set_progress(
                AutoRngPhase.RUN_SEED_SCRIPT,
                f"{attempt_label} 未出闪，间隔 {interval_text}，进入下一轮测种",
                loop_index=self._completed_loops,
                last_script_path=path,
            )
            return
        self._set_progress(
            AutoRngPhase.COMPLETED,
            f"{attempt_label} 未出闪，间隔 {interval_text}，自动流程完成",
            loop_index=self._completed_loops,
            last_script_path=path,
        )

    def _run_escape_script(self) -> None:
        path = self.config.escape_script_path
        if path is None:
            raise RuntimeError("逃跑续搜已启用，但未配置逃跑脚本")
        attempt_label = self._attempt_label()
        self._set_progress(
            AutoRngPhase.RUN_ESCAPE_SCRIPT,
            f"{attempt_label} 启动逃跑脚本——{path.name}",
            last_script_path=path,
        )
        if self.should_stop():
            return
        try:
            self.services.run_script_text(path.read_text(encoding="utf-8"), path.name)
        except Exception:
            if self.should_stop():
                return
            raise
        if self.should_stop():
            return

        seed = self._require_seed()
        self._seed_result = replace(
            seed,
            expected_advances_hint=None,
            after_exit_reseed=False,
            advance_mode="linear",
            timing_seed=None,
            timeline_npc=0,
            pokemon_npc=0,
            white_delay=0.0,
            advance_delay=0,
            advance_delay_2=0,
        )
        self._locked_target = None
        self._requested_advances = 0
        self._reserved_exit_reseed_pending = False
        self._exit_reseed_done = False
        self._need_sync_switch = False
        self._later_candidate_count = 0
        self._set_progress(
            AutoRngPhase.REIDENTIFY,
            f"{attempt_label} 逃跑脚本完成——{path.name}，开始普通校正",
            locked_target=None,
            raw_target_advances=None,
            trigger_advances=None,
            remaining_to_trigger=None,
            final_flash_frames=None,
            current_advances=seed.current_advances,
            last_script_path=path,
        )

    def _reverse_lookup(self) -> None:
        """运行反查脚本 → OCR → 搜索 → 输出候选个体。"""
        seed = self._require_seed()
        target = self._locked_target
        if target is None:
            self._set_progress(AutoRngPhase.LOOP_CHECK, "目标尚未锁定，跳过反查")
            return
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
            self._locked_target = None
            self._history("cycle_result", False, interval, None, used_delay)
            self._cycle_started = False
            self._set_progress(AutoRngPhase.LOOP_CHECK, f"反查失败: {exc}")
            return
        self._locked_target = None
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

    def _attempt_label(self) -> str:
        return f"[第 {self._completed_loops} 轮 / 第 {self._attempt_index} 次]"

    def _next_attempt_label(self) -> str:
        return f"[第 {self._completed_loops} 轮 / 第 {self._attempt_index + 1} 次]"

    def _stop_for_unknown_shiny_result(self, path: object) -> None:
        self._cycle_started = False
        self._set_progress(
            AutoRngPhase.FAILED,
            f"{self._attempt_label()} OCR 判闪结果未知，已停止自动流程，请人工确认当前战斗",
            loop_index=self._completed_loops,
            last_script_path=path,
        )

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
        if self._stop_requested and phase != AutoRngPhase.IDLE:
            return
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

    def _record_no_candidate_seed(self, seed: AutoRngSeedResult) -> None:
        seed_key = _seed_identity(seed)
        if seed_key == self._last_no_candidate_seed_key:
            self._repeated_no_candidate_seed_count += 1
        else:
            self._last_no_candidate_seed_key = seed_key
            self._repeated_no_candidate_seed_count = 1
        if self._repeated_no_candidate_seed_count >= _REPEATED_NO_CANDIDATE_SEED_LIMIT:
            raise RuntimeError(
                f"连续 {_REPEATED_NO_CANDIDATE_SEED_LIMIT} 次捕获到相同 seed 且无候选，"
                "可能眼睛 ROI 或眼睛模板不正确，请停止后重新框选眼睛区域再开始"
            )

    def _clear_no_candidate_seed_guard(self) -> None:
        self._last_no_candidate_seed_key = None
        self._repeated_no_candidate_seed_count = 0

    def _require_seed(self) -> AutoRngSeedResult:
        if self._seed_result is None:
            raise RuntimeError("seed 尚未捕获")
        return self._seed_result

    def _with_measurement_time(self, seed_result: AutoRngSeedResult) -> AutoRngSeedResult:
        if seed_result.measured_at is not None:
            return seed_result
        return replace(seed_result, measured_at=self.services.monotonic())

    def _call_reidentify_with_retry(
        self,
        service: Callable[[AutoRngSeedResult], AutoRngSeedResult],
        seed: AutoRngSeedResult,
        *,
        label: str,
    ) -> AutoRngSeedResult:
        last_error: Exception | None = None
        for attempt in range(1, 3):
            if self.should_stop():
                raise RuntimeError(f"{label}已取消")
            try:
                return service(seed)
            except Exception as exc:
                last_error = exc
                if self.should_stop():
                    raise
                if attempt == 1:
                    self._emit(
                        AutoRngProgress(
                            phase=self.progress.phase,
                            loop_index=self.progress.loop_index,
                            log_message=f"{label} 第 1 次失败: {exc}，重试一次",
                            locked_target=self.progress.locked_target,
                            raw_target_advances=self.progress.raw_target_advances,
                            fixed_delay=self.progress.fixed_delay,
                            trigger_advances=self.progress.trigger_advances,
                            current_advances=self.progress.current_advances,
                            remaining_to_trigger=self.progress.remaining_to_trigger,
                            final_flash_frames=self.progress.final_flash_frames,
                            last_script_path=self.progress.last_script_path,
                            seed_text=self.progress.seed_text,
                        )
                    )
        assert last_error is not None
        raise last_error

    def _restart_from_seed_script(self, message: str) -> None:
        if self.should_stop():
            return
        self._seed_result = None
        self._locked_target = None
        self._missed_target_advance = None
        self._last_search_was_missed = False
        self._requested_advances = 0
        self._later_candidate_count = 0
        self._reserved_exit_reseed_pending = False
        self._exit_reseed_done = False
        self._need_sync_switch = False
        self._cycle_started = False
        self._set_progress(AutoRngPhase.RUN_SEED_SCRIPT, message, locked_target=None)

    def _reset_advance_counter(self, seed_result: AutoRngSeedResult) -> None:
        self._advance_counter = self._build_advance_counter(seed_result)

    def _build_advance_counter(
        self,
        seed_result: AutoRngSeedResult,
    ) -> ProjectXsAdvanceCounter | ProjectXsTimelineAdvanceCounter:
        if seed_result.advance_mode == "timeline":
            counter = ProjectXsTimelineAdvanceCounter()
            counter.reset(
                current_advances=seed_result.current_advances,
                state=self._timing_state(seed_result),
                now=self._seed_measured_at(seed_result),
                timeline_npc=seed_result.timeline_npc,
                pokemon_npc=seed_result.pokemon_npc,
                white_delay=seed_result.white_delay,
                advance_delay=seed_result.advance_delay,
                advance_delay_2=seed_result.advance_delay_2,
            )
            return counter
        counter = ProjectXsAdvanceCounter()
        counter.reset(
            current_advances=seed_result.current_advances,
            npc=seed_result.npc,
            now=self._seed_measured_at(seed_result),
        )
        return counter

    def _timing_state(self, seed_result: AutoRngSeedResult) -> SeedState32:
        seed = seed_result.timing_seed if seed_result.timing_seed is not None else seed_result.seed
        if isinstance(seed, SeedState32):
            return seed
        if isinstance(seed, SeedPair64):
            return seed.to_state32()
        to_state32 = getattr(seed, "to_state32", None)
        if callable(to_state32):
            state = to_state32()
            if isinstance(state, SeedState32):
                return state
        raise RuntimeError("timeline advance requires a SeedState32 timing seed")

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
