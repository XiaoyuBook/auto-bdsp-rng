from __future__ import annotations

import json
import subprocess
import struct
import sys
import time
from pathlib import Path

import numpy as np
import pytest

from auto_bdsp_rng.capture_broker import (
    CAPTURE_API_MSMF,
    DEFAULT_CAPTURE_API,
    GLOBAL_HEADER_SIZE,
    LEGACY_MANIFEST_ENVIRONMENT_VARIABLE,
    MANIFEST_ENVIRONMENT_VARIABLE,
    SLOT_HEADER_SIZE,
    BrokerManifest,
    BrokerManifestError,
    BrokerState,
    BrokerUnavailableError,
    CaptureBroker,
    CaptureBrokerClient,
    FakeCapture,
    FrameRing,
    PIXEL_FORMAT_BGR24,
    PROTOCOL_MAGIC,
    PROTOCOL_NAME,
    PROTOCOL_VERSION,
    default_manifest_path,
)


def _frame(value: int, *, width: int = 4, height: int = 2) -> np.ndarray:
    return np.full((height, width, 3), value, dtype=np.uint8)


def _broker(
    tmp_path: Path,
    capture: FakeCapture,
    *,
    first_frame_timeout: float = 0.2,
    frame_timeout: float = 0.05,
) -> CaptureBroker:
    return CaptureBroker(
        3,
        700,
        manifest_path=tmp_path / "capture_broker.json",
        capture_factory=lambda _index, _api: capture,
        width=4,
        height=2,
        slot_count=3,
        first_frame_timeout=first_frame_timeout,
        frame_timeout=frame_timeout,
        poll_interval=0.001,
    )


def _client_for_ring(
    tmp_path: Path,
    ring: FrameRing,
    *,
    frame_timeout: float,
) -> CaptureBrokerClient:
    manifest_path = tmp_path / "capture_broker.json"
    manifest = BrokerManifest(
        schema_version=PROTOCOL_VERSION,
        protocol=PROTOCOL_NAME,
        session_id="test-session",
        pid=123,
        state=BrokerState.RUNNING,
        mapping_name=ring._shm.name,
        manifest_path=str(manifest_path),
        control_path=str(tmp_path / "capture_broker.stop.json"),
        header_size=GLOBAL_HEADER_SIZE,
        slot_header_size=SLOT_HEADER_SIZE,
        slot_count=ring.slot_count,
        slot_size=ring.slot_size,
        width=ring.width,
        height=ring.height,
        stride=ring.stride,
        pixel_format="BGR24",
        capture={"device_index": 3, "api": 700, "fourcc": "MJPG", "fps": 30.0},
        frame_timeout_seconds=frame_timeout,
    )
    return CaptureBrokerClient(manifest, FrameRing.open_from_manifest(manifest), manifest_path)


def test_capture_broker_defaults_to_media_foundation(tmp_path: Path):
    capture = FakeCapture([_frame(1)])
    broker = CaptureBroker(
        0,
        manifest_path=tmp_path / "capture_broker.json",
        capture_factory=lambda _index, _api: capture,
        width=4,
        height=2,
    )

    assert DEFAULT_CAPTURE_API == CAPTURE_API_MSMF == 1400
    assert broker.capture_api == CAPTURE_API_MSMF


def test_wire_layout_and_manifest_are_cross_language_json(tmp_path: Path):
    assert PROTOCOL_MAGIC == b"ABRNGFB1"
    assert GLOBAL_HEADER_SIZE == 64
    assert SLOT_HEADER_SIZE == 64
    assert PIXEL_FORMAT_BGR24 == 1
    assert dict(BrokerState.__members__) == {
        "STARTING": BrokerState.STARTING,
        "RUNNING": BrokerState.RUNNING,
        "FAILED": BrokerState.FAILED,
        "STOPPED": BrokerState.STOPPED,
    }

    ring = FrameRing.create(width=4, height=2, slot_count=3)
    manifest_path = tmp_path / "capture.json"
    try:
        manifest = BrokerManifest(
            schema_version=PROTOCOL_VERSION,
            protocol=PROTOCOL_NAME,
            session_id="test-session",
            pid=123,
            state=BrokerState.STARTING,
            mapping_name=ring._shm.name,
            manifest_path=str(manifest_path),
            control_path=str(tmp_path / "capture.stop.json"),
            header_size=GLOBAL_HEADER_SIZE,
            slot_header_size=SLOT_HEADER_SIZE,
            slot_count=ring.slot_count,
            slot_size=ring.slot_size,
            width=ring.width,
            height=ring.height,
            stride=ring.stride,
            pixel_format="BGR24",
            capture={"device_index": 3, "api": 700, "fourcc": "MJPG", "fps": 30.0},
            updated_at_ns=1,
        )
        manifest.write()

        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert raw["state"] == "starting"
        assert raw["state_code"] == 1
        assert raw["header_size"] == 64
        assert raw["slot_header_size"] == 64
        assert raw["slot_size"] == 64 + 4 * 3 * 2
        assert BrokerManifest.load(manifest_path) == manifest

        packet = ring.write(_frame(11), timestamp_ns=123456)
        header = ring.snapshot_header()
        assert header["latest_sequence"] == packet.sequence == 1
        assert header["latest_slot"] == 1
        slot_offset = GLOBAL_HEADER_SIZE + header["latest_slot"] * ring.slot_size
        slot = struct.unpack_from("<QQQIIIIQ", ring._shm.buf, slot_offset)
        assert slot == (
            2,  # committed token = sequence << 1
            1,
            123456,
            4 * 3 * 2,
            4,
            2,
            12,
            2,
        )
    finally:
        ring.close(unlink=True)


