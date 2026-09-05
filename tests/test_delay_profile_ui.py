from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, QRect, QSettings
from PySide6.QtWidgets import QApplication, QTableWidget, QToolButton

from auto_bdsp_rng.automation.auto_rng.delay_strategy import (
    DelayStrategy,
    DelayStrategyConfig,
    MultiCandidatePolicy,
)
from auto_bdsp_rng import data as data_module
from auto_bdsp_rng.data import StaticEncounterRecord, get_static_encounters
from auto_bdsp_rng.gen8_static import StateFilter
from auto_bdsp_rng.ui.auto_rng_panel import AutoRngPanel
from auto_bdsp_rng.ui.main_window import MainWindow


DIALGA_SPECIES = 483
PALKIA_SPECIES = 484


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    application = QApplication.instance() or QApplication([])
    yield application
    for widget in application.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    application.processEvents()


@pytest.fixture
def species_records() -> dict[int, StaticEncounterRecord]:
    wanted = {DIALGA_SPECIES, PALKIA_SPECIES}
    records = {
        record.template.species: record
        for record in get_static_encounters()
        if record.template.species in wanted
    }
    assert set(records) == wanted
    return records


def _new_settings(path: Path) -> QSettings:
    settings = QSettings(str(path), QSettings.Format.IniFormat)
    settings.clear()
    return settings


def _select_species(panel: AutoRngPanel, record: StaticEncounterRecord) -> None:
    panel.set_targets([(record, StateFilter(), "any")])


def _commit_config(panel: AutoRngPanel, config: DelayStrategyConfig) -> None:
    panel._commit_delay_strategy_config(config, persist=True, emit=False)


def _row_for_round(table: QTableWidget, round_number: int) -> int:
    expected = f"第 {round_number} 轮"
    for row in range(table.rowCount()):
        item = table.item(row, 0)
        if item is not None and item.text().splitlines()[0] == expected:
            return row
    raise AssertionError(f"round {round_number} is not visible in the table")


def test_delay_refreshes_do_not_accumulate_description_height(app, tmp_path):
    panel = AutoRngPanel(script_dir=tmp_path, settings=_new_settings(tmp_path / "refresh.ini"))
    MainWindow._apply_theme(panel)
    dialog = panel.delay_strategy_dialog
    dialog.show()
    app.processEvents()
    dialog._resize_for_current_page()
    initial_height = dialog.strategy_description.minimumHeight()
    initial_body_height = dialog.body_widget.sizeHint().height()

    for _ in range(12):
        dialog._resize_for_current_page()
        app.processEvents()

    assert dialog.strategy_description.minimumHeight() == initial_height
    assert dialog.body_widget.sizeHint().height() == initial_body_height


def test_delay_dialog_has_no_transparent_outer_shell(app, tmp_path):
    panel = AutoRngPanel(script_dir=tmp_path, settings=_new_settings(tmp_path / "window-shell.ini"))
    MainWindow._apply_theme(panel)
    dialog = panel.delay_strategy_dialog
    dialog.show()
    app.processEvents()

    assert dialog.surface.geometry() == dialog.rect()
    snapshot = dialog.grab().toImage()
    for x, y in (
        (0, 0),
        (snapshot.width() - 1, 0),
        (0, snapshot.height() - 1),
        (snapshot.width() - 1, snapshot.height() - 1),
    ):
        assert snapshot.pixelColor(x, y).alpha() == 255


def test_full_history_remains_usable_on_a_short_screen(app, tmp_path, monkeypatch):
    panel = AutoRngPanel(script_dir=tmp_path, settings=_new_settings(tmp_path / "short-screen.ini"))
    MainWindow._apply_theme(panel)
    for offset in range(12):
        panel.record_delay_sample([1450 + offset], observed_at="2026-09-05T19:42:18+08:00")
    dialog = panel.delay_strategy_dialog
    work_area = QRect(0, 0, 1280, 720)

    class Screen:
        def availableGeometry(self):
            return work_area

    monkeypatch.setattr(dialog, "screen", lambda: Screen())
    dialog.show()
    dialog.show_sample_time_check.setChecked(True)
    dialog.view_all_samples_button.click()
    app.processEvents()
    dialog._resize_for_current_page()
    app.processEvents()

    history = dialog.history_page
    assert dialog.height() <= work_area.height()
    assert work_area.contains(dialog.geometry())
    assert history.table.rowCount() == 10
    for button in (history.back_button, history.previous_button, history.next_button):
        button_rect = QRect(button.mapTo(dialog, QPoint()), button.size())
        assert dialog.rect().contains(button_rect)
    assert history.table.verticalScrollBar().maximum() > 0
    history.table.scrollToBottom()
    app.processEvents()
    last_row = history.table.visualItemRect(history.table.item(9, 0))
    assert history.table.viewport().rect().contains(last_row)

    history.next_button.click()
    app.processEvents()
    assert history.table.rowCount() == 2
    assert history.previous_button.isEnabled()
    assert not history.next_button.isEnabled()


