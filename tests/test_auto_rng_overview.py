from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from auto_bdsp_rng.automation.auto_rng.models import AutoRngPhase, AutoRngProgress
from auto_bdsp_rng.ui.auto_rng_panel import AutoRngPanel


@pytest.fixture
def panel(tmp_path):
    app = QApplication.instance() or QApplication([])
    widget = AutoRngPanel(script_dir=tmp_path, settings=QSettings(
        str(tmp_path / "overview.ini"), QSettings.Format.IniFormat))
    widget.resize(820, 700)
    widget.show()
    app.processEvents()
    yield widget
    widget.runtime_dialog.close()
    widget.close()
    widget.deleteLater()
    app.processEvents()


def test_overview_tracks_actual_branches_and_distinguishes_waiting_from_hit(panel):
    for phase in (AutoRngPhase.CAPTURE_SEED, AutoRngPhase.SEARCH_TARGET, AutoRngPhase.FINAL_WAIT):
        panel.apply_progress(AutoRngProgress(phase=phase, loop_index=1,
            current_advances=120, trigger_advances=200))
    assert panel.runtime_flow.current_node == "wait"
    assert panel.runtime_focus_value.text() == "80 帧"
    assert ("search", "wait") in panel.runtime_flow.visited_edges
    assert ("search", "advance") not in panel.runtime_flow.visited_edges
    assert "等待" in panel.runtime_flow.accessibleName()
    panel.set_live_advances(200)
    assert panel.runtime_focus_value.text() == "0 帧"
    assert panel.runtime_flow.current_node == "wait"  # Wait for the real phase event.
    panel.apply_progress(AutoRngProgress(phase=AutoRngPhase.RUN_HIT_SCRIPT, loop_index=1))
    assert panel.runtime_flow.current_node == "hit"
    assert ("wait", "hit") in panel.runtime_flow.visited_edges


def test_failure_retains_its_node_and_next_round_resets_observed_path(panel):
    for phase in (AutoRngPhase.RUN_ADVANCE_SCRIPT, AutoRngPhase.REIDENTIFY, AutoRngPhase.FAILED):
        panel.apply_progress(AutoRngProgress(phase=phase, loop_index=1))
    assert panel.runtime_flow.current_node == "calibrate"
    assert panel.runtime_flow.failed
    assert panel.runtime_state_label.text() == "失败"
    panel.begin_runtime_cycle(2)
    panel.apply_progress(AutoRngProgress(phase=AutoRngPhase.CAPTURE_SEED, loop_index=2))
    assert panel.runtime_flow.current_node == "seed"
    assert not panel.runtime_flow.failed
    assert not panel.runtime_flow.visited_edges
    assert panel.runtime_focus.isHidden()


def test_details_close_reopen_keeps_live_data_and_same_page_configuration(panel):
    panel.max_wait_frames.setValue(456)
    panel.apply_progress(AutoRngProgress(phase=AutoRngPhase.FINAL_WAIT,
        loop_index=2, current_advances=120, trigger_advances=200))
    panel.set_candidate_targets([SimpleNamespace(advances=i + 200, nature=0, shiny=0, ivs=(31,) * 6)
                                 for i in range(25)], locked_index=24)
    assert not hasattr(panel, "local_views")
    assert panel.target_summary_group.isVisible() and panel.runtime_card.isVisible()
    panel.runtime_details_toggle.click()
    QTest.qWait(10)
    assert panel.runtime_dialog.isVisible()
    assert not panel.runtime_dialog.isModal()
    assert panel.candidate_table.isVisible()
    assert panel.candidate_table.item(0, 1).text() == "224"
    panel.runtime_dialog.close()
    assert not panel.runtime_details_toggle.isChecked()
    panel.set_live_advances(190)
    assert panel.runtime_focus_value.text() == "10 帧"
    panel.runtime_details_toggle.click()
    assert panel.runtime_remaining_value.text() == "10 帧"
    assert panel.candidate_table.rowCount() == 20
    assert panel.max_wait_frames.value() == 456
    panel.set_phase_text("已停止")
    assert panel.runtime_focus.isHidden()
    assert panel.runtime_flow.current_node is None
