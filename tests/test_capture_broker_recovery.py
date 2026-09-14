from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import auto_bdsp_rng.capture_broker as broker_module
from auto_bdsp_rng.capture_broker import (
    BrokerManifest,
    BrokerState,
    CaptureBroker,
    CaptureBrokerClient,
    FakeCapture,
    FrameRing,
)
from auto_bdsp_rng.capture_broker_process import CaptureBrokerProcess, CaptureBrokerProcessError


@pytest.fixture
def broker_record(tmp_path):
    ring = FrameRing.create(width=4, height=2, slot_count=3)
    manifest = BrokerManifest(
        schema_version=broker_module.PROTOCOL_VERSION,
        protocol=broker_module.PROTOCOL_NAME,
        session_id="old-session",
        # Simulate a stale PID now belonging to an unrelated live process.
        pid=os.getpid(),
        parent_pid=2147483647,
        state=BrokerState.RUNNING,
        mapping_name=ring._shm.name,
        manifest_path=str(tmp_path / "broker.json"),
        control_path=str(tmp_path / "broker.stop.json"),
        header_size=broker_module.GLOBAL_HEADER_SIZE,
        slot_header_size=broker_module.SLOT_HEADER_SIZE,
        slot_count=ring.slot_count,
        slot_size=ring.slot_size,
        width=ring.width,
        height=ring.height,
        stride=ring.stride,
        pixel_format="BGR24",
    )
    manifest.write()
    try:
        yield manifest, ring
    finally:
        ring.close(unlink=True)


def test_orphan_stop_request_does_not_need_frame_mapping(broker_record):
    manifest, ring = broker_record
    ring.close(unlink=True)
    controller = CaptureBrokerProcess(manifest_path=manifest.manifest_path)

    assert controller._request_orphan_stop(manifest)
    command = json.loads(Path(manifest.control_path).read_text(encoding="utf-8"))
    assert command["command"] == "stop"
    assert command["session_id"] == manifest.session_id


def test_owned_stop_request_does_not_need_frame_mapping(broker_record):
    manifest, ring = broker_record
    ring.close(unlink=True)
    controller = CaptureBrokerProcess(manifest_path=manifest.manifest_path)
    controller._process = SimpleNamespace(pid=manifest.pid)
    controller._session_id = manifest.session_id

    controller._request_stop()

    command = json.loads(Path(manifest.control_path).read_text(encoding="utf-8"))
    assert command["session_id"] == manifest.session_id


@pytest.mark.parametrize("changed_field", [{"pid": 1}, {"session_id": "new-session"}])
def test_old_stop_request_does_not_target_replacement_session(broker_record, changed_field):
    manifest, ring = broker_record
    with CaptureBrokerClient.connect(manifest.manifest_path) as client:
        replacement = replace(manifest, **changed_field)
        replacement.write()
        controller = CaptureBrokerProcess(manifest_path=manifest.manifest_path)
        controller._process = SimpleNamespace(pid=manifest.pid)
        controller._session_id = manifest.session_id

        assert not controller._request_orphan_stop(manifest)
        controller._request_stop()
        with pytest.raises(broker_module.BrokerUnavailableError, match="会话已更换"):
            client.request_stop()

    assert BrokerManifest.load(manifest.manifest_path) == replacement
    assert not Path(manifest.control_path).exists()


def test_owned_child_still_terminates_when_stop_file_cannot_be_written(broker_record, monkeypatch):
    manifest, _ring = broker_record

    class StuckChild:
        pid = manifest.pid
        terminated = False

        def poll(self):
            return 0 if self.terminated else None

        def wait(self, timeout):
            if not self.terminated:
                raise subprocess.TimeoutExpired("fake-broker", timeout)
            return 0

        def terminate(self):
            self.terminated = True

    def denied_write(*_args):
        raise PermissionError("control file locked")

    monkeypatch.setattr(broker_module, "_atomic_json_write", denied_write)
    child = StuckChild()
    controller = CaptureBrokerProcess(manifest_path=manifest.manifest_path, stop_timeout=0.1)
    controller._process = child
    controller._session_id = manifest.session_id

    assert controller.stop()
    assert child.terminated
    assert controller.process is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows named mutex and mapping lifetime")
@pytest.mark.parametrize("remaining_resource", ["mapping", "mutex"])
def test_existing_broker_resources_still_prevent_takeover(broker_record, remaining_resource):
    manifest, ring = broker_record
    manifest = replace(manifest, parent_pid=os.getpid())
    manifest.write()
    mutex = broker_module._BrokerLifetimeMutex(Path(manifest.manifest_path))
    if remaining_resource == "mutex":
        ring.close(unlink=True)
        mutex.acquire()
    try:
        controller = CaptureBrokerProcess(manifest_path=manifest.manifest_path)
        with pytest.raises(CaptureBrokerProcessError, match="另一个本软件实例"):
            controller._recover_or_reject_existing_broker()
        assert BrokerManifest.load(manifest.manifest_path) == manifest
        assert not Path(manifest.control_path).exists()
    finally:
        mutex.release()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows named mutex and mapping lifetime")
