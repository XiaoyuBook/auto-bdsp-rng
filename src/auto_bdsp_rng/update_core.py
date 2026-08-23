from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import time
import unicodedata
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


APPLICATION_ID = "auto-bdsp-rng"
UPDATE_PLATFORM = "windows-x64"
PATCH_SCHEMA_VERSION = 1
PATCH_MANIFEST_NAME = "update.json"
PATCH_PAYLOAD_PREFIX = "payload/"
TRANSACTION_SCHEMA_VERSION = 1
TRANSACTION_JOURNAL_NAME = "transaction.json"
TRANSACTION_DIR_PREFIX = ".auto-bdsp-update-"
TRANSACTION_CLEANUP_DIR_PREFIX = ".auto-bdsp-cleanup-"
MAX_PATCH_FILES = 50_000
MAX_PATCH_EXPANDED_SIZE = 4 * 1024 * 1024 * 1024
MAX_PATCH_ARCHIVE_SIZE = 2 * 1024 * 1024 * 1024
MAX_MANIFEST_SIZE = 8 * 1024 * 1024
MAX_PRESERVED_SIDECAR_CANDIDATES = 1000
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_VERSION_PATTERN = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_WINDOWS_DEVICE_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


class UpdatePackageError(RuntimeError):
    """Raised when an update package is unsafe, corrupt, or not applicable."""


@dataclass(frozen=True)
class PatchFile:
    path: str
    size: int
    sha256: str
    previous_sha256: str | None
    preserve_if_modified: bool

    @property
    def payload_name(self) -> str:
        return f"{PATCH_PAYLOAD_PREFIX}{self.path}"


@dataclass(frozen=True)
class RemovedFile:
    path: str
    sha256: str
    preserve_if_modified: bool


@dataclass(frozen=True)
class PatchManifest:
    from_version: str
    to_version: str
    files: tuple[PatchFile, ...]
    remove: tuple[RemovedFile, ...]


@dataclass(frozen=True)
class DeferredUpdate:
    version: str
    transaction_dir: Path

    def __str__(self) -> str:
        return self.version


def parse_version(version: str) -> tuple[int, int, int]:
    match = _VERSION_PATTERN.fullmatch(version)
    if match is None:
        raise UpdatePackageError(f"无效版本号：{version!r}")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _best_effort_logger(
    log: Callable[[str], object] | None,
) -> Callable[[str], None]:
    callback = log or (lambda _message: None)

    def write(message: str) -> None:
        try:
            callback(message)
        except BaseException:
            pass

    return write


def _validate_expected_patch_digests(
    patches: Sequence[Path],
    expected_patch_sha256: Sequence[str] | None,
) -> tuple[str, ...] | None:
    if expected_patch_sha256 is None:
        return None
    digests = tuple(expected_patch_sha256)
    if len(digests) != len(patches):
        raise UpdatePackageError("升级包数量与外层 SHA-256 数量不一致")
    if any(_SHA256_PATTERN.fullmatch(digest) is None for digest in digests):
        raise UpdatePackageError("升级包外层 SHA-256 格式无效")
    return digests


