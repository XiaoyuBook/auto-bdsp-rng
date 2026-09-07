from __future__ import annotations

import pytest


pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, QRect, QSettings, Qt
from PySide6.QtWidgets import QApplication, QComboBox, QPushButton, QSpinBox

from auto_bdsp_rng.data import GameVersion, get_static_encounters
from auto_bdsp_rng.gen8_static import StateFilter
from auto_bdsp_rng.ui.auto_rng_panel import AutoRngPanel
from auto_bdsp_rng.ui.target_dialog import TargetDialog


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    application = QApplication.instance() or QApplication([])
    yield application
    for widget in application.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    application.processEvents()


def _shaymin_record():
    return next(
        record
        for record in get_static_encounters()
        if record.description == "Shaymin"
    )


def _show(dialog: TargetDialog, app: QApplication) -> None:
    dialog.show()
    for _ in range(4):
        app.processEvents()


def _widget_rect_in_dialog(dialog: TargetDialog, widget) -> QRect:
    return QRect(widget.mapTo(dialog, QPoint(0, 0)), widget.size())


def test_target_dialog_fits_canvas_and_keeps_complete_compact_form_visible(app):
    dialog = TargetDialog(version=GameVersion.BD)
    _show(dialog, app)

    assert dialog.objectName() == "TargetDialog"
    assert dialog.width() <= 1150
    assert dialog.height() <= 900
    assert dialog.rect().contains(_widget_rect_in_dialog(dialog, dialog.ok_button))
    assert dialog.rect().contains(_widget_rect_in_dialog(dialog, dialog.cancel_button))

    form = dialog.target_form
    required_controls = [
        form.category_combo,
        form.encounter_combo,
        form.level_display,
        form.template_ability_display,
        form.template_shiny_display,
        form.iv_count_display,
        *form.iv_min,
        *form.iv_max,
        form.ability_filter,
        form.gender_filter,
        form.nature_combo,
        form.shiny_filter,
        form.height_min,
        form.height_max,
        form.weight_min,
        form.weight_max,
        form.skip_filter,
    ]
    assert all(control.isVisible() for control in required_controls)
    assert all(
        30 <= control.height() <= 34
        for control in required_controls
        if isinstance(control, (QComboBox, QSpinBox))
    )
    assert not form.show_stats_check.isVisible()
    assert not form.iv_calculator_button.isVisible()
    assert dialog.target_scroll.maximumHeight() <= 220

    stylesheet = dialog.styleSheet().lower()
    assert "#087c58" in stylesheet
    assert "#f6f8f7" in stylesheet
    assert "#e2e8e4" in stylesheet


def test_adding_and_removing_target_conditions_updates_lock_and_indexes(app):
    dialog = TargetDialog(version=GameVersion.BD)
    _show(dialog, app)

    dialog.add_button.click()
    dialog.target_form.height_min.setValue(7)
    dialog.add_button.click()
    app.processEvents()

    assert len(dialog.get_targets()) == 2
    assert [entry.index_label.text() for entry in dialog._entries] == ["01", "02"]
    assert dialog.target_count_label.text() == "2 组条件"
    assert not dialog.target_form.category_combo.isEnabled()
    assert not dialog.target_form.encounter_combo.isEnabled()
    assert dialog.get_targets()[0][1].height_min == 0
    assert dialog.get_targets()[1][1].height_min == 7

    dialog._entries[0].delete_button.click()
    app.processEvents()

    assert len(dialog.get_targets()) == 1
    assert dialog._entries[0].index_label.text() == "01"
    assert dialog.get_targets()[0][1].height_min == 7

    dialog._entries[0].delete_button.click()
    app.processEvents()

    assert dialog.get_targets() == []
    assert dialog.empty_label.isVisible()
    assert dialog.target_form.category_combo.isEnabled()
    assert dialog.target_form.encounter_combo.isEnabled()


def test_set_targets_replaces_entries_locks_species_and_preserves_or_list(app):
    record = _shaymin_record()
    original = [
        (record, StateFilter(height_min=1, height_max=3), "any"),
        (record, StateFilter(weight_min=4, weight_max=8, shiny=2), "square"),
    ]
    dialog = TargetDialog(version=GameVersion.BD)
    dialog.set_targets(original)
    _show(dialog, app)

    assert dialog.get_targets() == original
    assert dialog.target_form.encounter_combo.currentData().description == "Shaymin"
    assert not dialog.target_form.category_combo.isEnabled()
    assert not dialog.target_form.encounter_combo.isEnabled()

    replacement = [(record, StateFilter(ability=1), "none")]
    dialog.set_targets(replacement)

    assert dialog.get_targets() == replacement
    assert [entry.index_label.text() for entry in dialog._entries] == ["01"]


