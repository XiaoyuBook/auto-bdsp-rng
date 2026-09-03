"""Persistent, pure-Python EasyCon backend."""

from __future__ import annotations

import math
import threading
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from auto_bdsp_rng import __version__
from auto_bdsp_rng.automation.easycon.backend import EasyConBackend
from auto_bdsp_rng.automation.easycon.models import (
    EasyConInstallation,
    EasyConRunResult,
    EasyConRunTask,
    EasyConStatus,
)
from auto_bdsp_rng.automation.easycon.native import (
    EasyConScriptEngine,
    ScriptCancelled,
    ScriptCompileError,
)
from auto_bdsp_rng.automation.easycon.native.device import (
    DeviceCancelledError,
    DeviceConnectionError,
    DeviceNotConnectedError,
    NativeGamePadAdapter,
    NintendoSwitchDevice,
    SwitchReport,
    list_ports as list_native_ports,
)
from auto_bdsp_rng.automation.easycon.native.image_labels import (
    ImageSearchResult,
    OcrReader,
    load_image_labels,
)
from auto_bdsp_rng.capture_broker import (
    BrokerUnavailableError,
    CaptureBrokerClient,
)
from auto_bdsp_rng.resources import resource_path


LogCallback = Callable[[str, str], None]
FrameClientFactory = Callable[[], Any]
ImageResultCallback = Callable[[ImageSearchResult], None]


class NativeEasyConBusyError(RuntimeError):
    """Raised when a second script is started while one is still running."""


class _RunOutput:
    def __init__(self, emit: LogCallback) -> None:
        self._emit = emit
        self._parts: list[str] = []

    @property
    def text(self) -> str:
        return "".join(self._parts)

    def print(self, message: str, newline: bool) -> None:
        rendered = str(message) + ("\n" if newline else "")
        self._parts.append(rendered)
        self._emit("INFO", str(message))

    def alert(self, message: str) -> None:
        rendered = str(message)
        self._parts.append(rendered + "\n")
        self._emit("WARNING", rendered)


def _default_frame_client_factory() -> CaptureBrokerClient:
    return CaptureBrokerClient.connect(require_running=True, require_live_pid=True)


def _script_location(name: str | None, script_dir: str | Path | None) -> tuple[Path, Path | None, str]:
    directory = Path(script_dir).resolve() if script_dir is not None else None
    display_name = name or "script.ecs"
    candidate = Path(display_name)
    if directory is None and candidate.parent != Path("."):
        directory = candidate.expanduser().resolve().parent
    if directory is not None and candidate.parent == Path("."):
        script_path = directory / candidate.name
    else:
        script_path = candidate
    return script_path, directory, str(script_path)


def _stick_coordinates(direction: str | int) -> tuple[int, int]:
    if isinstance(direction, str):
        normalized = direction.strip().upper().replace("_", "")
        degrees = {
            "RIGHT": 0,
            "UPRIGHT": 45,
            "UP": 90,
            "UPLEFT": 135,
            "LEFT": 180,
            "DOWNLEFT": 225,
            "DOWN": 270,
            "DOWNRIGHT": 315,
            "RESET": -1,
        }
        try:
            degree = degrees[normalized]
        except KeyError as exc:
            raise ValueError(f"未知摇杆方向: {direction}") from exc
    else:
        degree = int(direction)
    if degree == -1:
        return 128, 128
    radians = math.radians(degree % 360)
    cosine = math.cos(radians)
    sine = math.sin(radians)
    if abs(cosine) < 1e-12:
        cosine = 0.0
    if abs(sine) < 1e-12:
        sine = 0.0
    scale = max(abs(cosine), abs(sine), 1e-12)
    x = int((cosine / scale + 1.0) * 128.0)
    y = int((-sine / scale + 1.0) * 128.0)
    return max(0, min(255, x)), max(0, min(255, y))


