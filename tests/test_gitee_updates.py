import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
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


def split_release():
    content = _patch_bytes('3.3.3', '3.4.0')
    asset = _asset('3.3.3', '3.4.0', len(content), digest=hashlib.sha256(content).hexdigest())
    asset['browser_download_url'] = asset['browser_download_url'].replace('github.com/XiaoyuBook', 'gitee.com/shekongsk')
    chunks = [content[:100], content[100:200], content[200:]]
    asset['parts'] = [dict(name=f"{asset['name']}.part{i:04d}", size=len(data),
                          digest='sha256:' + hashlib.sha256(data).hexdigest(),
                          browser_download_url=f"{asset['browser_download_url']}.part{i:04d}")
                      for i, data in enumerate(chunks, 1)]
    metadata = dict(schema_version=1, tag_name='v3.4.0', assets=[asset])
    release = _release('3.4.0', body='<!-- auto-bdsp-update:' + json.dumps(metadata) + ' -->')
    return content, chunks, metadata, release


def test_split_download_reconstructs_original_zip_using_only_gitee(tmp_path):
    content, chunks, _, release = split_release()
    plan = service.build_update_plan('3.3.3', service._normalize_gitee_releases([release]))
    calls = []
    def opener(request, **kwargs):
        calls.append(request.full_url)
        assert request.full_url.startswith('https://gitee.com/shekongsk/auto-bdsp-rng/releases/download/v3.4.0/')
        return _Response(chunks[int(request.full_url[-4:]) - 1])
    paths = service.download_update_assets(plan, download_dir=tmp_path, opener=opener)
    assert paths[0].read_bytes() == content and len(calls) == 3


@pytest.mark.parametrize('failure', ['wrong-hash', 'reorder', 'wrong-url', 'wrong-total', 'empty'])
def test_split_metadata_rejects_invalid_parts(failure):
    _, _, _, release = split_release()
    normalized = service._normalize_gitee_releases([release])
    asset = normalized[0]['assets'][0]
    if failure == 'wrong-hash': asset['parts'][0]['digest'] = 'sha256:invalid'
    if failure == 'reorder': asset['parts'].reverse()
    if failure == 'wrong-url': asset['parts'][0]['browser_download_url'] += '?redirect=evil'
    if failure == 'wrong-total': asset['parts'][0]['size'] += 1
    if failure == 'empty': asset['parts'] = []
    assert not service.build_update_plan('3.3.3', normalized).incremental_available


def test_corrupt_split_part_cannot_create_cached_update(tmp_path):
    _, chunks, _, release = split_release()
    plan = service.build_update_plan('3.3.3', service._normalize_gitee_releases([release]))
    def opener(request, **kwargs):
        if 'github.com' in request.full_url:
            raise URLError('GitHub unavailable')
        index = int(request.full_url[-4:]) - 1
        return _Response(b'x' * len(chunks[index]) if index == 1 else chunks[index])
    with pytest.raises(service.UpdateServiceError):
        service.download_update_assets(plan, download_dir=tmp_path, opener=opener)
    assert not list(tmp_path.iterdir())


def test_split_download_falls_back_to_original_github_zip(tmp_path):
    content, _, _, release = split_release()
    plan = service.build_update_plan('3.3.3', service._normalize_gitee_releases([release]))
    calls = []
    def opener(request, **kwargs):
        calls.append(request.full_url)
        if 'gitee.com' in request.full_url:
            raise URLError('Gitee offline')
        assert request.full_url.endswith('.update.zip')
        return _Response(content)
    assert service.download_update_assets(plan, download_dir=tmp_path, opener=opener)[0].read_bytes() == content
    assert len(calls) == 2


def test_split_download_checks_original_hash_even_when_part_hashes_match(tmp_path):
    _, chunks, metadata, release = split_release()
    asset = metadata['assets'][0]
    asset['digest'] = 'sha256:' + '0' * 64
    release['body'] = '<!-- auto-bdsp-update:' + json.dumps(metadata) + ' -->'
    plan = service.build_update_plan('3.3.3', service._normalize_gitee_releases([release]))
    def opener(request, **kwargs):
        if 'github.com' in request.full_url:
            raise URLError('GitHub offline')
        return _Response(chunks[int(request.full_url[-4:]) - 1])
    with pytest.raises(service.UpdateServiceError):
        service.download_update_assets(plan, download_dir=tmp_path, opener=opener)
    assert not list(tmp_path.iterdir())


def test_powershell_helper_populates_cache_usable_by_old_client_without_github(tmp_path):
    pwsh = shutil.which('pwsh')
    if pwsh is None:
        pytest.skip('PowerShell is not installed')
    content, chunks, metadata, release = split_release()
    cache = tmp_path / 'cache'
    parts_dir = cache / 'v3.4.0' / '.gitee-parts'
    parts_dir.mkdir(parents=True)
    for part, data in zip(metadata['assets'][0]['parts'], chunks):
        (parts_dir / part['name']).write_bytes(data)
    metadata_path = tmp_path / 'metadata.json'
    metadata_path.write_text(json.dumps(metadata), encoding='utf-8')
    helper = Path(__file__).resolve().parents[1] / 'scripts' / 'upgrade-from-gitee.ps1'
    result = subprocess.run([pwsh, '-NoProfile', '-File', str(helper), '-Tag', 'v3.4.0',
                             '-CurrentVersion', '3.3.3', '-MetadataFile', str(metadata_path),
                             '-CacheRoot', str(cache)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    # Remove the new fields to exercise the old client's single-ZIP cache path.
    normalized = service._normalize_gitee_releases([release])
    del normalized[0]['assets'][0]['parts']
    plan = service.build_update_plan('3.3.3', normalized)
    def offline(*args, **kwargs):
        pytest.fail('A verified cached ZIP must not access GitHub or Gitee')
    paths = service.download_update_assets(plan, download_dir=cache / 'v3.4.0', opener=offline)
    assert paths[0].read_bytes() == content
