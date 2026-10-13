"""Mirror an immutable GitHub release to Gitee; publish metadata last."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import shutil
import tempfile
import time

import requests

REPOSITORY = "shekongsk/auto-bdsp-rng"
API = f"https://gitee.com/api/v5/repos/{REPOSITORY}"
MARKER = re.compile(r"\n*## 更新校验信息\n.*", re.DOTALL)
MIN_TRANSFER_SPEED = 5 * 1024
LOW_SPEED_WINDOW = 60
ATTEMPTS = 3
# Operational mirror policy, not a claim about Gitee's account-specific quota.
MAX_MIRROR_ASSET_SIZE = 90 * 1024 * 1024
MAX_UPDATE_ASSET_SIZE = 2 * 1024 * 1024 * 1024
MIRROR_PART_SIZE = 8 * 1024 * 1024


class LowSpeedTransferError(RuntimeError):
    """Stop the sync instead of retrying a transfer below the requested minimum."""


def log(message: str) -> None:
    print(message, flush=True)


def transfer(url: str, output: Path, *, form: str | None = None, max_size: int | None = None) -> None:
    """Allow long transfers while stopping sustained speeds below the threshold."""
    curl = shutil.which("curl.exe" if os.name == "nt" else "curl")
    if curl is None:
        raise RuntimeError("curl is required for release transfers")
    command = [curl, "--silent", "--show-error", "--fail", "--connect-timeout", "30",
               "--speed-limit", str(MIN_TRANSFER_SPEED),
               "--speed-time", str(LOW_SPEED_WINDOW),
               "--output", str(output), "--write-out",
               "%{http_code} %{size_upload} %{size_download} %{time_total}"]
    if form is None:
        command += ["--location", "--proto", "=https", "--proto-redir", "=https"]
    else:
        # Credentials travel over stdin, never in command-line arguments or logs.
        # Avoid Expect: 100-continue / HTTP/2 interoperability problems on the
        # attachment endpoint. Keep public downloads on curl's default protocol.
        command += ["--http1.1", "--header", "Expect:", "--config", "-"]
    if max_size is not None:
        command += ["--max-filesize", str(max_size)]
    command.append(url)
    result = subprocess.run(command, input=form, capture_output=True, text=True)
    fields = result.stdout.split()
    status = fields[0] if fields and re.fullmatch(r"\d{3}", fields[0]) else "unknown"
    # Only emit numeric counters; never echo unexpected stdout, stderr or URLs.
    if len(fields) == 4 and all(re.fullmatch(r"\d+(?:\.\d+)?", item) for item in fields[1:]):
        log(f"Transfer: HTTP {status}, uploaded {fields[1]} bytes, "
            f"downloaded {fields[2]} bytes, elapsed {fields[3]} s")
    if result.returncode or not status.startswith("2"):
        # Do not print curl stderr or server bodies, which may echo credentials.
        if result.returncode == 28 and "Operation too slow" in result.stderr:
            raise LowSpeedTransferError(
                f"Transfer stopped: speed below {MIN_TRANSFER_SPEED} bytes/s for {LOW_SPEED_WINDOW} seconds")
        raise RuntimeError(f"Transfer failed: curl exit {result.returncode}, HTTP {status}")


def upload_file(path: Path, release_id: int, token: str) -> None:
    def quoted(value: str) -> str:
        if "\n" in value or "\r" in value:
            raise ValueError("Invalid multipart value")
        return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'
    # Authenticate before the server reads the multipart body. Keep secrets on stdin.
    form = "header = " + quoted("Authorization: Bearer " + token) + "\n"
    form += "form = " + quoted(f"file=@{path.resolve().as_posix()};type=application/octet-stream") + "\n"
    with tempfile.TemporaryDirectory() as directory:
        transfer(API + f"/releases/{release_id}/attach_files", Path(directory) / "response", form=form)


def request(method: str, endpoint: str, token: str, **kwargs):
    # Never print response bodies, request URLs or headers: they may contain credentials.
    attempts = ATTEMPTS if method in ("GET", "PATCH") else 1
    for attempt in range(1, attempts + 1):
        try:
            response = requests.request(method, API + endpoint, headers={"Authorization": f"Bearer {token}"},
                                        timeout=(30, 60), **kwargs)
            if response.ok:
                return response.json()
            detail = f"HTTP {response.status_code}"
            if response.status_code not in (408, 429, 500, 502, 503, 504):
                break
        except requests.RequestException as exc:
            detail = type(exc).__name__
        if attempt < attempts:
            log(f"Gitee {method} {endpoint}: {detail}; retry {attempt}/{attempts}")
            time.sleep(5 * attempt)
    raise RuntimeError(f"Gitee {method} {endpoint} failed: {detail}") from None


def metadata_for(tag: str, paths: list[Path]) -> dict:
    assets = []
    for path in paths:
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        assets.append({"name": path.name, "size": path.stat().st_size,
                       "digest": f"sha256:{digest}",
                       "browser_download_url": f"https://gitee.com/{REPOSITORY}/releases/download/{tag}/{path.name}"})
    return {"schema_version": 1, "tag_name": tag, "assets": assets}


def verify_public(asset: dict) -> bool:
    # Verify the actual anonymous download, not just the upload response or a login page.
    try:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "asset"
            transfer(asset["browser_download_url"], path, max_size=asset["size"])
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            valid = path.stat().st_size == asset["size"] and f"sha256:{digest}" == asset["digest"]
            if not valid:
                log(f"Download size or SHA-256 mismatch: {asset['name']}")
            return valid
    except LowSpeedTransferError:
        raise
    except RuntimeError as exc:
        log(f"Anonymous verification failed for {asset['name']}: {exc}")
        return False


def sync_asset(path: Path, asset: dict, release_id: int, token: str) -> None:
    for attempt in range(1, ATTEMPTS + 1):
        log(f"Attachment {path.name} ({asset['size']} bytes), attempt {attempt}/{ATTEMPTS}")
        try:
            # A previous timed-out upload may actually have succeeded server-side.
            existing = request("GET", f"/releases/{release_id}/attach_files", token, params={"per_page": 100})
            if not isinstance(existing, list):
                raise ValueError("Unexpected Gitee attachment response")
            present = any(item.get("name") == path.name for item in existing)
            if present:
                log(f"Reusing existing attachment: {path.name}")
            else:
                log(f"Uploading: {path.name}")
                upload_file(path, release_id, token)
            log(f"Verifying anonymous download: {path.name}")
            if not verify_public(asset):
                raise RuntimeError(f"Attachment cannot be verified: {path.name}; refusing to replace it")
            log(f"Uploaded and verified: {path.name}")
            return
        except LowSpeedTransferError:
            raise
        except RuntimeError as exc:
            log(f"Attempt {attempt}/{ATTEMPTS} failed for {path.name}: {exc}")
            if attempt == ATTEMPTS:
                raise
            time.sleep(5 * attempt)


def load_release(tag: str) -> dict:
    if re.fullmatch(r"v\d+\.\d+\.\d+", tag) is None:
        raise ValueError("Expected a stable version tag")
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = requests.get(f"https://api.github.com/repos/XiaoyuBook/auto-bdsp-rng/releases/tags/{tag}",
                                headers=headers, timeout=(30, 60))
    except requests.RequestException as exc:
        raise RuntimeError(f"GitHub release lookup failed: {type(exc).__name__}") from None
    if not response.ok:
        raise RuntimeError(f"GitHub release lookup failed: HTTP {response.status_code}")
    source = response.json()
    if source["draft"] or source["prerelease"]:
        raise ValueError("Only published stable releases can be mirrored")
    return source


def mirror_assets(source: dict) -> dict[str, dict]:
    selected = {}
    for asset in source["assets"]:
        name = asset["name"]
        if not re.fullmatch(r"auto-bdsp-rng-v\d+\.\d+\.\d+(?:-to-v\d+\.\d+\.\d+)?-windows-x64\.(?:update\.zip|manifest\.json)", name):
            continue
        limit = MAX_UPDATE_ASSET_SIZE if name.endswith(".update.zip") else MAX_MIRROR_ASSET_SIZE
        if not 0 < asset["size"] <= limit:
            raise ValueError(f"Unsupported mirror asset size: {name}")
        selected[name] = asset
    return selected


def prepare_transfers(tag: str, paths: list[Path], metadata: dict) -> list[tuple[Path, dict]]:
    transfers = []
    for path, asset in zip(paths, metadata["assets"], strict=True):
        if asset["size"] <= MAX_MIRROR_ASSET_SIZE:
            transfers.append((path, asset))
            continue
        parts = []
        with path.open("rb") as source:
            index = 1
            while chunk := source.read(MIRROR_PART_SIZE):
                part = path.with_name(f"{path.name}.part{index:04d}")
                part.write_bytes(chunk)
                info = metadata_for(tag, [part])["assets"][0]
                parts.append(info)
                transfers.append((part, info))
                index += 1
        asset["parts"] = parts
        log(f"Split {path.name} into {len(parts)} Gitee attachments")
    if any("parts" in asset for asset in metadata["assets"]):
        source_helper = Path(__file__).with_name("upgrade-from-gitee.ps1")
        helper = paths[0].parent / source_helper.name
        # A retry on Windows/Linux must publish the same helper bytes.
        helper.write_bytes(source_helper.read_bytes().replace(b"\r\n", b"\n"))
        transfers.append((helper, metadata_for(tag, [helper])["assets"][0]))
    return transfers


def download_assets(tag: str, directory: Path) -> None:
    selected = mirror_assets(load_release(tag))
    directory.mkdir(parents=True, exist_ok=True)
    for name in sorted(selected):
        log(f"Downloading mirror asset: {name}")
        # This repository is public; credentials are needed only for API discovery.
        url = f"https://github.com/XiaoyuBook/auto-bdsp-rng/releases/download/{tag}/{name}"
        transfer(url, directory / name, max_size=selected[name]["size"])


def prepare_publication(tag: str, directory: Path) -> tuple[dict, dict, list[tuple[Path, dict]]]:
    source = load_release(tag)
    expected = mirror_assets(source)
    paths = [directory / name for name in sorted(expected)]
    if any(not path.is_file() for path in paths):
        raise ValueError("Downloaded release assets are incomplete")
    metadata = metadata_for(tag, paths)
    for asset in metadata["assets"]:
        original = expected[asset["name"]]
        if asset["size"] != original["size"] or asset["digest"] != original.get("digest"):
            raise ValueError("GitHub release asset size or SHA-256 mismatch")
    transfers = prepare_transfers(tag, paths, metadata)
    return source, metadata, transfers


def sync(tag: str, directory: Path, token: str) -> None:
    source, metadata, transfers = prepare_publication(tag, directory)
    releases = request("GET", "/releases", token, params={"per_page": 100})
    release = next((item for item in releases if item["tag_name"] == tag), None)
    if release is None:
        release = request("POST", "/releases", token, json={
            "tag_name": tag, "name": source["name"], "body": "发布文件同步中，请稍后下载。",
            "target_commitish": tag, "prerelease": False})
    release_id = release["id"]
    for path, asset in transfers:
        sync_asset(path, asset, release_id, token)
    request("PATCH", f"/releases/{release_id}", token, json=release_payload(tag, source, metadata))
    log(f"Gitee release ready: {tag}")


def release_payload(tag: str, source: dict, metadata: dict) -> dict:
    github_release = f"https://github.com/XiaoyuBook/auto-bdsp-rng/releases/tag/{tag}"
    body = (
        "## 下载说明\n\n"
        "Gitee 提供适合镜像的增量更新包，供软件内「帮助 → 检查更新」使用。"
        "增量包不能作为完整软件直接解压运行。\n\n"
        f"首次安装、修复安装或没有可用增量升级链时，请到 [GitHub 下载完整包]({github_release})。"
        "完整包保留在 GitHub；超过单附件大小的增量包会分卷上传到 Gitee。\n\n"
        + MARKER.sub("", source["body"]).rstrip()
    )
    if any("parts" in asset for asset in metadata["assets"]):
        helper_url = f"https://gitee.com/{REPOSITORY}/releases/download/{tag}/upgrade-from-gitee.ps1"
        body += (
            "\n\n## 旧版客户端升级\n\n"
            f"旧版不支持分卷下载。下载 [Gitee 升级辅助脚本]({helper_url})，在 PowerShell 中运行：\n\n"
            f"```powershell\npowershell -NoProfile -ExecutionPolicy Bypass -File .\\upgrade-from-gitee.ps1 -Tag {tag} -CurrentVersion 3.3.3\n```\n\n"
            "将 CurrentVersion 改为当前软件版本（需要本次发行版提供对应的直接增量包）。"
            "脚本只从 Gitee 获取分卷并校验合并后的原始增量包。完成后，在软件里打开「帮助 → 检查更新」并升级。"
            "脚本不会替换安装目录文件，原有升级器负责安装与回滚。\n"
        )
    body += "\n\n## 更新校验信息\n\n<!-- auto-bdsp-update:" + json.dumps(metadata, ensure_ascii=False, separators=(",", ":")) + " -->"
    # Gitee PATCH requires tag_name, name and body, unlike GitHub's partial update.
    return {"tag_name": tag, "name": source["name"], "body": body, "prerelease": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    parser.add_argument("--directory", type=Path, default=Path("release"))
    parser.add_argument("--download-only", action="store_true")
    parser.add_argument("--prepare-only", action="store_true",
                        help="Export release text and metadata for manual publication; never writes to Gitee")
    args = parser.parse_args()
    try:
        if args.download_only:
            download_assets(args.tag, args.directory)
        elif args.prepare_only:
            source, metadata, _ = prepare_publication(args.tag, args.directory)
            (args.directory / "gitee-release-body.md").write_text(release_payload(args.tag, source, metadata)["body"], encoding="utf-8")
            (args.directory / "gitee-update-metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
            log("Prepared local release text; publish only after verifying every public attachment")
        else:
            sync(args.tag, args.directory, os.environ["GITEE_ACCESS_TOKEN"])
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from None
