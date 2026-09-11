import json
from types import SimpleNamespace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from auto_bdsp_rng.ui.table_workbench import IDENTITY_ROLE, ResultItem
from auto_bdsp_rng.automation.auto_tid_rng import AutoTidRngProgress, AutoTidRngPhase
from tests.test_start_readiness import window


def states():
    return [SimpleNamespace(advances=n, tid=n + 3, sid=n + 4, tsv=n + 5, display_tid=d)
            for n, d in ((100, 2), (9, 100), (20, 1))]


def test_tid_numeric_sort_retains_target_identity_and_export_order(window):
    p = window.auto_tid_rng_tab
    source = states()
    p.set_id_states(source)
    p._target_key = (100, 2)
    p._highlight_target()
    original = p._table_text()
    p.id_table_tools._sort_clicked(4)
    assert [p.id_table.item(r, 4).text() for r in range(3)] == ["000001", "000002", "000100"]
    assert p._highlighted_target_row == 1
    assert p.id_table.item(1, 0).text() == "100"
    assert p._table_text() == original
    p.id_table_tools.set_column_visible(4, False)
    p._locate_target()
    assert p.id_table.currentRow() == 1 and not p.id_table.isColumnHidden(4)
    # A subsequent batch is populated with sorting suspended, keeping whole rows together.
    p.set_id_states(source)
    assert p.id_table.item(1, 0).text() == "100"
    assert tuple(p.id_table.item(1, 0).data(IDENTITY_ROLE)) == (100, 2)


def test_selected_rows_include_hidden_columns_and_preserve_display_tid(window):
    p = window.auto_tid_rng_tab
    p.set_id_states(states())
    p.id_table_tools._sort_clicked(0)
    p.id_table_tools.set_column_visible(2, False)
    p.id_table.item(0, 0).setSelected(True)
    p.id_table.item(2, 4).setSelected(True)
    text = p.id_table_tools.selected_text()
    assert text.splitlines()[1].split("\t") == ["9", "12", "13", "14", "000100", "—", "—"]
    assert len(text.splitlines()) == 3
    assert text.splitlines()[2].split("\t")[4] == "000002"
    assert p.id_table_tools.copy_button.isEnabled()


def test_frozen_column_shares_selection_and_preferences(window):
    # Column preferences remain supported on the static result page.
    table = window.table
    table.setRowCount(1)
    tools = window.static_table_tools
    tools.set_pinned(True)
    tools.refresh_frozen()
    assert tools.frozen.model() is table.model()
    assert tools.frozen.selectionModel() is table.selectionModel()
    assert tools.frozen.columnWidth(0) == table.columnWidth(0)
    assert tools.frozen.isColumnHidden(1)
    tools.set_column_visible(3, False)
    tools.pin_enabled = False
    table.setColumnHidden(3, False)
    tools.restore()
    assert tools.pin_enabled and table.isColumnHidden(3)


def test_result_item_sorts_hex_and_numeric_by_value():
    assert ResultItem("9") < ResultItem("100")
    assert ResultItem("00000099", sort_value=0x99) < ResultItem("000000A0", sort_value=0xA0)


def test_filter_schemes_round_trip_without_changing_seed_or_running(window, monkeypatch):
    w = window
    before_seed = [box.text() for box in w.bdsp_seed64_inputs]
    w.iv_min[0].setText("23")
    w.height_max.setText("170")
    w.shiny_filter.setCurrentIndex(w.shiny_filter.findData("none"))
    expected = w._current_filter()
    w.filter_presets.save_named("自己的筛选")
    w.iv_min[0].setText("0")
    w.height_max.setText("255")
    def forbidden(*a):
        raise AssertionError("Applying a preset must not run generation")
    monkeypatch.setattr(w, "generate_results", forbidden)
    w.filter_presets.apply_named("自己的筛选")
    assert w._current_filter() == expected
    assert [box.text() for box in w.bdsp_seed64_inputs] == before_seed


def test_invalid_scheme_is_rejected_before_any_form_changes(window):
    import pytest
    button = window.filter_presets
    before = button.values()
    bad = dict(before, iv_min_0=31, iv_max_0=10)
    button.settings.setValue("filter_presets/v1", json.dumps({"broken": bad}))
    with pytest.raises(ValueError):
        button.apply_named("broken")
    assert button.values() == before
