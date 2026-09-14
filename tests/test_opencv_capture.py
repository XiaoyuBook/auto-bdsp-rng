from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from auto_bdsp_rng.capture_broker import CaptureOpenError, OpenCVCapture


def _fourcc(value: str) -> int:
    return sum(ord(char) << (8 * index) for index, char in enumerate(value))


@pytest.fixture
def fake_cv2(monkeypatch):
    handles = []

    class Handle:
        def __init__(self, index, backend):
            self.backend = 700 if backend == 0 else backend
            self.values = {3: 640, 4: 480, 5: 30.0, 6: _fourcc("YUY2"), 42: self.backend}
            self.released = False
            handles.append(self)

        def isOpened(self):
            return not self.released

        def get(self, prop):
            return self.values.get(prop, 0)

        def getBackendName(self):
            return {700: "DSHOW", 1400: "MSMF"}.get(self.backend, "OTHER")

        def set(self, prop, value):
            # DirectShow FPS changes rebuild the graph using the default
            # subtype; compressed capture must be selected after that rebuild.
            if self.backend == 700 and prop == 5:
                self.values[6] = _fourcc("YUY2")
            # MSMF's FOURCC property selects the decoded output format; MJPG
            # is not a supported decoded output, unlike DirectShow input.
            if self.backend == 1400 and prop == 6 and value == _fourcc("MJPG"):
                raise RuntimeError("unsupported MSMF output format")
            self.values[prop] = value
            return True

        def read(self):
            return True, np.zeros((int(self.values[4]), int(self.values[3]), 3), dtype=np.uint8)

        def release(self):
            self.released = True

    module = SimpleNamespace(
        CAP_PROP_FRAME_WIDTH=3, CAP_PROP_FRAME_HEIGHT=4, CAP_PROP_FPS=5,
        CAP_PROP_FOURCC=6, CAP_PROP_BACKEND=42,
        VideoCapture=Handle,
        VideoWriter_fourcc=lambda *chars: _fourcc("".join(chars)),
        videoio_registry=SimpleNamespace(hasBackend=lambda _backend: True),
        handles=handles,
    )
    monkeypatch.setitem(sys.modules, "cv2", module)
    return module


@pytest.mark.parametrize("backend", [0, 700])
def test_directshow_keeps_mjpeg_after_fps_negotiation(fake_cv2, backend):
    capture = OpenCVCapture(2, backend)
    try:
        assert capture.open()
        capture.set_properties(1920, 1080, "MJPG", 30.0)
        assert fake_cv2.handles[0].values[6] == _fourcc("MJPG")
        assert capture.read()[1].shape == (1080, 1920, 3)
    finally:
        capture.release()


def test_msmf_does_not_request_mjpeg_as_decoded_output(fake_cv2):
    capture = OpenCVCapture(2, 1400)
    try:
        assert capture.open()
        capture.set_properties(1920, 1080, "MJPG", 30.0)
        assert capture.read()[1].shape == (1080, 1920, 3)
    finally:
        capture.release()


def test_disabled_backend_reports_reason_before_opening_device(fake_cv2, monkeypatch):
    fake_cv2.videoio_registry.hasBackend = lambda _backend: False
    monkeypatch.setenv("OPENCV_VIDEOIO_PRIORITY_MSMF", "0")
    capture = OpenCVCapture(2, 1400)
    with pytest.raises(CaptureOpenError, match="Media Foundation.*不可用"):
        capture.open()
    assert fake_cv2.handles == []


def test_msmf_defaults_to_software_transforms_without_overriding_user_setting(fake_cv2, monkeypatch):
    monkeypatch.delenv("OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS", raising=False)
    OpenCVCapture(2, 1400)
    assert os.environ["OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS"] == "0"
    monkeypatch.setenv("OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS", "1")
    OpenCVCapture(2, 1400)
    assert os.environ["OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS"] == "1"


def test_diagnostics_report_negotiated_values_and_rejected_settings(fake_cv2):
    capture = OpenCVCapture(2, 700)
    try:
        assert capture.open()
        handle = fake_cv2.handles[0]
        set_property = handle.set

        def reject_fps(prop, value):
            if prop == fake_cv2.CAP_PROP_FPS:
                handle.values[prop] = 15.0
                return False
            return set_property(prop, value)

        handle.set = reject_fps
        capture.set_properties(1920, 1080, "MJPG", 30.0)

        details = capture.diagnostics()
        assert details["actual_width"] == 1920
        assert details["actual_height"] == 1080
        assert details["reported_fps"] == 15.0
        assert details["reported_fourcc"] == "MJPG"
        assert details["rejected_properties"] == ["fps"]
        assert capture.read()[0]
    finally:
        capture.release()
