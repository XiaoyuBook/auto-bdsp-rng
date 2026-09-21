import hashlib
import io
import json
import threading
from urllib.error import URLError

import pytest

from auto_bdsp_rng import update_service as service
from tests.test_update_service import _asset, _release, _patch_bytes, _Response


def domestic_release(content=None):
    content = content or _patch_bytes("3.3.0", "3.3.1")
    asset = _asset("3.3.0", "3.3.1", len(content), digest=hashlib.sha256(content).hexdigest())
    asset["browser_download_url"] = asset["browser_download_url"].replace(
        "github.com/XiaoyuBook", "gitee.com/shekongsk")
    metadata = {"schema_version": 1, "tag_name": "v3.3.1", "assets": [asset]}
    body = "## 本次更新\n\n- 支持 Gitee 更新源。\n\n## 更新校验信息\n\n<!-- auto-bdsp-update:" + json.dumps(metadata) + " -->"
    return _release("3.3.1", body=body)


def test_domestic_source_works_without_github():
    calls = []
    def opener(request, **kwargs):
        calls.append(request.full_url)
        assert request.full_url == service.GITEE_API_URL
        return io.BytesIO(json.dumps([domestic_release()]).encode())
    plan = service.check_for_updates("3.3.0", opener=opener)
    assert plan.incremental_available
    assert plan.release_url == service.GITEE_RELEASES_URL + "/tag/v3.3.1"
    assert "auto-bdsp-update" not in plan.release_notes
    assert len(calls) == 1


@pytest.mark.parametrize("payload", [b"[]", b"not json", b"{}", json.dumps([_release("3.3.1")]).encode()])
def test_unready_or_invalid_domestic_source_falls_back(payload):
    calls = []
    def opener(request, **kwargs):
        calls.append(request.full_url)
        return io.BytesIO(payload if request.full_url == service.GITEE_API_URL else
                          json.dumps([_release("3.3.1", _asset("3.3.0", "3.3.1", 20))]).encode())
    assert service.check_for_updates("3.3.0", opener=opener).incremental_available
    assert calls == [service.GITEE_API_URL, service.RELEASES_API_URL]


def test_both_network_failures_are_reported():
    def opener(*args, **kwargs):
        raise URLError("offline")
    with pytest.raises(service.UpdateServiceError, match="均失败"):
        service.check_for_updates("3.3.0", opener=opener)


@pytest.mark.parametrize("github_available", [True, False])
def test_stale_mirror_checks_github_but_does_not_require_it(github_available):
    def opener(request, **kwargs):
        if request.full_url == service.GITEE_API_URL:
            return io.BytesIO(json.dumps([domestic_release()]).encode())
        if not github_available:
            raise URLError("offline")
        return io.BytesIO(json.dumps([_release("3.3.2")]).encode())
    plan = service.check_for_updates("3.3.1", opener=opener)
    assert plan.latest_version == ("3.3.2" if github_available else "3.3.1")


@pytest.mark.parametrize("suffix", ["?redirect=evil", "#fragment", "/extra"])
def test_domestic_download_url_is_strict(suffix):
    releases = service._normalize_gitee_releases([domestic_release()])
    releases[0]["assets"][0]["browser_download_url"] += suffix
    assert not service.build_update_plan("3.3.0", releases).incremental_available


@pytest.mark.parametrize("failure", ["network", "corrupt"])
def test_download_falls_back_and_validates_same_digest(tmp_path, failure):
    content = _patch_bytes("3.3.0", "3.3.1")
    plan = service.build_update_plan("3.3.0", service._normalize_gitee_releases([domestic_release(content)]))
    calls = []
    def opener(request, **kwargs):
        calls.append(request.full_url)
        if "gitee.com" in request.full_url:
            if failure == "network":
                raise URLError("offline")
            return _Response(b"x" * len(content))
        return _Response(content)
    paths = service.download_update_assets(plan, download_dir=tmp_path, opener=opener)
    assert paths[0].read_bytes() == content
    assert len(calls) == 2 and "github.com" in calls[1]
    assert not list(tmp_path.glob("*.part"))


def test_cancel_does_not_switch_source(tmp_path):
    plan = service.build_update_plan("3.3.0", service._normalize_gitee_releases([domestic_release()]))
    event = threading.Event()
    event.set()
    def opener(*args, **kwargs):
        pytest.fail("Cancelled update must not connect")
    with pytest.raises(service.UpdateDownloadCancelled):
        service.download_update_assets(plan, cancel_event=event, download_dir=tmp_path, opener=opener)
