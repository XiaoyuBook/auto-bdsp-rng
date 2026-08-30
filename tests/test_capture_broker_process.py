from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from auto_bdsp_rng.capture_broker import BrokerState
from auto_bdsp_rng.capture_broker_process import CaptureBrokerProcess, CaptureBrokerProcessError
import auto_bdsp_rng.capture_broker_process as process_module


class _FakeProcess:
    def __init__(self, pid: int = 4321) -> None:
        self.pid = pid
        self.returncode = None
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = 0
        return 0

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def kill(self):
        self.killed = True
        self.returncode = -9


def test_capture_broker_process_starts_child_and_waits_for_running_manifest(monkeypatch, tmp_path):
    commands = []
    child = _FakeProcess()

    def popen(command, **kwargs):
        commands.append((command, kwargs))
        return child

    monkeypatch.setattr(
        process_module,
        "discover_manifest",
        lambda _path: SimpleNamespace(pid=child.pid, state=BrokerState.RUNNING, session_id="session-1"),
    )
    controller = CaptureBrokerProcess(
        manifest_path=tmp_path / "broker.json",
        popen_factory=popen,
    )
    monkeypatch.setattr(controller, "_request_stop", lambda: None)
    monkeypatch.setattr(controller, "_remove_owned_manifest", lambda _pid: None)

    assert controller.start(device_index=2, capture_api=700)
    assert commands[0][0][1:4] == ["-m", "auto_bdsp_rng", "capture-broker"]
    assert "--device-index" in commands[0][0]
    assert commands[0][0][commands[0][0].index("--device-index") + 1] == "2"
    assert commands[0][0][commands[0][0].index("--parent-pid") + 1] == str(controller.parent_pid)
    assert controller.status is BrokerState.RUNNING
    assert controller.stop()
    assert child.returncode == 0


def test_capture_broker_process_defaults_to_media_foundation(tmp_path):
    controller = CaptureBrokerProcess(manifest_path=tmp_path / "broker.json")
    command = controller._command()

    assert controller.capture_api == 1400
    assert command[command.index("--capture-api") + 1] == "1400"


def test_capture_broker_process_uses_hidden_frozen_child_argument(monkeypatch, tmp_path):
    controller = CaptureBrokerProcess(manifest_path=tmp_path / "broker.json", parent_pid=2468)
    monkeypatch.setattr(process_module.sys, "frozen", True, raising=False)
    monkeypatch.setattr(process_module.sys, "executable", "auto-bdsp-rng.exe")

    command = controller._command()

    assert command[:2] == ["auto-bdsp-rng.exe", "--capture-broker-child"]
    assert command[command.index("--parent-pid") + 1] == "2468"


def test_capture_broker_process_rejects_a_live_owner_before_spawning(monkeypatch, tmp_path):
    manifest = SimpleNamespace(
        pid=9876,
        parent_pid=2468,
        state=BrokerState.RUNNING,
        session_id="live-session",
    )
    spawned = []
    monkeypatch.setattr(process_module, "discover_manifest", lambda _path: manifest)
    monkeypatch.setattr(
        process_module,
        "_process_status",
        lambda _pid: process_module._ProcessStatus.ALIVE,
    )
    controller = CaptureBrokerProcess(
        manifest_path=tmp_path / "broker.json",
        popen_factory=lambda *_args, **_kwargs: spawned.append(True),
    )

    with pytest.raises(CaptureBrokerProcessError, match="另一个本软件实例"):
        controller.start()

    assert spawned == []


def test_capture_broker_process_does_not_take_over_a_live_legacy_broker(monkeypatch, tmp_path):
    manifest = SimpleNamespace(
        pid=9876,
        parent_pid=0,
        state=BrokerState.RUNNING,
        session_id="legacy-session",
    )
    monkeypatch.setattr(process_module, "discover_manifest", lambda _path: manifest)
    monkeypatch.setattr(
        process_module,
        "_process_status",
        lambda _pid: process_module._ProcessStatus.ALIVE,
    )
    controller = CaptureBrokerProcess(manifest_path=tmp_path / "broker.json")

    with pytest.raises(CaptureBrokerProcessError, match="旧版本"):
        controller.start()


