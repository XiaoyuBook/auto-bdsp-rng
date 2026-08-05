from __future__ import annotations

import json
import threading
import time
from collections.abc import Mapping
from datetime import datetime
from itertools import count
from pathlib import Path

import pytest

from auto_bdsp_rng.automation.easycon import BridgeEasyConBackend, BridgeProtocolError
from auto_bdsp_rng.automation.easycon.bridge_backend import JsonLineBridgeTransport
from auto_bdsp_rng.automation.easycon.models import EasyConRunTask, EasyConStatus


class FakeBridgeTransport:
    def __init__(self) -> None:
        self.commands: list[tuple[str, dict[str, object]]] = []
        self.timeouts: list[tuple[str, float | None]] = []
        self.closed = False
        self.connected_port: str | None = None

    def request(
        self,
        command: str,
        payload: Mapping[str, object] | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, object]:
        data = dict(payload or {})
        self.commands.append((command, data))
        self.timeouts.append((command, timeout_seconds))
        if command == "version":
            return {"version": "bridge-test"}
        if command == "list_ports":
            return {"ports": ["COM7", "COM9"]}
        if command == "connect":
            self.connected_port = str(data["port"])
            return {"status": "connected", "port": self.connected_port}
        if command == "disconnect":
            self.connected_port = None
            return {"status": "disconnected"}
        if command == "status":
            return {
                "status": "connected" if self.connected_port else "disconnected",
                "port": self.connected_port,
            }
        if command == "run_script":
            return {"exit_code": 0, "stdout": f"ran {data['name']}", "stderr": ""}
        if command in {"press", "stick", "stop", "key_down", "key_up", "stick_direction"}:
            return {"status": "connected"}
        return {}

    def close(self) -> None:
        self.closed = True


class _RecordingStdin:
    def __init__(self) -> None:
        self.writes: list[str] = []

    def write(self, value: str) -> None:
        self.writes.append(value)

    def flush(self) -> None:
        pass


class _AliveProcessWithoutResponses:
    def __init__(self) -> None:
        self.stdin = _RecordingStdin()
        self.stdout = object()


def _transport_without_reader(timeout_seconds: float) -> JsonLineBridgeTransport:
    transport = JsonLineBridgeTransport.__new__(JsonLineBridgeTransport)
    transport._ids = count(1)
    transport._log_callback = None
    transport._request_timeout_seconds = timeout_seconds
    transport._process = _AliveProcessWithoutResponses()
    transport._lock = threading.Lock()
    transport._pending = {}
    transport._closed_error = None
    return transport


def test_bridge_request_timeout_cleans_pending_and_later_request_still_works():
    transport = _transport_without_reader(0.02)

    with pytest.raises(BridgeProtocolError, match="timed out.*status"):
        transport.request("status")

    first_request = json.loads(transport._process.stdin.writes[0])
    assert transport._pending == {}
    transport._dispatch_response({"id": first_request["id"], "ok": True, "payload": {"stale": True}})
    assert transport._pending == {}

    result: list[dict[str, object]] = []
    errors: list[BaseException] = []

    def request_version() -> None:
        try:
            result.append(transport.request("version", timeout_seconds=0.5))
        except BaseException as exc:
            errors.append(exc)

    request_thread = threading.Thread(target=request_version)
    request_thread.start()
    deadline = time.monotonic() + 0.5
    while len(transport._process.stdin.writes) < 2 and time.monotonic() < deadline:
        time.sleep(0.005)
    second_request = json.loads(transport._process.stdin.writes[1])
    transport._dispatch_response(
        {"id": second_request["id"], "ok": True, "payload": {"version": "recovered"}}
    )
    request_thread.join(timeout=1)

    assert not request_thread.is_alive()
    assert errors == []
    assert result == [{"version": "recovered"}]
    assert transport._pending == {}


