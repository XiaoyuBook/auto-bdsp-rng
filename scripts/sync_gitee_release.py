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
import uuid

import requests

REPOSITORY = "shekongsk/auto-bdsp-rng"
API = f"https://gitee.com/api/v5/repos/{REPOSITORY}"
MARKER = re.compile(r"\n*## 更新校验信息\n.*", re.DOTALL)


def request(method: str, endpoint: str, token: str, **kwargs):
    # Never print response bodies, request URLs or headers: they may contain credentials.
    response = requests.request(method, API + endpoint, headers={"Authorization": f"Bearer {token}"},
                                timeout=(30, 120), **kwargs)
    if not response.ok:
        raise RuntimeError(f"Gitee {method} failed: HTTP {response.status_code}")
    return response.json()


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
        with requests.get(asset["browser_download_url"], stream=True, timeout=(30, 120)) as response:
            if response.status_code != 200:
                return False
            digest = hashlib.sha256()
            size = 0
            for chunk in response.iter_content(1024 * 1024):
                size += len(chunk)
                if size > asset["size"]:
                    return False
                digest.update(chunk)
            return size == asset["size"] and f"sha256:{digest.hexdigest()}" == asset["digest"]
    except requests.RequestException:
        return False


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
    existing = request("GET", f"/releases/{release_id}/attach_files", token, params={"per_page": 100})
    if not isinstance(existing, list):
        raise ValueError("Unexpected Gitee attachment response")
    for path, asset in zip(paths, metadata["assets"], strict=True):
        if any(item.get("name") == path.name for item in existing):
            if not verify_public(asset):
                raise RuntimeError(f"Existing attachment cannot be verified: {path.name}; refusing to replace it")
            continue
        with path.open("rb") as stream, tempfile.TemporaryFile() as form:
            boundary = uuid.uuid4().hex
            form.write((f'--{boundary}\r\nContent-Disposition: form-data; name="access_token"\r\n\r\n'
                        f'{token}\r\n--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                        f'filename="{path.name}"\r\nContent-Type: application/octet-stream\r\n\r\n').encode())
            shutil.copyfileobj(stream, form, 1024 * 1024)
            form.write(f'\r\n--{boundary}--\r\n'.encode())
            form.seek(0)
            response = requests.post(API + f"/releases/{release_id}/attach_files", data=form,
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, timeout=(30, 600))
            if not response.ok:
                raise RuntimeError(f"Gitee upload failed: HTTP {response.status_code} ({path.name})")
        if not verify_public(asset):
            raise RuntimeError(f"Anonymous download verification failed: {path.name}")
        print(f"Uploaded and verified: {path.name}")
    body = MARKER.sub("", source["body"]).rstrip()
    body += "\n\n## 更新校验信息\n\n<!-- auto-bdsp-update:" + json.dumps(metadata, ensure_ascii=False, separators=(",", ":")) + " -->"
    request("PATCH", f"/releases/{release_id}", token, json={"body": body, "name": source["name"]})
    print(f"Gitee release ready: {tag}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    parser.add_argument("--directory", type=Path, default=Path("release"))
    args = parser.parse_args()
    try:
        sync(args.tag, args.directory, os.environ["GITEE_ACCESS_TOKEN"])
    except requests.RequestException:
        raise SystemExit("Gitee network request failed; retry the sync workflow") from None