def test_capture_broker_process_cooperatively_stops_an_orphan(monkeypatch, tmp_path):
    manifest = SimpleNamespace(
        pid=9876,
        parent_pid=2468,
        state=BrokerState.RUNNING,
        session_id="orphan-session",
    )
    alive = {9876: True, 2468: False}
    stop_requests = []

    class ExistingBrokerClient:
        def __init__(self):
            self.manifest = manifest

        def request_stop(self):
            stop_requests.append(True)
            alive[9876] = False

        def close(self):
            return None

    monkeypatch.setattr(process_module, "discover_manifest", lambda _path: manifest)
    monkeypatch.setattr(
        process_module,
        "_process_status",
        lambda pid: (
            process_module._ProcessStatus.ALIVE
            if alive.get(pid, False)
            else process_module._ProcessStatus.DEAD
        ),
    )
    monkeypatch.setattr(
        process_module.CaptureBrokerClient,
        "connect",
        lambda *_args, **_kwargs: ExistingBrokerClient(),
    )
    controller = CaptureBrokerProcess(manifest_path=tmp_path / "broker.json")

    controller._recover_or_reject_existing_broker()

    assert stop_requests == [True]


def test_capture_broker_process_never_stops_owner_when_parent_status_is_unknown(
    monkeypatch,
    tmp_path,
):
    manifest = SimpleNamespace(
        pid=9876,
        parent_pid=2468,
        state=BrokerState.RUNNING,
        session_id="unknown-parent-session",
    )
    stop_requests = []

    def process_status(pid):
        if pid == manifest.pid:
            return process_module._ProcessStatus.ALIVE
        return process_module._ProcessStatus.UNKNOWN

    monkeypatch.setattr(process_module, "discover_manifest", lambda _path: manifest)
    monkeypatch.setattr(process_module, "_process_status", process_status)
    controller = CaptureBrokerProcess(manifest_path=tmp_path / "broker.json")
    monkeypatch.setattr(
        controller,
        "_request_orphan_stop",
        lambda _manifest: stop_requests.append(True),
    )

    with pytest.raises(CaptureBrokerProcessError, match="无法安全确认"):
        controller._recover_or_reject_existing_broker()

    assert stop_requests == []


def test_capture_broker_process_waits_when_orphan_exits_before_stop_request(monkeypatch, tmp_path):
    manifest = SimpleNamespace(
        pid=9876,
        parent_pid=2468,
        state=BrokerState.RUNNING,
        session_id="orphan-session",
    )
    alive = {9876: True, 2468: False}
    monkeypatch.setattr(process_module, "discover_manifest", lambda _path: manifest)
    monkeypatch.setattr(process_module.BrokerManifest, "load", lambda _path: manifest)
    monkeypatch.setattr(
        process_module,
        "_process_status",
        lambda pid: (
            process_module._ProcessStatus.ALIVE
            if alive.get(pid, False)
            else process_module._ProcessStatus.DEAD
        ),
    )
    monkeypatch.setattr(
        process_module.time,
        "sleep",
        lambda _seconds: alive.__setitem__(9876, False),
    )
    controller = CaptureBrokerProcess(manifest_path=tmp_path / "broker.json")
    monkeypatch.setattr(controller, "_request_orphan_stop", lambda _manifest: False)

    controller._recover_or_reject_existing_broker()

    assert alive[9876] is False


