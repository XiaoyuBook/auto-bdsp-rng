from __future__ import annotations

import hashlib
import importlib.util
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

import auto_bdsp_rng.update_core as update_core


ROOT = Path(__file__).resolve().parents[1]
ENTRY_PATH = ROOT / "packaging" / "updater_entry.py"
SPEC_PATH = ROOT / "packaging" / "auto-bdsp-rng-updater.spec"
APPROVAL_TOKEN = "a" * 32


def _load_updater_entry():
    spec = importlib.util.spec_from_file_location("updater_entry", ENTRY_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _approval_args(
    install_dir: Path,
    *,
    create: bool = True,
    state: str = "approved",
) -> list[str]:
    approval_file = install_dir / ".approve-update.token"
    if create:
        approval_file.write_text(f"{state}:{APPROVAL_TOKEN}\n", encoding="ascii")
    return [
        "--approval-file",
        str(approval_file),
        "--approval-token",
        APPROVAL_TOKEN,
    ]


@pytest.fixture
def updater():
    return _load_updater_entry()


class FakeKernel32:
    def __init__(self, *, handle=123, wait_result=0) -> None:
        self.handle = handle
        self.wait_result = wait_result
        self.open_calls: list[tuple[int, bool, int]] = []
        self.wait_calls: list[tuple[int, int]] = []
        self.closed: list[int] = []
        self.mutex_names: list[str] = []
        self.released: list[int] = []

    def OpenProcess(self, access: int, inherit: bool, pid: int):
        self.open_calls.append((access, inherit, pid))
        return self.handle

    def WaitForSingleObject(self, handle: int, timeout_ms: int) -> int:
        self.wait_calls.append((handle, timeout_ms))
        return self.wait_result

    def CreateMutexW(self, _security, _owned: bool, name: str):
        self.mutex_names.append(name)
        return self.handle

    def ReleaseMutex(self, handle: int) -> int:
        self.released.append(handle)
        return 1

    def CloseHandle(self, handle: int) -> int:
        self.closed.append(handle)
        return 1


def test_wait_for_process_exit_uses_windows_wait_handle(updater):
    api = FakeKernel32(wait_result=updater.WAIT_OBJECT_0)

    updater.wait_for_process_exit(4321, timeout_ms=2500, kernel32=api)

    assert api.open_calls == [(updater.SYNCHRONIZE, False, 4321)]
    assert api.wait_calls == [(123, 2500)]
    assert api.closed == [123]


def test_wait_for_process_exit_accepts_already_exited_process(updater):
    api = FakeKernel32(handle=0)

    updater.wait_for_process_exit(
        4321,
        kernel32=api,
        get_last_error=lambda: updater.ERROR_INVALID_PARAMETER,
    )

    assert api.wait_calls == []
    assert api.closed == []


def test_wait_for_process_exit_times_out_and_closes_handle(updater):
    api = FakeKernel32(wait_result=updater.WAIT_TIMEOUT)

    with pytest.raises(TimeoutError, match="等待主程序退出超时"):
        updater.wait_for_process_exit(4321, timeout_ms=1000, kernel32=api)

    assert api.closed == [123]


def test_install_update_mutex_is_global_and_released(tmp_path, updater):
    api = FakeKernel32(wait_result=updater.WAIT_OBJECT_0)

    with updater.install_update_mutex(tmp_path, timeout_ms=2500, kernel32=api):
        assert api.released == []

    assert len(api.mutex_names) == 1
    assert api.mutex_names[0].startswith("Global\\auto-bdsp-rng-update-")
    assert api.wait_calls == [(123, 2500)]
    assert api.released == [123]
    assert api.closed == [123]


def test_consume_update_approval_cannot_be_replayed_when_claim_cleanup_fails(
    tmp_path,
    monkeypatch,
    updater,
):
    install_dir = tmp_path / "app"
    install_dir.mkdir()
    approval_file = install_dir / ".approve-update.token"
    approval_file.write_text(f"approved:{APPROVAL_TOKEN}\n", encoding="ascii")
    original_unlink = updater.Path.unlink

    def unlink(path, *args, **kwargs):
        if ".consumed-" in path.name:
            raise PermissionError("claimed token is still open")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(updater.Path, "unlink", unlink)

    updater.consume_update_approval(approval_file, APPROVAL_TOKEN, install_dir)

    assert not approval_file.exists()
    assert len(list(install_dir.glob(".approve-*.consumed-*.token"))) == 1
    with pytest.raises(updater.UpdateNotApproved, match="未授权"):
        updater.consume_update_approval(approval_file, APPROVAL_TOKEN, install_dir)


def test_consume_update_approval_rejects_failed_atomic_claim(
    tmp_path,
    monkeypatch,
    updater,
):
    install_dir = tmp_path / "app"
    install_dir.mkdir()
    approval_file = install_dir / ".approve-update.token"
    approval_file.write_text(f"approved:{APPROVAL_TOKEN}\n", encoding="ascii")
    monkeypatch.setattr(
        updater.os,
        "replace",
        lambda *_args: (_ for _ in ()).throw(PermissionError("claim denied")),
    )

    with pytest.raises(updater.UpdateNotApproved, match="无法占有"):
        updater.consume_update_approval(approval_file, APPROVAL_TOKEN, install_dir)

    assert approval_file.exists()


def test_consume_update_approval_wraps_path_validation_errors(
    tmp_path,
    monkeypatch,
    updater,
):
    install_dir = tmp_path / "app"
    install_dir.mkdir()
    approval_file = install_dir / ".approve-update.token"
    approval_file.write_text(f"approved:{APPROVAL_TOKEN}\n", encoding="ascii")
    original_resolve = updater.Path.resolve

    def resolve(path, *args, **kwargs):
        if path == approval_file:
            raise OSError("path lookup failed")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(updater.Path, "resolve", resolve)

    with pytest.raises(updater.UpdateNotApproved, match="无法验证"):
        updater.consume_update_approval(approval_file, APPROVAL_TOKEN, install_dir)

    assert approval_file.exists()


def test_run_update_applies_patches_checks_version_and_restarts(tmp_path, monkeypatch, updater):
    install_dir = tmp_path / "安装 目录"
    install_dir.mkdir()
    patches = [tmp_path / "one.patch", tmp_path / "two.patch"]
    patches[0].write_bytes(b"first patch")
    patches[1].write_bytes(b"second patch")
    launch = install_dir / "珍钻复刻自动乱数.exe"
    launch.write_bytes(b"exe")
    log_path = tmp_path / "日志" / "update.log"
    waited: list[int] = []
    applied: list[tuple[object, Path, str, object]] = []
    launched: list[tuple[Path, Path]] = []
    committed: list[tuple[Path, Path]] = []
    transaction_dir = install_dir / ".auto-bdsp-update-test"
    transaction_dir.mkdir()

    class DeferredResult:
        def __init__(self) -> None:
            self.transaction_dir = transaction_dir

        def __str__(self) -> str:
            return "2.3.0"

    class RunningProcess:
        def wait(self, *, timeout: float) -> int:
            raise updater.subprocess.TimeoutExpired("app.exe", timeout)

    def apply_update_packages(
        items,
        *,
        install_dir,
        expected_version,
        expected_patch_sha256,
        defer_commit,
        log,
    ):
        assert defer_commit is True
        applied.append((items, install_dir, expected_version, expected_patch_sha256))
        log("核心补丁日志")
        return DeferredResult()

    monkeypatch.setattr(updater, "wait_for_process_exit", waited.append)
    monkeypatch.setattr(updater, "_load_apply_update_packages", lambda: apply_update_packages)
    monkeypatch.setattr(
        updater,
        "launch_updated_application",
        lambda path, cwd: launched.append((path, cwd)) or RunningProcess(),
    )
    monkeypatch.setattr(
        update_core,
        "commit_update_transaction",
        lambda path, *, install_dir: committed.append((path, install_dir)),
    )

    result = updater.main(
        [
            *_approval_args(install_dir),
            "--wait-pid",
            "4321",
            "--install-dir",
            str(install_dir),
            "--current-version",
            "2.2.0",
            "--target-version",
            "2.3.0",
            "--patch",
            str(patches[0]),
            "--patch",
            str(patches[1]),
            "--patch-sha256",
            hashlib.sha256(patches[0].read_bytes()).hexdigest(),
            "--patch-sha256",
            hashlib.sha256(patches[1].read_bytes()).hexdigest(),
            "--launch",
            launch.name,
            "--log",
            str(log_path),
        ]
    )

    assert result == 0
    assert waited == [4321]
    assert applied == [
        (
            tuple(path.resolve() for path in patches),
            install_dir.resolve(),
            "2.2.0",
            tuple(hashlib.sha256(path.read_bytes()).hexdigest() for path in patches),
        )
    ]
    assert launched == [(Path(launch.name), install_dir.resolve())]
    assert committed == [(transaction_dir, install_dir.resolve())]
    text = log_path.read_text(encoding="utf-8")
    assert "核心补丁日志" in text
    assert "升级完成：2.3.0" in text


def test_run_update_rejects_unexpected_final_version_and_logs_utf8(
    tmp_path, monkeypatch, updater
):
    install_dir = tmp_path / "app"
    install_dir.mkdir()
    patch = tmp_path / "update.patch"
    patch.write_bytes(b"patch")
    log_path = tmp_path / "update.log"
    shown: list[str] = []
    launched: list[object] = []
    rolled_back: list[Path] = []
    transaction_dir = install_dir / ".auto-bdsp-update-test"
    transaction_dir.mkdir()

    class DeferredResult:
        def __init__(self) -> None:
            self.transaction_dir = transaction_dir

        def __str__(self) -> str:
            return "2.2.1"

    monkeypatch.setattr(updater, "wait_for_process_exit", lambda _pid: None)
    monkeypatch.setattr(
        updater,
        "_load_apply_update_packages",
        lambda: lambda *_a, **_kw: DeferredResult(),
    )
    monkeypatch.setattr(updater, "launch_updated_application", lambda *_args: launched.append(True))
    monkeypatch.setattr(
        update_core,
        "rollback_update_transaction",
        lambda path, **_kwargs: rolled_back.append(path),
    )
    monkeypatch.setattr(updater, "show_error_message", shown.append)

    result = updater.main(
        [
            *_approval_args(install_dir),
            "--wait-pid",
            "4321",
            "--install-dir",
            str(install_dir),
            "--current-version",
            "2.2.0",
            "--target-version",
            "2.3.0",
            "--patch",
            str(patch),
            "--patch-sha256",
            hashlib.sha256(patch.read_bytes()).hexdigest(),
            "--launch",
            "app.exe",
            "--log",
            str(log_path),
        ]
    )

    assert result == 1
    assert launched == [True]
    assert rolled_back == [transaction_dir]
    assert len(shown) == 1
    assert "升级失败" in shown[0]
    assert "预期为 2.3.0" in shown[0]
    assert "补丁完成后的版本为 2.2.1" in log_path.read_text(encoding="utf-8")


def test_run_update_without_close_approval_restarts_without_applying(
    tmp_path,
    monkeypatch,
    updater,
):
    install_dir = tmp_path / "app"
    install_dir.mkdir()
    patch = tmp_path / "update.patch"
    patch.write_bytes(b"patch")
    applied: list[bool] = []
    launched: list[bool] = []
    shown: list[str] = []
    log_path = tmp_path / "update.log"
    monkeypatch.setattr(updater, "wait_for_process_exit", lambda _pid: None)
    monkeypatch.setattr(
        updater,
        "_load_apply_update_packages",
        lambda: lambda *_args, **_kwargs: applied.append(True),
    )
    monkeypatch.setattr(updater, "show_error_message", shown.append)
    monkeypatch.setattr(
        updater,
        "launch_updated_application",
        lambda *_args: launched.append(True),
    )

    result = updater.main(
        [
            *_approval_args(install_dir, create=False),
            "--wait-pid",
            "4321",
            "--install-dir",
            str(install_dir),
            "--current-version",
            "2.2.0",
            "--target-version",
            "2.3.0",
            "--patch",
            str(patch),
            "--patch-sha256",
            hashlib.sha256(patch.read_bytes()).hexdigest(),
            "--launch",
            "app.exe",
            "--log",
            str(log_path),
        ]
    )

    assert result == 0
    assert applied == []
    assert shown == []
    assert launched == [True]
    assert "未获授权" in log_path.read_text(encoding="utf-8")


def test_run_update_with_pending_approval_restarts_without_applying(
    tmp_path,
    monkeypatch,
    updater,
):
    install_dir = tmp_path / "app"
    install_dir.mkdir()
    patch = tmp_path / "update.patch"
    patch.write_bytes(b"patch")
    applied: list[bool] = []
    launched: list[bool] = []
    shown: list[str] = []
    log_path = tmp_path / "update.log"
    monkeypatch.setattr(updater, "wait_for_process_exit", lambda _pid: None)
    monkeypatch.setattr(
        updater,
        "_load_apply_update_packages",
        lambda: lambda *_args, **_kwargs: applied.append(True),
    )
    monkeypatch.setattr(updater, "show_error_message", shown.append)
    monkeypatch.setattr(
        updater,
        "launch_updated_application",
        lambda *_args: launched.append(True),
    )

    result = updater.main(
        [
            *_approval_args(install_dir, state="pending"),
            "--wait-pid",
            "4321",
            "--install-dir",
            str(install_dir),
            "--current-version",
            "2.2.0",
            "--target-version",
            "2.3.0",
            "--patch",
            str(patch),
            "--patch-sha256",
            hashlib.sha256(patch.read_bytes()).hexdigest(),
            "--launch",
            "app.exe",
            "--log",
            str(log_path),
        ]
    )

    assert result == 0
    assert applied == []
    assert shown == []
    assert launched == [True]
    assert "未获授权" in log_path.read_text(encoding="utf-8")


def test_run_update_with_cancelled_approval_does_not_apply_or_restart(
    tmp_path,
    monkeypatch,
    updater,
):
    install_dir = tmp_path / "app"
    install_dir.mkdir()
    patch = tmp_path / "update.patch"
    patch.write_bytes(b"patch")
    applied: list[bool] = []
    launched: list[bool] = []
    shown: list[str] = []
    log_path = tmp_path / "update.log"
    monkeypatch.setattr(updater, "wait_for_process_exit", lambda _pid: None)
    monkeypatch.setattr(
        updater,
        "_load_apply_update_packages",
        lambda: lambda *_args, **_kwargs: applied.append(True),
    )
    monkeypatch.setattr(updater, "show_error_message", shown.append)
    monkeypatch.setattr(
        updater,
        "launch_updated_application",
        lambda *_args: launched.append(True),
    )

    result = updater.main(
        [
            *_approval_args(install_dir, state="cancelled"),
            "--wait-pid",
            "4321",
            "--install-dir",
            str(install_dir),
            "--current-version",
            "2.2.0",
            "--target-version",
            "2.3.0",
            "--patch",
            str(patch),
            "--patch-sha256",
            hashlib.sha256(patch.read_bytes()).hexdigest(),
            "--launch",
            "app.exe",
            "--log",
            str(log_path),
        ]
    )

    assert result == 0
    assert applied == []
    assert shown == []
    assert launched == []
    assert "已取消" in log_path.read_text(encoding="utf-8")


def test_run_update_commits_only_after_new_application_stays_running(
    tmp_path,
    monkeypatch,
    updater,
):
    install_dir = tmp_path / "app"
    install_dir.mkdir()
    patch = tmp_path / "update.patch"
    patch.write_bytes(b"patch")
    transaction_dir = install_dir / ".auto-bdsp-update-test"
    transaction_dir.mkdir()
    log_path = tmp_path / "update.log"
    committed: list[tuple[Path, Path]] = []

    class DeferredResult:
        def __init__(self) -> None:
            self.transaction_dir = transaction_dir

        def __str__(self) -> str:
            return "2.3.0"

    class RunningProcess:
        def wait(self, *, timeout: float) -> int:
            raise updater.subprocess.TimeoutExpired("app.exe", timeout)

    monkeypatch.setattr(updater, "wait_for_process_exit", lambda _pid: None)
    monkeypatch.setattr(
        updater,
        "_load_apply_update_packages",
        lambda: lambda *_args, **_kwargs: DeferredResult(),
    )
    monkeypatch.setattr(updater, "launch_updated_application", lambda *_args: RunningProcess())
    monkeypatch.setattr(
        update_core,
        "commit_update_transaction",
        lambda path, *, install_dir: committed.append((path, install_dir)),
    )

    result = updater.main(
        [
            *_approval_args(install_dir),
            "--wait-pid",
            "4321",
            "--install-dir",
            str(install_dir),
            "--current-version",
            "2.2.0",
            "--target-version",
            "2.3.0",
            "--patch",
            str(patch),
            "--patch-sha256",
            hashlib.sha256(patch.read_bytes()).hexdigest(),
            "--launch",
            "app.exe",
            "--log",
            str(log_path),
        ]
    )

    assert result == 0
    assert committed == [(transaction_dir, install_dir.resolve())]


def test_commit_failure_stops_new_app_and_rolls_back_before_releasing_mutex(
    tmp_path,
    monkeypatch,
    updater,
):
    install_dir = tmp_path / "app"
    install_dir.mkdir()
    patch = tmp_path / "update.patch"
    patch.write_bytes(b"patch")
    transaction_dir = install_dir / ".auto-bdsp-update-test"
    transaction_dir.mkdir()
    log_path = tmp_path / "update.log"
    events: list[object] = []
    launches = 0

    class DeferredResult:
        def __init__(self) -> None:
            self.transaction_dir = transaction_dir

        def __str__(self) -> str:
            return "2.3.0"

    class RunningProcess:
        running = True

        def poll(self):
            return None if self.running else 1

        def wait(self, *, timeout: float) -> int:
            events.append(("wait", timeout))
            if self.running:
                raise updater.subprocess.TimeoutExpired("app.exe", timeout)
            return 1

        def terminate(self) -> None:
            events.append("terminate")
            self.running = False

        def kill(self) -> None:
            events.append("kill")
            self.running = False

    @contextmanager
    def mutex(_install_dir):
        events.append("mutex-enter")
        try:
            yield
        finally:
            events.append("mutex-exit")

    def launch(*_args):
        nonlocal launches
        launches += 1
        events.append(("launch", launches))
        return RunningProcess() if launches == 1 else SimpleNamespace(pid=2)

    monkeypatch.setattr(updater, "wait_for_process_exit", lambda _pid: None)
    monkeypatch.setattr(updater, "install_update_mutex", mutex)
    monkeypatch.setattr(
        updater,
        "_load_apply_update_packages",
        lambda: lambda *_args, **_kwargs: DeferredResult(),
    )
    monkeypatch.setattr(updater, "launch_updated_application", launch)
    monkeypatch.setattr(
        update_core,
        "commit_update_transaction",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("commit failed")),
    )
    monkeypatch.setattr(
        update_core,
        "rollback_update_transaction",
        lambda *_args, **_kwargs: events.append("rollback"),
    )
    monkeypatch.setattr(updater, "show_error_message", lambda _message: None)

    result = updater.main(
        [
            *_approval_args(install_dir),
            "--wait-pid",
            "4321",
            "--install-dir",
            str(install_dir),
            "--current-version",
            "2.2.0",
            "--target-version",
            "2.3.0",
            "--patch",
            str(patch),
            "--patch-sha256",
            hashlib.sha256(patch.read_bytes()).hexdigest(),
            "--launch",
            "app.exe",
            "--log",
            str(log_path),
        ]
    )

    assert result == 1
    assert events.index("rollback") < events.index("mutex-exit")
    assert events == [
        "mutex-enter",
        ("launch", 1),
        ("wait", updater.STARTUP_CONFIRM_SECONDS),
        "terminate",
        ("wait", 5.0),
        "rollback",
        ("launch", 2),
        "mutex-exit",
    ]