def test_manifest_discovery_prefers_canonical_environment_variable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    canonical = tmp_path / "canonical.json"
    legacy = tmp_path / "legacy.json"
    monkeypatch.setenv(LEGACY_MANIFEST_ENVIRONMENT_VARIABLE, str(legacy))
    monkeypatch.setenv(MANIFEST_ENVIRONMENT_VARIABLE, str(canonical))

    assert default_manifest_path() == canonical

    monkeypatch.delenv(MANIFEST_ENVIRONMENT_VARIABLE)
    assert default_manifest_path() == legacy


def test_manifest_rejects_invalid_timeout_fields_as_protocol_errors(tmp_path: Path):
    manifest_path = tmp_path / "invalid.json"
    ring = FrameRing.create(width=4, height=2, slot_count=3)
    try:
        raw = {
            "schema_version": PROTOCOL_VERSION,
            "protocol": PROTOCOL_NAME,
            "session_id": "bad-timeout",
            "pid": 123,
            "state": "running",
            "mapping_name": ring._shm.name,
            "header_size": GLOBAL_HEADER_SIZE,
            "slot_header_size": SLOT_HEADER_SIZE,
            "slot_count": ring.slot_count,
            "slot_size": ring.slot_size,
            "width": ring.width,
            "height": ring.height,
            "stride": ring.stride,
            "pixel_format": "BGR24",
            "timeouts": {"first_frame_seconds": "broken", "frame_seconds": 1.0},
        }
        manifest_path.write_text(json.dumps(raw), encoding="utf-8")

        with pytest.raises(BrokerManifestError, match="manifest 字段无效"):
            BrokerManifest.load(manifest_path)
    finally:
        ring.close(unlink=True)


def test_frame_ring_clients_read_latest_complete_frame_independently(tmp_path: Path):
    capture = FakeCapture([_frame(23)], repeat=True, read_delay=0.002)
    broker = _broker(tmp_path, capture)
    clients: list[CaptureBrokerClient] = []
    try:
        assert broker.start()
        clients = [
            CaptureBrokerClient.connect(broker.manifest_path, require_running=True),
            CaptureBrokerClient.connect(broker.manifest_path, require_running=True),
        ]
        first = clients[0].wait_for_frame(timeout=0.2).as_array()
        second = clients[1].read_array()

        assert second is not None
        np.testing.assert_array_equal(first, _frame(23))
        np.testing.assert_array_equal(second, _frame(23))
        first[:] = 99
        np.testing.assert_array_equal(clients[1].read_array(), _frame(23))
        assert capture.open_calls == 1
        assert capture.properties == (4, 2, "MJPG", 30.0)
    finally:
        for client in clients:
            client.close()
        broker.stop()
    assert capture.released


