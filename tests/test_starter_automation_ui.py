from hashlib import sha256
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from auto_bdsp_rng.automation.auto_rng.models import AutoRngPhase
from auto_bdsp_rng.automation.auto_rng.starter_scripts import starter_script_paths
from auto_bdsp_rng.automation.easycon.native.parser import parse_text
from auto_bdsp_rng.data import get_static_encounters
from auto_bdsp_rng.gen8_static import StateFilter
from auto_bdsp_rng.ui.auto_rng_panel import AutoRngPanel


@pytest.fixture
def panel(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    result = AutoRngPanel(script_dir=tmp_path, settings=QSettings(str(tmp_path / "panel.ini"), QSettings.Format.IniFormat))
    yield result
    result.close()
    result.deleteLater()
    app.processEvents()


def select(panel, *species):
    records = {record.template.species: record for record in get_static_encounters()}
    panel.set_targets([(records[value], StateFilter(), "any") for value in species])


@pytest.mark.parametrize("species", [387, 390, 393])
def test_starter_binds_bundles_without_manual_scripts_and_preserves_selections(panel, species):
    select(panel, species)
    manual = panel.build_config()
    panel.starter_automation_check.setChecked(True)
    config = panel.build_config()
    assert config.starter_automation and config.auto_reverse
    assert not config.escape_continue and config.sync_mode == 0
    assert (config.seed_script_path, config.reverse_script_path) == starter_script_paths()
    assert config.advance_script_path is None and config.hit_script_path is None
    assert config.fixed_delay == 66
    assert panel._missing_script_fields() == []
    assert panel.starter_script_description.isHidden() is False
    assert all(picker.isHidden() for picker in panel.script_picker_widgets.values())
    assert not panel.start_from_reidentify_action.isEnabled()
    assert not panel.auto_reverse_combo.isEnabled()
    assert panel.auto_reverse_combo.currentIndex() == 1
    starts = []
    panel.startRequested.connect(starts.append)
    panel._start_from_capture_clicked()
    assert starts[0].start_phase == AutoRngPhase.CAPTURE_SEED
    panel.starter_automation_check.setChecked(False)
    restored = panel.build_config()
    assert restored.seed_script_path == manual.seed_script_path
    assert restored.hit_script_path == manual.hit_script_path
    assert restored.auto_reverse == manual.auto_reverse


def test_existing_custom_delay_and_samples_survive_starter_toggle(panel):
    select(panel, 390)
    panel.fixed_delay.setValue(43)
    panel.record_delay_sample((41,))
    panel.starter_automation_check.setChecked(True)
    assert panel.build_config().fixed_delay == 43
    assert panel.delay_samples() == [(41,)]


def test_script_save_keeps_mode_and_automatic_delay_preset(panel):
    select(panel, 387)
    panel.starter_automation_check.setChecked(True)
    panel._save_script_state()
    restored = AutoRngPanel(script_dir=panel.script_dir, settings=panel._settings)
    try:
        assert restored.starter_automation_check.isChecked()
        assert restored.build_config().fixed_delay == 66
    finally:
        restored.close()
        restored.deleteLater()


def test_starter_mode_persists_and_is_disabled_for_mixed_species(panel):
    select(panel, 393)
    panel.starter_automation_check.setChecked(True)
    panel._save_panel_state()
    restored = AutoRngPanel(script_dir=panel.script_dir, settings=panel._settings)
    try:
        assert restored.starter_automation_check.isChecked()
        assert restored.build_config().fixed_delay == 66
        select(restored, 387, 390)
        assert not restored.starter_automation_check.isChecked()
        assert not restored.starter_automation_check.isEnabled()
    finally:
        restored.close()
        restored.deleteLater()


def test_starter_blocks_invalid_delay_threshold_and_reidentify_start(panel):
    select(panel, 387)
    panel.starter_automation_check.setChecked(True)
    with pytest.raises(ValueError, match="测种或捕获"):
        panel.build_config(start_phase=AutoRngPhase.REIDENTIFY)
    panel.shiny_threshold_seconds.setValue(0)
    with pytest.raises(ValueError, match="闪光阈值"):
        panel.build_config()
    panel.shiny_threshold_seconds.setValue(4.5)
    panel.fixed_delay.setValue(78)
    with pytest.raises(ValueError, match="小于 78"):
        panel.build_config()
    panel.set_preparing(True)
    assert not panel.starter_automation_check.isEnabled()
    panel.set_preparing(False)
    assert panel.starter_automation_check.isEnabled()


def test_reverse_bundle_matches_updated_library_and_native_parser_accepts_both():
    seed, reverse = starter_script_paths()
    assert sha256(reverse.read_bytes()).hexdigest() == "0ffc05b35a37fdf8dcae749e8c81332b847ed6a79ead8b8772c35320af26c442"
    assert reverse.read_bytes() == (Path(__file__).resolve().parents[1] / "script" / "御三家反查.txt").read_bytes()
    for path in (seed, reverse):
        parse_text(path.read_text(encoding="utf-8"), source=str(path))
