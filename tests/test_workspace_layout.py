import json
from types import SimpleNamespace
import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QBoxLayout
from tests.test_start_readiness import window
from auto_bdsp_rng.automation.auto_rng.models import AutoRngPhase, AutoRngProgress
from auto_bdsp_rng.automation.auto_tid_rng import AutoTidRngPhase, AutoTidRngProgress
from auto_bdsp_rng.gen8_id import IDState8


def settle():
    for _ in range(3):
        QApplication.processEvents()
        QTest.qWait(10)


def test_narrow_pages_reflow_without_changing_fonts_or_controls(window, monkeypatch):
    w = window
    monkeypatch.setattr(w, "_screen_available_geometry", lambda: QRect(0, 0, 1800, 1100))
    original = w.auto_rng_tab.max_wait_frames.value()
    font_size = w.auto_rng_tab.runtime_current_value.font().pixelSize()
    w.resize(860, 600)
    for page in (w.auto_rng_tab, w.auto_tid_rng_tab):
        w.tabs.setCurrentWidget(page)
        settle()
        assert w.width() == 860 and w.height() == 600
        assert page.local_views.currentIndex() == 0
        assert page.local_views.currentWidget().isVisible()
        page.local_views.setCurrentIndex(1)
        assert page.local_views.currentWidget().isVisible()
        assert w.help_button.isVisible()
        assert w.view_status_logs_button.isVisible()
    w.tabs.setCurrentWidget(w.project_xs_tab)
    settle()
    assert w.capture_button.isVisible()
    assert w.project_xs_config_scroll.geometry().top() >= w.capture_toolbar.geometry().bottom()
    assert w.monitor_sidebar.isVisible()
    w.tabs.setCurrentWidget(w.bdsp_tab)
    settle()
    assert w.bdsp_reflow.layout.direction() == QBoxLayout.Direction.TopToBottom
    w.resize(1500, 900)
    for page in (w.auto_rng_tab, w.auto_tid_rng_tab):
        w.tabs.setCurrentWidget(page)
        settle()
        assert page.local_views.currentIndex() == 1
    assert w.auto_rng_tab.runtime_current_value.font().pixelSize() == font_size == 28
    assert w.auto_rng_tab.max_wait_frames.value() == original


def test_readiness_and_empty_state_reveal_configuration_without_page_header(window):
    w = window
    panel = w.auto_tid_rng_tab
    panel.config_scroll.hide()
    assert panel.config_scroll.isHidden()
    w.readiness.show_for(panel)
    w.readiness.navigate("targets")
    settle()
    assert not panel.config_scroll.isHidden()
    w.tabs.setCurrentWidget(w.bdsp_tab)
    w.bdsp_config_scroll.hide()
    assert w.bdsp_config_scroll.isHidden()
    w._focus_static_configuration(w.iv_min[0])
    settle()
    assert not w.bdsp_config_scroll.isHidden()


def test_monitor_layout_migrates_defaults_and_preserves_user_drag(window, monkeypatch):
    monkeypatch.setattr(window, "_screen_available_geometry", lambda: QRect(0, 0, 1800, 1100))
    window.resize(1500, 900)
    split = window.workspace_splitter
    settings = split.settings
    settings.remove(split.key)
    settings.setValue("workspace_layout/monitor/horizontal", "[760, 360]")
    split.restore_sizes()
    settle()
    assert split.sizes()[1] > 600
    split.setSizes([900, 550])
    split._save_sizes()
    stored = json.loads(str(settings.value(split.key)))
    window.resize(860, 600)
    settle()
    assert json.loads(str(settings.value(split.key))) == stored
    assert split._custom
    split.restore_sizes()
    assert split.sizes()[1] >= 260


