from __future__ import annotations

import json
import math
import queue
import subprocess
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from itertools import count
from pathlib import Path
from typing import Callable, Protocol

from auto_bdsp_rng.automation.easycon.backend import EasyConBackend
from auto_bdsp_rng.automation.easycon.discovery import discover_ezcon
from auto_bdsp_rng.automation.easycon.models import EasyConInstallation, EasyConRunResult, EasyConRunTask, EasyConStatus
from auto_bdsp_rng.automation.easycon.process import no_window_subprocess_kwargs


DEFAULT_BRIDGE_REQUEST_TIMEOUT_SECONDS = 10.0
BRIDGE_SCRIPT_REQUEST_TIMEOUT_SECONDS = 24 * 60 * 60.0


class BridgeProtocolError(RuntimeError):
    pass


class BridgeTransportTerminatedError(BridgeProtocolError):
    """A bounded request failed and the transport was intentionally terminated."""


@dataclass
class _QueuedBridgeWrite:
    request_id: int
    line: str
    completed: threading.Event = field(default_factory=threading.Event)


class BridgeTransport(Protocol):
    def request(
        self,
        command: str,
        payload: Mapping[str, object] | None = None,
        *,
        timeout_seconds: float | None = None,
        terminate_on_timeout: bool = False,
    ) -> dict[str, object]:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


