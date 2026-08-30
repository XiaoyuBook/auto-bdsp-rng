from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

import auto_bdsp_rng.update_core as update_core

from auto_bdsp_rng.update_core import (
    DeferredUpdate,
    UpdatePackageError,
    apply_update_packages,
    commit_update_transaction,
    has_uncommitted_update_transaction,
    load_patch_manifest,
    migrate_legacy_internal_scripts,
    recover_interrupted_update_transactions,
    rollback_update_transaction,
)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_patch(
    path: Path,
    *,
    from_version: str = "2.2.0",
    to_version: str = "2.2.1",
    files: list[tuple[str, bytes, bytes | None, bool]] | None = None,
    remove: list[tuple[str, bytes, bool]] | None = None,
    manifest_changes: dict[str, object] | None = None,
) -> Path:
    files = files or []
    remove = remove or []
    manifest: dict[str, object] = {
        "schema_version": 1,
        "application": "auto-bdsp-rng",
        "platform": "windows-x64",
        "from_version": from_version,
        "to_version": to_version,
        "files": [
            {
                "path": relative,
                "size": len(content),
                "sha256": _digest(content),
                "previous_sha256": None if previous is None else _digest(previous),
                "preserve_if_modified": preserve,
            }
            for relative, content, previous, preserve in files
        ],
        "remove": [
            {
                "path": relative,
                "sha256": _digest(previous),
                "preserve_if_modified": preserve,
            }
            for relative, previous, preserve in remove
        ],
    }
    if manifest_changes:
        manifest.update(manifest_changes)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("update.json", json.dumps(manifest, ensure_ascii=False))
        for relative, content, _previous, _preserve in files:
            archive.writestr(f"payload/{relative}", content)
    return path


def test_apply_update_replaces_adds_removes_and_preserves_unknown_files(tmp_path: Path):
    install = tmp_path / "app"
    install.mkdir()
    (install / "app.exe").write_bytes(b"old exe")
    (install / "obsolete.dll").write_bytes(b"old dll")
    (install / "user-note.txt").write_text("keep", encoding="utf-8")
    patch = _write_patch(
        tmp_path / "update.zip",
        files=[
            ("app.exe", b"new exe", b"old exe", False),
            ("_internal/new.dll", b"new dll", None, False),
        ],
        remove=[("obsolete.dll", b"old dll", False)],
    )

    version = apply_update_packages([patch], install_dir=install, expected_version="2.2.0")

    assert version == "2.2.1"
    assert (install / "app.exe").read_bytes() == b"new exe"
    assert (install / "_internal/new.dll").read_bytes() == b"new dll"
    assert not (install / "obsolete.dll").exists()
    assert (install / "user-note.txt").read_text(encoding="utf-8") == "keep"
    assert list(install.glob(".auto-bdsp-update-*")) == []


def test_separate_updates_can_replace_a_removed_file_parent_directory_with_a_file(tmp_path: Path):
    install = tmp_path / "app"
    nested = install / "runtime" / "old.dll"
    nested.parent.mkdir(parents=True)
    nested.write_bytes(b"old dll")
    remove_patch = _write_patch(
        tmp_path / "remove.zip",
        files=[],
        remove=[("runtime/old.dll", b"old dll", False)],
    )

    version = apply_update_packages(
        [remove_patch],
        install_dir=install,
        expected_version="2.2.0",
    )

    assert version == "2.2.1"
    assert not (install / "runtime").exists()

    add_patch = _write_patch(
        tmp_path / "add.zip",
        from_version="2.2.1",
        to_version="2.2.2",
        files=[("runtime", b"new file", None, False)],
    )

    version = apply_update_packages(
        [add_patch],
        install_dir=install,
        expected_version="2.2.1",
    )

    assert version == "2.2.2"
    assert (install / "runtime").read_bytes() == b"new file"


