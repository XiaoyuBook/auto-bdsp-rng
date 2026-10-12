"""Bounded display work and cooperative video cleanup outside the Qt loop."""

from __future__ import annotations

import threading
import sys
from collections.abc import Callable

from PySide6.QtCore import QObject, QThread


class CleanupThread(QThread):
    """Keep a blocking cleanup alive until its finished signal is delivered."""

    def __init__(self, action: Callable[[], object], parent: QObject) -> None:
        super().__init__(parent)
        self.action = action
        self.result: object = None
        self.error: BaseException | None = None

    def run(self) -> None:
        try:
            self.result = self.action()
        except BaseException as exc:
            self.error = exc


class CaptureDeviceEnumerationThread(CleanupThread):
    """Enumerate on one COM-initialized worker, retaining its owner until done."""

    def __init__(
        self,
        enumerate_devices: Callable[[int], object],
        capture_api: int,
        generation: int,
        parent: QObject,
    ) -> None:
        self.enumerate_devices = enumerate_devices
        self.capture_api = capture_api
        self.generation = generation
        super().__init__(self._enumerate, parent)

    def _enumerate(self) -> object:
        ole32 = None
        if sys.platform == "win32":
            import ctypes

            ole32 = ctypes.WinDLL("ole32")
            ole32.CoInitializeEx.argtypes = (ctypes.c_void_p, ctypes.c_ulong)
            ole32.CoInitializeEx.restype = ctypes.c_long
            ole32.CoUninitialize.argtypes = ()
            ole32.CoUninitialize.restype = None
            # DirectShow and Media Foundation enumeration accept an STA;
            # COM objects are created and destroyed on this same worker.
            result = ole32.CoInitializeEx(None, 2)
            if result < 0:
                raise OSError(f"无法初始化采集设备枚举 COM: 0x{result & 0xffffffff:08X}")
        try:
            return self.enumerate_devices(self.capture_api)
        finally:
            if ole32 is not None:
                ole32.CoUninitialize()


class BrokerStopThread(CleanupThread):
    """Stop once now, then once after a cancelled startup has relinquished I/O.

    Startup can publish a child after the first stop inspected the controller.
    Its Python event, rather than a possibly deleted QThread wrapper, is the
    boundary after which the final stop can safely confirm process ownership.
    """

    def __init__(
        self,
        controller: object | None,
        startup_done: threading.Event | None,
        parent: QObject,
        *,
        missing_controller_is_error: bool = False,
    ) -> None:
        self.controller = controller
        self.startup_done = startup_done
        self.missing_controller_is_error = missing_controller_is_error
        super().__init__(self._stop, parent)

    def _stop_once(self) -> bool:
        if self.controller is None:
            if self.missing_controller_is_error:
                raise RuntimeError("共享视频源进程控制器不存在，无法确认停止")
            return True
        stop = getattr(self.controller, "stop", None)
        if not callable(stop):
            raise RuntimeError("共享视频源进程控制器不支持停止")
        if stop() is False:
            raise RuntimeError("Broker 未确认停止")
        return True

    def _stop(self) -> bool:
        first_error: BaseException | None = None
        try:
            self._stop_once()
        except BaseException as exc:
            first_error = exc
        if self.startup_done is not None:
            self.startup_done.wait()
            # A failed early stop is recoverable once startup no longer owns
            # the controller. Only the final confirmation decides the result.
            return self._stop_once()
        if first_error is not None:
            raise first_error
        return True


class LatestFrameMailbox:
    """One frame and one lightweight queued wakeup, regardless of producer FPS."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest: tuple[int, object] | None = None
        self._wake_pending = False
        self._closed = False

    def offer(self, generation: int, frame: object) -> bool:
        with self._lock:
            if self._closed:
                return False
            self._latest = (generation, frame)
            if self._wake_pending:
                return False
            self._wake_pending = True
            return True

    def take(self) -> tuple[int, object] | None:
        with self._lock:
            latest, self._latest = self._latest, None
            self._wake_pending = False
            return latest

    def clear(self, *, close: bool = False) -> None:
        with self._lock:
            self._latest = None
            # An already queued wakeup still belongs to this mailbox.
            self._closed = self._closed or close


class PreviewFrameThread(QThread):
    """Serialize read/render/release, keeping only the latest request/result.

    The GUI's regular preview timer polls results. Frames never become Qt
    signal payloads and a slow capture backend cannot accumulate UI events.
    """

    def __init__(
        self,
        read_render: Callable[[object], object],
        release: Callable[[], None],
        parent: QObject,
    ) -> None:
        super().__init__(parent)
        self._read_render = read_render
        self._release = release
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._request: object | None = None
        self._latest: tuple[bool, object] | None = None
        self.release_error: BaseException | None = None

    def submit(self, request: object) -> None:
        with self._lock:
            if self._stop.is_set():
                return
            self._request = request
            self._wake.set()

    def take(self) -> tuple[bool, object] | None:
        with self._lock:
            latest, self._latest = self._latest, None
            return latest

    def request_stop(self) -> None:
        self.requestInterruption()
        with self._lock:
            self._stop.set()
            self._request = None
            self._latest = None
            self._wake.set()

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    def run(self) -> None:
        try:
            while not self._stop.is_set():
                self._wake.wait()
                with self._lock:
                    self._wake.clear()
                    request, self._request = self._request, None
                if request is None or self._stop.is_set():
                    continue
                try:
                    result = (True, self._read_render(request))
                except BaseException as exc:
                    result = (False, (request, exc))
                with self._lock:
                    if not self._stop.is_set():
                        self._latest = result
                if not result[0]:
                    break
        finally:
            try:
                self._release()
            except BaseException as exc:
                self.release_error = exc
