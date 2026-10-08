"""Recorded dialogue sequences with OCR anchors and the Project_Xs clock.

Only the observer thread performs OCR. The main thread owns the RNG clock and
dispatches both countdown presses without waiting for an OCR response.
"""
from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable

TIMELINE_BUFFER = 200
STARTER_TIMING = {"threshold": 0.7, "timeDelay": 0.0, "advanceDelay": 41,
                  "advanceDelay2": 48, "npc": 1, "timelineNpc": -1,
                  "pokemonNpc": 2, "noisy": False}
STARTER_SLOTS = {387: 0, 390: 1, 393: 2}
SELECTION_PRESS_MS = 50  # Match bare A/UP clicks in the user's verified ECS.
SELECTION_MENU_WAIT_MS = 800
SELECTION_CONFIRM_WAIT_MS = 100
# Successful A-down intervals from 11.mp4's frame PTS, rounded up to 10 ms.
# Eight ordinary presses reach the doctor; the last two sequences each add one
# ordinary press after a clock-controlled A. These waits include scene changes.
DIALOG_PRESS_MS = 200  # Recording holds span 133-200 ms, at 33 ms resolution.
DOCTOR_PRESS_INTERVALS_MS = (3540, 1870, 900, 870, 670, 5000, 1140)
SECOND_PRESS_INTERVALS_MS = (3470,)
BALL_PRESS_INTERVALS_MS = (4840,)
# Eleven tracking ticks, the initial transition, eleven timeline events, and
# the second transition must finish before the final selection can begin.
TRANSITION_ADVANCES = 11 * (STARTER_TIMING["npc"] + 1) + STARTER_TIMING["advanceDelay"] + 11 + STARTER_TIMING["advanceDelay2"]


class StarterTargetMissed(RuntimeError):
    """No selection was dispatched; this dialogue must be restarted."""


def validate_starter_delay(delay: int) -> None:
    maximum = TIMELINE_BUFFER - TRANSITION_ADVANCES
    if delay < 0 or delay >= maximum:
        raise ValueError(f"御三家全自动本轮 delay 为 {delay} 帧，固定 200 帧窗口要求 delay 小于 {maximum} 帧；请检查该御三家的 delay 策略")


def normalize_dialog(text: object) -> str:
    # An ellipsis-only page in the recording still needs one A press.
    return re.sub(r"[^\w….]+", "", str(text))


