from __future__ import annotations

import argparse
import ctypes
import hashlib
import hmac
import os
import re
import subprocess
import sys
import traceback
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator, Protocol, Sequence


SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0x00000000
WAIT_ABANDONED_0 = 0x00000080
WAIT_TIMEOUT = 0x00000102
WAIT_FAILED = 0xFFFFFFFF
ERROR_INVALID_PARAMETER = 87
WAIT_TIMEOUT_MS = 5 * 60 * 1000
UPDATE_MUTEX_TIMEOUT_MS = 5 * 60 * 1000
STARTUP_CONFIRM_SECONDS = 5.0
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_APPROVAL_TOKEN_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_UPDATE_TRANSACTION_GLOB = ".auto-bdsp-update-*"


class UpdateNotApproved(RuntimeError):
    pass


class UpdateCancelled(RuntimeError):
    pass


class ApplyUpdatePackages(Protocol):
    def __call__(
        self,
        patches: Sequence[Path],
        *,
        install_dir: Path,
        expected_version: str,
        expected_patch_sha256: Sequence[str],
        defer_commit: bool,
        log: Callable[[str], object],
    ) -> object: ...


class FileLogger:
    def __init__(self, path: Path) -> None:
        self.path = path

    def __call__(self, message: object) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(f"{timestamp} {message}\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Apply auto-bdsp-rng update packages.")
    parser.add_argument("--wait-pid", required=True, type=_positive_pid)
    parser.add_argument("--install-dir", required=True, type=Path)
    parser.add_argument("--current-version", required=True)
    parser.add_argument("--target-version", required=True)
    parser.add_argument("--approval-file", required=True, type=Path)
    parser.add_argument("--approval-token", required=True, type=_approval_token)
    parser.add_argument("--patch", required=True, action="append", type=Path)
    parser.add_argument("--patch-sha256", required=True, action="append")
    parser.add_argument("--launch", required=True, type=Path)
    parser.add_argument("--log", required=True, type=Path)
    return parser


def _positive_pid(value: str) -> int:
    try:
        pid = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("PID must be an integer") from exc
    if pid <= 0:
        raise argparse.ArgumentTypeError("PID must be positive")
    return pid


def _approval_token(value: str) -> str:
    if _APPROVAL_TOKEN_PATTERN.fullmatch(value) is None:
        raise argparse.ArgumentTypeError("approval token must be 32 lowercase hexadecimal characters")
    return value


def _load_kernel32():
    if os.name != "nt":
        raise OSError("The updater process waiter is only available on Windows")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
    kernel32.WaitForSingleObject.restype = ctypes.c_uint32
    kernel32.ReleaseMutex.argtypes = (ctypes.c_void_p,)
    kernel32.ReleaseMutex.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    kernel32.CloseHandle.restype = ctypes.c_int
    return kernel32


def _last_windows_error() -> int:
    return int(ctypes.get_last_error())


def _windows_error(error_code: int) -> OSError:
    win_error = getattr(ctypes, "WinError", None)
    if win_error is not None:
        return win_error(error_code)
    return OSError(error_code, f"Windows error {error_code}")


def wait_for_process_exit(
    pid: int,
    timeout_ms: int = WAIT_TIMEOUT_MS,
    *,
    kernel32=None,
    get_last_error: Callable[[], int] = _last_windows_error,
) -> None:
    if pid <= 0:
        raise ValueError("PID must be positive")
    if timeout_ms <= 0:
        raise ValueError("timeout_ms must be positive")

    api = kernel32 or _load_kernel32()
    handle = api.OpenProcess(SYNCHRONIZE, False, pid)
    if not handle:
        error_code = int(get_last_error())
        if error_code == ERROR_INVALID_PARAMETER:
            return
        raise _windows_error(error_code)

    try:
        result = int(api.WaitForSingleObject(handle, timeout_ms))
        if result == WAIT_OBJECT_0:
            return
        if result == WAIT_TIMEOUT:
            raise TimeoutError(f"等待主程序退出超时（{timeout_ms // 1000} 秒）")
        if result == WAIT_FAILED:
            raise _windows_error(int(get_last_error()))
        raise OSError(f"WaitForSingleObject returned unexpected result: 0x{result:08X}")
    finally:
        api.CloseHandle(handle)


