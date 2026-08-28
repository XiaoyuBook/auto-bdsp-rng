from __future__ import annotations

import base64
import json
import threading
from pathlib import Path

import cv2
import numpy as np
import pytest

from auto_bdsp_rng.automation.easycon import EasyConStatus
from auto_bdsp_rng.automation.easycon.native.image_labels import ImageSearchResult, SearchMethod
from auto_bdsp_rng.automation.easycon.native.device import (
    COMMAND_CHANGE_AMIIBO_INDEX,
    READY,
    MemoryTransport,
    SwitchReport,
)
from auto_bdsp_rng.automation.easycon.native_backend import (
    NativeEasyConBackend,
    NativeEasyConBusyError,
)


class FakeFrameClient:
    def __init__(self, frame: np.ndarray) -> None:
        self.frame = frame
        self.read_count = 0
        self.closed = False
        self.read_event = threading.Event()

    def read_array(self) -> np.ndarray:
        self.read_count += 1
        self.read_event.set()
        return self.frame.copy()

    def close(self) -> None:
        self.closed = True


class RecordingWaiter:
    def __init__(self) -> None:
        self.values: list[int] = []

    def wait(self, milliseconds: int, cancel_event=None) -> None:  # type: ignore[no-untyped-def]
        self.values.append(milliseconds)


def _backend(
    client: FakeFrameClient,
    *,
    waiter=None,  # type: ignore[no-untyped-def]
    image_results: list[ImageSearchResult] | None = None,
) -> NativeEasyConBackend:
    backend = NativeEasyConBackend(
        frame_client_factory=lambda: client,
        waiter=waiter,
        image_result_callback=None if image_results is None else image_results.append,
    )
    backend.connect("mock")
    return backend


def _write_label(path: Path, template: np.ndarray) -> None:
    ok, payload = cv2.imencode(".png", template)
    assert ok
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "searchMethod": int(SearchMethod.C_COEFF_NORMED),
                "ImgBase64": base64.b64encode(payload).decode("ascii"),
                "RangeX": 0,
                "RangeY": 0,
                "RangeWidth": 16,
                "RangeHeight": 12,
                "TargetX": 6,
                "TargetY": 5,
                "TargetWidth": int(template.shape[1]),
                "TargetHeight": int(template.shape[0]),
            }
        ),
        encoding="utf-8",
    )


def test_native_backend_keeps_serial_connected_and_requires_broker_for_every_script() -> None:
    client = FakeFrameClient(np.zeros((12, 16, 3), dtype=np.uint8))
    waiter = RecordingWaiter()
    backend = _backend(client, waiter=waiter)
    try:
        result = backend.run_script_text("WAIT 25", "wait.ecs")

        assert result.status is EasyConStatus.COMPLETED
        assert result.exit_code == 0
        assert result.port == "mock"
        assert client.read_count == 1
        assert client.closed
        assert waiter.values == [25]
        assert backend.connected_port == "mock"
        assert backend.status() is EasyConStatus.BRIDGE_CONNECTED
    finally:
        backend.close()


def test_native_backend_releases_controller_after_successful_script() -> None:
    client = FakeFrameClient(np.zeros((12, 16, 3), dtype=np.uint8))
    backend = _backend(client, waiter=RecordingWaiter())
    try:
        backend.stick_direction("left", "up", True)
        result = backend.run_script_text("A DOWN\nLS RIGHT", "legacy-recording.ecs")

        assert result.status is EasyConStatus.COMPLETED
        assert backend._device.report_history[-2] != SwitchReport()
        assert backend._device.get_report() == SwitchReport()
        assert backend._device.report_history[-1] == SwitchReport()
        assert backend._stick_directions == {"LS": set(), "RS": set()}
        assert backend.connected_port == "mock"
    finally:
        backend.close()


def test_native_backend_releases_controller_after_runtime_failure() -> None:
    client = FakeFrameClient(np.zeros((12, 16, 3), dtype=np.uint8))
    backend = _backend(client, waiter=RecordingWaiter())
    try:
        result = backend.run_script_text(
            "A DOWN\nLS RIGHT\n$value = 1 / 0",
            "failed-recording.ecs",
        )

        assert result.status is EasyConStatus.FAILED
        assert backend._device.get_report() == SwitchReport()
        assert backend._device.report_history[-1] == SwitchReport()
        assert backend.connected_port == "mock"
    finally:
        backend.close()