def test_species_profiles_are_isolated_and_survive_restart(
    app,
    tmp_path,
    species_records,
):
    settings_path = tmp_path / "species-profiles.ini"
    settings = _new_settings(settings_path)
    panel = AutoRngPanel(script_dir=tmp_path, settings=settings)

    dialga_config = DelayStrategyConfig(
        strategy=DelayStrategy.MEDIAN,
        baseline_delay=1450,
        multi_candidate_policy=MultiCandidatePolicy.WEIGHTED,
        window_size=3,
    )
    _select_species(panel, species_records[DIALGA_SPECIES])
    _commit_config(panel, dialga_config)
    panel.record_delay_sample(
        [1451],
        observed_at="2026-09-05T19:42:18+08:00",
    )
    panel.record_delay_sample(
        [1453],
        observed_at="2026-09-05T19:48:20+08:00",
    )

    palkia_config = DelayStrategyConfig(
        strategy=DelayStrategy.LAST,
        baseline_delay=1510,
    )
    _select_species(panel, species_records[PALKIA_SPECIES])
    _commit_config(panel, palkia_config)
    panel.record_delay_sample(
        [1512],
        observed_at="2026-09-05T20:01:02+08:00",
    )

    _select_species(panel, species_records[DIALGA_SPECIES])
    assert panel.delay_strategy_config() == dialga_config
    assert [sample.candidates for sample in panel.delay_sample_records()] == [
        (1451,),
        (1453,),
    ]
    assert panel.effective_delay_for_next_round() == 1452

    _select_species(panel, species_records[PALKIA_SPECIES])
    assert panel.delay_strategy_config() == palkia_config
    assert [sample.candidates for sample in panel.delay_sample_records()] == [(1512,)]
    assert panel.effective_delay_for_next_round() == 1512

    panel._save_panel_state()
    settings.sync()
    restored = AutoRngPanel(
        script_dir=tmp_path,
        settings=QSettings(str(settings_path), QSettings.Format.IniFormat),
    )

    assert restored.delay_strategy_config() == palkia_config
    assert [sample.candidates for sample in restored.delay_sample_records()] == [(1512,)]
    assert restored.delay_strategy_dialog.species_name_label.text() == "帕路奇亚"
    _select_species(restored, species_records[DIALGA_SPECIES])
    assert restored.delay_strategy_config() == dialga_config
    assert [sample.round_number for sample in restored.delay_sample_records()] == [1, 2]
    assert restored.delay_strategy_dialog.species_name_label.text() == "帝牙卢卡"


def test_round_metadata_and_soft_exclusion_restore_through_the_table(
    app,
    tmp_path,
    species_records,
):
    settings_path = tmp_path / "sample-metadata.ini"
    settings = _new_settings(settings_path)
    panel = AutoRngPanel(script_dir=tmp_path, settings=settings)
    _select_species(panel, species_records[DIALGA_SPECIES])
    _commit_config(
        panel,
        DelayStrategyConfig(
            strategy=DelayStrategy.ROLLING_MEAN,
            baseline_delay=1450,
            window_size=5,
        ),
    )
    timestamps = (
        "2026-09-05T19:30:00+08:00",
        "2026-09-05T19:35:52+08:00",
        "2026-09-05T19:42:18+08:00",
    )
    for value, observed_at in zip((1450, 1678, 1452), timestamps):
        panel.record_delay_sample([value], observed_at=observed_at)

    panel.set_delay_sample_excluded(2, True)
    records = panel.delay_sample_records()
    assert [sample.round_number for sample in records] == [1, 2, 3]
    assert records[1].candidates == (1678,)
    assert records[1].observed_at == timestamps[1]
    assert records[1].excluded

    dialog = panel.delay_strategy_dialog
    dialog.show_sample_time_check.setChecked(True)
    app.processEvents()
    row = _row_for_round(dialog.recent_samples_table, 2)
    assert dialog.recent_samples_table.item(row, 0).text().splitlines() == [
        "第 2 轮",
        "2026-09-05",
        "19:35:52",
    ]
    assert all(
        dialog.recent_samples_table.item(row, column).font().strikeOut()
        for column in range(3)
    )
    restore_button = dialog.recent_samples_table.cellWidget(row, 3)
    assert isinstance(restore_button, QToolButton)
    assert restore_button.text() == ""
    assert not restore_button.icon().isNull()
    assert restore_button.accessibleName() == "恢复第 2 轮样本"
    assert restore_button.toolTip() == "恢复第 2 轮样本"
    assert not restore_button.font().strikeOut()
    assert "已删除 1 轮" in dialog.sample_summary_label.text()

    panel._save_panel_state()
    settings.sync()
    restored = AutoRngPanel(
        script_dir=tmp_path,
        settings=QSettings(str(settings_path), QSettings.Format.IniFormat),
    )
    restored_records = restored.delay_sample_records()
    assert [sample.round_number for sample in restored_records] == [1, 2, 3]
    assert restored_records[1].observed_at == timestamps[1]
    assert restored_records[1].excluded

    restored_dialog = restored.delay_strategy_dialog
    restored_row = _row_for_round(restored_dialog.recent_samples_table, 2)
    restored_button = restored_dialog.recent_samples_table.cellWidget(restored_row, 3)
    assert isinstance(restored_button, QToolButton)
    restored_button.click()
    app.processEvents()

    restored_records = restored.delay_sample_records()
    assert [sample.round_number for sample in restored_records] == [1, 2, 3]
    assert restored_records[1].observed_at == timestamps[1]
    assert not restored_records[1].excluded
    restored_row = _row_for_round(restored_dialog.recent_samples_table, 2)
    assert not restored_dialog.recent_samples_table.item(restored_row, 1).font().strikeOut()
    delete_button = restored_dialog.recent_samples_table.cellWidget(restored_row, 3)
    assert isinstance(delete_button, QToolButton)
    assert delete_button.accessibleName() == "删除第 2 轮样本"