def test_shared_log_entry_preserves_module_scope(window):
    w = window
    w._active_auto_rng_run_id = "layout-run"
    w._active_auto_rng_round_id = "layout-round"
    w._active_auto_tid_run_id = "tid-run"
    w._active_auto_tid_round_id = "tid-round"
    called = []
    w.run_records_tab.show_logs = lambda source, **kw: called.append((source, kw))
    for page, source, run_id, round_id in (
        (w.auto_rng_tab, "自动定点", "layout-run", "layout-round"),
        (w.auto_tid_rng_tab, "自动 TID", "tid-run", "tid-round"),
        (w.project_xs_tab, "Seed 捕捉", None, None),
        (w.easycon_tab, "伊机控", None, None),
        (w.bdsp_tab, None, None, None),
        (w.run_records_tab, None, None, None),
    ):
        w.tabs.setCurrentWidget(page)
        w.view_status_logs_button.click()
        assert w.tabs.currentWidget() is w.run_records_tab
        assert called[-1] == (source, {"run_id": run_id, "round_id": round_id})
    w.tabs.setCurrentWidget(w.auto_tid_rng_tab)
    settle()
    assert w.auto_tid_rng_tab.view_log_button.isVisible()
    w.auto_tid_rng_tab.view_log_button.click()
    assert called[-1] == ("自动 TID", {"run_id": "tid-run", "round_id": "tid-round"})
    w.help_menu_controller.view_run_logs_action.trigger()
    assert called[-1] == (None, {"run_id": None, "round_id": None})


@pytest.mark.parametrize("size,right_min,video_min", [((860, 600), 376, 360), ((1150, 900), 476, 460), ((1440, 960), 606, 580), ((854, 480), 376, 250)])
def test_default_geometry_keeps_forms_navigation_and_monitor_usable(window, monkeypatch, size, right_min, video_min):
    w = window
    monkeypatch.setattr(w, "_screen_available_geometry", lambda: QRect(0, 0, 2400, 1400))
    w.resize(*size)
    for page in (w.auto_rng_tab, w.auto_tid_rng_tab, w.project_xs_tab, w.bdsp_tab, w.easycon_tab, w.run_records_tab):
        w.tabs.setCurrentWidget(page)
        settle()
        assert w.size().toTuple() == size
        assert w.monitor_sidebar.width() >= right_min
        assert w.preview_label.width() >= video_min
        assert abs(w.preview_aspect_container.width() * 9 - w.preview_aspect_container.height() * 16) <= 8
        assert w.live_log_panel.source_combo.isVisible() and w.live_log_panel.expand_button.isVisible()
        assert w.live_log_panel.text.viewport().height() >= w.live_log_panel.text.fontMetrics().height() + 4
        assert w.monitor_preview_scroll.verticalScrollBar().maximum() == 0
    bar = w.tabs.tabBar()
    assert all(bar.rect().contains(bar.tabRect(index)) for index in range(6))
    assert not bar.usesScrollButtons()
    for area in (w.auto_rng_tab.config_panel, w.auto_tid_rng_tab.config_scroll, w.project_xs_config_scroll, w.bdsp_config_scroll):
        assert area.horizontalScrollBar().maximum() == 0


def test_runtime_views_keep_results_in_first_screen_and_preserve_configuration(window, monkeypatch):
    w = window
    monkeypatch.setattr(w, "_screen_available_geometry", lambda: QRect(0, 0, 1800, 1200))
    w.resize(860, 600)
    candidates = [SimpleNamespace(advances=1000 + i, nature=3, shiny=0, ivs=(31,) * 6) for i in range(24)]
    auto, tid = w.auto_rng_tab, w.auto_tid_rng_tab
    auto.max_wait_frames.setValue(456)
    auto.apply_progress(AutoRngProgress(phase=AutoRngPhase.FINAL_WAIT, current_advances=900, raw_target_advances=1000, remaining_to_trigger=100))
    auto.set_candidate_targets(candidates, locked_index=2)
    tid.add_target_display_tid(123456)
    tid.apply_progress(AutoTidRngProgress(phase=AutoTidRngPhase.WAIT_NAME_TRIGGER, current_advances=900, target_advances=1000, target_display_tid=123456))
    tid.set_id_states([IDState8(i, 12000, 24000, 12000, 123456) for i in range(24)])
    for panel, table, area in ((auto, auto.candidate_table, auto.runtime_panel), (tid, tid.id_table, tid.runtime_scroll)):
        w.tabs.setCurrentWidget(panel)
        panel.runStateChanged.emit(True)
        settle()
        assert panel.local_views.currentIndex() == 1
        assert area.horizontalScrollBar().maximum() == 0
        top = table.mapTo(area.viewport(), table.rect().topLeft()).y()
        assert top + table.horizontalHeader().height() + 2 * table.verticalHeader().defaultSectionSize() < area.viewport().height()
        panel.local_views.setCurrentIndex(0)
        panel.local_views.setCurrentIndex(1)
        assert table.rowCount() >= 20
    assert auto.max_wait_frames.value() == 456