def test_commit_failure_retries_old_app_launch_after_successful_rollback(
    tmp_path,
    monkeypatch,
    updater,
):
    install_dir = tmp_path / "app"
    install_dir.mkdir()
    patch = tmp_path / "update.patch"
    patch.write_bytes(b"patch")
    transaction_dir = install_dir / ".auto-bdsp-update-test"
    transaction_dir.mkdir()
    log_path = tmp_path / "update.log"
    launches = 0
    rollbacks: list[Path] = []

    class DeferredResult:
        def __init__(self) -> None:
            self.transaction_dir = transaction_dir

        def __str__(self) -> str:
            return "2.3.0"

    class RunningProcess:
        running = True

        def poll(self):
            return None if self.running else 1

        def wait(self, *, timeout: float) -> int:
            if self.running:
                raise updater.subprocess.TimeoutExpired("app.exe", timeout)
            return 1

        def terminate(self) -> None:
            self.running = False

        def kill(self) -> None:
            self.running = False

    def launch(*_args):
        nonlocal launches
        launches += 1
        if launches == 1:
            return RunningProcess()
        if launches == 2:
            raise OSError("first old-app launch failed")
        return SimpleNamespace(pid=3)

    monkeypatch.setattr(updater, "wait_for_process_exit", lambda _pid: None)
    monkeypatch.setattr(
        updater,
        "_load_apply_update_packages",
        lambda: lambda *_args, **_kwargs: DeferredResult(),
    )
    monkeypatch.setattr(updater, "launch_updated_application", launch)
    monkeypatch.setattr(
        update_core,
        "commit_update_transaction",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("commit failed")),
    )
    monkeypatch.setattr(
        update_core,
        "rollback_update_transaction",
        lambda path, **_kwargs: rollbacks.append(path),
    )
    monkeypatch.setattr(updater, "show_error_message", lambda _message: None)

    result = updater.main(
        [
            *_approval_args(install_dir),
            "--wait-pid",
            "4321",
            "--install-dir",
            str(install_dir),
            "--current-version",
            "2.2.0",
            "--target-version",
            "2.3.0",
            "--patch",
            str(patch),
            "--patch-sha256",
            hashlib.sha256(patch.read_bytes()).hexdigest(),
            "--launch",
            "app.exe",
            "--log",
            str(log_path),
        ]
    )

    assert result == 1
    assert rollbacks == [transaction_dir]
    assert launches == 3
    assert "已完成回滚，原程序已重新启动" in log_path.read_text(encoding="utf-8")


