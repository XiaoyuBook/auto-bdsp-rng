from __future__ import annotations

import sys
from pathlib import Path

from auto_bdsp_rng import resources


def test_resource_path_uses_project_root_in_source_checkout():
    root = Path(__file__).resolve().parents[1]

    assert resources.resource_path("script") == root / "script"


def test_resource_path_uses_meipass_when_frozen(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "_internal"), raising=False)

    assert resources.resource_path("script") == tmp_path / "_internal" / "script"


def test_app_path_uses_executable_directory_when_frozen(monkeypatch, tmp_path):
    exe = tmp_path / "auto-bdsp-rng.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))

    assert resources.app_path("bridge") == tmp_path / "bridge"


def test_app_icon_path_points_to_packaged_icon_source():
    assert resources.app_icon_path().name == "app-icon.png"
    assert resources.app_icon_path().exists()
