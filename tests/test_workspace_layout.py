import json
from PySide6.QtCore import QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QBoxLayout
from tests.test_start_readiness import window


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
    for page, splitter in ((w.auto_rng_tab, w.auto_rng_tab.workspace_splitter),
                           (w.auto_tid_rng_tab, w.auto_tid_rng_tab.workspace_splitter),
                           (w.project_xs_tab, w.project_xs_splitter)):
        w.tabs.setCurrentWidget(page)
        settle()
        assert w.width() == 860 and w.height() == 600
        assert splitter.orientation() == Qt.Orientation.Vertical
        assert splitter.widget(0).isVisible()
        assert splitter.widget(1).isVisible()
        assert w.help_button.isVisible()
        assert w.view_status_logs_button.isVisible()
    assert w.project_xs_splitter.geometry() == w.project_xs_tab.rect()
    w.tabs.setCurrentWidget(w.bdsp_tab)
    settle()
    assert w.bdsp_reflow.layout.direction() == QBoxLayout.Direction.TopToBottom
    w.resize(1260, 900)
    for page, splitter in ((w.auto_rng_tab, w.auto_rng_tab.workspace_splitter), (w.project_xs_tab, w.project_xs_splitter)):
        w.tabs.setCurrentWidget(page)
        settle()
        assert splitter.orientation() == Qt.Orientation.Horizontal
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


def test_splitter_preferences_keep_independent_orientations(window, monkeypatch):
    monkeypatch.setattr(window, "_screen_available_geometry", lambda: QRect(0, 0, 1800, 1100))
    window.resize(1150, 900)
    p = window.auto_rng_tab
    split = p.workspace_splitter
    window.tabs.setCurrentWidget(p)
    settle()
    split.setSizes([390, 700])
    split._save_sizes()
    stored = json.loads(str(split.settings.value(split.key + "/horizontal")))
    window.resize(860, 600)
    settle()
    split.setSizes([170, 250])
    split._save_sizes()
    assert split.settings.contains(split.key + "/vertical")
    assert json.loads(str(split.settings.value(split.key + "/horizontal"))) == stored


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
