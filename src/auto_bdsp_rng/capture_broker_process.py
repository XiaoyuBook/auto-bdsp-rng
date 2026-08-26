"""Lifecycle controller for the standalone capture Broker process."""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from auto_bdsp_rng.capture_broker import (
    BrokerError,
    BrokerManifest,
    BrokerState,
    CaptureBrokerClient,
    default_manifest_path,
    discover_manifest,
)


class CaptureBrokerProcessError(RuntimeError):
    """Raised when the standalone Broker cannot be started or controlled."""


class CaptureBrokerProcess:
    """Own a Broker child process without ever opening the card in the GUI.

    ``start`` waits for the first committed frame (up to five seconds by
    default). Consumers use :meth:`client` to open their own read-only mapping.
    ``stop`` first requests a cooperative shutdown and only terminates a child
    that fails to exit inside the bounded grace period.
    """

    def __init__(
        self,
        device_index: int = 0,
        capture_api: int = 700,
        *,
        manifest_path: str | Path | None = None,
        first_frame_timeout: float = 5.0,
        frame_timeout: float = 1.0,
        stop_timeout: float = 2.0,
        popen_factory: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
    ) -> None:
        self.device_index = int(device_index)
        self.capture_api = int(capture_api)
        self.manifest_path = Path(manifest_path) if manifest_path is not None else default_manifest_path()
        self.first_frame_timeout = max(0.1, float(first_frame_timeout))
        self.frame_timeout = max(0.1, float(frame_timeout))
        self.stop_timeout = max(0.1, float(stop_timeout))
        self._popen_factory = popen_factory
        self._process: subprocess.Popen[bytes] | None = None
        self._failure: str | None = None
        self._session_id: str | None = None
        self._lock = threading.RLock()

    @property
    def process(self) -> subprocess.Popen[bytes] | None:
        return self._process

    @property
    def failure(self) -> str | None:
        # Refresh a later child failure before returning the cached detail.
        _ = self.status
        return self._failure

    @property
    def status(self) -> BrokerState:
        process = self._process
        if process is None:
            return BrokerState.STOPPED
        exit_code = process.poll()
        if exit_code is not None:
            try:
                manifest = discover_manifest(self.manifest_path)
            except BrokerError:
                if exit_code != 0:
                    self._failure = f"共享视频源进程已退出 (exit={exit_code})"
                    return BrokerState.FAILED
                return BrokerState.STOPPED
            return manifest.state if manifest.pid == process.pid else BrokerState.STOPPED
        try:
            manifest = discover_manifest(self.manifest_path)
        except BrokerError:
            return BrokerState.STARTING
        if manifest.pid != process.pid:
            return BrokerState.STARTING
        return manifest.state

    def configure(self, *, device_index: int, capture_api: int) -> None:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                raise CaptureBrokerProcessError("视频源运行时不能修改采集设备")
            self.device_index = int(device_index)
            self.capture_api = int(capture_api)

    def _command(self) -> list[str]:
        arguments = [
            "--device-index",
            str(self.device_index),
            "--capture-api",
            str(self.capture_api),
            "--manifest",
            str(self.manifest_path),
            "--first-frame-timeout",
            f"{self.first_frame_timeout:g}",
            "--frame-timeout",
            f"{self.frame_timeout:g}",
        ]
        if getattr(sys, "frozen", False):
            return [sys.executable, "--capture-broker-child", *arguments]
        return [sys.executable, "-m", "auto_bdsp_rng", "capture-broker", *arguments]

    def start(self, *, device_index: int | None = None, capture_api: int | None = None) -> bool:
        with self._lock:
            process = self._process
            if process is not None and process.poll() is None:
                return self.status is BrokerState.RUNNING
            if device_index is not None:
                self.device_index = int(device_index)
            if capture_api is not None:
                self.capture_api = int(capture_api)
            self._failure = None
            self._session_id = None

            popen_kwargs: dict[str, object] = {
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                "close_fds": True,
            }
            if sys.platform == "win32":
                creation_flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if creation_flags:
                    popen_kwargs["creationflags"] = creation_flags
            try:
                self._process = self._popen_factory(self._command(), **popen_kwargs)
            except OSError as exc:
                self._failure = str(exc)
                self._process = None
                raise CaptureBrokerProcessError(f"无法启动共享视频源进程: {exc}") from exc

        deadline = time.monotonic() + self.first_frame_timeout
        while time.monotonic() < deadline:
            process = self._process
            if process is None:
                break
            exit_code = process.poll()
            try:
                manifest = discover_manifest(self.manifest_path)
            except BrokerError:
                manifest = None
            if manifest is not None and manifest.pid == process.pid:
                self._session_id = manifest.session_id
                if manifest.state is BrokerState.RUNNING:
                    return True
                if manifest.state is BrokerState.FAILED:
                    self._failure = "共享视频源报告采集失败"
                    break
            if exit_code is not None:
                self._failure = f"共享视频源进程已退出 (exit={exit_code})"
                break
            time.sleep(0.025)

        if self._failure is None:
            self._failure = "5 秒内未收到采集卡首帧"
        self.stop()
        return False

    def client(self) -> CaptureBrokerClient:
        return CaptureBrokerClient.connect(self.manifest_path, require_running=True, require_live_pid=True)

    def _request_stop(self) -> None:
        process = self._process
        owned_pid = getattr(process, "pid", None) if process is not None else None
        if owned_pid is None:
            return
        try:
            client = CaptureBrokerClient.connect(self.manifest_path, require_live_pid=False)
        except BrokerError:
            return
        try:
            # The discovery path is shared by all Broker launches. A child that
            # has not published its own manifest yet must never stop an older
            # Broker merely because that manifest happens to be discoverable.
            if client.manifest.pid != owned_pid:
                return
            if self._session_id is not None and client.manifest.session_id != self._session_id:
                return
            client.request_stop()
        finally:
            client.close()

    def _remove_owned_manifest(self, process_pid: int | None) -> None:
        try:
            manifest = BrokerManifest.load(self.manifest_path)
        except BrokerError:
            return
        if process_pid is not None and manifest.pid != process_pid:
            return
        if self._session_id is not None and manifest.session_id != self._session_id:
            return
        try:
            Path(manifest.control_path).unlink()
        except (OSError, ValueError):
            pass
        try:
            self.manifest_path.unlink()
        except OSError:
            pass

    def stop(self) -> bool:
        with self._lock:
            process = self._process
            if process is None:
                return True
            process_pid = getattr(process, "pid", None)
            if process.poll() is None:
                self._request_stop()
                try:
                    process.wait(timeout=self.stop_timeout)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        process.wait(timeout=self.stop_timeout)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=self.stop_timeout)
            self._process = None
            self._remove_owned_manifest(process_pid)
            self._session_id = None
            return True

    def __enter__(self) -> "CaptureBrokerProcess":
        if not self.start():
            raise CaptureBrokerProcessError(self.failure or "共享视频源启动失败")
        return self

    def __exit__(self, *_args: object) -> None:
        self.stop()


__all__ = ["CaptureBrokerProcess", "CaptureBrokerProcessError"]
