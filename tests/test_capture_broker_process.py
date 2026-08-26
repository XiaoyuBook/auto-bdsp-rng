from __future__ import annotations

import subprocess
from types import SimpleNamespace

from auto_bdsp_rng.capture_broker import BrokerState
from auto_bdsp_rng.capture_broker_process import CaptureBrokerProcess
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
    assert controller.status is BrokerState.RUNNING
    assert controller.stop()
    assert child.returncode == 0


def test_capture_broker_process_uses_hidden_frozen_child_argument(monkeypatch, tmp_path):
    controller = CaptureBrokerProcess(manifest_path=tmp_path / "broker.json")
    monkeypatch.setattr(process_module.sys, "frozen", True, raising=False)
    monkeypatch.setattr(process_module.sys, "executable", "auto-bdsp-rng.exe")

    command = controller._command()

    assert command[:2] == ["auto-bdsp-rng.exe", "--capture-broker-child"]


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
