from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from auto_bdsp_rng.automation.easycon import BridgeEasyConBackend
from auto_bdsp_rng.automation.easycon.models import EasyConRunTask, EasyConStatus


class FakeBridgeTransport:
    def __init__(self) -> None:
        self.commands: list[tuple[str, dict[str, object]]] = []
        self.closed = False

    def request(self, command: str, payload: Mapping[str, object] | None = None) -> dict[str, object]:
        data = dict(payload or {})
        self.commands.append((command, data))
        if command == "version":
            return {"version": "bridge-test"}
        if command == "list_ports":
            return {"ports": ["COM7", "COM9"]}
        if command == "run_script":
            return {"exit_code": 0, "stdout": f"ran {data['name']}", "stderr": ""}
        return {}

    def close(self) -> None:
        self.closed = True


def test_bridge_backend_reuses_connection_for_multiple_script_runs():
    transport = FakeBridgeTransport()
    backend = BridgeEasyConBackend(transport=transport)

    backend.connect("COM7")
    first = backend.run_script_text("A 100", name="first.ecs")
    second = backend.run_script_text("B 100", name="second.ecs")

    assert backend.status() == EasyConStatus.BRIDGE_CONNECTED
    assert first.status == EasyConStatus.COMPLETED
    assert second.status == EasyConStatus.COMPLETED
    assert first.port == "COM7"
    assert second.stdout == "ran second.ecs"
    assert [command for command, _payload in transport.commands] == ["connect", "run_script", "run_script"]


def test_bridge_backend_requires_connect_before_running_script():
    backend = BridgeEasyConBackend(transport=FakeBridgeTransport())

    with pytest.raises(RuntimeError, match="not connected"):
        backend.run_script_text("A 100")


def test_bridge_backend_runs_script_file_as_text(tmp_path):
    script = tmp_path / "sample.ecs"
    script.write_text("A 100\n", encoding="utf-8")
    transport = FakeBridgeTransport()
    backend = BridgeEasyConBackend(transport=transport)

    backend.connect("COM9")
    result = backend.run_script(EasyConRunTask(script_path=script, port="COM9"))

    assert result.stdout == "ran sample.ecs"
    assert transport.commands[-1] == ("run_script", {"script_text": "A 100\n", "name": "sample.ecs"})


def test_bridge_backend_disconnect_releases_only_on_explicit_request():
    transport = FakeBridgeTransport()
    backend = BridgeEasyConBackend(transport=transport)

    backend.connect("COM7")
    backend.run_script_text("A 100")
    backend.disconnect()

    assert backend.status() == EasyConStatus.BRIDGE_DISCONNECTED
    assert [command for command, _payload in transport.commands] == ["connect", "run_script", "disconnect"]