@pytest.mark.parametrize("unavailable_resource", ["mapping", "mutex"])
def test_resource_access_failure_is_not_proof_of_stale_manifest(
    broker_record, monkeypatch, unavailable_resource
):
    manifest, _ring = broker_record

    def access_denied(*_args, **_kwargs):
        if unavailable_resource == "mapping":
            raise PermissionError("mapping access denied")
        raise broker_module.BrokerError("mutex access denied")

    if unavailable_resource == "mapping":
        monkeypatch.setattr(broker_module, "SharedMemory", access_denied)
    else:
        monkeypatch.setattr(broker_module._BrokerLifetimeMutex, "acquire", access_denied)

    assert not broker_module._manifest_has_no_owner(manifest, Path(manifest.manifest_path))
    assert BrokerManifest.load(manifest.manifest_path) == manifest


@pytest.mark.skipif(sys.platform != "win32", reason="Windows named mutex and mapping lifetime")
def test_stale_resource_check_revalidates_session_after_acquiring_mutex(broker_record, monkeypatch):
    manifest, ring = broker_record
    ring.close(unlink=True)
    replacement = replace(manifest, session_id="new-session")
    acquire = broker_module._BrokerLifetimeMutex.acquire

    def replace_during_acquire(mutex):
        acquire(mutex)
        replacement.write()

    monkeypatch.setattr(broker_module._BrokerLifetimeMutex, "acquire", replace_during_acquire)

    assert not broker_module._manifest_has_no_owner(manifest, Path(manifest.manifest_path))
    assert BrokerManifest.load(manifest.manifest_path) == replacement


@pytest.mark.skipif(sys.platform != "win32", reason="Windows named mutex and mapping lifetime")
@pytest.mark.parametrize("parent_pid", [0, os.getpid(), 2147483647])
def test_reused_pid_without_broker_resources_allows_reconnect(broker_record, parent_pid):
    manifest, ring = broker_record
    manifest = replace(manifest, parent_pid=parent_pid)
    manifest.write()
    ring.close(unlink=True)
    controller = CaptureBrokerProcess(manifest_path=manifest.manifest_path, stop_timeout=0.1)

    controller._recover_or_reject_existing_broker()

    # The child must also accept the same stale record under its startup lock.
    replacement = CaptureBroker(
        0,
        manifest_path=manifest.manifest_path,
        capture_factory=lambda *_args: FakeCapture(
            [np.zeros((2, 4, 3), dtype=np.uint8)], repeat=True, read_delay=0.002
        ),
        width=4,
        height=2,
        first_frame_timeout=1.0,
        frame_timeout=1.0,
    )
    try:
        assert replacement.start()
        assert BrokerManifest.load(manifest.manifest_path).session_id != manifest.session_id
        assert not Path(manifest.control_path).exists()
    finally:
        replacement.stop()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows named mutex and mapping lifetime")
def test_controller_reconnects_to_real_child_after_stale_record(broker_record, monkeypatch):
    manifest, ring = broker_record
    ring.close(unlink=True)
    controller = CaptureBrokerProcess(manifest_path=manifest.manifest_path, first_frame_timeout=5.0)
    # Windows venv python.exe is a launcher with a different PID from the
    # interpreter. Use a direct child (as in the packaged app), with the same
    # source/dependency paths as this test process.
    child_code = "\n".join((
        "import sys",
        f"sys.path[:] = {sys.path!r}",
        "import numpy as np",
        "from auto_bdsp_rng.capture_broker import CaptureBroker, FakeCapture",
        "capture = FakeCapture([np.zeros((2, 4, 3), dtype=np.uint8)], repeat=True, read_delay=0.002)",
        "broker = CaptureBroker(0, manifest_path=sys.argv[1], parent_pid=int(sys.argv[2]),",
        "    capture_factory=lambda *_args: capture, width=4, height=2)",
        "raise SystemExit(0 if broker.serve_forever() else 2)",
    ))
    monkeypatch.setattr(controller, "_command", lambda: [
        sys._base_executable, "-c", child_code, manifest.manifest_path, str(os.getpid())
    ])
    child = None
    try:
        assert controller.start(), controller.failure
        child = controller.process
        assert child is not None
        with controller.client() as client:
            assert client.manifest.pid == child.pid
            assert client.manifest.session_id != manifest.session_id
            assert client.wait_for_frame(timeout=1.0).as_array().shape == (2, 4, 3)
    finally:
        controller.stop()
    assert child is not None and child.returncode == 0
    assert not Path(manifest.manifest_path).exists()
