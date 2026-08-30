from __future__ import annotations

import sys
from pathlib import Path

from auto_bdsp_rng import resources


def test_resource_path_uses_project_root_in_source_checkout():
    root = Path(__file__).resolve().parents[1]

    assert resources.resource_path("script") == root / "script"


def test_resource_path_never_uses_internal_script_when_frozen(monkeypatch, tmp_path):
    exe = tmp_path / "auto-bdsp-rng.exe"
    internal = tmp_path / "_internal"
    (internal / "script").mkdir(parents=True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.setattr(sys, "_MEIPASS", str(internal), raising=False)

    assert resources.resource_path("script") == tmp_path / "script"
    assert resources.resource_path("script", "nested", "hit.txt") == (
        tmp_path / "script" / "nested" / "hit.txt"
    )


def test_app_path_uses_executable_directory_when_frozen(monkeypatch, tmp_path):
    exe = tmp_path / "auto-bdsp-rng.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))

    assert resources.app_path("bridge") == tmp_path / "bridge"


def test_script_directory_uses_project_root_in_source_checkout():
    root = Path(__file__).resolve().parents[1]

    assert resources.script_directory() == root / "script"


def test_script_directory_uses_executable_directory_without_meipass_fallback(
    monkeypatch, tmp_path
):
    exe = tmp_path / "auto-bdsp-rng.exe"
    internal = tmp_path / "_internal"
    (internal / "script").mkdir(parents=True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.setattr(sys, "_MEIPASS", str(internal), raising=False)

    assert resources.script_directory() == tmp_path / "script"


def test_remap_legacy_script_path_handles_posix_nested_path():
    migrated = resources.remap_legacy_script_path(
        "/opt/auto-bdsp-rng/_internal/script/nested/arceus.txt",
    )

    assert migrated == resources.script_directory() / "nested" / "arceus.txt"


def test_remap_legacy_script_path_handles_windows_nested_path(tmp_path):
    canonical = tmp_path / "script"

    migrated = resources.remap_legacy_script_path(
        r"D:\auto-bdsp-rng\_internal\script\nested\arceus.txt",
        script_dir=canonical,
    )

    assert migrated == canonical / "nested" / "arceus.txt"


def test_remap_legacy_script_path_matches_mixed_case_posix_marker(tmp_path):
    canonical = tmp_path / "script"

    migrated = resources.remap_legacy_script_path(
        "/opt/auto-bdsp-rng/_InTeRnAl/ScRiPt/nested/arceus.txt",
        script_dir=canonical,
    )

    assert migrated == canonical / "nested" / "arceus.txt"


def test_remap_legacy_script_path_matches_mixed_case_windows_marker(tmp_path):
    canonical = tmp_path / "script"

    migrated = resources.remap_legacy_script_path(
        r"D:\auto-bdsp-rng\_INTERNAL\sCrIpT\nested\arceus.txt",
        script_dir=canonical,
    )

    assert migrated == canonical / "nested" / "arceus.txt"


def test_remap_legacy_script_path_preserves_existing_external_marker_path(tmp_path):
    canonical = tmp_path / "app" / "script"
    external = tmp_path / "workspace" / "_internal" / "script" / "custom.ecs"
    external.parent.mkdir(parents=True)
    external.write_text("A 100\n", encoding="utf-8")

    assert resources.remap_legacy_script_path(external, script_dir=canonical) == external


def test_remap_legacy_script_path_maps_existing_current_install_legacy_path(tmp_path):
    canonical = tmp_path / "app" / "script"
    legacy = tmp_path / "app" / "_internal" / "script" / "hit.txt"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("_闪帧 = 1\n", encoding="utf-8")

    assert resources.remap_legacy_script_path(legacy, script_dir=canonical) == (
        canonical / "hit.txt"
    )


def test_remap_legacy_script_path_preserves_nonlegacy_path(monkeypatch, tmp_path):
    original = Path("/opt/auto-bdsp-rng/script/arceus.txt")
    monkeypatch.setattr(resources, "script_directory", lambda: tmp_path / "script")

    assert resources.remap_legacy_script_path(original) == original


def test_remap_legacy_script_path_rejects_traversal(monkeypatch, tmp_path):
    original = r"D:\auto-bdsp-rng\_internal\script\nested\..\..\settings.json"
    monkeypatch.setattr(resources, "script_directory", lambda: tmp_path / "script")

    assert resources.remap_legacy_script_path(original) == Path(original)


def test_remap_legacy_script_path_rejects_absolute_windows_suffix(tmp_path):
    original = "/opt/auto-bdsp-rng/_internal/script/C:/outside.txt"

    assert resources.remap_legacy_script_path(
        original,
        script_dir=tmp_path / "script",
    ) == Path(original)


def test_remap_legacy_script_path_rejects_drive_relative_windows_suffix(tmp_path):
    original = "/opt/auto-bdsp-rng/_internal/script/C:outside.txt"

    assert resources.remap_legacy_script_path(
        original,
        script_dir=tmp_path / "script",
    ) == Path(original)


def test_app_icon_path_points_to_packaged_icon_source():
    assert resources.app_icon_path().name == "app-icon.png"
    assert resources.app_icon_path().exists()


def test_resource_path_prefers_exe_adjacent_resource_when_frozen(monkeypatch, tmp_path):
    exe = tmp_path / "auto-bdsp-rng.exe"
    project_xs = tmp_path / "third_party" / "Project_Xs_CHN" / "images"
    project_xs.mkdir(parents=True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "_internal"), raising=False)

    assert resources.resource_path("third_party", "Project_Xs_CHN", "images") == project_xs