def test_run_update_rolls_back_and_relaunches_old_version_on_early_exit(
    tmp_path,
    monkeypatch,
    updater,
):
    install_dir = tmp_path / "app"
    install_dir.mkdir()
    patch = tmp_path / "update.patch"
    patch.write_bytes(b"patch")
    transaction_dir = install_dir / ".auto-bdsp-update-test"
    transaction_dir.mkdir()
    log_path = tmp_path / "update.log"
    rolled_back: list[tuple[Path, Path]] = []
    launches: list[object] = []
    shown: list[str] = []

    class DeferredResult:
        def __init__(self) -> None:
            self.transaction_dir = transaction_dir

        def __str__(self) -> str:
            return "2.3.0"

    class ExitedProcess:
        def wait(self, *, timeout: float) -> int:
            return 7

    def launch(*_args):
        launches.append(True)
        return ExitedProcess() if len(launches) == 1 else SimpleNamespace(pid=2)

    monkeypatch.setattr(updater, "wait_for_process_exit", lambda _pid: None)
    monkeypatch.setattr(
        updater,
        "_load_apply_update_packages",
        lambda: lambda *_args, **_kwargs: DeferredResult(),
    )
    monkeypatch.setattr(updater, "launch_updated_application", launch)
    monkeypatch.setattr(
        update_core,
        "rollback_update_transaction",
        lambda path, *, install_dir, log: rolled_back.append((path, install_dir)),
    )
    monkeypatch.setattr(updater, "show_error_message", shown.append)

    result = updater.main(
        [
            *_approval_args(install_dir),
            "--wait-pid",
            "4321",
            "--install-dir",
            str(install_dir),
            "--current-version",
            "2.2.0",
            "--target-version",
            "2.3.0",
            "--patch",
            str(patch),
            "--patch-sha256",
            hashlib.sha256(patch.read_bytes()).hexdigest(),
            "--launch",
            "app.exe",
            "--log",
            str(log_path),
        ]
    )

    assert result == 1
    assert rolled_back == [(transaction_dir, install_dir.resolve())]
    assert len(launches) == 2
    assert "已恢复旧版本" in shown[0]


