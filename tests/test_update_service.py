from __future__ import annotations

import hashlib
import io
import json
import threading
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import auto_bdsp_rng.update_service as update_service

from auto_bdsp_rng.update_service import (
    UpdateAsset,
    UpdatePlan,
    UpdateServiceError,
    build_update_plan,
    download_update_assets,
    launch_update_installer,
)


def _asset(from_version: str, to_version: str, size: int, *, digest: str = "a" * 64) -> dict[str, object]:
    name = f"auto-bdsp-rng-v{from_version}-to-v{to_version}-windows-x64.update.zip"
    return {
        "name": name,
        "size": size,
        "digest": f"sha256:{digest}",
        "browser_download_url": (
            f"https://github.com/XiaoyuBook/auto-bdsp-rng/releases/download/v{to_version}/{name}"
        ),
    }


def _release(version: str, *assets: dict[str, object], prerelease: bool = False) -> dict[str, object]:
    return {
        "tag_name": f"v{version}",
        "draft": False,
        "prerelease": prerelease,
        "html_url": f"https://github.com/XiaoyuBook/auto-bdsp-rng/releases/tag/v{version}",
        "body": f"notes {version}",
        "assets": list(assets),
    }


def _patch_bytes(from_version: str, to_version: str) -> bytes:
    stream = io.BytesIO()
    manifest = {
        "schema_version": 1,
        "application": "auto-bdsp-rng",
        "platform": "windows-x64",
        "from_version": from_version,
        "to_version": to_version,
        "files": [],
        "remove": [],
    }
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("update.json", json.dumps(manifest))
    return stream.getvalue()


class _Response(io.BytesIO):
    def __init__(self, data: bytes):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}


def test_build_update_plan_uses_lowest_download_size_chain_to_latest():
    releases = [
        _release("2.2.3", _asset("2.2.2", "2.2.3", 30), _asset("2.2.0", "2.2.3", 100)),
        _release("2.2.2", _asset("2.2.1", "2.2.2", 20)),
        _release("2.2.1", _asset("2.2.0", "2.2.1", 10)),
        _release("9.0.0", prerelease=True),
    ]

    plan = build_update_plan("2.2.0", releases)

    assert plan.latest_version == "2.2.3"
    assert plan.incremental_available is True
    assert [(item.from_version, item.to_version) for item in plan.assets] == [
        ("2.2.0", "2.2.1"),
        ("2.2.1", "2.2.2"),
        ("2.2.2", "2.2.3"),
    ]
    assert plan.download_size == 60


def test_build_update_plan_reports_full_release_fallback_when_chain_is_missing():
    plan = build_update_plan("2.2.0", [_release("2.2.2", _asset("2.2.1", "2.2.2", 20))])

    assert plan.update_available is True
    assert plan.incremental_available is False
    assert plan.assets == ()


def test_build_update_plan_rejects_chain_over_total_download_limit():
    half_plus_one = update_service.MAX_UPDATE_DOWNLOAD_SIZE // 2 + 1
    releases = [
        _release("2.2.2", _asset("2.2.1", "2.2.2", half_plus_one)),
        _release("2.2.1", _asset("2.2.0", "2.2.1", half_plus_one)),
    ]

    plan = build_update_plan("2.2.0", releases)

    assert plan.update_available is True
    assert plan.incremental_available is False
    assert plan.assets == ()


def test_build_update_plan_ignores_assets_without_github_sha256_digest():
    invalid = _asset("2.2.0", "2.2.1", 20)
    invalid["digest"] = None

    plan = build_update_plan("2.2.0", [_release("2.2.1", invalid)])

    assert plan.update_available is True
    assert plan.incremental_available is False


def test_download_update_assets_streams_and_validates_patch(tmp_path: Path):
    content = _patch_bytes("2.2.0", "2.2.1")
    digest = hashlib.sha256(content).hexdigest()
    asset = UpdateAsset(
        "auto-bdsp-rng-v2.2.0-to-v2.2.1-windows-x64.update.zip",
        "https://github.com/XiaoyuBook/auto-bdsp-rng/releases/download/v2.2.1/"
        "auto-bdsp-rng-v2.2.0-to-v2.2.1-windows-x64.update.zip",
        len(content),
        f"sha256:{digest}",
        "2.2.0",
        "2.2.1",
    )
    plan = UpdatePlan("2.2.0", "2.2.1", (asset,), "https://example.invalid", "")
    progress: list[tuple[int, int]] = []

    outputs = download_update_assets(
        plan,
        lambda downloaded, total: progress.append((downloaded, total)),
        threading.Event(),
        download_dir=tmp_path,
        opener=lambda *_args, **_kwargs: _Response(content),
    )

    assert outputs == (tmp_path / asset.name,)
    assert outputs[0].read_bytes() == content
    assert progress[-1] == (len(content), len(content))