def test_single_patch_chain_can_replace_a_removed_file_parent_directory_with_a_file(tmp_path: Path):
    install = tmp_path / "app"
    nested = install / "runtime" / "old.dll"
    nested.parent.mkdir(parents=True)
    nested.write_bytes(b"old dll")
    remove_patch = _write_patch(
        tmp_path / "remove.zip",
        files=[],
        remove=[("runtime/old.dll", b"old dll", False)],
    )
    add_patch = _write_patch(
        tmp_path / "add.zip",
        from_version="2.2.1",
        to_version="2.2.2",
        files=[("runtime", b"new file", None, False)],
    )

    version = apply_update_packages(
        [remove_patch, add_patch],
        install_dir=install,
        expected_version="2.2.0",
    )

    assert version == "2.2.2"
    assert (install / "runtime").read_bytes() == b"new file"


def test_single_patch_chain_can_replace_a_removed_file_with_a_directory(tmp_path: Path):
    install = tmp_path / "app"
    install.mkdir()
    runtime = install / "runtime"
    runtime.write_bytes(b"old file")
    remove_patch = _write_patch(
        tmp_path / "remove.zip",
        files=[],
        remove=[("runtime", b"old file", False)],
    )
    add_patch = _write_patch(
        tmp_path / "add.zip",
        from_version="2.2.1",
        to_version="2.2.2",
        files=[("runtime/new.dll", b"new dll", None, False)],
    )

    version = apply_update_packages(
        [remove_patch, add_patch],
        install_dir=install,
        expected_version="2.2.0",
    )

    assert version == "2.2.2"
    assert (runtime / "new.dll").read_bytes() == b"new dll"


def test_remove_keeps_nonempty_parent_directories(tmp_path: Path):
    install = tmp_path / "app"
    runtime = install / "runtime"
    runtime.mkdir(parents=True)
    (runtime / "old.dll").write_bytes(b"old dll")
    (runtime / "user.dat").write_bytes(b"user data")
    patch = _write_patch(
        tmp_path / "remove.zip",
        files=[],
        remove=[("runtime/old.dll", b"old dll", False)],
    )

    version = apply_update_packages([patch], install_dir=install, expected_version="2.2.0")

    assert version == "2.2.1"
    assert runtime.is_dir()
    assert (runtime / "user.dat").read_bytes() == b"user data"


def test_parent_directory_cleanup_failure_does_not_fail_update(tmp_path: Path, monkeypatch):
    install = tmp_path / "app"
    runtime = install / "runtime"
    runtime.mkdir(parents=True)
    old_file = runtime / "old.dll"
    old_file.write_bytes(b"old dll")
    patch = _write_patch(
        tmp_path / "remove.zip",
        files=[],
        remove=[("runtime/old.dll", b"old dll", False)],
    )
    original_rmdir = Path.rmdir

    def fail_runtime_rmdir(path: Path) -> None:
        if path == runtime:
            raise PermissionError("directory metadata is locked")
        original_rmdir(path)

    monkeypatch.setattr(Path, "rmdir", fail_runtime_rmdir)

    version = apply_update_packages([patch], install_dir=install, expected_version="2.2.0")

    assert version == "2.2.1"
    assert not old_file.exists()
    assert runtime.is_dir()


def test_rollback_removes_empty_parent_directories_created_for_new_files(tmp_path: Path):
    install = tmp_path / "app"
    install.mkdir()
    conflict = install / "conflict.bin"
    conflict.write_bytes(b"locally changed")
    patch = _write_patch(
        tmp_path / "update.zip",
        files=[
            ("runtime/new.dll", b"new dll", None, False),
            ("conflict.bin", b"new conflict", b"expected old", False),
        ],
    )

    with pytest.raises(UpdatePackageError, match="本地程序文件已被修改"):
        apply_update_packages([patch], install_dir=install, expected_version="2.2.0")

    assert conflict.read_bytes() == b"locally changed"
    assert not (install / "runtime").exists()


def test_apply_update_keeps_modified_mutable_file_and_writes_new_copy(tmp_path: Path):
    install = tmp_path / "app"
    script = install / "script" / "run.txt"
    script.parent.mkdir(parents=True)
    script.write_bytes(b"user edit")
    patch = _write_patch(
        tmp_path / "update.zip",
        files=[("script/run.txt", b"new default", b"old default", True)],
    )

    apply_update_packages([patch], install_dir=install, expected_version="2.2.0")

    assert script.read_bytes() == b"user edit"
    assert (script.parent / "run.txt.new-v2.2.1").read_bytes() == b"new default"


