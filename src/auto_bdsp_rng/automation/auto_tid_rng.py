from __future__ import annotations

import math
import re
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any

from auto_bdsp_rng.automation.auto_rng.ocr_regions import OcrRegion
from auto_bdsp_rng.gen8_id import IDFilter, IDState8, generate_ids
from auto_bdsp_rng.rng_core import BDSPXorshift, SeedPair64, SeedState32


# Estimating a target arrival time only clones the small Xorshift state and
# consumes the remaining interval values.  Keep an upper bound so a malformed
# or unusually large user-supplied target cannot make diagnostics expensive.
_MAX_TARGET_TIME_ESTIMATE_ADVANCES = 100_000
_WAIT_PROGRESS_LOG_ADVANCES = 500


@dataclass
class ProjectXsMunchlaxAdvanceCounter:
    """Project_Xs TID/SID counter: one advance at each Munchlax blink interval."""

    current_advances: int = 0
    next_tick_at: float = 0.0
    # Timing metadata is diagnostic only.  ``last_tick_at`` is the scheduled
    # monotonic instant of the most recently consumed blink; it is deliberately
    # separate from the wall-clock timestamp shown by the UI log.
    last_tick_at: float | None = None
    last_interval_seconds: float | None = None
    # Positive lateness observed when a scheduled tick is consumed.  A large
    # value is useful for diagnosing sleep/resume or a stalled worker clock.
    last_tick_lag_seconds: float | None = None
    max_tick_lag_seconds: float | None = None
    _rng: BDSPXorshift | None = None

    def reset(self, *, current_advances: int, seed: SeedPair64 | SeedState32 | Any, now: float) -> None:
        self.current_advances = int(current_advances)
        self._rng = BDSPXorshift(_seed_state_from_value(seed))
        now_value = float(now)
        self.next_tick_at = now_value + self._next_interval()
        self.last_tick_at = None
        self.last_interval_seconds = self.next_tick_at - now_value
        self.last_tick_lag_seconds = 0.0
        self.max_tick_lag_seconds = 0.0

    def _rangefloat(self, minimum: float, maximum: float) -> float:
        if self._rng is None:
            raise RuntimeError("Munchlax counter has not been reset")
        temp = (self._rng.next() & 0x7FFFFF) / 8388607.0
        return temp * minimum + (1.0 - temp) * maximum

    def _next_interval(self) -> float:
        return self._rangefloat(3.0, 12.0) + 0.285

    def estimate_target_at(self, target_advances: int) -> float | None:
        """Estimate the scheduled monotonic time at which ``target_advances``
        is reached.

        The counter's RNG is copied, so asking for an estimate never changes
        the live schedule.  ``None`` is returned when the counter is not
        initialized or the requested span is too large for a bounded
        diagnostic calculation.
        """

        if self._rng is None:
            return None
        try:
            target = int(target_advances)
        except (TypeError, ValueError, OverflowError):
            return None
        remaining = target - int(self.current_advances)
        if remaining <= 0 or remaining > _MAX_TARGET_TIME_ESTIMATE_ADVANCES:
            return None
        try:
            next_tick_at = float(self.next_tick_at)
            clone = BDSPXorshift(SeedState32(*self._rng.words))
            # ``next_tick_at`` is already the first remaining tick.  The
            # cloned RNG therefore supplies intervals two through N.
            for _ in range(remaining - 1):
                value = clone.next()
                temp = (value & 0x7FFFFF) / 8388607.0
                next_tick_at += temp * 3.0 + (1.0 - temp) * 12.0 + 0.285
            return next_tick_at if math.isfinite(next_tick_at) else None
        except (TypeError, ValueError, OverflowError):
            return None

    def advance_one_blink(self) -> int:
        if self._rng is None:
            raise RuntimeError("Munchlax counter has not been reset")
        scheduled_tick = self.next_tick_at
        self.current_advances += 1
        self.last_tick_at = scheduled_tick
        self.last_interval_seconds = self._next_interval()
        self.next_tick_at = scheduled_tick + self.last_interval_seconds
        return self.current_advances

    def advance_to(self, now: float) -> int:
        now_value = float(now)
        advanced = 0
        while now_value + 1e-9 >= self.next_tick_at:
            lag = max(0.0, now_value - self.next_tick_at)
            self.last_tick_lag_seconds = lag
            if self.max_tick_lag_seconds is None:
                self.max_tick_lag_seconds = lag
            else:
                self.max_tick_lag_seconds = max(self.max_tick_lag_seconds, lag)
            self.advance_one_blink()
            advanced += 1
        if advanced <= 0:
            self.last_tick_lag_seconds = max(0.0, now_value - self.next_tick_at)
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
                if should_stop():
                    break
            advanced = self.advance_to(monotonic())
            if advanced <= 0:
                continue
            if on_frame is not None:
                on_frame(self.current_advances)
        if self.current_advances >= target_advances and not should_stop() and on_target is not None:
            on_target(self.current_advances)
        return self.current_advances


class AutoTidRngPhase(str, Enum):
    IDLE = "空闲"
    RUN_SEED_SCRIPT = "运行测种脚本"
    CAPTURE_TIDSID = "TID/SID测种"
    SEARCH_TARGET = "搜索目标Display TID"
    WAIT_NAME_TRIGGER = "等待取名帧"
    RUN_NAME_SCRIPT = "运行取名脚本"
    WAIT_REVERSE_TRIGGER = "等待反查帧"
    RUN_REVERSE_ID_SCRIPT = "运行反查ID脚本"
    OCR_TID = "OCR识别TID"
    CALIBRATE_DELAY = "校准delay"
    COMPLETED = "已完成"
    FAILED = "失败"


