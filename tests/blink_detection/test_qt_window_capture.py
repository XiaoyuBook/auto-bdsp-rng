from __future__ import annotations

import sys
import threading
import types

import numpy as np
import pytest

import auto_bdsp_rng.blink_detection.qt_window_capture as qt_window_capture
from auto_bdsp_rng.blink_detection.qt_window_capture import QtWindowCapture, WindowTarget


class _FakeBackendCapture:
    def __init__(self) -> None:
        self.stop_count = 0
        self.errorOccurred = _FakeDisconnectableSignal()

    def stop(self) -> None:
        self.stop_count += 1


class _FakeWaitingLoop:
    def __init__(self) -> None:
        self.quit_count = 0

    def quit(self) -> None:
        self.quit_count += 1


class _FakeDisconnectableSignal:
    def __init__(self) -> None:
        self.disconnect_count = 0

    def disconnect(self, _callback) -> None:
        self.disconnect_count += 1


class _FakeSink:
    def __init__(self) -> None:
        self.videoFrameChanged = _FakeDisconnectableSignal()


class _FakeSession:
    def __init__(self) -> None:
        self.video_sinks = []
        self.window_captures = []

    def setVideoSink(self, sink) -> None:
        self.video_sinks.append(sink)

    def setWindowCapture(self, capture) -> None:
        self.window_captures.append(capture)


def _capture_without_qt() -> QtWindowCapture:
    capture = object.__new__(QtWindowCapture)
    capture._owner_thread = threading.get_ident()
    capture._target = WindowTarget(321, "OBS Projector", "obs64.exe")
    capture._crop = None
    capture._latest_frame = None
    capture._frame_serial = 0
    capture._last_read_serial = 0
    capture._waiting_loop = None
    capture._error = ""
    capture._released = False
    capture._owned_app = None
    capture._capture = _FakeBackendCapture()
    capture._sink = _FakeSink()
    capture._session = _FakeSession()
    return capture


def _install_fake_qt_core(monkeypatch):
    class FakeSignal:
        def __init__(self) -> None:
            self.callback = None

        def connect(self, callback) -> None:
            self.callback = callback

    class FakeEventLoop:
        instances = []

        def __init__(self) -> None:
            self.exec_count = 0
            self.quit_count = 0
            type(self).instances.append(self)

        def exec(self) -> None:
            self.exec_count += 1

        def quit(self) -> None:
            self.quit_count += 1

    class FakeTimer:
        instances = []

        def __init__(self) -> None:
            self.timeout = FakeSignal()
            self.single_shot = False
            self.started_with = None
            self.stop_count = 0
            type(self).instances.append(self)

        def setSingleShot(self, enabled: bool) -> None:
            self.single_shot = enabled

        def start(self, milliseconds: int) -> None:
            self.started_with = milliseconds

        def stop(self) -> None:
            self.stop_count += 1

    package = types.ModuleType("PySide6")
    package.__path__ = []
    core = types.ModuleType("PySide6.QtCore")
    core.QEventLoop = FakeEventLoop
    core.QTimer = FakeTimer
    monkeypatch.setitem(sys.modules, "PySide6", package)
    monkeypatch.setitem(sys.modules, "PySide6.QtCore", core)
    return FakeEventLoop, FakeTimer


def test_slice_frame_returns_clipped_contiguous_copy() -> None:
    frame = np.arange(6 * 8 * 3, dtype=np.uint8).reshape((6, 8, 3))

    cropped = QtWindowCapture._slice_frame(frame, 6, 4, 4, 4)

    np.testing.assert_array_equal(cropped, frame[4:6, 6:8])
    assert cropped.flags.c_contiguous
    assert not np.shares_memory(cropped, frame)

    with pytest.raises(RuntimeError, match="裁剪区域无效"):
        QtWindowCapture._slice_frame(frame, 8, 0, 1, 1)


def test_image_to_bgr_removes_stride_padding_and_copies(monkeypatch) -> None:
    class FakeQImage:
        class Format:
            Format_BGR888 = object()

    backing = bytearray(
        [
            1,
            2,
            3,
            4,
            5,
            6,
            90,
            91,
            7,
            8,
            9,
            10,
            11,
            12,
            92,
            93,
        ]
    )

    class ConvertedImage:
        def width(self) -> int:
            return 2

        def height(self) -> int:
            return 2

        def bytesPerLine(self) -> int:
            return 8

        def sizeInBytes(self) -> int:
            return len(backing)

        def bits(self):
            return backing

    requested_formats = []

    class SourceImage:
        def convertToFormat(self, image_format):
            requested_formats.append(image_format)
            return ConvertedImage()

    package = types.ModuleType("PySide6")
    package.__path__ = []
    gui = types.ModuleType("PySide6.QtGui")
    gui.QImage = FakeQImage
    monkeypatch.setitem(sys.modules, "PySide6", package)
    monkeypatch.setitem(sys.modules, "PySide6.QtGui", gui)

    frame = QtWindowCapture._image_to_bgr(SourceImage())

    np.testing.assert_array_equal(
        frame,
        np.array(
            [
                [[1, 2, 3], [4, 5, 6]],
                [[7, 8, 9], [10, 11, 12]],
            ],
            dtype=np.uint8,
        ),
    )
    assert requested_formats == [FakeQImage.Format.Format_BGR888]
    assert frame.flags.c_contiguous
    backing[0] = 255
    assert frame[0, 0, 0] == 1


