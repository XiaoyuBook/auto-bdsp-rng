import hashlib
import json
from types import SimpleNamespace

import pytest

from scripts import sync_gitee_release as sync


def setup_release(tmp_path, monkeypatch, *, existing=False, upload_ok=True, public_ok=True):
    package = tmp_path / "auto-bdsp-rng-v3.3.1-windows-x64.zip"
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
    def post(url, data, **kwargs):
        content = data.read()
        assert b"release bytes" in content
        assert b'name="access_token"' in content
        calls.append(("UPLOAD", url, {}))
        return SimpleNamespace(ok=upload_ok, status_code=413 if not upload_ok else 201)
    monkeypatch.setattr(sync.requests, "post", post)
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
    class Response:
        status_code = status
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def iter_content(self, chunk_size):
            yield content
    monkeypatch.setattr(sync.requests, "get", lambda *a, **k: Response())
    asset = {"browser_download_url": "https://gitee.com/example", "size": 7,
             "digest": "sha256:" + hashlib.sha256(b"package").hexdigest()}
    assert sync.verify_public(asset) is valid