@dataclass(frozen=True)
class AutoTidTarget:
    advances: int
    tid: int
    sid: int
    tsv: int
    display_tid: int
    state: IDState8

    @classmethod
    def from_state(cls, state: IDState8) -> "AutoTidTarget":
        return cls(
            advances=int(state.advances),
            tid=int(state.tid),
            sid=int(state.sid),
            tsv=int(state.tsv),
            display_tid=int(state.display_tid),
            state=state,
        )


@dataclass(frozen=True)
class AutoTidRngConfig:
    script_dir: Path
    seed_script_path: Path | None = None
    name_script_path: Path | None = None
    reverse_id_script_path: Path | None = None
    start_phase: AutoTidRngPhase = AutoTidRngPhase.RUN_SEED_SCRIPT
    frame_threshold: int = 300
    target_tids: tuple[int, ...] = ()
    target_display_tids: tuple[int, ...] = ()
    delay: int = 0
    reverse_lookup_window: int = 50
    ocr_region: OcrRegion | None = None
    loop_mode: str = "single"
    loop_count: int = 1
    debug_output: bool = False


@dataclass(frozen=True)
class AutoTidSeedResult:
    seed: SeedPair64 | SeedState32 | Any
    current_advances: int = 0
    npc: int = 0
    seed_text: str = ""
    measured_at: float | None = None


@dataclass(frozen=True)
class AutoTidRngProgress:
    phase: AutoTidRngPhase = AutoTidRngPhase.IDLE
    loop_index: int = 0
    seed_text: str = ""
    current_advances: int | None = None
    target_tid: int | None = None
    target_sid: int | None = None
    target_display_tid: int | None = None
    target_advances: int | None = None
    trigger_advances: int | None = None
    remaining_to_trigger: int | None = None
    ocr_text: str = ""
    ocr_tid: int | None = None
    ocr_advances: int | None = None
    actual_delay: int | None = None
    id_states: tuple[IDState8, ...] = ()
    last_script_path: Path | None = None
    log_message: str = ""
    # Set only when a stop request has been accepted.  Keeping the reason in
    # the immutable progress object lets the UI/history consumers inspect the
    # same context that was written to the run log.
    stop_reason: str = ""
    # Monotonic timing for the active Munchlax wait.  These fields are kept at
    # the end of the dataclass so existing positional construction remains
    # compatible with older integrations.
    wait_started_at: float | None = None
    wait_elapsed_seconds: float | None = None
    wait_next_tick_at: float | None = None
    wait_target_at: float | None = None
    wait_tick_lag_seconds: float | None = None
    wait_max_tick_lag_seconds: float | None = None
    wait_keep_awake: bool | None = None


def parse_tid_text(text: str) -> int | None:
    for match in re.finditer(r"\d{1,6}", str(text)):
        value = int(match.group(0))
        if 0 <= value <= 65535:
            return value
    return None


def select_target_tid(
    states: Sequence[IDState8],
    target_tids: Sequence[int],
    *,
    frame_threshold: int,
) -> AutoTidTarget | None:
    targets = {int(tid) for tid in target_tids}
    matches = [
        state
        for state in states
        if int(state.tid) in targets and 0 <= int(state.advances) <= int(frame_threshold)
    ]
    if not matches:
        return None
    return AutoTidTarget.from_state(min(matches, key=lambda state: int(state.advances)))


def select_target_display_tid(
    states: Sequence[IDState8],
    target_display_tids: Sequence[int],
    *,
    frame_threshold: int,
) -> AutoTidTarget | None:
    targets = {int(tid) for tid in target_display_tids}
    matches = [
        state
        for state in states
        if int(state.display_tid) in targets and 0 <= int(state.advances) <= int(frame_threshold)
    ]
    if not matches:
        return None
    return AutoTidTarget.from_state(min(matches, key=lambda state: int(state.advances)))


def reverse_lookup_span(center_advances: int, window: int) -> tuple[int, int, int]:
    clamped_window = max(0, min(10_000, int(window)))
    start = max(0, int(center_advances) - clamped_window)
    end = int(center_advances) + clamped_window
    return start, end, end - start + 1


def _seed_state_from_value(seed: SeedPair64 | SeedState32 | Any) -> SeedState32:
    if isinstance(seed, SeedState32):
        return seed
    if isinstance(seed, SeedPair64):
        return seed.to_state32()
    to_state32 = getattr(seed, "to_state32", None)
    if callable(to_state32):
        return to_state32()
    to_seed_pair64 = getattr(seed, "to_seed_pair64", None)
    if callable(to_seed_pair64):
        return to_seed_pair64().to_state32()
    raise TypeError("Auto TID RNG seed result must contain SeedPair64 or SeedState32")


def _seed_pair_from_result(seed_result: AutoTidSeedResult) -> SeedPair64:
    seed = seed_result.seed
    if isinstance(seed, SeedPair64):
        return seed
    if isinstance(seed, SeedState32):
        return seed.to_seed_pair64()
    to_seed_pair64 = getattr(seed, "to_seed_pair64", None)
    if callable(to_seed_pair64):
        return to_seed_pair64()
    raise TypeError("Auto TID RNG seed result must contain SeedPair64 or SeedState32")


def _default_search_id_states(
    seed_result: AutoTidSeedResult,
    frame_threshold: int,
    target_display_tids: Sequence[int],
) -> Sequence[IDState8]:
    _ = target_display_tids
    return generate_ids(
        _seed_pair_from_result(seed_result),
        initial_advances=0,
        max_advances=max(0, int(frame_threshold)) + 1,
        state_filter=IDFilter(),
    )