def test_early_exit_retries_old_app_launch_after_successful_rollback(
    tmp_path,
    monkeypatch,
    updater,
):
    install_dir = tmp_path / "app"
    install_dir.mkdir()
    patch = tmp_path / "update.patch"
    patch.write_bytes(b"patch")
    transaction_dir = install_dir / ".auto-bdsp-update-test"
    transaction_dir.mkdir()
    log_path = tmp_path / "update.log"
    launches = 0
    rollbacks: list[Path] = []

    class DeferredResult:
        def __init__(self) -> None:
            self.transaction_dir = transaction_dir

        def __str__(self) -> str:
            return "2.3.0"

    class ExitedProcess:
        def wait(self, *, timeout: float) -> int:
            return 7

    def launch(*_args):
        nonlocal launches
        launches += 1
        if launches == 1:
            return ExitedProcess()
        if launches == 2:
            raise OSError("first old-app launch failed")
        return SimpleNamespace(pid=3)

    monkeypatch.setattr(updater, "wait_for_process_exit", lambda _pid: None)
    monkeypatch.setattr(
        updater,
        "_load_apply_update_packages",
        lambda: lambda *_args, **_kwargs: DeferredResult(),
    )
    monkeypatch.setattr(updater, "launch_updated_application", launch)
    monkeypatch.setattr(
        update_core,
        "rollback_update_transaction",
        lambda path, **_kwargs: rollbacks.append(path),
    )
    monkeypatch.setattr(updater, "show_error_message", lambda _message: None)

    result = updater.main(
        [
            *_approval_args(install_dir),
            "--wait-pid",
            "4321",
            "--install-dir",
            str(install_dir),
            "--current-version",
            "2.2.0",
            "--target-version",
            "2.3.0",
            "--patch",
            str(patch),
            "--patch-sha256",
            hashlib.sha256(patch.read_bytes()).hexdigest(),
            "--launch",
            "app.exe",
            "--log",
            str(log_path),
        ]
    )

    assert result == 1
    assert rollbacks == [transaction_dir]
    assert launches == 3
    assert "已完成回滚，原程序已重新启动" in log_path.read_text(encoding="utf-8")