def test_download_update_assets_removes_partial_file_on_digest_failure(tmp_path: Path):
    content = _patch_bytes("2.2.0", "2.2.1")
    asset = UpdateAsset(
        "auto-bdsp-rng-v2.2.0-to-v2.2.1-windows-x64.update.zip",
        "https://github.com/XiaoyuBook/auto-bdsp-rng/releases/download/v2.2.1/"
        "auto-bdsp-rng-v2.2.0-to-v2.2.1-windows-x64.update.zip",
        len(content),
        f"sha256:{'0' * 64}",
        "2.2.0",
        "2.2.1",
    )
    plan = UpdatePlan("2.2.0", "2.2.1", (asset,), "https://example.invalid", "")

    with pytest.raises(UpdateServiceError, match="SHA-256"):
        download_update_assets(
            plan,
            download_dir=tmp_path,
            opener=lambda *_args, **_kwargs: _Response(content),
        )

    assert list(tmp_path.iterdir()) == []


def test_download_update_assets_removes_partial_file_when_progress_callback_raises(
    tmp_path: Path,
):
    content = _patch_bytes("2.2.0", "2.2.1")
    digest = hashlib.sha256(content).hexdigest()
    asset = UpdateAsset(
        "auto-bdsp-rng-v2.2.0-to-v2.2.1-windows-x64.update.zip",
        "https://github.com/XiaoyuBook/auto-bdsp-rng/releases/download/v2.2.1/"
        "auto-bdsp-rng-v2.2.0-to-v2.2.1-windows-x64.update.zip",
        len(content),
        f"sha256:{digest}",
        "2.2.0",
        "2.2.1",
    )
    plan = UpdatePlan("2.2.0", "2.2.1", (asset,), "https://example.invalid", "")

    class ProgressCallbackError(RuntimeError):
        pass

    def fail_progress(_downloaded: int, _total: int) -> None:
        raise ProgressCallbackError("progress receiver stopped")

    with pytest.raises(ProgressCallbackError, match="progress receiver stopped"):
        download_update_assets(
            plan,
            fail_progress,
            download_dir=tmp_path,
            opener=lambda *_args, **_kwargs: _Response(content),
        )

    assert list(tmp_path.iterdir()) == []


def test_launch_update_installer_rejects_source_mode():
    with pytest.raises(UpdateServiceError, match="源码运行模式"):
        launch_update_installer((), "2.2.0", "2.2.1")


def test_launch_update_installer_passes_digest_and_uses_install_scoped_helper(
    tmp_path: Path,
    monkeypatch,
):
    install = tmp_path / "app"
    install.mkdir()
    updater_source = install / update_service.UPDATER_EXE_NAME
    updater_source.write_bytes(b"updater")
    executable = install / "app.exe"
    executable.write_bytes(b"app")
    patch = tmp_path / "update.zip"
    patch.write_bytes(_patch_bytes("2.2.0", "2.2.1"))
    digest = hashlib.sha256(patch.read_bytes()).hexdigest()
    app_data = tmp_path / "local-data"
    calls: list[tuple[list[str], dict[str, object]]] = []

    class FakeProcess:
        pid = 123

        @staticmethod
        def poll():
            return None

    def writable(*parts: str) -> Path:
        path = app_data.joinpath(*parts)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def popen(command, **kwargs):
        calls.append((command, kwargs))
        return FakeProcess()

    monkeypatch.setattr(update_service.sys, "frozen", True, raising=False)
    monkeypatch.setattr(update_service.sys, "executable", str(executable))
    monkeypatch.setattr(update_service, "app_base_dir", lambda: install)
    monkeypatch.setattr(update_service, "app_path", lambda _name: updater_source)
    monkeypatch.setattr(update_service, "writable_app_data_dir", writable)
    monkeypatch.setattr(update_service.subprocess, "Popen", popen)

    launched = launch_update_installer(
        (patch,),
        "2.2.0",
        "2.2.1",
        (f"sha256:{digest}",),
    )

    assert len(calls) == 1
    command, kwargs = calls[0]
    helper = Path(command[0])
    assert helper.parent == install / ".auto-bdsp-rng-updater"
    assert helper.read_bytes() == b"updater"
    assert command[command.index("--patch-sha256") + 1] == digest
    approval_file = Path(command[command.index("--approval-file") + 1])
    approval_token = command[command.index("--approval-token") + 1]
    assert approval_file == launched.approval_file
    assert approval_token == launched.approval_token
    assert approval_file.read_text(encoding="ascii").strip() == f"pending:{approval_token}"
    launched.approve()
    assert approval_file.read_text(encoding="ascii").strip() == f"approved:{approval_token}"
    assert kwargs["cwd"] == install


def test_cleanup_stale_helpers_removes_pending_and_consumed_approval_tokens(
    tmp_path: Path,
    monkeypatch,
):
    helper_dir = tmp_path / ".auto-bdsp-rng-updater"
    helper_dir.mkdir()
    pending = helper_dir / ".approve-pending.token"
    consumed = helper_dir / ".approve-id.consumed-4321-unique.token"
    pending.write_text("pending:token\n", encoding="ascii")
    consumed.write_text("approved:token\n", encoding="ascii")
    monkeypatch.setattr(update_service.time, "time", lambda: 10**12)

    update_service._cleanup_stale_helpers(helper_dir)

    assert not pending.exists()
    assert not consumed.exists()
