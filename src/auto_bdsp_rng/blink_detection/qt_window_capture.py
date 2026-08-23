from __future__ import annotations

import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


OBS_PROCESS_NAMES = {"obs.exe", "obs32.exe", "obs64.exe"}
_QT_CALL_BRIDGE: Any | None = None
_QT_CALL_BRIDGE_LOCK = threading.Lock()


@dataclass(frozen=True)
class WindowTarget:
    hwnd: int
    title: str
    process_name: str


def _looks_like_obs_projector_title(title: str) -> bool:
    normalized = title.strip().casefold()
    return normalized.startswith(("投影 - ", "projector (", "projector - ", "multiview - "))


def _process_name_for_window(hwnd: int) -> str:
    try:
        import ctypes
        import ctypes.wintypes
        import win32process

        process_id = win32process.GetWindowThreadProcessId(hwnd)[1]
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = (ctypes.wintypes.DWORD, ctypes.wintypes.BOOL, ctypes.wintypes.DWORD)
        open_process.restype = ctypes.wintypes.HANDLE
        query_image_name = kernel32.QueryFullProcessImageNameW
        query_image_name.argtypes = (
            ctypes.wintypes.HANDLE,
            ctypes.wintypes.DWORD,
            ctypes.wintypes.LPWSTR,
            ctypes.POINTER(ctypes.wintypes.DWORD),
        )
        query_image_name.restype = ctypes.wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (ctypes.wintypes.HANDLE,)
        close_handle.restype = ctypes.wintypes.BOOL

        process = open_process(0x1000, False, process_id)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not process:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            size = ctypes.wintypes.DWORD(len(buffer))
            if not query_image_name(process, 0, buffer, ctypes.byref(size)):
                raise ctypes.WinError(ctypes.get_last_error())
            return Path(buffer.value).name.casefold()
        finally:
            close_handle(process)
    except Exception:
        return ""


def find_obs_window_target(window_prefix: str) -> WindowTarget | None:
    if sys.platform != "win32" or not window_prefix:
        return None
    try:
        import win32gui
    except ImportError:
        return None

    matches: list[WindowTarget] = []
    unresolved_projectors: list[str] = []

    def visit(hwnd: int, _context: object) -> bool:
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        if window_prefix not in title:
            return True
        process_name = _process_name_for_window(hwnd)
        if process_name in OBS_PROCESS_NAMES:
            matches.append(WindowTarget(hwnd=int(hwnd), title=title, process_name=process_name))
        elif not process_name and _looks_like_obs_projector_title(title):
            unresolved_projectors.append(title)
        return True

    win32gui.EnumWindows(visit, None)
    if unresolved_projectors:
        titles = ", ".join(repr(title) for title in unresolved_projectors)
        raise RuntimeError(
            "无法确认 OBS 投影窗口所属进程，请确认 OBS 与本软件使用相同权限运行"
            f": {titles}"
        )
    if not matches:
        return None
    exact_matches = [target for target in matches if target.title == window_prefix]
    if len(exact_matches) == 1:
        return exact_matches[0]
    if len(matches) == 1:
        return matches[0]
    titles = ", ".join(repr(target.title) for target in matches)
    raise RuntimeError(f"OBS 窗口前缀匹配到多个窗口，请输入更完整的窗口名: {titles}")


def _extended_frame_bounds(hwnd: int) -> tuple[int, int, int, int]:
    """Return the visible DWM bounds used by Windows Graphics Capture."""

    import ctypes
    import ctypes.wintypes
    import win32gui

    try:
        rect = ctypes.wintypes.RECT()
        get_window_attribute = ctypes.WinDLL("dwmapi", use_last_error=True).DwmGetWindowAttribute
        get_window_attribute.argtypes = (
            ctypes.wintypes.HWND,
            ctypes.wintypes.DWORD,
            ctypes.wintypes.LPVOID,
            ctypes.wintypes.DWORD,
        )
        get_window_attribute.restype = ctypes.c_long
        result = get_window_attribute(
            ctypes.wintypes.HWND(hwnd),
            9,  # DWMWA_EXTENDED_FRAME_BOUNDS
            ctypes.byref(rect),
            ctypes.sizeof(rect),
        )
        bounds = (rect.left, rect.top, rect.right, rect.bottom)
        if result == 0 and bounds[2] > bounds[0] and bounds[3] > bounds[1]:
            return bounds
    except (AttributeError, OSError):
        pass
    return tuple(int(value) for value in win32gui.GetWindowRect(hwnd))  # type: ignore[return-value]


def _ensure_window_available(hwnd: int) -> None:
    import win32gui

    if not win32gui.IsWindow(hwnd):
        raise RuntimeError("OBS 投影窗口已经关闭")
    if win32gui.IsIconic(hwnd):
        raise RuntimeError("OBS 投影窗口已最小化，请先恢复窗口")