def crop_starter_dialog(image):
    """Find the actual white dialogue body, excluding the speaker and scenery."""
    import cv2
    import numpy as np
    height, width = image.shape[:2]
    top = int(height * .66)
    lower = image[top:]
    hsv = cv2.cvtColor(lower, cv2.COLOR_BGR2HSV)
    white = ((hsv[:, :, 2] > 205) & (hsv[:, :, 1] < 70)).astype('uint8') * 255
    contours, _ = cv2.findContours(white, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []
    for contour in contours:
        x,y,w,h = cv2.boundingRect(contour)
        if w >= width*.5 and height*.045 <= h <= height*.20 and cv2.contourArea(contour) >= width*height*.02:
            candidates.append((w*h,x,top+y,w,h))
    if not candidates:
        return None
    _,x,y,w,h = max(candidates)
    return image[y:y+h,x:x+w]


def is_start_dialog(text: str) -> bool:
    return ("怎么回事" in text or "怎麼回事" in text) and ("刚才那两人" in text or "剛才那兩人" in text)


def is_doctor_dialog(text: str) -> bool:
    return ("想还" in text or "想還" in text) and "博士" in text


def is_second_dialog(text: str) -> bool:
    return "搞什么" in text or "搞什麼" in text


def is_ball_dialog(text: str) -> bool:
    return "要选哪" in text or "要選哪" in text


def selection_script(species: int) -> str:
    if species not in STARTER_SLOTS:
        raise ValueError("御三家全自动目标无效")
    # The cursor is already positioned; the confirmation defaults to No.
    return (f"A {SELECTION_PRESS_MS}\nWAIT {SELECTION_MENU_WAIT_MS}\n"
            f"UP {SELECTION_PRESS_MS}\nWAIT {SELECTION_CONFIRM_WAIT_MS}\n"
            f"A {SELECTION_PRESS_MS}\n")


class DialogObserver:
    """Publish complete, repeated OCR observations without blocking the clock."""
    def __init__(self, read_text: Callable[[], str], should_stop: Callable[[], bool]):
        self.read_text, self.should_stop = read_text, should_stop
        self.stopped = threading.Event()
        self.lock = threading.Lock()
        self.text: str | None = None
        self.observed_at = 0.0
        self.error: Exception | None = None
        self.enabled = True
        self.generation = 0
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def close(self) -> None:
        # Do not add a join delay immediately before the final controller press.
        self.stopped.set()

    def set_enabled(self, enabled: bool) -> None:
        """Start a fresh anchor check, or pause OCR during keys/countdowns."""
        with self.lock:
            self.generation += 1
            self.enabled = enabled
            self.text, self.observed_at = None, 0.0

    def latest(self) -> str | None:
        with self.lock:
            if self.error is not None:
                raise RuntimeError(f"御三家对话识别失败: {self.error}") from self.error
            if time.monotonic() - self.observed_at > 2.0:
                return None
            return self.text

    def _run(self) -> None:
        previous, repeated, previous_generation = None, 0, None
        while not self.stopped.is_set() and not self.should_stop():
            with self.lock:
                enabled, generation = self.enabled, self.generation
            if not enabled:
                self.stopped.wait(0.1)
                continue
            try:
                current = normalize_dialog(self.read_text())
            except Exception as error:
                with self.lock:
                    if generation != self.generation or not self.enabled or self.stopped.is_set() or self.should_stop():
                        continue
                    self.error = error
                return
            if self.stopped.is_set() or self.should_stop():
                return
            with self.lock:
                if generation != self.generation or not self.enabled:
                    continue  # An in-flight read belongs to the old anchor.
                if previous_generation != generation:
                    previous, repeated = None, 0
                repeated = repeated + 1 if current == previous else 1
                previous, previous_generation = current, generation
                self.text = current if repeated >= 2 else None
                self.observed_at = time.monotonic()
            self.stopped.wait(0.1)


class StarterFlow:
    def __init__(self, seed, target, delay: int, species: int, blink: dict, *,
                 latest_text: Callable[[], str | None], run_script: Callable[[str, str], object],
                 position_cursor: Callable[[], object],
                 progress: Callable[[str, str, int], None] = lambda *_: None,
                 monotonic: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep,
                 should_stop: Callable[[], bool] = lambda: False,
                 tracker=None, on_balls: Callable[[], None] = lambda: None,
                 observe_dialog: Callable[[bool], None] = lambda *_: None,
                 on_press: Callable[[dict], None] = lambda *_: None):
        validate_starter_delay(delay)
        self.delay, self.species = delay, species
        self.anchor = target.raw_target_advances - TIMELINE_BUFFER
        self.select_at = target.raw_target_advances - delay
        self.latest_text, self.run_script, self.progress = latest_text, run_script, progress
        self.position_cursor = position_cursor
        self.monotonic, self.sleep, self.should_stop, self.on_balls = monotonic, sleep, should_stop, on_balls
        self.observe_dialog, self.on_press = observe_dialog, on_press
        self.next_dialog_at: float | None = None
        self.pending_intervals = iter(())
        self.last_press_at: float | None = None
        self.last_completed_at = 0.0
        self.last_hold_ms = 0
        self.started_at = 0.0
        if tracker is None:
            from auto_bdsp_rng.blink_detection.project_xs import _load_module
            from .starter_clock import BlinkTracking
            Xorshift = _load_module("xorshift").Xorshift
            config = {**blink, **STARTER_TIMING, "mode": "recover"}
            measured = seed.measured_at if seed.measured_at is not None else monotonic()
            tracker = BlinkTracking(Xorshift(*seed.seed.words), config,
                                    seed.current_advances - int(config.get("menuClose", True)), measured, measured)
        self.tracker = tracker

    def _press(self, duration_ms: int, name: str, scheduled_at: float) -> float:
        started = self.monotonic()
        self.run_script(f"A {duration_ms}\n", name)
        completed = self.monotonic()
        self.on_press({"name": name, "hold_ms": duration_ms,
                       "elapsed_ms": round((started-self.started_at)*1000, 3),
                       "since_previous_press_ms": None if self.last_press_at is None else round((started-self.last_press_at)*1000, 3),
                       "late_ms": round(max(0.0, started-scheduled_at)*1000, 3),
                       "controller_request_ms": round((completed-started)*1000, 3)})
        self.last_press_at = started
        self.last_completed_at, self.last_hold_ms = completed, duration_ms
        return started

    def _dialog_deadline(self, after: float, interval_ms: int) -> float:
        # A slow controller round trip must not consume the release gap.
        return max(after + interval_ms/1000,
                   self.last_completed_at + max(0, interval_ms-self.last_hold_ms)/1000)

    def _queue_dialogue(self, intervals: tuple[int, ...], after: float) -> None:
        self.observe_dialog(False)
        self.pending_intervals = iter(intervals)
        self.next_dialog_at = self._dialog_deadline(after, next(self.pending_intervals))

    def _advance_dialogue(self, now: float, name: str) -> None:
        if self.next_dialog_at is None or now < self.next_dialog_at:
            return
        started = self._press(DIALOG_PRESS_MS, name, self.next_dialog_at)
        interval = next(self.pending_intervals, None)
        # A late worker never sends a burst to catch up with old deadlines.
        self.next_dialog_at = None if interval is None else self._dialog_deadline(started, interval)
        if self.next_dialog_at is None:
            self.observe_dialog(True)

    def run(self) -> str:
        stage, entered_at = "start", self.monotonic()
        self.started_at = entered_at
        self.observe_dialog(True)
        reported_at = float("-inf")
        labels = {"start": "确认起点对话", "doctor": "自动推进到博士对话",
                  "anchor": "博士对话等待目标前 200 帧", "first_countdown": "Timeline 第一段倒计时",
                  "second": "自动推进到搞什么啊对话", "second_countdown": "Timeline 第二段倒计时",
                  "balls": "自动推进到精灵球界面", "position": "提前移动到目标精灵球",
                  "selection": "光标已定位，等待本轮 delay 触发"}
        reported_stage = None
        while True:
            if self.should_stop():
                raise RuntimeError("御三家自动接管已停止")
            now = self.monotonic()
            tracking = self.tracker.update(now)
            current = tracking["advances"]
            timeline = self.tracker.timeline
            text = (self.latest_text() or "") if stage in ("start", "doctor", "second", "balls") and self.next_dialog_at is None else ""
            if stage != reported_stage or now - reported_at >= 0.2:
                self.progress(stage, labels[stage], current)
                reported_stage, reported_at = stage, now
            if current > self.select_at:
                raise StarterTargetMissed(f"御三家已错过选择帧 {self.select_at}（当前 {current}），本轮不选精灵")
            if stage in ("start", "doctor", "anchor") and current > self.anchor:
                raise StarterTargetMissed(f"御三家未及时进入博士对话，错过固定启动帧 {self.anchor}")
            if stage == "start":
                if is_start_dialog(text):
                    self.observe_dialog(False)
                    started = self._press(DIALOG_PRESS_MS, "御三家·起点对话", now)
                    self._queue_dialogue(DOCTOR_PRESS_INTERVALS_MS, started)
                    stage = "doctor"
            elif stage == "doctor":
                if self.next_dialog_at is not None:
                    self._advance_dialogue(now, "御三家·固定推进博士对话")
                elif is_doctor_dialog(text):
                    self.observe_dialog(False)
                    stage = "anchor"
                elif is_second_dialog(text) or is_ball_dialog(text):
                    raise RuntimeError("御三家起点之后未识别到博士对话，已停止")
            elif stage == "anchor":
                if current == self.anchor:
                    if not self.tracker.request_timeline():
                        raise RuntimeError("御三家 Timeline 无法启动")
                    stage = "first_countdown"
            elif stage == "first_countdown":
                zero_at = self.tracker.countdown_zero_at
                if zero_at is not None:
                    if self.monotonic() - zero_at > 0.15:
                        raise StarterTargetMissed("御三家第一段倒计时按键超时，本轮不选精灵")
                    started = self._press(100, "御三家·第一段归零", zero_at)
                    self._queue_dialogue(SECOND_PRESS_INTERVALS_MS, started)
                    stage = "second"
            elif stage == "second":
                if is_ball_dialog(text) or "是精灵球" in text or "是精靈球" in text:
                    raise StarterTargetMissed("未确认第二段对话就进入精灵球界面，本轮不选精灵")
                if timeline is not None and timeline.delay2_zero_at is not None:
                    raise StarterTargetMissed("第二段 Timer 归零前未进入搞什么啊对话，本轮不选精灵")
                if self.next_dialog_at is not None:
                    self._advance_dialogue(now, "御三家·固定推进遭遇对话")
                elif is_second_dialog(text):
                    self.observe_dialog(False)
                    stage = "second_countdown"
            elif stage == "second_countdown":
                zero_at = timeline.delay2_zero_at if timeline is not None else None
                if zero_at is not None:
                    if self.monotonic() - zero_at > 0.15:
                        raise StarterTargetMissed("御三家第二段倒计时按键超时，本轮不选精灵")
                    started = self._press(100, "御三家·第二段归零", zero_at)
                    self._queue_dialogue(BALL_PRESS_INTERVALS_MS, started)
                    stage = "balls"
            elif stage == "balls":
                if self.next_dialog_at is not None:
                    self._advance_dialogue(now, "御三家·固定推进精灵球对话")
                elif is_ball_dialog(text):
                    self.observe_dialog(False)
                    self.on_balls()
                    stage = "position"
            elif stage == "position":
                # Progress callbacks and controller requests can consume time;
                # refresh the RNG clock on both sides of cursor positioning.
                if self.should_stop():
                    raise RuntimeError("御三家自动接管已停止")
                if self.tracker.update(self.monotonic())["advances"] >= self.select_at:
                    raise StarterTargetMissed("到达选择帧时尚未开始光标定位，本轮不选精灵")
                self.position_cursor()
                if self.should_stop():
                    raise RuntimeError("御三家自动接管已停止")
                if self.tracker.update(self.monotonic())["advances"] >= self.select_at:
                    raise StarterTargetMissed("光标定位未在选择帧前完成，本轮不选精灵")
                stage = "selection"
            elif stage == "selection" and current == self.select_at:
                self.progress("confirm", f"到达选择帧 {self.select_at}，使用本轮 delay {self.delay} 帧", current)
                return selection_script(self.species)
            if stage != reported_stage:
                entered_at = now
            if stage in ("start", "doctor", "second", "balls") and now-entered_at > 45:
                raise RuntimeError(f"御三家对话识别超时：{labels[stage]}")
            if current == self.select_at and stage != "selection":
                raise StarterTargetMissed("到达选择帧时尚未完成精灵球界面准备，本轮不选精灵")
            next_at = self.tracker.next_at
            if timeline is not None:
                next_at = timeline.queue[0][0] if timeline.queue else timeline.starts_at
            if self.next_dialog_at is not None:
                next_at = min(next_at, self.next_dialog_at)
            self.sleep(max(0.001, min(0.02, next_at-self.monotonic())))
