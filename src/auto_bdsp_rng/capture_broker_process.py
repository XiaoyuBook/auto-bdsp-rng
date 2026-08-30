"""Lifecycle controller for the standalone capture Broker process."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from auto_bdsp_rng.capture_broker import (
    BROKER_ALREADY_RUNNING_EXIT_CODE,
    BrokerError,
    BrokerManifest,
    BrokerState,
    CaptureBrokerClient,
    DEFAULT_CAPTURE_API,
    _ProcessStatus,
    _process_status,
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
        capture_api: int = DEFAULT_CAPTURE_API,
        *,
        manifest_path: str | Path | None = None,
        first_frame_timeout: float = 5.0,
        frame_timeout: float = 1.0,
        stop_timeout: float = 2.0,
        parent_pid: int | None = None,
        popen_factory: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
    ) -> None:
        self.device_index = int(device_index)
        self.capture_api = int(capture_api)
        self.manifest_path = Path(manifest_path) if manifest_path is not None else default_manifest_path()
        self.first_frame_timeout = max(0.1, float(first_frame_timeout))
        self.frame_timeout = max(0.1, float(frame_timeout))
        self.stop_timeout = max(0.1, float(stop_timeout))
        self.parent_pid = os.getpid() if parent_pid is None else max(0, int(parent_pid))
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
            if manifest.pid != process.pid:
                return BrokerState.STOPPED
            if manifest.state is BrokerState.FAILED:
                self._failure = getattr(manifest, "failure_message", "") or self._failure or (
                    f"共享视频源进程已退出 (exit={exit_code})"
                )
            return manifest.state
        try:
            manifest = discover_manifest(self.manifest_path)
        except BrokerError:
            return BrokerState.STARTING
        if manifest.pid != process.pid:
            return BrokerState.STARTING
        if manifest.state is BrokerState.FAILED:
            self._failure = getattr(manifest, "failure_message", "") or self._failure or "共享视频源报告采集失败"
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
            "--parent-pid",
            str(self.parent_pid),
        ]
        if getattr(sys, "frozen", False):
            return [sys.executable, "--capture-broker-child", *arguments]
        return [sys.executable, "-m", "auto_bdsp_rng", "capture-broker", *arguments]

    @staticmethod
    def _existing_broker_message(manifest: BrokerManifest) -> str:
        parent_pid = int(getattr(manifest, "parent_pid", 0) or 0)
        if parent_pid > 0:
            return (
                "采集卡正在被另一个本软件实例使用"
                f"（主程序 PID {parent_pid}，视频源 PID {manifest.pid}）。"
                "请先关闭另一个实例后再连接。"
            )
        return (
            f"采集卡正被已有共享视频源占用（视频源 PID {manifest.pid}）。"
            "该进程来自不含主程序信息的旧版本；为避免误停正在使用的视频源，"
            "请先关闭旧版软件或在任务管理器中结束该进程。"
        )

    @staticmethod
    def _unknown_parent_message(manifest: BrokerManifest) -> str:
        return (
            "检测到已有共享视频源，但无法安全确认它的主程序是否已退出"
            f"（主程序 PID {manifest.parent_pid}，视频源 PID {manifest.pid}）。"
            "为避免中断仍在使用的采集卡，本次不会自动停止该进程。"
            "请稍候重试；若确认旧实例已崩溃，请在任务管理器中结束视频源进程。"
        )

    def _live_existing_manifest(self) -> BrokerManifest | None:
        try:
            manifest = discover_manifest(self.manifest_path)
        except BrokerError:
            return None
        if manifest.state not in (BrokerState.STARTING, BrokerState.RUNNING):
            return None
        return (
            None
            if _process_status(manifest.pid) is _ProcessStatus.DEAD
            else manifest
        )

    def _request_orphan_stop(self, manifest: BrokerManifest) -> bool:
        try:
            client = CaptureBrokerClient.connect(
                self.manifest_path,
                require_live_pid=True,
            )
        except (BrokerError, OSError):
            return False
        try:
            if client.manifest.pid != manifest.pid or client.manifest.session_id != manifest.session_id:
                return False
            try:
                client.request_stop()
            except (BrokerError, OSError):
                return False
            return True
        finally:
            client.close()

    def _recover_or_reject_existing_broker(self) -> None:
        manifest = self._live_existing_manifest()
        if manifest is None:
            return
        parent_pid = int(getattr(manifest, "parent_pid", 0) or 0)
        if parent_pid <= 0:
            raise CaptureBrokerProcessError(self._existing_broker_message(manifest))
        parent_status = _process_status(parent_pid)
        if parent_status is _ProcessStatus.ALIVE:
            raise CaptureBrokerProcessError(self._existing_broker_message(manifest))
        if parent_status is _ProcessStatus.UNKNOWN:
            raise CaptureBrokerProcessError(self._unknown_parent_message(manifest))

        stop_requested = self._request_orphan_stop(manifest)

        deadline = time.monotonic() + self.stop_timeout
        while (
            _process_status(manifest.pid) is not _ProcessStatus.DEAD
            and time.monotonic() < deadline
        ):
            try:
                current = BrokerManifest.load(self.manifest_path)
            except BrokerError:
                current = None
            if current is not None and (
                current.pid != manifest.pid or current.session_id != manifest.session_id
            ):
                raise CaptureBrokerProcessError(self._existing_broker_message(current))
            time.sleep(0.025)
        if _process_status(manifest.pid) is not _ProcessStatus.DEAD:
            detail = "自动释放超时" if stop_requested else "无法发送自动释放请求"
            raise CaptureBrokerProcessError(
                "检测到上次软件异常退出遗留的采集进程"
                f"（PID {manifest.pid}），{detail}。"
                "请稍候重试；若持续出现，请在任务管理器中结束该进程。"
            )

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

        self._recover_or_reject_existing_broker()

        with self._lock:
            process = self._process
            if process is not None and process.poll() is None:
                return self.status is BrokerState.RUNNING
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
                    self._failure = manifest.failure_message or "共享视频源报告采集失败"
                    break
            if exit_code is not None:
                if (
                    manifest is not None
                    and manifest.pid != process.pid
                    and manifest.state in (BrokerState.STARTING, BrokerState.RUNNING)
                    and _process_status(manifest.pid) is not _ProcessStatus.DEAD
                ):
                    self._failure = self._existing_broker_message(manifest)
                elif exit_code == BROKER_ALREADY_RUNNING_EXIT_CODE:
                    self._failure = (
                        "采集卡正在被另一个本软件实例启动或使用。"
                        "请稍候重试，或先关闭另一个实例。"
                    )
                else:
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