def test_migrate_legacy_internal_scripts_removes_duplicates_and_backs_up_differences(
    tmp_path: Path,
):
    install = tmp_path / "app"
    canonical = install / "script"
    legacy = install / "_internal" / "script"
    canonical.mkdir(parents=True)
    (legacy / "nested").mkdir(parents=True)
    (install / "_internal" / "core.dll").write_bytes(b"runtime")
    (canonical / "same.txt").write_bytes(b"same")
    (legacy / "same.txt").write_bytes(b"same")
    (canonical / "edited.txt").write_bytes(b"canonical")
    (legacy / "edited.txt").write_bytes(b"legacy user edit")
    (legacy / "nested" / "only-internal.txt").write_bytes(b"only internal")
    backup_root = canonical / ".legacy-internal-backup"
    backup_root.mkdir()
    (backup_root / "edited.txt").write_bytes(b"existing backup")
    messages: list[str] = []

    migrated = migrate_legacy_internal_scripts(install, log=messages.append)

    assert not legacy.exists()
    assert (install / "_internal" / "core.dll").read_bytes() == b"runtime"
    assert (canonical / "same.txt").read_bytes() == b"same"
    assert (canonical / "edited.txt").read_bytes() == b"canonical"
    assert (backup_root / "edited.txt").read_bytes() == b"existing backup"
    assert (backup_root / "edited.txt.1").read_bytes() == b"legacy user edit"
    assert (backup_root / "nested" / "only-internal.txt").read_bytes() == b"only internal"
    assert migrated == (
        backup_root / "edited.txt.1",
        backup_root / "nested" / "only-internal.txt",
    )
    assert any("已删除旧版内部重复脚本" in message for message in messages)
    assert len([message for message in messages if "已备份并删除旧版内部脚本" in message]) == 2
    assert migrate_legacy_internal_scripts(install) == ()


def test_migrate_legacy_internal_scripts_keeps_source_when_backup_fails(
    tmp_path: Path,
    monkeypatch,
):
    install = tmp_path / "app"
    source = install / "_internal" / "script" / "user.txt"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"user content")

    original_fsync = update_core.os.fsync

    def fail_fsync(_file_descriptor: int) -> None:
        raise OSError("disk failure")

    monkeypatch.setattr(update_core.os, "fsync", fail_fsync)

    with pytest.raises(UpdatePackageError, match="迁移旧版内部脚本失败"):
        migrate_legacy_internal_scripts(install)

    backup_root = install / "script" / ".legacy-internal-backup"
    staging_dirs = list(
        backup_root.glob(f"{update_core.LEGACY_INTERNAL_SCRIPT_STAGING_PREFIX}*")
    )
    assert not source.exists()
    assert len(staging_dirs) == 1
    assert (staging_dirs[0] / "user.txt").read_bytes() == b"user content"
    assert not (backup_root / "user.txt").exists()

    monkeypatch.setattr(update_core.os, "fsync", original_fsync)
    migrated = migrate_legacy_internal_scripts(install)

    assert migrated == (backup_root / "user.txt",)
    assert (backup_root / "user.txt").read_bytes() == b"user content"
    assert not staging_dirs[0].exists()


def test_migrate_legacy_internal_scripts_recovers_crash_staging(tmp_path: Path):
    install = tmp_path / "app"
    canonical = install / "script"
    backup_root = canonical / ".legacy-internal-backup"
    staging = backup_root / (
        f"{update_core.LEGACY_INTERNAL_SCRIPT_STAGING_PREFIX}{'0' * 32}"
    )
    staging.mkdir(parents=True)
    canonical.mkdir(parents=True, exist_ok=True)
    (canonical / "duplicate.txt").write_bytes(b"same")
    (staging / "duplicate.txt").write_bytes(b"same")
    (staging / "recovered.txt").write_bytes(b"recovered user content")

    migrated = migrate_legacy_internal_scripts(install)

    assert migrated == (backup_root / "recovered.txt",)
    assert (backup_root / "recovered.txt").read_bytes() == b"recovered user content"
    assert not staging.exists()