def test_crop_frame_uses_dwm_extended_bounds_for_client_coordinates(monkeypatch) -> None:
    raw_width = 979
    raw_height = 727
    raw = np.empty((raw_height, raw_width, 3), dtype=np.uint16)
    raw[:, :, 0] = np.arange(raw_width, dtype=np.uint16)
    raw[:, :, 1] = np.arange(raw_height, dtype=np.uint16)[:, None]
    raw[:, :, 2] = 1
    get_window_rect_calls = []
    fake_win32gui = types.SimpleNamespace(
        IsWindow=lambda hwnd: hwnd == 321,
        IsIconic=lambda _hwnd: False,
        GetWindowRect=lambda hwnd: get_window_rect_calls.append(hwnd) or (100, 100, 1097, 836),
        ClientToScreen=lambda _hwnd, _point: (111, 147),
        GetClientRect=lambda _hwnd: (0, 0, 975, 680),
    )
    extended_bounds_calls = []
    monkeypatch.setitem(sys.modules, "win32gui", fake_win32gui)
    monkeypatch.setattr(
        qt_window_capture,
        "_extended_frame_bounds",
        lambda hwnd: extended_bounds_calls.append(hwnd) or (109, 100, 1088, 827),
    )
    capture = _capture_without_qt()

    cropped = capture._crop_frame(raw)

    np.testing.assert_array_equal(cropped, raw[47:727, 2:977])
    assert cropped.shape == (680, 975, 3)
    assert cropped.flags.c_contiguous
    assert not np.shares_memory(cropped, raw)
    assert extended_bounds_calls == [321]
    assert get_window_rect_calls == []


def test_crop_frame_uses_explicit_crop_without_window_geometry(monkeypatch) -> None:
    raw = np.arange(8 * 10 * 3, dtype=np.uint8).reshape((8, 10, 3))
    fake_win32gui = types.SimpleNamespace(
        IsWindow=lambda _hwnd: True,
        IsIconic=lambda _hwnd: False,
    )
    monkeypatch.setitem(sys.modules, "win32gui", fake_win32gui)
    monkeypatch.setattr(
        qt_window_capture,
        "_extended_frame_bounds",
        lambda _hwnd: pytest.fail("Explicit crop must not query DWM geometry"),
    )
    capture = _capture_without_qt()
    capture._crop = (2, 3, 4, 2)

    cropped = capture._crop_frame(raw)

    np.testing.assert_array_equal(cropped, raw[3:5, 2:6])


def test_window_availability_reports_closed_and_minimized(monkeypatch) -> None:
    fake_win32gui = types.SimpleNamespace(IsWindow=lambda _hwnd: False, IsIconic=lambda _hwnd: False)
    monkeypatch.setitem(sys.modules, "win32gui", fake_win32gui)

    with pytest.raises(RuntimeError, match="已经关闭"):
        qt_window_capture._ensure_window_available(321)

    fake_win32gui.IsWindow = lambda _hwnd: True
    fake_win32gui.IsIconic = lambda _hwnd: True
    with pytest.raises(RuntimeError, match="已最小化"):
        qt_window_capture._ensure_window_available(321)


def test_find_obs_window_reports_process_permission_mismatch(monkeypatch) -> None:
    title = "投影 - 源：窗口采集 2"
    fake_win32gui = types.SimpleNamespace(
        EnumWindows=lambda callback, context: callback(321, context),
        IsWindowVisible=lambda _hwnd: True,
        GetWindowText=lambda _hwnd: title,
    )
    monkeypatch.setattr(qt_window_capture.sys, "platform", "win32")
    monkeypatch.setattr(qt_window_capture, "_process_name_for_window", lambda _hwnd: "")
    monkeypatch.setitem(sys.modules, "win32gui", fake_win32gui)

    with pytest.raises(RuntimeError, match="相同权限"):
        qt_window_capture.find_obs_window_target(title)


def test_crop_frame_rejects_stale_geometry_during_resize(monkeypatch) -> None:
    raw = np.empty((727, 979, 3), dtype=np.uint8)
    fake_win32gui = types.SimpleNamespace(
        IsWindow=lambda _hwnd: True,
        IsIconic=lambda _hwnd: False,
        ClientToScreen=lambda _hwnd, _point: (111, 147),
        GetClientRect=lambda _hwnd: (0, 0, 1155, 800),
    )
    monkeypatch.setitem(sys.modules, "win32gui", fake_win32gui)
    monkeypatch.setattr(qt_window_capture, "_extended_frame_bounds", lambda _hwnd: (109, 100, 1268, 947))
    capture = _capture_without_qt()

    with pytest.raises(qt_window_capture._TransientFrameGeometry):
        capture._crop_frame(raw)