def test_run_update_rechecks_patch_inside_mutex_before_loading_core(
    tmp_path,
    monkeypatch,
    updater,
):
    install_dir = tmp_path / "app"
    install_dir.mkdir()
    patch = tmp_path / "update.patch"
    patch.write_bytes(b"verified patch")
    expected_digest = hashlib.sha256(patch.read_bytes()).hexdigest()
    log_path = tmp_path / "update.log"
    loaded_core: list[bool] = []
    launched: list[bool] = []

    @contextmanager
    def mutate_patch_while_acquiring_mutex(_install_dir):
        patch.write_bytes(b"replaced after approval verification")
        yield

    monkeypatch.setattr(updater, "wait_for_process_exit", lambda _pid: None)
    monkeypatch.setattr(updater, "install_update_mutex", mutate_patch_while_acquiring_mutex)
    monkeypatch.setattr(
        updater,
        "_load_apply_update_packages",
        lambda: loaded_core.append(True),
    )
    monkeypatch.setattr(
        updater,
        "launch_updated_application",
        lambda *_args: launched.append(True),
    )
    monkeypatch.setattr(updater, "show_error_message", lambda _message: None)

    result = updater.main(
        [
            *_approval_args(install_dir),
            "--wait-pid",
            "4321",
            "--install-dir",
            str(install_dir),
            "--current-version",
            "2.2.0",
            "--target-version",
            "2.3.0",
            "--patch",
            str(patch),
            "--patch-sha256",
            expected_digest,
            "--launch",
            "app.exe",
            "--log",
            str(log_path),
        ]
    )

    assert result == 1
    assert loaded_core == []
    assert launched == [True]
    assert "重新校验升级包" in log_path.read_text(encoding="utf-8")


