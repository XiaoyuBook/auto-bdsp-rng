from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication

from auto_bdsp_rng.automation.auto_rng.ocr_regions import OcrRegion
from auto_bdsp_rng.automation.auto_tid_rng import AutoTidRngConfig
from auto_bdsp_rng.gen8_id import IDState8
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
    for path in (seed_script, name_script):
        path.write_text("A 100\n", encoding="utf-8")
    panel = AutoTidRngPanel(script_dir=tmp_path)
    panel.frame_threshold.setValue(300)
    panel.delay.setValue(20)
    panel.add_target_display_tid(1)
    panel.add_target_display_tid(222222)
    panel.seed_script_combo.setCurrentIndex(panel.seed_script_combo.findText(seed_script.name))
    panel.name_script_combo.setCurrentIndex(panel.name_script_combo.findText(name_script.name))

    config = panel.build_config()

    assert isinstance(config, AutoTidRngConfig)
    assert config.frame_threshold == 300
    assert config.delay == 20
    assert config.target_display_tids == (1, 222222)
    assert config.seed_script_path == seed_script
    assert config.name_script_path == name_script


def test_auto_tid_panel_replaces_log_with_operable_id_table(app, tmp_path: Path) -> None:
    panel = AutoTidRngPanel(script_dir=tmp_path)

    panel.set_id_states([
        IDState8(advances=5, tid=10, sid=20, tsv=1, display_tid=1),
        IDState8(advances=42, tid=30, sid=40, tsv=2, display_tid=123456),
    ])

    assert panel.id_table.rowCount() == 2
    assert panel.id_table.item(1, 4).text() == "123456"
    assert panel.log_view.isVisible() is False
    assert panel.copy_button.isEnabled()
    assert panel.export_button.isEnabled()


def test_auto_tid_panel_keeps_targets_compact_and_gives_id_table_space(app, tmp_path: Path) -> None:
    panel = AutoTidRngPanel(script_dir=tmp_path)

    assert panel.target_list.maximumHeight() <= 140
    assert panel.id_table.minimumHeight() >= 320
    assert panel.id_table.horizontalHeader().stretchLastSection()


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
        frame_threshold=300,
        target_display_tids=(1,),
        delay=20,
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
