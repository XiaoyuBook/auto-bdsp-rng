from __future__ import annotations

import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from auto_bdsp_rng.resources import app_path
from auto_bdsp_rng.run_log import ExceptionHookGuard, RunLogError, RunLogManager


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


def test_default_directory_is_next_to_application_and_logging_starts_disabled():
    manager = RunLogManager(now=lambda: datetime(2026, 8, 22, 10, 0), pid=12)

    assert manager.directory == app_path("logs")
    assert manager.enabled is False
    assert manager.current_path is None


def test_disabled_write_does_not_create_directory(tmp_path):
    directory = tmp_path / "logs"
    manager = RunLogManager(directory, now=lambda: datetime(2026, 8, 22, 10, 0), pid=12)

    manager.write("应用", "不会保存")

    assert not directory.exists()


def test_enable_writes_utf8_timestamped_multiline_records(tmp_path):
    clock = MutableClock(datetime(2026, 8, 22, 15, 31, 8, 125999))
    manager = RunLogManager(tmp_path / "logs", now=clock, pid=4321)

    path = manager.enable()
    manager.write("自动定点", "捕获 Seed 成功\n目标 Adv=123", level="info")
    manager.close()

    assert re.fullmatch(
        r"run_2026-08-22_session-20260822T153108125999_pid-4321\.log",
        path.name,
    )
    assert path.read_text(encoding="utf-8").splitlines() == [
        "2026-08-22 15:31:08.125 [INFO] [自动定点] 捕获 Seed 成功",
        "2026-08-22 15:31:08.125 [INFO] [自动定点] 目标 Adv=123",
    ]
    assert manager.enabled is False
    assert manager.current_path is None


def test_log_records_redact_windows_user_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", r"C:\Users\Trainer")
    manager = RunLogManager(
        tmp_path / "logs",
        now=lambda: datetime(2026, 8, 22, 15, 0),
        pid=6,
    )
    path = manager.enable()

    manager.write("伊机控", r"脚本路径: C:\Users\Trainer\scripts\hit.txt")
    manager.close()

    text = path.read_text(encoding="utf-8")
    assert r"脚本路径: %USERPROFILE%\scripts\hit.txt" in text
    assert "Trainer" not in text


def test_log_records_redact_escaped_path_and_source(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", r"C:\Users\Trainer")
    manager = RunLogManager(
        tmp_path / "logs",
        now=lambda: datetime(2026, 8, 22, 15, 0),
        pid=7,
    )
    path = manager.enable()

    manager.write(
        r"线程/C:\Users\Trainer\worker",
        r"FileNotFoundError: 'C:\\Users\\Trainer\\scripts\\hit.txt'",
        level="ERROR",
    )
    manager.close()

    text = path.read_text(encoding="utf-8")
    assert "Trainer" not in text
    assert r"[线程/%USERPROFILE%\worker]" in text
    assert r"'%USERPROFILE%\\scripts\\hit.txt'" in text


def test_reenable_in_same_session_appends_to_same_file(tmp_path):
    manager = RunLogManager(
        tmp_path / "logs",
        now=lambda: datetime(2026, 8, 22, 15, 0),
        pid=5,
    )

    first_path = manager.enable()
    manager.write("应用", "first")
    manager.disable()
    manager.write("应用", "ignored")
    second_path = manager.enable()
    manager.write("应用", "second")
    manager.close()

    assert second_path == first_path
    text = first_path.read_text(encoding="utf-8")
    assert "first" in text
    assert "second" in text
    assert "ignored" not in text


def test_write_after_midnight_rotates_to_new_daily_file(tmp_path):
    clock = MutableClock(datetime(2026, 8, 22, 23, 59, 59, 999000))
    manager = RunLogManager(tmp_path / "logs", now=clock, pid=8)
    first_path = manager.enable()
    manager.write("应用", "before midnight")

    clock.value = datetime(2026, 8, 23, 0, 0, 0, 1000)
    manager.write("应用", "after midnight")
    second_path = manager.current_path
    manager.close()

    assert second_path is not None
    assert second_path != first_path
    assert first_path.name.startswith("run_2026-08-22_")
    assert second_path.name.startswith("run_2026-08-23_")
    assert "before midnight" in first_path.read_text(encoding="utf-8")
    assert "after midnight" not in first_path.read_text(encoding="utf-8")
    assert "after midnight" in second_path.read_text(encoding="utf-8")


def test_cleanup_keeps_today_and_previous_six_days_and_ignores_other_files(tmp_path):
    directory = tmp_path / "logs"
    directory.mkdir()
    expired = directory / "run_2026-08-15_session-20260801T000000000000_pid-1.log"
    boundary = directory / "run_2026-08-16_session-20260801T000000000000_pid-1.log"
    today = directory / "run_2026-08-22_session-20260801T000000000000_pid-1.log"
    future = directory / "run_2026-08-23_session-20260801T000000000000_pid-1.log"
    unrelated = directory / "notes.log"
    matching_directory = directory / "run_2026-08-01_session-20260801T000000000000_pid-1.log"
    for path in (expired, boundary, today, future, unrelated):
        path.write_text(path.name, encoding="utf-8")
    matching_directory.mkdir()
    manager = RunLogManager(directory, now=lambda: datetime(2026, 8, 22, 12, 0), pid=2)

    removed = manager.cleanup()

    assert removed == (expired, future)
    assert not expired.exists()
    assert boundary.exists()
    assert today.exists()
    assert not future.exists()
    assert unrelated.exists()
    assert matching_directory.is_dir()


def test_cleanup_failure_does_not_notify_fatal_callback_or_prevent_enable(tmp_path, monkeypatch):
    directory = tmp_path / "logs"
    directory.mkdir()
    expired = directory / "run_2026-08-01_session-20260801T000000000000_pid-1.log"
    expired.write_text("old", encoding="utf-8")
    errors: list[str] = []
    manager = RunLogManager(directory, now=lambda: datetime(2026, 8, 22, 12, 0), pid=2)
    manager.set_error_callback(errors.append)
    original_unlink = Path.unlink

    def fail_for_expired(path: Path, *args, **kwargs):
        if path == expired:
            raise PermissionError("in use")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_for_expired)

    active_path = manager.enable()
    manager.write("应用", "logging remains active")
    manager.close()

    assert expired.exists()
    assert errors == []
    assert "logging remains active" in active_path.read_text(encoding="utf-8")


