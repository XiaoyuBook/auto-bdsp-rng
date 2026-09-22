import hashlib
import json
import subprocess
from types import SimpleNamespace

import pytest

from scripts import sync_gitee_release as sync


def setup_release(tmp_path, monkeypatch, *, existing=False, upload_ok=True, public_ok=True):
    monkeypatch.setattr(sync.time, "sleep", lambda seconds: None)
    package = tmp_path / "auto-bdsp-rng-v3.3.0-to-v3.3.1-windows-x64.update.zip"
    package.write_bytes(b"release bytes")
    metadata = sync.metadata_for("v3.3.1", [package])
    source = dict(name="v3.3.1", body="## 本次更新\n\n- 支持 Gitee 更新源。", draft=False,
                  prerelease=False, assets=metadata["assets"])
    monkeypatch.setattr(sync.subprocess, "check_output", lambda *a, **k: json.dumps(source))
    calls = []
    def request(method, endpoint, token, **kwargs):
        calls.append((method, endpoint, kwargs))
        if endpoint == "/releases" and method == "GET":
            return [{"tag_name": "v3.3.1", "id": 123}] if existing else []
        if endpoint == "/releases" and method == "POST":
            return {"id": 123}
        if endpoint.endswith("attach_files"):
            return [{"name": package.name}] if existing else []
        return {}
    monkeypatch.setattr(sync, "request", request)
    def upload(path, release_id, token):
        assert path.read_bytes() == b"release bytes"
        calls.append(("UPLOAD", str(path), {}))
        if not upload_ok:
            raise RuntimeError("Transfer failed: curl exit 22, HTTP 413")
    monkeypatch.setattr(sync, "upload_file", upload)
    monkeypatch.setattr(sync, "verify_public", lambda asset: public_ok)
    return calls, source


def test_metadata_only_published_after_upload_and_verification(tmp_path, monkeypatch):
    calls, _ = setup_release(tmp_path, monkeypatch)
    sync.sync("v3.3.1", tmp_path, "test-token")
    assert calls[-1][0] == "PATCH"
    assert any(call[0] == "UPLOAD" for call in calls[:-1])
    body = calls[-1][2]["json"]["body"]
    assert "auto-bdsp-update:" in body
    assert "test-token" not in body


@pytest.mark.parametrize("failure", ["upload", "public"])
def test_failed_upload_or_public_download_never_publishes_metadata(tmp_path, monkeypatch, failure):
    calls, _ = setup_release(tmp_path, monkeypatch, upload_ok=failure != "upload", public_ok=failure != "public")
    with pytest.raises(RuntimeError):
        sync.sync("v3.3.1", tmp_path, "test-token")
    assert not any(call[0] == "PATCH" for call in calls)


def test_retry_verifies_and_reuses_existing_attachment(tmp_path, monkeypatch):
    calls, _ = setup_release(tmp_path, monkeypatch, existing=True)
    sync.sync("v3.3.1", tmp_path, "test-token")
    assert not any(call[0] in ("UPLOAD", "POST", "DELETE") for call in calls)
    assert calls[-1][0] == "PATCH"


def test_conflicting_existing_attachment_is_not_overwritten(tmp_path, monkeypatch):
    calls, _ = setup_release(tmp_path, monkeypatch, existing=True, public_ok=False)
    with pytest.raises(RuntimeError, match="refusing to replace"):
        sync.sync("v3.3.1", tmp_path, "test-token")
    assert not any(call[0] in ("PATCH", "UPLOAD", "DELETE") for call in calls)


def test_github_digest_mismatch_stops_before_writing_gitee(tmp_path, monkeypatch):
    calls, source = setup_release(tmp_path, monkeypatch)
    source["assets"][0]["digest"] = "sha256:" + "0" * 64
    with pytest.raises(ValueError, match="mismatch"):
        sync.sync("v3.3.1", tmp_path, "test-token")
    assert not calls


@pytest.mark.parametrize("content,status,valid", [(b"package", 200, True), (b"corrupt", 200, False), (b"login", 403, False)])
def test_public_download_requires_matching_bytes(monkeypatch, content, status, valid):
    def transfer(url, output, **kwargs):
        assert kwargs["max_size"] == 7
        if status != 200:
            raise RuntimeError(f"HTTP {status}")
        output.write_bytes(content)
    monkeypatch.setattr(sync, "transfer", transfer)
    asset = {"name": "package.zip", "browser_download_url": "https://gitee.com/example", "size": 7,
             "digest": "sha256:" + hashlib.sha256(b"package").hexdigest()}
    assert sync.verify_public(asset) is valid


def test_upload_timeout_rechecks_remote_before_retrying(tmp_path, monkeypatch):
    calls, _ = setup_release(tmp_path, monkeypatch)
    original = sync.request
    uploaded = False
    def request(method, endpoint, token, **kwargs):
        if endpoint.endswith("attach_files") and uploaded:
            return [{"name": "auto-bdsp-rng-v3.3.0-to-v3.3.1-windows-x64.update.zip"}]
        return original(method, endpoint, token, **kwargs)
    def upload(*args):
        nonlocal uploaded
        assert not uploaded, "Must not upload twice after an ambiguous timeout"
        uploaded = True
        raise RuntimeError("Transfer timed out after server accepted upload")
    monkeypatch.setattr(sync, "request", request)
    monkeypatch.setattr(sync, "upload_file", upload)
    sync.sync("v3.3.1", tmp_path, "test-token")
    assert calls[-1][0] == "PATCH"