def _install_mutex_name(install_dir: Path) -> str:
    normalized = os.path.normcase(str(install_dir.resolve())).casefold()
    suffix = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"Global\\auto-bdsp-rng-update-{suffix}"


@contextmanager
def install_update_mutex(
    install_dir: Path,
    timeout_ms: int = UPDATE_MUTEX_TIMEOUT_MS,
    *,
    kernel32=None,
    get_last_error: Callable[[], int] = _last_windows_error,
) -> Iterator[None]:
    if timeout_ms <= 0:
        raise ValueError("timeout_ms must be positive")
    if kernel32 is None and os.name != "nt":
        yield
        return

    api = kernel32 or _load_kernel32()
    handle = api.CreateMutexW(None, False, _install_mutex_name(install_dir))
    if not handle:
        raise _windows_error(int(get_last_error()))
    acquired = False
    try:
        result = int(api.WaitForSingleObject(handle, timeout_ms))
        if result in {WAIT_OBJECT_0, WAIT_ABANDONED_0}:
            acquired = True
        elif result == WAIT_TIMEOUT:
            raise TimeoutError(f"等待其他升级任务结束超时（{timeout_ms // 1000} 秒）")
        elif result == WAIT_FAILED:
            raise _windows_error(int(get_last_error()))
        else:
            raise OSError(f"WaitForSingleObject returned unexpected result: 0x{result:08X}")
        yield
    finally:
        if acquired:
            api.ReleaseMutex(handle)
        api.CloseHandle(handle)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def verify_patch_digests(patches: Sequence[Path], expected_sha256: Sequence[str]) -> None:
    if len(patches) != len(expected_sha256):
        raise ValueError("升级包数量与 SHA-256 数量不一致")
    for digest in expected_sha256:
        if _SHA256_PATTERN.fullmatch(digest) is None:
            raise ValueError("升级包 SHA-256 格式无效")
    for patch, expected_digest in zip(patches, expected_sha256, strict=True):
        if _sha256_file(patch) != expected_digest:
            raise ValueError(f"升级包外层 SHA-256 校验失败：{patch.name}")


def consume_update_approval(path: Path, expected_token: str, install_dir: Path) -> None:
    raw_approval_path = Path(path)
    if raw_approval_path.is_symlink():
        raise UpdateNotApproved("升级授权文件不能是符号链接")
    try:
        approval_path = raw_approval_path.resolve()
        resolved_install_dir = Path(install_dir).resolve()
    except OSError as exc:
        raise UpdateNotApproved(f"无法验证升级授权文件路径：{exc}") from exc
    try:
        approval_path.relative_to(resolved_install_dir)
    except ValueError as exc:
        raise UpdateNotApproved("升级授权文件不在安装目录内") from exc

    claimed_path = approval_path.with_name(
        f".approve-{uuid.uuid4().hex}.consumed-{os.getpid()}-{uuid.uuid4().hex}.token"
    )
    try:
        os.replace(approval_path, claimed_path)
    except FileNotFoundError as exc:
        raise UpdateNotApproved("主程序未授权本次安装") from exc
    except OSError as exc:
        raise UpdateNotApproved(f"无法占有升级授权文件：{exc}") from exc

    try:
        if claimed_path.is_symlink():
            raise UpdateNotApproved("升级授权文件不能是符号链接")
        if claimed_path.stat().st_size > 128:
            raise UpdateNotApproved("升级授权文件超过安全限制")
        approval_state = claimed_path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as exc:
        raise UpdateNotApproved(f"无法读取升级授权文件：{exc}") from exc
    finally:
        try:
            claimed_path.unlink()
        except OSError:
            pass
    state, separator, actual_token = approval_state.partition(":")
    token_matches = bool(separator) and hmac.compare_digest(actual_token, expected_token)
    if token_matches and state == "approved":
        return
    if token_matches and state == "cancelled":
        raise UpdateCancelled("主程序取消了本次安装")
    raise UpdateNotApproved("主程序未完成本次安装授权")


