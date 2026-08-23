from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import zipfile
from pathlib import Path

import pytest

from auto_bdsp_rng.update_core import apply_update_packages


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "build_update_package.py"


def _load_builder_module():
    spec = importlib.util.spec_from_file_location("build_update_package", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = _load_builder_module()


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _entry(path: str, value: bytes, *, preserve: bool = False) -> dict[str, object]:
    return {
        "path": path,
        "size": len(value),
        "sha256": _sha256(value),
        "preserve_if_modified": preserve,
    }


def _manifest(version: str, files: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "application": "auto-bdsp-rng",
        "platform": "windows-x64",
        "version": version,
        "files": sorted(files, key=lambda entry: str(entry["path"]).casefold()),
    }


def test_build_manifest_hashes_files_and_marks_user_editable_paths(tmp_path: Path):
    dist = tmp_path / "dist"
    (dist / "_internal").mkdir(parents=True)
    (dist / "script").mkdir()
    (dist / "logs").mkdir()
    custom = dist / "third_party" / "Project_Xs_CHN" / "images" / "custom"
    configs = dist / "third_party" / "Project_Xs_CHN" / "configs"
    custom.mkdir(parents=True)
    configs.mkdir(parents=True)
    (dist / "_internal" / "core.bin").write_bytes(b"core")
    (dist / "script" / "用户脚本.txt").write_text("脚本\n", encoding="utf-8")
    (dist / "logs" / "old.log").write_bytes(b"log")
    (custom / "eye.png").write_bytes(b"image")
    (configs / "config.json").write_bytes(b"{}")

    manifest = builder.build_manifest(dist, "2.3.0")

    assert set(manifest) == {"schema_version", "application", "platform", "version", "files"}
    assert manifest["version"] == "2.3.0"
    files = {entry["path"]: entry for entry in manifest["files"]}
    assert files["_internal/core.bin"] == _entry("_internal/core.bin", b"core")
    assert files["script/用户脚本.txt"]["preserve_if_modified"] is True
    assert files["logs/old.log"]["preserve_if_modified"] is True
    assert files["third_party/Project_Xs_CHN/images/custom/eye.png"]["preserve_if_modified"] is True
    assert files["third_party/Project_Xs_CHN/configs/config.json"]["preserve_if_modified"] is True

    output = tmp_path / "manifest.json"
    builder.write_manifest(manifest, output)
    assert "用户脚本.txt".encode("utf-8") in output.read_bytes()


def test_build_update_artifacts_without_previous_manifest_is_bootstrap_only(tmp_path: Path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "app.exe").write_bytes(b"application")

    manifest_path, patch_path = builder.build_update_artifacts(dist, tmp_path / "release", "1.0.0")

    assert manifest_path.name == "auto-bdsp-rng-v1.0.0-windows-x64.manifest.json"
    assert patch_path is None
    raw = manifest_path.read_bytes()
    assert raw.endswith(b"\n")
    assert json.loads(raw.decode("utf-8"))["files"][0]["path"] == "app.exe"


def test_patch_contains_only_added_and_changed_payloads_plus_removals(tmp_path: Path):
    dist = tmp_path / "dist"
    (dist / "script").mkdir(parents=True)
    (dist / "app.exe").write_bytes(b"new app")
    (dist / "same.dll").write_bytes(b"same")
    (dist / "新增.dll").write_bytes(b"new")
    (dist / "script" / "default.txt").write_bytes(b"new default")
    previous = _manifest(
        "1.0.0",
        [
            _entry("app.exe", b"old app"),
            _entry("same.dll", b"same"),
            _entry("old.dll", b"old"),
            _entry("script/default.txt", b"old default", preserve=True),
            _entry("script/removed.txt", b"user default", preserve=True),
        ],
    )
    previous_path = tmp_path / "previous.manifest.json"
    previous_path.write_text(json.dumps(previous), encoding="utf-8")

    manifest_path, patch_path = builder.build_update_artifacts(
        dist,
        tmp_path / "release",
        "1.1.0",
        previous_manifest_path=previous_path,
    )

    assert manifest_path.exists()
    assert patch_path is not None
    assert patch_path.name == "auto-bdsp-rng-v1.0.0-to-v1.1.0-windows-x64.update.zip"
    with zipfile.ZipFile(patch_path) as archive:
        assert set(archive.namelist()) == {
            "update.json",
            "payload/app.exe",
            "payload/新增.dll",
            "payload/script/default.txt",
        }
        raw_metadata = archive.read("update.json")
        assert "新增.dll".encode("utf-8") in raw_metadata
        metadata = json.loads(raw_metadata.decode("utf-8"))
        assert set(metadata) == {
            "schema_version",
            "application",
            "platform",
            "from_version",
            "to_version",
            "files",
            "remove",
        }
        changed = {entry["path"]: entry for entry in metadata["files"]}
        assert changed["app.exe"]["previous_sha256"] == _sha256(b"old app")
        assert changed["新增.dll"]["previous_sha256"] is None
        assert changed["script/default.txt"]["preserve_if_modified"] is True
        assert "same.dll" not in changed
        removed = {entry["path"]: entry for entry in metadata["remove"]}
        assert removed["old.dll"]["preserve_if_modified"] is False
        assert removed["script/removed.txt"]["preserve_if_modified"] is True
        assert archive.read("payload/app.exe") == b"new app"


def test_generated_patch_is_accepted_by_runtime_updater_and_preserves_user_edits(tmp_path: Path):
    previous_dist = tmp_path / "previous"
    (previous_dist / "script").mkdir(parents=True)
    (previous_dist / "app.exe").write_bytes(b"old app")
    (previous_dist / "obsolete.dll").write_bytes(b"obsolete")
    (previous_dist / "script" / "default.txt").write_bytes(b"old default")
    previous_manifest = builder.build_manifest(previous_dist, "2.2.0")
    previous_manifest_path = tmp_path / "previous.manifest.json"
    builder.write_manifest(previous_manifest, previous_manifest_path)

    current_dist = tmp_path / "current"
    (current_dist / "script").mkdir(parents=True)
    (current_dist / "_internal").mkdir()
    (current_dist / "app.exe").write_bytes(b"new app")
    (current_dist / "_internal" / "new.dll").write_bytes(b"new dll")
    (current_dist / "script" / "default.txt").write_bytes(b"new default")
    _manifest_path, patch_path = builder.build_update_artifacts(
        current_dist,
        tmp_path / "release",
        "2.2.1",
        previous_manifest_path=previous_manifest_path,
    )
    assert patch_path is not None

    install_dir = tmp_path / "installed"
    shutil.copytree(previous_dist, install_dir)
    (install_dir / "script" / "default.txt").write_bytes(b"user edit")

    final_version = apply_update_packages(
        [patch_path],
        install_dir=install_dir,
        expected_version="2.2.0",
    )

    assert final_version == "2.2.1"
    assert (install_dir / "app.exe").read_bytes() == b"new app"
    assert (install_dir / "_internal" / "new.dll").read_bytes() == b"new dll"
    assert not (install_dir / "obsolete.dll").exists()
    assert (install_dir / "script" / "default.txt").read_bytes() == b"user edit"
    assert (install_dir / "script" / "default.txt.new-v2.2.1").read_bytes() == b"new default"


def test_manifest_rejects_unsafe_paths_and_case_collisions():
    unsafe = _manifest("1.0.0", [_entry("../outside.exe", b"bad")])
    with pytest.raises(builder.UpdatePackageError, match="traversal"):
        builder.validate_manifest(unsafe)

    collision = _manifest(
        "1.0.0",
        [_entry("Folder/File.dll", b"one"), _entry("folder/file.dll", b"two")],
    )
    with pytest.raises(builder.UpdatePackageError, match="case-insensitive path collision"):
        builder.validate_manifest(collision)

    parent_collision = _manifest(
        "1.0.0",
        [_entry("Folder/one.dll", b"one"), _entry("folder/two.dll", b"two")],
    )
    with pytest.raises(builder.UpdatePackageError, match="case-insensitive path collision"):
        builder.validate_manifest(parent_collision)

    file_and_directory = _manifest(
        "1.0.0",
        [_entry("runtime", b"file"), _entry("runtime/library.dll", b"child")],
    )
    with pytest.raises(builder.UpdatePackageError, match="both a file and directory"):
        builder.validate_manifest(file_and_directory)


def test_manifest_rejects_incorrect_user_file_policy():
    manifest = _manifest("1.0.0", [_entry("script/default.txt", b"script", preserve=False)])

    with pytest.raises(builder.UpdatePackageError, match="Incorrect preserve_if_modified"):
        builder.validate_manifest(manifest)


@pytest.mark.parametrize(
    ("previous_path", "current_path"),
    [
        ("Folder/File.dll", "folder/file.dll"),
        ("Folder/old.dll", "folder/new.dll"),
    ],
)
def test_patch_rejects_case_only_path_changes(previous_path: str, current_path: str):
    previous = _manifest("1.0.0", [_entry(previous_path, b"old")])
    current = _manifest("1.1.0", [_entry(current_path, b"new")])

    with pytest.raises(builder.UpdatePackageError, match="Case-only path changes"):
        builder.build_patch_metadata(current, previous)


@pytest.mark.parametrize(
    ("previous_paths", "current_paths", "message"),
    [
        (["runtime"], ["runtime/library.dll"], "File-to-directory"),
        (["runtime/library.dll"], ["runtime"], "Directory-to-file"),
    ],
)
def test_patch_rejects_cross_version_file_directory_changes(
    previous_paths: list[str],
    current_paths: list[str],
    message: str,
):
    previous = _manifest("1.0.0", [_entry(path, b"old") for path in previous_paths])
    current = _manifest("1.1.0", [_entry(path, b"new") for path in current_paths])

    with pytest.raises(builder.UpdatePackageError, match=message):
        builder.build_patch_metadata(current, previous)


def test_release_workflow_builds_and_uploads_update_artifacts():
    workflow = (ROOT / ".github" / "workflows" / "build-windows-release.yml").read_text(
        encoding="utf-8"
    )

    assert "scripts\\build_update_package.py" in workflow
    assert "gh release download" in workflow
    assert "gh release view" in workflow
    assert 'throw "Could not list previous releases from GitHub."' in workflow
    assert "foreach ($candidate in $orderedCandidates)" in workflow
    assert "Smoke test frozen updater" in workflow
    assert "release/*.zip" in workflow
    assert "release/*.manifest.json" in workflow