def test_native_backend_loads_original_il_from_script_directory(tmp_path: Path) -> None:
    rng = np.random.default_rng(20260825)
    frame = rng.integers(0, 256, size=(12, 16, 3), dtype=np.uint8)
    template = frame[5:8, 6:10].copy()
    _write_label(tmp_path / "ImgLabel" / "目标.IL", template)
    client = FakeFrameClient(frame)
    image_results: list[ImageSearchResult] = []
    backend = _backend(client, waiter=RecordingWaiter(), image_results=image_results)
    try:
        result = backend.run_script_text(
            "$score = @目标\nPRINT $score",
            "main.ecs",
            script_dir=tmp_path,
        )

        assert result.status is EasyConStatus.COMPLETED
        assert "100" in result.stdout
        assert client.read_count == 2
        assert image_results[-1].label_name == "目标"
        assert image_results[-1].match_rect == (6, 5, 4, 3)
    finally:
        backend.close()


def test_native_backend_reports_missing_il_as_compile_failure(tmp_path: Path) -> None:
    client = FakeFrameClient(np.zeros((12, 16, 3), dtype=np.uint8))
    backend = _backend(client, waiter=RecordingWaiter())
    try:
        result = backend.run_script_text("$score = @不存在", "main.ecs", script_dir=tmp_path)

        assert result.status is EasyConStatus.FAILED
        assert result.exit_code == 2
        assert "找不到搜图标签" in result.stderr
    finally:
        backend.close()


def test_native_backend_cancels_one_script_and_rejects_a_second() -> None:
    client = FakeFrameClient(np.zeros((12, 16, 3), dtype=np.uint8))
    backend = _backend(client)
    results = []
    thread = threading.Thread(
        target=lambda: results.append(backend.run_script_text("A DOWN\nFOR\nWAIT 10000\nNEXT", "long.ecs"))
    )
    try:
        thread.start()
        assert client.read_event.wait(1.0)
        with pytest.raises(NativeEasyConBusyError):
            backend.run_script_text("WAIT 1", "second.ecs")
        backend.stop_current_script()
        thread.join(2.0)

        assert not thread.is_alive()
        assert results[0].status is EasyConStatus.CANCELLED
        assert results[0].exit_code == 130
        assert backend._device.get_report() == SwitchReport()
        assert backend._device.report_history[-1] == SwitchReport()
        assert backend.connected_port == "mock"
    finally:
        if thread.is_alive():
            backend.stop_current_script()
            thread.join(2.0)
        backend.close()


def test_native_backend_rejects_gamepad_script_until_serial_is_connected() -> None:
    client = FakeFrameClient(np.zeros((12, 16, 3), dtype=np.uint8))
    backend = NativeEasyConBackend(frame_client_factory=lambda: client, waiter=RecordingWaiter())

    result = backend.run_script_text("A 50", "button.ecs")

    assert result.status is EasyConStatus.FAILED
    assert "连接伊机控串口" in result.stderr
    assert client.read_count == 0


def test_native_backend_requires_serial_and_sends_amiibo_for_amiibo_only_script() -> None:
    disconnected_client = FakeFrameClient(np.zeros((12, 16, 3), dtype=np.uint8))
    disconnected = NativeEasyConBackend(
        frame_client_factory=lambda: disconnected_client,
        waiter=RecordingWaiter(),
    )

    try:
        rejected = disconnected.run_script_text("AMIIBO 3", "amiibo.ecs")
        assert rejected.status is EasyConStatus.FAILED
        assert "连接伊机控串口" in rejected.stderr
        assert disconnected_client.read_count == 0
    finally:
        disconnected.close()

    connected_client = FakeFrameClient(np.zeros((12, 16, 3), dtype=np.uint8))
    connected = _backend(connected_client, waiter=RecordingWaiter())
    try:
        transport = connected._device.transport
        assert isinstance(transport, MemoryTransport)

        result = connected.run_script_text("AMIIBO 3", "amiibo.ecs")

        assert result.status is EasyConStatus.COMPLETED
        assert bytes((READY, 3, COMMAND_CHANGE_AMIIBO_INDEX)) in transport.writes
    finally:
        connected.close()
