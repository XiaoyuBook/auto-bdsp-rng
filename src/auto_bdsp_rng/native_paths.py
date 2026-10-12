"""ASCII paths for native Windows libraries that open narrow-character filenames."""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
import threading
from collections.abc import Sequence
from pathlib import Path


class NativePathError(RuntimeError):
    """A native resource could not be prepared in a writable ASCII directory."""


_resource_lock = threading.Lock()
_IGNORED_DIRECTORIES = {".cache", "__pycache__"}


def needs_ascii_path(path: Path) -> bool:
    return sys.platform == "win32" and not str(path.absolute()).isascii()


def _cache_candidates() -> list[Path]:
    override = os.environ.get("AUTO_BDSP_RNG_NATIVE_CACHE_HOME", "").strip()
    if override:
        path = Path(override)
        if not path.is_absolute():
            raise NativePathError("AUTO_BDSP_RNG_NATIVE_CACHE_HOME 必须是可写的纯英文绝对路径")
        return [path]
    user_key = hashlib.sha256(str(Path.home()).casefold().encode("utf-8")).hexdigest()[:16]
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    program_data = Path(os.environ.get("PROGRAMDATA", "C:/ProgramData"))
    drive = Path(os.environ.get("SystemDrive", "C:") + "/")
    return [
        local / "auto_bdsp_rng" / "native_cache",
        Path(tempfile.gettempdir()) / "auto_bdsp_rng" / "native_cache",
        program_data / "auto_bdsp_rng" / "native_cache" / user_key,
        drive / "auto_bdsp_rng_cache" / user_key,
    ]


def native_cache_dir(namespace: str, *, reserve: int = 80) -> Path:
    if not namespace or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in namespace):
        raise ValueError("Invalid native cache namespace")
    errors: list[str] = []
    for candidate in _cache_candidates():
        directory = candidate.absolute() / namespace
        if not str(directory).isascii():
            continue
        # Native CRT file access can still enforce MAX_PATH even when Python's
        # executable declares longPathAware. Reserve room for resource filenames.
        if len(str(directory)) + reserve >= 248:
            errors.append(f"{directory}: 缓存路径过长")
            continue
        try:
            directory.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryFile(dir=directory):
                pass
        except OSError as exc:
            errors.append(f"{directory}: {exc}")
            continue
        return directory
    detail = "; ".join(errors)
    raise NativePathError(
        "无法准备 OCR 英文缓存目录；请将 AUTO_BDSP_RNG_NATIVE_CACHE_HOME 设置为可写的纯英文绝对路径"
        + (f": {detail}" if detail else "（完整路径不能包含中文用户名）")
    )


def _file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _resource_files(source: Path, subdirectories: Sequence[str] | None) -> dict[Path, str]:
    roots = [source / name for name in subdirectories] if subdirectories is not None else [source]
    files: dict[Path, str] = {}
    for root in roots:
        if not root.is_dir():
            raise NativePathError(f"OCR 资源目录不存在: {root}")
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(source)
            if not _IGNORED_DIRECTORIES.intersection(relative.parts) and path.is_file():
                files[relative] = _file_digest(path)
    if not files:
        raise NativePathError(f"OCR 资源目录为空: {source}")
    return files


def _matches(directory: Path, files: dict[Path, str]) -> bool:
    try:
        return all(_file_digest(directory / relative) == digest for relative, digest in files.items())
    except OSError:
        return False


def ascii_resource_directory(
    source: Path,
    namespace: str,
    *,
    subdirectories: Sequence[str] | None = None,
) -> Path:
    """Reuse a verified content-addressed copy; keep native paths free of user names."""
    source = source.absolute()
    if not needs_ascii_path(source):
        return source
    with _resource_lock:
        files = _resource_files(source, subdirectories)
        identity = hashlib.sha256()
        for relative, digest in files.items():
            identity.update(relative.as_posix().encode("utf-8") + b"\0" + digest.encode("ascii") + b"\0")
        reserve = len(identity.hexdigest()) + 2 + max(len(str(relative)) for relative in files)
        root = native_cache_dir(namespace, reserve=max(80, reserve))
        destination = root / identity.hexdigest()
        if _matches(destination, files):
            return destination
        with tempfile.TemporaryDirectory(dir=root, prefix=".prepare-") as temporary:
            staged = Path(temporary) / "resources"
            staged.mkdir()
            for relative in files:
                output = staged / relative
                output.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source / relative, output)
            if not _matches(staged, files):
                raise NativePathError(f"复制时 OCR 资源发生变化，请重试: {source}")
            try:
                staged.rename(destination)
            except FileExistsError:
                # Another process may have published the same resources. Repair
                # an interrupted/corrupt copy using complete files, never partial writes.
                if not _matches(destination, files):
                    for relative in files:
                        output = destination / relative
                        output.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(staged / relative, output)
            if not _matches(destination, files):
                raise NativePathError(f"OCR 缓存校验失败: {destination}")
        return destination