def test_non_shiny_filter_uses_state_filter_zero(app):
    dialog = TargetDialog(version=GameVersion.BD)
    form = dialog.target_form
    form.shiny_filter.setCurrentIndex(form.shiny_filter.findData("none"))

    state_filter, shiny_mode = form.current_filter()

    assert shiny_mode == "none"
    assert state_filter.shiny == 0


def test_confirm_returns_dialog_targets_and_cancel_does_not_change_caller_list(app):
    record = _shaymin_record()
    caller_targets = [(record, StateFilter(), "any")]

    accepted = TargetDialog(version=GameVersion.BD)
    accepted.set_targets(caller_targets)
    accepted.target_form.height_min.setValue(11)
    accepted.add_button.click()
    accepted.ok_button.click()

    assert accepted.result() == accepted.DialogCode.Accepted
    assert len(accepted.get_targets()) == 2
    assert accepted.get_targets()[1][1].height_min == 11

    cancelled = TargetDialog(version=GameVersion.BD)
    cancelled.set_targets(caller_targets)
    cancelled.target_form.weight_min.setValue(12)
    cancelled.add_button.click()
    cancelled.cancel_button.click()

    assert cancelled.result() == cancelled.DialogCode.Rejected
    assert caller_targets == [(record, StateFilter(), "any")]


def test_auto_rng_panel_rejected_target_draft_preserves_runtime_and_settings(
    app,
    tmp_path,
    monkeypatch,
):
    settings_path = tmp_path / "target-cancel.ini"
    settings = QSettings(str(settings_path), QSettings.Format.IniFormat)
    settings.clear()
    panel = AutoRngPanel(script_dir=tmp_path, settings=settings)
    record = _shaymin_record()
    original_targets = [(record, StateFilter(height_min=3, height_max=8), "any")]
    panel.set_targets(original_targets)
    species_id = int(record.template.species)
    panel.set_active_delay(1450, species_id=species_id)
    panel._save_panel_state()
    settings.sync()

    summary_before = panel.target_summary_text()
    runtime_before = (
        panel._delay_species_id,
        panel._active_delay,
        dict(panel._active_delay_by_species),
        panel.delay_strategy_config(),
        panel.delay_sample_records(),
    )
    targets_json_before = settings.value("target_list_json")
    profiles_json_before = settings.value("delay_profiles_json")

    def reject_changed_draft(dialog: TargetDialog):
        dialog.target_form.height_min.setValue(11)
        dialog.add_button.click()
        dialog._entries[0].delete_button.click()
        assert dialog.get_targets()[0][1].height_min == 11
        return dialog.DialogCode.Rejected

    monkeypatch.setattr(TargetDialog, "exec", reject_changed_draft)
    panel.open_target_dialog()
    settings.sync()

    assert panel.targets() == original_targets
    assert panel.target_summary_text() == summary_before
    assert (
        panel._delay_species_id,
        panel._active_delay,
        dict(panel._active_delay_by_species),
        panel.delay_strategy_config(),
        panel.delay_sample_records(),
    ) == runtime_before
    assert settings.value("target_list_json") == targets_json_before
    assert settings.value("delay_profiles_json") == profiles_json_before


def test_auto_rng_panel_applies_accepted_target_draft(app, tmp_path, monkeypatch):
    settings = QSettings(str(tmp_path / "target-accept.ini"), QSettings.Format.IniFormat)
    settings.clear()
    panel = AutoRngPanel(script_dir=tmp_path, settings=settings)
    record = _shaymin_record()
    panel.set_targets([(record, StateFilter(), "any")])

    def accept_changed_draft(dialog: TargetDialog):
        dialog.target_form.height_min.setValue(11)
        dialog.add_button.click()
        return dialog.DialogCode.Accepted

    monkeypatch.setattr(TargetDialog, "exec", accept_changed_draft)
    panel.open_target_dialog()

    assert len(panel.targets()) == 2
    assert panel.targets()[1][1].height_min == 11
    assert panel.target_count_label.text() == "2 组目标条件"


def test_many_targets_scroll_without_covering_footer(app):
    record = _shaymin_record()
    dialog = TargetDialog(version=GameVersion.BD)
    dialog.set_targets(
        [
            (record, StateFilter(height_min=index, height_max=index), "any")
            for index in range(12)
        ]
    )
    _show(dialog, app)

    assert len(dialog._entries) == 12
    assert dialog.target_scroll.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
    assert dialog.target_scroll.verticalScrollBar().maximum() > 0
    assert dialog.target_scroll.height() <= dialog.target_scroll.maximumHeight()
    assert dialog.rect().contains(_widget_rect_in_dialog(dialog, dialog.ok_button))
    assert dialog.ok_button.isVisible()
    assert dialog.cancel_button.isVisible()
    assert all(isinstance(entry.delete_button, QPushButton) for entry in dialog._entries)