def test_multiple_processes_read_the_same_broker_frame_independently(tmp_path: Path):
    capture = FakeCapture([_frame(61)], repeat=True, read_delay=0.002)
    broker = _broker(tmp_path, capture)
    readers: list[subprocess.Popen[str]] = []
    reader_code = """
import json
import sys

from auto_bdsp_rng.capture_broker import CaptureBrokerClient

client = CaptureBrokerClient.connect(sys.argv[1], require_running=True, require_live_pid=True)
try:
    frame = client.wait_for_frame(timeout=2.0).as_array()
    print(json.dumps({"shape": list(frame.shape), "value": int(frame[0, 0, 0])}))
finally:
    client.close()
"""
    try:
        assert broker.start()
        readers = [
            subprocess.Popen(
                [sys.executable, "-c", reader_code, str(broker.manifest_path)],
                cwd=Path.cwd(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _ in range(2)
        ]
        results = []
        for reader in readers:
            stdout, stderr = reader.communicate(timeout=10.0)
            assert reader.returncode == 0, stderr
            results.append(json.loads(stdout))

        assert results == [
            {"shape": [2, 4, 3], "value": 61},
            {"shape": [2, 4, 3], "value": 61},
        ]
        assert broker.state is BrokerState.RUNNING
        assert capture.open_calls == 1
    finally:
        for reader in readers:
            if reader.poll() is None:
                reader.kill()
                reader.wait(timeout=2.0)
        broker.stop()


def test_static_pixels_are_new_frames_not_a_health_failure(tmp_path: Path):
    capture = FakeCapture([_frame(7)], repeat=True, read_delay=0.002)
    broker = _broker(tmp_path, capture, frame_timeout=0.03)
    try:
        assert broker.start()
        assert broker.ring is not None
        initial_sequence = broker.ring.latest_sequence
        time.sleep(0.06)
        assert broker.state is BrokerState.RUNNING
        assert broker.ring.latest_sequence > initial_sequence
    finally:
        broker.stop()


def test_successful_capture_reads_are_not_limited_by_failure_poll_interval(tmp_path: Path):
    capture = FakeCapture([_frame(9)], repeat=True)
    broker = CaptureBroker(
        3,
        1400,
        manifest_path=tmp_path / "capture_broker.json",
        capture_factory=lambda _index, _api: capture,
        width=4,
        height=2,
        slot_count=3,
        first_frame_timeout=0.2,
        frame_timeout=0.2,
        poll_interval=0.1,
    )
    try:
        assert broker.start()
        assert broker.ring is not None
        first_sequence = broker.ring.latest_sequence
        deadline = time.perf_counter() + 0.05
        while broker.ring.latest_sequence < first_sequence + 3 and time.perf_counter() < deadline:
            time.sleep(0.002)

        assert broker.ring.latest_sequence >= first_sequence + 3
    finally:
        broker.stop()


def test_client_uses_committed_packet_when_global_heartbeat_is_invalid(tmp_path: Path):
    ring = FrameRing.create(width=4, height=2, slot_count=3, state=BrokerState.RUNNING)
    client: CaptureBrokerClient | None = None
    try:
        expected = ring.write(_frame(17), timestamp_ns=time.monotonic_ns())
        ring.heartbeat(0)
        client = _client_for_ring(tmp_path, ring, frame_timeout=1.0)

        packet = client.read_latest()
        waited = client.wait_for_frame(after_sequence=0, timeout=0.05)

        assert packet is not None
        assert packet.sequence == waited.sequence == expected.sequence
        np.testing.assert_array_equal(packet.as_array(), _frame(17))
    finally:
        if client is not None:
            client.close()
        ring.close(unlink=True)


def test_client_rejects_stale_packet_even_when_global_heartbeat_is_fresh(tmp_path: Path):
    ring = FrameRing.create(width=4, height=2, slot_count=3, state=BrokerState.RUNNING)
    client: CaptureBrokerClient | None = None
    try:
        ring.write(_frame(18), timestamp_ns=time.monotonic_ns() - 1_000_000_000)
        ring.heartbeat(time.monotonic_ns())
        client = _client_for_ring(tmp_path, ring, frame_timeout=0.05)

        with pytest.raises(BrokerUnavailableError, match="没有新帧"):
            client.read_latest()
        with pytest.raises(BrokerUnavailableError, match="没有新帧"):
            client.wait_for_frame(after_sequence=0, timeout=0.05)
        assert client.read_latest(allow_unavailable=True) is not None
    finally:
        if client is not None:
            client.close()
        ring.close(unlink=True)


def test_wait_for_frame_does_not_copy_payload_while_sequence_is_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    ring = FrameRing.create(width=4, height=2, slot_count=3, state=BrokerState.RUNNING)
    client: CaptureBrokerClient | None = None
    try:
        expected = ring.write(_frame(19), timestamp_ns=time.monotonic_ns())
        client = _client_for_ring(tmp_path, ring, frame_timeout=1.0)
        payload_read_calls = 0
        original_read_latest = client._ring.read_latest

        def counted_read_latest():
            nonlocal payload_read_calls
            payload_read_calls += 1
            return original_read_latest()

        monkeypatch.setattr(client._ring, "read_latest", counted_read_latest)

        with pytest.raises(TimeoutError, match="等待共享视频源新帧超时"):
            client.wait_for_frame(
                after_sequence=expected.sequence,
                timeout=0.02,
                poll_interval=0.001,
            )

        assert payload_read_calls == 0
    finally:
        if client is not None:
            client.close()
        ring.close(unlink=True)


def test_wait_for_frame_reports_stale_packet_while_waiting_for_next_sequence(tmp_path: Path):
    ring = FrameRing.create(width=4, height=2, slot_count=3, state=BrokerState.RUNNING)
    client: CaptureBrokerClient | None = None
    try:
        expected = ring.write(_frame(20), timestamp_ns=time.monotonic_ns())
        client = _client_for_ring(tmp_path, ring, frame_timeout=0.03)

        initial = client.read_latest()
        assert initial is not None
        assert initial.sequence == expected.sequence
        with pytest.raises(BrokerUnavailableError, match="没有新帧"):
            client.wait_for_frame(
                after_sequence=expected.sequence,
                timeout=0.2,
                poll_interval=0.001,
            )
    finally:
        if client is not None:
            client.close()
        ring.close(unlink=True)


def test_client_does_not_report_stale_when_payload_read_is_delayed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    capture = FakeCapture([_frame(22)], repeat=True, read_delay=0.002)
    broker = _broker(tmp_path, capture, frame_timeout=0.03)
    client: CaptureBrokerClient | None = None
    try:
        assert broker.start()
        client = CaptureBrokerClient.connect(broker.manifest_path, require_running=True)
        original_read_latest = client._ring.read_latest

        def delayed_read_latest():
            packet = original_read_latest()
            time.sleep(0.06)
            return packet

        monkeypatch.setattr(client._ring, "read_latest", delayed_read_latest)

        packet = client.read_latest()
        waited = client.wait_for_frame(after_sequence=0, timeout=0.2)

        assert packet is not None
        assert waited.sequence > 0
        assert broker.state is BrokerState.RUNNING
    finally:
        if client is not None:
            client.close()
        broker.stop()


def test_client_rejects_committed_packet_with_future_timestamp(tmp_path: Path):
    ring = FrameRing.create(width=4, height=2, slot_count=3, state=BrokerState.RUNNING)
    client: CaptureBrokerClient | None = None
    try:
        ring.write(_frame(21), timestamp_ns=time.monotonic_ns() + 1_000_000_000)
        client = _client_for_ring(tmp_path, ring, frame_timeout=1.0)

        with pytest.raises(BrokerUnavailableError, match="没有新帧"):
            client.read_latest()
        with pytest.raises(BrokerUnavailableError, match="没有新帧"):
            client.wait_for_frame(after_sequence=0, timeout=0.05)
    finally:
        if client is not None:
            client.close()
        ring.close(unlink=True)


def test_first_frame_deadline_fails_and_releases_capture(tmp_path: Path):
    capture = FakeCapture([], read_delay=0.002)
    broker = _broker(tmp_path, capture, first_frame_timeout=0.03)
    try:
        assert broker.start() is False
        assert broker.wait(0.3)
        assert broker.state is BrokerState.FAILED
        assert capture.released
        assert BrokerManifest.load(broker.manifest_path).state is BrokerState.FAILED
    finally:
        broker.stop()


def test_runtime_one_second_contract_uses_configurable_health_deadline(tmp_path: Path):
    capture = FakeCapture([_frame(1), None], repeat=True, read_delay=0.002)
    broker = _broker(tmp_path, capture, frame_timeout=0.03)
    client: CaptureBrokerClient | None = None
    try:
        assert broker.start()
        client = CaptureBrokerClient.connect(broker.manifest_path, require_running=True)
        assert broker.wait(0.3)
        assert broker.state is BrokerState.FAILED
        assert capture.released
        with pytest.raises(BrokerUnavailableError, match="failed"):
            client.read_latest()
        # The old frame remains inspectable only through the explicit
        # diagnostic opt-in; normal consumers cannot silently reuse it.
        assert client.read_latest(allow_unavailable=True) is not None
    finally:
        if client is not None:
            client.close()
        broker.stop()


def test_client_rejects_old_frame_when_capture_read_itself_is_blocked(tmp_path: Path):
    class BlockingAfterFirstCapture(FakeCapture):
        def read(self):
            if self.read_calls == 0:
                return super().read()
            self.read_calls += 1
            time.sleep(0.12)
            return False, None

    capture = BlockingAfterFirstCapture([_frame(8)], repeat=True)
    broker = _broker(tmp_path, capture, frame_timeout=0.03)
    client: CaptureBrokerClient | None = None
    try:
        assert broker.start()
        client = CaptureBrokerClient.connect(broker.manifest_path, require_running=True)
        time.sleep(0.05)

        assert broker.state is BrokerState.RUNNING
        with pytest.raises(BrokerUnavailableError, match="没有新帧"):
            client.read_latest()
    finally:
        if client is not None:
            client.close()
        broker.stop()


def test_client_stop_request_stops_session_without_opening_capture_again(tmp_path: Path):
    capture = FakeCapture([_frame(42)], repeat=True, read_delay=0.002)
    broker = _broker(tmp_path, capture)
    client: CaptureBrokerClient | None = None
    try:
        assert broker.start()
        client = CaptureBrokerClient.connect(broker.manifest_path, require_running=True)
        client.request_stop()
        assert broker.wait(0.3)
        assert broker.state is BrokerState.STOPPED
        assert client.state is BrokerState.STOPPED
        assert capture.open_calls == 1
    finally:
        if client is not None:
            client.close()
        broker.stop()
