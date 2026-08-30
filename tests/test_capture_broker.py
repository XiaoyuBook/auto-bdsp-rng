from __future__ import annotations

import ctypes
import json
import subprocess
import struct
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest

import auto_bdsp_rng.capture_broker as broker_module
from auto_bdsp_rng.capture_broker import (
    CAPTURE_API_MSMF,
    DEFAULT_CAPTURE_API,
    GLOBAL_HEADER_SIZE,
    LEGACY_MANIFEST_ENVIRONMENT_VARIABLE,
    MANIFEST_ENVIRONMENT_VARIABLE,
    SLOT_HEADER_SIZE,
    BrokerError,
    BrokerManifest,
    BrokerManifestError,
    BrokerState,
    BrokerUnavailableError,
    CaptureBroker,
    CaptureBrokerClient,
    CaptureOpenError,
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


def _wait_for_manifest_state(
    manifest_path: Path,
    state: BrokerState,
    *,
    timeout: float = 30.0,
    broker_process: subprocess.Popen[bytes] | subprocess.Popen[str] | None = None,
    owner_process: subprocess.Popen[bytes] | None = None,
) -> BrokerManifest:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            manifest = BrokerManifest.load(manifest_path)
        except BrokerError:
            pass
        else:
            if manifest.state is state:
                return manifest
        time.sleep(0.025)
    diagnostics = ""
    if broker_process is not None:
        broker_returncode = broker_process.poll()
        if broker_returncode is None:
            broker_process.kill()
        try:
            _stdout, stderr = broker_process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            stderr = "<communicate timed out>"
        diagnostics = (
            f"; broker_returncode={broker_process.returncode!r}"
            f"; broker_stderr={stderr!r}"
        )
    if owner_process is not None:
        diagnostics += f"; owner_returncode={owner_process.poll()!r}"
    pytest.fail(f"standalone Broker did not reach {state.name}{diagnostics}")


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
            parent_pid=456,
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
            failure_message="test failure",
        )
        manifest.write()

        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert raw["state"] == "starting"
        assert raw["state_code"] == 1
        assert raw["header_size"] == 64
        assert raw["slot_header_size"] == 64
        assert raw["slot_size"] == 64 + 4 * 3 * 2
        assert raw["parent_pid"] == 456
        assert raw["failure_message"] == "test failure"
        assert BrokerManifest.load(manifest_path) == manifest

        del raw["parent_pid"]
        del raw["failure_message"]
        legacy = BrokerManifest.from_dict(raw, source=manifest_path)
        assert legacy.parent_pid == 0
        assert legacy.failure_message == ""

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


def test_manifest_rejects_negative_parent_pid(tmp_path: Path):
    ring = FrameRing.create(width=4, height=2, slot_count=3)
    try:
        manifest = BrokerManifest(
            schema_version=PROTOCOL_VERSION,
            protocol=PROTOCOL_NAME,
            session_id="bad-parent",
            pid=123,
            state=BrokerState.RUNNING,
            mapping_name=ring._shm.name,
            manifest_path=str(tmp_path / "capture.json"),
            control_path=str(tmp_path / "capture.stop.json"),
            header_size=GLOBAL_HEADER_SIZE,
            slot_header_size=SLOT_HEADER_SIZE,
            slot_count=ring.slot_count,
            slot_size=ring.slot_size,
            width=ring.width,
            height=ring.height,
            stride=ring.stride,
            pixel_format="BGR24",
        )
        raw = manifest.to_dict()
        raw["parent_pid"] = -1

        with pytest.raises(BrokerManifestError, match="parent_pid"):
            BrokerManifest.from_dict(raw)
    finally:
        ring.close(unlink=True)


def test_broker_mutex_name_uses_normalized_manifest_path(tmp_path: Path):
    direct = tmp_path / "capture_broker.json"
    equivalent = tmp_path / "unused" / ".." / "capture_broker.json"

    assert broker_module._broker_mutex_name(direct) == broker_module._broker_mutex_name(equivalent)