def test_migrate_legacy_internal_scripts_does_not_delete_recreated_source_tree(
    tmp_path: Path,
    monkeypatch,
):
    install = tmp_path / "app"
    canonical = install / "script"
    legacy = install / "_internal" / "script"
    canonical.mkdir(parents=True)
    legacy.mkdir(parents=True)
    (canonical / "old.txt").write_bytes(b"old content")
    (legacy / "old.txt").write_bytes(b"old content")
    original_rename = update_core.os.rename

    def rename_and_recreate(source, destination) -> None:
        original_rename(source, destination)
        if Path(source) == legacy:
            legacy.mkdir(parents=True)
            (legacy / "new.txt").write_bytes(b"concurrent content")

    monkeypatch.setattr(update_core.os, "rename", rename_and_recreate)

    assert migrate_legacy_internal_scripts(install) == ()

    assert (legacy / "new.txt").read_bytes() == b"concurrent content"
    assert not (legacy / "old.txt").exists()
    backup_root = canonical / ".legacy-internal-backup"
    assert list(backup_root.glob(f"{update_core.LEGACY_INTERNAL_SCRIPT_STAGING_PREFIX}*")) == []


def test_uncommitted_update_transaction_blocks_nontransactional_migration(
    tmp_path: Path,
    monkeypatch,
):
    install = tmp_path / "app"
    target = install / "app.exe"
    install.mkdir()
    target.write_bytes(b"old")
    patch = _write_patch(
        tmp_path / "update.zip",
        files=[("app.exe", b"new", b"old", False)],
    )

    result = apply_update_packages(
        [patch],
        install_dir=install,
        expected_version="2.2.0",
        defer_commit=True,
    )

    assert isinstance(result, DeferredUpdate)
    assert has_uncommitted_update_transaction(install) is True

    def fail_remove_transaction_dir(_work_dir: Path) -> None:
        raise UpdatePackageError("cleanup locked")

    monkeypatch.setattr(update_core, "_remove_transaction_dir", fail_remove_transaction_dir)
    commit_update_transaction(result.transaction_dir, install_dir=install)

    assert result.transaction_dir.exists()
    assert has_uncommitted_update_transaction(install) is False


@pytest.mark.parametrize("failure_point", ["resolve", "glob", "resolve_transaction"])
def test_uncommitted_update_transaction_check_fails_closed_on_filesystem_errors(
    tmp_path: Path,
    monkeypatch,
    failure_point: str,
):
    install = tmp_path / "app"
    install.mkdir()
    if failure_point == "resolve":
        original_resolve = Path.resolve

        def fail_resolve(path: Path, *args, **kwargs):
            if path == install:
                raise OSError("resolve failed")
            return original_resolve(path, *args, **kwargs)

        monkeypatch.setattr(Path, "resolve", fail_resolve)
    elif failure_point == "glob":
        original_glob = Path.glob

        def fail_glob(path: Path, pattern: str):
            if path == install:
                raise OSError("glob failed")
            return original_glob(path, pattern)

        monkeypatch.setattr(Path, "glob", fail_glob)
    else:
        (install / f"{update_core.TRANSACTION_DIR_PREFIX}broken").mkdir()

        def fail_resolve_transaction(_candidate: Path, _install_dir: Path) -> Path:
            raise OSError("transaction resolve failed")

        monkeypatch.setattr(
            update_core,
            "_resolve_transaction_dir",
            fail_resolve_transaction,
        )

    assert has_uncommitted_update_transaction(install) is True


