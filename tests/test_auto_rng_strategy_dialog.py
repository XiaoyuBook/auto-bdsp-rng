from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QApplication, QFormLayout

from auto_bdsp_rng.ui.auto_rng_panel import AutoRngPanel, QT_INT_MAX


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def _settings(path: Path) -> QSettings:
    settings = QSettings(str(path), QSettings.Format.IniFormat)
    settings.clear()
    return settings


def test_strategy_dialog_defaults_and_row_order(app, tmp_path):
    panel = AutoRngPanel(script_dir=tmp_path, settings=_settings(tmp_path / "auto-rng.ini"))
    dialog = panel.strategy_dialog

    assert dialog.windowTitle() == "校正策略设置"
    assert dialog.reseed_threshold_frames.value() == 900_000
    assert dialog.reidentify_max_attempts.value() == 2
    assert dialog.policy() == "next_round"
    assert dialog.reidentify_seed_max_attempts.value() == 1
    assert dialog.reidentify_seed_max_attempts.isEnabled()
    assert dialog.reseeding_threshold.value() == 500_000
    assert [
        dialog.form.labelForField(field).text()
        for field in (
            dialog.reseed_threshold_frames,
            dialog.reidentify_max_attempts,
            dialog.reidentify_failure_policy,
            dialog.reidentify_seed_max_attempts,
            dialog.reseeding_threshold,
        )
    ] == [
        "校正帧数上限",
        "普通校正最大尝试次数",
        "普通校正连续失败后",
        "重测 Seed 最大尝试次数",
        "过场预留帧数",
    ]
    assert dialog.reidentify_failure_policy.itemData(0) == "next_round"
    assert dialog.reidentify_failure_policy.itemData(1) == "recapture_seed"
    assert dialog.restore_defaults_button.text() == "恢复默认值"
    assert dialog.ok_button.text() == "确定"
    assert dialog.cancel_button.text() == "取消"


def test_strategy_dialog_keeps_seed_attempts_editable_for_all_policies(app, tmp_path):
    panel = AutoRngPanel(script_dir=tmp_path, settings=_settings(tmp_path / "auto-rng.ini"))
    dialog = panel.strategy_dialog
    label = dialog.form.labelForField(dialog.reidentify_seed_max_attempts)

    dialog.set_policy("next_round")
    assert dialog.reidentify_seed_max_attempts.isEnabled()
    assert label.isEnabled()

    dialog.reidentify_seed_max_attempts.setValue(8)
    dialog.set_policy("recapture_seed")
    assert dialog.reidentify_seed_max_attempts.isEnabled()
    assert label.isEnabled()
    assert dialog.reidentify_seed_max_attempts.value() == 8


def test_strategy_dialog_cancel_restores_all_values_without_persisting(app, tmp_path):
    settings = _settings(tmp_path / "auto-rng.ini")
    panel = AutoRngPanel(script_dir=tmp_path, settings=settings)
    original_values = panel.strategy_dialog.values()

    def edit_then_cancel() -> None:
        panel.reseed_threshold_frames.setValue(123_456)
        panel.reidentify_max_attempts.setValue(7)
        panel.strategy_dialog.set_policy("recapture_seed")
        panel.reidentify_seed_max_attempts.setValue(4)
        panel.reseeding_threshold.setValue(65_432)
        panel.strategy_dialog.reject()

    QTimer.singleShot(0, edit_then_cancel)
    panel.strategy_settings_button.click()

    assert panel.strategy_dialog.values() == original_values
    assert not settings.contains("reseed_threshold_frames")
    assert not settings.contains("reidentify_max_attempts")
    assert not settings.contains("reidentify_failure_policy")
    assert not settings.contains("reidentify_seed_max_attempts")
    assert not settings.contains("reseeding_threshold")