def validate_relative_path(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise UpdatePackageError("升级清单包含空文件路径")
    if "\\" in value or "\0" in value or ":" in value:
        raise UpdatePackageError(f"升级清单包含不安全路径：{value!r}")
    pure_path = PurePosixPath(value)
    if pure_path.is_absolute() or str(pure_path) != value:
        raise UpdatePackageError(f"升级清单包含非规范路径：{value!r}")
    if any(part in {"", ".", ".."} for part in pure_path.parts):
        raise UpdatePackageError(f"升级清单包含路径穿越：{value!r}")
    for part in pure_path.parts:
        if any(ord(character) < 32 for character in part):
            raise UpdatePackageError(f"升级清单包含控制字符：{value!r}")
        if part.endswith((" ", ".")):
            raise UpdatePackageError(f"升级清单包含 Windows 不支持的路径：{value!r}")
        device_name = part.split(".", 1)[0].upper()
        if device_name in _WINDOWS_DEVICE_NAMES:
            raise UpdatePackageError(f"升级清单包含 Windows 设备名：{value!r}")
    return value


def normalized_path_key(path: str) -> str:
    return unicodedata.normalize("NFC", path).casefold()


def load_patch_manifest(
    archive_path: Path,
    *,
    verify_payload: bool = True,
) -> PatchManifest:
    try:
        with zipfile.ZipFile(archive_path) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_PATCH_FILES + 1:
                raise UpdatePackageError("升级包文件数量超过安全限制")
            _validate_archive_members(infos)
            try:
                manifest_info = archive.getinfo(PATCH_MANIFEST_NAME)
            except KeyError as exc:
                raise UpdatePackageError("升级包缺少 update.json") from exc
            if manifest_info.file_size > MAX_MANIFEST_SIZE:
                raise UpdatePackageError("升级清单超过安全限制")
            try:
                raw_manifest = json.loads(archive.read(manifest_info).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise UpdatePackageError("无法读取升级清单") from exc
            manifest = _parse_patch_manifest(raw_manifest)
            _validate_payload_members(archive, manifest, verify_payload=verify_payload)
            return manifest
    except (OSError, zipfile.BadZipFile) as exc:
        raise UpdatePackageError(f"无法打开升级包 {archive_path.name}：{exc}") from exc


def apply_update_packages(
    patches: Sequence[Path],
    *,
    install_dir: Path,
    expected_version: str,
    expected_patch_sha256: Sequence[str] | None = None,
    defer_commit: bool = False,
    log: Callable[[str], object] | None = None,
) -> str | DeferredUpdate:
    if not patches:
        raise UpdatePackageError("没有可安装的升级包")
    parse_version(expected_version)
    install_dir = install_dir.resolve()
    if not install_dir.is_dir():
        raise UpdatePackageError(f"安装目录不存在：{install_dir}")
    logger = _best_effort_logger(log)
    if recover_interrupted_update_transactions(install_dir, log=logger):
        error = UpdatePackageError(
            "已恢复上次中断升级的旧版本，请从恢复后的版本重新检查更新"
        )
        error.rollback_completed = True
        raise error
    expected_digests = _validate_expected_patch_digests(patches, expected_patch_sha256)

    try:
        work_dir = Path(tempfile.mkdtemp(prefix=TRANSACTION_DIR_PREFIX, dir=install_dir))
    except OSError as exc:
        raise UpdatePackageError(f"安装目录不可写：{exc}") from exc
    backup_dir = work_dir / "backup"
    package_dir = work_dir / "packages"
    staging_dir = work_dir / "staging"
    journal_path = work_dir / TRANSACTION_JOURNAL_NAME
    originals: dict[Path, Path | None] = {}
    preserve_work_dir = False
    current_version = expected_version
    result: str | DeferredUpdate = current_version

    try:
        _write_transaction_journal(
            journal_path,
            install_dir=install_dir,
            originals=originals,
            state="applying",
        )
        verified_patches: list[Path] = []
        for index, patch_path in enumerate(patches):
            verified_patch = package_dir / f"{index}.update.zip"
            expected_digest = None if expected_digests is None else expected_digests[index]
            _copy_verified_patch(Path(patch_path), verified_patch, expected_digest)
            verified_patches.append(verified_patch)

        manifests: list[PatchManifest] = []
        for patch_path in verified_patches:
            manifest = load_patch_manifest(patch_path, verify_payload=True)
            if manifest.from_version != current_version:
                raise UpdatePackageError(
                    f"升级链不连续：当前为 v{current_version}，升级包要求 v{manifest.from_version}"
                )
            manifests.append(manifest)
            current_version = manifest.to_version

        expanded_size = sum(entry.size for manifest in manifests for entry in manifest.files)
        backup_size, rollback_temporary_size = _estimate_backup_space(manifests, install_dir)
        required_space = (
            expanded_size
            + backup_size
            + rollback_temporary_size
            + 64 * 1024 * 1024
        )
        try:
            free_space = shutil.disk_usage(install_dir).free
        except OSError as exc:
            raise UpdatePackageError(f"无法检查安装磁盘空间：{exc}") from exc
        if free_space < required_space:
            raise UpdatePackageError(
                f"磁盘空间不足：至少需要 {required_space / (1024 * 1024):.0f} MB 可用空间"
            )

        logger(f"开始安装 v{expected_version} -> v{current_version}")
        for index, (patch_path, manifest) in enumerate(
            zip(verified_patches, manifests, strict=True)
        ):
            patch_stage = staging_dir / str(index)
            _extract_verified_payload(Path(patch_path), manifest, patch_stage)
            _apply_manifest(
                manifest,
                install_dir=install_dir,
                staging_dir=patch_stage,
                backup_dir=backup_dir,
                originals=originals,
                journal_path=journal_path,
                log=logger,
            )
        logger(f"升级安装完成：v{current_version}")
        _write_transaction_journal(
            journal_path,
            install_dir=install_dir,
            originals=originals,
            state="applied",
        )
        if defer_commit:
            preserve_work_dir = True
            result = DeferredUpdate(current_version, work_dir)
        else:
            _write_transaction_journal(
                journal_path,
                install_dir=install_dir,
                originals=originals,
                state="committed",
            )
            result = current_version
    except BaseException as exc:
        logger(f"升级失败，正在回滚：{exc}")
        rollback_errors = _rollback(originals, install_dir=install_dir, log=logger)
        if rollback_errors:
            preserve_work_dir = True
            details = "；".join(rollback_errors)
            raise UpdatePackageError(
                f"升级失败且回滚不完整，备份保留在 {work_dir}：{exc}；{details}"
            ) from exc
        try:
            _write_transaction_journal(
                journal_path,
                install_dir=install_dir,
                originals=originals,
                state="rolled_back",
            )
        except UpdatePackageError as journal_error:
            preserve_work_dir = True
            error = UpdatePackageError(
                f"升级失败且无法持久化回滚结果，备份保留在 {work_dir}：{journal_error}"
            )
            error.rollback_completed = True
            raise error from journal_error
        if isinstance(exc, UpdatePackageError):
            exc.rollback_completed = True
            raise
        error = UpdatePackageError(f"升级安装失败：{exc}")
        error.rollback_completed = True
        raise error from exc
    finally:
        if not preserve_work_dir:
            _cleanup_finished_transaction(work_dir, log=logger)

    return result


def recover_interrupted_update_transactions(
    install_dir: Path,
    *,
    log: Callable[[str], object] | None = None,
) -> bool:
    install_dir = install_dir.resolve()
    logger = _best_effort_logger(log)
    recovered_version = False
    _retry_isolated_transaction_cleanup(install_dir, log=logger)
    for candidate in sorted(install_dir.glob(f"{TRANSACTION_DIR_PREFIX}*")):
        work_dir = _resolve_transaction_dir(candidate, install_dir)
        state, originals = _read_transaction_journal(work_dir, install_dir)
        if state in {"committed", "rolled_back"}:
            _cleanup_finished_transaction(work_dir, log=logger)
            continue
        logger(f"发现中断的升级事务，正在恢复旧版本：{work_dir.name}")
        rollback_errors = _rollback(originals, install_dir=install_dir, log=logger)
        if rollback_errors:
            details = "；".join(rollback_errors)
            raise UpdatePackageError(
                f"中断升级事务恢复不完整，备份保留在 {work_dir}：{details}"
            )
        try:
            _write_transaction_journal(
                work_dir / TRANSACTION_JOURNAL_NAME,
                install_dir=install_dir,
                originals=originals,
                state="rolled_back",
            )
        except UpdatePackageError as journal_error:
            journal_error.rollback_completed = True
            raise
        _cleanup_finished_transaction(work_dir, log=logger)
        recovered_version = True
    return recovered_version


def _retry_isolated_transaction_cleanup(
    install_dir: Path,
    *,
    log: Callable[[str], object],
) -> None:
    for candidate in sorted(install_dir.glob(f"{TRANSACTION_CLEANUP_DIR_PREFIX}*")):
        if candidate.is_symlink():
            continue
        try:
            shutil.rmtree(candidate)
        except FileNotFoundError:
            continue
        except OSError as exc:
            log(f"暂时无法清理已结束的升级事务目录 {candidate}：{exc}")


def commit_update_transaction(transaction_dir: Path, *, install_dir: Path) -> None:
    install_dir = install_dir.resolve()
    work_dir = _resolve_transaction_dir(transaction_dir, install_dir)
    state, originals = _read_transaction_journal(work_dir, install_dir)
    if state != "applied":
        raise UpdatePackageError(f"升级事务不能提交：当前状态为 {state}")
    _write_transaction_journal(
        work_dir / TRANSACTION_JOURNAL_NAME,
        install_dir=install_dir,
        originals=originals,
        state="committed",
    )
    try:
        _remove_transaction_dir(work_dir)
    except UpdatePackageError:
        # The durable committed marker makes cleanup retryable on the next run.
        pass


def rollback_update_transaction(
    transaction_dir: Path,
    *,
    install_dir: Path,
    log: Callable[[str], object] | None = None,
) -> None:
    install_dir = install_dir.resolve()
    work_dir = _resolve_transaction_dir(transaction_dir, install_dir)
    state, originals = _read_transaction_journal(work_dir, install_dir)
    if state == "committed":
        raise UpdatePackageError("已提交的升级事务不能回滚")
    if state == "rolled_back":
        _cleanup_finished_transaction(work_dir, log=_best_effort_logger(log))
        return
    rollback_errors = _rollback(
        originals,
        install_dir=install_dir,
        log=_best_effort_logger(log),
    )
    if rollback_errors:
        details = "；".join(rollback_errors)
        raise UpdatePackageError(f"升级事务回滚不完整，备份保留在 {work_dir}：{details}")
    _write_transaction_journal(
        work_dir / TRANSACTION_JOURNAL_NAME,
        install_dir=install_dir,
        originals=originals,
        state="rolled_back",
    )
    _cleanup_finished_transaction(work_dir, log=_best_effort_logger(log))


def _resolve_transaction_dir(transaction_dir: Path, install_dir: Path) -> Path:
    candidate = Path(transaction_dir)
    if candidate.is_symlink():
        raise UpdatePackageError(f"升级事务目录不能是符号链接：{candidate}")
    resolved = candidate.resolve()
    if (
        resolved.parent != install_dir
        or not resolved.name.startswith(TRANSACTION_DIR_PREFIX)
        or not resolved.is_dir()
    ):
        raise UpdatePackageError(f"升级事务目录无效：{candidate}")
    return resolved


def _read_transaction_journal(
    work_dir: Path,
    install_dir: Path,
) -> tuple[str, dict[Path, Path | None]]:
    journal_path = work_dir / TRANSACTION_JOURNAL_NAME
    try:
        raw = json.loads(journal_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdatePackageError(f"无法读取中断升级事务：{journal_path}") from exc
    if not isinstance(raw, dict):
        raise UpdatePackageError(f"升级事务日志格式无效：{journal_path}")
    if (
        raw.get("schema_version") != TRANSACTION_SCHEMA_VERSION
        or raw.get("application") != APPLICATION_ID
    ):
        raise UpdatePackageError(f"升级事务日志版本无效：{journal_path}")
    state = raw.get("state")
    if state not in {"applying", "applied", "committed", "rolled_back"}:
        raise UpdatePackageError(f"升级事务状态无效：{journal_path}")
    raw_originals = raw.get("originals")
    if not isinstance(raw_originals, list) or len(raw_originals) > MAX_PATCH_FILES:
        raise UpdatePackageError(f"升级事务文件列表无效：{journal_path}")

    originals: dict[Path, Path | None] = {}
    seen: set[str] = set()
    backup_dir = work_dir / "backup"
    for raw_entry in raw_originals:
        if not isinstance(raw_entry, dict):
            raise UpdatePackageError(f"升级事务文件条目无效：{journal_path}")
        relative = validate_relative_path(raw_entry.get("path"))
        key = normalized_path_key(relative)
        if key in seen or not isinstance(raw_entry.get("has_backup"), bool):
            raise UpdatePackageError(f"升级事务包含重复或无效路径：{relative}")
        seen.add(key)
        destination = _destination_path(install_dir, relative)
        backup = (
            backup_dir.joinpath(*PurePosixPath(relative).parts)
            if raw_entry["has_backup"]
            else None
        )
        originals[destination] = backup
    return state, originals


def _write_transaction_journal(
    journal_path: Path,
    *,
    install_dir: Path,
    originals: dict[Path, Path | None],
    state: str,
) -> None:
    if state not in {"applying", "applied", "committed", "rolled_back"}:
        raise ValueError(f"invalid transaction state: {state}")
    payload = {
        "schema_version": TRANSACTION_SCHEMA_VERSION,
        "application": APPLICATION_ID,
        "state": state,
        "originals": [
            {
                "path": destination.relative_to(install_dir).as_posix(),
                "has_backup": backup is not None,
            }
            for destination, backup in originals.items()
        ],
    }
    temporary = journal_path.with_name(f".{journal_path.name}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, journal_path)
    except OSError as exc:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise UpdatePackageError(f"无法持久化升级事务日志：{exc}") from exc


def _remove_transaction_dir(work_dir: Path) -> None:
    suffix = work_dir.name.removeprefix(TRANSACTION_DIR_PREFIX)
    cleanup_dir = work_dir.with_name(f"{TRANSACTION_CLEANUP_DIR_PREFIX}{suffix}")
    try:
        os.replace(work_dir, cleanup_dir)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise UpdatePackageError(f"无法隔离待清理的升级事务目录 {work_dir}：{exc}") from exc
    try:
        shutil.rmtree(cleanup_dir)
    except OSError as exc:
        raise UpdatePackageError(f"无法清理升级事务目录 {cleanup_dir}：{exc}") from exc


def _cleanup_finished_transaction(
    work_dir: Path,
    *,
    log: Callable[[str], object],
) -> None:
    try:
        _remove_transaction_dir(work_dir)
    except UpdatePackageError as exc:
        log(f"升级事务已结束，但暂时无法清理备份目录：{exc}")


def _estimate_backup_space(
    manifests: Sequence[PatchManifest],
    install_dir: Path,
) -> tuple[int, int]:
    candidates: set[Path] = set()
    sidecar_copy_size = 0
    for manifest in manifests:
        for entry in manifest.files:
            destination = _destination_path(install_dir, entry.path)
            candidates.add(destination)
            if entry.preserve_if_modified:
                sidecar_copy_size = max(sidecar_copy_size, entry.size)
        for entry in manifest.remove:
            candidates.add(_destination_path(install_dir, entry.path))

    sizes: list[int] = []
    for candidate in candidates:
        if candidate.is_symlink():
            raise UpdatePackageError(f"升级目标不能是符号链接：{candidate}")
        if not candidate.is_file():
            continue
        try:
            sizes.append(candidate.stat().st_size)
        except OSError as exc:
            raise UpdatePackageError(f"无法检查待备份文件大小：{candidate}：{exc}") from exc
    return sum(sizes), max([sidecar_copy_size, *sizes])


def _parse_patch_manifest(raw: Any) -> PatchManifest:
    if not isinstance(raw, dict):
        raise UpdatePackageError("升级清单根节点必须是对象")
    if raw.get("schema_version") != PATCH_SCHEMA_VERSION:
        raise UpdatePackageError("不支持的升级清单版本")
    if raw.get("application") != APPLICATION_ID:
        raise UpdatePackageError("升级包不属于本应用")
    if raw.get("platform") != UPDATE_PLATFORM:
        raise UpdatePackageError("升级包平台不匹配")

    from_version = _required_string(raw, "from_version")
    to_version = _required_string(raw, "to_version")
    if parse_version(to_version) <= parse_version(from_version):
        raise UpdatePackageError("升级包目标版本必须高于来源版本")

    raw_files = raw.get("files")
    raw_remove = raw.get("remove")
    if not isinstance(raw_files, list) or not isinstance(raw_remove, list):
        raise UpdatePackageError("升级清单 files/remove 字段无效")
    if len(raw_files) + len(raw_remove) > MAX_PATCH_FILES:
        raise UpdatePackageError("升级清单文件数量超过安全限制")

    files: list[PatchFile] = []
    removed: list[RemovedFile] = []
    seen_paths: set[str] = set()
    seen_directories: set[str] = set()
    seen_components: dict[str, str] = {}
    expanded_size = 0
    for raw_file in raw_files:
        if not isinstance(raw_file, dict):
            raise UpdatePackageError("升级清单 files 条目无效")
        path = validate_relative_path(raw_file.get("path"))
        _claim_path(path, seen_paths, seen_directories, seen_components)
        size = raw_file.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise UpdatePackageError(f"升级文件大小无效：{path}")
        expanded_size += size
        sha256 = _required_sha256(raw_file, "sha256", path)
        previous_value = raw_file.get("previous_sha256")
        if previous_value is not None and (
            not isinstance(previous_value, str) or _SHA256_PATTERN.fullmatch(previous_value) is None
        ):
            raise UpdatePackageError(f"升级文件 previous_sha256 无效：{path}")
        preserve = raw_file.get("preserve_if_modified", False)
        if not isinstance(preserve, bool):
            raise UpdatePackageError(f"升级文件 preserve_if_modified 无效：{path}")
        files.append(PatchFile(path, size, sha256, previous_value, preserve))

    for raw_file in raw_remove:
        if not isinstance(raw_file, dict):
            raise UpdatePackageError("升级清单 remove 条目无效")
        path = validate_relative_path(raw_file.get("path"))
        _claim_path(path, seen_paths, seen_directories, seen_components)
        sha256 = _required_sha256(raw_file, "sha256", path)
        preserve = raw_file.get("preserve_if_modified", False)
        if not isinstance(preserve, bool):
            raise UpdatePackageError(f"删除文件 preserve_if_modified 无效：{path}")
        removed.append(RemovedFile(path, sha256, preserve))

    if expanded_size > MAX_PATCH_EXPANDED_SIZE:
        raise UpdatePackageError("升级包展开大小超过安全限制")
    return PatchManifest(from_version, to_version, tuple(files), tuple(removed))


def _validate_archive_members(infos: Sequence[zipfile.ZipInfo]) -> None:
    seen: set[str] = set()
    for info in infos:
        if info.is_dir():
            raise UpdatePackageError(f"升级包包含多余目录项：{info.filename}")
        name = info.filename
        key = normalized_path_key(name)
        if key in seen:
            raise UpdatePackageError(f"升级包包含重复路径：{name}")
        seen.add(key)
        if info.flag_bits & 0x1:
            raise UpdatePackageError(f"升级包包含加密文件：{name}")
        file_mode = (info.external_attr >> 16) & 0xFFFF
        if stat.S_IFMT(file_mode) == stat.S_IFLNK:
            raise UpdatePackageError(f"升级包包含符号链接：{name}")


def _validate_payload_members(
    archive: zipfile.ZipFile,
    manifest: PatchManifest,
    *,
    verify_payload: bool,
) -> None:
    expected_names = {PATCH_MANIFEST_NAME, *(entry.payload_name for entry in manifest.files)}
    actual_names = {info.filename for info in archive.infolist()}
    extra = actual_names - expected_names
    missing = expected_names - actual_names
    if extra:
        raise UpdatePackageError(f"升级包包含清单外文件：{sorted(extra)[0]}")
    if missing:
        raise UpdatePackageError(f"升级包缺少文件：{sorted(missing)[0]}")

    for entry in manifest.files:
        info = archive.getinfo(entry.payload_name)
        if info.file_size != entry.size:
            raise UpdatePackageError(f"升级文件大小校验失败：{entry.path}")
        if verify_payload:
            digest = hashlib.sha256()
            with archive.open(info) as source:
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
            if digest.hexdigest() != entry.sha256:
                raise UpdatePackageError(f"升级文件 SHA-256 校验失败：{entry.path}")


def _extract_verified_payload(archive_path: Path, manifest: PatchManifest, target: Path) -> None:
    with zipfile.ZipFile(archive_path) as archive:
        for entry in manifest.files:
            output = target.joinpath(*PurePosixPath(entry.path).parts)
            output.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            written = 0
            with archive.open(entry.payload_name) as source, output.open("wb") as destination:
                while chunk := source.read(1024 * 1024):
                    destination.write(chunk)
                    digest.update(chunk)
                    written += len(chunk)
                destination.flush()
                os.fsync(destination.fileno())
            if written != entry.size or digest.hexdigest() != entry.sha256:
                raise UpdatePackageError(f"升级文件解压校验失败：{entry.path}")


def _copy_verified_patch(source: Path, destination: Path, expected_sha256: str | None) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    copied = 0
    try:
        with source.open("rb") as input_file, destination.open("xb") as output_file:
            while chunk := input_file.read(1024 * 1024):
                copied += len(chunk)
                if copied > MAX_PATCH_ARCHIVE_SIZE:
                    raise UpdatePackageError("升级包大小超过安全限制")
                output_file.write(chunk)
                digest.update(chunk)
            output_file.flush()
            os.fsync(output_file.fileno())
    except UpdatePackageError:
        raise
    except OSError as exc:
        raise UpdatePackageError(f"无法读取升级包 {source.name}：{exc}") from exc
    if expected_sha256 is not None and digest.hexdigest() != expected_sha256:
        raise UpdatePackageError(f"升级包外层 SHA-256 校验失败：{source.name}")


def _apply_manifest(
    manifest: PatchManifest,
    *,
    install_dir: Path,
    staging_dir: Path,
    backup_dir: Path,
    originals: dict[Path, Path | None],
    journal_path: Path,
    log: Callable[[str], object],
) -> None:
    for entry in manifest.files:
        destination = _destination_path(install_dir, entry.path)
        staged = staging_dir.joinpath(*PurePosixPath(entry.path).parts)
        current_hash = _existing_file_hash(destination)

        if current_hash == entry.sha256:
            continue
        expected_hash = entry.previous_sha256
        local_matches_previous = expected_hash is None and current_hash is None
        if expected_hash is not None:
            local_matches_previous = current_hash == expected_hash

        if not local_matches_previous:
            if not entry.preserve_if_modified:
                raise UpdatePackageError(f"本地程序文件已被修改，无法安全升级：{entry.path}")
            sidecar, already_current = _write_preserved_sidecar(
                staged,
                destination,
                manifest.to_version,
                entry.sha256,
                install_dir=install_dir,
                originals=originals,
                journal_path=journal_path,
            )
            if already_current:
                log(f"保留用户文件并复用新版副本：{entry.path} -> {sidecar.name}")
            else:
                log(f"保留用户文件并写入新版副本：{entry.path} -> {sidecar.name}")
            continue

        _replace_destination(
            staged,
            destination,
            install_dir,
            backup_dir,
            originals,
            journal_path,
        )

    for entry in manifest.remove:
        destination = _destination_path(install_dir, entry.path)
        current_hash = _existing_file_hash(destination)
        if current_hash is None:
            continue
        if current_hash != entry.sha256:
            if entry.preserve_if_modified:
                log(f"保留已修改的用户文件：{entry.path}")
                continue
            raise UpdatePackageError(f"待删除程序文件已被修改，无法安全升级：{entry.path}")
        _backup_original(
            destination,
            install_dir,
            backup_dir,
            originals,
            journal_path,
        )
        _unlink_with_retry(destination)
        _remove_empty_parent_directories(destination.parent, install_dir)


def _replace_destination(
    source: Path,
    destination: Path,
    install_dir: Path,
    backup_dir: Path,
    originals: dict[Path, Path | None],
    journal_path: Path,
) -> None:
    _ensure_regular_or_missing(destination)
    _backup_original(
        destination,
        install_dir,
        backup_dir,
        originals,
        journal_path,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    _replace_with_retry(source, destination)


def _backup_original(
    destination: Path,
    install_dir: Path,
    backup_dir: Path,
    originals: dict[Path, Path | None],
    journal_path: Path,
) -> None:
    if destination in originals:
        return
    _ensure_regular_or_missing(destination)
    if not destination.exists():
        originals[destination] = None
        _write_transaction_journal(
            journal_path,
            install_dir=install_dir,
            originals=originals,
            state="applying",
        )
        return
    relative = destination.relative_to(install_dir)
    backup = backup_dir / relative
    backup.parent.mkdir(parents=True, exist_ok=True)
    _copy_or_link_backup(destination, backup)
    originals[destination] = backup
    _write_transaction_journal(
        journal_path,
        install_dir=install_dir,
        originals=originals,
        state="applying",
    )


def _copy_or_link_backup(source: Path, destination: Path) -> None:
    try:
        os.link(source, destination)
        return
    except OSError:
        pass
    shutil.copy2(source, destination)
    with destination.open("r+b") as backup_file:
        os.fsync(backup_file.fileno())


def _rollback(
    originals: dict[Path, Path | None],
    *,
    install_dir: Path,
    log: Callable[[str], object],
) -> list[str]:
    errors: list[str] = []
    for destination, backup in reversed(tuple(originals.items())):
        try:
            if backup is None:
                if destination.exists():
                    _ensure_regular_or_missing(destination)
                    _unlink_with_retry(destination)
                _remove_empty_parent_directories(destination.parent, install_dir)
                continue
            if not backup.exists():
                raise FileNotFoundError(f"回滚备份不存在：{backup}")
            _ensure_regular_or_missing(backup)
            _ensure_regular_or_missing(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            _restore_backup(backup, destination)
        except Exception as exc:
            errors.append(f"{destination}: {exc}")
    if not errors:
        log("旧版本文件已恢复")
    return errors


def _restore_backup(backup: Path, destination: Path) -> None:
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=".update-rollback-",
        dir=destination.parent,
    )
    os.close(file_descriptor)
    temporary = Path(temporary_name)
    try:
        shutil.copy2(backup, temporary)
        with temporary.open("r+b") as restored_file:
            os.fsync(restored_file.fileno())
        _replace_with_retry(temporary, destination)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def _destination_path(install_dir: Path, relative_path: str) -> Path:
    destination = install_dir.joinpath(*PurePosixPath(relative_path).parts)
    try:
        destination.resolve(strict=False).relative_to(install_dir)
    except ValueError as exc:
        raise UpdatePackageError(f"升级目标越过安装目录：{relative_path}") from exc
    return destination


def _existing_file_hash(path: Path) -> str | None:
    _ensure_regular_or_missing(path)
    return sha256_file(path) if path.exists() else None


def _write_preserved_sidecar(
    source: Path,
    destination: Path,
    version: str,
    expected_sha256: str,
    *,
    install_dir: Path,
    originals: dict[Path, Path | None],
    journal_path: Path,
) -> tuple[Path, bool]:
    base_name = f"{destination.name}.new-v{version}"
    for index in range(MAX_PRESERVED_SIDECAR_CANDIDATES):
        name = base_name if index == 0 else f"{base_name}.{index}"
        candidate = destination.with_name(name)
        try:
            status = candidate.lstat()
        except FileNotFoundError:
            if _create_preserved_sidecar(
                source,
                candidate,
                install_dir=install_dir,
                originals=originals,
                journal_path=journal_path,
            ):
                return candidate, False
            continue
        except OSError:
            continue
        if not stat.S_ISREG(status.st_mode):
            continue
        try:
            if sha256_file(candidate) == expected_sha256:
                return candidate, True
        except OSError:
            continue
    raise UpdatePackageError(
        f"无法为已修改的用户文件选择安全的新版副本名称：{destination}"
    )


def _create_preserved_sidecar(
    source: Path,
    candidate: Path,
    *,
    install_dir: Path,
    originals: dict[Path, Path | None],
    journal_path: Path,
) -> bool:
    candidate.parent.mkdir(parents=True, exist_ok=True)
    try:
        output = candidate.open("xb")
    except FileExistsError:
        return False
    originals[candidate] = None
    try:
        _write_transaction_journal(
            journal_path,
            install_dir=install_dir,
            originals=originals,
            state="applying",
        )
        with output, source.open("rb") as input_file:
            shutil.copyfileobj(input_file, output, length=1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
        _unlink_with_retry(source)
    except BaseException:
        if not output.closed:
            output.close()
        raise
    return True


def _ensure_regular_or_missing(path: Path) -> None:
    if path.is_symlink():
        raise UpdatePackageError(f"升级目标不能是符号链接：{path}")
    if path.exists() and not path.is_file():
        raise UpdatePackageError(f"升级目标不是普通文件：{path}")


def _replace_with_retry(source: Path, destination: Path, *, attempts: int = 20) -> None:
    for attempt in range(attempts):
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if attempt + 1 >= attempts:
                raise
            time.sleep(0.25)


def _unlink_with_retry(path: Path, *, attempts: int = 20) -> None:
    for attempt in range(attempts):
        try:
            path.unlink()
            return
        except PermissionError:
            if attempt + 1 >= attempts:
                raise
            time.sleep(0.25)


def _remove_empty_parent_directories(directory: Path, install_dir: Path) -> None:
    current = directory
    while current != install_dir:
        try:
            current.relative_to(install_dir)
        except ValueError:
            return
        if current.is_symlink():
            return
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def _required_string(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise UpdatePackageError(f"升级清单缺少 {key}")
    return value


def _required_sha256(raw: dict[str, Any], key: str, path: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise UpdatePackageError(f"升级文件 {key} 无效：{path}")
    return value


def _claim_path(
    path: str,
    seen_files: set[str],
    seen_directories: set[str],
    seen_components: dict[str, str],
) -> None:
    parts = PurePosixPath(path).parts
    for index in range(1, len(parts) + 1):
        component_path = "/".join(parts[:index])
        key = normalized_path_key(component_path)
        previous = seen_components.get(key)
        if previous is not None and previous != component_path:
            raise UpdatePackageError(f"升级清单包含重复或大小写冲突路径：{path}")
        seen_components[key] = component_path
        if index < len(parts):
            if key in seen_files:
                raise UpdatePackageError(f"升级清单路径同时是文件和目录：{component_path}")
            seen_directories.add(key)
            continue
        if key in seen_files:
            raise UpdatePackageError(f"升级清单包含重复或大小写冲突路径：{path}")
        if key in seen_directories:
            raise UpdatePackageError(f"升级清单路径同时是文件和目录：{path}")
        seen_files.add(key)