def test_excluding_and_clearing_samples_preserves_active_delay_and_other_species(
    app,
    tmp_path,
    species_records,
):
    panel = AutoRngPanel(
        script_dir=tmp_path,
        settings=_new_settings(tmp_path / "clear-current-species.ini"),
    )
    dialga_config = DelayStrategyConfig(
        strategy=DelayStrategy.ROLLING_MEAN,
        baseline_delay=1450,
        window_size=5,
    )
    _select_species(panel, species_records[DIALGA_SPECIES])
    _commit_config(panel, dialga_config)
    panel.record_delay_sample([1450], observed_at="2026-09-05T19:30:00+08:00")
    panel.record_delay_sample([1700], observed_at="2026-09-05T19:35:00+08:00")
    panel.set_active_delay(1450)

    assert panel.effective_delay_for_next_round() == 1575
    assert panel.delay_strategy_dialog.current_delay_value.text() == "1450"
    panel.set_delay_sample_excluded(2, True)
    assert panel.effective_delay_for_next_round() == 1450
    assert panel.delay_strategy_dialog.current_delay_value.text() == "1450"
    assert panel.delay_strategy_dialog.next_delay_value.text() == "1450"

    palkia_config = DelayStrategyConfig(
        strategy=DelayStrategy.MEDIAN,
        baseline_delay=1510,
    )
    _select_species(panel, species_records[PALKIA_SPECIES])
    _commit_config(panel, palkia_config)
    panel.record_delay_sample([1511], observed_at="2026-09-05T20:00:00+08:00")

    _select_species(panel, species_records[DIALGA_SPECIES])
    assert panel.delay_strategy_dialog.clear_samples_button.text() == "清空当前精灵样本"
    panel.clear_delay_samples()
    assert panel.delay_sample_records(DIALGA_SPECIES) == []
    assert panel.delay_strategy_config() == dialga_config
    assert [sample.candidates for sample in panel.delay_sample_records(PALKIA_SPECIES)] == [
        (1511,),
    ]

    _select_species(panel, species_records[PALKIA_SPECIES])
    assert panel.delay_strategy_config() == palkia_config
    assert panel.effective_delay_for_next_round() == 1511


def test_recommended_delay_is_optional_and_only_updates_the_dialog_draft(
    app,
    tmp_path,
    species_records,
    monkeypatch,
):
    monkeypatch.setitem(
        data_module.RECOMMENDED_DELAY_BY_SPECIES,
        DIALGA_SPECIES,
        1450,
    )
    panel = AutoRngPanel(
        script_dir=tmp_path,
        settings=_new_settings(tmp_path / "recommendation.ini"),
    )

    _select_species(panel, species_records[DIALGA_SPECIES])
    dialog = panel.delay_strategy_dialog
    committed = panel.delay_strategy_config()
    assert dialog.species_name_label.text() == "帝牙卢卡"
    assert dialog.recommendation_label.text() == "推荐 delay：1450"
    assert not dialog.use_recommendation_button.isHidden()
    assert dialog.use_recommendation_button.isEnabled()

    dialog.use_recommendation_button.click()
    app.processEvents()
    assert dialog.baseline_delay.value() == 1450
    assert panel.delay_strategy_config() == committed
    assert "保存后生效" in dialog.apply_status.text()

    _select_species(panel, species_records[PALKIA_SPECIES])
    assert dialog.species_name_label.text() == "帕路奇亚"
    assert dialog.recommendation_label.text() == "推荐 delay：暂无推荐"
    assert dialog.use_recommendation_button.isHidden()
    assert panel.delay_strategy_config().baseline_delay == DelayStrategyConfig().baseline_delay


