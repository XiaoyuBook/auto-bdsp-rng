from __future__ import annotations

import os
import re
import sys
import threading
import traceback
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from types import TracebackType
from typing import TextIO

from auto_bdsp_rng.resources import app_path


_LOG_FILE_RE = re.compile(
    r"^run_(?P<date>\d{4}-\d{2}-\d{2})"
    r"(?:_session-\d{8}T\d{12}_pid-\d+)?\.log$"
)
_RETENTION_DAYS = 7

ErrorCallback = Callable[[str], object]
NowCallback = Callable[[], datetime]


class RunLogError(RuntimeError):
    """Raised when run logging cannot be enabled."""


class RunLogManager:
    """Thread-safe, opt-in storage for user-facing run log events."""

    def __init__(
        self,
        directory: str | os.PathLike[str] | None = None,
        now: NowCallback | None = None,
    ) -> None:
        self._directory = Path(directory) if directory is not None else app_path("logs")
        self._now = now or datetime.now
        self._lock = threading.RLock()
        self._enabled = False
        self._stream: TextIO | None = None
        self._current_date: date | None = None
        self._current_path: Path | None = None
        self._error_callback: ErrorCallback | None = None

    @property
    def directory(self) -> Path:
        return self._directory

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._enabled

    @property
    def current_path(self) -> Path | None:
        with self._lock:
            return self._current_path

    def set_error_callback(self, callback: ErrorCallback | None) -> None:
        if callback is not None and not callable(callback):
            raise TypeError("callback must be callable or None")
        with self._lock:
            self._error_callback = callback

    def enable(self) -> Path:
        """Enable logging, raising only when the active file cannot be opened."""

        try:
            with self._lock:
                if self._enabled and self._current_path is not None:
                    return self._current_path

                now = self._read_now()
                self._directory.mkdir(parents=True, exist_ok=True)
                self._cleanup_locked(now.date())
                path, stream = self._open_stream(now.date())
                self._stream = stream
                self._current_date = now.date()
                self._current_path = path
                self._enabled = True
        except Exception as exc:
            with self._lock:
                self._deactivate_locked()
            raise RunLogError(f"无法启用运行日志: {exc}") from exc

        return path

    def disable(self) -> None:
        notice: str | None = None
        with self._lock:
            close_error = self._deactivate_locked()
            if close_error is not None:
                notice = f"关闭运行日志文件失败: {close_error}"
        if notice is not None:
            self._notify_errors((notice,))

    def close(self) -> None:
        self.disable()

    def write(self, source: object, message: object, level: str = "INFO") -> None:
        """Write an event if enabled; runtime I/O errors never escape."""

        notices: list[str] = []
        with self._lock:
            if not self._enabled:
                return
            try:
                now = self._read_now()
                if now.date() != self._current_date:
                    close_error = self._close_stream_locked()
                    if close_error is not None:
                        raise close_error
                    self._cleanup_locked(now.date())
                    path, stream = self._open_stream(now.date())
                    self._stream = stream
                    self._current_date = now.date()
                    self._current_path = path

                if self._stream is None:
                    raise OSError("运行日志文件未打开")
                record = self._format_record(now, source, message, level)
                self._stream.write(record)
                self._stream.flush()
            except Exception as exc:
                self._deactivate_locked()
                notices.append(f"写入运行日志失败，已自动停用: {exc}")

        self._notify_errors(notices)

    def write_exception(
        self,
        source: object,
        exc_type: type[BaseException],
        exc_value: BaseException,
        tb: TracebackType | None,
    ) -> None:
        try:
            message = "".join(traceback.format_exception(exc_type, exc_value, tb)).rstrip("\r\n")
        except Exception:
            try:
                message = f"{getattr(exc_type, '__name__', 'Exception')}: {exc_value!r}"
            except Exception:
                message = "无法格式化未处理异常"
        try:
            self.write(source, message, level="ERROR")
        except Exception:
            pass

    def cleanup(self) -> tuple[Path, ...]:
        """Remove this module's log files older than the seven-day window."""

        try:
            today = self._read_now().date()
        except Exception:
            return ()

        with self._lock:
            return self._cleanup_locked(today)

    def _read_now(self) -> datetime:
        value = self._now()
        if not isinstance(value, datetime):
            raise TypeError("now callback must return datetime")
        return value

    def _path_for_date(self, day: date) -> Path:
        name = f"run_{day:%Y-%m-%d}.log"
        return self._directory / name

    def _open_stream(self, day: date) -> tuple[Path, TextIO]:
        path = self._path_for_date(day)
        stream = path.open("a", encoding="utf-8", newline="\n", buffering=1)
        return path, stream

    def _cleanup_locked(self, today: date) -> tuple[Path, ...]:
        cutoff = today - timedelta(days=_RETENTION_DAYS - 1)
        removed: list[Path] = []
        try:
            candidates = tuple(self._directory.iterdir())
        except FileNotFoundError:
            return ()
        except OSError:
            return ()

        for path in candidates:
            match = _LOG_FILE_RE.fullmatch(path.name)
            if match is None or path.is_symlink():
                continue
            try:
                file_date = datetime.strptime(match.group("date"), "%Y-%m-%d").date()
                if cutoff <= file_date <= today or not path.is_file():
                    continue
                path.unlink()
            except (OSError, ValueError):
                continue
            else:
                removed.append(path)
        return tuple(sorted(removed))

    def _close_stream_locked(self) -> Exception | None:
        stream = self._stream
        self._stream = None
        self._current_date = None
        self._current_path = None
        if stream is None:
            return None

        first_error: Exception | None = None
        try:
            stream.flush()
        except Exception as exc:
            first_error = exc
        try:
            stream.close()
        except Exception as exc:
            if first_error is None:
                first_error = exc
        return first_error

    def _deactivate_locked(self) -> Exception | None:
        self._enabled = False
        return self._close_stream_locked()

    @staticmethod
    def _format_record(now: datetime, source: object, message: object, level: str) -> str:
        safe_source = RunLogManager._redact_user_home(" ".join(str(source).split())) or "应用"
        safe_source = safe_source.replace("]", ")")
        safe_level = re.sub(r"[^A-Z0-9_-]+", "_", str(level).upper()).strip("_") or "INFO"
        timestamp = f"{now:%Y-%m-%d %H:%M:%S}.{now.microsecond // 1000:03d}"
        lines = RunLogManager._redact_user_home(str(message)).splitlines() or [""]
        return "".join(
            f"{timestamp} [{safe_level}] [{safe_source}] {line}\n"
            for line in lines
        )

    @staticmethod
    def _redact_user_home(text: str) -> str:
        candidates = (os.environ.get("USERPROFILE", ""), str(Path.home()))
        redacted = text
        seen: set[str] = set()
        for candidate in candidates:
            normalized = candidate.strip().rstrip("\\/")
            if len(normalized) < 3:
                continue
            base_variants = {
                normalized,
                normalized.replace("\\", "/"),
                normalized.replace("/", "\\"),
            }
            variants = base_variants | {variant.replace("\\", "\\\\") for variant in base_variants}
            for variant in sorted(variants, key=len, reverse=True):
                if variant in seen:
                    continue
                seen.add(variant)
                redacted = re.sub(re.escape(variant), "%USERPROFILE%", redacted, flags=re.IGNORECASE)
        return redacted

    def _notify_errors(self, messages: tuple[str, ...] | list[str]) -> None:
        if not messages:
            return
        with self._lock:
            callback = self._error_callback
        if callback is None:
            return
        for message in messages:
            try:
                callback(message)
            except Exception:
                pass