def test_real_static_generation_collapses_conditions_and_can_reopen_each_group(window):
    w = window
    errors = []
    w._show_error = lambda title, exc: errors.append(exc)
    for box, seed in zip(w.bdsp_seed64_inputs, ("123456789ABCDEF0", "13579BDF2468ACE0")):
        box.setText(seed)
    w.initial_advances.setText("0")
    w.max_advances.setText("20")
    w.generate_results()
    assert not errors
    assert w.table.rowCount() == 21
    assert not w.query_toggle.isChecked()
    assert "21" in w.query_summary.text()
    for control, expected in ((w.bdsp_seed64_inputs[0], 0), (w.encounter_combo, 1), (w.iv_min[0], 2)):
        w._focus_static_configuration(control)
        settle()
        assert w.query_groups.currentIndex() == expected
        assert w.query_toggle.isChecked() and control.isVisible()
        assert w.bdsp_config_scroll.horizontalScrollBar().maximum() == 0


def test_temporary_script_tools_keep_draft_and_restore_editor_at_short_height(window, monkeypatch):
    w, panel = window, window.easycon_tab
    monkeypatch.setattr(w, "_screen_available_geometry", lambda: QRect(0, 0, 1800, 1200))
    w.resize(854, 480)
    w.tabs.setCurrentWidget(panel)
    panel.editor.setPlainText("WAIT 100\n" * 24)
    original = panel.editor
    for section in ("library", "control"):
        panel.show_tools(section)
        settle()
        assert panel.tools_panel.isVisible()
        assert panel.editor_content.isHidden()
        assert panel.close_tools_button.isVisible()
        assert w.monitor_preview_scroll.verticalScrollBar().maximum() == 0
        assert w.live_log_panel.source_combo.isVisible()
        panel.close_tools_button.click()
        settle()
        assert panel.editor is original
        assert panel.editor.isVisible()
        assert panel.editor.toPlainText().count("WAIT") == 24
        assert panel.editor.width() >= w.tabs.width() - 28
        assert panel.editor.height() >= 100
    panel._saved_editor_text = panel.editor.toPlainText()


def test_round_snapshot_and_expanded_details_preserve_complete_data_at_short_height(window, monkeypatch):
    w, history = window, window.history_tab
    monkeypatch.setattr(w, "_screen_available_geometry", lambda: QRect(0, 0, 1800, 1200))
    w.resize(854, 480)
    w.tabs.setCurrentWidget(w.run_records_tab)
    history.begin_run("workspace-rounds", "帝牙卢卡")
    history.cycle_start(1)
    seed = "123456789ABCDEF0 / 13579BDF2468ACE0"
    history.seed_captured(seed, 0, 0, 1000)
    states = [SimpleNamespace(advances=i, ivs=(31,) * 6, ec=0xAABBCCDD, pid=0x11223344,
                              nature=3, shiny=2, ability=1, gender=0, height=255, weight=12) for i in range(8)]
    history.candidates_found(states, locked_index=0)
    settle()
    snapshot = history.round_candidate_table
    assert snapshot.rowCount() == 8 and snapshot.columnCount() == 17
    assert snapshot.item(0, 2).text() == "0"
    assert snapshot.item(0, 13).text() == "AABBCCDD"
    assert snapshot.item(0, 0).background().color().name() == "#eaf7f1"
    history.copy_round_button.click()
    copied = QApplication.clipboard().text()
    assert seed in copied and "EC=AABBCCDD" in copied and "PID=11223344" in copied
    assert "锁定帧 0" in history.round_summary.text()
    for toggle, control in ((history.meta_toggle, history.seed_value_label),
                            (history.feed_toggle, history.history_scroll)):
        toggle.setChecked(True)
        settle()
        history.detail_scroll.ensureWidgetVisible(control, 8, 8)
        settle()
        assert history.detail_scroll.horizontalScrollBar().maximum() == 0
        viewport = history.detail_scroll.viewport()
        assert viewport.rect().intersects(QRect(control.mapTo(viewport, control.rect().topLeft()), control.size()))
        toggle.setChecked(False)
    assert snapshot.rowCount() == 8
