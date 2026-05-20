from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication

from auto_bdsp_rng.automation.auto_rng.ocr_regions import OcrRegion
from auto_bdsp_rng.automation.auto_tid_rng import AutoTidRngConfig
from auto_bdsp_rng.ui import MainWindow
from auto_bdsp_rng.ui.auto_tid_rng_panel import AutoTidRngPanel
from auto_bdsp_rng.ui.tid_ocr_dialog import TidOcrDialog


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def _settings(tmp_path: Path) -> QSettings:
    return QSettings(str(tmp_path / "tid_ocr.ini"), QSettings.Format.IniFormat)


def test_auto_tid_panel_builds_config_with_target_list(app, tmp_path: Path) -> None:
    seed_script = tmp_path / "BDSP测种.txt"
    name_script = tmp_path / "取名.txt"
    reverse_script = tmp_path / "反查ID.txt"
    for path in (seed_script, name_script, reverse_script):
        path.write_text("A 100\n", encoding="utf-8")
    panel = AutoTidRngPanel(script_dir=tmp_path)
    panel.frame_threshold.setValue(300)
    panel.delay.setValue(20)
    panel.reverse_lookup_window.setValue(50)
    panel.add_target_tid(1)
    panel.add_target_tid(22222)
    panel.seed_script_combo.setCurrentIndex(panel.seed_script_combo.findText(seed_script.name))
    panel.name_script_combo.setCurrentIndex(panel.name_script_combo.findText(name_script.name))
    panel.reverse_id_script_combo.setCurrentIndex(panel.reverse_id_script_combo.findText(reverse_script.name))
    panel.set_ocr_region(OcrRegion(10, 20, 30, 40))

    config = panel.build_config()

    assert isinstance(config, AutoTidRngConfig)
    assert config.frame_threshold == 300
    assert config.delay == 20
    assert config.reverse_lookup_window == 50
    assert config.target_tids == (1, 22222)
    assert config.seed_script_path == seed_script
    assert config.name_script_path == name_script
    assert config.reverse_id_script_path == reverse_script
    assert config.ocr_region == OcrRegion(10, 20, 30, 40)


def test_tid_ocr_dialog_is_non_modal_and_has_two_primary_actions(app, tmp_path: Path) -> None:
    dialog = TidOcrDialog(settings=_settings(tmp_path))

    assert not dialog.isModal()
    assert dialog.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert dialog.recognize_button.text() == "识别当前内容"
    assert dialog.select_button.text() == "框选范围"


def test_tid_ocr_dialog_saves_region_and_runs_on_demand_recognizer(app, tmp_path: Path) -> None:
    dialog = TidOcrDialog(settings=_settings(tmp_path), recognizer=lambda region: f"{region.x:05d}")
    dialog.set_region(OcrRegion(123, 20, 30, 40))

    dialog.recognize_current()

    assert dialog.region == OcrRegion(123, 20, 30, 40)
    assert dialog.result_value.text() == "00123"
    restored = TidOcrDialog(settings=_settings(tmp_path))
    assert restored.region == OcrRegion(123, 20, 30, 40)


def test_main_window_starts_auto_tid_runner_from_panel_signal(app, tmp_path: Path, monkeypatch) -> None:
    window = MainWindow()
    seed_script = tmp_path / "BDSP测种.txt"
    name_script = tmp_path / "取名.txt"
    reverse_script = tmp_path / "反查ID.txt"
    for path in (seed_script, name_script, reverse_script):
        path.write_text("A 100\n", encoding="utf-8")
    config = AutoTidRngConfig(
        script_dir=tmp_path,
        seed_script_path=seed_script,
        name_script_path=name_script,
        reverse_id_script_path=reverse_script,
        frame_threshold=300,
        target_tids=(1,),
        delay=20,
        ocr_region=OcrRegion(1, 2, 3, 4),
    )
    started = []
    window._latest_preview_frame = object()
    monkeypatch.setattr(window.auto_tid_rng_tab, "run_with_runner", started.append)

    window._start_auto_tid_rng(config)

    assert len(started) == 1
    assert started[0].config == config


def test_main_window_tid_ocr_region_selection_confirm_emits_region(app, monkeypatch) -> None:
    window = MainWindow()
    emitted = []
    window.tidOcrRegionSelected.connect(emitted.append)
    window._selection_mode = "tid_ocr_region"
    window.preview_label.set_selection_enabled(True)
    monkeypatch.setattr(window, "_confirm_preview_selection", lambda _roi: True)

    window._handle_preview_selection((10, 20, 30, 40))

    assert emitted == [(10, 20, 30, 40)]
    assert window._selection_mode is None
    assert window.preview_label._ocr_overlay_field == "tid"
