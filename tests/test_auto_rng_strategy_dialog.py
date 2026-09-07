from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QApplication, QFormLayout

from auto_bdsp_rng.automation.auto_rng.delay_strategy import (
    DelayStrategy,
    DelayStrategyConfig,
    MultiCandidatePolicy,
)
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
    sync_row, _ = form.getWidgetPosition(panel.sync_field)
    reverse_row, _ = form.getWidgetPosition(panel.reverse_field)

    assert panel.strategy_settings_button.text() == "设置"
    assert panel.strategy_settings_button.objectName() == "SecondaryButton"
    assert panel.strategy_settings_button.size().width() == 180
    assert panel.strategy_settings_button.size().height() == 32
    assert button_role == QFormLayout.ItemRole.FieldRole
    assert shiny_row > max_wait_row
    assert shiny_row < sync_row < reverse_row < button_row
    assert form.labelForField(panel.strategy_settings_button).text() == "校正策略"
    assert form.indexOf(panel.reseeding_threshold) == -1
    assert panel.shiny_threshold_seconds.isHidden()
    panel.more_strategy_button.setChecked(True)
    assert not panel.shiny_threshold_seconds.isHidden()
    assert not panel.sync_field.isHidden()
    assert not panel.reverse_field.isHidden()


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


def test_delay_strategy_button_and_dialog_defaults(app, tmp_path):
    panel = AutoRngPanel(script_dir=tmp_path, settings=_settings(tmp_path / "auto-rng.ini"))
    dialog = panel.delay_strategy_dialog
    form = panel.strategy_group.layout()

    assert form.labelForField(panel.delay_settings_field).text() == "delay 策略"
    assert form.indexOf(panel.fixed_delay) == -1
    assert panel.fixed_delay.isHidden()
    assert panel.fixed_delay.value() == 100
    assert panel.delay_settings_button.text() == "固定 delay · 下轮 100"
    assert panel.delay_settings_button.size().width() == 180
    assert panel.delay_settings_button.size().height() == 32
    assert dialog.windowTitle() == "delay 策略设置"
    assert [dialog.strategy_combo.itemData(index) for index in range(dialog.strategy_combo.count())] == [
        "fixed",
        "last",
        "mode",
        "median",
        "mean",
        "ema",
        "trimmed_mean",
        "dense_interval",
    ]
    assert dialog.values().strategy is DelayStrategy.FIXED
    assert dialog.values().multi_candidate_policy is MultiCandidatePolicy.IGNORE
    assert dialog.values().window_size == 5
    assert dialog.values().ewma_alpha == 0.5
    assert dialog.values().dense_interval_width == 2
    assert dialog.multi_candidate_widget.isHidden()
    assert dialog._form_rows[dialog.strategy_combo].label.text() == "delay 策略"
    assert dialog._form_rows[dialog.strategy_combo].label.width() == 92
    assert dialog.strategy_description.text() == "始终使用基准 delay；样本会继续保留。"
    runtime_layout = dialog.runtime_summary.layout()
    assert runtime_layout.count() == 3
    assert runtime_layout.stretch(0) == 100
    assert runtime_layout.stretch(2) == 145
    assert dialog.valid_sample_count.isHidden()


