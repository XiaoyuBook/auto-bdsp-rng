from __future__ import annotations

import json
import queue
import subprocess
import threading
from collections.abc import Mapping
from datetime import datetime
from itertools import count
from pathlib import Path
from typing import Callable, Protocol

from auto_bdsp_rng.automation.easycon.backend import EasyConBackend
from auto_bdsp_rng.automation.easycon.discovery import discover_ezcon
from auto_bdsp_rng.automation.easycon.models import EasyConInstallation, EasyConRunResult, EasyConRunTask, EasyConStatus


class BridgeProtocolError(RuntimeError):
    pass


class BridgeTransport(Protocol):
    def request(self, command: str, payload: Mapping[str, object] | None = None) -> dict[str, object]:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


class JsonLineBridgeTransport:
    """JSON Lines transport for EasyConBridge.exe stdin/stdout IPC."""

    def __init__(self, bridge_path: Path, log_callback: Callable[[str, str], None] | None = None) -> None:
        self._ids = count(1)
        self._log_callback = log_callback
        self._process = subprocess.Popen(
            [str(bridge_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self._lock = threading.Lock()
        self._pending: dict[int, queue.Queue[dict[str, object] | BaseException]] = {}
        self._closed_error: BaseException | None = None
        self._reader = threading.Thread(target=self._read_loop, name="EasyConBridgeReader", daemon=True)
        self._reader.start()

    def request(self, command: str, payload: Mapping[str, object] | None = None) -> dict[str, object]:
        if self._process.stdin is None or self._process.stdout is None:
            raise BridgeProtocolError("Bridge process is not connected")
        request_id = next(self._ids)
        request = {"id": request_id, "command": command, "payload": dict(payload or {})}
        response_queue: queue.Queue[dict[str, object] | BaseException] = queue.Queue(maxsize=1)
        with self._lock:
            if self._closed_error is not None:
                raise self._closed_error
            self._pending[request_id] = response_queue
            try:
                self._process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
                self._process.stdin.flush()
            except BaseException:
                self._pending.pop(request_id, None)
                raise

        response = response_queue.get()
        with self._lock:
            self._pending.pop(request_id, None)
        if isinstance(response, BaseException):
            raise response
        if response.get("ok") is not True:
            raise BridgeProtocolError(str(response.get("error") or f"Bridge command failed: {command}"))
        payload_value = response.get("payload")
        return payload_value if isinstance(payload_value, dict) else {}

    def close(self) -> None:
        if self._process.poll() is None:
            self._process.terminate()
        self._fail_pending(BridgeProtocolError("Bridge transport closed"))

    def _read_loop(self) -> None:
        try:
            if self._process.stdout is None:
                raise BridgeProtocolError("Bridge process is not connected")
            while True:
                line = self._process.stdout.readline()
                if not line:
                    stderr = self._process.stderr.read() if self._process.stderr is not None else ""
                    raise BridgeProtocolError(stderr.strip() or "Bridge closed stdout")
                response = json.loads(line)
                if response.get("type") == "log":
                    if self._log_callback is not None:
                        self._log_callback(str(response.get("level") or "info"), str(response.get("message") or ""))
                    continue
                response_id = response.get("id")
                if not isinstance(response_id, int):
                    continue
                with self._lock:
                    response_queue = self._pending.get(response_id)
                if response_queue is not None:
                    response_queue.put(response)
        except BaseException as exc:
            self._fail_pending(exc if isinstance(exc, BridgeProtocolError) else BridgeProtocolError(str(exc)))

    def _fail_pending(self, error: BaseException) -> None:
        with self._lock:
            self._closed_error = error
            pending = list(self._pending.values())
            self._pending.clear()
        for response_queue in pending:
            response_queue.put(error)


class BridgeEasyConBackend(EasyConBackend):
    """Persistent EasyCon bridge backend client.

    The bridge process owns the serial connection. This Python-side backend
    keeps the IPC session open and never asks the bridge to disconnect after an
    individual script completes.
    """

    def __init__(
        self,
        bridge_path: Path | None = None,
        transport: BridgeTransport | None = None,
        installation: EasyConInstallation | None = None,
        log_callback: Callable[[str, str], None] | None = None,
    ) -> None:
        self._bridge_path = bridge_path
        self._transport = transport
        self._installation = installation
        self._log_callback = log_callback
        self._status = EasyConStatus.UNCONFIGURED
        self._connected_port: str | None = None

    def discover(self) -> EasyConInstallation:
        if self._installation is None:
            self._installation = discover_ezcon()
        return self._installation

    def version(self) -> str | None:
        response = self._request("version")
        version = response.get("version")
        return str(version) if version is not None else None

    def list_ports(self) -> list[str]:
        response = self._request("list_ports")
        ports = response.get("ports", [])
        return [str(port) for port in ports] if isinstance(ports, list) else []

    def status(self) -> EasyConStatus:
        if self._transport is None and self._bridge_path is None:
            return self._status
        try:
            response = self._request("status")
        except Exception:
            self._status = EasyConStatus.FAILED
            raise
        self._connected_port = str(response["port"]) if response.get("port") else None
        self._status = _status_from_bridge(response.get("status"))
        return self._status

    def connect(self, port: str) -> None:
        self._request("connect", {"port": port})
        self._connected_port = port
        self._status = EasyConStatus.BRIDGE_CONNECTED

    def disconnect(self) -> None:
        self._request("disconnect")
        self._connected_port = None
        self._status = EasyConStatus.BRIDGE_DISCONNECTED

    def run_script(self, task: EasyConRunTask) -> EasyConRunResult:
        script_text = task.script_path.read_text(encoding="utf-8")
        return self.run_script_text(script_text, name=task.name or task.script_path.name)

    def run_script_text(self, script_text: str, name: str | None = None, *, high_resolution: bool = True) -> EasyConRunResult:
        if self._connected_port is None:
            raise RuntimeError("Bridge is not connected to a port")
        started_at = datetime.now()
        self._status = EasyConStatus.RUNNING
        try:
            response = self._request("run_script", {
                "script_text": script_text,
                "name": name or "script",
                "high_resolution": high_resolution,
            })
        except Exception:
            self._status = EasyConStatus.FAILED
            raise
        ended_at = datetime.now()
        exit_code = int(response.get("exit_code", 0))
        result_status = EasyConStatus.COMPLETED if exit_code == 0 else EasyConStatus.FAILED
        self._status = EasyConStatus.BRIDGE_CONNECTED if result_status == EasyConStatus.COMPLETED else EasyConStatus.FAILED
        return EasyConRunResult(
            status=result_status,
            exit_code=exit_code,
            started_at=started_at,
            ended_at=ended_at,
            script_path=Path(name or "<bridge-script>"),
            port=self._connected_port,
            stdout=str(response.get("stdout", "")),
            stderr=str(response.get("stderr", "")),
        )

    def stop_current_script(self) -> None:
        self._request("stop")
        self._status = EasyConStatus.BRIDGE_CONNECTED if self._connected_port else EasyConStatus.BRIDGE_DISCONNECTED

    def stop(self) -> None:
        self.stop_current_script()

    def press(self, button: str, duration_ms: int) -> None:
        self._request("press", {"button": button, "duration_ms": duration_ms})

    def stick(self, side: str, direction: str | int, duration_ms: int | None) -> None:
        self._request("stick", {"side": side, "direction": direction, "duration_ms": duration_ms})

    def key_down(self, button: str) -> None:
        self._request("key_down", {"button": button})

    def key_up(self, button: str) -> None:
        self._request("key_up", {"button": button})

    def stick_direction(self, side: str, direction: str, down: bool) -> None:
        self._request("stick_direction", {"side": side, "direction": direction, "down": down})

    def close(self) -> None:
        if self._transport is not None:
            self._transport.close()

    def _request(self, command: str, payload: Mapping[str, object] | None = None) -> dict[str, object]:
        transport = self._ensure_transport()
        return transport.request(command, payload)

    def _ensure_transport(self) -> BridgeTransport:
        if self._transport is None:
            if self._bridge_path is None:
                raise RuntimeError("EasyConBridge.exe path is not configured")
            self._transport = JsonLineBridgeTransport(self._bridge_path, log_callback=self._log_callback)
        return self._transport


def _status_from_bridge(value: object) -> EasyConStatus:
    if value == "connected":
        return EasyConStatus.BRIDGE_CONNECTED
    if value == "running":
        return EasyConStatus.RUNNING
    if value == "disconnected":
        return EasyConStatus.BRIDGE_DISCONNECTED
    return EasyConStatus.FAILED
