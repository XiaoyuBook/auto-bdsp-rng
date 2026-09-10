import csv
from dataclasses import replace

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog

from auto_bdsp_rng.data import get_static_encounters
from auto_bdsp_rng.gen8_static import State8, StateFilter
from auto_bdsp_rng.rng_core import SeedPair64
from auto_bdsp_rng.ui import main_window as mw
from tests.test_start_readiness import window


def populate(window):
    record = next(r for r in get_static_encounters() if r.description == "Shaymin")
    states = [
        State8(advances=n, ec=n, sidtid=0, pid=n + 1, ivs=ivs, ability=0,
               gender=2, level=50, nature=nature, shiny=0, height=120, weight=80)
        for n, ivs, nature in (
            (10, (31, 31, 0, 15, 20, 7), 5),
            (20, (0, 20, 31, 2, 4, 31), 1),
            (30, (15, 25, 12, 31, 0, 16), 0),
        )
    ]
    window.tabs.setCurrentWidget(window.bdsp_tab)
    window._finish_static_generation(record, states)
    return record, states


def test_toggle_preserves_cells_and_displays_real_stats_in_all_six_columns(window):
    w = window
    _, states = populate(w)
    cells = [[w.table.item(r, c) for c in range(16)] for r in range(3)]
    w.table.setCurrentCell(1, 8)
    expected = [(175, 108, 115, 112, 115, 108),
                (160, 126, 108, 106, 107, 120),
                (167, 117, 111, 120, 105, 113)]
    for show_stats in (True, False, True, False):
        w.show_stats_check.setChecked(show_stats)
        assert w.table.currentItem() is cells[1][8]
        for r, state in enumerate(states):
            values = expected[r] if show_stats else state.ivs
            assert [w.table.item(r, c).text() for c in range(7, 13)] == list(map(str, values))
            assert all(w.table.item(r, c) is cells[r][c] for c in range(16))
        assert w.table.horizontalHeaderItem(7).text() == ("HP能力" if show_stats else "HP")


def test_toggle_resorts_values_and_keeps_selection_pinned_view_and_copy(window):
    w = window
    populate(w)
    tools = w.static_table_tools
    tools.set_pinned(True)
    tools.set_column_visible(9, False)
    tools._sort_clicked(8)
    assert [w.table.item(r, 0).text() for r in range(3)] == ["20", "30", "10"]
    selected = w.table.item(2, 8)
    w.table.setCurrentItem(selected)
    w.show_stats_check.setChecked(True)
    QApplication.processEvents()
    assert [w.table.item(r, 0).text() for r in range(3)] == ["10", "30", "20"]
    assert w.table.currentItem() is selected and selected.isSelected()
    assert w.table.isColumnHidden(9)
    assert tools.frozen.model().data(tools.frozen.model().index(0, 8)) == "108"
    assert tools.frozen.selectionModel().isSelected(w.table.model().index(0, 8))
    assert tools.frozen.columnWidth(0) == w.table.columnWidth(0)
    copied = tools.selected_text().splitlines()
    assert len(copied) == 2
    assert copied[1].split("\t")[7:13] == ["175", "108", "115", "112", "115", "108"]
    # Prefix search must see the displayed stats, then the IVs after switching back.
    QTest.keyClicks(w.table, "126")
    assert w.table.currentItem().text() == "126"
    w.show_stats_check.setChecked(False)
    assert [w.table.item(r, 0).text() for r in range(3)] == ["20", "30", "10"]
    assert w.table.currentItem().text() == "20"


def test_new_results_and_language_use_current_record_without_stale_stats(window):
    w = window
    w.show_stats_check.setChecked(True)  # Also works before any results exist.
    record, states = populate(w)
    assert w.table.item(0, 7).text() == "175"
    w.lang = "en"
    w._refresh_result_columns()
    assert w.table.horizontalHeaderItem(7).text() == "HP Stat"
    assert w.table.item(0, 7).text() == "175"
    other_record = next(r for r in get_static_encounters() if r.species_info.stats != record.species_info.stats)
    new_state = replace(states[0], level=other_record.template.level)
    w._finish_static_generation(other_record, [new_state])
    assert [w.table.item(0, c).text() for c in range(7, 13)] == list(map(str, w._stat_values(new_state)))
    w.show_stats_check.setChecked(False)
    assert [w.table.item(0, c).text() for c in range(7, 13)] == list(map(str, new_state.ivs))
    w._finish_static_generation(other_record, [])
    w.show_stats_check.setChecked(True)
    assert w.table.rowCount() == 0


def test_full_exports_follow_display_mode_and_keep_generation_order(window, monkeypatch, tmp_path):
    w = window
    _, states = populate(w)
    w.static_table_tools._sort_clicked(8)
    for show_stats in (True, False):
        w.show_stats_check.setChecked(show_stats)
        expected = [w._result_headers(), *(w._state_row(s) for s in states)]
        assert [line.split("\t") for line in w._table_text().splitlines()] == expected
        output = tmp_path / "stats.csv"
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (str(output), ""))
        w.export_results()
        with output.open(encoding="utf-8", newline="") as handle:
            assert list(csv.reader(handle)) == expected


def test_failed_auto_sync_keeps_old_results_bound_to_their_species(window, monkeypatch):
    w = window
    record, states = populate(w)
    other_record = next(r for r in get_static_encounters() if r.species_info.stats != record.species_info.stats)
    w.auto_rng_tab.set_targets([(other_record, StateFilter(), "any")])
    def fail(*args, **kwargs):
        raise RuntimeError("generation failed")
    errors = []
    monkeypatch.setattr(mw, "generate_static_candidates", fail)
    monkeypatch.setattr(w, "_show_error", lambda *args: errors.append(args))
    w._sync_bdsp_data_from_auto_rng(SeedPair64(1, 2))
    assert errors
    assert w._states is states and w._active_record is record
    w.show_stats_check.setChecked(True)
    assert w.table.item(0, 7).text() == "175"
    assert w._table_text().splitlines()[1].split("\t")[7] == "175"