def test_delay_strategy_dialog_only_shows_strategy_specific_parameters(app, tmp_path):
    panel = AutoRngPanel(script_dir=tmp_path, settings=_settings(tmp_path / "auto-rng.ini"))
    dialog = panel.delay_strategy_dialog

    assert dialog.window_size.isHidden()
    assert dialog.ewma_weight_percent.isHidden()
    assert dialog.dense_interval_width.isHidden()

    dialog.strategy_combo.setCurrentIndex(dialog.strategy_combo.findData("last"))
    assert dialog.multi_candidate_widget.isHidden()
    assert dialog.strategy_description.text() == "使用最近一次唯一候选；多个候选的轮次自动跳过。"

    dialog.strategy_combo.setCurrentIndex(dialog.strategy_combo.findData("ema"))
    assert not dialog.multi_candidate_widget.isHidden()
    assert not dialog.window_size.isHidden()
    assert not dialog.ewma_weight_percent.isHidden()
    assert dialog.dense_interval_width.isHidden()
    dialog.weight_multi_candidate_button.click()

    dialog.strategy_combo.setCurrentIndex(dialog.strategy_combo.findData("last"))
    assert dialog.multi_candidate_widget.isHidden()

    dialog.strategy_combo.setCurrentIndex(dialog.strategy_combo.findData("mode"))
    assert dialog.weight_multi_candidate_button.isChecked()

    dialog.strategy_combo.setCurrentIndex(dialog.strategy_combo.findData("dense_interval"))
    assert not dialog.window_size.isHidden()
    assert dialog.ewma_weight_percent.isHidden()
    assert not dialog.dense_interval_width.isHidden()

    dialog.strategy_combo.setCurrentIndex(dialog.strategy_combo.findData("trimmed_mean"))
    assert "不足 5 个有效轮次" in dialog.strategy_combo.toolTip()


@pytest.mark.parametrize(
    ("strategy", "description_fragment"),
    [
        ("fixed", "样本会继续保留"),
        ("last", "最近一次唯一候选"),
        ("mode", "累计权重最高"),
        ("median", "中间值"),
        ("mean", "平均值，每轮权重相同"),
        ("ema", "逐轮融合"),
        ("trimmed_mean", "两端各去掉一轮权重"),
        ("dense_interval", "最集中的候选"),
    ],
)
def test_delay_strategy_dialog_shows_effect_for_every_strategy(
    app,
    tmp_path,
    strategy: str,
    description_fragment: str,
):
    panel = AutoRngPanel(script_dir=tmp_path, settings=_settings(tmp_path / "descriptions.ini"))
    dialog = panel.delay_strategy_dialog

    dialog.strategy_combo.setCurrentIndex(dialog.strategy_combo.findData(strategy))

    assert description_fragment in dialog.strategy_description.text()


def test_delay_strategy_description_reserves_its_wrapped_text_height(app, tmp_path):
    panel = AutoRngPanel(script_dir=tmp_path, settings=_settings(tmp_path / "description-height.ini"))
    dialog = panel.delay_strategy_dialog
    dialog.show()
    app.processEvents()

    dialog.strategy_combo.setCurrentIndex(dialog.strategy_combo.findData("last"))
    app.processEvents()

    reserved_height = dialog.strategy_description.minimumHeight()
    dialog.strategy_description.setMinimumHeight(0)
    required_height = dialog.strategy_description.heightForWidth(dialog.strategy_description.width())
    dialog.strategy_description.setMinimumHeight(reserved_height)
    assert dialog.strategy_description.height() >= required_height + 1
    dialog.close()


def test_delay_strategy_numeric_fields_use_plain_c_locale_values(app, tmp_path):
    panel = AutoRngPanel(script_dir=tmp_path, settings=_settings(tmp_path / "auto-rng.ini"))
    dialog = panel.delay_strategy_dialog

    for field in (
        panel.fixed_delay,
        dialog.baseline_delay,
        dialog.window_size,
        dialog.ewma_weight_percent,
        dialog.dense_interval_width,
    ):
        assert field.locale().name() == "C"
        assert field.lineEdit().locale().name() == "C"
        assert field.suffix() == ""
    assert dialog._form_rows[dialog.baseline_delay].label.text() == "基准 delay"
    assert dialog._form_rows[dialog.ewma_weight_percent].label.text() == "最新样本权重"
    assert dialog._form_rows[dialog.dense_interval_width].label.text() == "密集区间跨度"