def test_preserved_update_never_overwrites_an_existing_different_sidecar(tmp_path: Path):
    install = tmp_path / "app"
    script = install / "script" / "run.txt"
    script.parent.mkdir(parents=True)
    script.write_bytes(b"user edit")
    existing_sidecar = script.with_name("run.txt.new-v2.2.1")
    existing_sidecar.write_bytes(b"user sidecar")
    patch = _write_patch(
        tmp_path / "update.zip",
        files=[("script/run.txt", b"new default", b"old default", True)],
    )

    apply_update_packages([patch], install_dir=install, expected_version="2.2.0")

    assert script.read_bytes() == b"user edit"
    assert existing_sidecar.read_bytes() == b"user sidecar"
    assert script.with_name("run.txt.new-v2.2.1.1").read_bytes() == b"new default"


def test_preserved_update_reuses_an_existing_matching_sidecar(tmp_path: Path):
    install = tmp_path / "app"
    script = install / "script" / "run.txt"
    script.parent.mkdir(parents=True)
    script.write_bytes(b"user edit")
    existing_sidecar = script.with_name("run.txt.new-v2.2.1")
    existing_sidecar.write_bytes(b"new default")
    patch = _write_patch(
        tmp_path / "update.zip",
        files=[("script/run.txt", b"new default", b"old default", True)],
    )

    apply_update_packages([patch], install_dir=install, expected_version="2.2.0")

    assert script.read_bytes() == b"user edit"
    assert existing_sidecar.read_bytes() == b"new default"
    assert not script.with_name("run.txt.new-v2.2.1.1").exists()


@pytest.mark.parametrize("occupied_kind", ["directory", "symlink"])
def test_preserved_update_skips_non_file_sidecar_paths(
    tmp_path: Path,
    occupied_kind: str,
):
    install = tmp_path / "app"
    script = install / "script" / "run.txt"
    script.parent.mkdir(parents=True)
    script.write_bytes(b"user edit")
    occupied_sidecar = script.with_name("run.txt.new-v2.2.1")
    if occupied_kind == "directory":
        occupied_sidecar.mkdir()
    else:
        symlink_target = tmp_path / "user-sidecar.txt"
        symlink_target.write_bytes(b"user sidecar")
        try:
            occupied_sidecar.symlink_to(symlink_target)
        except OSError as exc:
            pytest.skip(f"symbolic links are not available: {exc}")
    patch = _write_patch(
        tmp_path / "update.zip",
        files=[("script/run.txt", b"new default", b"old default", True)],
    )

    apply_update_packages([patch], install_dir=install, expected_version="2.2.0")

    assert script.read_bytes() == b"user edit"
    if occupied_kind == "directory":
        assert occupied_sidecar.is_dir()
    else:
        assert occupied_sidecar.is_symlink()
        assert occupied_sidecar.read_bytes() == b"user sidecar"
    assert script.with_name("run.txt.new-v2.2.1.1").read_bytes() == b"new default"


def test_apply_update_rolls_back_earlier_replacement_on_later_conflict(tmp_path: Path):
    install = tmp_path / "app"
    install.mkdir()
    first = install / "first.bin"
    conflict = install / "conflict.bin"
    first.write_bytes(b"first old")
    conflict.write_bytes(b"locally changed")
    patch = _write_patch(
        tmp_path / "update.zip",
        files=[
            ("first.bin", b"first new", b"first old", False),
            ("conflict.bin", b"conflict new", b"expected old", False),
        ],
    )

    with pytest.raises(UpdatePackageError, match="本地程序文件已被修改"):
        apply_update_packages([patch], install_dir=install, expected_version="2.2.0")

    assert first.read_bytes() == b"first old"
    assert conflict.read_bytes() == b"locally changed"


def test_apply_update_rolls_back_when_failure_logging_raises(tmp_path: Path):
    install = tmp_path / "app"
    install.mkdir()
    first = install / "first.bin"
    conflict = install / "conflict.bin"
    first.write_bytes(b"first old")
    conflict.write_bytes(b"locally changed")
    patch = _write_patch(
        tmp_path / "update.zip",
        files=[
            ("first.bin", b"first new", b"first old", False),
            ("conflict.bin", b"conflict new", b"expected old", False),
        ],
    )

    def failing_logger(message: str) -> None:
        if message.startswith("升级失败，正在回滚"):
            raise OSError("log destination unavailable")

    with pytest.raises(UpdatePackageError, match="本地程序文件已被修改"):
        apply_update_packages(
            [patch],
            install_dir=install,
            expected_version="2.2.0",
            log=failing_logger,
        )

    assert first.read_bytes() == b"first old"
    assert conflict.read_bytes() == b"locally changed"
    assert list(install.glob(".auto-bdsp-update-*")) == []