def test_strategy_dialog_restore_defaults_is_transactional(app, tmp_path):
    settings = _settings(tmp_path / "auto-rng.ini")
    settings.setValue("reseed_threshold_frames", 123_456)
    settings.setValue("reidentify_max_attempts", 7)
    settings.setValue("reidentify_failure_policy", "recapture_seed")
    settings.setValue("reidentify_seed_max_attempts", 4)
    settings.setValue("reseeding_threshold", 65_432)
    panel = AutoRngPanel(script_dir=tmp_path, settings=settings)
    saved_values = panel.strategy_dialog.values()

    def reset_then_cancel() -> None:
        panel.strategy_dialog.restore_defaults_button.click()
        assert panel.strategy_dialog.values() == (900_000, 2, "next_round", 1, 500_000)
        panel.strategy_dialog.reject()

    QTimer.singleShot(0, reset_then_cancel)
    panel.strategy_settings_button.click()

    assert panel.strategy_dialog.values() == saved_values
    assert int(settings.value("reseed_threshold_frames")) == 123_456
    assert int(settings.value("reidentify_max_attempts")) == 7
    assert settings.value("reidentify_failure_policy") == "recapture_seed"
    assert int(settings.value("reidentify_seed_max_attempts")) == 4
    assert int(settings.value("reseeding_threshold")) == 65_432

    def reset_then_accept() -> None:
        panel.strategy_dialog.restore_defaults_button.click()
        panel.strategy_dialog.accept()

    QTimer.singleShot(0, reset_then_accept)
    panel.strategy_settings_button.click()

    assert panel.strategy_dialog.values() == (900_000, 2, "next_round", 1, 500_000)
    assert int(settings.value("reseed_threshold_frames")) == 900_000
    assert int(settings.value("reidentify_max_attempts")) == 2
    assert settings.value("reidentify_failure_policy") == "next_round"
    assert int(settings.value("reidentify_seed_max_attempts")) == 1
    assert int(settings.value("reseeding_threshold")) == 500_000


def test_strategy_dialog_accept_persists_and_builds_config(app, tmp_path):
    settings_path = tmp_path / "auto-rng.ini"
    settings = _settings(settings_path)
    panel = AutoRngPanel(script_dir=tmp_path, settings=settings)

    def edit_then_accept() -> None:
        panel.reseed_threshold_frames.setValue(1_234_567)
        panel.reidentify_max_attempts.setValue(12)
        panel.strategy_dialog.set_policy("recapture_seed")
        panel.reidentify_seed_max_attempts.setValue(9)
        panel.reseeding_threshold.setValue(234_567)
        panel.strategy_dialog.accept()

    QTimer.singleShot(0, edit_then_accept)
    panel.strategy_settings_button.click()
    settings.sync()

    restored_settings = QSettings(str(settings_path), QSettings.Format.IniFormat)
    restored = AutoRngPanel(script_dir=tmp_path, settings=restored_settings)
    config = restored.build_config()

    assert restored.strategy_dialog.values() == (1_234_567, 12, "recapture_seed", 9, 234_567)
    assert config.reseed_threshold_frames == 1_234_567
    assert config.reidentify_max_attempts == 12
    assert config.reidentify_failure_policy == "recapture_seed"
    assert config.reidentify_seed_max_attempts == 9
    assert config.reseeding_threshold == 234_567


def test_strategy_button_replaces_main_form_reserve_frames_row(app, tmp_path):
    panel = AutoRngPanel(script_dir=tmp_path, settings=_settings(tmp_path / "auto-rng.ini"))
    form = panel.strategy_group.layout()

    button_index = form.indexOf(panel.strategy_settings_button)
    button_row, button_role = form.getItemPosition(button_index)
    max_wait_row, _ = form.getWidgetPosition(panel.max_wait_frames)
    shiny_row, _ = form.getWidgetPosition(panel.shiny_threshold_seconds)

    assert panel.strategy_settings_button.text() == "校正策略设置..."
    assert panel.strategy_settings_button.objectName() == "SecondaryButton"
    assert panel.strategy_settings_button.size().width() == 215
    assert panel.strategy_settings_button.size().height() == 34
    assert button_role == QFormLayout.ItemRole.FieldRole
    assert button_row == max_wait_row + 1
    assert shiny_row == button_row + 1
    assert form.indexOf(panel.reseeding_threshold) == -1


def test_strategy_numeric_fields_use_c_locale_and_qt_integer_limit(app, tmp_path):
    panel = AutoRngPanel(script_dir=tmp_path, settings=_settings(tmp_path / "auto-rng.ini"))
    numeric_fields = (
        panel.reseed_threshold_frames,
        panel.reidentify_max_attempts,
        panel.reidentify_seed_max_attempts,
        panel.reseeding_threshold,
    )

    for field in numeric_fields:
        assert field.locale().name() == "C"
        assert field.lineEdit().locale().name() == "C"
        assert field.maximum() == QT_INT_MAX
        assert field.suffix() == ""
        assert field.lineEdit().text() == str(field.value())
    assert panel.reidentify_max_attempts.minimum() == 1
    assert panel.reidentify_seed_max_attempts.minimum() == 1


def test_strategy_dialog_restores_legacy_reserve_frames_setting(app, tmp_path):
    settings = _settings(tmp_path / "auto-rng.ini")
    settings.setValue("reseeding_threshold", 345_678)

    panel = AutoRngPanel(script_dir=tmp_path, settings=settings)

    assert panel.reseeding_threshold.value() == 345_678
    assert panel.build_config().reseeding_threshold == 345_678