def test_running_manifest_publish_retries_a_transient_replace_failure(tmp_path: Path, monkeypatch):
    original_write = BrokerManifest.write
    failed_once = False

    def flaky_write(manifest, path=None):
        nonlocal failed_once
        if manifest.state is BrokerState.RUNNING and not failed_once:
            failed_once = True
            raise PermissionError("transient sharing violation")
        return original_write(manifest, path)

    monkeypatch.setattr(BrokerManifest, "write", flaky_write)
    broker = _broker(
        tmp_path,
        FakeCapture([_frame(33)], repeat=True, read_delay=0.002),
        frame_timeout=0.2,
    )
    try:
        assert broker.start()
        assert failed_once
        assert BrokerManifest.load(broker.manifest_path).state is BrokerState.RUNNING
    finally:
        broker.stop()


def test_start_timeout_rechecks_a_running_state_during_manifest_publish(tmp_path: Path, monkeypatch):
    original_write = BrokerManifest.write
    delayed = False

    def delayed_write(manifest, path=None):
        nonlocal delayed
        if manifest.state is BrokerState.RUNNING and not delayed:
            delayed = True
            time.sleep(0.05)
        return original_write(manifest, path)

    monkeypatch.setattr(BrokerManifest, "write", delayed_write)
    broker = _broker(
        tmp_path,
        FakeCapture([_frame(35)], repeat=True),
        frame_timeout=0.2,
    )
    try:
        assert broker.start(timeout=0.01)
        assert delayed
        assert BrokerManifest.load(broker.manifest_path).state is BrokerState.RUNNING
    finally:
        broker.stop()


def test_failed_state_cannot_regress_to_running(tmp_path: Path):
    broker = _broker(tmp_path, FakeCapture([]))
    ring = FrameRing.create(width=4, height=2, slot_count=3, state=BrokerState.STARTING)
    broker._ring = ring
    broker._state = BrokerState.STARTING
    try:
        broker._fail(CaptureOpenError("startup failed"))

        assert broker.state is BrokerState.FAILED
        assert broker._set_state(BrokerState.RUNNING) is False
        assert broker.state is BrokerState.FAILED
        assert BrokerManifest.load(broker.manifest_path).state is BrokerState.FAILED
    finally:
        broker._ring = None
        ring.close(unlink=True)
        try:
            broker.manifest_path.unlink()
        except OSError:
            pass


def test_restart_waits_until_previous_stop_releases_lifecycle_mutex(tmp_path: Path, monkeypatch):
    broker = _broker(
        tmp_path,
        FakeCapture([_frame(34)], repeat=True, read_delay=0.002),
        frame_timeout=0.2,
    )
    release_entered = threading.Event()
    allow_release = threading.Event()
    restart_finished = threading.Event()
    stop_results: list[object] = []
    restart_results: list[object] = []
    original_release = broker._lifecycle_mutex.release
    should_block = True

    def blocking_release() -> None:
        nonlocal should_block
        if should_block:
            should_block = False
            release_entered.set()
            if not allow_release.wait(2):
                raise TimeoutError("test did not release lifecycle mutex")
        original_release()

    def stop_broker() -> None:
        try:
            stop_results.append(broker.stop())
        except BaseException as exc:
            stop_results.append(exc)

    def restart_broker() -> None:
        try:
            restart_results.append(broker.start())
        except BaseException as exc:
            restart_results.append(exc)
        finally:
            restart_finished.set()

    monkeypatch.setattr(broker._lifecycle_mutex, "release", blocking_release)
    try:
        assert broker.start()
        stop_thread = threading.Thread(target=stop_broker)
        stop_thread.start()
        assert release_entered.wait(1)

        restart_thread = threading.Thread(target=restart_broker)
        restart_thread.start()
        assert not restart_finished.wait(0.05)

        allow_release.set()
        stop_thread.join(timeout=2)
        restart_thread.join(timeout=2)

        assert not stop_thread.is_alive()
        assert not restart_thread.is_alive()
        assert stop_results == [True]
        assert restart_results == [True]
    finally:
        allow_release.set()
        broker.stop()