class JsonLineBridgeTransport:
    """JSON Lines transport for EasyConBridge.exe stdin/stdout IPC."""

    def __init__(
        self,
        bridge_path: Path,
        log_callback: Callable[[str, str], None] | None = None,
        *,
        request_timeout_seconds: float = DEFAULT_BRIDGE_REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        self._ids = count(1)
        self._log_callback = log_callback
        self._request_timeout_seconds = _validate_timeout(request_timeout_seconds)
        self._process = subprocess.Popen(
            [str(bridge_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            **no_window_subprocess_kwargs(),
        )
        self._lock = threading.Lock()
        self._pending: dict[int, queue.Queue[dict[str, object] | BaseException]] = {}
        self._closed_error: BaseException | None = None
        self._write_queue: queue.Queue[_QueuedBridgeWrite | None] = queue.Queue()
        self._writer = threading.Thread(target=self._write_loop, name="EasyConBridgeWriter", daemon=True)
        self._reader = threading.Thread(target=self._read_loop, name="EasyConBridgeReader", daemon=True)
        self._writer.start()
        self._reader.start()

    def request(
        self,
        command: str,
        payload: Mapping[str, object] | None = None,
        *,
        timeout_seconds: float | None = None,
        terminate_on_timeout: bool = False,
    ) -> dict[str, object]:
        if self._process.stdin is None or self._process.stdout is None:
            raise BridgeProtocolError("Bridge process is not connected")
        timeout = _validate_timeout(
            timeout_seconds
            if timeout_seconds is not None
            else BRIDGE_SCRIPT_REQUEST_TIMEOUT_SECONDS
            if command == "run_script"
            else self._request_timeout_seconds
        )
        deadline = time.monotonic() + timeout
        request_id = next(self._ids)
        request = {"id": request_id, "command": command, "payload": dict(payload or {})}
        response_queue: queue.Queue[dict[str, object] | BaseException] = queue.Queue(maxsize=1)
        queued_write = _QueuedBridgeWrite(
            request_id=request_id,
            line=json.dumps(request, ensure_ascii=False) + "\n",
        )
        with self._lock:
            if self._closed_error is not None:
                raise self._closed_error
            self._pending[request_id] = response_queue
        self._write_queue.put_nowait(queued_write)

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            error = _request_timeout_error(command, timeout, terminate_on_timeout)
            self._handle_request_timeout(
                request_id,
                response_queue,
                queued_write,
                error,
                terminate_on_timeout=terminate_on_timeout,
            )
            raise error
        try:
            response = response_queue.get(timeout=remaining)
        except queue.Empty as exc:
            error = _request_timeout_error(command, timeout, terminate_on_timeout)
            self._handle_request_timeout(
                request_id,
                response_queue,
                queued_write,
                error,
                terminate_on_timeout=terminate_on_timeout,
            )
            raise error from exc
        self._remove_pending(request_id, response_queue)
        if isinstance(response, BaseException):
            raise response
        if response.get("ok") is not True:
            raise BridgeProtocolError(str(response.get("error") or f"Bridge command failed: {command}"))
        payload_value = response.get("payload")
        return payload_value if isinstance(payload_value, dict) else {}

    def close(self) -> None:
        self._break_transport(BridgeProtocolError("Bridge transport closed"))

    def _write_loop(self) -> None:
        while True:
            queued_write = self._write_queue.get()
            if queued_write is None:
                return
            with self._lock:
                if self._closed_error is not None:
                    return
            try:
                stdin = self._process.stdin
                if stdin is None:
                    raise BridgeProtocolError("Bridge process is not connected")
                stdin.write(queued_write.line)
                stdin.flush()
                queued_write.completed.set()
            except BaseException as exc:
                error = exc if isinstance(exc, BridgeProtocolError) else BridgeProtocolError(str(exc))
                self._break_transport(error)
                return

    def _read_loop(self) -> None:
        try:
            if self._process.stdout is None:
                raise BridgeProtocolError("Bridge process is not connected")
            while True:
                line = self._process.stdout.readline()
                if not line:
                    stderr = ""
                    if self._process.poll() is not None and self._process.stderr is not None:
                        stderr = self._process.stderr.read()
                    raise BridgeProtocolError(stderr.strip() or "Bridge closed stdout")
                response = json.loads(line)
                if response.get("type") == "log":
                    if self._log_callback is not None:
                        self._log_callback(str(response.get("level") or "info"), str(response.get("message") or ""))
                    continue
                self._dispatch_response(response)
        except BaseException as exc:
            error = exc if isinstance(exc, BridgeProtocolError) else BridgeProtocolError(str(exc))
            self._break_transport(error)

    def _dispatch_response(self, response: dict[str, object]) -> None:
        response_id = response.get("id")
        if not isinstance(response_id, int):
            return
        with self._lock:
            response_queue = self._pending.get(response_id)
        if response_queue is None:
            return
        try:
            response_queue.put_nowait(response)
        except queue.Full:
            pass

    def _handle_request_timeout(
        self,
        request_id: int,
        response_queue: queue.Queue[dict[str, object] | BaseException],
        queued_write: _QueuedBridgeWrite,
        error: BridgeProtocolError,
        *,
        terminate_on_timeout: bool,
    ) -> None:
        if queued_write.completed.is_set() and not terminate_on_timeout:
            self._remove_pending(request_id, response_queue)
            return
        self._break_transport(error)

    def _remove_pending(
        self,
        request_id: int,
        response_queue: queue.Queue[dict[str, object] | BaseException],
    ) -> None:
        with self._lock:
            if self._pending.get(request_id) is response_queue:
                self._pending.pop(request_id, None)

    def _break_transport(self, error: BaseException) -> None:
        with self._lock:
            if self._closed_error is not None:
                return
            self._closed_error = error
            pending = list(self._pending.values())
            self._pending.clear()
        self._write_queue.put_nowait(None)
        for response_queue in pending:
            try:
                response_queue.put_nowait(error)
            except queue.Full:
                pass
        threading.Thread(
            target=self._terminate_process,
            name="EasyConBridgeTerminator",
            daemon=True,
        ).start()

    def _terminate_process(self) -> None:
        try:
            if self._process.poll() is None:
                self._process.terminate()
        except BaseException:
            pass


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

    def run_script_text(self, script_text: str, name: str | None = None, *, high_resolution: bool = False) -> EasyConRunResult:
        if self._connected_port is None:
            raise RuntimeError("Bridge is not connected to a port")
        started_at = datetime.now()
        self._status = EasyConStatus.RUNNING
        try:
            response = self._request("run_script", {
                "script_text": script_text,
                "name": name or "script",
                "high_resolution": high_resolution,
                "requested_at": started_at.isoformat(),
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

    def press(
        self,
        button: str,
        duration_ms: int,
        *,
        timeout_seconds: float | None = None,
        terminate_on_timeout: bool = False,
    ) -> None:
        self._request(
            "press",
            {"button": button, "duration_ms": duration_ms},
            timeout_seconds=timeout_seconds,
            terminate_on_timeout=terminate_on_timeout,
        )

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

    def _request(
        self,
        command: str,
        payload: Mapping[str, object] | None = None,
        *,
        timeout_seconds: float | None = None,
        terminate_on_timeout: bool = False,
    ) -> dict[str, object]:
        transport = self._ensure_transport()
        if timeout_seconds is None and not terminate_on_timeout:
            return transport.request(command, payload)
        return transport.request(
            command,
            payload,
            timeout_seconds=timeout_seconds,
            terminate_on_timeout=terminate_on_timeout,
        )

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


def _validate_timeout(value: float) -> float:
    timeout = float(value)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Bridge request timeout must be finite and greater than zero")
    return timeout


def _request_timeout_error(
    command: str,
    timeout: float,
    terminate_on_timeout: bool,
) -> BridgeProtocolError:
    message = f"Bridge request timed out after {timeout:g}s: {command}"
    if terminate_on_timeout:
        return BridgeTransportTerminatedError(message)
    return BridgeProtocolError(message)