def test_network_upload_retry_is_bounded(tmp_path, monkeypatch):
    calls, _ = setup_release(tmp_path, monkeypatch, upload_ok=False)
    with pytest.raises(RuntimeError):
        sync.sync("v3.3.1", tmp_path, "test-token")
    assert sum(c[0] == "UPLOAD" for c in calls) == sync.ATTEMPTS
    assert not any(c[0] == "PATCH" for c in calls)


def test_curl_upload_uses_stdin_credentials_and_hard_deadline(tmp_path, monkeypatch):
    monkeypatch.setattr(sync.shutil, "which", lambda command: "curl")
    path = tmp_path / "package.zip"
    path.write_bytes(b"data")
    def run(command, **kwargs):
        assert "secret-token" not in " ".join(command)
        assert "secret-token" in kwargs["input"]
        assert "form-string" in kwargs["input"]
        assert "--max-time" in command and "--speed-time" in command
        assert kwargs["timeout"] == sync.TRANSFER_TIMEOUT + 30
        return SimpleNamespace(returncode=0, stdout="201", stderr="")
    monkeypatch.setattr(sync.subprocess, "run", run)
    sync.upload_file(path, 123, "secret-token")


def test_curl_deadline_and_error_do_not_expose_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(sync.shutil, "which", lambda command: "curl")
    def run(command, **kwargs):
        raise subprocess.TimeoutExpired(command + ["sensitive"], 10, output="sensitive")
    monkeypatch.setattr(sync.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="wall-clock") as error:
        sync.transfer("https://gitee.com/example", tmp_path / "out", form="sensitive")
    assert "sensitive" not in str(error.value)


def test_api_retries_transient_errors_without_logging_token(monkeypatch, capsys):
    monkeypatch.setattr(sync.time, "sleep", lambda seconds: None)
    calls = []
    def request(*args, **kwargs):
        calls.append(1)
        if len(calls) < 3:
            raise sync.requests.ConnectionError("sensitive-token")
        return SimpleNamespace(ok=True, json=lambda: [])
    monkeypatch.setattr(sync.requests, "request", request)
    assert sync.request("GET", "/releases", "sensitive-token") == []
    assert len(calls) == 3
    assert "sensitive-token" not in capsys.readouterr().out


def test_full_archive_is_not_required_or_advertised_as_a_domestic_asset(tmp_path, monkeypatch):
    calls, source = setup_release(tmp_path, monkeypatch)
    source['assets'].append({'name': 'auto-bdsp-rng-v3.3.1-windows-x64.zip', 'size': 695_000_000})
    sync.sync('v3.3.1', tmp_path, 'test-token')
    body = calls[-1][2]['json']['body']
    assert '[GitHub 下载完整包](https://github.com/XiaoyuBook/auto-bdsp-rng/releases/tag/v3.3.1)' in body
    metadata = json.loads(body.split('<!-- auto-bdsp-update:')[1].split(' -->')[0])
    assert len(metadata['assets']) == 1
    assert metadata['assets'][0]['name'].endswith('.update.zip')
    from auto_bdsp_rng.update_service import _normalize_gitee_releases, build_update_plan
    releases = _normalize_gitee_releases([{'tag_name': 'v3.3.1', 'body': body}])
    assert build_update_plan('3.3.0', releases).incremental_available


def test_oversized_patch_is_excluded_and_downloads_only_mirror_assets(tmp_path, monkeypatch):
    calls, source = setup_release(tmp_path, monkeypatch)
    patch = source['assets'][0]['name']
    source['assets'] += [
        {'name': 'auto-bdsp-rng-v3.3.1-windows-x64.zip', 'size': 695_000_000},
        {'name': 'auto-bdsp-rng-v3.2.0-to-v3.3.1-windows-x64.update.zip', 'size': sync.MAX_MIRROR_ASSET_SIZE + 1},
        {'name': 'auto-bdsp-rng-v3.3.1-windows-x64.manifest.json', 'size': 100},
    ]
    commands = []
    monkeypatch.setattr(sync.subprocess, 'run', lambda command, **kwargs: commands.append(command))
    sync.download_assets('v3.3.1', tmp_path)
    names = {command[command.index('--pattern') + 1] for command in commands}
    assert names == {patch, 'auto-bdsp-rng-v3.3.1-windows-x64.manifest.json'}


def test_missing_selected_patch_still_prevents_publication(tmp_path, monkeypatch):
    calls, source = setup_release(tmp_path, monkeypatch)
    (tmp_path / source['assets'][0]['name']).unlink()
    with pytest.raises(ValueError, match='incomplete'):
        sync.sync('v3.3.1', tmp_path, 'test-token')
    assert not calls
