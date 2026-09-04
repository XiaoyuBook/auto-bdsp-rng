from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from auto_bdsp_rng.automation.auto_rng.delay_strategy import (
    DelayStrategy,
    MultiCandidatePolicy,
)
from auto_bdsp_rng.ui.auto_rng_panel import AutoRngPanel


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def _settings(path: Path) -> QSettings:
    settings = QSettings(str(path), QSettings.Format.IniFormat)
    settings.clear()
    return settings


def _write_delay_config(settings: QSettings, strategy: str) -> None:
    settings.setValue("fixed_delay", 1450)
    settings.setValue("delay_strategy", strategy)
    settings.setValue("delay_multi_candidate_policy", "weighted")
    settings.setValue("delay_window_size", 5)
    settings.setValue("delay_ewma_alpha", 0.5)
    settings.setValue("delay_dense_interval_width", 2)
    settings.setValue(
        "delay_sample_rounds_json",
        json.dumps([[1448], [1450], [1452], [1450], [1460]]),
    )


@pytest.mark.parametrize(
    ("strategy", "label", "expected_delay"),
    [
        ("fixed", "固定 delay", 1450),
        ("last", "上次实际 delay", 1460),
        ("mode", "众数", 1450),
        ("median", "中位数", 1450),
        ("mean", "滚动平均值", 1452),
        ("ema", "指数平滑", 1455),
        ("trimmed_mean", "截尾平均", 1451),
        ("dense_interval", "密集区间", 1450),
    ],
)
def test_panel_restores_each_strategy_and_updates_summary_button(
    app,
    tmp_path,
    strategy: str,
    label: str,
    expected_delay: int,
):
    settings = _settings(tmp_path / f"{strategy}.ini")
    _write_delay_config(settings, strategy)

    panel = AutoRngPanel(script_dir=tmp_path, settings=settings)
    config = panel.delay_strategy_config()

    assert config.strategy is DelayStrategy(strategy)
    assert config.baseline_delay == 1450
    assert config.multi_candidate_policy is MultiCandidatePolicy.WEIGHTED
    assert config.window_size == 5
    assert config.ewma_alpha == 0.5
    assert config.dense_interval_width == 2
    assert panel.effective_delay_for_next_round() == expected_delay
    assert panel.delay_settings_button.text() == f"{label} · {expected_delay}"
    assert "下轮预计" in panel.delay_settings_button.toolTip()
    assert str(expected_delay) in panel.delay_settings_button.toolTip()


@pytest.mark.parametrize(
    ("strategy", "multi_visible", "window_visible", "ewma_visible", "dense_visible"),
    [
        ("fixed", False, False, False, False),
        ("last", False, False, False, False),
        ("mode", True, True, False, False),
        ("median", True, True, False, False),
        ("mean", True, True, False, False),
        ("ema", True, True, True, False),
        ("trimmed_mean", True, True, False, False),
        ("dense_interval", True, True, False, True),
    ],
)
def test_dialog_keeps_only_strategy_specific_controls_visible_and_editable(
    app,
    tmp_path,
    strategy: str,
    multi_visible: bool,
    window_visible: bool,
    ewma_visible: bool,
    dense_visible: bool,
):
    panel = AutoRngPanel(script_dir=tmp_path, settings=_settings(tmp_path / "controls.ini"))
    dialog = panel.delay_strategy_dialog

    dialog.strategy_combo.setCurrentIndex(dialog.strategy_combo.findData(strategy))

    conditional_controls = (
        (dialog.window_size, window_visible),
        (dialog.ewma_weight_percent, ewma_visible),
        (dialog.dense_interval_width, dense_visible),
    )
    for control, visible in conditional_controls:
        assert control.isHidden() == (not visible)
        if visible:
            assert control.isEnabled()
    assert dialog.baseline_delay.isEnabled()
    assert dialog.multi_candidate_widget.isHidden() == (not multi_visible)
    assert dialog.multi_candidate_widget.isEnabled()
    assert dialog.ignore_multi_candidate_button.isEnabled()
    assert dialog.weight_multi_candidate_button.isEnabled()