def test_run_update_restarts_old_app_when_core_load_fails_before_transaction(
    tmp_path,
    monkeypatch,
    updater,
):
    install_dir = tmp_path / "app"
    install_dir.mkdir()
    patch = tmp_path / "update.patch"
    patch.write_bytes(b"patch")
    log_path = tmp_path / "update.log"
    launched: list[bool] = []

    monkeypatch.setattr(updater, "wait_for_process_exit", lambda _pid: None)
    monkeypatch.setattr(
        updater,
        "_load_apply_update_packages",
        lambda: (_ for _ in ()).throw(ImportError("core import failed")),
    )
    monkeypatch.setattr(
        updater,
        "launch_updated_application",
        lambda *_args: launched.append(True),
    )
    monkeypatch.setattr(updater, "show_error_message", lambda _message: None)

    result = updater.main(
        [
            *_approval_args(install_dir),
            "--wait-pid",
            "4321",
            "--install-dir",
            str(install_dir),
            "--current-version",
            "2.2.0",
            "--target-version",
            "2.3.0",
            "--patch",
            str(patch),
            "--patch-sha256",
            hashlib.sha256(patch.read_bytes()).hexdigest(),
            "--launch",
            "app.exe",
            "--log",
            str(log_path),
        ]
    )

    assert result == 1
    assert launched == [True]
    assert "原程序已重新启动" in log_path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("pending_transaction", "expected_launches"),
    [(False, [True]), (True, [])],
)
def test_core_failure_restarts_only_without_a_pending_transaction(
    tmp_path,
    monkeypatch,
    updater,
    pending_transaction,
    expected_launches,
):
    install_dir = tmp_path / "app"
    install_dir.mkdir()
    if pending_transaction:
        (install_dir / ".auto-bdsp-update-incomplete").mkdir()
    patch = tmp_path / "update.patch"
    patch.write_bytes(b"patch")
    log_path = tmp_path / "update.log"
    launched: list[bool] = []

    def fail_before_transaction(*_args, **_kwargs):
        raise OSError("transaction directory creation failed")

    monkeypatch.setattr(updater, "wait_for_process_exit", lambda _pid: None)
    monkeypatch.setattr(
        updater,
        "_load_apply_update_packages",
        lambda: fail_before_transaction,
    )
    monkeypatch.setattr(
        updater,
        "launch_updated_application",
        lambda *_args: launched.append(True),
    )
    monkeypatch.setattr(updater, "show_error_message", lambda _message: None)

    result = updater.main(
        [
            *_approval_args(install_dir),
            "--wait-pid",
            "4321",
            "--install-dir",
            str(install_dir),
            "--current-version",
            "2.2.0",
            "--target-version",
            "2.3.0",
            "--patch",
            str(patch),
            "--patch-sha256",
            hashlib.sha256(patch.read_bytes()).hexdigest(),
            "--launch",
            "app.exe",
            "--log",
            str(log_path),
        ]
    )

    assert result == 1
    assert launched == expected_launches