def test_windows_process_guard_requests_only_synchronize_access(monkeypatch):
    calls: list[tuple[int, bool, int]] = []

    class Function:
        def __init__(self, result):
            self.result = result
            self.argtypes = None
            self.restype = None

        def __call__(self, *args):
            if len(args) == 3:
                calls.append((int(args[0]), bool(args[1]), int(args[2])))
            return self.result

    class Kernel32:
        OpenProcess = Function(123)
        WaitForSingleObject = Function(258)
        CloseHandle = Function(1)

    kernel32 = Kernel32()
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: kernel32, raising=False)

    handle, loaded_kernel32, status = broker_module._open_windows_process_wait_handle(2468)
    try:
        assert handle == 123
        assert loaded_kernel32 is kernel32
        assert status is broker_module._ProcessStatus.ALIVE
        assert calls == [(0x00100000, False, 2468)]
    finally:
        kernel32.CloseHandle(handle)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows named mutex behavior")
def test_mutex_release_failure_keeps_local_owner_reserved(tmp_path: Path, monkeypatch):
    class Function:
        def __init__(self, result):
            self.result = result
            self.argtypes = None
            self.restype = None

        def __call__(self, *_args):
            return self.result

    class Kernel32:
        CreateMutexW = Function(123)
        WaitForSingleObject = Function(0)
        ReleaseMutex = Function(0)
        CloseHandle = Function(1)

    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: Kernel32(), raising=False)
    first = broker_module._BrokerLifetimeMutex(tmp_path / "capture_broker.json")
    second = broker_module._BrokerLifetimeMutex(tmp_path / "capture_broker.json")
    try:
        first.acquire()

        with pytest.raises(BrokerError, match="无法释放"):
            first.release()
        with pytest.raises(BrokerError, match="无法释放"):
            first.acquire()
        with pytest.raises(broker_module.BrokerAlreadyRunningError, match="启动或运行"):
            second.acquire()
    finally:
        first._release_error = None
        first._release_local_name()


def test_unknown_process_status_is_not_treated_as_dead(monkeypatch):
    monkeypatch.setattr(
        broker_module,
        "_process_status",
        lambda _pid: broker_module._ProcessStatus.UNKNOWN,
    )

    assert broker_module._pid_is_alive(2468)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows parent HANDLE behavior")
def test_parent_guard_keeps_unknown_parent_status_conservative(monkeypatch):
    monkeypatch.setattr(
        broker_module,
        "_open_windows_process_wait_handle",
        lambda _pid: (None, None, broker_module._ProcessStatus.UNKNOWN),
    )
    guard = broker_module._ParentProcessGuard(2468)
    try:
        assert guard.status() is broker_module._ProcessStatus.UNKNOWN
        assert guard.is_alive()
    finally:
        guard.close()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows named mutex behavior")
def test_broker_mutex_is_held_until_successful_stop(tmp_path: Path):
    manifest_path = tmp_path / "capture_broker.json"
    first = CaptureBroker(
        3,
        700,
        manifest_path=manifest_path,
        capture_factory=lambda _index, _api: FakeCapture(
            [_frame(41)], repeat=True, read_delay=0.002
        ),
        width=4,
        height=2,
        slot_count=3,
        first_frame_timeout=1.0,
        frame_timeout=1.0,
    )
    second = CaptureBroker(
        3,
        700,
        manifest_path=manifest_path,
        capture_factory=lambda _index, _api: FakeCapture(
            [_frame(42)], repeat=True, read_delay=0.002
        ),
        width=4,
        height=2,
        slot_count=3,
        first_frame_timeout=1.0,
        frame_timeout=1.0,
    )
    try:
        assert first.start()
        manifest_path.unlink()

        with pytest.raises(broker_module.BrokerAlreadyRunningError, match="启动或运行"):
            second.start()

        stop_results: list[object] = []

        def stop_first() -> None:
            try:
                stop_results.append(first.stop())
            except BaseException as exc:
                stop_results.append(exc)

        stop_thread = threading.Thread(target=stop_first)
        stop_thread.start()
        stop_thread.join(timeout=2)

        assert not stop_thread.is_alive()
        assert stop_results == [True]
        assert second.start()
    finally:
        first.stop()
        second.stop()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows named mutex behavior")
