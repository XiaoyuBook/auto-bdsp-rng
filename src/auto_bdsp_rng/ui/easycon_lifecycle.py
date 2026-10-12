"""Background operations for persistent EasyCon device lifecycle changes."""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass

from PySide6.QtCore import QObject, QThread


class BackendOperation(QThread):
    """Own one blocking backend call; completion is delivered on the UI thread."""

    def __init__(
        self,
        operation: str,
        action: Callable[[], object],
        *,
        parent: QObject,
        port: str = "",
        cleanup: tuple[Callable[[], object], ...] = (),
        keyboard_hook: object | None = None,
    ) -> None:
        super().__init__(parent)
        self.operation = operation
        self.port = port
        self.cancel_event = threading.Event()
        self.action = action
        self.cleanup = cleanup
        self.keyboard_hook = keyboard_hook
        self.cleanup_completed = False
        self.hook_stopped = False
        self.result: object = None
        self.error: Exception | None = None

    def run(self) -> None:
        cleanup_error: Exception | None = None
        for cleanup in self.cleanup:
            try:
                cleanup()
                if self.keyboard_hook is not None and getattr(cleanup, "__self__", None) is self.keyboard_hook:
                    self.hook_stopped = True
            except Exception as exc:
                cleanup_error = cleanup_error or exc
        self.cleanup_completed = cleanup_error is None
        try:
            self.result = self.action()
        except Exception as exc:
            self.error = exc
        else:
            self.error = cleanup_error


def _wait_for_keep_awake(keep_awake: Future[None] | None, timeout: float = 6.0) -> None:
    if keep_awake is None:
        return
    try:
        keep_awake.result(timeout=timeout)
    except TimeoutError as exc:
        raise TimeoutError("亮屏保活任务尚未退出") from exc
    except Exception:
        pass


def connect_backend(
    backend: object,
    port: str,
    cancel_event: threading.Event,
    keep_awake: Future[None] | None = None,
) -> bool:
    _wait_for_keep_awake(keep_awake)
    error: Exception | None = None
    if not cancel_event.is_set():
        try:
            cancellable = getattr(backend, "connect_cancellable", None)
            if callable(cancellable):
                cancellable(port, cancel_event)
            else:
                backend.connect(port)  # type: ignore[attr-defined]
        except Exception as exc:
            error = exc
    if cancel_event.is_set():
        # Legacy injected backends can finish a handshake after cancellation.
        # Serial ownership stays with this operation until cleanup completes.
        backend.disconnect()  # type: ignore[attr-defined]
        return False
    if error is not None:
        raise error
    return True


def disconnect_backend(backend: object, keep_awake: Future[None] | None) -> None:
    _wait_for_keep_awake(keep_awake)
    backend.disconnect()  # type: ignore[attr-defined]


@dataclass(frozen=True)
class BackendCloseResult:
    native_closed: bool
    bridge_closed: bool
    errors: tuple[str, ...]


def close_backends(
    native: object | None,
    bridge: object | None,
    keep_awake: Future[None] | None,
    timeout: float,
) -> BackendCloseResult:
    if keep_awake is not None:
        try:
            keep_awake.result(timeout=max(0.0, timeout))
        except TimeoutError:
            return BackendCloseResult(False, False, ("亮屏保活任务尚未退出",))
        except Exception:
            pass  # Its completion callback already reports device errors.
    closed = [native is None, bridge is None]
    errors: list[str] = []
    for index, (backend, label) in enumerate(((native, "原生伊机控连接"), (bridge, "Bridge 进程"))):
        if backend is None:
            continue
        try:
            backend.close()  # type: ignore[attr-defined]
        except Exception as exc:
            errors.append(f"关闭{label}失败: {exc}")
        else:
            closed[index] = True
    return BackendCloseResult(closed[0], closed[1], tuple(errors))