def test_repainting_sample_tables_keeps_only_current_action_buttons(
    app,
    tmp_path,
    species_records,
):
    panel = AutoRngPanel(
        script_dir=tmp_path,
        settings=_new_settings(tmp_path / "sample-actions.ini"),
    )
    _select_species(panel, species_records[DIALGA_SPECIES])
    for offset in range(12):
        panel.record_delay_sample(
            [1450 + offset],
            observed_at=f"2026-09-05T19:{offset:02d}:00+08:00",
        )

    dialog = panel.delay_strategy_dialog
    assert dialog.samples_toggle.isChecked()
    dialog.show()
    app.processEvents()
    assert dialog.sample_details.isVisible()
    panel.set_delay_sample_excluded(11, True)
    dialog.show_sample_time_check.setChecked(True)
    app.processEvents()

    table = dialog.recent_samples_table
    visible_actions = [
        button
        for button in table.findChildren(QToolButton)
        if button.objectName() == "DelaySampleActionButton" and button.isVisible()
    ]
    current_actions = [table.cellWidget(row, 3) for row in range(table.rowCount())]

    assert len(visible_actions) == table.rowCount() == 5
    assert set(visible_actions) == set(current_actions)
    assert all(button.pos().x() > 0 for button in visible_actions)


def test_corrupt_target_list_activates_default_species_without_migrating_samples(
    app,
    tmp_path,
):
    settings = _new_settings(tmp_path / "corrupt-target-list.ini")
    settings.setValue("target_list_json", "not valid json")
    settings.setValue("delay_sample_rounds_json", "[[9999]]")
    settings.setValue("fixed_delay", 1450)

    panel = AutoRngPanel(script_dir=tmp_path, settings=settings)
    default_species = int(panel.targets()[0][0].template.species)

    assert panel._delay_species_id == default_species
    assert panel.delay_strategy_config().baseline_delay == 1450
    assert panel.delay_sample_records() == []

    panel.record_delay_sample(
        [1451],
        observed_at="2026-09-05T19:42:18+08:00",
    )
    assert [sample.candidates for sample in panel.delay_sample_records()] == [(1451,)]
    assert panel.delay_sample_records()[0].round_number == 1


def test_reopening_full_history_returns_to_the_newest_page(
    app,
    tmp_path,
    species_records,
):
    panel = AutoRngPanel(
        script_dir=tmp_path,
        settings=_new_settings(tmp_path / "history-page.ini"),
    )
    _select_species(panel, species_records[DIALGA_SPECIES])
    for offset in range(12):
        panel.record_delay_sample(
            [1450 + offset],
            observed_at=f"2026-09-05T19:{offset:02d}:00+08:00",
        )

    dialog = panel.delay_strategy_dialog
    history = dialog.history_page
    dialog.baseline_delay.setValue(1499)
    dialog.show_sample_time_check.setChecked(True)
    dialog.show()
    app.processEvents()

    assert dialog.page_stack.currentWidget() is dialog.settings_page
    assert dialog.samples_toggle.isChecked()
    assert dialog.sample_details.isVisible()
    assert dialog.page_stack.indexOf(history) >= 0
    assert history.window() is dialog

    dialog.view_all_samples_button.click()
    app.processEvents()

    assert dialog.isVisible()
    assert dialog.page_stack.currentWidget() is history
    assert dialog.title_label.text() == "delay 样本记录"
    assert history._page_index == 0
    assert history.page_summary.text() == "第 1–10 条，共 12 条"
    assert history.show_time_check.isChecked()
    assert history.table.item(0, 0).text().splitlines() == [
        "第 12 轮",
        "2026-09-05",
        "19:11:00",
    ]

    history.next_button.click()
    app.processEvents()
    assert history._page_index == 1
    assert history.page_summary.text() == "第 11–12 条，共 12 条"

    history.back_button.click()
    app.processEvents()
    assert dialog.page_stack.currentWidget() is dialog.settings_page
    assert dialog.title_label.text() == "delay 策略设置"
    assert dialog.baseline_delay.value() == 1499
    assert dialog.show_sample_time_check.isChecked()
    assert dialog.samples_toggle.isChecked()
    assert dialog.sample_details.isVisible()

    dialog.view_all_samples_button.click()
    app.processEvents()

    assert dialog.page_stack.currentWidget() is history
    assert history._page_index == 0
    assert history.page_summary.text() == "第 1–10 条，共 12 条"
    assert history.table.item(0, 0).text().splitlines()[0] == "第 12 轮"