def test_concurrent_writes_are_complete_and_not_interleaved(tmp_path):
    manager = RunLogManager(
        tmp_path / "logs",
        now=lambda: datetime(2026, 8, 22, 12, 0, 0, 456000),
        pid=9,
    )
    path = manager.enable()

    def write_batch(worker: int) -> None:
        for item in range(40):
            manager.write("并发", f"{worker}:{item}")

    with ThreadPoolExecutor(max_workers=8) as pool:
        tuple(pool.map(write_batch, range(8)))
    manager.close()

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 320
    messages = {line.rsplit(" ", 1)[-1] for line in lines}
    assert messages == {f"{worker}:{item}" for worker in range(8) for item in range(40)}
    assert all(
        line.startswith("2026-08-22 12:00:00.456 [INFO] [并发] ")
        for line in lines
    )


def test_enable_failure_raises_run_log_error(tmp_path):
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("blocked", encoding="utf-8")
    manager = RunLogManager(
        blocker / "logs",
        now=lambda: datetime(2026, 8, 22, 12, 0),
        pid=1,
    )

    with pytest.raises(RunLogError, match="无法启用运行日志"):
        manager.enable()

    assert manager.enabled is False
    assert manager.current_path is None


def test_runtime_write_failure_is_swallowed_disables_logging_and_notifies(tmp_path):
    clock = MutableClock(datetime(2026, 8, 22, 23, 59))
    errors: list[str] = []
    manager = RunLogManager(tmp_path / "logs", now=clock, pid=3)
    manager.set_error_callback(errors.append)
    manager.enable()
    manager.write("应用", "works")

    blocker = tmp_path / "logs" / manager._path_for_date(datetime(2026, 8, 23).date()).name
    blocker.mkdir()
    clock.value = datetime(2026, 8, 23, 0, 0)
    manager.write("应用", "must not escape")

    assert manager.enabled is False
    assert manager.current_path is None
    assert len(errors) == 1
    assert "写入运行日志失败，已自动停用" in errors[0]


def test_exception_hook_guard_logs_and_restores_existing_hooks(tmp_path, monkeypatch):
    manager = RunLogManager(
        tmp_path / "logs",
        now=lambda: datetime(2026, 8, 22, 12, 0),
        pid=4,
    )
    path = manager.enable()
    sys_calls: list[tuple[object, ...]] = []
    thread_calls: list[object] = []

    def previous_sys_hook(*args) -> None:
        sys_calls.append(args)

    def previous_thread_hook(args) -> None:
        thread_calls.append(args)

    monkeypatch.setattr(sys, "excepthook", previous_sys_hook)
    monkeypatch.setattr(threading, "excepthook", previous_thread_hook)
    guard = ExceptionHookGuard(manager)
    guard.install()

    try:
        raise ValueError("main boom")
    except ValueError:
        main_exc = sys.exc_info()
    sys.excepthook(*main_exc)

    try:
        raise RuntimeError("thread boom")
    except RuntimeError:
        thread_exc = sys.exc_info()
    args = SimpleNamespace(
        exc_type=thread_exc[0],
        exc_value=thread_exc[1],
        exc_traceback=thread_exc[2],
        thread=SimpleNamespace(name="worker-1"),
    )
    threading.excepthook(args)
    guard.restore()
    manager.close()

    assert guard.installed is False
    assert sys.excepthook is previous_sys_hook
    assert threading.excepthook is previous_thread_hook
    assert len(sys_calls) == 1
    assert thread_calls == [args]
    text = path.read_text(encoding="utf-8")
    assert "[ERROR] [应用未处理异常] Traceback (most recent call last):" in text
    assert "ValueError: main boom" in text
    assert "[ERROR] [后台线程未处理异常/worker-1] Traceback (most recent call last):" in text
    assert "RuntimeError: thread boom" in text