def _default_lookup_tid_state(
    seed_result: AutoTidSeedResult,
    tid: int,
    center_advances: int,
    window: int,
) -> IDState8 | None:
    start, _end, count = reverse_lookup_span(center_advances, window)
    states = generate_ids(
        _seed_pair_from_result(seed_result),
        initial_advances=start,
        max_advances=count,
        state_filter=IDFilter(tid=[int(tid)]),
    )
    if not states:
        return None
    return min(states, key=lambda state: (abs(int(state.advances) - int(center_advances)), int(state.advances)))


def _missing_service(*_args: object, **_kwargs: object) -> object:
    raise RuntimeError("AutoTidRngRunner service is not configured")


@dataclass(frozen=True)
class AutoTidRngServices:
    capture_seed: Callable[[], AutoTidSeedResult] = _missing_service  # type: ignore[assignment]
    search_id_states: Callable[[AutoTidSeedResult, int, Sequence[int]], Sequence[IDState8]] = _default_search_id_states
    lookup_tid_state: Callable[[AutoTidSeedResult, int, int, int], IDState8 | None] = _default_lookup_tid_state
    run_script_text: Callable[[str, str], object] = _missing_service  # type: ignore[assignment]
    recognize_tid: Callable[[], str] = _missing_service  # type: ignore[assignment]
    recover_zoom_mode: Callable[[], bool] | None = None
    stop_current_script: Callable[[], None] | None = None
    monotonic: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep


