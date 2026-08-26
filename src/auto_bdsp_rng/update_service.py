from __future__ import annotations

import hashlib
import heapq
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from auto_bdsp_rng.resources import app_base_dir, app_path, writable_app_data_dir
from auto_bdsp_rng.update_core import UpdatePackageError, load_patch_manifest, parse_version, sha256_file


REPOSITORY = "XiaoyuBook/auto-bdsp-rng"
RELEASES_API_URL = f"https://api.github.com/repos/{REPOSITORY}/releases?per_page=100"
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases"
UPDATER_EXE_NAME = "auto-bdsp-rng-updater.exe"
MAX_UPDATE_DOWNLOAD_SIZE = 2 * 1024 * 1024 * 1024
_ASSET_PATTERN = re.compile(
    r"^auto-bdsp-rng-v(?P<from>\d+\.\d+\.\d+)-to-v(?P<to>\d+\.\d+\.\d+)"
    r"-windows-x64\.update\.zip$"
)
_ASSET_DIGEST_PATTERN = re.compile(r"^sha256:([0-9a-f]{64})$")
_RELEASE_NOTES_HEADING_PATTERN = re.compile(r"^##[ \t]+本次更新(?:[ \t]+##)?[ \t]*$")
_RELEASE_NOTES_END_PATTERN = re.compile(r"^#{1,2}[ \t]+")


class UpdateServiceError(RuntimeError):
    """Raised when update discovery, download, or launch fails."""


class UpdateDownloadCancelled(UpdateServiceError):
    pass


@dataclass(frozen=True)
class UpdateAsset:
    name: str
    url: str
    size: int
    digest: str
    from_version: str
    to_version: str


@dataclass(frozen=True)
class UpdatePlan:
    current_version: str
    latest_version: str
    assets: tuple[UpdateAsset, ...]
    release_url: str
    release_notes: str

    @property
    def update_available(self) -> bool:
        return parse_version(self.latest_version) > parse_version(self.current_version)

    @property
    def incremental_available(self) -> bool:
        return bool(
            self.update_available
            and self.assets
            and self.assets[0].from_version == self.current_version
            and self.assets[-1].to_version == self.latest_version
        )

    @property
    def download_size(self) -> int:
        return sum(asset.size for asset in self.assets)


@dataclass
class LaunchedUpdateInstaller:
    process: subprocess.Popen[bytes]
    approval_file: Path
    approval_token: str

    def approve(self) -> None:
        poll = getattr(self.process, "poll", None)
        if callable(poll) and poll() is not None:
            raise UpdateServiceError("独立升级器在获得安装授权前已退出")
        try:
            _write_approval_state(self.approval_file, self.approval_token, "approved")
        except OSError as exc:
            raise UpdateServiceError(f"无法授权独立升级器开始安装：{exc}") from exc

    def cancel(self) -> None:
        try:
            _write_approval_state(self.approval_file, self.approval_token, "cancelled")
        except OSError as exc:
            raise UpdateServiceError(f"无法记录升级取消状态：{exc}") from exc

    def poll(self) -> int | None:
        return self.process.poll()

    def terminate(self) -> None:
        self.process.terminate()

    def kill(self) -> None:
        self.process.kill()

    def wait(self, *, timeout: float) -> int:
        return self.process.wait(timeout=timeout)


