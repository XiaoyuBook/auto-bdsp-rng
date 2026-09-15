"""Cooperative manual-script pause, with interruptible WAIT durations."""

from __future__ import annotations

from math import ceil
from threading import Condition, Event
from time import monotonic
from typing import Callable

from .errors import ScriptCancelled


class _WaitPaused(Exception):
    pass


class _WaitSignal:
    def __init__(self, control: PauseControl) -> None:
        self.control = control

    def is_set(self) -> bool:
        if self.control.cancel.is_set():
            return True
        if self.control.requested.is_set():
            raise _WaitPaused()
        return False

    def wait(self, timeout: float | None = None) -> bool:
        self.control.wake.wait(timeout)
        return self.is_set()


class PauseControl:
    def __init__(self, cancel: Event, state_changed: Callable[[str], None], on_pause: Callable, on_resume: Callable) -> None:
        self.cancel = cancel
        self.requested = Event()
        self.wake = Event()
        self.condition = Condition()
        self.paused = False
        self.finished = False
        self.replace_program: Callable | None = None
        self._state_changed = state_changed
        self._on_pause = on_pause
        self._on_resume = on_resume

    def request_pause(self) -> bool:
        with self.condition:
            if self.finished or self.cancel.is_set():
                return False
            if not self.requested.is_set():
                self.requested.set()
                self._state_changed("pausing")
                self.wake.set()
            return True

    def checkpoint(self) -> None:
        if not self.requested.is_set():
            return
        with self.condition:
            if self.cancel.is_set():
                raise ScriptCancelled("脚本已取消")
            held_inputs = self._on_pause()
            self.paused = True
            self._state_changed("paused")
            try:
                while self.requested.is_set() and not self.cancel.is_set():
                    self.condition.wait()
                if self.cancel.is_set():
                    raise ScriptCancelled("脚本已取消")
                self._on_resume(held_inputs)
                self._state_changed("running")
            finally:
                self.paused = False

    def resume(self, apply: Callable[[], None]) -> None:
        with self.condition:
            if not self.paused or not self.requested.is_set() or self.cancel.is_set() or self.finished:
                raise RuntimeError("脚本尚未暂停或已停止")
            apply()
            self.requested.clear()
            self.wake.clear()
            self._state_changed("resuming")
            self.condition.notify_all()

    def stop(self) -> None:
        with self.condition:
            self.cancel.set()
            self.wake.set()
            self.condition.notify_all()

    def finish(self) -> None:
        with self.condition:
            self.finished = True
            self.condition.notify_all()

    def wait(self, waiter, milliseconds: int, remaining_changed: Callable[[int], None]) -> None:
        remaining = milliseconds / 1000
        duration = milliseconds
        signal = _WaitSignal(self)
        while True:
            started = monotonic()
            try:
                method = getattr(waiter, "wait", waiter)
                method(duration, signal)
                return
            except _WaitPaused:
                remaining = max(0, remaining - (monotonic() - started))
                duration = ceil(remaining * 1000)
                remaining_changed(duration)
                self.checkpoint()
                remaining_changed(duration)
