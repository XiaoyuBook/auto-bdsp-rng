from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path


class EasyConStatus(str, Enum):
    UNCONFIGURED = "UNCONFIGURED"
    MISSING_EZCON = "MISSING_EZCON"
    READY = "READY"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    BRIDGE_CONNECTED = "BRIDGE_CONNECTED"
    BRIDGE_DISCONNECTED = "BRIDGE_DISCONNECTED"


@dataclass(frozen=True)
class EasyConConfig:
    ezcon_path: Path | None = None
    last_port: str | None = None
    mock_enabled: bool = False
    recent_scripts: tuple[Path, ...] = ()
    keep_generated: int = 20


@dataclass(frozen=True)
class EasyConInstallation:
    path: Path | None
    version: str | None = None
    source: str = "missing"
    error: str | None = None

    @property
    def is_available(self) -> bool:
        return self.path is not None and self.error is None


@dataclass(frozen=True)
class ScriptParameter:
    name: str
    value: str
    default: str
    required: bool
    is_integer: bool
    comment: str = ""
    line_index: int = -1


@dataclass(frozen=True)
class EasyConRunTask:
    script_path: Path
    port: str
    ezcon_path: Path | None = None
    mock: bool = False


@dataclass(frozen=True)
class EasyConLogEntry:
    level: str
    message: str
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass(frozen=True)
class EasyConRunResult:
    status: EasyConStatus
    exit_code: int | None
    started_at: datetime
    ended_at: datetime
    script_path: Path
    port: str
    stdout: str = ""
    stderr: str = ""

    @property
    def duration_seconds(self) -> float:
        return max(0.0, (self.ended_at - self.started_at).total_seconds())