def test_capture_broker_process_reports_orphan_cleanup_timeout(monkeypatch, tmp_path):
    manifest = SimpleNamespace(
        pid=9876,
        parent_pid=2468,
        state=BrokerState.RUNNING,
        session_id="orphan-session",
    )
    times = iter((0.0, 0.0, 1.0))
    monkeypatch.setattr(process_module, "discover_manifest", lambda _path: manifest)
    monkeypatch.setattr(process_module.BrokerManifest, "load", lambda _path: manifest)
    monkeypatch.setattr(
        process_module,
        "_process_status",
        lambda pid: (
            process_module._ProcessStatus.ALIVE
            if pid == 9876
            else process_module._ProcessStatus.DEAD
        ),
    )
    monkeypatch.setattr(process_module.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(process_module.time, "sleep", lambda _seconds: None)
    controller = CaptureBrokerProcess(manifest_path=tmp_path / "broker.json", stop_timeout=0.1)
    monkeypatch.setattr(controller, "_request_orphan_stop", lambda _manifest: True)

    with pytest.raises(CaptureBrokerProcessError, match="自动释放超时"):
        controller._recover_or_reject_existing_broker()


def test_capture_broker_process_does_not_follow_manifest_into_a_new_session(monkeypatch, tmp_path):
    orphan = SimpleNamespace(
        pid=9876,
        parent_pid=2468,
        state=BrokerState.RUNNING,
        session_id="orphan-session",
    )
    replacement = SimpleNamespace(
        pid=5555,
        parent_pid=1357,
        state=BrokerState.RUNNING,
        session_id="replacement-session",
    )
    monkeypatch.setattr(process_module, "discover_manifest", lambda _path: orphan)
    monkeypatch.setattr(process_module.BrokerManifest, "load", lambda _path: replacement)
    monkeypatch.setattr(
        process_module,
        "_process_status",
        lambda pid: (
            process_module._ProcessStatus.ALIVE
            if pid in (9876, 5555, 1357)
            else process_module._ProcessStatus.DEAD
        ),
    )
    controller = CaptureBrokerProcess(manifest_path=tmp_path / "broker.json")
    monkeypatch.setattr(controller, "_request_orphan_stop", lambda _manifest: True)

    with pytest.raises(CaptureBrokerProcessError, match="另一个本软件实例"):
        controller._recover_or_reject_existing_broker()


def test_capture_broker_process_preserves_child_failure_detail(monkeypatch, tmp_path):
    child = _FakeProcess()
    manifest = SimpleNamespace(
        pid=child.pid,
        parent_pid=111,
        state=BrokerState.FAILED,
        session_id="failed-session",
        failure_message="采集卡打开失败，设备可能正被其他程序占用",
    )
    monkeypatch.setattr(process_module, "discover_manifest", lambda _path: manifest)
    controller = CaptureBrokerProcess(
        manifest_path=tmp_path / "broker.json",
        popen_factory=lambda *_args, **_kwargs: child,
    )
    monkeypatch.setattr(controller, "_remove_owned_manifest", lambda _pid: None)

    assert controller.start() is False
    assert controller.failure == "采集卡打开失败，设备可能正被其他程序占用"


def test_capture_broker_process_status_refreshes_failed_manifest_detail(monkeypatch, tmp_path):
    child = _FakeProcess()
    child.returncode = 2
    manifest = SimpleNamespace(
        pid=child.pid,
        state=BrokerState.FAILED,
        failure_message="共享视频源连续无新帧超过 1 秒",
    )
    monkeypatch.setattr(process_module, "discover_manifest", lambda _path: manifest)
    controller = CaptureBrokerProcess(manifest_path=tmp_path / "broker.json")
    controller._process = child  # type: ignore[assignment]

    assert controller.status is BrokerState.FAILED
    assert controller.failure == "共享视频源连续无新帧超过 1 秒"


def test_capture_broker_process_reports_concurrent_owner_exit_code(monkeypatch, tmp_path):
    child = _FakeProcess()
    child.returncode = process_module.BROKER_ALREADY_RUNNING_EXIT_CODE
    monkeypatch.setattr(
        process_module,
        "discover_manifest",
        lambda _path: (_ for _ in ()).throw(process_module.BrokerError("missing")),
    )
    controller = CaptureBrokerProcess(
        manifest_path=tmp_path / "broker.json",
        popen_factory=lambda *_args, **_kwargs: child,
    )

    assert controller.start() is False
    assert "另一个本软件实例" in (controller.failure or "")
    assert "首帧" not in (controller.failure or "")


def test_capture_broker_process_escalates_when_cooperative_stop_times_out(monkeypatch, tmp_path):
    class StuckProcess(_FakeProcess):
        def wait(self, timeout=None):
            if not self.terminated:
                raise subprocess.TimeoutExpired("broker", timeout)
            self.returncode = 0
            return 0

        def terminate(self):
            self.terminated = True

    child = StuckProcess()
    controller = CaptureBrokerProcess(manifest_path=tmp_path / "broker.json")
    controller._process = child
    monkeypatch.setattr(controller, "_request_stop", lambda: None)
    monkeypatch.setattr(controller, "_remove_owned_manifest", lambda _pid: None)

    assert controller.stop()
    assert child.terminated
    assert not child.killed


def test_capture_broker_process_never_stops_a_manifest_owned_by_another_process(
    monkeypatch, tmp_path
):
    child = _FakeProcess(pid=4321)
    requested = []

    class ExistingBrokerClient:
        manifest = SimpleNamespace(pid=9876, session_id="existing-session")

        def request_stop(self):
            requested.append(True)

        def close(self):
            return None

    controller = CaptureBrokerProcess(manifest_path=tmp_path / "broker.json")
    controller._process = child
    monkeypatch.setattr(
        process_module.CaptureBrokerClient,
        "connect",
        lambda *_args, **_kwargs: ExistingBrokerClient(),
    )

    controller._request_stop()

    assert requested == []