def test_raw_candidate_rounds_remain_grouped_across_qsettings_round_trip(app, tmp_path):
    settings_path = tmp_path / "samples.ini"
    settings = _settings(settings_path)
    panel = AutoRngPanel(script_dir=tmp_path, settings=settings)

    panel.record_delay_sample([1453, 1451, 1453, -1, "bad"])
    panel.record_delay_sample([1452])
    panel.record_delay_sample(None)
    settings.sync()

    assert json.loads(str(settings.value("delay_sample_rounds_json"))) == [
        [1451, 1453],
        [1452],
    ]

    restored = AutoRngPanel(
        script_dir=tmp_path,
        settings=QSettings(str(settings_path), QSettings.Format.IniFormat),
    )
    assert restored.delay_samples() == [(1451, 1453), (1452,)]
    assert restored.delay_strategy_dialog.recent_samples.text() == "1451,1453 / 1452"


def test_saved_last_strategy_ignores_trailing_multi_candidate_round(app, tmp_path):
    settings = _settings(tmp_path / "last-weighted.ini")
    _write_delay_config(settings, "last")
    settings.setValue("delay_sample_rounds_json", json.dumps([[1448], [1451, 1452]]))

    panel = AutoRngPanel(script_dir=tmp_path, settings=settings)

    assert panel.delay_strategy_config().multi_candidate_policy is MultiCandidatePolicy.WEIGHTED
    assert panel.effective_delay_for_next_round() == 1448
    assert panel.delay_strategy_dialog.multi_candidate_widget.isHidden()


def test_cancel_discards_all_draft_fields_and_leaves_saved_config_unchanged(app, tmp_path):
    settings = _settings(tmp_path / "cancel.ini")
    _write_delay_config(settings, "median")
    panel = AutoRngPanel(script_dir=tmp_path, settings=settings)
    committed = panel.delay_strategy_config()

    def edit_then_cancel() -> None:
        dialog = panel.delay_strategy_dialog
        dialog.strategy_combo.setCurrentIndex(dialog.strategy_combo.findData("ema"))
        dialog.baseline_delay.setValue(1499)
        dialog.window_size.setValue(12)
        dialog.ewma_weight_percent.setValue(25)
        dialog.dense_interval_width.setValue(7)
        dialog.ignore_multi_candidate_button.click()
        dialog.reject()

    QTimer.singleShot(0, edit_then_cancel)
    panel.delay_settings_button.click()

    assert panel.delay_strategy_config() == committed
    assert panel.delay_strategy_dialog.values() == committed
    assert panel.fixed_delay.value() == committed.baseline_delay
    assert settings.value("delay_strategy") == "median"
    assert int(settings.value("fixed_delay")) == 1450
    assert settings.value("delay_multi_candidate_policy") == "weighted"
    assert int(settings.value("delay_window_size")) == 5
    assert float(settings.value("delay_ewma_alpha")) == 0.5
    assert int(settings.value("delay_dense_interval_width")) == 2


def test_clear_samples_is_immediate_and_is_not_rolled_back_by_cancel(
    app,
    tmp_path,
    monkeypatch,
):
    settings = _settings(tmp_path / "clear.ini")
    panel = AutoRngPanel(script_dir=tmp_path, settings=settings)
    panel.record_delay_sample([1451, 1452])
    panel.record_delay_sample([1450])
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )

    def clear_then_cancel() -> None:
        dialog = panel.delay_strategy_dialog
        dialog.clear_samples_button.click()
        dialog.reject()

    QTimer.singleShot(0, clear_then_cancel)
    panel.delay_settings_button.click()
    app.processEvents()
    settings.sync()

    assert panel.delay_samples() == []
    assert json.loads(str(settings.value("delay_sample_rounds_json"))) == []
    assert panel.delay_strategy_dialog.recent_samples.text() == "暂无样本"
    assert not panel.delay_strategy_dialog.clear_samples_button.isEnabled()
    assert panel.delay_settings_button.text() == "固定 delay · 100"
