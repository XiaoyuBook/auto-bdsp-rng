from __future__ import annotations

from abc import ABC, abstractmethod

from auto_bdsp_rng.automation.easycon.models import EasyConInstallation, EasyConRunResult, EasyConRunTask, EasyConStatus


class EasyConBackend(ABC):
    @abstractmethod
    def discover(self) -> EasyConInstallation:
        raise NotImplementedError

    @abstractmethod
    def version(self) -> str | None:
        raise NotImplementedError

    @abstractmethod
    def list_ports(self) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def status(self) -> EasyConStatus:
        raise NotImplementedError

    @abstractmethod
    def run_script(self, task: EasyConRunTask) -> EasyConRunResult:
        raise NotImplementedError

    @abstractmethod
    def stop(self) -> None:
        raise NotImplementedError

    def connect(self, port: str) -> None:
        raise NotImplementedError("Backend does not support persistent connections")

    def disconnect(self) -> None:
        raise NotImplementedError("Backend does not support persistent connections")

    def run_script_text(self, script_text: str, name: str | None = None) -> EasyConRunResult:
        raise NotImplementedError("Backend does not support running script text")

    def stop_current_script(self) -> None:
        raise NotImplementedError("Backend does not support script-level stop")

    def press(self, button: str, duration_ms: int) -> None:
        raise NotImplementedError("Backend does not support direct button presses")

    def stick(self, side: str, direction: str | int, duration_ms: int | None) -> None:
        raise NotImplementedError("Backend does not support direct stick actions")
