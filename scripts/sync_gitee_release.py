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
TRANSFER_TIMEOUT = 900
ATTEMPTS = 3


def log(message: str) -> None:
    print(message, flush=True)


def transfer(url: str, output: Path, *, form: str | None = None, max_size: int | None = None) -> None:
    """Bound the entire transfer, including a slowly trickling connection."""
    curl = shutil.which("curl.exe" if os.name == "nt" else "curl")
    if curl is None:
        raise RuntimeError("curl is required for bounded release transfers")
    command = [curl, "--silent", "--show-error", "--fail", "--connect-timeout", "30",
               "--max-time", str(TRANSFER_TIMEOUT), "--speed-limit", "32768", "--speed-time", "60",
               "--output", str(output), "--write-out", "%{http_code}"]
    if form is None:
        command += ["--location", "--proto", "=https", "--proto-redir", "=https"]
    else:
        # Credentials travel over stdin, never in command-line arguments or logs.
        command += ["--config", "-"]
    if max_size is not None:
        command += ["--max-filesize", str(max_size)]
    command.append(url)
    try:
        result = subprocess.run(command, input=form, capture_output=True, text=True,
                                timeout=TRANSFER_TIMEOUT + 30)
    except subprocess.TimeoutExpired:
        raise RuntimeError("Transfer exceeded its wall-clock deadline") from None
    if result.returncode or not result.stdout.strip().startswith("2"):
        # Do not print curl stderr or server bodies, which may echo credentials.
        status = result.stdout.strip()
        status = status if re.fullmatch(r"\d{3}", status) else "unknown"
        raise RuntimeError(f"Transfer failed: curl exit {result.returncode}, HTTP {status}")


def upload_file(path: Path, release_id: int, token: str) -> None:
    def quoted(value: str) -> str:
        if "\n" in value or "\r" in value:
            raise ValueError("Invalid multipart value")
        return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'
    # form-string prevents a token beginning with @ from being treated as a filename.
    form = "form-string = " + quoted("access_token=" + token) + "\n"
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
        except RuntimeError as exc:
            log(f"Attempt {attempt}/{ATTEMPTS} failed for {path.name}: {exc}")
            if attempt == ATTEMPTS:
                raise
            time.sleep(5 * attempt)


def sync(tag: str, directory: Path, token: str) -> None:
    if re.fullmatch(r"v\d+\.\d+\.\d+", tag) is None:
        raise ValueError("Expected a stable version tag")
    raw = subprocess.check_output(["gh", "api",
        f"repos/XiaoyuBook/auto-bdsp-rng/releases/tags/{tag}"], text=True, encoding="utf-8")
    source = json.loads(raw)
    if source["draft"] or source["prerelease"]:
        raise ValueError("Only published stable releases can be mirrored")
    paths = sorted(directory.glob("*.zip")) + sorted(directory.glob("*.manifest.json"))
    expected = {asset["name"]: asset for asset in source["assets"]
                if asset["name"].endswith((".zip", ".manifest.json"))}
    if not paths or {path.name for path in paths} != set(expected):
        raise ValueError("Downloaded release assets are incomplete")
    metadata = metadata_for(tag, paths)
    for asset in metadata["assets"]:
        original = expected[asset["name"]]
        if asset["size"] != original["size"] or asset["digest"] != original.get("digest"):
            raise ValueError("GitHub release asset size or SHA-256 mismatch")
    releases = request("GET", "/releases", token, params={"per_page": 100})
    release = next((item for item in releases if item["tag_name"] == tag), None)
    if release is None:
        release = request("POST", "/releases", token, json={
            "tag_name": tag, "name": source["name"], "body": "发布文件同步中，请稍后下载。",
            "target_commitish": tag, "prerelease": False})
    release_id = release["id"]
    for path, asset in zip(paths, metadata["assets"], strict=True):
        sync_asset(path, asset, release_id, token)
    body = MARKER.sub("", source["body"]).rstrip()
    body += "\n\n## 更新校验信息\n\n<!-- auto-bdsp-update:" + json.dumps(metadata, ensure_ascii=False, separators=(",", ":")) + " -->"
    request("PATCH", f"/releases/{release_id}", token, json={"body": body, "name": source["name"]})
    log(f"Gitee release ready: {tag}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    parser.add_argument("--directory", type=Path, default=Path("release"))
    args = parser.parse_args()
    try:
        sync(args.tag, args.directory, os.environ["GITEE_ACCESS_TOKEN"])
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from None
