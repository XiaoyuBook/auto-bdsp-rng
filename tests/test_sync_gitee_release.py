import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import shutil
import subprocess
import threading
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
    monkeypatch.setattr(sync, "load_release", lambda tag: source)
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
    payload = calls[-1][2]["json"]
    assert payload["tag_name"] == "v3.3.1"
    assert payload["name"] == "v3.3.1"
    assert payload["prerelease"] is False


def test_local_preparation_never_changes_gitee(tmp_path, monkeypatch):
    calls, _ = setup_release(tmp_path, monkeypatch)
    source, metadata, transfers = sync.prepare_publication('v3.3.1', tmp_path)
    payload = sync.release_payload('v3.3.1', source, metadata)
    assert not calls
    assert payload['tag_name'] == metadata['tag_name']
    assert len(transfers) == 1


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
        assert 'header = "Authorization: Bearer secret-token"' in kwargs["input"]
        assert "access_token=" not in kwargs["input"]
        assert command[command.index("--speed-limit") + 1] == "1024"
        assert "--max-time" in command and "--speed-time" in command
        assert "--http1.1" in command
        assert command[command.index("--header") + 1] == "Expect:"
        assert kwargs["timeout"] == sync.TRANSFER_TIMEOUT + 30
        return SimpleNamespace(returncode=0, stdout="201 123 0 0.250", stderr="")
    monkeypatch.setattr(sync.subprocess, "run", run)
    sync.upload_file(path, 123, "secret-token")


def test_real_curl_upload_to_server_that_rejects_expect(tmp_path, monkeypatch, capsys):
    if shutil.which("curl.exe" if sync.os.name == "nt" else "curl") is None:
        pytest.skip("curl is not installed")
    received = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def handle_expect_100(self):
            self.send_error(417, "Expect header is not supported")
            return False

        def do_POST(self):
            assert self.headers.get("Authorization") == "Bearer secret-token"
            self.connection.settimeout(5)
            body = self.rfile.read(int(self.headers["Content-Length"]))
            received.append((self.path, self.request_version, self.headers.get("Expect"), body))
            self.send_response(201)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(sync, "API", f"http://127.0.0.1:{server.server_port}")
    package = tmp_path / "package.zip"
    package.write_bytes(b"x" * (2 * 1024 * 1024))
    try:
        sync.upload_file(package, 123, "secret-token")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert len(received) == 1
    path, version, expect, body = received[0]
    assert path == "/releases/123/attach_files"
    assert version == "HTTP/1.1" and expect is None
    assert b"secret-token" not in body and package.read_bytes() in body
    output = capsys.readouterr().out
    assert "HTTP 201" in output and "uploaded" in output
    assert "secret-token" not in output


def test_stalled_upload_reports_numeric_progress_without_credentials(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sync.shutil, "which", lambda command: "curl")
    monkeypatch.setattr(sync.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=28, stdout="100 3070186 0 62.500", stderr="secret-token"))
    with pytest.raises(RuntimeError, match="curl exit 28, HTTP 100"):
        sync.transfer("https://gitee.com/example", tmp_path / "out", form="secret-token")
    output = capsys.readouterr().out
    assert "uploaded 3070186 bytes" in output and "elapsed 62.500 s" in output
    assert "secret-token" not in output


def test_unexpected_curl_output_is_not_logged(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sync.shutil, "which", lambda command: "curl")
    monkeypatch.setattr(sync.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=28, stdout="secret-token", stderr="secret-token"))
    with pytest.raises(RuntimeError, match="HTTP unknown") as error:
        sync.transfer("https://gitee.com/example", tmp_path / "out", form="secret-token")
    assert "secret-token" not in str(error.value) + capsys.readouterr().out


def test_public_download_keeps_default_protocol_and_https_redirects(tmp_path, monkeypatch):
    monkeypatch.setattr(sync.shutil, "which", lambda command: "curl")
    def run(command, **kwargs):
        assert "--http1.1" not in command and "--header" not in command
        assert command[command.index("--proto-redir") + 1] == "=https"
        assert command[command.index("--max-filesize") + 1] == "7"
        assert kwargs["input"] is None
        return SimpleNamespace(returncode=0, stdout="200 0 7 0.250", stderr="")
    monkeypatch.setattr(sync.subprocess, "run", run)
    sync.transfer("https://gitee.com/example", tmp_path / "out", max_size=7)


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


