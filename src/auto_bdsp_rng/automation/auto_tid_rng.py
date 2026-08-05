from __future__ import annotations

import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any

from auto_bdsp_rng.automation.auto_rng.ocr_regions import OcrRegion
from auto_bdsp_rng.gen8_id import IDFilter, IDState8, generate_ids
from auto_bdsp_rng.rng_core import BDSPXorshift, SeedPair64, SeedState32


@dataclass
class ProjectXsMunchlaxAdvanceCounter:
    """Project_Xs TID/SID counter: one advance at each Munchlax blink interval."""

    current_advances: int = 0
    next_tick_at: float = 0.0
    _rng: BDSPXorshift | None = None

    def reset(self, *, current_advances: int, seed: SeedPair64 | SeedState32 | Any, now: float) -> None:
        self.current_advances = int(current_advances)
        self._rng = BDSPXorshift(_seed_state_from_value(seed))
        self.next_tick_at = float(now) + self._next_interval()

    def _rangefloat(self, minimum: float, maximum: float) -> float:
        if self._rng is None:
            raise RuntimeError("Munchlax counter has not been reset")
        temp = (self._rng.next() & 0x7FFFFF) / 8388607.0
        return temp * minimum + (1.0 - temp) * maximum

    def _next_interval(self) -> float:
        return self._rangefloat(3.0, 12.0) + 0.285

    def advance_one_blink(self) -> int:
        if self._rng is None:
            raise RuntimeError("Munchlax counter has not been reset")
        self.current_advances += 1
        self.next_tick_at += self._next_interval()
        return self.current_advances

    def advance_to(self, now: float) -> int:
        advanced = 0
        while float(now) + 1e-9 >= self.next_tick_at:
            self.advance_one_blink()
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
        self._seed_result: AutoTidSeedResult | None = None
        self._target: AutoTidTarget | None = None
        self._completed_loops = 0
        self._advance_counter = ProjectXsMunchlaxAdvanceCounter()

    def stop(self) -> None:
        self._stop_requested = True
        if self.services.stop_current_script is not None:
            self.services.stop_current_script()
        self._set_progress(AutoTidRngPhase.IDLE, "已请求停止自动 TID 乱数")

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

    def _begin_cycle(self, message: str, *, phase: AutoTidRngPhase = AutoTidRngPhase.RUN_SEED_SCRIPT) -> None:
        self._completed_loops += 1
        self._target = None
        self._set_progress(phase, message, loop_index=self._completed_loops)

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
            self._loop_or_complete(f"TID/SID 测种失败: {exc}，重新运行测种脚本")
            return
        if seed.measured_at is None:
            seed = replace(seed, measured_at=self.services.monotonic())
        self._seed_result = seed
        self._set_progress(
            AutoTidRngPhase.SEARCH_TARGET,
            "TID/SID 测种完成，搜索目标 TID",
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
            self._loop_or_complete(message)
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
            f"命中目标 Display TID {target.display_tid:06d} @ Adv {target.advances}，取名脚本触发帧 {trigger}",
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
        self._advance_counter.reset(
            current_advances=int(seed.current_advances),
            seed=seed.seed,
            now=float(seed.measured_at if seed.measured_at is not None else self.services.monotonic()),
        )

        def on_frame(live_current: int) -> None:
            self._set_progress(
                AutoTidRngPhase.WAIT_NAME_TRIGGER,
                "",
                current_advances=live_current,
                remaining_to_trigger=max(0, trigger - live_current),
            )

        self._advance_counter.run_until(
            trigger,
            monotonic=self.services.monotonic,
            sleep=self.services.sleep,
            should_stop=self.should_stop,
            on_frame=on_frame,
        )
        if self._stop_requested:
            return
        self._set_progress(
            AutoTidRngPhase.RUN_NAME_SCRIPT,
            f"到达取名脚本触发帧 {trigger}，运行取名脚本",
            current_advances=trigger,
            remaining_to_trigger=0,
        )

    def _wait_reverse_trigger(self) -> None:
        seed = self._require_seed()
        trigger = self.progress.trigger_advances
        if trigger is None:
            self._fail("反查 ID 启动帧尚未计算")
            return
        if trigger < int(seed.current_advances):
            self._fail(f"已超过反查 ID 启动帧 {trigger}，当前帧 {seed.current_advances}")
            return
        self._advance_counter.reset(
            current_advances=int(seed.current_advances),
            npc=int(seed.npc),
            now=float(seed.measured_at if seed.measured_at is not None else self.services.monotonic()),
        )

        def on_frame(live_current: int) -> None:
            self._set_progress(
                AutoTidRngPhase.WAIT_REVERSE_TRIGGER,
                "",
                current_advances=live_current,
                remaining_to_trigger=max(0, trigger - live_current),
            )

        self._advance_counter.run_until(
            trigger,
            monotonic=self.services.monotonic,
            sleep=self.services.sleep,
            should_stop=self.should_stop,
            on_frame=on_frame,
        )
        if self._stop_requested:
            return
        self._set_progress(
            AutoTidRngPhase.RUN_REVERSE_ID_SCRIPT,
            f"到达反查 ID 启动帧 {trigger}，运行反查 ID 脚本",
            current_advances=trigger,
            remaining_to_trigger=0,
        )

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
        }
        self.progress = replace(self.progress, **values)
        self._emit(self.progress)
