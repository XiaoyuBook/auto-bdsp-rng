from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from auto_bdsp_rng import __version__
from auto_bdsp_rng.ui.main_window import APP_DISPLAY_TITLE, MainWindow, configure_application_identity


ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT_PATH = ROOT / "scripts" / "build_exe.py"


def _load_build_script_module():
    spec = importlib.util.spec_from_file_location("build_exe", BUILD_SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_script_module = _load_build_script_module()


def test_pyinstaller_spec_collects_project_xs_win32ui_dependency():
    root = Path(__file__).resolve().parents[1]
    spec = (root / "packaging" / "auto-bdsp-rng.spec").read_text(encoding="utf-8")

    assert '"win32ui"' in spec


def test_capture_device_enumerator_is_installed_and_bundled():
    root = Path(__file__).resolve().parents[1]
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    spec = (root / "packaging" / "auto-bdsp-rng.spec").read_text(encoding="utf-8")

    assert '"cv2-enumerate-cameras>=1.3.3,<2"' in pyproject
    assert '"cv2_enumerate_cameras"' in spec
    assert '"cv2-enumerate-cameras"' in spec


def test_build_script_copies_project_xs_user_resources_next_to_exe():
    root = Path(__file__).resolve().parents[1]
    script = (root / "scripts" / "build_exe.py").read_text(encoding="utf-8")

    assert 'DIST_DIR / "third_party" / "Project_Xs_CHN" / "configs"' in script
    assert 'DIST_DIR / "third_party" / "Project_Xs_CHN" / "images"' in script
    assert 'DIST_DIR / "third_party" / "Project_Xs_CHN" / "src"' in script
    assert '"windowcapture.py"' in script
    assert "PROJECT_XS_OVERRIDES" in script
    assert "overlay_optional_tree" in script
    assert "verify_project_xs_assets" in script


def test_windows_build_keeps_user_scripts_only_next_to_exe():
    build_script = BUILD_SCRIPT_PATH.read_text(encoding="utf-8")
    spec = (ROOT / "packaging" / "auto-bdsp-rng.spec").read_text(encoding="utf-8")

    assert "ignore=shutil.ignore_patterns(SCRIPT_GENERATED_DIR_NAME)" in build_script
    assert "verify_script_layout()" in build_script
    assert 'DIST_DIR / "_internal" / "script"' in build_script
    assert 'tree_datas("script", "script")' not in spec


def test_copy_release_files_excludes_generated_script_snapshots(monkeypatch, tmp_path: Path):
    project_root = tmp_path / "project"
    source_script = project_root / "script"
    generated = source_script / ".generated"
    generated.mkdir(parents=True)
    (source_script / "default.txt").write_bytes(b"default")
    (generated / "stale.ecs").write_bytes(b"generated")

    dist_dir = tmp_path / "dist"
    stale_dist_generated = dist_dir / "script" / ".generated"
    stale_dist_generated.mkdir(parents=True)
    (stale_dist_generated / "older.ecs").write_bytes(b"older")

    monkeypatch.setattr(build_script_module, "ROOT", project_root)
    monkeypatch.setattr(build_script_module, "DIST_DIR", dist_dir)
    monkeypatch.setattr(build_script_module, "PROJECT_XS_ROOT", project_root / "missing-project-xs")
    monkeypatch.setattr(build_script_module, "PROJECT_XS_OVERRIDES", project_root / "missing-overrides")

    build_script_module.copy_release_files()

    assert (dist_dir / "script" / "default.txt").read_bytes() == b"default"
    assert not (dist_dir / "script" / ".generated").exists()
    build_script_module.verify_script_layout()


@pytest.mark.parametrize("generated_kind", ["directory", "file"])
def test_verify_script_layout_rejects_generated_snapshots(
    monkeypatch,
    tmp_path: Path,
    generated_kind: str,
):
    dist_dir = tmp_path / "dist"
    script_dir = dist_dir / "script"
    script_dir.mkdir(parents=True)
    generated = script_dir / ".generated"
    if generated_kind == "directory":
        generated.mkdir()
    else:
        generated.write_bytes(b"unexpected")
    monkeypatch.setattr(build_script_module, "DIST_DIR", dist_dir)

    with pytest.raises(SystemExit, match="must not contain generated script snapshots"):
        build_script_module.verify_script_layout()


def test_windows_build_includes_ocr_dependencies():
    root = Path(__file__).resolve().parents[1]
    script = (root / "scripts" / "build_exe.py").read_text(encoding="utf-8")
    spec = (root / "packaging" / "auto-bdsp-rng.spec").read_text(encoding="utf-8")
    runtime_hook = (root / "packaging" / "runtime_paddlex_cache.py").read_text(encoding="utf-8")

    assert '".[dev,ocr]"' in script
    assert "verify_ocr_dependencies" in script
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    assert '"paddlex[ocr]>=3.5,<3.6"' in pyproject
    assert '"paddle"' in spec
    assert '"paddleocr"' in spec
    assert '"paddlex"' in spec
    assert "copy_metadata" in spec
    assert '"scikit-learn"' in spec
    assert '"python-bidi"' in spec
    assert '"tokenizers"' in spec
    assert '"paddleocr",' not in spec.partition("excludes=[")[2].partition("]")[0]
    assert "official_models" in spec
    assert '"PP-OCRv5_mobile_det"' in spec
    assert '"PP-OCRv5_mobile_rec"' in spec
    assert 'tree_datas(str(model_cache), "paddlex_cache/official_models")' not in spec
    assert "runtime_paddlex_cache.py" in spec
    assert "PADDLE_PDX_CACHE_HOME" in runtime_hook


def test_packaged_gui_entry_has_ocr_smoke_probe():
    root = Path(__file__).resolve().parents[1]
    entry = (root / "packaging" / "entry_gui.py").read_text(encoding="utf-8")

    assert "AUTO_BDSP_RNG_OCR_SMOKE" in entry
    assert "read_paddle_ocr_text" in entry
    assert "PADDLE_PDX_CACHE_HOME" in entry


def test_pyinstaller_spec_names_chinese_executable():
    root = Path(__file__).resolve().parents[1]
    spec = (root / "packaging" / "auto-bdsp-rng.spec").read_text(encoding="utf-8")

    assert 'name="珍钻复刻自动乱数"' in spec


def test_windows_build_bundles_standalone_updater_next_to_main_executable():
    root = Path(__file__).resolve().parents[1]
    script = (root / "scripts" / "build_exe.py").read_text(encoding="utf-8")
    updater_spec = (root / "packaging" / "auto-bdsp-rng-updater.spec").read_text(encoding="utf-8")

    assert "build_updater(python)" in script
    assert 'UPDATER_EXE_NAME = "auto-bdsp-rng-updater.exe"' in script
    assert "shutil.copy2(source, DIST_DIR / UPDATER_EXE_NAME)" in script
    assert 'name="auto-bdsp-rng-updater"' in updater_spec
    assert "console=False" in updater_spec


def test_qt_application_and_window_use_project_identity(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    configure_application_identity(app)
    window = MainWindow()

    assert APP_DISPLAY_TITLE.endswith(f"v{__version__}")
    assert app.applicationName() == APP_DISPLAY_TITLE
    assert window.windowTitle() == APP_DISPLAY_TITLE
    assert not app.windowIcon().isNull()
    assert not window.windowIcon().isNull()