def test_run_update_rejects_mismatched_patch_and_digest_counts(
    tmp_path, monkeypatch, updater
):
    install_dir = tmp_path / "app"
    install_dir.mkdir()
    patches = [tmp_path / "one.patch", tmp_path / "two.patch"]
    for patch in patches:
        patch.write_bytes(patch.name.encode("ascii"))
    log_path = tmp_path / "update.log"
    waited: list[int] = []
    applied: list[object] = []
    launched: list[bool] = []
    shown: list[str] = []

    monkeypatch.setattr(updater, "wait_for_process_exit", waited.append)
    monkeypatch.setattr(
        updater,
        "_load_apply_update_packages",
        lambda: lambda *args, **kwargs: applied.append((args, kwargs)),
    )
    monkeypatch.setattr(
        updater,
        "launch_updated_application",
        lambda *_args: launched.append(True),
    )
    monkeypatch.setattr(updater, "show_error_message", shown.append)

    result = updater.main(
        [
            *_approval_args(install_dir),
            "--wait-pid",
            "4321",
            "--install-dir",
            str(install_dir),
            "--current-version",
            "2.2.0",
            "--target-version",
            "2.3.0",
            "--patch",
            str(patches[0]),
            "--patch",
            str(patches[1]),
            "--patch-sha256",
            hashlib.sha256(patches[0].read_bytes()).hexdigest(),
            "--launch",
            "app.exe",
            "--log",
            str(log_path),
        ]
    )

    assert result == 1
    assert waited == [4321]
    assert applied == []
    assert launched == [True]
    assert len(shown) == 1
    assert "数量" in shown[0]
    assert "数量" in log_path.read_text(encoding="utf-8")