def test_two_broker_processes_cannot_start_for_the_same_manifest(tmp_path: Path):
    manifest_path = tmp_path / "capture_broker.json"
    go_path = tmp_path / "go"
    broker_code = "\n".join(
        (
            "import pathlib",
            "import sys",
            "import time",
            "import numpy as np",
            "from auto_bdsp_rng.capture_broker import (",
            "    BROKER_ALREADY_RUNNING_EXIT_CODE, BrokerAlreadyRunningError,",
            "    CaptureBroker, FakeCapture,",
            ")",
            "manifest = pathlib.Path(sys.argv[1])",
            "ready = pathlib.Path(sys.argv[2])",
            "go = pathlib.Path(sys.argv[3])",
            "frame = np.zeros((2, 4, 3), dtype=np.uint8)",
            "capture = FakeCapture([frame], repeat=True, read_delay=0.002)",
            "broker = CaptureBroker(3, 700, manifest_path=manifest,",
            "    capture_factory=lambda _index, _api: capture, width=4, height=2,",
            "    slot_count=3, first_frame_timeout=1.0, frame_timeout=1.0)",
            "ready.write_text('ready', encoding='utf-8')",
            "while not go.exists():",
            "    time.sleep(0.005)",
            "try:",
            "    started = broker.start()",
            "except BrokerAlreadyRunningError:",
            "    print('locked', flush=True)",
            "    raise SystemExit(BROKER_ALREADY_RUNNING_EXIT_CODE)",
            "if not started:",
            "    raise SystemExit(2)",
            "print('running', flush=True)",
            "time.sleep(1.0)",
            "broker.stop()",
        )
    )
    processes: list[subprocess.Popen[str]] = []
    try:
        for index in range(2):
            ready_path = tmp_path / f"ready-{index}"
            processes.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        "-c",
                        broker_code,
                        str(manifest_path),
                        str(ready_path),
                        str(go_path),
                    ],
                    cwd=Path(__file__).resolve().parents[1],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            )

        deadline = time.monotonic() + 30
        ready_paths = [tmp_path / "ready-0", tmp_path / "ready-1"]
        while time.monotonic() < deadline and not all(path.exists() for path in ready_paths):
            time.sleep(0.025)
        assert all(path.exists() for path in ready_paths)
        go_path.write_text("go", encoding="utf-8")

        results = [process.communicate(timeout=10) for process in processes]
        exit_codes = [process.returncode for process in processes]
        outputs = [stdout.strip() for stdout, _stderr in results]

        assert sorted(exit_codes) == [0, broker_module.BROKER_ALREADY_RUNNING_EXIT_CODE]
        assert sorted(outputs) == ["locked", "running"]
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=2)


def test_standalone_broker_releases_capture_when_parent_exits(tmp_path: Path, monkeypatch):
    capture = FakeCapture([_frame(31)], repeat=True, read_delay=0.002)
    guard_closed: list[bool] = []

    class DeadParentGuard:
        def __init__(self, pid: int) -> None:
            assert pid == 2468

        def status(self):
            return broker_module._ProcessStatus.DEAD

        def close(self) -> None:
            guard_closed.append(True)

    monkeypatch.setattr(broker_module, "_ParentProcessGuard", DeadParentGuard)
    broker = CaptureBroker(
        3,
        700,
        manifest_path=tmp_path / "capture_broker.json",
        capture_factory=lambda _index, _api: capture,
        width=4,
        height=2,
        slot_count=3,
        first_frame_timeout=0.2,
        frame_timeout=0.2,
        parent_pid=2468,
        parent_poll_interval=0.025,
        parent_shutdown_timeout=0.1,
    )

    assert broker.serve_forever()
    assert capture.released
    assert broker.state is BrokerState.STOPPED
    assert not broker.manifest_path.exists()
    assert guard_closed == [True]


def test_standalone_broker_preserves_capture_open_failure_for_controller(tmp_path: Path):
    capture = FakeCapture([], open_result=False)
    broker = _broker(tmp_path, capture)
    try:
        assert broker.serve_forever() is False

        manifest = BrokerManifest.load(broker.manifest_path)
        assert manifest.state is BrokerState.FAILED
        assert "可能正被其他程序占用" in manifest.failure_message
        assert capture.released
    finally:
        broker.stop()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows parent HANDLE behavior")