class _TransientFrameGeometry(RuntimeError):
    pass


def _qt_call_bridge(app: Any) -> Any:
    global _QT_CALL_BRIDGE

    with _QT_CALL_BRIDGE_LOCK:
        if _QT_CALL_BRIDGE is not None:
            return _QT_CALL_BRIDGE

        from PySide6.QtCore import QObject, Signal, Slot

        class QtCallBridge(QObject):
            requested = Signal(object, object, object, object)

            def __init__(self) -> None:
                super().__init__()
                self.requested.connect(self.execute)

            @Slot(object, object, object, object)
            def execute(
                self,
                callback: Any,
                result: list[object],
                errors: list[BaseException],
                done: threading.Event,
            ) -> None:
                try:
                    result.append(callback())
                except BaseException as exc:
                    errors.append(exc)
                finally:
                    done.set()

        bridge = QtCallBridge()
        bridge.moveToThread(app.thread())
        _QT_CALL_BRIDGE = bridge
        return bridge


def _call_on_qt_app_thread(callback: Any) -> Any:
    from PySide6.QtCore import QThread
    from PySide6.QtGui import QGuiApplication

    app = QGuiApplication.instance()
    if app is None or QThread.currentThread() == app.thread():
        return callback()

    done = threading.Event()
    result: list[object] = []
    errors: list[BaseException] = []
    _qt_call_bridge(app).requested.emit(callback, result, errors, done)
    if not done.wait(10.0):
        raise RuntimeError("等待 Qt 主线程执行 OBS 窗口捕捉超时")
    if errors:
        raise errors[0]
    return result[0] if result else None