def test_apply_update_rejects_patch_when_private_copy_digest_mismatches(tmp_path: Path):
    install = tmp_path / "app"
    install.mkdir()
    target = install / "app.exe"
    target.write_bytes(b"old")
    patch = _write_patch(
        tmp_path / "update.zip",
        files=[("app.exe", b"new", b"old", False)],
    )

    with pytest.raises(UpdatePackageError, match="外层 SHA-256 校验失败"):
        apply_update_packages(
            [patch],
            install_dir=install,
            expected_version="2.2.0",
            expected_patch_sha256=["0" * 64],
        )

    assert target.read_bytes() == b"old"
    assert list(install.glob(".auto-bdsp-update-*")) == []
    assert list(install.glob(".auto-bdsp-cleanup-*")) == []


def test_apply_update_accepts_a_contiguous_patch_chain(tmp_path: Path):
    install = tmp_path / "app"
    install.mkdir()
    target = install / "app.exe"
    target.write_bytes(b"v1")
    first = _write_patch(
        tmp_path / "first.zip",
        files=[("app.exe", b"v2", b"v1", False)],
    )
    second = _write_patch(
        tmp_path / "second.zip",
        from_version="2.2.1",
        to_version="2.2.2",
        files=[("app.exe", b"v3", b"v2", False)],
    )

    version = apply_update_packages([first, second], install_dir=install, expected_version="2.2.0")

    assert version == "2.2.2"
    assert target.read_bytes() == b"v3"


def test_deferred_update_keeps_backup_until_commit(tmp_path: Path):
    install = tmp_path / "app"
    install.mkdir()
    target = install / "app.exe"
    target.write_bytes(b"old")
    patch = _write_patch(
        tmp_path / "update.zip",
        files=[("app.exe", b"new", b"old", False)],
    )

    result = apply_update_packages(
        [patch],
        install_dir=install,
        expected_version="2.2.0",
        defer_commit=True,
    )

    assert isinstance(result, DeferredUpdate)
    assert str(result) == "2.2.1"
    assert target.read_bytes() == b"new"
    assert (result.transaction_dir / "backup" / "app.exe").read_bytes() == b"old"

    commit_update_transaction(result.transaction_dir, install_dir=install)

    assert target.read_bytes() == b"new"
    assert not result.transaction_dir.exists()


def test_next_update_recovers_interrupted_deferred_transaction(tmp_path: Path):
    install = tmp_path / "app"
    install.mkdir()
    target = install / "app.exe"
    target.write_bytes(b"old")
    patch = _write_patch(
        tmp_path / "update.zip",
        files=[
            ("app.exe", b"new", b"old", False),
            ("_internal/new.dll", b"new dll", None, False),
        ],
    )
    result = apply_update_packages(
        [patch],
        install_dir=install,
        expected_version="2.2.0",
        defer_commit=True,
    )
    assert isinstance(result, DeferredUpdate)

    recover_interrupted_update_transactions(install)

    assert target.read_bytes() == b"old"
    assert not (install / "_internal/new.dll").exists()
    assert not result.transaction_dir.exists()