def test_standalone_broker_process_exits_when_its_gui_owner_dies(tmp_path: Path):
    manifest_path = tmp_path / "capture_broker.json"
    owner = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    broker_code = "\n".join(
        (
            "import sys",
            "import numpy as np",
            "from auto_bdsp_rng.capture_broker import CaptureBroker, FakeCapture",
            "frame = np.zeros((2, 4, 3), dtype=np.uint8)",
            "capture = FakeCapture([frame], repeat=True, read_delay=0.002)",
            "broker = CaptureBroker(3, 700, manifest_path=sys.argv[1], ",
            "    capture_factory=lambda _index, _api: capture, width=4, height=2, ",
            "    slot_count=3, first_frame_timeout=1.0, frame_timeout=1.0, ",
            "    parent_pid=int(sys.argv[2]), parent_poll_interval=0.025, ",
            "    parent_shutdown_timeout=0.2)",
            "raise SystemExit(0 if broker.serve_forever() else 2)",
        )
    )
    broker_process = subprocess.Popen(
        [sys.executable, "-c", broker_code, str(manifest_path), str(owner.pid)],
        cwd=Path(__file__).resolve().parents[1],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        running_manifest = _wait_for_manifest_state(
            manifest_path,
            BrokerState.RUNNING,
            broker_process=broker_process,
            owner_process=owner,
        )

        owner.terminate()
        owner.wait(timeout=2)

        assert broker_process.wait(timeout=5) == 0
        assert broker_module._process_status(running_manifest.pid) is broker_module._ProcessStatus.DEAD
        assert not manifest_path.exists()
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=2)
        if broker_process.poll() is None:
            broker_process.kill()
            broker_process.wait(timeout=2)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows parent HANDLE behavior")
def test_standalone_broker_hard_exits_when_capture_read_is_blocked(tmp_path: Path):
    manifest_path = tmp_path / "capture_broker.json"
    owner = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    broker_code = "\n".join(
        (
            "import sys",
            "import threading",
            "import numpy as np",
            "from auto_bdsp_rng.capture_broker import CaptureBroker",
            "class BlockingCapture:",
            "    def __init__(self):",
            "        self.read_count = 0",
            "    def open(self, _device_index, _capture_api):",
            "        return True",
            "    def set_properties(self, _width, _height, _fourcc, _fps):",
            "        return None",
            "    def read(self):",
            "        self.read_count += 1",
            "        if self.read_count == 1:",
            "            return True, np.zeros((2, 4, 3), dtype=np.uint8)",
            "        threading.Event().wait(60)",
            "        return False, None",
            "    def release(self):",
            "        return None",
            "capture = BlockingCapture()",
            "broker = CaptureBroker(3, 700, manifest_path=sys.argv[1],",
            "    capture_factory=lambda _index, _api: capture, width=4, height=2,",
            "    slot_count=3, first_frame_timeout=1.0, frame_timeout=1.0,",
            "    parent_pid=int(sys.argv[2]), parent_poll_interval=0.025,",
            "    parent_shutdown_timeout=0.1)",
            "raise SystemExit(0 if broker.serve_forever() else 2)",
        )
    )
    broker_process = subprocess.Popen(
        [sys.executable, "-c", broker_code, str(manifest_path), str(owner.pid)],
        cwd=Path(__file__).resolve().parents[1],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    broker_pid: int | None = None
    try:
        running_manifest = _wait_for_manifest_state(
            manifest_path,
            BrokerState.RUNNING,
            broker_process=broker_process,
            owner_process=owner,
        )
        broker_pid = running_manifest.pid

        owner.terminate()
        owner.wait(timeout=2)

        try:
            exit_code = broker_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            broker_process.kill()
            _stdout, stderr = broker_process.communicate(timeout=2)
            pytest.fail(f"blocked-read Broker remained alive after owner exit: {stderr}")
        assert exit_code == 0
        assert broker_pid is not None
        assert broker_module._process_status(broker_pid) is broker_module._ProcessStatus.DEAD

        replacement = CaptureBroker(
            3,
            700,
            manifest_path=manifest_path,
            capture_factory=lambda _index, _api: FakeCapture(
                [_frame(52)], repeat=True, read_delay=0.002
            ),
            width=4,
            height=2,
            slot_count=3,
            first_frame_timeout=1.0,
            frame_timeout=1.0,
        )
        try:
            assert replacement.start()
        finally:
            replacement.stop()
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=2)
        if broker_process.poll() is None:
            broker_process.kill()
            broker_process.wait(timeout=2)


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
