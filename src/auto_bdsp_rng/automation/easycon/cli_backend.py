from __future__ import annotations

import re
import subprocess
from datetime import datetime

from auto_bdsp_rng.automation.easycon.backend import EasyConBackend
from auto_bdsp_rng.automation.easycon.discovery import discover_ezcon, list_ports
from auto_bdsp_rng.automation.easycon.models import EasyConInstallation, EasyConRunResult, EasyConRunTask, EasyConStatus


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
        self._process: subprocess.Popen[str] | None = None

    def discover(self) -> EasyConInstallation:
        self._installation = self._installation or discover_ezcon()
        self._status = EasyConStatus.READY if self._installation.is_available else EasyConStatus.MISSING_EZCON
        return self._installation

    def version(self) -> str | None:
        return self.discover().version

    def list_ports(self) -> list[str]:
        return list_ports(self.discover())

    def status(self) -> EasyConStatus:
        return self._status

    def run_script(self, task: EasyConRunTask) -> EasyConRunResult:
        installation = self.discover()
        ezcon_path = task.ezcon_path or installation.path
        if ezcon_path is None:
            raise RuntimeError("ezcon.exe is not configured")
        port = "mock" if task.mock else task.port
        started_at = datetime.now()
        self._status = EasyConStatus.RUNNING
        completed = subprocess.run(
            [str(ezcon_path), "run", str(task.script_path), "-p", port],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        ended_at = datetime.now()
        failure_type = classify_cli_failure(completed.stdout, completed.stderr, completed.returncode)
        exit_code = completed.returncode
        if completed.returncode == 0 and failure_type != "completed":
            exit_code = 2 if failure_type == "script_compile_failed" else 1
        self._status = EasyConStatus.COMPLETED if failure_type == "completed" else EasyConStatus.FAILED
        return EasyConRunResult(
            status=self._status,
            exit_code=exit_code,
            started_at=started_at,
            ended_at=ended_at,
            script_path=task.script_path,
            port=port,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

    def stop(self) -> None:
        if self._process is not None and self._process.poll() is None:
            self._process.terminate()
            self._status = EasyConStatus.CANCELLED


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