def _load_apply_update_packages() -> ApplyUpdatePackages:
    from auto_bdsp_rng.update_core import apply_update_packages

    return apply_update_packages


def _has_pending_update_transaction(install_dir: Path) -> bool:
    try:
        return any(install_dir.glob(_UPDATE_TRANSACTION_GLOB))
    except OSError:
        return True


def _resolve_launch_path(launch: Path, install_dir: Path) -> Path:
    candidate = launch if launch.is_absolute() else install_dir / launch
    resolved = candidate.resolve()
    try:
        resolved.relative_to(install_dir)
    except ValueError as exc:
        raise ValueError("启动程序必须位于安装目录内") from exc
    return resolved


def launch_updated_application(
    launch: Path,
    install_dir: Path,
) -> subprocess.Popen[bytes]:
    launch_path = _resolve_launch_path(launch, install_dir)
    if not launch_path.is_file():
        raise FileNotFoundError(f"更新后的启动程序不存在：{launch_path}")
    return subprocess.Popen(
        [str(launch_path)],
        cwd=str(install_dir),
        shell=False,
        close_fds=True,
    )


def stop_updated_application(
    process: object,
    *,
    timeout_seconds: float = 5.0,
) -> None:
    poll = getattr(process, "poll", None)
    if callable(poll) and poll() is not None:
        return
    terminate = getattr(process, "terminate", None)
    wait = getattr(process, "wait", None)
    if not callable(terminate) or not callable(wait):
        raise RuntimeError("无法控制已启动的新版程序进程")
    terminate()
    try:
        wait(timeout=timeout_seconds)
        return
    except subprocess.TimeoutExpired:
        pass
    kill = getattr(process, "kill", None)
    if not callable(kill):
        raise RuntimeError("新版程序终止超时且无法强制结束")
    kill()
    wait(timeout=timeout_seconds)


def show_error_message(message: str) -> None:
    if os.name != "nt":
        return
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.MessageBoxW.argtypes = (
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_uint32,
    )
    user32.MessageBoxW.restype = ctypes.c_int
    user32.MessageBoxW(
        None,
        message,
        "珍钻复刻自动乱数升级失败",
        0x00000000 | 0x00000010 | 0x00010000,
    )


def _safe_log(log: FileLogger, message: object) -> None:
    try:
        log(message)
    except BaseException:
        pass


