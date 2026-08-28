from __future__ import annotations

import time

import numpy as np
import pytest

from auto_bdsp_rng.blink_detection import (
    BlinkCaptureConfig,
    BrokerFrameCapture,
    ProjectXsIntegrationError,
)
import auto_bdsp_rng.blink_detection.project_xs as project_xs_module
from auto_bdsp_rng.capture_broker import FramePacket


def _packet(sequence: int, value: int) -> FramePacket:
    frame = np.full((1, 2, 3), value, dtype=np.uint8)
    return FramePacket(
        sequence=sequence,
        timestamp_ns=time.monotonic_ns(),
        width=2,
        height=1,
        stride=6,
        data=frame.tobytes(),
    )


def test_tracking_capture_waits_for_each_new_sequence_and_returns_private_frames():
    first_packet = _packet(7, 17)
    second_packet = _packet(9, 19)
    packets = [first_packet, second_packet]

    class Client:
        def __init__(self) -> None:
            self.wait_calls: list[tuple[int, float]] = []
            self.close_calls = 0

        def wait_for_frame(self, *, after_sequence: int, timeout: float) -> FramePacket:
            self.wait_calls.append((after_sequence, timeout))
            return packets.pop(0)

        def read_latest(self):
            raise AssertionError("tracking capture must wait for a new sequence")

        def close(self) -> None:
            self.close_calls += 1

    client = Client()
    capture = BrokerFrameCapture(lambda: client, wait_for_new_frame=True)

    first_ok, first = capture.read()
    second_ok, second = capture.read()
    first[0, 0, 0] = 255
    capture.release()
    capture.release()

    assert first_ok and second_ok
    assert client.wait_calls == [(0, 0.1), (7, 0.1)]
    assert int(first_packet.as_array(copy=False)[0, 0, 0]) == 17
    assert int(second[0, 0, 0]) == 19
    assert packets == []
    assert client.close_calls == 1


def test_tracking_capture_treats_new_frame_wait_timeout_as_empty_read():
    class Client:
        def __init__(self) -> None:
            self.read_calls = 0

        def wait_for_frame(self, *, after_sequence: int, timeout: float) -> FramePacket:
            assert after_sequence == 0
            assert timeout == 0.1
            raise TimeoutError("no frame yet")

        def read_latest(self):
            self.read_calls += 1
            return _packet(1, 1)

    client = Client()
    capture = BrokerFrameCapture(lambda: client, wait_for_new_frame=True)

    assert capture.read() == (False, None)
    assert client.read_calls == 0


def test_tracking_capture_does_not_advance_sequence_when_private_copy_fails():
    class CopyFailure:
        def copy(self):
            raise RuntimeError("private copy failed")

    class FailingPacket:
        sequence = 7

        @staticmethod
        def as_array(*, copy: bool):
            assert copy is False
            return CopyFailure()

    responses = [FailingPacket(), _packet(7, 29)]

    class Client:
        def __init__(self) -> None:
            self.wait_calls: list[int] = []

        def wait_for_frame(self, *, after_sequence: int, timeout: float):
            assert timeout == 0.1
            self.wait_calls.append(after_sequence)
            return responses.pop(0)

    client = Client()
    capture = BrokerFrameCapture(lambda: client, wait_for_new_frame=True)

    with pytest.raises(ProjectXsIntegrationError, match="private copy failed") as error:
        capture.read()
    ok, frame = capture.read()

    assert isinstance(error.value.__cause__, RuntimeError)
    assert ok
    assert int(frame[0, 0, 0]) == 29
    assert client.wait_calls == [0, 0]


def test_preview_capture_keeps_nonblocking_latest_frame_behavior():
    packet = _packet(11, 23)

    class Client:
        def __init__(self) -> None:
            self.wait_calls = 0
            self.read_calls = 0

        def wait_for_frame(self, **_kwargs):
            self.wait_calls += 1
            raise AssertionError("preview must not block waiting for a new frame")

        def read_latest(self) -> FramePacket:
            self.read_calls += 1
            return packet

    client = Client()
    capture = BrokerFrameCapture(lambda: client)

    ok, frame = capture.read()
    frame[0, 0, 0] = 255

    assert ok
    assert client.wait_calls == 0
    assert client.read_calls == 1
    assert int(packet.as_array(copy=False)[0, 0, 0]) == 23


@pytest.mark.parametrize(
    "tracking_name",
    ("_tracking_blink_controlled", "_tracking_poke_blink_controlled"),
)
def test_controlled_tracking_requests_new_broker_frames(monkeypatch, tmp_path, tracking_name):
    cv2_sentinel = object()
    opened: list[tuple[object, dict[str, object]]] = []

    class Video:
        def __init__(self) -> None:
            self.released = False

        def release(self) -> None:
            self.released = True

    video = Video()

    def open_capture_source(config, **kwargs):
        opened.append((config, kwargs))
        return video

    monkeypatch.setattr(project_xs_module, "_load_cv2", lambda: cv2_sentinel)
    monkeypatch.setattr(project_xs_module, "_open_capture_source", open_capture_source)
    config = BlinkCaptureConfig(
        eye_image_path=tmp_path / "eye.png",
        roi=(0, 0, 1, 1),
        blink_count=1,
        source="broker",
    )
    stop_checks = iter((False, True))
    tracking = getattr(project_xs_module, tracking_name)

    tracking(
        np.zeros((1, 1), dtype=np.uint8),
        config,
        should_stop=lambda: next(stop_checks),
        frame_callback=None,
        progress_callback=None,
        show_window=False,
    )

    assert len(opened) == 1
    opened_config, kwargs = opened[0]
    assert opened_config is config
    assert kwargs["cv2"] is cv2_sentinel
    assert kwargs["prefer_v4l"] is True
    assert kwargs["wait_for_new_frame"] is True
    assert video.released