def test_transient_resize_frame_is_dropped_without_permanent_error(monkeypatch) -> None:
    capture = _capture_without_qt()
    waiting_loop = _FakeWaitingLoop()
    capture._waiting_loop = waiting_loop
    monkeypatch.setattr(capture, "_image_to_bgr", lambda _image: np.empty((10, 10, 3), dtype=np.uint8))
    monkeypatch.setattr(
        capture,
        "_crop_frame",
        lambda _frame: (_ for _ in ()).throw(qt_window_capture._TransientFrameGeometry("resizing")),
    )

    class FakeImage:
        @staticmethod
        def isNull() -> bool:
            return False

    class FakeVideoFrame:
        @staticmethod
        def isValid() -> bool:
            return True

        @staticmethod
        def toImage() -> FakeImage:
            return FakeImage()

    capture._receive_frame(FakeVideoFrame())

    assert capture._error == ""
    assert capture._frame_serial == 0
    assert waiting_loop.quit_count == 0


def test_read_returns_each_frame_once_and_times_out_duplicates(monkeypatch) -> None:
    _, FakeTimer = _install_fake_qt_core(monkeypatch)
    monkeypatch.setattr(qt_window_capture, "_ensure_window_available", lambda _hwnd: None)
    capture = _capture_without_qt()
    source = np.arange(12, dtype=np.uint8).reshape((2, 2, 3))
    capture._latest_frame = source
    capture._frame_serial = 1

    ok, first = capture.read()
    duplicate_ok, duplicate = capture.read()

    assert ok is True
    np.testing.assert_array_equal(first, source)
    assert first is not source
    assert duplicate_ok is False
    assert duplicate is None
    assert len(FakeTimer.instances) == 1
    timer = FakeTimer.instances[0]
    assert timer.single_shot is True
    assert timer.started_with == 250
    assert timer.stop_count == 1
    assert capture._waiting_loop is None


def test_read_uses_longer_timeout_for_initial_frame(monkeypatch) -> None:
    _, FakeTimer = _install_fake_qt_core(monkeypatch)
    monkeypatch.setattr(qt_window_capture, "_ensure_window_available", lambda _hwnd: None)
    capture = _capture_without_qt()

    ok, frame = capture.read()

    assert ok is False
    assert frame is None
    assert len(FakeTimer.instances) == 1
    assert FakeTimer.instances[0].started_with == 1000
    assert FakeTimer.instances[0].stop_count == 1


def test_read_rechecks_window_after_wait(monkeypatch) -> None:
    _install_fake_qt_core(monkeypatch)
    checks = []

    def check_window(_hwnd: int) -> None:
        checks.append("check")
        if len(checks) == 2:
            raise RuntimeError("OBS 投影窗口已最小化，请先恢复窗口")

    monkeypatch.setattr(qt_window_capture, "_ensure_window_available", check_window)
    capture = _capture_without_qt()

    with pytest.raises(RuntimeError, match="已最小化"):
        capture.read()

    assert checks == ["check", "check"]


def test_capture_rejects_cross_thread_read(monkeypatch) -> None:
    monkeypatch.setattr(qt_window_capture, "_ensure_window_available", lambda _hwnd: None)
    capture = _capture_without_qt()
    errors = []

    def read_from_other_thread() -> None:
        try:
            capture.read()
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=read_from_other_thread)
    thread.start()
    thread.join()

    assert len(errors) == 1
    assert "创建它的线程" in str(errors[0])


def test_qt_window_capture_proxy_delegates_to_qt_app_thread(monkeypatch) -> None:
    backend = types.SimpleNamespace(
        read=lambda: (True, "frame"),
        release=lambda: calls.append("release"),
    )
    calls = []

    def call_on_qt_thread(callback):
        calls.append("dispatch")
        return callback()

    monkeypatch.setattr(qt_window_capture, "_call_on_qt_app_thread", call_on_qt_thread)
    monkeypatch.setattr(qt_window_capture, "QtWindowCapture", lambda _target, _crop: backend)

    proxy = qt_window_capture.QtWindowCaptureProxy(WindowTarget(321, "OBS", "obs64.exe"))

    assert proxy.read() == (True, "frame")
    proxy.release()
    proxy.release()
    assert proxy.read() == (False, None)
    assert calls == ["dispatch", "dispatch", "dispatch", "release"]


def test_release_is_idempotent_and_unblocks_waiter(monkeypatch) -> None:
    _install_fake_qt_core(monkeypatch)
    capture = _capture_without_qt()
    backend = capture._capture
    sink = capture._sink
    session = capture._session
    waiting_loop = _FakeWaitingLoop()
    capture._latest_frame = np.ones((1, 1, 3), dtype=np.uint8)
    capture._waiting_loop = waiting_loop

    capture.release()
    capture.release()
    ok, frame = capture.read()

    assert capture._released is True
    assert capture._latest_frame is None
    assert backend.stop_count == 1
    assert backend.errorOccurred.disconnect_count == 1
    assert sink.videoFrameChanged.disconnect_count == 1
    assert session.video_sinks == [None]
    assert session.window_captures == [None]
    assert capture._capture is None
    assert capture._sink is None
    assert capture._session is None
    assert waiting_loop.quit_count == 1
    assert ok is False
    assert frame is None