def test_large_patch_is_downloaded_for_splitting_but_full_archive_is_excluded(tmp_path, monkeypatch):
    calls, source = setup_release(tmp_path, monkeypatch)
    patch = source['assets'][0]['name']
    source['assets'] += [
        {'name': 'auto-bdsp-rng-v3.3.1-windows-x64.zip', 'size': 695_000_000},
        {'name': 'auto-bdsp-rng-v3.2.0-to-v3.3.1-windows-x64.update.zip', 'size': sync.MAX_MIRROR_ASSET_SIZE + 1},
        {'name': 'auto-bdsp-rng-v3.3.1-windows-x64.manifest.json', 'size': 100},
    ]
    commands = []
    monkeypatch.setattr(sync, 'transfer', lambda url, path, **kwargs: commands.append((url, path, kwargs)))
    sync.download_assets('v3.3.1', tmp_path)
    names = {path.name for url, path, kwargs in commands}
    assert names == {patch, 'auto-bdsp-rng-v3.2.0-to-v3.3.1-windows-x64.update.zip',
                     'auto-bdsp-rng-v3.3.1-windows-x64.manifest.json'}


def test_split_mirror_verifies_all_parts_before_publishing_metadata(tmp_path, monkeypatch):
    calls, source = setup_release(tmp_path, monkeypatch)
    monkeypatch.setattr(sync, "MAX_MIRROR_ASSET_SIZE", 5)
    monkeypatch.setattr(sync, "MIRROR_PART_SIZE", 4)
    uploaded = []
    monkeypatch.setattr(sync, "upload_file", lambda path, *a: uploaded.append(path))
    sync.sync("v3.3.1", tmp_path, "test-token")
    body = calls[-1][2]["json"]["body"]
    metadata = json.loads(body.split('<!-- auto-bdsp-update:')[1].split(' -->')[0])
    asset = metadata['assets'][0]
    assert asset['digest'] == source['assets'][0]['digest']
    parts = asset['parts']
    assert len(parts) == 4 and sum(p['size'] for p in parts) == asset['size']
    assert b''.join((tmp_path / p['name']).read_bytes() for p in parts) == b'release bytes'
    assert uploaded[-1].name == 'upgrade-from-gitee.ps1'
    assert all(p.name.endswith(('.part0001', '.part0002', '.part0003', '.part0004', '.ps1')) for p in uploaded)
    assert 'CurrentVersion' in body


def test_invalid_part_never_publishes_reconstructed_asset(tmp_path, monkeypatch):
    calls, _ = setup_release(tmp_path, monkeypatch)
    monkeypatch.setattr(sync, "MAX_MIRROR_ASSET_SIZE", 5)
    monkeypatch.setattr(sync, "MIRROR_PART_SIZE", 4)
    monkeypatch.setattr(sync, "upload_file", lambda *a: None)
    monkeypatch.setattr(sync, "verify_public", lambda asset: not asset['name'].endswith('part0002'))
    with pytest.raises(RuntimeError):
        sync.sync('v3.3.1', tmp_path, 'test-token')
    assert not any(c[0] == 'PATCH' for c in calls)


def test_missing_selected_patch_still_prevents_publication(tmp_path, monkeypatch):
    calls, source = setup_release(tmp_path, monkeypatch)
    (tmp_path / source['assets'][0]['name']).unlink()
    with pytest.raises(ValueError, match='incomplete'):
        sync.sync('v3.3.1', tmp_path, 'test-token')
    assert not calls


def test_release_discovery_uses_api_token_without_requiring_gh(monkeypatch):
    monkeypatch.setenv('GH_TOKEN', 'secret-token')
    def get(url, **kwargs):
        assert url.endswith('/releases/tags/v3.4.0')
        assert kwargs['headers']['Authorization'] == 'Bearer secret-token'
        return SimpleNamespace(ok=True, json=lambda: dict(draft=False, prerelease=False))
    monkeypatch.setattr(sync.requests, 'get', get)
    assert sync.load_release('v3.4.0')['draft'] is False


def test_release_discovery_does_not_log_response_or_token(monkeypatch):
    monkeypatch.setattr(sync.requests, 'get', lambda *a, **k: SimpleNamespace(
        ok=False, status_code=403, text='secret-token'))
    with pytest.raises(RuntimeError, match='HTTP 403') as error:
        sync.load_release('v3.4.0')
    assert 'secret-token' not in str(error.value)