class AutoTidRngRunner:
    def __init__(
        self,
        config: AutoTidRngConfig,
        *,
        services: AutoTidRngServices | None = None,
        progress_callback: Callable[[AutoTidRngProgress], None] | None = None,
        log_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.config = config
        self.services = services or AutoTidRngServices()
        self.progress_callback = progress_callback
        self.log_callback = log_callback
        self.progress = AutoTidRngProgress()
        self._stop_requested = False
        self._stop_lock = threading.Lock()
        self._seed_result: AutoTidSeedResult | None = None
        self._target: AutoTidTarget | None = None
        self._completed_loops = 0
        self._advance_counter = ProjectXsMunchlaxAdvanceCounter()
        self._wait_phase: AutoTidRngPhase | None = None
        self._wait_started_at: float | None = None
        self._wait_target_at: float | None = None
        self._wait_target_advances: int | None = None
        self._wait_theoretical_seconds: float | None = None
        self._wait_last_logged_advances: int | None = None

    def stop(self, reason: str | None = None) -> None:
        """Request a cooperative stop and emit one diagnostic snapshot.

        ``stop()`` remains intentionally compatible with older callers.  When
        no reason is supplied it keeps the historical short message; callers
        that know the source of the request should pass ``reason`` so the
        current phase/advance state is preserved in the log.
        """

        # Serialize the acceptance of a stop request.  The UI and worker can
        # race when a capture error arrives at the same time as a button
        # click; only the first caller should cancel the script and emit a
        # snapshot.
        with self._stop_lock:
            if self._stop_requested:
                return
            # ``run_until`` may currently be sleeping between two blinks.
            # Refresh the diagnostic fields before taking the snapshot so a
            # stop request does not report the previous callback's Adv value.
            if self._wait_phase is not None:
                try:
                    self._refresh_wait_progress(emit=False)
                except Exception:
                    # A diagnostic clock failure must never prevent the stop
                    # request itself from reaching the worker.
                    pass
            previous = self.progress
            self._stop_requested = True
        normalized_reason = (
            ""
            if reason is None
            else " ".join(str(reason).split()).replace("；", "/")
        )
        script_stop_error: str | None = None
        if self.services.stop_current_script is not None:
            try:
                self.services.stop_current_script()
            except Exception as exc:
                # A failing script cancellation must not hide the stop
                # request itself or prevent its diagnostic record from being
                # delivered to the UI.
                script_stop_error = f"{type(exc).__name__}: {exc}".strip()
        if normalized_reason:
            try:
                message = self._format_stop_message(
                    previous,
                    normalized_reason,
                    script_stop_error=script_stop_error,
                )
            except Exception as exc:
                # Formatting is diagnostic-only.  A malformed third-party
                # progress object must not turn a successful stop into a
                # silent failure.
                message = (
                    "已请求停止自动 TID 乱数"
                    f"；原因={normalized_reason}"
                    f"；停止快照格式化失败={type(exc).__name__}: {exc}"
                )
        else:
            message = "已请求停止自动 TID 乱数"
            if script_stop_error:
                message += f"；停止脚本调用失败={script_stop_error}"
        try:
            self._set_progress(
                AutoTidRngPhase.IDLE,
                message,
                stop_reason=normalized_reason,
            )
        except Exception:
            # Stop state has already been accepted.  A third-party progress
            # or log callback must not turn that accepted request into an
            # exception visible to the UI thread.
            pass

    def _format_stop_message(
        self,
        progress: AutoTidRngProgress,
        reason: str,
        *,
        script_stop_error: str | None = None,
    ) -> str:
        """Return one-line, human-readable state for a stop request."""

        def value_or_dash(value: object) -> str:
            return "-" if value is None or value == "" else str(value)

        phase = progress.phase.value if hasattr(progress.phase, "value") else str(progress.phase)
        current = progress.current_advances
        target = progress.target_advances
        trigger = progress.trigger_advances
        remaining = progress.remaining_to_trigger
        if remaining is None and current is not None and trigger is not None:
            try:
                remaining = max(0, int(trigger) - int(current))
            except (TypeError, ValueError, OverflowError):
                remaining = None
        trigger_delta: int | None = None
        if current is not None and trigger is not None:
            try:
                trigger_delta = int(trigger) - int(current)
            except (TypeError, ValueError, OverflowError):
                trigger_delta = None
        if progress.target_display_tid is None:
            display_tid = "-"
        else:
            try:
                display_tid = f"{int(progress.target_display_tid):06d}"
            except (TypeError, ValueError, OverflowError):
                display_tid = value_or_dash(progress.target_display_tid)
        fields = [
            "已请求停止自动 TID 乱数",
            f"原因={reason or '-'}",
            f"阶段={phase}",
            f"轮次={value_or_dash(progress.loop_index)}",
            f"当前Adv={value_or_dash(current)}",
            f"目标Adv={value_or_dash(target)}",
            f"触发Adv={value_or_dash(trigger)}",
            f"剩余Adv={value_or_dash(remaining)}",
            f"触发差Adv={value_or_dash(trigger_delta)}",
            f"目标DisplayTID={display_tid}",
            f"目标TID={value_or_dash(progress.target_tid)}",
            f"目标SID={value_or_dash(progress.target_sid)}",
            f"配置delay={value_or_dash(self.config.delay)}",
            f"候选数={len(progress.id_states)}",
        ]
        if progress.seed_text:
            fields.append(f"Seed={progress.seed_text}")
        if progress.ocr_tid is not None or progress.ocr_text:
            fields.append(f"OCR_TID={value_or_dash(progress.ocr_tid)}")
            if progress.ocr_text:
                fields.append(f"OCR原文={progress.ocr_text}")
        if progress.actual_delay is not None:
            fields.append(f"实际delay={progress.actual_delay}")
        if progress.last_script_path is not None:
            fields.append(f"最近脚本={getattr(progress.last_script_path, 'name', progress.last_script_path)}")
        if progress.wait_started_at is not None or phase in {
            AutoTidRngPhase.WAIT_NAME_TRIGGER.value,
            AutoTidRngPhase.WAIT_REVERSE_TRIGGER.value,
        }:
            started_at = progress.wait_started_at
            target_at = progress.wait_target_at
            next_tick_at = progress.wait_next_tick_at

            def seconds_text(value: object) -> str:
                try:
                    number = float(value)
                except (TypeError, ValueError, OverflowError):
                    return "-"
                return f"{number:.3f}" if math.isfinite(number) else "-"

            def relative_text(value: object) -> str:
                if started_at is None:
                    return "-"
                try:
                    return self._duration_text(float(value) - float(started_at))
                except (TypeError, ValueError, OverflowError):
                    return "-"

            theoretical: float | None = None
            if started_at is not None and target_at is not None:
                try:
                    candidate = max(0.0, float(target_at) - float(started_at))
                except (TypeError, ValueError, OverflowError):
                    candidate = None
                if candidate is not None and math.isfinite(candidate):
                    theoretical = candidate
            keep_awake = "启用" if progress.wait_keep_awake is True else "未启用"
            fields.extend(
                [
                    f"计时起点(monotonic)={seconds_text(started_at)}",
                    f"等待已用={self._duration_text(progress.wait_elapsed_seconds)}",
                    f"理论等待={self._duration_text(theoretical)}",
                    f"目标时刻={relative_text(target_at)}",
                    f"下一tick={relative_text(next_tick_at)}",
                    f"tick滞后={self._duration_text(progress.wait_tick_lag_seconds)}",
                    f"最大tick滞后={self._duration_text(progress.wait_max_tick_lag_seconds)}",
                    f"等待Adv保活={keep_awake}",
                ]
            )
        if script_stop_error:
            fields.append(f"停止脚本调用失败={script_stop_error}")
        return "；".join(fields)

    def should_stop(self) -> bool:
        return self._stop_requested

    def run(self, *, max_steps: int = 10000) -> AutoTidRngProgress:
        if self.progress.phase == AutoTidRngPhase.IDLE:
            if self.config.start_phase == AutoTidRngPhase.CAPTURE_TIDSID:
                self._begin_cycle("开始自动 TID 乱数，从捕获 Seed 开始", phase=AutoTidRngPhase.CAPTURE_TIDSID)
            else:
                self._begin_cycle("开始自动 TID 乱数，运行测种脚本")
        steps = 0
        while not self._stop_requested and steps < max_steps:
            steps += 1
            phase = self.progress.phase
            if phase == AutoTidRngPhase.RUN_SEED_SCRIPT:
                self._run_seed_script()
            elif phase == AutoTidRngPhase.CAPTURE_TIDSID:
                self._capture_tidsid()
            elif phase == AutoTidRngPhase.SEARCH_TARGET:
                self._search_target()
            elif phase == AutoTidRngPhase.WAIT_NAME_TRIGGER:
                self._wait_name_trigger()
            elif phase == AutoTidRngPhase.RUN_NAME_SCRIPT:
                self._run_name_script()
            elif phase == AutoTidRngPhase.WAIT_REVERSE_TRIGGER:
                self._wait_reverse_trigger()
            elif phase == AutoTidRngPhase.RUN_REVERSE_ID_SCRIPT:
                self._run_reverse_id_script()
            elif phase == AutoTidRngPhase.OCR_TID:
                self._ocr_tid()
            elif phase == AutoTidRngPhase.CALIBRATE_DELAY:
                self._calibrate_delay()
            else:
                break
        return self.progress

    def _reset_wait_state(self) -> None:
        self._wait_phase = None
        self._wait_started_at = None
        self._wait_target_at = None
        self._wait_target_advances = None
        self._wait_theoretical_seconds = None
        self._wait_last_logged_advances = None

    def _read_monotonic(self) -> float:
        """Read the injected clock as a finite float for diagnostics."""

        value = float(self.services.monotonic())
        if not math.isfinite(value):
            raise ValueError("monotonic clock returned a non-finite value")
        return value

    @staticmethod
    def _seconds_text(value: object) -> str:
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return "-"
        return f"{number:.3f}" if math.isfinite(number) else "-"

    @classmethod
    def _duration_text(cls, value: object) -> str:
        text = cls._seconds_text(value)
        return "-" if text == "-" else f"{text}s"

    @staticmethod
    def _seed_text_for_log(seed: AutoTidSeedResult) -> str:
        if seed.seed_text:
            return str(seed.seed_text)
        try:
            return " ".join(_seed_pair_from_result(seed).format_seeds())
        except Exception:
            return "-"

    @staticmethod
    def _exception_chain_text(error: BaseException) -> str:
        parts: list[str] = []
        current: BaseException | None = error
        seen: set[int] = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            parts.append(f"{type(current).__name__}: {current}".strip())
            current = current.__cause__ or current.__context__
        return " <- ".join(parts)

    def _wait_progress_updates(
        self,
        *,
        now: float | None = None,
        current_advances: int | None = None,
        target_advances: int | None = None,
    ) -> dict[str, object]:
        """Build the live timing fields for the active Munchlax wait."""

        if self._wait_started_at is None:
            return {}
        now_value = self._read_monotonic() if now is None else float(now)
        if not math.isfinite(now_value):
            now_value = self._wait_started_at
        start = float(self._wait_started_at)
        target = self._wait_target_advances if target_advances is None else int(target_advances)
        if target is None:
            target = self.progress.trigger_advances

        counter = self._advance_counter
        if current_advances is None:
            current = int(counter.current_advances)
        else:
            current = int(current_advances)
        next_tick_at: float | None = None
        if getattr(counter, "_rng", None) is not None:
            candidate = float(counter.next_tick_at)
            if math.isfinite(candidate):
                next_tick_at = candidate
        if next_tick_at is None:
            next_tick_at = self.progress.wait_next_tick_at

        elapsed = max(0.0, now_value - start)
        remaining = None if target is None else max(0, int(target) - current)

        lag: float | None = None
        counter_lag = getattr(counter, "last_tick_lag_seconds", None)
        if counter_lag is not None:
            try:
                lag = max(0.0, float(counter_lag))
            except (TypeError, ValueError, OverflowError):
                lag = None
        if next_tick_at is not None:
            try:
                overdue = max(0.0, now_value - float(next_tick_at))
            except (TypeError, ValueError, OverflowError):
                overdue = 0.0
            lag = overdue if lag is None else max(lag, overdue)

        max_lag: float | None = None
        counter_max_lag = getattr(counter, "max_tick_lag_seconds", None)
        if counter_max_lag is not None:
            try:
                max_lag = max(0.0, float(counter_max_lag))
            except (TypeError, ValueError, OverflowError):
                max_lag = None
        previous_max = self.progress.wait_max_tick_lag_seconds
        if previous_max is not None:
            try:
                max_lag = max(float(previous_max), max_lag or 0.0)
            except (TypeError, ValueError, OverflowError):
                pass

        return {
            "current_advances": current,
            "remaining_to_trigger": remaining,
            "wait_started_at": start,
            "wait_elapsed_seconds": elapsed,
            "wait_next_tick_at": next_tick_at,
            "wait_target_at": self._wait_target_at,
            "wait_tick_lag_seconds": lag,
            "wait_max_tick_lag_seconds": max_lag,
            # The waiting loop must not send controller input.  Keep this
            # explicit so a stop snapshot cannot be mistaken for an active
            # keep-awake task.
            "wait_keep_awake": False,
        }

    def _refresh_wait_progress(
        self,
        *,
        now: float | None = None,
        current_advances: int | None = None,
        message: str = "",
        emit: bool = True,
    ) -> dict[str, object]:
        """Refresh progress timing while a wait is active."""

        if self._wait_phase is None or self._wait_started_at is None:
            return {}
        updates = self._wait_progress_updates(now=now, current_advances=current_advances)
        if emit:
            self._set_progress(self._wait_phase, message, **updates)
        else:
            # A stop can be requested by a progress callback.  Updating the
            # immutable snapshot directly avoids re-entering that callback
            # before ``_stop_requested`` is set.
            self.progress = replace(self.progress, **updates)
        return updates

    def _format_wait_timing_message(
        self,
        prefix: str,
        updates: dict[str, object],
        *,
        include_start: bool = False,
    ) -> str:
        start = updates.get("wait_started_at")
        target_at = updates.get("wait_target_at")
        next_tick_at = updates.get("wait_next_tick_at")

        def relative(value: object) -> str:
            if start is None or value is None:
                return "-"
            try:
                return self._duration_text(float(value) - float(start))
            except (TypeError, ValueError, OverflowError):
                return "-"

        theoretical: float | None = None
        if start is not None and target_at is not None:
            try:
                theoretical = max(0.0, float(target_at) - float(start))
            except (TypeError, ValueError, OverflowError):
                theoretical = None
        fields = [
            prefix,
            f"当前Adv={updates.get('current_advances', '-')}"
        ]
        fields.extend(
            [
                f"剩余Adv={updates.get('remaining_to_trigger', '-')}",
                f"等待已用={self._duration_text(updates.get('wait_elapsed_seconds'))}",
                f"理论等待={self._duration_text(theoretical)}",
                f"目标时刻={relative(target_at)}",
                f"下一tick={relative(next_tick_at)}",
                f"tick滞后={self._duration_text(updates.get('wait_tick_lag_seconds'))}",
                f"最大tick滞后={self._duration_text(updates.get('wait_max_tick_lag_seconds'))}",
                "等待Adv保活=未启用（等待期间不发送按键；眨眼捕捉阶段每10次按L）",
            ]
        )
        if include_start:
            fields.insert(1, f"计时起点(monotonic)={self._seconds_text(start)}")
        return "；".join(fields)

    def _begin_wait(
        self,
        *,
        phase: AutoTidRngPhase,
        seed: AutoTidSeedResult,
        trigger: int,
        label: str,
    ) -> dict[str, object]:
        """Initialize a Munchlax schedule and emit its timing baseline."""

        if seed.measured_at is None:
            start = self._read_monotonic()
        else:
            start = float(seed.measured_at)
            if not math.isfinite(start):
                start = self._read_monotonic()
        self._advance_counter.reset(
            current_advances=int(seed.current_advances),
            seed=seed.seed,
            now=start,
        )
        self._wait_phase = phase
        self._wait_started_at = start
        self._wait_target_advances = int(trigger)
        self._wait_target_at = self._advance_counter.estimate_target_at(int(trigger))
        if self._wait_target_at is None and int(trigger) <= int(seed.current_advances):
            self._wait_target_at = start
        self._wait_theoretical_seconds = (
            None
            if self._wait_target_at is None
            else max(0.0, float(self._wait_target_at) - start)
        )
        self._wait_last_logged_advances = int(seed.current_advances)
        updates = self._wait_progress_updates(
            now=self._read_monotonic(),
            current_advances=int(seed.current_advances),
            target_advances=int(trigger),
        )
        message = self._format_wait_timing_message(
            f"{label}等待开始：起点Adv={int(seed.current_advances)}；目标触发Adv={int(trigger)}",
            updates,
            include_start=True,
        )
        self._set_progress(phase, message, **updates)
        return updates

    def _begin_cycle(self, message: str, *, phase: AutoTidRngPhase = AutoTidRngPhase.RUN_SEED_SCRIPT) -> None:
        self._completed_loops += 1
        self._target = None
        self._reset_wait_state()
        # A new measurement invalidates the previous seed/target context.  Do
        # not let a stop during the next seed script be reported with the
        # preceding cycle's target Adv or Display TID.
        self._set_progress(
            phase,
            message,
            loop_index=self._completed_loops,
            seed_text="",
            current_advances=None,
            target_tid=None,
            target_sid=None,
            target_display_tid=None,
            target_advances=None,
            trigger_advances=None,
            remaining_to_trigger=None,
            ocr_text="",
            ocr_tid=None,
            ocr_advances=None,
            actual_delay=None,
            id_states=(),
            last_script_path=None,
            stop_reason="",
            wait_started_at=None,
            wait_elapsed_seconds=None,
            wait_next_tick_at=None,
            wait_target_at=None,
            wait_tick_lag_seconds=None,
            wait_max_tick_lag_seconds=None,
            wait_keep_awake=None,
        )

    def _loop_or_complete(self, message: str) -> None:
        self._begin_cycle(message)

    def _emit(self, progress: AutoTidRngProgress) -> None:
        if self.log_callback is not None and progress.log_message:
            self.log_callback(progress.log_message)
        if self.progress_callback is not None:
            self.progress_callback(progress)

    def _read_script(self, path: Path | None, label: str) -> tuple[str, Path]:
        if path is None:
            raise RuntimeError(f"{label}未配置")
        return path.read_text(encoding="utf-8"), path

    def _run_seed_script(self) -> None:
        try:
            text, path = self._read_script(self.config.seed_script_path, "测种脚本")
            if self._completed_loops > 1 and self.services.recover_zoom_mode is not None:
                recovered = self.services.recover_zoom_mode()
                if self.should_stop():
                    return
                if recovered:
                    self._set_progress(
                        AutoTidRngPhase.RUN_SEED_SCRIPT,
                        "检测到缩放模式，已执行双 HOME 恢复",
                        loop_index=self._completed_loops,
                    )
            if self.should_stop():
                return
            self.services.run_script_text(text, path.name)
        except Exception as exc:
            self._fail(str(exc))
            return
        if self.should_stop():
            return
        self._set_progress(AutoTidRngPhase.CAPTURE_TIDSID, f"测种脚本完成——{path.name}", last_script_path=path)

    def _capture_tidsid(self) -> None:
        try:
            seed = self.services.capture_seed()
        except Exception as exc:
            detail = self._exception_chain_text(exc)
            self._loop_or_complete(
                f"TID/SID 测种失败: {detail}；捕获服务将重新运行测种脚本"
            )
            return
        if seed.measured_at is None:
            seed = replace(seed, measured_at=self._read_monotonic())
        seed_text = self._seed_text_for_log(seed)
        if seed.seed_text != seed_text and seed_text != "-":
            seed = replace(seed, seed_text=seed_text)
        self._seed_result = seed
        self._set_progress(
            AutoTidRngPhase.SEARCH_TARGET,
            (
                "TID/SID 测种完成，搜索目标 TID"
                f"；Seed={seed_text}"
                f"；起点Adv={int(seed.current_advances)}"
                f"；计时起点(monotonic)={self._seconds_text(seed.measured_at)}"
            ),
            seed_text=seed.seed_text,
            current_advances=seed.current_advances,
        )

    def _search_target(self) -> None:
        seed = self._require_seed()
        target_display_tids = self._target_display_tids()
        states = list(self.services.search_id_states(seed, self.config.frame_threshold, target_display_tids))
        target = select_target_display_tid(states, target_display_tids, frame_threshold=self.config.frame_threshold)
        if target is None:
            message = f"阈值 {self.config.frame_threshold} 帧内未命中目标 Display TID，重新运行测种脚本"
            self._set_progress(
                AutoTidRngPhase.SEARCH_TARGET,
                message,
                current_advances=seed.current_advances,
                id_states=tuple(states),
            )
            self._set_progress(
                AutoTidRngPhase.SEARCH_TARGET,
                (
                    f"本轮搜索详情：候选数={len(states)}；"
                    f"搜索范围=0..{max(0, int(self.config.frame_threshold))}；"
                    f"目标DisplayTID={','.join(f'{value:06d}' for value in target_display_tids) or '-'}；"
                    f"Seed={self._seed_text_for_log(seed)}；当前Adv={int(seed.current_advances)}"
                ),
                current_advances=seed.current_advances,
                id_states=tuple(states),
            )
            self._loop_or_complete("")
            return
        trigger = target.advances - int(self.config.delay)
        self._target = target
        if trigger < 0:
            self._loop_or_complete(
                f"目标 Display TID {target.display_tid:06d} @ Adv {target.advances} 小于 delay {self.config.delay}，重新测种"
            )
            return
        self._set_progress(
            AutoTidRngPhase.WAIT_NAME_TRIGGER,
            (
                f"命中目标 Display TID {target.display_tid:06d} @ Adv {target.advances}，"
                f"取名脚本触发帧 {trigger}；delay={int(self.config.delay)}；"
                f"需等待Adv={max(0, int(trigger) - int(seed.current_advances))}"
            ),
            target_tid=target.tid,
            target_sid=target.sid,
            target_display_tid=target.display_tid,
            target_advances=target.advances,
            trigger_advances=trigger,
            current_advances=seed.current_advances,
            id_states=tuple(states),
        )

    def _run_name_script(self) -> None:
        try:
            text, path = self._read_script(self.config.name_script_path, "取名脚本")
            self.services.run_script_text(text, path.name)
        except Exception as exc:
            self._fail(str(exc))
            return
        self._set_progress(AutoTidRngPhase.COMPLETED, f"取名脚本完成——{path.name}", last_script_path=path)

    def _wait_name_trigger(self) -> None:
        seed = self._require_seed()
        trigger = self.progress.trigger_advances
        if trigger is None:
            self._fail("取名脚本触发帧尚未计算")
            return
        if trigger < int(seed.current_advances):
            self._loop_or_complete(f"已超过取名脚本触发帧 {trigger}，当前帧 {seed.current_advances}，重新测种")
            return
        self._begin_wait(
            phase=AutoTidRngPhase.WAIT_NAME_TRIGGER,
            seed=seed,
            trigger=int(trigger),
            label="取名帧",
        )

        def on_frame(live_current: int) -> None:
            updates = self._wait_progress_updates(
                now=self._read_monotonic(),
                current_advances=live_current,
                target_advances=int(trigger),
            )
            milestone = (int(live_current) // _WAIT_PROGRESS_LOG_ADVANCES) * _WAIT_PROGRESS_LOG_ADVANCES
            previous_milestone = (
                (int(self._wait_last_logged_advances) // _WAIT_PROGRESS_LOG_ADVANCES) * _WAIT_PROGRESS_LOG_ADVANCES
                if self._wait_last_logged_advances is not None
                else 0
            )
            message = ""
            if milestone > 0 and milestone > previous_milestone:
                self._wait_last_logged_advances = milestone
                message = self._format_wait_timing_message(
                    "取名帧等待进度",
                    updates,
                )
            self._set_progress(AutoTidRngPhase.WAIT_NAME_TRIGGER, message, **updates)

        self._advance_counter.run_until(
            trigger,
            monotonic=self.services.monotonic,
            sleep=self.services.sleep,
            should_stop=self.should_stop,
            on_frame=on_frame,
        )
        if self._stop_requested:
            return
        updates = self._wait_progress_updates(
            now=self._read_monotonic(),
            current_advances=int(self._advance_counter.current_advances),
            target_advances=int(trigger),
        )
        actual_current = int(updates.get("current_advances", self._advance_counter.current_advances))
        if actual_current != int(trigger):
            prefix = (
                f"到达取名脚本触发帧 {trigger} 时实际Adv={actual_current}"
                f"（超出{max(0, actual_current - int(trigger))}）"
            )
        else:
            prefix = f"到达取名脚本触发帧 {trigger}"
        self._set_progress(
            AutoTidRngPhase.RUN_NAME_SCRIPT,
            self._format_wait_timing_message(
                f"{prefix}，运行取名脚本",
                updates,
            ),
            **updates,
        )
        # The schedule is complete.  Keep its values in ``progress`` for
        # later diagnostics, but stop treating it as an active wait when a
        # subsequent script is running.
        self._wait_phase = None

    def _wait_reverse_trigger(self) -> None:
        seed = self._require_seed()
        trigger = self.progress.trigger_advances
        if trigger is None:
            self._fail("反查 ID 启动帧尚未计算")
            return
        if trigger < int(seed.current_advances):
            self._fail(f"已超过反查 ID 启动帧 {trigger}，当前帧 {seed.current_advances}")
            return
        self._begin_wait(
            phase=AutoTidRngPhase.WAIT_REVERSE_TRIGGER,
            seed=seed,
            trigger=int(trigger),
            label="反查帧",
        )

        def on_frame(live_current: int) -> None:
            updates = self._wait_progress_updates(
                now=self._read_monotonic(),
                current_advances=live_current,
                target_advances=int(trigger),
            )
            milestone = (int(live_current) // _WAIT_PROGRESS_LOG_ADVANCES) * _WAIT_PROGRESS_LOG_ADVANCES
            previous_milestone = (
                (int(self._wait_last_logged_advances) // _WAIT_PROGRESS_LOG_ADVANCES) * _WAIT_PROGRESS_LOG_ADVANCES
                if self._wait_last_logged_advances is not None
                else 0
            )
            message = ""
            if milestone > 0 and milestone > previous_milestone:
                self._wait_last_logged_advances = milestone
                message = self._format_wait_timing_message(
                    "反查帧等待进度",
                    updates,
                )
            self._set_progress(AutoTidRngPhase.WAIT_REVERSE_TRIGGER, message, **updates)

        self._advance_counter.run_until(
            trigger,
            monotonic=self.services.monotonic,
            sleep=self.services.sleep,
            should_stop=self.should_stop,
            on_frame=on_frame,
        )
        if self._stop_requested:
            return
        updates = self._wait_progress_updates(
            now=self._read_monotonic(),
            current_advances=int(self._advance_counter.current_advances),
            target_advances=int(trigger),
        )
        actual_current = int(updates.get("current_advances", self._advance_counter.current_advances))
        if actual_current != int(trigger):
            prefix = (
                f"到达反查 ID 启动帧 {trigger} 时实际Adv={actual_current}"
                f"（超出{max(0, actual_current - int(trigger))}）"
            )
        else:
            prefix = f"到达反查 ID 启动帧 {trigger}"
        self._set_progress(
            AutoTidRngPhase.RUN_REVERSE_ID_SCRIPT,
            self._format_wait_timing_message(
                f"{prefix}，运行反查 ID 脚本",
                updates,
            ),
            **updates,
        )
        self._wait_phase = None

    def _run_reverse_id_script(self) -> None:
        try:
            text, path = self._read_script(self.config.reverse_id_script_path, "反查 ID 脚本")
            self.services.run_script_text(text, path.name)
        except Exception as exc:
            self._fail(str(exc))
            return
        self._set_progress(AutoTidRngPhase.OCR_TID, f"反查 ID 脚本完成——{path.name}", last_script_path=path)

    def _ocr_tid(self) -> None:
        try:
            text = self.services.recognize_tid()
        except Exception as exc:
            self._fail(f"OCR 失败: {exc}")
            return
        tid = parse_tid_text(text)
        if tid is None:
            self._set_progress(AutoTidRngPhase.FAILED, f"OCR 未识别到有效 TID：{text}", ocr_text=text)
            return
        self._set_progress(AutoTidRngPhase.CALIBRATE_DELAY, f"OCR TID={tid}", ocr_text=text, ocr_tid=tid)

    def _calibrate_delay(self) -> None:
        seed = self._require_seed()
        target = self._require_target()
        trigger = self.progress.trigger_advances
        tid = self.progress.ocr_tid
        if trigger is None or tid is None:
            self._fail("缺少 delay 校准所需的启动帧或 OCR TID")
            return
        state = self.services.lookup_tid_state(seed, tid, target.advances, self.config.reverse_lookup_window)
        if state is None:
            self._set_progress(
                AutoTidRngPhase.FAILED,
                f"ID 数据中未找到 OCR TID {tid}（反查范围 ±{self.config.reverse_lookup_window}）",
            )
            return
        actual_delay = int(state.advances) - int(trigger)
        self._set_progress(
            AutoTidRngPhase.COMPLETED,
            f"delay 校准完成：实际 delay={actual_delay}",
            ocr_advances=int(state.advances),
            actual_delay=actual_delay,
        )

    def _fail(self, message: str) -> None:
        self._set_progress(AutoTidRngPhase.FAILED, message)

    def _require_seed(self) -> AutoTidSeedResult:
        if self._seed_result is None:
            raise RuntimeError("TID/SID seed 尚未捕获")
        return self._seed_result

    def _require_target(self) -> AutoTidTarget:
        if self._target is None:
            raise RuntimeError("目标 TID 尚未锁定")
        return self._target

    def _target_display_tids(self) -> tuple[int, ...]:
        if self.config.target_display_tids:
            return tuple(int(value) for value in self.config.target_display_tids)
        return tuple(int(value) for value in self.config.target_tids)

    def _set_progress(self, phase: AutoTidRngPhase, message: str = "", **updates: object) -> None:
        if self._stop_requested and phase != AutoTidRngPhase.IDLE:
            return
        values = {
            "phase": phase,
            "loop_index": updates.get("loop_index", self.progress.loop_index),
            "seed_text": updates.get("seed_text", self.progress.seed_text),
            "current_advances": updates.get("current_advances", self.progress.current_advances),
            "target_tid": updates.get("target_tid", self.progress.target_tid),
            "target_sid": updates.get("target_sid", self.progress.target_sid),
            "target_display_tid": updates.get("target_display_tid", self.progress.target_display_tid),
            "target_advances": updates.get("target_advances", self.progress.target_advances),
            "trigger_advances": updates.get("trigger_advances", self.progress.trigger_advances),
            "remaining_to_trigger": updates.get("remaining_to_trigger", self.progress.remaining_to_trigger),
            "ocr_text": updates.get("ocr_text", self.progress.ocr_text),
            "ocr_tid": updates.get("ocr_tid", self.progress.ocr_tid),
            "ocr_advances": updates.get("ocr_advances", self.progress.ocr_advances),
            "actual_delay": updates.get("actual_delay", self.progress.actual_delay),
            "id_states": updates.get("id_states", self.progress.id_states),
            "last_script_path": updates.get("last_script_path", self.progress.last_script_path),
            "log_message": message,
            "stop_reason": updates.get("stop_reason", self.progress.stop_reason),
            "wait_started_at": updates.get("wait_started_at", self.progress.wait_started_at),
            "wait_elapsed_seconds": updates.get("wait_elapsed_seconds", self.progress.wait_elapsed_seconds),
            "wait_next_tick_at": updates.get("wait_next_tick_at", self.progress.wait_next_tick_at),
            "wait_target_at": updates.get("wait_target_at", self.progress.wait_target_at),
            "wait_tick_lag_seconds": updates.get("wait_tick_lag_seconds", self.progress.wait_tick_lag_seconds),
            "wait_max_tick_lag_seconds": updates.get(
                "wait_max_tick_lag_seconds", self.progress.wait_max_tick_lag_seconds
            ),
            "wait_keep_awake": updates.get("wait_keep_awake", self.progress.wait_keep_awake),
        }
        self.progress = replace(self.progress, **values)
        self._emit(self.progress)
