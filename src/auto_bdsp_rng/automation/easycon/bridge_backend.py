from __future__ import annotations

from auto_bdsp_rng.automation.easycon.backend import EasyConBackend


class BridgeEasyConBackend(EasyConBackend):
    """Placeholder for the future persistent EasyCon bridge backend."""

    def discover(self):  # type: ignore[no-untyped-def]
        raise NotImplementedError("Bridge backend is planned for phase 2")

    def version(self) -> str | None:
        raise NotImplementedError("Bridge backend is planned for phase 2")

    def list_ports(self) -> list[str]:
        raise NotImplementedError("Bridge backend is planned for phase 2")

    def status(self):  # type: ignore[no-untyped-def]
        raise NotImplementedError("Bridge backend is planned for phase 2")

    def run_script(self, task):  # type: ignore[no-untyped-def]
        raise NotImplementedError("Bridge backend is planned for phase 2")

    def stop(self) -> None:
        raise NotImplementedError("Bridge backend is planned for phase 2")