class QtWindowCapture:
    """cv2.VideoCapture-compatible reader backed by Qt Windows Graphics Capture."""

    keep_open_for_preview = True

    def __init__(self, target: WindowTarget, crop: list[int] | None = None) -> None:
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtMultimedia import QMediaCaptureSession, QVideoSink, QWindowCapture

        self._owner_thread = threading.get_ident()
        self._target = target
        self._crop = tuple(int(value) for value in crop) if crop is not None else None
        self._latest_frame: np.ndarray | None = None
        self._frame_serial = 0
        self._last_read_serial = 0
        self._waiting_loop: Any | None = None
        self._error = ""
        self._released = False
        self._owned_app: Any | None = None

        app = QGuiApplication.instance()
        if app is None:
            if threading.current_thread() is not threading.main_thread():
                raise RuntimeError("OBS 窗口捕捉需要先在主线程创建 Qt 应用")
            self._owned_app = QGuiApplication([])

        candidates = [
            window
            for window in QWindowCapture.capturableWindows()
            if window.isValid() and window.description() == target.title
        ]
        if len(candidates) != 1:
            raise RuntimeError(f"Qt 无法唯一定位 OBS 窗口: {target.title}")

        self._capture = QWindowCapture()
        self._session = QMediaCaptureSession()
        self._sink = QVideoSink()
        self._session.setWindowCapture(self._capture)
        self._session.setVideoSink(self._sink)
        self._capture.setWindow(candidates[0])
        self._sink.videoFrameChanged.connect(self._receive_frame)
        self._capture.errorOccurred.connect(self._capture_failed)
        self._capture.start()

    def _check_thread(self) -> None:
        if threading.get_ident() != self._owner_thread:
            raise RuntimeError("OBS 窗口捕捉必须在创建它的线程中读取和关闭")

    def _capture_failed(self, _error: object, message: str) -> None:
        self._error = message or "OBS 窗口捕捉失败"
        if self._waiting_loop is not None:
            self._waiting_loop.quit()

    @staticmethod
    def _image_to_bgr(image: Any) -> np.ndarray:
        from PySide6.QtGui import QImage

        converted = image.convertToFormat(QImage.Format.Format_BGR888)
        width = converted.width()
        height = converted.height()
        bytes_per_line = converted.bytesPerLine()
        raw = np.frombuffer(converted.bits(), dtype=np.uint8, count=converted.sizeInBytes())
        rows = raw.reshape((height, bytes_per_line))
        pixels = rows[:, : width * 3].reshape((height, width, 3))
        return np.ascontiguousarray(pixels).copy()

    @staticmethod
    def _slice_frame(frame: np.ndarray, x: int, y: int, width: int, height: int) -> np.ndarray:
        frame_height, frame_width = frame.shape[:2]
        left = min(max(0, x), frame_width)
        top = min(max(0, y), frame_height)
        right = min(max(left, left + width), frame_width)
        bottom = min(max(top, top + height), frame_height)
        if right <= left or bottom <= top:
            raise RuntimeError("OBS 窗口捕捉裁剪区域无效")
        return np.ascontiguousarray(frame[top:bottom, left:right]).copy()

    def _crop_frame(self, frame: np.ndarray) -> np.ndarray:
        import win32gui

        _ensure_window_available(self._target.hwnd)
        if self._crop is not None and self._crop != (0, 0, 0, 0):
            return self._slice_frame(frame, *self._crop)

        window_left, window_top, window_right, window_bottom = _extended_frame_bounds(self._target.hwnd)
        client_left, client_top = win32gui.ClientToScreen(self._target.hwnd, (0, 0))
        _, _, client_width, client_height = win32gui.GetClientRect(self._target.hwnd)
        window_width = window_right - window_left
        window_height = window_bottom - window_top
        if window_width <= 0 or window_height <= 0 or client_width <= 0 or client_height <= 0:
            raise RuntimeError("OBS 投影窗口已最小化或尺寸无效")
        if abs(frame.shape[1] - window_width) > 1 or abs(frame.shape[0] - window_height) > 1:
            raise _TransientFrameGeometry("OBS 投影窗口尺寸正在变化")

        scale_x = frame.shape[1] / window_width
        scale_y = frame.shape[0] / window_height
        x = round((client_left - window_left) * scale_x)
        y = round((client_top - window_top) * scale_y)
        width = round(client_width * scale_x)
        height = round(client_height * scale_y)
        return self._slice_frame(frame, x, y, width, height)

    def _receive_frame(self, video_frame: Any) -> None:
        if self._released or not video_frame.isValid():
            return
        image = video_frame.toImage()
        if image.isNull():
            return
        try:
            frame = self._crop_frame(self._image_to_bgr(image))
        except _TransientFrameGeometry:
            return
        except Exception as exc:
            self._error = str(exc)
        else:
            self._latest_frame = frame
            self._frame_serial += 1
        if self._waiting_loop is not None:
            self._waiting_loop.quit()

    def read(self) -> tuple[bool, np.ndarray | None]:
        from PySide6.QtCore import QEventLoop, QTimer

        self._check_thread()
        if self._released:
            return False, None
        _ensure_window_available(self._target.hwnd)
        if self._error:
            raise RuntimeError(self._error)

        if self._frame_serial <= self._last_read_serial:
            event_loop = QEventLoop()
            timer = QTimer()
            timer.setSingleShot(True)
            timer.timeout.connect(event_loop.quit)
            self._waiting_loop = event_loop
            timer.start(1000 if self._last_read_serial == 0 else 250)
            event_loop.exec()
            timer.stop()
            self._waiting_loop = None
            _ensure_window_available(self._target.hwnd)

        if self._error:
            raise RuntimeError(self._error)
        if self._latest_frame is None or self._frame_serial <= self._last_read_serial:
            return False, None
        self._last_read_serial = self._frame_serial
        return True, self._latest_frame.copy()

    def release(self) -> None:
        self._check_thread()
        if self._released:
            return
        self._released = True
        capture = self._capture
        sink = self._sink
        session = self._session
        if self._waiting_loop is not None:
            self._waiting_loop.quit()
        try:
            sink.videoFrameChanged.disconnect(self._receive_frame)
        except (RuntimeError, TypeError):
            pass
        try:
            capture.errorOccurred.disconnect(self._capture_failed)
        except (RuntimeError, TypeError):
            pass
        try:
            capture.stop()
        finally:
            session.setVideoSink(None)
            session.setWindowCapture(None)
            self._capture = None
            self._sink = None
            self._session = None
            self._latest_frame = None


class QtWindowCaptureProxy:
    """Synchronous proxy that keeps Qt Multimedia objects in the app thread."""

    keep_open_for_preview = True

    def __init__(self, target: WindowTarget, crop: list[int] | None = None) -> None:
        self._capture = _call_on_qt_app_thread(lambda: QtWindowCapture(target, crop))
        self._released = False

    def read(self) -> tuple[bool, np.ndarray | None]:
        if self._released:
            return False, None
        capture = self._capture
        return _call_on_qt_app_thread(capture.read)

    def release(self) -> None:
        if self._released:
            return
        capture = self._capture
        _call_on_qt_app_thread(capture.release)
        self._capture = None
        self._released = True


def create_qt_window_capture(target: WindowTarget, crop: list[int] | None = None) -> Any:
    from PySide6.QtCore import QThread
    from PySide6.QtGui import QGuiApplication

    app = QGuiApplication.instance()
    if app is not None and QThread.currentThread() != app.thread():
        return QtWindowCaptureProxy(target, crop)
    return QtWindowCapture(target, crop)
