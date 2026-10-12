"""Optional UI heartbeat diagnostics; file I/O and stack collection stay off Qt.

The Python monitor cannot run while a native extension holds the GIL. A caller
that exclusively owns the process-wide faulthandler timeout may opt into its
native fallback. That fallback dumps once without requiring the GIL; it does
not enable/replace exception hooks, signal handlers, or crash handling.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
import faulthandler
import importlib.metadata
import os
from pathlib import Path
import platform
import re
import sys
import threading
import time
import traceback
from typing import BinaryIO

from auto_bdsp_rng import __version__
from auto_bdsp_rng.run_log import RunLogManager


_FILE_RE = re.compile(r"^ui_stall_(?P<date>\d{4}-\d{2}-\d{2})_pid-\d+\.log$")
_native_owner_lock = threading.RLock()
_native_owner: UiStallWatchdog | None = None


class UiStallWatchdog:
    """Record each stalled heartbeat once, and reset after Qt responds again."""

    def __init__(
        self,
        directory: Path,
        enabled: Callable[[], bool],
        *,
        threshold_seconds: float = 2.0,
        poll_seconds: float = 0.1,
        native_fallback: bool = False,
        report: Callable[[str], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        if threshold_seconds <= 0 or poll_seconds <= 0:
            raise ValueError("watchdog intervals must be greater than zero")
        self.directory = Path(directory)
        self.threshold_seconds = float(threshold_seconds)
        self.poll_seconds = min(float(poll_seconds), self.threshold_seconds / 2)
        self.native_fallback = bool(native_fallback)
        self._enabled_callback, self._report = enabled, report
        self._clock, self._now = clock, now
        self._lock = threading.Lock()
        self._last_heartbeat = self._clock()
        self._generation = 0
        self._reported_generation: int | None = None
        self._armed_generation: int | None = None
        self._armed_size = 0
        self._armed_heartbeat = 0.0
        self._stream: BinaryIO | None = None
        self._stream_date = None
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_path: Path | None = None
        self.last_error: str | None = None

    def heartbeat(self) -> None:
        """Called by Qt: only update a timestamp, without disk or stack work."""
        with self._lock:
            self._last_heartbeat = self._clock()
            self._generation += 1

    def start(self) -> UiStallWatchdog:
        if self._thread is not None:
            return self
        self._thread = threading.Thread(target=self._run, name="UI stall diagnostics", daemon=True)
        self._thread.start()
        return self

    def stop(self, timeout: float = 0.5) -> bool:
        """Stop sampling; a slow filesystem cannot indefinitely block shutdown."""
        self._stop_event.set()
        self._cancel_native()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(max(0.0, timeout))
            return not thread.is_alive()
        if thread is None:
            self._close_stream()
        return True

    def _run(self) -> None:
        try:
            while not self._stop_event.is_set():
                try:
                    self._check_once()
                except Exception as exc:
                    self.last_error = f"卡顿诊断保存失败: {exc}"
                    self._notify(self.last_error)
                    self._cancel_native()
                    self._close_stream()
                    # Retry only after another heartbeat. Repeated storage
                    # failure during one stall must not produce a busy loop.
                    with self._lock:
                        self._reported_generation = self._generation
                self._stop_event.wait(self.poll_seconds)
        finally:
            self._cancel_native()
            self._close_stream()

    def _check_once(self) -> None:
        if self._stop_event.is_set():
            return
        with self._lock:
            heartbeat, generation = self._last_heartbeat, self._generation
        if not self._enabled_callback():
            self._cancel_native()
            self._close_stream()
            return
        if self._armed_generation is not None and self._stream is not None and os.fstat(self._stream.fileno()).st_size > self._armed_size:
            # The native timeout may have fired while this Python thread was
            # unable to run. Preserve that event even if Qt has already resumed.
            native_generation = self._armed_generation
            native_elapsed = max(0.0, self._clock() - self._armed_heartbeat)
            self._cancel_native()
            self._reported_generation = native_generation
            self._append(f"\nUI stall at {self._now().isoformat(timespec='milliseconds')}; heartbeat_delay={native_elapsed:.3f}s; generation={native_generation}\n")
            self._append("Native faulthandler already captured the Python thread stacks above.\n")
            self._notify(f"界面心跳延迟 {native_elapsed:.3f} 秒，诊断已保存到 {self.last_path}")
        if self._reported_generation == generation:
            return
        self._ensure_stream()
        # Opening the file/version lookup can be slow; it must not turn healthy
        # heartbeats received in the meantime into a false stall.
        with self._lock:
            heartbeat, generation = self._last_heartbeat, self._generation
        elapsed = max(0.0, self._clock() - heartbeat)
        if elapsed >= self.threshold_seconds:
            native_dumped = self._armed_generation == generation and self._stream is not None and os.fstat(self._stream.fileno()).st_size > self._armed_size
            self._cancel_native()
            self._reported_generation = generation
            self._append(f"\nUI stall at {self._now().isoformat(timespec='milliseconds')}; heartbeat_delay={elapsed:.3f}s; generation={generation}\n")
            if native_dumped:
                self._append("Native faulthandler already captured the Python thread stacks above.\n")
            else:
                self._append(self._thread_stacks())
            self._notify(f"界面心跳延迟 {elapsed:.3f} 秒，诊断已保存到 {self.last_path}")
            return
        if self.native_fallback and self._armed_generation != generation:
            self._arm_native(generation, self.threshold_seconds - elapsed + self.poll_seconds, heartbeat)

    def _ensure_stream(self) -> None:
        now = self._now()
        if self._stream is not None and self._stream_date == now.date():
            return
        self._cancel_native()
        self._close_stream()
        self.directory.mkdir(parents=True, exist_ok=True)
        self._cleanup(now)
        self.last_path = self.directory / f"ui_stall_{now:%Y-%m-%d}_pid-{os.getpid()}.log"
        self._stream = self.last_path.open("ab", buffering=0)
        self._stream_date = now.date()
        try:
            qt_version = importlib.metadata.version("PySide6")
        except importlib.metadata.PackageNotFoundError:
            qt_version = "unavailable"
        self._append(
            f"\nUI stall diagnostics started {now.isoformat(timespec='milliseconds')}\n"
            f"app={__version__}; python={platform.python_version()}; PySide6={qt_version}; "
            f"os={platform.system()} {platform.release()}; pid={os.getpid()}; "
            f"threshold={self.threshold_seconds:.3f}s; native_fallback={self.native_fallback}\n"
        )

    def _append(self, text: str) -> None:
        if self._stream is not None:
            self._stream.write(RunLogManager._redact_user_home(text).encode("utf-8", errors="replace"))

    @staticmethod
    def _thread_stacks() -> str:
        names = {thread.ident: thread.name for thread in threading.enumerate()}
        lines = []
        for ident, frame in sys._current_frames().items():
            lines.append(f"\nPython thread {ident} ({names.get(ident, 'unknown')}):\n")
            lines.extend(traceback.format_stack(frame, limit=64))
        return "".join(lines)

    def _arm_native(self, generation: int, timeout: float, heartbeat: float) -> None:
        global _native_owner
        with _native_owner_lock:
            if self._stop_event.is_set() or self._stream is None or not self._enabled_callback():
                return
            if _native_owner is not None and _native_owner is not self:
                return
            self._armed_size = os.fstat(self._stream.fileno()).st_size
            faulthandler.dump_traceback_later(max(0.001, timeout), repeat=False, file=self._stream, exit=False)
            _native_owner = self
            self._armed_generation = generation
            self._armed_heartbeat = heartbeat

    def _cancel_native(self) -> None:
        global _native_owner
        with _native_owner_lock:
            if _native_owner is self:
                faulthandler.cancel_dump_traceback_later()
                _native_owner = None
            self._armed_generation = None

    def _close_stream(self) -> None:
        stream, self._stream = self._stream, None
        self._stream_date = None
        if stream is not None:
            stream.close()

    def _notify(self, message: str) -> None:
        if self._report is not None:
            try:
                self._report(message)
            except Exception:
                pass

    def _cleanup(self, now: datetime) -> None:
        cutoff = now.date() - timedelta(days=6)
        for path in self.directory.iterdir():
            match = _FILE_RE.fullmatch(path.name)
            if match is None or path.is_symlink() or not path.is_file():
                continue
            try:
                day = datetime.strptime(match.group("date"), "%Y-%m-%d").date()
                if not cutoff <= day <= now.date():
                    path.unlink()
            except (OSError, ValueError):
                continue


def start_ui_stall_diagnostics(app, manager: RunLogManager, *, threshold_seconds: float = 2.0, heartbeat_ms: int = 250, native_fallback: bool = False):
    """Attach a Qt heartbeat; stop the returned guard before closing manager.

    Disk saving follows ``manager.enabled``. ``guard.refresh_enabled()`` can
    cancel a native timer immediately when the save setting is switched off.
    Native fallback is opt-in because Python exposes no query/restore API for
    someone else's existing ``dump_traceback_later`` timer.
    """
    from PySide6.QtCore import QCoreApplication, QObject, QTimer

    if not isinstance(app, QCoreApplication):
        return None
    if heartbeat_ms <= 0 or heartbeat_ms / 1000 >= threshold_seconds:
        raise ValueError("heartbeat interval must be positive and below the stall threshold")

    class QtHeartbeatGuard(QObject):
        def __init__(self):
            super().__init__(app)
            self.watchdog = UiStallWatchdog(
                manager.directory, lambda: manager.enabled,
                threshold_seconds=threshold_seconds,
                native_fallback=native_fallback,
                report=lambda message: manager.write("界面卡顿诊断", message, level="WARNING"),
            ).start()
            self.timer = QTimer(self)
            self.timer.setInterval(heartbeat_ms)
            self.timer.timeout.connect(self.watchdog.heartbeat)
            self.timer.start()
            app.aboutToQuit.connect(self.stop)

        def refresh_enabled(self) -> None:
            if not manager.enabled:
                self.watchdog._cancel_native()

        def stop(self) -> bool:
            self.timer.stop()
            return self.watchdog.stop()

    return QtHeartbeatGuard()