def check_for_updates(
    current_version: str,
    *,
    api_url: str = RELEASES_API_URL,
    opener: Callable[..., BinaryIO] = urlopen,
    timeout: float = 10.0,
) -> UpdatePlan:
    parse_version(current_version)
    request = Request(
        api_url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"auto-bdsp-rng/{current_version}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with opener(request, timeout=timeout) as response:
            payload = response.read(8 * 1024 * 1024 + 1)
    except HTTPError as exc:
        if exc.code == 403:
            raise UpdateServiceError("GitHub 暂时限制了更新检查，请稍后重试") from exc
        raise UpdateServiceError(f"检查更新失败：GitHub 返回 HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise UpdateServiceError(f"检查更新失败：{_network_error_text(exc)}") from exc
    if len(payload) > 8 * 1024 * 1024:
        raise UpdateServiceError("GitHub 更新信息超过安全限制")
    try:
        releases = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateServiceError("GitHub 返回了无法解析的更新信息") from exc
    return build_update_plan(current_version, releases)


def build_update_plan(current_version: str, releases: object) -> UpdatePlan:
    current_key = parse_version(current_version)
    if not isinstance(releases, list):
        raise UpdateServiceError("GitHub 更新信息格式无效")

    valid_releases: dict[str, dict[str, Any]] = {}
    for raw_release in releases:
        if not isinstance(raw_release, dict):
            continue
        if raw_release.get("draft") is True or raw_release.get("prerelease") is True:
            continue
        tag = raw_release.get("tag_name")
        if not isinstance(tag, str) or not tag.startswith("v"):
            continue
        version = tag[1:]
        try:
            parse_version(version)
        except UpdatePackageError:
            continue
        valid_releases[version] = raw_release

    if not valid_releases:
        raise UpdateServiceError("没有找到有效的正式 Release")
    latest_version = max(valid_releases, key=parse_version)
    latest_release = valid_releases[latest_version]
    release_url = _release_url(latest_release, latest_version)

    if parse_version(latest_version) <= current_key:
        return UpdatePlan(current_version, latest_version, (), release_url, "")

    release_notes = _collect_release_notes(current_key, valid_releases)
    assets = tuple(_iter_update_assets(valid_releases))
    chain = _shortest_asset_chain(current_version, latest_version, assets)
    if sum(asset.size for asset in chain) > MAX_UPDATE_DOWNLOAD_SIZE:
        chain = ()
    return UpdatePlan(current_version, latest_version, chain, release_url, release_notes)


def _collect_release_notes(
    current_key: tuple[int, int, int],
    releases: dict[str, dict[str, Any]],
) -> str:
    sections: list[str] = []
    newer_versions = sorted(
        (version for version in releases if parse_version(version) > current_key),
        key=parse_version,
        reverse=True,
    )
    for version in newer_versions:
        body = releases[version].get("body")
        if not isinstance(body, str):
            continue
        notes = _extract_release_notes_section(body)
        if notes:
            sections.append(f"### v{version}\n\n{notes}")
    return "\n\n".join(sections)


def _extract_release_notes_section(body: str) -> str:
    lines = body.splitlines()
    heading_index: int | None = None
    for index, line in enumerate(lines):
        if _RELEASE_NOTES_HEADING_PATTERN.fullmatch(line):
            heading_index = index
            break
    if heading_index is None:
        return ""

    end_index = len(lines)
    for index in range(heading_index + 1, len(lines)):
        if _RELEASE_NOTES_END_PATTERN.match(lines[index]):
            end_index = index
            break
    return "\n".join(lines[heading_index + 1 : end_index]).strip()


def download_update_assets(
    plan: UpdatePlan,
    progress_callback: Callable[[int, int], object] | None = None,
    cancel_event: threading.Event | None = None,
    *,
    download_dir: Path | None = None,
    opener: Callable[..., BinaryIO] = urlopen,
    timeout: float = 10.0,
) -> tuple[Path, ...]:
    if not plan.incremental_available:
        raise UpdateServiceError("当前版本没有通往最新版的增量升级包")
    total_size = plan.download_size
    if total_size <= 0 or total_size > MAX_UPDATE_DOWNLOAD_SIZE:
        raise UpdateServiceError("增量升级链总大小超过安全限制")
    cancel_event = cancel_event or threading.Event()
    progress_callback = progress_callback or (lambda _downloaded, _total: None)
    download_dir = download_dir or writable_app_data_dir("updates", "downloads", f"v{plan.latest_version}")
    download_dir.mkdir(parents=True, exist_ok=True)
    completed_size = 0
    outputs: list[Path] = []

    for asset in plan.assets:
        _raise_if_cancelled(cancel_event)
        destination = download_dir / asset.name
        if _existing_download_is_valid(destination, asset):
            completed_size += asset.size
            progress_callback(completed_size, total_size)
            outputs.append(destination)
            continue

        temporary = destination.with_name(f".{destination.name}.{os.getpid()}.part")
        try:
            if temporary.exists():
                temporary.unlink()
            request = Request(
                asset.url,
                headers={
                    "Accept": "application/octet-stream",
                    "User-Agent": f"auto-bdsp-rng/{plan.current_version}",
                },
            )
            digest = hashlib.sha256()
            asset_downloaded = 0
            with opener(request, timeout=timeout) as response, temporary.open("wb") as output:
                header_size = response.headers.get("Content-Length")
                if header_size is not None:
                    try:
                        parsed_header_size = int(header_size)
                    except ValueError as exc:
                        raise UpdateServiceError("升级包返回了无效的文件大小") from exc
                    if parsed_header_size != asset.size:
                        raise UpdateServiceError("升级包下载大小与 Release 信息不一致")
                while chunk := response.read(1024 * 1024):
                    _raise_if_cancelled(cancel_event)
                    asset_downloaded += len(chunk)
                    if asset_downloaded > asset.size or completed_size + asset_downloaded > MAX_UPDATE_DOWNLOAD_SIZE:
                        raise UpdateServiceError("升级包下载大小超过安全限制")
                    output.write(chunk)
                    digest.update(chunk)
                    progress_callback(completed_size + asset_downloaded, total_size)
                output.flush()
                os.fsync(output.fileno())
            if asset_downloaded != asset.size:
                raise UpdateServiceError("升级包下载不完整")
            expected_digest = _asset_digest_hex(asset.digest)
            if digest.hexdigest() != expected_digest:
                raise UpdateServiceError("升级包 SHA-256 校验失败")
            _validate_downloaded_patch(temporary, asset)
            os.replace(temporary, destination)
        except UpdateDownloadCancelled:
            _unlink_quietly(temporary)
            raise
        except UpdateServiceError:
            _unlink_quietly(temporary)
            raise
        except HTTPError as exc:
            _unlink_quietly(temporary)
            raise UpdateServiceError(f"下载升级包失败：HTTP {exc.code}") from exc
        except (URLError, TimeoutError, OSError, UpdatePackageError) as exc:
            _unlink_quietly(temporary)
            raise UpdateServiceError(f"下载升级包失败：{_network_error_text(exc)}") from exc
        except Exception:
            _unlink_quietly(temporary)
            raise
        completed_size += asset.size
        progress_callback(completed_size, total_size)
        outputs.append(destination)

    return tuple(outputs)


def has_bundled_updater() -> bool:
    return bool(getattr(sys, "frozen", False) and app_path(UPDATER_EXE_NAME).is_file())


def launch_update_installer(
    patches: tuple[Path, ...],
    current_version: str,
    target_version: str,
    asset_digests: tuple[str, ...] = (),
) -> LaunchedUpdateInstaller:
    if not getattr(sys, "frozen", False):
        raise UpdateServiceError("源码运行模式不能自动替换程序，请到 Release 页面下载正式版")
    updater_source = app_path(UPDATER_EXE_NAME)
    if not updater_source.is_file():
        raise UpdateServiceError("当前安装包缺少独立升级器，请下载一次完整 Release")
    if not patches:
        raise UpdateServiceError("没有可安装的升级包")
    if len(asset_digests) != len(patches):
        raise UpdateServiceError("升级包数量与 Release SHA-256 数量不一致")

    chained_version = current_version
    verified_digests: list[str] = []
    for patch, asset_digest in zip(patches, asset_digests, strict=True):
        expected_digest = _asset_digest_hex(asset_digest)
        try:
            actual_digest = sha256_file(Path(patch))
        except OSError as exc:
            raise UpdateServiceError(f"无法读取下载的升级包：{exc}") from exc
        if actual_digest != expected_digest:
            raise UpdateServiceError(f"升级包 SHA-256 校验失败：{Path(patch).name}")
        verified_digests.append(expected_digest)
        manifest = load_patch_manifest(Path(patch), verify_payload=True)
        if manifest.from_version != chained_version:
            raise UpdateServiceError("下载的升级包版本链不连续")
        chained_version = manifest.to_version
    if chained_version != target_version:
        raise UpdateServiceError("下载的升级包没有到达目标版本")

    install_dir = app_base_dir()
    helper_dir = install_dir / ".auto-bdsp-rng-updater"
    try:
        helper_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise UpdateServiceError(f"安装目录不可写，无法准备独立升级器：{exc}") from exc
    _cleanup_stale_helpers(helper_dir)
    launch_id = uuid.uuid4().hex
    helper_copy = helper_dir / f"auto-bdsp-rng-updater-v{target_version}-{launch_id}.exe"
    approval_file = helper_dir / f".approve-{launch_id}.token"
    approval_token = uuid.uuid4().hex
    try:
        shutil.copy2(updater_source, helper_copy)
        _write_approval_state(approval_file, approval_token, "pending")
    except OSError as exc:
        _unlink_quietly(helper_copy)
        _unlink_quietly(approval_file)
        raise UpdateServiceError(f"无法准备独立升级器：{exc}") from exc
    log_path = writable_app_data_dir("updates") / "update.log"
    command = [
        str(helper_copy),
        "--wait-pid",
        str(os.getpid()),
        "--install-dir",
        str(install_dir),
        "--current-version",
        current_version,
        "--target-version",
        target_version,
        "--approval-file",
        str(approval_file),
        "--approval-token",
        approval_token,
        "--launch",
        str(Path(sys.executable).resolve()),
        "--log",
        str(log_path),
    ]
    for patch, digest in zip(patches, verified_digests, strict=True):
        command.extend(
            (
                "--patch",
                str(Path(patch).resolve()),
                "--patch-sha256",
                digest,
            )
        )

    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    try:
        process = subprocess.Popen(
            command,
            cwd=install_dir,
            close_fds=True,
            creationflags=creationflags,
        )
    except OSError as exc:
        _unlink_quietly(helper_copy)
        _unlink_quietly(approval_file)
        raise UpdateServiceError(f"无法启动独立升级器：{exc}") from exc
    return LaunchedUpdateInstaller(process, approval_file, approval_token)


def _iter_update_assets(releases: dict[str, dict[str, Any]]) -> Iterable[UpdateAsset]:
    for release_version, release in releases.items():
        raw_assets = release.get("assets")
        if not isinstance(raw_assets, list):
            continue
        for raw_asset in raw_assets:
            if not isinstance(raw_asset, dict):
                continue
            name = raw_asset.get("name")
            if not isinstance(name, str):
                continue
            match = _ASSET_PATTERN.fullmatch(name)
            if match is None or match.group("to") != release_version:
                continue
            from_version = match.group("from")
            to_version = match.group("to")
            try:
                if parse_version(to_version) <= parse_version(from_version):
                    continue
            except UpdatePackageError:
                continue
            url = raw_asset.get("browser_download_url")
            size = raw_asset.get("size")
            digest = raw_asset.get("digest")
            if not _is_official_asset_url(url, release_version, name):
                continue
            if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= MAX_UPDATE_DOWNLOAD_SIZE:
                continue
            if not isinstance(digest, str) or _ASSET_DIGEST_PATTERN.fullmatch(digest) is None:
                continue
            yield UpdateAsset(name, url, size, digest, from_version, to_version)


def _shortest_asset_chain(
    current_version: str,
    latest_version: str,
    assets: tuple[UpdateAsset, ...],
) -> tuple[UpdateAsset, ...]:
    outgoing: dict[str, list[UpdateAsset]] = {}
    for asset in assets:
        if parse_version(asset.from_version) < parse_version(current_version):
            continue
        if parse_version(asset.to_version) > parse_version(latest_version):
            continue
        outgoing.setdefault(asset.from_version, []).append(asset)

    distances: dict[str, int] = {current_version: 0}
    previous: dict[str, tuple[str, UpdateAsset]] = {}
    queue: list[tuple[int, tuple[int, int, int], str]] = [(0, parse_version(current_version), current_version)]
    while queue:
        distance, _version_key, version = heapq.heappop(queue)
        if distance != distances.get(version):
            continue
        if version == latest_version:
            break
        for asset in outgoing.get(version, ()):
            candidate = distance + asset.size
            if candidate >= distances.get(asset.to_version, sys.maxsize):
                continue
            distances[asset.to_version] = candidate
            previous[asset.to_version] = (version, asset)
            heapq.heappush(queue, (candidate, parse_version(asset.to_version), asset.to_version))

    if latest_version not in distances:
        return ()
    chain: list[UpdateAsset] = []
    cursor = latest_version
    while cursor != current_version:
        prior, asset = previous[cursor]
        chain.append(asset)
        cursor = prior
    chain.reverse()
    return tuple(chain)


def _existing_download_is_valid(path: Path, asset: UpdateAsset) -> bool:
    try:
        if not path.is_file() or path.stat().st_size != asset.size:
            return False
        if sha256_file(path) != _asset_digest_hex(asset.digest):
            return False
        _validate_downloaded_patch(path, asset)
    except (OSError, UpdatePackageError, UpdateServiceError):
        return False
    return True


def _validate_downloaded_patch(path: Path, asset: UpdateAsset) -> None:
    manifest = load_patch_manifest(path, verify_payload=True)
    if manifest.from_version != asset.from_version or manifest.to_version != asset.to_version:
        raise UpdateServiceError("升级包内部版本与 Release 资产名称不一致")


def _asset_digest_hex(digest: str) -> str:
    match = _ASSET_DIGEST_PATTERN.fullmatch(digest)
    if match is None:
        raise UpdateServiceError("Release 未提供可验证的 SHA-256")
    return match.group(1)


def _is_official_asset_url(url: object, version: str, name: str) -> bool:
    if not isinstance(url, str):
        return False
    parsed = urlparse(url)
    expected_path = f"/{REPOSITORY}/releases/download/v{version}/{name}"
    return parsed.scheme == "https" and parsed.netloc == "github.com" and parsed.path == expected_path


def _release_url(release: dict[str, Any], version: str) -> str:
    value = release.get("html_url")
    expected = f"https://github.com/{REPOSITORY}/releases/tag/v{version}"
    return value if value == expected else expected


def _raise_if_cancelled(cancel_event: threading.Event) -> None:
    if cancel_event.is_set():
        raise UpdateDownloadCancelled("已取消下载升级包")


def _cleanup_stale_helpers(directory: Path, *, minimum_age_seconds: float = 24 * 60 * 60) -> None:
    cutoff = time.time() - minimum_age_seconds
    for pattern in ("auto-bdsp-rng-updater-*.exe", ".approve-*.token"):
        for path in directory.glob(pattern):
            try:
                if path.stat().st_mtime > cutoff:
                    continue
                path.unlink()
            except OSError:
                pass


def _write_approval_state(path: Path, token: str, state: str) -> None:
    if state not in {"pending", "approved", "cancelled"}:
        raise ValueError(f"invalid approval state: {state}")
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="ascii", newline="\n") as handle:
            handle.write(f"{state}:{token}\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        _unlink_quietly(temporary)


def _unlink_quietly(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def _network_error_text(error: BaseException) -> str:
    reason = getattr(error, "reason", None)
    return str(reason or error)
