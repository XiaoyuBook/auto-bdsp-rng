from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "generate_release_notes.py"


def _load_release_notes_module():
    spec = importlib.util.spec_from_file_location("generate_release_notes", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release_notes = _load_release_notes_module()


def _write_release_metadata(root: Path, version: str, changelog_entry: str) -> None:
    (root / "src" / "auto_bdsp_rng").mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        f"[project]\nversion = \"{version}\"\n",
        encoding="utf-8",
    )
    (root / "src" / "auto_bdsp_rng" / "__init__.py").write_text(
        f'__version__ = "{version}"\n',
        encoding="utf-8",
    )
    (root / "CHANGELOG.md").write_text(
        f"# {version}\n\n{changelog_entry}\n\n# 1.0.0\n\n- 旧版本\n",
        encoding="utf-8",
    )


def test_generate_release_notes_includes_matching_changelog_entry(tmp_path: Path):
    _write_release_metadata(tmp_path, "2.2.0", "- 新增发布说明自动生成")
    output = tmp_path / "release" / "release-notes.md"

    release_notes.generate_release_notes(tmp_path, "v2.2.0", output)

    body = output.read_text(encoding="utf-8")
    assert "# 珍钻复刻自动乱数 v2.2.0" in body
    assert "## 本次更新" in body
    assert "- 新增发布说明自动生成" in body
    assert "- 旧版本" not in body
    assert "auto-bdsp-rng-v2.2.0-windows-x64.zip" in body
    assert "帮助 -> 检查更新…" in body
    assert "只下载发生变化的文件" in body


def test_generate_release_notes_rejects_mismatched_tag(tmp_path: Path):
    _write_release_metadata(tmp_path, "2.2.0", "- 新增发布说明自动生成")

    with pytest.raises(release_notes.ReleaseNotesError, match="does not match"):
        release_notes.generate_release_notes(
            tmp_path,
            "v2.2.1",
            tmp_path / "release" / "release-notes.md",
        )


def test_generate_release_notes_requires_current_changelog_entry(tmp_path: Path):
    _write_release_metadata(tmp_path, "2.2.0", "- 新增发布说明自动生成")
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "# 2.2.1\n\n- 尚未发布的内容\n\n" + changelog.read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    with pytest.raises(release_notes.ReleaseNotesError, match="current top-level entry"):
        release_notes.generate_release_notes(
            tmp_path,
            "v2.2.0",
            tmp_path / "release" / "release-notes.md",
        )


def test_release_workflow_uses_generated_release_body():
    workflow = (ROOT / ".github" / "workflows" / "build-windows-release.yml").read_text(
        encoding="utf-8"
    )

    assert "scripts\\generate_release_notes.py" in workflow
    assert "body_path: release/release-notes.md" in workflow
    assert "softprops/action-gh-release@v3" in workflow
