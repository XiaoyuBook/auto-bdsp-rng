"""Bounded execution observation, independent of Qt and device timing."""

from __future__ import annotations

from dataclasses import dataclass, replace
from threading import Lock
from time import monotonic

from .errors import SourceLocation


@dataclass(frozen=True, slots=True)
class LoopProgress:
    location: SourceLocation
    iteration: int
    total: int | None = None


@dataclass(frozen=True, slots=True)
class ExecutionPoint:
    location: SourceLocation
    action: str
    started_at: float
    duration_ms: int | None = None
    caller: SourceLocation | None = None
    loops: tuple[LoopProgress, ...] = ()


@dataclass(frozen=True, slots=True)
class ExecutionSnapshot:
    run_id: int = 0
    source: str = ""
    sources: tuple[tuple[str, str], ...] = ()
    state: str = "idle"
    point: ExecutionPoint | None = None


class ExecutionTrace:
    """Publish only the latest position; the UI polls without queuing every line.

    Locks protect reference swaps only. No callback, formatting, file access,
    device access or GUI work is performed while holding the lock.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._snapshot = ExecutionSnapshot()
        self._point: ExecutionPoint | None = None

    def snapshot(self) -> ExecutionSnapshot:
        with self._lock:
            snapshot, point = self._snapshot, self._point
        return replace(snapshot, point=point)

    def begin(self, source: str) -> None:
        with self._lock:
            self._snapshot = ExecutionSnapshot(self._snapshot.run_id + 1, source, state="running")
            self._point = None

    def set_sources(self, sources: tuple[tuple[str, str], ...]) -> None:
        with self._lock:
            self._snapshot = replace(self._snapshot, sources=sources)

    def record(self, point: ExecutionPoint) -> None:
        with self._lock:
            self._point = point

    def finish(self, state: str) -> None:
        with self._lock:
            self._snapshot = replace(self._snapshot, state=state)


def execution_point(
    location: SourceLocation, action: str, duration_ms: int | None, caller: SourceLocation | None,
    loops: tuple[LoopProgress, ...] = (),
) -> ExecutionPoint:
    return ExecutionPoint(location, action, monotonic(), duration_ms, caller, loops)
