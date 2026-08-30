from __future__ import annotations

import re
import subprocess
import threading
from datetime import datetime
from pathlib import Path

from auto_bdsp_rng.automation.easycon.backend import EasyConBackend
from auto_bdsp_rng.automation.easycon.discovery import discover_ezcon, list_ports
from auto_bdsp_rng.automation.easycon.models import EasyConInstallation, EasyConRunResult, EasyConRunTask, EasyConStatus
from auto_bdsp_rng.automation.easycon.process import no_window_subprocess_kwargs
from auto_bdsp_rng.automation.easycon.scripts import create_temporary_cli_script, remove_temporary_cli_script


CLI_TRANSITION_NOTICE = (
    "CLI 过渡后端可用，但它不是真实长期连接；每次运行脚本都会启动 ezcon.exe 并重新连接单片机。"
)
CLI_RESET_NOTICE = "如果单片机每次连接前需要 reset，CLI 过渡后端无法免除这一步。"
CLI_NOT_FINAL_NOTICE = "CLI 只用于脚本验证、参数替换、日志捕获和临时兼容运行，不满足最终验收。"

COMPILE_ERROR_PATTERNS = (
    re.compile(r"(?:line|行)\s*[:：]?\s*(?P<line>\d+)", re.IGNORECASE),
    re.compile(r"(?P<file>[^:\r\n]+):(?P<line>\d+)(?::\d+)?"),
)


class CliEasyConBackend(EasyConBackend):
    def __init__(self, installation: EasyConInstallation | None = None) -> None:
        self._installation = installation
        self._status = EasyConStatus.UNCONFIGURED
        self._process_lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
        self._cancelled_process: subprocess.Popen[str] | None = None

    def discover(self) -> EasyConInstallation:
        self._installation = self._installation or discover_ezcon()
        with self._process_lock:
            if self._process is None:
                self._status = EasyConStatus.READY if self._installation.is_available else EasyConStatus.MISSING_EZCON
        return self._installation

    def version(self) -> str | None:
        return self.discover().version

    def list_ports(self) -> list[str]:
        return list_ports(self.discover())

    def status(self) -> EasyConStatus:
        with self._process_lock:
            return self._status

    def run_script(self, task: EasyConRunTask) -> EasyConRunResult:
        installation = self.discover()
        ezcon_path = task.ezcon_path or installation.path
        if ezcon_path is None:
            raise RuntimeError("ezcon.exe is not configured")
        port = "mock" if task.mock else task.port
        started_at = datetime.now()
        with self._process_lock:
            if self._process is not None:
                raise RuntimeError("已有 CLI 脚本正在运行")
            try:
                process = subprocess.Popen(
                    [str(ezcon_path), "run", str(task.script_path), "-p", port],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    **no_window_subprocess_kwargs(),
                )
            except Exception:
                self._status = EasyConStatus.FAILED
                raise
            self._process = process
            self._cancelled_process = None
            self._status = EasyConStatus.RUNNING
        try:
            stdout, stderr = process.communicate()
        except BaseException:
            with self._process_lock:
                cancelled = self._cancelled_process is process
                if self._process is process:
                    self._process = None
                if cancelled:
                    self._cancelled_process = None
                self._status = EasyConStatus.CANCELLED if cancelled else EasyConStatus.FAILED
            raise
        ended_at = datetime.now()
        failure_type = classify_cli_failure(stdout, stderr, process.returncode)
        exit_code = process.returncode
        if process.returncode == 0 and failure_type != "completed":
            exit_code = 2 if failure_type == "script_compile_failed" else 1
        result_status = EasyConStatus.COMPLETED if failure_type == "completed" else EasyConStatus.FAILED
        with self._process_lock:
            cancelled = self._cancelled_process is process
            if self._process is process:
                self._process = None
            if cancelled:
                self._cancelled_process = None
                result_status = EasyConStatus.CANCELLED
                exit_code = 130
            self._status = result_status
        return EasyConRunResult(
            status=result_status,
            exit_code=exit_code,
            started_at=started_at,
            ended_at=ended_at,
            script_path=task.script_path,
            port=port,
            stdout=stdout,
            stderr=stderr,
        )

    def run_script_text(self, script_text: str, name: str | None = None, *, port: str = "", high_resolution: bool = False) -> EasyConRunResult:
        """将脚本文本写入临时文件，通过 ezcon.exe 执行。"""
        t_start = datetime.now()
        installation = self.discover()
        ezcon_path = installation.path
        if ezcon_path is None:
            raise RuntimeError("ezcon.exe is not configured")
        if not port:
            raise RuntimeError("CLI 模式需要指定串口")
        tmp_path = create_temporary_cli_script(script_text)
        t_ready = datetime.now()
        try:
            task = EasyConRunTask(script_path=tmp_path, port=port, ezcon_path=ezcon_path, name=name or "cli-script")
            result = self.run_script(task)
            t_end = datetime.now()
            prepare_ms = (t_ready - t_start).total_seconds() * 1000
            ezcon_ms = (result.ended_at - result.started_at).total_seconds() * 1000 if result.ended_at and result.started_at else 0
            diag = (
                f"CLI 模式[{name}]: 准备={prepare_ms:.0f}ms, "
                f"耗时={ezcon_ms:.0f}ms (~{ezcon_ms / 1018:.0f}帧), "
                f"串口={port}"
            )
            result = EasyConRunResult(
                status=result.status, exit_code=result.exit_code,
                started_at=result.started_at, ended_at=result.ended_at,
                script_path=result.script_path, port=result.port,
                stdout=f"{diag}\n{result.stdout}",
                stderr=result.stderr,
            )
            return result
        finally:
            try:
                remove_temporary_cli_script(tmp_path)
            except OSError:
                pass

    def stop_current_script(self) -> None:
        """终止正在运行的 ezcon.exe 子进程。"""
        with self._process_lock:
            process = self._process
            if process is None or process.poll() is not None:
                return
            process.terminate()
            self._cancelled_process = process
            self._status = EasyConStatus.CANCELLED

    def stop(self) -> None:
        self.stop_current_script()


def cli_connection_notice() -> str:
    return " ".join((CLI_TRANSITION_NOTICE, CLI_RESET_NOTICE, CLI_NOT_FINAL_NOTICE))


def classify_cli_failure(stdout: str, stderr: str, exit_code: int | None) -> str:
    combined = f"{stdout}\n{stderr}".lower()
    if exit_code == 0:
        return "completed"
    if "compile" in combined or "编译" in combined or "parse" in combined or "syntax" in combined or "语法" in combined:
        return "script_compile_failed"
    if "连接失败" in combined or "connection failed" in combined or "cannot connect" in combined:
        return "device_connection_failed"
    return "failed"


def extract_compile_error_line(stdout: str, stderr: str) -> int | None:
    combined = f"{stdout}\n{stderr}"
    for pattern in COMPILE_ERROR_PATTERNS:
        match = pattern.search(combined)
        if match is not None:
            try:
                return int(match.group("line"))
            except (IndexError, ValueError):
                return None
    return None