class ExceptionHookGuard:
    """Installs run-log hooks while preserving the process's existing hooks."""

    def __init__(self, manager: RunLogManager) -> None:
        self._manager = manager
        self._lock = threading.RLock()
        self._installed = False
        self._previous_sys_hook: Callable[..., object] | None = None
        self._previous_thread_hook: Callable[..., object] | None = None
        self._sys_hook: Callable[..., object] | None = None
        self._thread_hook: Callable[..., object] | None = None

    @property
    def installed(self) -> bool:
        with self._lock:
            return self._installed

    def install(self) -> ExceptionHookGuard:
        with self._lock:
            if self._installed:
                return self

            previous_sys_hook = sys.excepthook
            previous_thread_hook = threading.excepthook

            def sys_hook(exc_type, exc_value, tb) -> None:
                try:
                    self._manager.write_exception("应用未处理异常", exc_type, exc_value, tb)
                except Exception:
                    pass
                previous_sys_hook(exc_type, exc_value, tb)

            def thread_hook(args) -> None:
                try:
                    thread = getattr(args, "thread", None)
                    thread_name = getattr(thread, "name", "未知线程") or "未知线程"
                    self._manager.write_exception(
                        f"后台线程未处理异常/{thread_name}",
                        args.exc_type,
                        args.exc_value,
                        args.exc_traceback,
                    )
                except Exception:
                    pass
                previous_thread_hook(args)

            self._previous_sys_hook = previous_sys_hook
            self._previous_thread_hook = previous_thread_hook
            self._sys_hook = sys_hook
            self._thread_hook = thread_hook
            sys.excepthook = sys_hook
            threading.excepthook = thread_hook
            self._installed = True
        return self

    def restore(self) -> None:
        with self._lock:
            if not self._installed:
                return
            if sys.excepthook is self._sys_hook and self._previous_sys_hook is not None:
                sys.excepthook = self._previous_sys_hook
            if threading.excepthook is self._thread_hook and self._previous_thread_hook is not None:
                threading.excepthook = self._previous_thread_hook
            self._installed = False
            self._previous_sys_hook = None
            self._previous_thread_hook = None
            self._sys_hook = None
            self._thread_hook = None

    def __enter__(self) -> ExceptionHookGuard:
        return self.install()

    def __exit__(self, exc_type, exc_value, tb) -> None:
        self.restore()