def test_delay_strategy_dialog_cancel_keeps_committed_configuration(app, tmp_path):
    settings = _settings(tmp_path / "auto-rng.ini")
    panel = AutoRngPanel(script_dir=tmp_path, settings=settings)

    def edit_then_cancel() -> None:
        dialog = panel.delay_strategy_dialog
        dialog.strategy_combo.setCurrentIndex(dialog.strategy_combo.findData("median"))
        dialog.baseline_delay.setValue(1450)
        dialog.window_size.setValue(9)
        dialog.weight_multi_candidate_button.click()
        dialog.reject()

    QTimer.singleShot(0, edit_then_cancel)
    panel.delay_settings_button.click()

    assert panel.delay_strategy_config().strategy is DelayStrategy.FIXED
    assert panel.fixed_delay.value() == 100
    assert panel.delay_settings_button.text() == "固定 delay · 下轮 100"
    assert not settings.contains("delay_strategy")


def test_delay_strategy_save_stays_open_and_close_actions_restore_latest_save(
    app,
    tmp_path,
):
    settings_path = tmp_path / "delay-save-flow.ini"
    settings = _settings(settings_path)
    panel = AutoRngPanel(script_dir=tmp_path, settings=settings)
    dialog = panel.delay_strategy_dialog
    expected = DelayStrategyConfig(
        strategy=DelayStrategy.MEDIAN,
        baseline_delay=1450,
        multi_candidate_policy=MultiCandidatePolicy.WEIGHTED,
        window_size=9,
    )

    def save_then_cancel_later_edit() -> None:
        dialog.strategy_combo.setCurrentIndex(dialog.strategy_combo.findData("median"))
        dialog.baseline_delay.setValue(1450)
        dialog.window_size.setValue(9)
        dialog.multi_candidate_widget.setCurrentIndex(
            dialog.multi_candidate_widget.findData("weighted")
        )
        dialog.ok_button.click()

        assert dialog.isVisible()
        assert panel.delay_strategy_config() == expected
        assert "已保存" in dialog.apply_status.text()

        dialog.strategy_combo.setCurrentIndex(dialog.strategy_combo.findData("mean"))
        dialog.baseline_delay.setValue(1499)
        dialog.cancel_button.click()

    QTimer.singleShot(0, save_then_cancel_later_edit)
    panel.delay_settings_button.click()
    assert panel.delay_strategy_config() == expected
    assert dialog.values() == expected

    def verify_cancel_rollback_then_close_with_x() -> None:
        assert dialog.values() == expected
        dialog.strategy_combo.setCurrentIndex(
            dialog.strategy_combo.findData("dense_interval")
        )
        dialog.baseline_delay.setValue(1600)
        dialog.close_button.click()

    QTimer.singleShot(0, verify_cancel_rollback_then_close_with_x)
    panel.delay_settings_button.click()
    assert panel.delay_strategy_config() == expected
    assert dialog.values() == expected

    def verify_x_rollback_on_next_open() -> None:
        assert dialog.values() == expected
        dialog.cancel_button.click()

    QTimer.singleShot(0, verify_x_rollback_on_next_open)
    panel.delay_settings_button.click()

    settings.sync()
    restored = AutoRngPanel(
        script_dir=tmp_path,
        settings=QSettings(str(settings_path), QSettings.Format.IniFormat),
    )
    assert restored.delay_strategy_config() == expected


def test_delay_strategy_dialog_accepts_and_persists_all_configuration(app, tmp_path):
    settings_path = tmp_path / "auto-rng.ini"
    settings = _settings(settings_path)
    panel = AutoRngPanel(script_dir=tmp_path, settings=settings)

    def edit_then_accept() -> None:
        dialog = panel.delay_strategy_dialog
        dialog.strategy_combo.setCurrentIndex(dialog.strategy_combo.findData("dense_interval"))
        dialog.baseline_delay.setValue(1450)
        dialog.window_size.setValue(12)
        dialog.ewma_weight_percent.setValue(35)
        dialog.dense_interval_width.setValue(3)
        dialog.weight_multi_candidate_button.click()
        dialog.accept()

    QTimer.singleShot(0, edit_then_accept)
    panel.delay_settings_button.click()
    settings.sync()

    restored = AutoRngPanel(
        script_dir=tmp_path,
        settings=QSettings(str(settings_path), QSettings.Format.IniFormat),
    )
    config = restored.delay_strategy_config()
    assert config.strategy is DelayStrategy.DENSE_INTERVAL
    assert config.baseline_delay == 1450
    assert config.multi_candidate_policy is MultiCandidatePolicy.WEIGHTED
    assert config.window_size == 12
    assert config.ewma_alpha == 0.35
    assert config.dense_interval_width == 3
    assert restored.fixed_delay.value() == 1450
    assert restored.delay_settings_button.text() == "密集区间 · 下轮 1450"
    built = restored.build_config()
    assert built.delay_strategy == "dense_interval"
    assert built.delay_multi_candidate_policy == "weighted"
    assert built.delay_sample_window == 12
    assert built.delay_ewma_alpha == 0.35
    assert built.delay_dense_interval_width == 3


