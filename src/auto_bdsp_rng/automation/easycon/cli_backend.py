from __future__ import annotations

import subprocess
from datetime import datetime

from auto_bdsp_rng.automation.easycon.backend import EasyConBackend
from auto_bdsp_rng.automation.easycon.discovery import discover_ezcon, list_ports
from auto_bdsp_rng.automation.easycon.models import EasyConInstallation, EasyConRunResult, EasyConRunTask, EasyConStatus


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
        self._status = EasyConStatus.COMPLETED if completed.returncode == 0 else EasyConStatus.FAILED
        return EasyConRunResult(
            status=self._status,
            exit_code=completed.returncode,
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
