from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

from auto_bdsp_rng.blink_detection import (
    BlinkCaptureConfig,
    ProjectXsCaptureConfigError,
    ProjectXsIntegrationError,
    ProjectXsNoFrameError,
    capture_player_blinks,
)
import auto_bdsp_rng.blink_detection.project_xs as project_xs
from auto_bdsp_rng.ui.main_window import _project_xs_capture_error_dialog


def test_missing_eye_template_is_reported_as_capture_config_error(monkeypatch, tmp_path: Path) -> None:
    fake_cv2 = type(
        "FakeCv2",
        (),
        {
            "IMREAD_GRAYSCALE": 0,
            "imdecode": staticmethod(lambda *_args: None),
            "imread": staticmethod(lambda *_args: None),
        },
    )
    monkeypatch.setitem(sys.modules, "cv2", fake_cv2)

    with pytest.raises(ProjectXsCaptureConfigError, match="Cannot read eye template"):
        capture_player_blinks(
            BlinkCaptureConfig(tmp_path / "missing-eye.png", (0, 0, 20, 20)),
            should_stop=lambda: True,
            show_window=False,
        )


def test_template_larger_than_roi_is_rejected_before_opening_capture(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(project_xs, "_read_grayscale_image", lambda _path: np.zeros((12, 16), dtype=np.uint8))
    monkeypatch.setattr(
        project_xs,
        "_open_capture_source",
        lambda *_args, **_kwargs: pytest.fail("capture source must not open for invalid config"),
    )

    with pytest.raises(ProjectXsCaptureConfigError, match="大于眼睛 ROI"):
        capture_player_blinks(
            BlinkCaptureConfig(tmp_path / "eye.png", (0, 0, 8, 10)),
            should_stop=lambda: False,
            show_window=False,
        )


def test_match_template_size_assertion_is_classified_as_capture_config(monkeypatch, tmp_path: Path) -> None:
    class FakeVideo:
        def set(self, *_args):
            return None

        def read(self):
            return True, np.zeros((20, 20, 3), dtype=np.uint8)

        def release(self):
            return None

    class FakeCv2:
        CAP_ANY = 0
        CAP_V4L = 0
        CAP_PROP_FRAME_WIDTH = 1
        CAP_PROP_FRAME_HEIGHT = 2
        CAP_PROP_BUFFERSIZE = 3
        COLOR_RGB2GRAY = 4
        TM_CCOEFF_NORMED = 5

        @staticmethod
        def VideoCapture(*_args):
            return FakeVideo()

        @staticmethod
        def cvtColor(frame, _mode):
            return frame[:, :, 0]

        @staticmethod
        def matchTemplate(*_args):
            raise RuntimeError(
                "OpenCV(4.10.0) modules/imgproc/src/templmatch.cpp:1175: "
                "(-215:Assertion failed) _img.size().width <= _templ.size().width"
            )

    monkeypatch.setattr(project_xs, "_load_cv2", lambda: FakeCv2)
    eye = np.ones((3, 3), dtype=np.uint8)
    config = BlinkCaptureConfig(tmp_path / "eye.png", (0, 0, 10, 10), monitor_window=False)

    with pytest.raises(ProjectXsCaptureConfigError, match="眼睛模板或 ROI"):
        project_xs._tracking_blink_controlled(
            eye,
            config,
            should_stop=lambda: False,
            frame_callback=None,
            progress_callback=None,
            show_window=False,
        )


def test_no_frame_has_a_distinct_error_type(monkeypatch, tmp_path: Path) -> None:
    class FakeVideo:
        def set(self, *_args):
            return None

        def read(self):
            return False, None

        def release(self):
            return None

    class FakeCv2:
        CAP_ANY = 0
        CAP_V4L = 0
        CAP_PROP_FRAME_WIDTH = 1
        CAP_PROP_FRAME_HEIGHT = 2
        CAP_PROP_BUFFERSIZE = 3

        @staticmethod
        def VideoCapture(*_args):
            return FakeVideo()

    monkeypatch.setattr(project_xs, "_load_cv2", lambda: FakeCv2)
    monkeypatch.setattr(project_xs.time, "sleep", lambda _seconds: None)
    config = BlinkCaptureConfig(tmp_path / "eye.png", (0, 0, 10, 10), monitor_window=False)

    with pytest.raises(ProjectXsNoFrameError, match="未检测到捕捉画面"):
        project_xs._tracking_blink_controlled(
            np.ones((3, 3), dtype=np.uint8),
            config,
            should_stop=lambda: False,
            frame_callback=None,
            progress_callback=None,
            show_window=False,
        )


def test_capture_error_dialog_maps_current_and_legacy_error_text() -> None:
    title, message = _project_xs_capture_error_dialog(ProjectXsNoFrameError("no frame"))
    assert title == "捕捉失败"
    assert "未检测到捕捉画面" in message

    title, message = _project_xs_capture_error_dialog(
        ProjectXsIntegrationError("Project_Xs blink tracking failed: templmatch.cpp _img.size")
    )
    assert title == "眼睛配置无效"
    assert "重新框选眼睛模板" in message

    wrapped_missing_template = ProjectXsIntegrationError("Project_Xs blink tracking failed")
    wrapped_missing_template.__cause__ = ProjectXsIntegrationError("Cannot read eye template image")
    title, message = _project_xs_capture_error_dialog(
        wrapped_missing_template,
        fallback_title="校正失败",
    )
    assert title == "眼睛配置无效"
    assert "保存配置" in message

    title, message = _project_xs_capture_error_dialog(ProjectXsIntegrationError("共享视频源读取失败"))
    assert title == "捕捉失败"
    assert message == "共享视频源读取失败"