def test_bridge_backend_reuses_connection_until_explicit_disconnect():
    transport = FakeBridgeTransport()
    backend = BridgeEasyConBackend(transport=transport)

    backend.connect("COM7")
    results = [
        backend.run_script_text(f"PRINT {index}", name=f"script-{index}.ecs")
        for index in range(1, 6)
    ]

    assert backend.status() == EasyConStatus.BRIDGE_CONNECTED
    assert all(result.status == EasyConStatus.COMPLETED for result in results)
    assert {result.port for result in results} == {"COM7"}
    assert results[-1].stdout == "ran script-5.ecs"
    assert [command for command, _payload in transport.commands] == ["connect", *["run_script"] * 5, "status"]


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
    command, payload = transport.commands[-1]
    assert command == "run_script"
    assert payload["script_text"] == "A 100\n"
    assert payload["name"] == "sample.ecs"
    assert payload["high_resolution"] is False
    assert isinstance(payload["requested_at"], str)
    datetime.fromisoformat(payload["requested_at"])


def test_bridge_backend_disconnect_releases_only_on_explicit_request():
    transport = FakeBridgeTransport()
    backend = BridgeEasyConBackend(transport=transport)

    backend.connect("COM7")
    backend.run_script_text("A 100")
    backend.disconnect()

    assert backend.status() == EasyConStatus.BRIDGE_DISCONNECTED
    assert [command for command, _payload in transport.commands] == ["connect", "run_script", "disconnect", "status"]


def test_bridge_backend_status_maps_bridge_response():
    transport = FakeBridgeTransport()
    backend = BridgeEasyConBackend(transport=transport)

    assert backend.status() == EasyConStatus.BRIDGE_DISCONNECTED

    backend.connect("COM7")

    assert backend.status() == EasyConStatus.BRIDGE_CONNECTED


def test_bridge_backend_press_stick_and_stop_use_bridge_session():
    transport = FakeBridgeTransport()
    backend = BridgeEasyConBackend(transport=transport)

    backend.connect("COM7")
    backend.press("A", 100)
    backend.stick("LS", "RESET", None)
    backend.stop()

    assert backend.status() == EasyConStatus.BRIDGE_CONNECTED
    assert [command for command, _payload in transport.commands] == [
        "connect",
        "press",
        "stick",
        "stop",
        "status",
    ]


def test_bridge_backend_press_forwards_keep_awake_timeout():
    transport = FakeBridgeTransport()
    backend = BridgeEasyConBackend(transport=transport)

    backend.press("ZR", 100, timeout_seconds=2.0)

    assert transport.commands == [("press", {"button": "ZR", "duration_ms": 100})]
    assert transport.timeouts == [("press", 2.0)]


def test_bridge_backend_virtual_controller_uses_down_up_commands():
    transport = FakeBridgeTransport()
    backend = BridgeEasyConBackend(transport=transport)

    backend.connect("COM7")
    backend.key_down("A")
    backend.key_up("A")
    backend.stick_direction("left", "Up", True)
    backend.stick_direction("left", "Up", False)

    assert transport.commands[-4:] == [
        ("key_down", {"button": "A"}),
        ("key_up", {"button": "A"}),
        ("stick_direction", {"side": "left", "direction": "Up", "down": True}),
        ("stick_direction", {"side": "left", "direction": "Up", "down": False}),
    ]


def test_run_script_passes_high_resolution_false_by_default():
    """默认 high_resolution=False（匹配原版 EasyCon 默认值）发送到 Bridge。"""
    transport = FakeBridgeTransport()
    backend = BridgeEasyConBackend(transport=transport)
    backend._connected_port = "COM7"

    backend.run_script_text("A 100\nWAIT 1000\nA 100\n", name="timing-test")

    assert len(transport.commands) >= 1
    cmd, payload = transport.commands[-1]
    assert cmd == "run_script"
    assert payload.get("high_resolution") is False
    assert payload.get("name") == "timing-test"
    assert "A 100" in str(payload.get("script_text"))


def test_run_script_can_enable_high_resolution():
    """high_resolution=True 应正确传递到 Bridge。"""
    transport = FakeBridgeTransport()
    backend = BridgeEasyConBackend(transport=transport)
    backend._connected_port = "COM7"

    backend.run_script_text("A 100\n", name="high-res", high_resolution=True)

    cmd, payload = transport.commands[-1]
    assert cmd == "run_script"
    assert payload.get("high_resolution") is True
