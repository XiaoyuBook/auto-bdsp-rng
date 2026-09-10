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
        assert w.page_headers[page].help_button.isVisible()
        assert w.page_headers[page].logs_button.isVisible()
        assert w.page_headers[page].geometry().right() <= page.width()
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


def test_readiness_and_empty_state_reveal_folded_configuration(window):
    w = window
    panel = w.auto_tid_rng_tab
    header = w.page_headers[panel]
    header.fold_button.click()
    assert panel.config_scroll.isHidden()
    w.readiness.show_for(panel)
    w.readiness.navigate("targets")
    settle()
    assert not panel.config_scroll.isHidden()
    assert not header.fold_button.isChecked()
    static_header = w.page_headers[w.bdsp_tab]
    w.tabs.setCurrentWidget(w.bdsp_tab)
    static_header.fold_button.click()
    assert w.bdsp_config_scroll.isHidden()
    w._focus_static_configuration(w.iv_min[0])
    settle()
    assert not w.bdsp_config_scroll.isHidden()
    assert not static_header.fold_button.isChecked()


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
    called = []
    w.run_records_tab.show_logs = lambda source, **kw: called.append((source, kw))
    w.page_headers[w.auto_rng_tab].logs_button.click()
    assert w.tabs.currentWidget() is w.run_records_tab
    assert called == [("自动定点", {"run_id": "layout-run", "round_id": "layout-round"})]
