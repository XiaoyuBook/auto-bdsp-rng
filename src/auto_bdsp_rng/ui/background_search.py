"""One cancellable worker with a replaceable pending search per UI owner."""
from __future__ import annotations

import threading
from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer

from auto_bdsp_rng.rng_core.cancellation import SearchCancellation, SearchCancelled


class BackgroundSearch(QObject):
    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self._lock = threading.Lock()
        self._generation = 0
        self._pending = None
        self._active_token: SearchCancellation | None = None
        self._completed = None
        self._callbacks = None
        self._closed = False
        self.worker_thread: threading.Thread | None = None
        self.timer = QTimer(self)
        self.timer.setInterval(20)
        self.timer.timeout.connect(self.poll)

    def submit(self, work: Callable, on_result: Callable, on_error: Callable) -> None:
        with self._lock:
            if self._closed:
                return
            self._generation += 1
            if self._active_token is not None:
                self._active_token.cancel()
            self._pending = (self._generation, work)
            self._completed = None
            self._callbacks = (on_result, on_error)
            if self.worker_thread is None or not self.worker_thread.is_alive():
                self.worker_thread = threading.Thread(target=self._run, name="rng-search", daemon=True)
                self.worker_thread.start()
        self.timer.start()

    def _run(self) -> None:
        while True:
            with self._lock:
                request = self._pending
                self._pending = None
                if request is None or self._closed:
                    # Clear under the lock: a new submission cannot lose its worker
                    # in the interval between the last request and thread return.
                    self.worker_thread = None
                    return
                generation, work = request
                token = self._active_token = SearchCancellation()
            try:
                result, error = work(token), None
                token.check()
            except SearchCancelled:
                result, error = None, None
            except Exception as exc:
                result, error = None, exc
            with self._lock:
                if generation == self._generation and not self._closed:
                    self._completed = (result, error)
                self._active_token = None

    def poll(self) -> None:
        with self._lock:
            completed = self._completed
            self._completed = None
            callbacks = self._callbacks
        if completed is not None and callbacks is not None and not self._closed:
            result, error = completed
            if error is not None:
                callbacks[1](error)
            elif result is not None:
                callbacks[0](result)
        # A result callback may submit the next request (for example applying
        # the current table filter). Do not stop that request's polling timer.
        with self._lock:
            if self.worker_thread is None and self._completed is None:
                self.timer.stop()

    def _invalidate(self, *, closing: bool) -> None:
        with self._lock:
            # Destruction may notify an owner after its explicit close. Avoid
            # touching a Qt timer that has already been destroyed in that case.
            if self._closed:
                return
            self._closed = closing
            self._generation += 1
            self._pending = self._completed = self._callbacks = None
            if self._active_token is not None:
                self._active_token.cancel()
        self.timer.stop()

    def cancel(self) -> None:
        self._invalidate(closing=False)

    def close(self) -> None:
        # Workers touch only Python data, never Qt objects or widgets. Clearing
        # callbacks before destruction prevents delivery after the owner closes.
        self._invalidate(closing=True)