def run_update(args: argparse.Namespace) -> int:
    install_dir = args.install_dir.resolve()
    patches = tuple(path.resolve() for path in args.patch)
    patch_sha256 = tuple(args.patch_sha256)
    log_path = args.log.resolve()
    log = FileLogger(log_path)
    safe_log = lambda message: _safe_log(log, message)
    transaction_dir: Path | None = None
    launched_process: object | None = None
    authorized = False
    safe_to_restart = False
    application_running = False

    try:
        if not install_dir.is_dir():
            raise NotADirectoryError(f"安装目录不存在：{install_dir}")
        if args.wait_pid == os.getpid():
            raise ValueError("等待 PID 不能是更新程序自身")

        safe_log(
            f"开始升级：{args.current_version} -> {args.target_version}；"
            f"补丁数量 {len(patches)}"
        )
        safe_log(f"等待主程序退出：PID {args.wait_pid}")
        wait_for_process_exit(args.wait_pid)
        try:
            consume_update_approval(args.approval_file, args.approval_token, install_dir)
        except UpdateCancelled as exc:
            safe_log(f"本次升级已取消：{exc}")
            return 0
        except UpdateNotApproved as exc:
            safe_log(f"本次升级未获授权：{exc}；正在重新启动原程序")
            launch_updated_application(args.launch, install_dir)
            return 0
        authorized = True
        safe_to_restart = True
        safe_log("主程序已确认关闭并授权安装")
        verify_patch_digests(patches, patch_sha256)
        safe_log("升级包外层 SHA-256 校验通过")
        safe_log("主程序已退出，等待安装目录升级锁")

        with install_update_mutex(install_dir):
            try:
                safe_log("已获得安装目录升级锁，正在重新校验升级包")
                verify_patch_digests(patches, patch_sha256)
                apply_update_packages = _load_apply_update_packages()
                safe_to_restart = False
                try:
                    update_result = apply_update_packages(
                        patches,
                        install_dir=install_dir,
                        expected_version=args.current_version,
                        expected_patch_sha256=patch_sha256,
                        defer_commit=True,
                        log=safe_log,
                    )
                except Exception as core_error:
                    rollback_state_missing = object()
                    rollback_completed = getattr(
                        core_error,
                        "rollback_completed",
                        rollback_state_missing,
                    )
                    safe_to_restart = rollback_completed is True or (
                        rollback_completed is rollback_state_missing
                        and not _has_pending_update_transaction(install_dir)
                    )
                    raise
                raw_transaction_dir = getattr(update_result, "transaction_dir", None)
                if raw_transaction_dir is None:
                    raise RuntimeError("升级核心未返回可延迟提交的事务")
                transaction_dir = Path(raw_transaction_dir)
                final_version = str(update_result)
                if final_version != args.target_version:
                    raise RuntimeError(
                        f"补丁完成后的版本为 {final_version}，预期为 {args.target_version}"
                    )

                safe_log(f"升级完成：{final_version}；正在重启应用")
                process = launch_updated_application(args.launch, install_dir)
                launched_process = process
                application_running = True
                try:
                    exit_code = process.wait(timeout=STARTUP_CONFIRM_SECONDS)
                except subprocess.TimeoutExpired:
                    from auto_bdsp_rng.update_core import commit_update_transaction

                    commit_update_transaction(transaction_dir, install_dir=install_dir)
                    transaction_dir = None
                    launched_process = None
                    safe_log("新版应用启动确认通过，旧版本备份已清理")
                else:
                    from auto_bdsp_rng.update_core import rollback_update_transaction

                    application_running = False
                    rollback_update_transaction(
                        transaction_dir,
                        install_dir=install_dir,
                        log=safe_log,
                    )
                    transaction_dir = None
                    launched_process = None
                    safe_log(f"新版应用过早退出（exit code: {exit_code}），已恢复旧版本")
                    safe_to_restart = True
                    launch_updated_application(args.launch, install_dir)
                    application_running = True
                    raise RuntimeError(
                        f"新版应用启动后过早退出（exit code: {exit_code}），已恢复旧版本"
                    )
            except Exception:
                if transaction_dir is not None:
                    if launched_process is not None:
                        stop_updated_application(launched_process)
                        launched_process = None
                        application_running = False
                        safe_log("提交升级失败，已停止新版程序")
                    from auto_bdsp_rng.update_core import rollback_update_transaction

                    rollback_update_transaction(
                        transaction_dir,
                        install_dir=install_dir,
                        log=safe_log,
                    )
                    transaction_dir = None
                    safe_log("升级未提交，已恢复旧版本")
                    safe_to_restart = True
                    launch_updated_application(args.launch, install_dir)
                    application_running = True
                    safe_log("旧版本已重新启动")
                raise
        return 0
    except Exception as exc:
        if authorized and safe_to_restart and not application_running:
            try:
                launch_updated_application(args.launch, install_dir)
                application_running = True
                _safe_log(log, "升级失败发生在文件替换前或已完成回滚，原程序已重新启动")
            except Exception as restart_error:
                _safe_log(log, f"升级失败后无法重新启动原程序：{restart_error}")
        _safe_log(log, f"升级失败：{exc}")
        _safe_log(log, traceback.format_exc())
        show_error_message(
            "升级失败，程序未能完成更新。\n\n"
            f"{exc}\n\n"
            f"详细日志：{log_path}"
        )
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_update(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