def test_run_update_rejects_invalid_patch_digest(tmp_path, monkeypatch, updater):
    install_dir = tmp_path / "app"
    install_dir.mkdir()
    patch = tmp_path / "update.patch"
    patch.write_bytes(b"patch")
    log_path = tmp_path / "update.log"
    waited: list[int] = []
    applied: list[object] = []
    launched: list[bool] = []
    shown: list[str] = []

    monkeypatch.setattr(updater, "wait_for_process_exit", waited.append)
    monkeypatch.setattr(
        updater,
        "_load_apply_update_packages",
        lambda: lambda *args, **kwargs: applied.append((args, kwargs)),
    )
    monkeypatch.setattr(
        updater,
        "launch_updated_application",
        lambda *_args: launched.append(True),
    )
    monkeypatch.setattr(updater, "show_error_message", shown.append)

    result = updater.main(
        [
            *_approval_args(install_dir),
            "--wait-pid",
            "4321",
            "--install-dir",
            str(install_dir),
            "--current-version",
            "2.2.0",
            "--target-version",
            "2.3.0",
            "--patch",
            str(patch),
            "--patch-sha256",
            "not-a-sha256",
            "--launch",
            "app.exe",
            "--log",
            str(log_path),
        ]
    )

    assert result == 1
    assert waited == [4321]
    assert applied == []
    assert launched == [True]
    assert len(shown) == 1
    assert not (install_dir / ".approve-update.token").exists()
    assert "SHA-256" in shown[0]
    assert "SHA-256" in log_path.read_text(encoding="utf-8")


def test_run_update_rejects_patch_with_mismatched_outer_sha256(
    tmp_path, monkeypatch, updater
):
    install_dir = tmp_path / "app"
    install_dir.mkdir()
    patch = tmp_path / "update.patch"
    patch.write_bytes(b"patch archive")
    log_path = tmp_path / "update.log"
    waited: list[int] = []
    applied: list[object] = []
    launched: list[object] = []
    shown: list[str] = []

    monkeypatch.setattr(updater, "wait_for_process_exit", waited.append)
    monkeypatch.setattr(
        updater,
        "_load_apply_update_packages",
        lambda: lambda *args, **kwargs: applied.append((args, kwargs)),
    )
    monkeypatch.setattr(updater, "launch_updated_application", lambda *_args: launched.append(True))
    monkeypatch.setattr(updater, "show_error_message", shown.append)

    result = updater.main(
        [
            *_approval_args(install_dir),
            "--wait-pid",
            "4321",
            "--install-dir",
            str(install_dir),
            "--current-version",
            "2.2.0",
            "--target-version",
            "2.3.0",
            "--patch",
            str(patch),
            "--patch-sha256",
            "0" * 64,
            "--launch",
            "app.exe",
            "--log",
            str(log_path),
        ]
    )

    assert result == 1
    assert waited == [4321]
    assert applied == []
    assert launched == [True]
    assert len(shown) == 1
    assert "SHA-256" in shown[0]
    assert "SHA-256" in log_path.read_text(encoding="utf-8")


def test_launch_updated_application_uses_argument_list_and_install_cwd(
    tmp_path, monkeypatch, updater
):
    install_dir = tmp_path / "带空格 & 中文"
    install_dir.mkdir()
    launch = install_dir / "珍钻复刻自动乱数.exe"
    launch.write_bytes(b"exe")
    calls: list[tuple[object, object]] = []

    def popen(arguments, **kwargs):
        calls.append((arguments, kwargs))
        return SimpleNamespace(pid=12)

    monkeypatch.setattr(updater.subprocess, "Popen", popen)

    updater.launch_updated_application(Path(launch.name), install_dir.resolve())

    assert calls == [
        (
            [str(launch.resolve())],
            {
                "cwd": str(install_dir.resolve()),
                "shell": False,
                "close_fds": True,
            },
        )
    ]


def test_launch_updated_application_rejects_path_outside_install_dir(tmp_path, updater):
    install_dir = tmp_path / "app"
    install_dir.mkdir()
    outside = tmp_path / "other.exe"
    outside.write_bytes(b"exe")

    with pytest.raises(ValueError, match="必须位于安装目录内"):
        updater.launch_updated_application(outside, install_dir.resolve())


def test_updater_spec_is_minimal_windowed_onefile():
    text = SPEC_PATH.read_text(encoding="utf-8")

    assert 'hiddenimports=["auto_bdsp_rng.update_core"]' in text
    assert 'name="auto-bdsp-rng-updater"' in text
    assert "console=False" in text
    assert "COLLECT(" not in text
    assert "collect_all" not in text
    for package in ("PySide6", "paddle", "paddleocr", "paddlex", "cv2", "numpy"):
        assert f'"{package}"' in text.partition("excludes=[")[2].partition("]")[0]