def test_apply_stops_after_recovering_an_interrupted_prior_update(
    tmp_path: Path,
    monkeypatch,
):
    install = tmp_path / "app"
    install.mkdir()
    target = install / "app.exe"
    target.write_bytes(b"v1")
    first = _write_patch(
        tmp_path / "first.zip",
        files=[("app.exe", b"v2", b"v1", False)],
    )
    interrupted = apply_update_packages(
        [first],
        install_dir=install,
        expected_version="2.2.0",
        defer_commit=True,
    )
    assert isinstance(interrupted, DeferredUpdate)
    assert target.read_bytes() == b"v2"
    future = _write_patch(
        tmp_path / "future.zip",
        from_version="2.2.1",
        to_version="2.2.2",
        files=[("future.dll", b"future", None, False)],
    )
    copied: list[Path] = []
    original_copy = update_core._copy_verified_patch

    def track_copy(source: Path, destination: Path, expected_sha256: str | None) -> None:
        copied.append(source)
        original_copy(source, destination, expected_sha256)

    monkeypatch.setattr(update_core, "_copy_verified_patch", track_copy)

    with pytest.raises(UpdatePackageError, match="已恢复上次中断升级") as raised:
        apply_update_packages(
            [future],
            install_dir=install,
            expected_version="2.2.1",
        )

    assert getattr(raised.value, "rollback_completed", False) is True
    assert copied == []
    assert target.read_bytes() == b"v1"
    assert not (install / "future.dll").exists()
    assert not interrupted.transaction_dir.exists()


def test_recovery_journal_failure_still_marks_completed_file_rollback(
    tmp_path: Path,
    monkeypatch,
):
    install = tmp_path / "app"
    install.mkdir()
    target = install / "app.exe"
    target.write_bytes(b"v1")
    first = _write_patch(
        tmp_path / "first.zip",
        files=[("app.exe", b"v2", b"v1", False)],
    )
    interrupted = apply_update_packages(
        [first],
        install_dir=install,
        expected_version="2.2.0",
        defer_commit=True,
    )
    assert isinstance(interrupted, DeferredUpdate)
    original_write_journal = update_core._write_transaction_journal

    def fail_rolled_back_journal(*args, state: str, **kwargs) -> None:
        if state == "rolled_back":
            raise UpdatePackageError("journal unavailable")
        original_write_journal(*args, state=state, **kwargs)

    monkeypatch.setattr(update_core, "_write_transaction_journal", fail_rolled_back_journal)
    future = _write_patch(
        tmp_path / "future.zip",
        from_version="2.2.1",
        to_version="2.2.2",
        files=[("future.dll", b"future", None, False)],
    )

    with pytest.raises(UpdatePackageError, match="journal unavailable") as raised:
        apply_update_packages(
            [future],
            install_dir=install,
            expected_version="2.2.1",
        )

    assert getattr(raised.value, "rollback_completed", False) is True
    assert target.read_bytes() == b"v1"
    assert interrupted.transaction_dir.exists()
    assert not (install / "future.dll").exists()


def test_completed_rollback_is_not_repeated_when_cleanup_initially_fails(
    tmp_path: Path,
    monkeypatch,
):
    install = tmp_path / "app"
    install.mkdir()
    target = install / "app.exe"
    target.write_bytes(b"old")
    patch = _write_patch(
        tmp_path / "update.zip",
        files=[("app.exe", b"new", b"old", False)],
    )
    result = apply_update_packages(
        [patch],
        install_dir=install,
        expected_version="2.2.0",
        defer_commit=True,
    )
    assert isinstance(result, DeferredUpdate)
    original_remove = update_core._remove_transaction_dir

    def fail_cleanup(_work_dir: Path) -> None:
        raise UpdatePackageError("cleanup locked")

    monkeypatch.setattr(update_core, "_remove_transaction_dir", fail_cleanup)
    rollback_update_transaction(result.transaction_dir, install_dir=install)

    journal = json.loads((result.transaction_dir / "transaction.json").read_text(encoding="utf-8"))
    assert journal["state"] == "rolled_back"
    target.write_bytes(b"user data after rollback")

    monkeypatch.setattr(update_core, "_remove_transaction_dir", original_remove)
    recover_interrupted_update_transactions(install)

    assert target.read_bytes() == b"user data after rollback"
    assert not result.transaction_dir.exists()