class NativeEasyConBackend(EasyConBackend):
    """Run EasyCon scripts without ezcon.exe, CLI, Bridge, or easycon_root."""

    def __init__(
        self,
        *,
        device: NintendoSwitchDevice | None = None,
        engine: EasyConScriptEngine | None = None,
        frame_client_factory: FrameClientFactory | None = None,
        log_callback: LogCallback | None = None,
        image_result_callback: ImageResultCallback | None = None,
        ocr_reader: OcrReader | None = None,
        waiter: Any | None = None,
        disconnect_timeout: float = 2.0,
    ) -> None:
        self._log_callback = log_callback
        self._device = device or NintendoSwitchDevice(action_sink=self._device_log)
        self._gamepad = NativeGamePadAdapter(self._device)
        self._engine = engine or EasyConScriptEngine()
        self._frame_client_factory = frame_client_factory or _default_frame_client_factory
        self._image_result_callback = image_result_callback
        self._ocr_reader = ocr_reader
        self._waiter = waiter
        self._disconnect_timeout = max(0.1, float(disconnect_timeout))
        self._state_lock = threading.RLock()
        self._run_lock = threading.Lock()
        self._run_cancel: threading.Event | None = None
        self._run_done = threading.Event()
        self._run_done.set()
        self._running = False
        self._closed = False
        self._stick_directions: dict[str, set[str]] = {"LS": set(), "RS": set()}

    @property
    def connected_port(self) -> str | None:
        return self._device.port if self._device.is_connected else None

    @property
    def is_running(self) -> bool:
        with self._state_lock:
            return self._running

    def get_report(self) -> SwitchReport:
        """Return a thread-safe snapshot of the report currently sent to the controller."""

        return self._device.get_report()

    def set_image_result_callback(self, callback: ImageResultCallback | None) -> None:
        self._image_result_callback = callback

    def discover(self) -> EasyConInstallation:
        return EasyConInstallation(
            path=Path(__file__).resolve(),
            version=self.version(),
            source="python-native",
        )

    def version(self) -> str:
        return f"{__version__} (Python native)"

    def list_ports(self) -> list[str]:
        return list_native_ports()

    def status(self) -> EasyConStatus:
        with self._state_lock:
            if self._running:
                return EasyConStatus.RUNNING
        if self._device.is_connected:
            return EasyConStatus.BRIDGE_CONNECTED
        return EasyConStatus.BRIDGE_DISCONNECTED

    def connect(self, port: str) -> None:
        actual_port = str(port).strip()
        if not actual_port:
            raise DeviceConnectionError("请选择伊机控串口")
        with self._state_lock:
            if self._closed:
                raise RuntimeError("原生伊机控后端已关闭")
            if self._running:
                raise NativeEasyConBusyError("脚本运行时不能切换串口")
        if not self._device.connect(actual_port):
            detail = f": {self._device.failure}" if self._device.failure is not None else ""
            raise DeviceConnectionError(f"无法连接伊机控串口 {actual_port}{detail}")
        self._emit("INFO", f"原生伊机控已连接: {actual_port}")

    def disconnect(self) -> None:
        self.stop_current_script()
        if not self._run_done.wait(self._disconnect_timeout):
            raise NativeEasyConBusyError("伊机控脚本仍在停止，暂不能断开串口")
        if not self._device.disconnect(release=True, timeout=self._disconnect_timeout):
            raise DeviceConnectionError("伊机控串口未能在限定时间内断开")
        with self._state_lock:
            for directions in self._stick_directions.values():
                directions.clear()
        self._emit("INFO", "原生伊机控已断开")

    def close(self) -> None:
        self.disconnect()
        with self._state_lock:
            self._closed = True

    def run_script(self, task: EasyConRunTask) -> EasyConRunResult:
        if not self._device.is_connected:
            self.connect("mock" if task.mock else task.port)
        text = task.script_path.read_text(encoding="utf-8-sig")
        return self.run_script_text(
            text,
            name=task.name or str(task.script_path),
            script_dir=task.script_path.parent,
        )

    def run_script_text(
        self,
        script_text: str,
        name: str | None = None,
        *,
        script_dir: str | Path | None = None,
    ) -> EasyConRunResult:
        if not self._run_lock.acquire(blocking=False):
            raise NativeEasyConBusyError("已有伊机控脚本正在运行")
        script_path, actual_script_dir, source = _script_location(name, script_dir)
        started_at = datetime.now()
        output = _RunOutput(self._emit)
        cancel = threading.Event()
        client: Any | None = None
        with self._state_lock:
            if self._closed:
                self._run_lock.release()
                raise RuntimeError("原生伊机控后端已关闭")
            self._running = True
            self._run_cancel = cancel
            self._run_done.clear()
        self._emit("INFO", f"开始运行原生伊机控脚本: {script_path.name}")
        try:
            program = self._engine.compile(
                script_text,
                source=source,
                script_dir=actual_script_dir,
            )
            if program.has_gamepad_actions and not self._device.is_connected:
                raise DeviceNotConnectedError("脚本包含手柄操作，请先连接伊机控串口")

            client = self._frame_client_factory()
            self._read_frame(client)
            roots: list[Path] = []
            if actual_script_dir is not None:
                roots.append(actual_script_dir)
            app_root = resource_path()
            if app_root not in roots:
                roots.append(app_root)
            labels = load_image_labels(roots)
            missing = sorted(program.external_labels.difference(labels.labels))
            if missing:
                raise ScriptCompileError(f"找不到搜图标签: {', '.join(missing)}")
            if labels.failed_files:
                failed = ", ".join(str(path) for path in labels.failed_files)
                self._emit("WARNING", f"无法加载部分搜图标签: {failed}")
            getters = labels.external_getters(
                lambda: self._read_frame(client),
                ocr_reader=self._ocr_reader,
                result_callback=self._image_result_callback,
            )
            program.run(
                gamepad=self._gamepad if program.has_gamepad_actions else None,
                external_getters=getters,
                output=output,
                cancel_event=cancel,
                waiter=self._waiter,
            )
            self._emit("INFO", "原生伊机控脚本运行完成")
            return self._result(
                EasyConStatus.COMPLETED,
                0,
                started_at,
                script_path,
                output.text,
                "",
            )
        except (ScriptCancelled, DeviceCancelledError) as exc:
            self._emit("WARNING", "原生伊机控脚本已停止")
            return self._result(
                EasyConStatus.CANCELLED,
                130,
                started_at,
                script_path,
                output.text,
                str(exc),
            )
        except ScriptCompileError as exc:
            message = str(exc)
            self._emit("ERROR", f"原生伊机控脚本编译失败: {message}")
            return self._result(
                EasyConStatus.FAILED,
                2,
                started_at,
                script_path,
                output.text,
                message,
            )
        except Exception as exc:
            message = str(exc)
            self._emit("ERROR", f"原生伊机控脚本运行失败: {message}")
            return self._result(
                EasyConStatus.FAILED,
                1,
                started_at,
                script_path,
                output.text,
                message,
            )
        finally:
            self._reset_after_script()
            if client is not None:
                with suppress(Exception):
                    client.close()
            with self._state_lock:
                self._running = False
                self._run_cancel = None
                self._run_done.set()
            self._run_lock.release()

    def stop_current_script(self) -> None:
        with self._state_lock:
            cancel = self._run_cancel
        if cancel is not None:
            cancel.set()

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
        del timeout_seconds, terminate_on_timeout
        self._gamepad.click_buttons(button, int(duration_ms))

    def stick(self, side: str, direction: str | int, duration_ms: int | None) -> None:
        normalized = side.strip().upper()
        if normalized in {"LEFT", "L", "LS"}:
            key = "LS"
        elif normalized in {"RIGHT", "R", "RS"}:
            key = "RS"
        elif normalized in {"HAT", "DPAD", "D-PAD"}:
            if duration_ms is None:
                self._gamepad.press_buttons(str(direction))
            else:
                self._gamepad.click_buttons(str(direction), int(duration_ms))
            return
        else:
            raise ValueError(f"未知摇杆: {side}")
        x, y = _stick_coordinates(direction)
        if duration_ms is None:
            self._gamepad.set_stick(key, x, y)
        else:
            self._gamepad.click_stick(key, x, y, int(duration_ms))

    def key_down(self, button: str) -> None:
        self._gamepad.press_buttons(button)

    def key_up(self, button: str) -> None:
        self._gamepad.release_buttons(button)

    def stick_direction(self, side: str, direction: str, down: bool) -> None:
        normalized_side = side.strip().upper()
        normalized_direction = direction.strip().upper().replace("_", "")
        if normalized_side in {"HAT", "DPAD", "D-PAD"}:
            if down:
                self._gamepad.press_buttons(normalized_direction)
            else:
                self._gamepad.release_buttons(normalized_direction)
            return
        key = "LS" if normalized_side in {"LEFT", "L", "LS"} else "RS" if normalized_side in {"RIGHT", "R", "RS"} else ""
        if not key:
            raise ValueError(f"未知摇杆: {side}")
        if normalized_direction not in {"UP", "DOWN", "LEFT", "RIGHT"}:
            raise ValueError(f"未知摇杆方向: {direction}")
        with self._state_lock:
            active = self._stick_directions[key]
            if down:
                active.add(normalized_direction)
            else:
                active.discard(normalized_direction)
            x = 128 + 127 * (("RIGHT" in active) - ("LEFT" in active))
            y = 128 + 127 * (("DOWN" in active) - ("UP" in active))
        self._gamepad.set_stick(key, x, y)

    def _read_frame(self, client: Any) -> np.ndarray:
        read_array = getattr(client, "read_array", None)
        frame = read_array() if callable(read_array) else None
        if frame is None:
            wait_for_frame = getattr(client, "wait_for_frame", None)
            if not callable(wait_for_frame):
                raise BrokerUnavailableError("共享视频源当前没有可用画面")
            packet = wait_for_frame(timeout=1.0)
            frame = packet.as_array() if hasattr(packet, "as_array") else packet
        array = np.asarray(frame)
        if array.dtype != np.uint8 or array.ndim != 3 or array.shape[2] != 3:
            raise BrokerUnavailableError("共享视频源没有返回 BGR24 画面")
        return array.copy()

    def _reset_after_script(self) -> None:
        with self._state_lock:
            for directions in self._stick_directions.values():
                directions.clear()
        if self._device.is_connected:
            with suppress(Exception):
                self._gamepad.reset()

    def _result(
        self,
        status: EasyConStatus,
        exit_code: int,
        started_at: datetime,
        script_path: Path,
        stdout: str,
        stderr: str,
    ) -> EasyConRunResult:
        return EasyConRunResult(
            status=status,
            exit_code=exit_code,
            started_at=started_at,
            ended_at=datetime.now(),
            script_path=script_path,
            port=self.connected_port or "",
            stdout=stdout,
            stderr=stderr,
        )

    def _device_log(self, message: str) -> None:
        self._emit("DEBUG", message)

    def _emit(self, level: str, message: str) -> None:
        callback = self._log_callback
        if callback is None:
            return
        with suppress(Exception):
            callback(level, message)


__all__ = ["NativeEasyConBackend", "NativeEasyConBusyError"]
