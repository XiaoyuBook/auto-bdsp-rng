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