def test_finished_transaction_is_quarantined_before_recursive_cleanup(
    tmp_path: Path,
    monkeypatch,
):
    install = tmp_path / "app"
    install.mkdir()
    target = install / "app.exe"
    target.write_bytes(b"old")
    patch = _write_patch(
        tmp_path / "update.zip",
        files=[("app.exe", b"new", b"old", False)],
    )
    result = apply_update_packages(
        [patch],
        install_dir=install,
        expected_version="2.2.0",
        defer_commit=True,
    )
    assert isinstance(result, DeferredUpdate)
    original_rmtree = update_core.shutil.rmtree
    monkeypatch.setattr(
        update_core.shutil,
        "rmtree",
        lambda _path: (_ for _ in ()).throw(PermissionError("backup locked")),
    )

    rollback_update_transaction(result.transaction_dir, install_dir=install)

    assert not result.transaction_dir.exists()
    cleanup_dirs = list(install.glob(f"{update_core.TRANSACTION_CLEANUP_DIR_PREFIX}*"))
    assert len(cleanup_dirs) == 1
    target.write_bytes(b"user data after rollback")

    recover_interrupted_update_transactions(install)

    assert target.read_bytes() == b"user data after rollback"
    assert cleanup_dirs[0].exists()

    monkeypatch.setattr(update_core.shutil, "rmtree", original_rmtree)
    recover_interrupted_update_transactions(install)

    assert target.read_bytes() == b"user data after rollback"
    assert not cleanup_dirs[0].exists()


def test_disk_space_check_includes_backup_and_rollback_copy(tmp_path: Path, monkeypatch):
    install = tmp_path / "app"
    install.mkdir()
    old_content = b"x" * 100
    (install / "app.exe").write_bytes(old_content)
    patch = _write_patch(
        tmp_path / "update.zip",
        files=[("app.exe", b"y", old_content, False)],
    )
    free_space = 64 * 1024 * 1024 + 150
    monkeypatch.setattr(
        update_core.shutil,
        "disk_usage",
        lambda _path: type("Usage", (), {"free": free_space})(),
    )

    with pytest.raises(UpdatePackageError, match="磁盘空间不足"):
        apply_update_packages([patch], install_dir=install, expected_version="2.2.0")

    assert (install / "app.exe").read_bytes() == old_content


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "../outside.exe",
        "/absolute.exe",
        "C:/drive.exe",
        "dir\\file.exe",
        "CON.txt",
        "file. ",
        "line\nbreak.exe",
    ],
)
def test_patch_manifest_rejects_unsafe_windows_paths(tmp_path: Path, unsafe_path: str):
    patch = _write_patch(tmp_path / "unsafe.zip", files=[(unsafe_path, b"x", None, False)])

    with pytest.raises(UpdatePackageError):
        load_patch_manifest(patch)


def test_patch_manifest_rejects_case_insensitive_path_collision(tmp_path: Path):
    patch = _write_patch(
        tmp_path / "collision.zip",
        files=[("Dir/File.dll", b"a", None, False), ("dir/file.dll", b"b", None, False)],
    )

    with pytest.raises(UpdatePackageError, match="重复|大小写冲突"):
        load_patch_manifest(patch)


def test_patch_manifest_rejects_file_directory_and_parent_case_collisions(tmp_path: Path):
    file_directory = _write_patch(
        tmp_path / "file-directory.zip",
        files=[("runtime", b"file", None, False), ("runtime/library.dll", b"child", None, False)],
    )
    with pytest.raises(UpdatePackageError, match="同时是文件和目录"):
        load_patch_manifest(file_directory)

    parent_case = _write_patch(
        tmp_path / "parent-case.zip",
        files=[("Folder/one.dll", b"one", None, False), ("folder/two.dll", b"two", None, False)],
    )
    with pytest.raises(UpdatePackageError, match="大小写冲突"):
        load_patch_manifest(parent_case)


def test_patch_manifest_rejects_payload_hash_mismatch(tmp_path: Path):
    patch = _write_patch(
        tmp_path / "bad-hash.zip",
        files=[("app.exe", b"good", None, False)],
        manifest_changes={
            "files": [
                {
                    "path": "app.exe",
                    "size": 4,
                    "sha256": "0" * 64,
                    "previous_sha256": None,
                    "preserve_if_modified": False,
                }
            ]
        },
    )

    with pytest.raises(UpdatePackageError, match="校验失败"):
        load_patch_manifest(patch)