def test_delay_samples_stay_grouped_persist_and_update_runtime_preview(app, tmp_path):
    settings_path = tmp_path / "auto-rng.ini"
    settings = _settings(settings_path)
    panel = AutoRngPanel(script_dir=tmp_path, settings=settings)

    def select_mean() -> None:
        dialog = panel.delay_strategy_dialog
        dialog.strategy_combo.setCurrentIndex(dialog.strategy_combo.findData("mean"))
        dialog.baseline_delay.setValue(1400)
        dialog.weight_multi_candidate_button.click()
        dialog.accept()

    QTimer.singleShot(0, select_mean)
    panel.delay_settings_button.click()
    panel.record_delay_sample([1450])
    panel.record_delay_sample([1453, 1451, 1451])
    panel.record_delay_sample([1452])
    panel.set_active_delay(1449)

    assert panel.delay_samples() == [(1450,), (1451, 1453), (1452,)]
    assert panel.effective_delay_for_next_round() == 1451
    assert panel.delay_settings_button.text() == "滚动平均值 · 下轮 1451"
    assert panel.delay_strategy_dialog.current_delay_value.text() == "1449"
    assert panel.delay_strategy_dialog.next_delay_value.text() == "1451"
    assert panel.delay_strategy_dialog.valid_sample_count.text() == "3 轮"
    assert panel.delay_strategy_dialog.valid_sample_count.isHidden()
    assert panel.delay_strategy_dialog.current_delay_note.text() == "本轮已锁定"
    assert (
        panel.delay_strategy_dialog.next_delay_note.text()
        == "滚动平均值 · 使用 3 个有效轮次"
    )
    assert panel.delay_strategy_dialog.recent_samples.text() == "1450 / 1451,1453 / 1452"

    settings.sync()
    restored = AutoRngPanel(
        script_dir=tmp_path,
        settings=QSettings(str(settings_path), QSettings.Format.IniFormat),
    )
    assert restored.delay_samples() == panel.delay_samples()
    assert restored.effective_delay_for_next_round() == 1451
    assert restored.build_config().delay_sample_rounds == ((1450,), (1451, 1453), (1452,))

    panel.clear_delay_samples()
    assert panel.delay_samples() == []
    assert panel.effective_delay_for_next_round() == 1400
    assert panel.delay_strategy_dialog.recent_samples.text() == "暂无样本"


def test_delay_sample_clear_button_uses_inline_confirmation(app, tmp_path):
    panel = AutoRngPanel(script_dir=tmp_path, settings=_settings(tmp_path / "auto-rng.ini"))
    panel.record_delay_sample([1452])
    dialog = panel.delay_strategy_dialog

    assert dialog.clear_confirm_frame.isHidden()
    dialog.clear_samples_button.click()
    assert not dialog.clear_confirm_frame.isHidden()
    assert panel.delay_samples() == [(1452,)]

    dialog.keep_samples_button.click()
    assert dialog.clear_confirm_frame.isHidden()
    assert panel.delay_samples() == [(1452,)]

    dialog.clear_samples_button.click()
    assert not dialog.clear_confirm_frame.isHidden()
    dialog.confirm_clear_button.click()
    assert dialog.clear_confirm_frame.isHidden()
    app.processEvents()
    assert panel.delay_samples() == []
